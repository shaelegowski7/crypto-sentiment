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
import os
from datetime import datetime, timedelta
from collections import defaultdict
from typing import Any

from mcp.server.fastmcp import FastMCP, Context
from mcp.server.transport_security import TransportSecuritySettings

from . import models
from .database import SessionLocal
from .scraper import BACKGROUND_TICKERS


# The tickers list lives here (rather than importing from main.py) because
# main.py imports from this module, and reversing that creates a cycle.
_PRIMARY_TICKERS = [
    "BTC", "ETH", "SOL", "XRP", "DOGE",
    "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD",
]


# DNS-rebinding protection defaults to ON with an EMPTY allowlist, which
# rejects every Host header -- including our own -- with 421 "Invalid Host
# header".  That made the mounted server unusable no matter how it was called.
# Keep the protection (it's the right default for a local-first protocol) but
# name the hosts this server is actually reachable on.  MCP_ALLOWED_HOSTS lets
# a preview/staging domain be added without a code change.
_MCP_HOSTS = [h.strip() for h in os.getenv(
    "MCP_ALLOWED_HOSTS",
    "api.sentimentfx.org,localhost,localhost:8000,127.0.0.1,127.0.0.1:8000",
).split(",") if h.strip()]

mcp = FastMCP(
    name="SentimentFX",
    transport_security=TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=_MCP_HOSTS,
        # Browser-originated calls aren't a supported client for this server
        # (MCP clients are desktop/CLI), so allow the same set rather than "*".
        allowed_origins=[f"https://{h}" for h in _MCP_HOSTS] +
                        [f"http://{h}" for h in _MCP_HOSTS],
    ),
    instructions=(
        "SentimentFX exposes FinBERT-scored news sentiment (-1 to +1) and "
        "GBP prices for 42 assets spanning crypto, forex, US equities, ETFs "
        "and commodity futures.  Use `list_tickers` first to see what's "
        "available.  `get_summary` for daily sentiment aggregates, "
        "`get_sentiment` for the raw scored headlines, `get_prices` for "
        "historical closes, `get_correlation` for a 180-day Pearson stat "
        "between daily sentiment shifts and next-day price returns, "
        "`run_backtest` to simulate a trading signal against real historical "
        "data, `generate_pine_script` to export the price-only Donchian "
        "signal as a TradingView strategy.  All responses are in GBP; "
        "sentiment scores are pos_prob - neg_prob."
    ),
)


# ---------------------------------------------------------------------------
# Auth + billing plumbing shared by every tool
# ---------------------------------------------------------------------------

def _open_authed_session(ctx: Context, enforce_quota: bool = True):
    """Extract + validate the X-API-Key header from the underlying HTTP request.

    Returns `(api_key, db_session)`.  Caller is responsible for closing the
    session (use a try/finally).  We do NOT reuse a global session because
    MCP tool calls can arrive concurrently and SQLAlchemy sessions are not
    goroutine-safe.

    `enforce_quota=False` is for the free introspection tools only — being told
    you're out of calls is useless if the tool that reports your remaining
    balance is itself blocked.  Everything else must enforce, otherwise MCP
    becomes the way around the /v1 paywall (MCP_MIRRORS_V1).

    Late imports of `_hash_key` / `track_usage` avoid the main.py <-> mcp_server
    circular import — they're only needed inside the tool body.
    """
    from .main import _hash_key, track_usage  # noqa: F401 — track_usage re-exported later
    from .main import _allowance_exhausted, _quota_message

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

    if enforce_quota and _allowance_exhausted(key):
        message = _quota_message(key)
        db.close()
        raise ValueError(message)
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
def get_usage(ctx: Context) -> dict[str, Any]:
    """Introspect your API key: calls used this month, included allowance,
    remaining credits, and when the counter resets.

    Free — doesn't hit the billing meter.  Mirrors `GET /v1/usage`
    (MCP_MIRRORS_V1: change both together).  Useful before a large batch of
    `get_sentiment`/`get_summary` calls to check you have credit headroom.
    """
    from datetime import datetime

    # Mirrors /v1/usage's quota exemption — must stay readable once a free key
    # is spent, since this is where the caller finds out that it is.
    api_key, db = _open_authed_session(ctx, enforce_quota=False)
    try:
        now = datetime.utcnow()
        resets_at = datetime(now.year + (1 if now.month == 12 else 0),
                             1 if now.month == 12 else now.month + 1, 1)

        included = (api_key.free_calls or 0) + (api_key.monthly_allowance or 0)
        used = api_key.calls_this_month or 0

        if api_key.unlimited:
            plan = "unlimited"
        elif api_key.stripe_customer_id:
            plan = "metered"
        else:
            plan = "free"

        return {
            "key_prefix": api_key.key_prefix,
            "plan": plan,
            "calls_this_month": used,
            "calls_total": api_key.calls_used or 0,
            "included_allowance": included,
            "included_remaining": None if api_key.unlimited else max(included - used, 0),
            "overage_billing": bool(api_key.stripe_customer_id) and not api_key.unlimited,
            "resets_at": resets_at.isoformat() + "Z",
        }
    finally:
        db.close()


@mcp.tool()
def get_sentiment(ctx: Context, ticker: str, limit: int = 25) -> dict[str, Any]:
    """Return the most recent FinBERT-scored headlines for `ticker`.

    Each headline has a `sentiment_score` in [-1, +1] (positive_prob -
    negative_prob) and a categorical `sentiment_label`.  Costs 1 API credit
    per 25 headlines actually returned — same billing as
    GET /v1/sentiment/{ticker}.  An empty result costs nothing.

    `limit` is capped at 100.  Titles come back reverse-chronological so the
    first item is the freshest.
    """
    limit = max(1, min(limit, 100))
    api_key, db = _open_authed_session(ctx)
    try:
        rows = db.query(models.Headline).filter(
            models.Headline.ticker == ticker.upper(),
        ).order_by(models.Headline.published_at.desc()).limit(limit).all()

        if not rows:
            return {"ticker": ticker.upper(), "data": [], "note": "No data found."}

        # Billed after the query, on rows actually returned — an empty result
        # costs nothing.  Mirrors api_sentiment (MCP_MIRRORS_V1).
        calls = math.ceil(len(rows) / 25)
        _bill(api_key, db, calls, endpoint="sentiment")

        return {
            "ticker": ticker.upper(),
            "limit": limit,
            "returned": len(rows),
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
    neutral, thresholded at ±0.1).  Costs 1 API credit per day actually
    returned — same as GET /v1/summary/{ticker}.  A window with no coverage
    costs nothing.
    """
    days = max(1, min(days, 365))
    api_key, db = _open_authed_session(ctx)
    try:
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

        # Per day RETURNED, after the query — asking for 365 days of a ticker
        # with a week of coverage costs 7.  Mirrors api_summary.
        calls = len(out)
        _bill(api_key, db, calls, endpoint="summary")

        return {
            "ticker": ticker.upper(),
            "days": days,
            "returned": len(out),
            "calls_used": calls,
            "data": out,
        }
    finally:
        db.close()


@mcp.tool()
def get_prices(ctx: Context, ticker: str, days: int = 30) -> dict[str, Any]:
    """Return daily close prices for `ticker` over the last `days`, in the
    ticker's native currency -- see the `currency` field on the response.

    Costs 1 API credit per day actually returned — same as
    GET /v1/prices/{ticker}.  Prices come
    back reverse-chronological.  Crypto is GBP (yfinance BTC-GBP etc.), FX
    pairs are a raw exchange rate in the pair's native convention (e.g.
    USDJPY is yen per dollar), and everything else (stocks/ETFs/commodity
    futures) is native USD -- there is no currency conversion.
    """
    days = max(1, min(days, 365))
    api_key, db = _open_authed_session(ctx)
    try:
        since = datetime.utcnow() - timedelta(days=days)
        rows = db.query(models.Price).filter(
            models.Price.ticker == ticker.upper(),
            models.Price.date >= since,
        ).order_by(models.Price.date.desc()).all()

        if not rows:
            return {"ticker": ticker.upper(), "data": [], "note": "No data in window."}

        from .main import _category_for
        category = _category_for(ticker.upper())
        currency = "GBP" if category == "crypto" else "RATE" if category == "fx" else "USD"

        # Per day RETURNED, after the query.  Mirrors api_prices.
        calls = len(rows)
        _bill(api_key, db, calls, endpoint="prices")

        return {
            "ticker": ticker.upper(),
            "days": days,
            "returned": len(rows),
            "calls_used": calls,
            "currency": currency,
            "data": [
                {
                    "date": p.date.isoformat() if p.date else None,
                    "close_price": p.close_price,
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


@mcp.tool()
def run_backtest(
    ctx: Context,
    ticker: str,
    signal: str = "shift",
    hold_days: int = 7,
    direction_mode: str = "momentum",
    donchian_n: int | None = None,
    costs_bps: int | None = None,
) -> dict[str, Any]:
    """Backtest a trading signal for `ticker` over the last 365 days using
    SentimentFX's own historical sentiment + price data.  A real simulation
    (long-only, one trade at a time, cost-adjusted) — not advice, and no
    funds are moved by calling this.

    `signal`: "shift" (default — sentiment shift vs its own 7-day trailing
    average) / "divergence" (sentiment vs price moving opposite ways) /
    "donchian" (price-only trend breakout — see `generate_pine_script` to
    export this one as a real TradingView strategy).  `donchian` is the
    only signal here with genuine out-of-sample support; `shift` and
    `divergence` are in-sample research over SentimentFX's proprietary
    sentiment feed and have not shown a durable out-of-sample edge — treat
    their numbers as exploratory, not a strategy to trade.

    `direction_mode`: "momentum" (default, trade with the signal) or
    "contrarian" (fade it).  `hold_days` (1-30) is the fixed holding period
    per trade.  `donchian_n` (5-200, default 20) is the breakout lookback,
    only used when signal="donchian".  `costs_bps` overrides the built-in
    per-asset-class round-trip cost estimate (crypto ~30bps, FX ~15bps,
    stocks ~6bps, etc).

    Costs 1 API credit regardless of outcome (the DB scan happens either
    way) — same meter as the other tools.  Returns gross and net (after
    costs) summaries plus buy-and-hold for comparison; `note` explains a
    "no trades" or "not enough data" result.
    """
    from .main import _build_daily_series, _simulate_trades, _costs_pct_for

    if signal not in ("divergence", "shift", "donchian"):
        raise ValueError("signal must be 'divergence', 'shift' or 'donchian'")
    if direction_mode not in ("momentum", "contrarian"):
        raise ValueError("direction_mode must be 'momentum' or 'contrarian'")
    hold_days = max(1, min(hold_days, 30))
    if donchian_n is not None:
        donchian_n = max(5, min(donchian_n, 200))
    if costs_bps is not None:
        costs_bps = max(0, min(costs_bps, 500))

    api_key, db = _open_authed_session(ctx)
    try:
        # Billed up front, same as get_correlation — the 365d headline+price
        # scan costs the same whether or not the signal ends up firing.
        _bill(api_key, db, 1, endpoint="backtest")

        since = datetime.utcnow() - timedelta(days=365)
        headlines = db.query(models.Headline).filter(
            models.Headline.ticker == ticker.upper(),
            models.Headline.published_at >= since,
        ).order_by(models.Headline.published_at).all()
        prices = db.query(models.Price).filter(
            models.Price.ticker == ticker.upper(),
            models.Price.date >= since,
        ).order_by(models.Price.date).all()

        needs_sentiment = signal != "donchian"
        if len(prices) < 30 or (needs_sentiment and len(headlines) < 20):
            return {
                "ticker": ticker.upper(), "signal": signal, "calls_used": 1,
                "note": "Not enough data in the last 365d to backtest this ticker/signal.",
            }

        daily_sentiment, daily_price, common = _build_daily_series(headlines, prices)
        if needs_sentiment and len(common) < 20:
            return {
                "ticker": ticker.upper(), "signal": signal, "calls_used": 1,
                "note": "Not enough overlapping sentiment/price data to backtest this window.",
            }

        trades = _simulate_trades(
            daily_sentiment, daily_price, common, signal, hold_days,
            direction_mode=direction_mode, donchian_n=donchian_n,
        )
        if not trades:
            return {
                "ticker": ticker.upper(), "signal": signal, "hold_days": hold_days,
                "calls_used": 1,
                "note": "Signal did not fire in this window — no trades generated.",
            }

        # Mirrors get_backtest's _agg / _compact_backtest_summary's _stats in
        # main.py (marker: MCP_MIRRORS_BACKTEST) — kept as its own small copy
        # rather than a shared helper since the MCP shape (no equity curve,
        # no per-trade list) genuinely differs from the HTTP response.
        costs_pct = _costs_pct_for(ticker, costs_bps)
        size_frac = 1.0  # run_backtest doesn't expose size_pct — full-size only
        costs_sized = costs_pct * size_frac

        def _stats(returns: list[float]) -> dict[str, Any]:
            n = len(returns)
            winning = sum(1 for r in returns if r > 0)
            compounded = 100.0
            for r in returns:
                compounded *= (1 + r / 100)
            return {
                "trades": n,
                "win_rate": round(winning / n, 3),
                "avg_return_pct": round(sum(returns) / n, 2),
                "total_return_pct": round(compounded - 100, 2),
            }

        sized_returns = [t["sized_return_pct"] for t in trades]
        gross = _stats(sized_returns)
        net = _stats([r - costs_sized for r in sized_returns])

        sorted_dates = sorted(daily_price.keys())
        first_price, last_price = daily_price[sorted_dates[0]], daily_price[sorted_dates[-1]]
        buy_hold = round((last_price - first_price) / first_price * 100, 2)
        net["buy_hold_return_pct"] = buy_hold
        net["alpha_pct"] = round(net["total_return_pct"] - buy_hold, 2)

        return {
            "ticker": ticker.upper(),
            "signal": signal,
            "hold_days": hold_days,
            "direction_mode": direction_mode,
            "window_days": (sorted_dates[-1] - sorted_dates[0]).days,
            "calls_used": 1,
            "costs_pct_per_trade": costs_pct,
            "summary": {"gross": gross, "net": net},
            "recent_trades": [
                {
                    "entry_date": str(t["entry_date"]),
                    "exit_date": str(t["exit_date"]),
                    "return_pct": round(t["sized_return_pct"], 2),
                    "exit_reason": t["exit_reason"],
                } for t in trades[-10:]
            ],
        }
    finally:
        db.close()


@mcp.tool()
def generate_pine_script(ticker: str, donchian_n: int = 20) -> dict[str, Any]:
    """Generate a real TradingView Pine Script v5 strategy for `ticker`,
    exporting SentimentFX's price-only Donchian breakout signal — long once
    price closes above the trailing `donchian_n`-bar high, flat once it
    closes below the trailing low.

    This is the *only* SentimentFX signal exported here on purpose: it's
    price-only and the one with genuine out-of-sample support (see
    `run_backtest`'s docstring).  The sentiment-driven signals (`shift`,
    `divergence`) can't be turned into a standalone Pine script — Pine has
    no way to pull SentimentFX's live FinBERT scores — so those only run
    inside `run_backtest`.  Paste the output straight into TradingView's
    Pine Editor to chart or backtest it there; nothing here places trades
    or touches TradingView on your behalf.

    Free — no API key required, doesn't hit the billing meter.

    Note on fidelity: this Pine strategy is *continuous* (stays long from
    breakout to breakdown); `run_backtest(signal="donchian")` simulates the
    same rule as discrete fixed `hold_days` trades instead, so the two
    won't produce identical numbers on the same window — same signal,
    different execution model.
    """
    ticker = ticker.upper()
    donchian_n = max(5, min(donchian_n, 200))
    script = f"""//@version=5
strategy("SentimentFX Donchian Breakout - {ticker}", overlay=true,
     initial_capital=100, default_qty_type=strategy.percent_of_equity,
     default_qty_value=100)

// Price-only trend signal, long-only. Long once price closes above the
// trailing n-bar high, flat once it closes below the trailing n-bar low.
// Same rule SentimentFX's run_backtest(signal="donchian") tool tests
// out-of-sample -- see that tool's docstring before trading this on size.
n = input.int({donchian_n}, "Lookback (bars)", minval=5, maxval=200)

upper = ta.highest(close, n)[1]
lower = ta.lowest(close, n)[1]

var bool inUptrend = na
if close > upper
    inUptrend := true
if close < lower
    inUptrend := false

if inUptrend and strategy.position_size == 0
    strategy.entry("Long", strategy.long)
if not inUptrend and strategy.position_size > 0
    strategy.close("Long")

plot(upper, "Donchian Upper", color=color.new(color.green, 0))
plot(lower, "Donchian Lower", color=color.new(color.red, 0))
"""
    return {
        "ticker": ticker,
        "strategy": "donchian",
        "donchian_n": donchian_n,
        "pine_script": script,
        "note": (
            "Price-only strategy, safe to paste directly into TradingView's "
            "Pine Editor. SentimentFX's sentiment-driven signals (shift, "
            "divergence) aren't exportable this way — run them via "
            "run_backtest instead."
        ),
    }
