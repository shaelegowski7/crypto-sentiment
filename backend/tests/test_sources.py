"""Social posts vs editorial news (app/sources.py).

The Sentiment Index must never average Reddit/StockTwits/X in -- its
methodology promises news only -- while /v1 and the MCP mirrors keep them by
default and drop them on sources=news.

Imports app.main, so like test_smoke.py this needs a reachable DATABASE_URL.
Rows are seeded under a fake ticker (and, for the index, in 2001) so nothing
the scheduler scrapes during the run can land in the assertions.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import main, mcp_server, models
from app.database import SessionLocal
from app.sentiment_index import compute_index
from app.sources import classify_source, social_headline_filter

TICKER = "ZZSRCTEST"
URL_PREFIX = "https://test.invalid/sources/"
INDEX_AS_OF = datetime(2001, 1, 8)

# (source, source_type, score). News averages 0.4; the social rows are all
# strongly negative so leaking any of them in visibly moves the number.
ROWS = [
    ("CoinDesk", None, 0.5),
    ("Reuters", None, 0.3),
    ("newest submissions : Bitcoin", None, -0.9),
    ("StockTwits", "stocktwits", -0.8),
    ("x.com", None, -0.7),
]
NEWS_COUNT = 2


@pytest.mark.parametrize("source, source_type, expected", [
    ("CoinDesk", None, "news"),
    (None, None, "news"),
    ("newest submissions : Bitcoin", None, "reddit"),
    ("Newest Submissions : Forex", None, "reddit"),
    ("old.reddit.com", None, "reddit"),
    # Exact match, not substring: a real company on its own domain is news.
    ("redditinc.com", None, "news"),
    ("StockTwits", "stocktwits", "stocktwits"),
    ("x.com", None, "x"),
    ("youtube.com", None, "youtube"),
    ("Some AI-scraped site", "ai", "news"),
])
def test_classify_source(source, source_type, expected):
    assert classify_source(source, source_type) == expected


def _insert(db, ticker, published_at):
    for i, (source, source_type, score) in enumerate(ROWS):
        db.add(models.Headline(
            ticker=ticker, title=f"test headline {i}", source=source,
            source_type=source_type, url=f"{URL_PREFIX}{ticker}/{i}",
            sentiment_score=score, sentiment_label="neutral",
            published_at=published_at,
        ))
    db.commit()


@pytest.fixture
def db():
    session = SessionLocal()
    _insert(session, TICKER, datetime.utcnow() - timedelta(minutes=5))
    _insert(session, "BTC", INDEX_AS_OF - timedelta(days=3))
    try:
        yield session
    finally:
        session.query(models.Headline).filter(
            models.Headline.url.like(f"{URL_PREFIX}%")
        ).delete(synchronize_session=False)
        session.commit()
        session.close()


def test_filter_agrees_with_classify(db):
    # The SQL predicate and the Python label must split rows the same way,
    # or /v1 could hand back a row labelled "news" that sources=news drops.
    rows = db.query(models.Headline).filter(models.Headline.ticker == TICKER).all()
    social_ids = {h.id for h in db.query(models.Headline).filter(
        models.Headline.ticker == TICKER, social_headline_filter()).all()}
    for h in rows:
        assert (h.id in social_ids) == (classify_source(h.source, h.source_type) != "news")


def test_index_excludes_social(db):
    index = compute_index(db, as_of=INDEX_AS_OF)
    crypto = index["categories"]["crypto"]
    assert crypto["headlines"] == NEWS_COUNT
    assert crypto["score"] == 70.0  # mean 0.4 on the 0-100 scale
    assert "Reddit, StockTwits and X posts are excluded" in index["methodology"]


@pytest.fixture
def client(monkeypatch):
    key = SimpleNamespace(key_prefix="sfx_test")
    main.app.dependency_overrides[main.get_api_key] = lambda: key
    monkeypatch.setattr(main, "track_usage", lambda *args, **kwargs: None)
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.pop(main.get_api_key, None)


def test_v1_sentiment_default_keeps_social_and_labels_it(db, client):
    body = client.get(f"/v1/sentiment/{TICKER}").json()
    assert body["sources"] == "all"
    assert body["returned"] == len(ROWS)
    assert {r["source_kind"] for r in body["data"]} == {"news", "reddit", "stocktwits", "x"}


def test_v1_sentiment_news_only(db, client):
    body = client.get(f"/v1/sentiment/{TICKER}?sources=news").json()
    assert body["returned"] == NEWS_COUNT
    assert {r["source_kind"] for r in body["data"]} == {"news"}


def test_v1_sentiment_rejects_unknown_sources(db, client):
    resp = client.get(f"/v1/sentiment/{TICKER}?sources=reddit")
    assert resp.status_code == 422
    assert resp.json()["error"]["type"] == "invalid_request_error"


def test_v1_summary_sources(db, client):
    everything = client.get(f"/v1/summary/{TICKER}?days=2").json()
    news = client.get(f"/v1/summary/{TICKER}?days=2&sources=news").json()
    # Summed, not data[0], in case the run straddles midnight UTC.
    assert sum(d["article_count"] for d in everything["data"]) == len(ROWS)
    assert sum(d["article_count"] for d in news["data"]) == NEWS_COUNT
    assert news["sources"] == "news"


@pytest.fixture
def mcp_session(monkeypatch):
    # MCP_MIRRORS_V1: the tools must filter exactly like the HTTP routes.
    monkeypatch.setattr(mcp_server, "_open_authed_session",
                        lambda ctx, enforce_quota=True: (SimpleNamespace(), SessionLocal()))
    monkeypatch.setattr(mcp_server, "_bill", lambda *args, **kwargs: None)


def test_mcp_get_sentiment_sources(db, mcp_session):
    everything = mcp_server.get_sentiment(None, TICKER)
    news = mcp_server.get_sentiment(None, TICKER, sources="news")
    assert everything["returned"] == len(ROWS)
    assert news["returned"] == NEWS_COUNT
    assert {r["source_kind"] for r in news["data"]} == {"news"}


def test_mcp_get_summary_sources(db, mcp_session):
    news = mcp_server.get_summary(None, TICKER, days=2, sources="news")
    assert sum(d["article_count"] for d in news["data"]) == NEWS_COUNT
