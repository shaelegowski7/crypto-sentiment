"""Standalone Hacker News Algolia backfill — run locally, write to the prod DB.

HN Algolia is fast enough (~10 min for all 42 tickers x 365 days) that the
/backfill endpoint can finish inside a Railway BackgroundTask without dying on
redeploy.  This script exists for the offline cases: smoke-testing one ticker
on a residential IP, running a long historical sweep against a beefy local
FinBERT, or recovering after a partial backfill.  Idempotent — headlines dedupe
by URL — so safe to re-run or resume after an interrupt.

Usage (from backend/, with venv active and .env pointing at the prod DB):

    python hn_backfill.py --verify                 # check every query returns hits, no writes
    python hn_backfill.py --tickers BTC --days 7   # smoke test one ticker
    python hn_backfill.py --tickers all --days 365 # full year sweep
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import argparse
import time
from datetime import datetime, timedelta, timezone

# Line-buffer stdout so progress is visible in real time even when redirected to
# a log file — Python block-buffers stdout otherwise, hiding progress for hours.
sys.stdout.reconfigure(line_buffering=True)

from app.database import SessionLocal, DATABASE_URL
from app.models import Headline
from app.sentiment import analyse_sentiment
from app.scraper import (
    fetch_hn_headlines,
    HN_QUERIES,
    HN_KEYWORDS,
    _hn_get,
)


def resolve_tickers(arg: str) -> list:
    if arg.lower() == "all":
        return list(HN_QUERIES.keys())
    tickers = [t.strip().upper() for t in arg.split(",") if t.strip()]
    unknown = [t for t in tickers if t not in HN_QUERIES]
    if unknown:
        sys.exit(f"Unknown tickers (not in HN_QUERIES): {', '.join(unknown)}")
    return tickers


def verify(tickers: list) -> None:
    """Ping each ticker's query once over the last 30 days; report counts.

    Catches typos in HN_QUERIES / HN_KEYWORDS that would make a ticker
    silently return zero post-filter hits.  No DB writes.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=30)
    print(f"Verifying {len(tickers)} queries over last 30 days\n")

    ok, broken, low = [], [], []
    for ticker in tickers:
        seen_urls = set()
        raw_total = 0
        kept_total = 0
        rejected = False
        keywords = HN_KEYWORDS.get(ticker, [])
        for q in HN_QUERIES[ticker]:
            params = {
                "query": q,
                "tags": "story",
                "numericFilters": (
                    f"created_at_i>{int(start.timestamp())},"
                    f"created_at_i<={int(end.timestamp())}"
                ),
                "hitsPerPage": 200,
            }
            data = _hn_get(params, f"verify {ticker} q={q!r}")
            if data is None:
                rejected = True
                break
            hits = data.get("hits", []) or []
            raw_total += len(hits)
            for h in hits:
                title = (h.get("title") or "").strip()
                if not title:
                    continue
                tl = title.lower()
                if tl.startswith(("show hn", "ask hn", "tell hn")):
                    continue
                if keywords and not any(kw in tl for kw in keywords):
                    continue
                url = h.get("url") or f"hn:{h.get('objectID')}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)
                kept_total += 1
            time.sleep(0.3)

        if rejected:
            broken.append(ticker)
            print(f"  X  {ticker:7s} REJECTED -- queries: {HN_QUERIES[ticker]}")
            continue

        flag = "OK " if kept_total >= 3 else " . "
        print(f"  {flag}{ticker:7s} {raw_total:4d} raw, {kept_total:4d} kept "
              f"(across {len(HN_QUERIES[ticker])} queries)")
        (ok if kept_total >= 3 else low).append(ticker)

    print(f"\n{len(ok)} ok, {len(low)} low-volume, {len(broken)} broken")
    if low:
        print("Low-volume tickers (HN coverage thin — rely on RSS):", ", ".join(low))
    if broken:
        print("Broken queries:", ", ".join(broken))


def backfill(tickers: list, days: int, chunk_days: int, start_days_ago: int) -> None:
    print(f"Backfilling {len(tickers)} ticker(s) x {days} days ({chunk_days}d chunks) "
          f"into:\n  {DATABASE_URL.split('@')[-1] if DATABASE_URL else '?'}\n")
    summary = {}
    for idx, ticker in enumerate(tickers, 1):
        print(f"[{idx}/{len(tickers)}] {ticker} — fetching…")
        # Fetch first WITHOUT a DB session open — Railway drops connections
        # left idle longer than a couple of minutes.  HN is fast so this is
        # rarely an issue, but stays consistent with the live scrape pattern.
        try:
            headlines = fetch_hn_headlines(
                ticker, days=days,
                chunk_days=chunk_days,
                start_days_ago=start_days_ago,
            )
        except KeyboardInterrupt:
            print("\nInterrupted during fetch — re-run to resume.")
            break
        except Exception as e:
            summary[ticker] = f"fetch error: {e}"
            print(f"[{idx}/{len(tickers)}] {ticker} — fetch error: {e}")
            continue

        db = SessionLocal()
        try:
            saved = 0
            for h in headlines:
                exists = db.query(Headline).filter(Headline.url == h["url"]).first()
                if exists:
                    continue
                sentiment = analyse_sentiment(h["title"])
                db.add(Headline(
                    ticker=h["ticker"],
                    title=h["title"],
                    source=h["source"],
                    url=h["url"],
                    sentiment_score=sentiment["score"],
                    sentiment_label=sentiment["label"],
                    published_at=h["published_at"],
                ))
                saved += 1
                if saved % 50 == 0:   # commit periodically so progress persists
                    db.commit()
            db.commit()
            summary[ticker] = saved
            print(f"[{idx}/{len(tickers)}] {ticker} — +{saved} new of {len(headlines)} fetched")
        except KeyboardInterrupt:
            db.rollback()
            print("\nInterrupted — progress committed per-50; re-run to resume.")
            db.close()
            break
        except Exception as e:
            db.rollback()
            summary[ticker] = f"error: {e}"
            print(f"[{idx}/{len(tickers)}] {ticker} — error: {e}")
        finally:
            db.close()

    total = sum(v for v in summary.values() if isinstance(v, int))
    print(f"\nDone — +{total} new headlines across {len(summary)} ticker(s)")


def main():
    p = argparse.ArgumentParser(description="Hacker News Algolia historical headline backfill (local, writes to prod DB).")
    p.add_argument("--tickers", default="all", help='"all" or comma list, e.g. BTC,ETH,XRP')
    p.add_argument("--days", type=int, default=365, help="how many days back to fetch")
    p.add_argument("--start-days-ago", type=int, default=0,
                   help="skip the most recent N days (use to fill an older gap without re-fetching)")
    p.add_argument("--chunk-days", type=int, default=14,
                   help="time-window size per Algolia request; auto-shrinks when a chunk hits the 1000-hit cap")
    p.add_argument("--verify", action="store_true", help="check every query returns kept hits, then exit (no writes)")
    args = p.parse_args()

    tickers = resolve_tickers(args.tickers)
    if args.verify:
        verify(tickers)
    else:
        backfill(tickers, args.days, max(1, args.chunk_days), max(0, args.start_days_ago))


if __name__ == "__main__":
    main()
