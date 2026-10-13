"""Social posts vs editorial news (app/sources.py).

Social posts are collected but kept separate: no sentiment number averages
them in with news, and they're reachable only on purpose (`sources=social`,
or `sources=all` on a labelled headline list).

Imports app.main, so like test_smoke.py this needs a reachable DATABASE_URL.
Rows are seeded under a fake ticker (and, for the index, in 2001) so nothing
the scheduler scrapes during the run can land in the assertions.
"""
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from app import brief, main, mcp_server, models
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
SOCIAL_COUNT = 3


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


def test_brief_uses_news_only(db, monkeypatch):
    monkeypatch.setitem(brief.TICKER_NAMES, TICKER, "Test ticker")
    data = brief.fetch_ticker_data(db, TICKER)
    assert data["current_sentiment"] == 0.4
    # The most extreme row is a -0.9 Reddit post; the brief must not pick it.
    assert data["top_headline"]["title"] == "test headline 0"


@pytest.fixture
def client(monkeypatch):
    key = SimpleNamespace(key_prefix="sfx_test")
    main.app.dependency_overrides[main.get_api_key] = lambda: key
    monkeypatch.setattr(main, "track_usage", lambda *args, **kwargs: None)
    try:
        yield TestClient(main.app)
    finally:
        main.app.dependency_overrides.pop(main.get_api_key, None)


def _kinds(body):
    return {r["source_kind"] for r in body["data"]}


def test_dashboard_chart_uses_news_only(db, client):
    body = client.get(f"/sentiment-summary/{TICKER}?days=2").json()
    assert sum(d["count"] for d in body["data"]) == NEWS_COUNT


def test_v1_sentiment_defaults_to_news(db, client):
    body = client.get(f"/v1/sentiment/{TICKER}").json()
    assert body["sources"] == "news"
    assert body["returned"] == NEWS_COUNT
    assert _kinds(body) == {"news"}


def test_v1_sentiment_social_and_all(db, client):
    social = client.get(f"/v1/sentiment/{TICKER}?sources=social").json()
    assert social["returned"] == SOCIAL_COUNT
    assert _kinds(social) == {"reddit", "stocktwits", "x"}
    everything = client.get(f"/v1/sentiment/{TICKER}?sources=all").json()
    assert everything["returned"] == len(ROWS)


def test_v1_sentiment_rejects_unknown_sources(db, client):
    resp = client.get(f"/v1/sentiment/{TICKER}?sources=reddit")
    assert resp.status_code == 422
    assert resp.json()["error"]["type"] == "invalid_request_error"


def test_v1_summary_keeps_news_and_social_apart(db, client):
    news = client.get(f"/v1/summary/{TICKER}?days=2").json()
    social = client.get(f"/v1/summary/{TICKER}?days=2&sources=social").json()
    # Summed, not data[0], in case the run straddles midnight UTC.
    assert news["sources"] == "news"
    assert sum(d["article_count"] for d in news["data"]) == NEWS_COUNT
    assert sum(d["article_count"] for d in social["data"]) == SOCIAL_COUNT
    # No mixed daily average.
    assert client.get(f"/v1/summary/{TICKER}?days=2&sources=all").status_code == 422


@pytest.fixture
def mcp_session(monkeypatch):
    # MCP_MIRRORS_V1: the tools must filter exactly like the HTTP routes.
    monkeypatch.setattr(mcp_server, "_open_authed_session",
                        lambda ctx, enforce_quota=True: (SimpleNamespace(), SessionLocal()))
    monkeypatch.setattr(mcp_server, "_bill", lambda *args, **kwargs: None)


def test_mcp_get_sentiment_sources(db, mcp_session):
    assert mcp_server.get_sentiment(None, TICKER)["returned"] == NEWS_COUNT
    social = mcp_server.get_sentiment(None, TICKER, sources="social")
    assert social["returned"] == SOCIAL_COUNT
    assert _kinds(social) == {"reddit", "stocktwits", "x"}
    assert mcp_server.get_sentiment(None, TICKER, sources="all")["returned"] == len(ROWS)


def test_mcp_get_summary_sources(db, mcp_session):
    news = mcp_server.get_summary(None, TICKER, days=2)
    social = mcp_server.get_summary(None, TICKER, days=2, sources="social")
    assert sum(d["article_count"] for d in news["data"]) == NEWS_COUNT
    assert sum(d["article_count"] for d in social["data"]) == SOCIAL_COUNT
