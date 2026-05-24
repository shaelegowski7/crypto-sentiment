"""Standalone GDELT historical backfill — run locally, write to the prod DB.

Why a script instead of the /backfill/gdelt endpoint?  GDELT allows only one
request every 5 seconds, so a full 42-ticker backfill takes hours.  A Railway
BackgroundTask of that length dies on any redeploy/restart with no resume, and
datacenter IPs get throttled harder.  Running this locally (residential IP, GPU
FinBERT) against the production DATABASE_URL is the reliable "leave it overnight"
path.  It is idempotent — headlines dedupe by URL — so it is safe to re-run or
resume after an interrupt.

Usage (from backend/, with venv active and .env pointing at the prod DB):

    python gdelt_backfill.py --verify                 # check every query works, no writes
    python gdelt_backfill.py --tickers BTC --days 7   # smoke test one ticker
    python gdelt_backfill.py --tickers all --days 365 # the overnight run

At 6s pacing, cost is days * windows-per-day * 6s per ticker, e.g. all 42
tickers x 365 days x 1 window ~= 25h, x 100 days ~= 7h.  Volume per day comes
from --max-per-window (250 cap), not from many windows.
"""
import sys
import os
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import argparse
import time
from datetime import datetime, timedelta, timezone

from app.database import SessionLocal, DATABASE_URL
from app.models import Headline
from app.sentiment import analyse_sentiment
from app.scraper import (
    fetch_gdelt_headlines,
    GDELT_QUERIES,
    _gdelt_get,
    _GDELT_BASE_DELAY,
)


def resolve_tickers(arg: str) -> list:
    if arg.lower() == "all":
        return list(GDELT_QUERIES.keys())
    tickers = [t.strip().upper() for t in arg.split(",") if t.strip()]
    unknown = [t for t in tickers if t not in GDELT_QUERIES]
    if unknown:
        sys.exit(f"Unknown tickers (not in GDELT_QUERIES): {', '.join(unknown)}")
    return tickers


def verify(tickers: list) -> None:
    """Ping each ticker's query once over a recent day; report counts/errors.

    Catches queries GDELT rejects (e.g. "phrase too short") that would otherwise
    return zero articles silently.  No DB writes.
    """
    day_end = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59, microsecond=0) - timedelta(days=1)
    day_start = day_end - timedelta(days=1, seconds=-1)
    print(f"Verifying {len(tickers)} queries over {day_start.date()} (one request / {_GDELT_BASE_DELAY}s)\n")

    ok, broken = [], []
    for ticker in tickers:
        params = {
            "query": GDELT_QUERIES[ticker],
            "mode": "artlist",
            "format": "json",
            "maxrecords": 75,
            "sort": "datedesc",
            "startdatetime": day_start.strftime("%Y%m%d%H%M%S"),
            "enddatetime": day_end.strftime("%Y%m%d%H%M%S"),
        }
        data = _gdelt_get(params, f"verify {ticker}")
        if data is None:
            broken.append(ticker)
            print(f"  ✗ {ticker:7s} REJECTED — {GDELT_QUERIES[ticker]}")
        else:
            n = len(data.get("articles", []))
            ok.append(ticker)
            print(f"  ✓ {ticker:7s} {n:3d} articles")
        time.sleep(_GDELT_BASE_DELAY)

    print(f"\n{len(ok)} ok, {len(broken)} broken")
    if broken:
        print("Broken queries need rewriting in GDELT_QUERIES:", ", ".join(broken))


def backfill(tickers: list, days: int, windows_per_day: int, max_per_window: int) -> None:
    print(f"Backfilling {len(tickers)} ticker(s) x {days} days x {windows_per_day} window(s) "
          f"into:\n  {DATABASE_URL.split('@')[-1] if DATABASE_URL else '?'}\n")
    summary = {}
    for idx, ticker in enumerate(tickers, 1):
        print(f"[{idx}/{len(tickers)}] {ticker} — fetching…")
        db = SessionLocal()
        try:
            headlines = fetch_gdelt_headlines(
                ticker, days=days,
                windows_per_day=windows_per_day,
                max_per_window=max_per_window,
            )
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
            db.commit()
            summary[ticker] = saved
            print(f"[{idx}/{len(tickers)}] {ticker} — +{saved} new of {len(headlines)} fetched")
        except KeyboardInterrupt:
            db.rollback()
            print("\nInterrupted — progress so far is committed per-ticker; re-run to resume.")
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
    p = argparse.ArgumentParser(description="GDELT historical headline backfill (local, writes to prod DB).")
    p.add_argument("--tickers", default="all", help='"all" or comma list, e.g. BTC,ETH,XRP')
    p.add_argument("--days", type=int, default=30, help="how many days back to fetch")
    p.add_argument("--windows-per-day", type=int, default=1,
                   help="sub-day windows (1 = 250 articles/day cap; more = more volume but slower)")
    p.add_argument("--max-per-window", type=int, default=250, help="records per window (GDELT caps at 250)")
    p.add_argument("--verify", action="store_true", help="check every query returns articles, then exit (no writes)")
    args = p.parse_args()

    tickers = resolve_tickers(args.tickers)
    if args.verify:
        verify(tickers)
    else:
        backfill(tickers, args.days, max(1, args.windows_per_day), args.max_per_window)


if __name__ == "__main__":
    main()
