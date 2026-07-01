"""SentimentFX MCP server.

Exposes the same read endpoints partners can hit via /v1/* — but as an MCP
server so Claude Code, Claude.ai desktop, Claude API apps, Cursor, Cline, and
any other MCP-speaking client can call them as native tools.  Mounted into
the existing FastAPI app at /mcp; partners point their client at
`https://api.sentimentfx.org/mcp` with header `X-API-Key: sk_...`.

Design decisions worth pinning:

* **Auth reuses existing SentimentFX API keys.**  Each tool call re-extracts
  the X-API-Key header from the underlying HTTP request, validates it against
  the APIKey table, and calls the same `track_usage(...)` billing hook as the
  /v1/* endpoints.  A partner's Claude usage bills identically to their
  direct HTTP usage — no separate MCP metering plane.
* **DB session per tool call.**  MCP has no equivalent of FastAPI's
  `Depends(get_db)`, so each tool opens and closes its own session.  Slightly
  more overhead per call, but keeps the tool functions self-contained and the
  concurrency story simple.
* **Query logic is a mini-copy of the /v1/* handlers, not a shared helper.**
  The queries are 3-5 lines each and the shape divergence is intentional
  (MCP returns leaner dicts optimised for LLM consumption; /v1/* returns
  full HTTP-style envelopes with `calls_used` etc).  Extracting a shared
  helper would fight the shape difference.  If a query changes materially,
  update BOTH — marker string: MCP_MIRRORS_V1.
"""
import math
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Any

from mcp.server.fastmcp import FastMCP, Context

from . import models
from .database import SessionLocal
from .scraper import BACKGROUND_TICKERS


# The tickers list lives here (rather than importing from main.py) because
# main.py imports from this module, and reversing that creates a cycle.
_PRIMARY_TICKERS = [
    "BTC", "ETH", "SOL", "XRP", "DOGE",
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
]


mcp = FastMCP(
    name="SentimentFX",
    instructions=(
        "SentimentFX exposes FinBERT-scored news sentiment (-1 to +1) and "
        "GBP prices for 42 assets spanning crypto, forex, US equities, ETFs "
        "and commodity futures.  Use `list_tickers` first to see what's "
        "available.  `get_summary` for daily sentiment aggregates, "
        "`get_sentiment` for the raw scored headlines, `get_prices` for "
        "historical closes, `get_correlation` for a 180-day Pearson stat "
        "between daily sentiment shifts and next-day price returns.  All "
        "responses are in GBP; sentiment scores are pos_prob - neg_prob."
    ),
)


# ---------------------------------------------------------------------------
# Auth + billing plumbing shared by every tool
# ---------------------------------------------------------------------------

def _open_authed_session(ctx: Context):
    """Extract + validate the X-API-Key header from the underlying HTTP request.

    Returns `(api_key, db_session)`.  Caller is responsible for closing the
    session (use a try/finally).  We do NOT reuse a global session because
    MCP tool calls can arrive concurrently and SQLAlchemy sessions are not
    goroutine-safe.

    Late imports of `_hash_key` / `track_usage` avoid the main.py <-> mcp_server
    circular import — they're only needed inside the tool body.
    """
    from .main import _hash_key, track_usage  # noqa: F401 — track_usage re-exported later

    request = ctx.request_context.request
    x_api_key = request.headers.get("x-api-key") or request.headers.get("X-API-Key")
    if not x_api_key:
        raise ValueError(
            "Missing X-API-Key header. Add it to your MCP client config — "
            "generate a key at https://developers.sentimentfx.org."
        )

    db = SessionLocal()
    key_hash = _hash_key(x_api_key)
    key = db.query(models.APIKey).filter(
        models.APIKey.key_hash == key_hash,
        models.APIKey.active == True,
    ).first()
    if not key:
        db.close()
        raise ValueError("Invalid or revoked API key.")
    return key, db


def _bill(api_key, db, calls: int, endpoint: str) -> None:
    """Bill an MCP tool call against the same meter as /v1/* HTTP calls.

    Late-imported for the same circular-import reason as _open_authed_session.
    """
    from .main import track_usage
    track_usage(api_key, db, calls, endpoint=endpoint)


# ---------------------------------------------------------------------------
# Tools — mirror /v1/* endpoints.  See MCP_MIRRORS_V1 note above.
# ---------------------------------------------------------------------------

@mcp.tool()
def list_tickers() -> dict[str, Any]:
    """List every asset SentimentFX tracks.

    Cheap (free — doesn't hit the billing meter).  Returns two groups:
    `primary` (5 crypto + 7 FX pairs — the ones with full sentiment coverage
    across the primary RSS feeds), and `background` (US equities, ETFs,
    commodity futures — coverage is thinner but real).  Use the exact ticker
    string from either group as the `ticker` arg to the other tools.
    """
    return {
        "primary":    _PRIMARY_TICKERS,
        "background": list(BACKGROUND_TICKERS),
        "total":      len(_PRIMARY_TICKERS) + len(BACKGROUND_TICKERS),
    }


@mcp.tool()
def get_sentiment(ctx: Context, ticker: str, limit: int = 25) -> dict[str, Any]:
    """Return the most recent FinBERT-scored headlines for `ticker`.

    Each headline has a `sentiment_score` in [-1, +1] (positive_prob -
    negative_prob) and a categorical `sentiment_label`.  Costs
    ceil(limit / 25) API credits — same billing as GET /v1/sentiment/{ticker}.

    `limit` is capped at 100.  Titles come back reverse-chronological so the
    first item is the freshest.
    """
    limit = max(1, min(limit, 100))
    api_key, db = _open_authed_session(ctx)
    try:
        calls = math.ceil(limit / 25)
        _bill(api_key, db, calls, endpoint="sentiment")

        rows = db.query(models.Headline).filter(
            models.Headline.ticker == ticker.upper(),
        ).order_by(models.Headline.published_at.desc()).limit(limit).all()

        if not rows:
            return {"ticker": ticker.upper(), "data": [], "note": "No data found."}

        return {
            "ticker": ticker.upper(),
            "limit": limit,
            "calls_used": calls,
            "data": [
                {
                    "date": h.published_at.isoformat() + "Z" if h.published_at else None,
                    "title": h.title,
                    "source": h.source,
                    "sentiment_score": h.sentiment_score,
                    "sentiment_label": h.sentiment_label,
                } for h in rows
            ],
        }
    finally:
        db.close()


@mcp.tool()
def get_summary(ctx: Context, ticker: str, days: int = 30) -> dict[str, Any]:
    """Return daily aggregated sentiment for `ticker` over the last `days`.

    Each entry has `avg_sentiment` (unweighted mean of that day's scores),
    `article_count`, and a directional `label` (positive / negative /
    neutral, thresholded at ±0.1).  Costs `days` API credits — same as
    GET /v1/summary/{ticker}.
    """
    days = max(1, min(days, 365))
    api_key, db = _open_authed_session(ctx)
    try:
        _bill(api_key, db, days, endpoint="summary")
        since = datetime.utcnow() - timedelta(days=days)
        rows = db.query(models.Headline).filter(
            models.Headline.ticker == ticker.upper(),
            models.Headline.published_at >= since,
        ).all()

        if not rows:
            return {"ticker": ticker.upper(), "data": [], "note": "No data in window."}

        by_date: dict[str, list[float]] = defaultdict(list)
        for h in rows:
            by_date[str(h.published_at.date())].append(h.sentiment_score)

        out = []
        for d in sorted(by_date.keys(), reverse=True):
            scores = by_date[d]
            avg = sum(scores) / len(scores)
            out.append({
                "date": d,
                "avg_sentiment": round(avg, 4),
                "article_count": len(scores),
                "label": "positive" if avg > 0.1 else "negative" if avg < -0.1 else "neutral",
            })

        return {
            "ticker": ticker.upper(),
            "days": days,
            "calls_used": days,
            "data": out,
        }
    finally:
        db.close()


@mcp.tool()
def get_prices(ctx: Context, ticker: str, days: int = 30) -> dict[str, Any]:
    """Return daily GBP close prices for `ticker` over the last `days`.

    Costs `days` API credits — same as GET /v1/prices/{ticker}.  Prices come
    back reverse-chronological.  FX pairs are quoted in the pair's native
    convention (e.g. USDJPY is yen per dollar); equity / ETF / commodity /
    crypto tickers are converted to GBP via the current USD/GBP rate.
    """
    days = max(1, min(days, 365))
    api_key, db = _open_authed_session(ctx)
    try:
        _bill(api_key, db, days, endpoint="prices")
        since = datetime.utcnow() - timedelta(days=days)
        rows = db.query(models.Price).filter(
            models.Price.ticker == ticker.upper(),
            models.Price.date >= since,
        ).order_by(models.Price.date.desc()).all()

        if not rows:
            return {"ticker": ticker.upper(), "data": [], "note": "No data in window."}

        return {
            "ticker": ticker.upper(),
            "days": days,
            "calls_used": days,
            "data": [
                {
                    "date": p.date.isoformat() if p.date else None,
                    "close_price_gbp": p.close_price,
                    "volume": p.volume,
                } for p in rows
            ],
        }
    finally:
        db.close()


@mcp.tool()
def get_correlation(ctx: Context, ticker: str) -> dict[str, Any]:
    """180-day Pearson correlation between daily sentiment shifts and next-day
    price returns for `ticker`.

    Returns Pearson `r`, `p_value`, `n_days` overlapping, a 95% confidence
    interval (Fisher z), and a categorical `strength` (strong / weak /
    inconclusive).  Costs 1 API credit — same as GET /v1/correlation/{ticker}.

    Requires ≥30 overlapping day-pairs.  Under that, returns a `note` field
    explaining what's missing so a caller can suggest waiting or switching
    to a higher-coverage ticker.
    """
    import numpy as np
    from scipy import stats

    api_key, db = _open_authed_session(ctx)
    try:
        _bill(api_key, db, 1, endpoint="correlation")
        since = datetime.utcnow() - timedelta(days=180)

        headlines = db.query(models.Headline).filter(
            models.Headline.ticker == ticker.upper(),
            models.Headline.published_at >= since,
        ).order_by(models.Headline.published_at).all()
        prices = db.query(models.Price).filter(
            models.Price.ticker == ticker.upper(),
            models.Price.date >= since,
        ).order_by(models.Price.date).all()

        if len(headlines) < 30 or len(prices) < 30:
            return {
                "ticker": ticker.upper(),
                "note": (
                    f"Not enough data yet — need ≥30 headlines and ≥30 price rows "
                    f"in the last 180d (have {len(headlines)} headlines, "
                    f"{len(prices)} prices)."
                ),
                "headlines_count": len(headlines),
                "prices_count": len(prices),
            }

        # Daily sentiment: mean of same-day scores; then 7d-rolling deviation.
        by_date: dict = defaultdict(list)
        for h in headlines:
            by_date[h.published_at.date()].append(h.sentiment_score)
        daily_sent = {d: sum(v) / len(v) for d, v in by_date.items()}
        daily_price = {p.date.date(): p.close_price for p in prices}

        common = sorted(set(daily_sent) & set(daily_price))
        if len(common) < 30:
            return {
                "ticker": ticker.upper(),
                "note": f"Only {len(common)} overlapping day-pairs (need 30).",
                "overlapping_days": len(common),
            }

        # 7-day rolling deviation of sentiment vs next-day price return
        shifts, returns = [], []
        for i, d in enumerate(common):
            if i < 7 or i + 1 >= len(common):
                continue
            prior = [daily_sent[common[j]] for j in range(i - 7, i)]
            shift = daily_sent[d] - sum(prior) / len(prior)
            next_d = common[i + 1]
            ret = (daily_price[next_d] - daily_price[d]) / daily_price[d]
            shifts.append(shift)
            returns.append(ret)

        if len(shifts) < 30:
            return {
                "ticker": ticker.upper(),
                "note": f"Only {len(shifts)} valid pairs after burn-in (need 30).",
                "pairs": len(shifts),
            }

        r, p = stats.pearsonr(shifts, returns)
        # Fisher z for CI
        z = 0.5 * math.log((1 + r) / (1 - r)) if abs(r) < 1 else 0.0
        se = 1 / math.sqrt(len(shifts) - 3)
        lo = math.tanh(z - 1.96 * se)
        hi = math.tanh(z + 1.96 * se)

        if abs(r) >= 0.3 and p < 0.05:
            strength = "strong"
        elif abs(r) >= 0.15 and p < 0.1:
            strength = "weak"
        else:
            strength = "inconclusive"

        return {
            "ticker": ticker.upper(),
            "r": round(float(r), 4),
            "p_value": round(float(p), 5),
            "n_pairs": len(shifts),
            "ci_95": [round(float(lo), 4), round(float(hi), 4)],
            "direction": "positive" if r > 0 else "negative" if r < 0 else "flat",
            "strength": strength,
            "window_days": 180,
            "calls_used": 1,
        }
    finally:
        db.close()
