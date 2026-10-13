"""Reddit is fetched one subreddit per tick, never inside the RSS burst.

Unauthenticated Reddit RSS allows one request per 60-second window per IP, so
the old back-to-back fetches only ever got r/Bitcoin through (see the Reddit
note in app/scraper.py).  No database needed: only app.scraper is imported.
"""
from types import SimpleNamespace

import feedparser
import pytest

from app import scraper

CRYPTO = "https://www.reddit.com/r/CryptoCurrency/new/.rss"
FOREX = "https://www.reddit.com/r/Forex/new/.rss"

ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>newest submissions : CryptoCurrency</title>
  <entry><title>Bitcoin breaks a new high</title>
    <link href="https://www.reddit.com/r/CryptoCurrency/comments/t1/"/>
    <updated>2026-10-13T08:00:00+00:00</updated></entry>
  <entry><title>Dogecoin pumps on a tweet</title>
    <link href="https://www.reddit.com/r/CryptoCurrency/comments/t2/"/>
    <updated>2026-10-13T08:05:00+00:00</updated></entry>
  <entry><title>Daily discussion thread</title>
    <link href="https://www.reddit.com/r/CryptoCurrency/comments/t3/"/>
    <updated>2026-10-13T08:10:00+00:00</updated></entry>
</feed>"""


def test_each_subreddit_listed_once_with_all_its_tickers():
    assert set(scraper.REDDIT_FEEDS[CRYPTO]) == {"BTC", "ETH", "SOL", "XRP", "DOGE"}
    assert len(scraper.REDDIT_FEEDS[FOREX]) == 7
    assert all(scraper._is_reddit(u) for u in scraper.REDDIT_FEEDS)


def test_rss_path_never_requests_reddit(monkeypatch):
    requested = []

    def fake_parse(url, **kwargs):
        requested.append(url)
        return feedparser.FeedParserDict(entries=[], feed={})

    monkeypatch.setattr(scraper.feedparser, "parse", fake_parse)
    for ticker in scraper.RSS_FEEDS:
        scraper.fetch_rss_headlines(ticker)
    assert requested
    assert not [u for u in requested if scraper._is_reddit(u)]


@pytest.fixture
def at_crypto(monkeypatch):
    monkeypatch.setattr(scraper, "_reddit_next", list(scraper.REDDIT_FEEDS).index(CRYPTO))


def _respond(monkeypatch, status, content=b""):
    calls = []

    def fake_get(url, **kwargs):
        calls.append(url)
        return SimpleNamespace(status_code=status, content=content,
                               headers={"x-ratelimit-reset": "42"})

    monkeypatch.setattr(scraper.requests, "get", fake_get)
    return calls


def test_throttled_feed_is_retried_next_tick(monkeypatch, at_crypto):
    calls = _respond(monkeypatch, 429)
    before = scraper._reddit_next
    assert scraper.fetch_next_reddit_feed() == []
    assert calls == [CRYPTO]
    assert scraper._reddit_next == before


def test_dead_feed_does_not_stall_rotation(monkeypatch, at_crypto):
    _respond(monkeypatch, 404)
    before = scraper._reddit_next
    assert scraper.fetch_next_reddit_feed() == []
    assert scraper._reddit_next == before + 1


def test_one_fetch_fans_out_with_keyword_filter(monkeypatch, at_crypto):
    calls = _respond(monkeypatch, 200, ATOM)
    before = scraper._reddit_next
    headlines = scraper.fetch_next_reddit_feed()
    assert calls == [CRYPTO]  # one request serves all five crypto tickers
    assert scraper._reddit_next == before + 1
    by_ticker = {(h["ticker"], h["title"]) for h in headlines}
    assert by_ticker == {
        ("BTC", "Bitcoin breaks a new high"),
        ("DOGE", "Dogecoin pumps on a tweet"),
    }
    # The source string is what sources.py uses to recognise Reddit.
    assert {h["source"] for h in headlines} == {"newest submissions : CryptoCurrency"}
