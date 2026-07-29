"""Runtime replay of ScraperAI configs — the light, LLM-free half.

Config JSON files under ``app/scraperai_configs/`` are produced offline by
``backend/build_scraper_config.py``.  Each file is
``{"source", "tickers", "config", ...}`` where ``config`` is a serialised
``ScraperConfig``.  At runtime we load them and, for a given ticker, replay the
matching configs with the vendored pure-``lxml`` engine (no LLM, no Selenium) to
yield headline dicts in the same shape every other fetcher in ``scraper.py``
returns: ``{ticker, title, source, url, published_at}`` (+ optional ``body``).

This module imports only ``requests`` + ``lxml`` (via the vendored package) and
the stdlib — safe to import from ``app/main.py`` and the CI smoke test.

Optional per-file metadata (add by hand after reviewing a generated config):
  * ``keywords``:   substrings; a row is kept only if its title matches one
                    (same relevance gate the general RSS feeds use).
  * ``field_map``:  ``{"title": "...", "url": "...", "body": "..."}`` naming the
                    extracted field to use for each role, overriding the
                    name-based heuristics below.
  * ``headers``:    extra HTTP headers for the replay crawler.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone

from .vendor.scraperai_replay import RequestsCrawler, Scraper, ScraperConfig
from .vendor.scraperai_replay.urls import fix_relative_url

logger = logging.getLogger("ai_sources")

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "scraperai_configs")

# Field-name substrings used to guess which extracted field fills each role when
# a config has no explicit `field_map`.
_TITLE_HINTS = ("title", "headline", "name", "heading")
_URL_HINTS = ("url", "link", "href", "permalink")
_BODY_HINTS = ("body", "content", "article", "text", "summary", "description")


class AISource:
    def __init__(self, path: str, raw: dict):
        self.path = path
        self.source = raw.get("source") or os.path.splitext(os.path.basename(path))[0]
        self.tickers = {t.upper() for t in raw.get("tickers", [])}
        self.keywords = [k.lower() for k in raw.get("keywords", [])]
        self.field_map = raw.get("field_map", {}) or {}
        self.headers = raw.get("headers", {}) or {}
        self.config = ScraperConfig(**raw["config"])


def _load_sources() -> list[AISource]:
    sources: list[AISource] = []
    if not os.path.isdir(CONFIG_DIR):
        return sources
    for name in sorted(os.listdir(CONFIG_DIR)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(CONFIG_DIR, name)
        try:
            with open(path, encoding="utf-8") as fh:
                sources.append(AISource(path, json.load(fh)))
        except Exception as e:  # a broken config must never break the loader
            logger.warning("[AI] failed to load %s: %s", name, e)
    return sources


# Loaded once at import; configs are static files committed to the repo.
AI_SOURCES = _load_sources()


def _pick(row: dict, hints: tuple, explicit: str | None):
    """Return the row value for a role, by explicit field name or name hints."""
    if explicit and explicit in row:
        val = row[explicit]
    else:
        val = None
        for key, value in row.items():
            kl = key.lower()
            if any(h in kl for h in hints):
                val = value
                break
    if isinstance(val, list):
        val = val[0] if val else None
    if val is None:
        return None
    return str(val).strip() or None


def _rows_to_headlines(src: AISource, ticker: str) -> list[dict]:
    crawler = RequestsCrawler(headers=src.headers)
    scraper = Scraper(src.config, crawler)
    now = datetime.now(timezone.utc)
    out: list[dict] = []

    for row in scraper.scrape():
        try:
            title = _pick(row, _TITLE_HINTS, src.field_map.get("title"))
            url = _pick(row, _URL_HINTS, src.field_map.get("url"))
            body = _pick(row, _BODY_HINTS, src.field_map.get("body"))
        except Exception as e:
            logger.debug("[AI] %s: row map error: %s", src.source, e)
            continue

        if not title or not url:
            # url is required for dedup; without it we'd re-ingest every run.
            continue
        url = fix_relative_url(src.config.start_url, url)

        if src.keywords and not any(k in title.lower() for k in src.keywords):
            continue

        headline = {
            "ticker": ticker.upper(),
            "title": title,
            "source": src.source,
            "url": url,
            "published_at": now,
            "source_type": "ai",
        }
        if body:
            headline["body"] = body
        out.append(headline)

    return out


def fetch_ai_headlines(ticker: str) -> list[dict]:
    """Replay every ScraperAI config that covers ``ticker``.

    Returns headline dicts in the standard ingestion shape.  Each source is
    isolated: a failing config logs and is skipped, never aborting the rest.
    """
    ticker = ticker.upper()
    headlines: list[dict] = []
    for src in AI_SOURCES:
        if ticker not in src.tickers:
            continue
        try:
            rows = _rows_to_headlines(src, ticker)
            logger.info("[AI] %s/%s: %d headlines", src.source, ticker, len(rows))
            headlines.extend(rows)
        except Exception as e:
            logger.warning("[AI] %s/%s: replay failed: %s", src.source, ticker, e)
    return headlines
