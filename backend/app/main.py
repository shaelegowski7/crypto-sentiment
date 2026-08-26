from fastapi import FastAPI, Depends, HTTPException, Header, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy import or_ as sa_or, func as sa_func
from fastapi import Request
from . import models, schemas
from .database import engine, get_db
from .scraper import fetch_headlines, fetch_rss_headlines, BACKGROUND_TICKERS, fetch_background_headlines, fetch_hn_headlines, HN_QUERIES, fetch_stocktwits_headlines, fetch_x_headlines
from .ai_sources import fetch_ai_headlines
from .sentiment import analyse_sentiment
from .prices import fetch_prices, fetch_latest_price, fetch_latest_prices_all, fetch_latest_stock_price
from .candles import fetch_intraday_prices, INTRADAY_BACKFILL_PERIOD, INTRADAY_REFRESH_PERIOD
from datetime import datetime, timedelta, timezone
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from app.brief import send_morning_briefs
from app.trade_card import build_trade_card, format_trade_card_html, format_trade_card_text
from . import models
import math
from .database import SessionLocal
from scipy.stats import pearsonr
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from fastapi.responses import JSONResponse, Response
from fastapi.exceptions import RequestValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from collections import defaultdict
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter, Gauge, Histogram
import numpy as np
import resend
import os
import uuid
import json
import requests
import stripe
import csv
import io
import secrets
import hashlib
from apscheduler.triggers.cron import CronTrigger
import time
import base64

last_scrape_time = None
last_scrape_duration = None

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
METRICS_TOKEN = os.getenv("METRICS_TOKEN")

models.Base.metadata.create_all(bind=engine)

# Idempotent schema patches for tables that already exist in prod.  We don't
# use Alembic here, and `create_all` doesn't add columns to existing tables,
# so any new APIKey/etc. column we introduce needs a hand-rolled ALTER guarded
# by `IF NOT EXISTS` (Postgres 9.6+).  Failure to add doesn't crash startup —
# the column may already be there or the DB user may lack DDL rights; we log
# and continue, and the app will 500 loudly on first use if the schema is
# genuinely wrong.
def _apply_startup_ddl_patches():
    from sqlalchemy import text as _text
    patches = [
        "ALTER TABLE api_keys ADD COLUMN IF NOT EXISTS unlimited BOOLEAN DEFAULT FALSE NOT NULL",
        # Widened deal sources (ScraperAI / StockTwits / X): body holds full
        # article text where available, source_type tags the origin.  Both
        # nullable so existing rows are unaffected.
        "ALTER TABLE headlines ADD COLUMN IF NOT EXISTS body TEXT",
        "ALTER TABLE headlines ADD COLUMN IF NOT EXISTS source_type VARCHAR",
        # OHLC for candlestick charts — nullable, see models.Price docstring.
        "ALTER TABLE prices ADD COLUMN IF NOT EXISTS open_price DOUBLE PRECISION",
        "ALTER TABLE prices ADD COLUMN IF NOT EXISTS high_price DOUBLE PRECISION",
        "ALTER TABLE prices ADD COLUMN IF NOT EXISTS low_price DOUBLE PRECISION",
    ]
    try:
        with engine.begin() as conn:
            for stmt in patches:
                try:
                    conn.execute(_text(stmt))
                except Exception as e:
                    print(f"[STARTUP-DDL] skipped '{stmt}': {e}")
    except Exception as e:
        print(f"[STARTUP-DDL] transaction failed: {e}")
_apply_startup_ddl_patches()

def get_api_key_value(request: Request) -> str:
    """Rate limit by API key header, fall back to IP."""
    return request.headers.get("x-api-key") or get_remote_address(request)

# headers_enabled=True → SlowAPIMiddleware stamps X-RateLimit-Limit /
# X-RateLimit-Remaining / X-RateLimit-Reset (+ Retry-After on 429) onto every
# rate-limited route's response — standard developer-API behaviour.
limiter = Limiter(key_func=get_api_key_value, headers_enabled=True)

# ---------------------------------------------------------------------------
# NaN-safe JSON response
# ---------------------------------------------------------------------------
# Starlette's JSONResponse encodes with allow_nan=False, so any float('nan')
# or inf bubbling up from a handler (numpy stats, empty-bucket averages, etc.)
# crashes the request with "Out of range float values are not JSON compliant".
# Strict JSON has no representation for NaN/Inf, so the standard fix is to
# coerce to None at the boundary.  Registered as the FastAPI default so every
# endpoint gets the safety net without per-route changes.
def _scrub_non_finite(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _scrub_non_finite(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_scrub_non_finite(v) for v in obj]
    return obj


class SafeJSONResponse(JSONResponse):
    def render(self, content) -> bytes:
        return super().render(_scrub_non_finite(content))


app = FastAPI(default_response_class=SafeJSONResponse)

# ---------------------------------------------------------------------------
# Prometheus metrics
# ---------------------------------------------------------------------------

HEADLINES_INGESTED = Counter(
    "sfx_headlines_ingested_total",
    "Headlines successfully scored and stored",
    ["source", "ticker"],
)
SCRAPER_RUNS = Counter(
    "sfx_scraper_runs_total",
    "Scraper executions",
    ["status"],  # success | failure
)
SCRAPER_DURATION = Histogram(
    "sfx_scraper_duration_seconds",
    "How long a full scrape cycle takes",
)
ACTIVE_SUBSCRIPTIONS = Gauge(
    "sfx_active_subscriptions",
    "Active Stripe subscriptions",
    ["plan"],  # pro_monthly | pro_annual
)
API_CALLS = Counter(
    "sfx_api_calls_total",
    "Metered developer API calls",
    ["endpoint"],
)
FINBERT_LATENCY = Histogram(
    "sfx_finbert_latency_seconds",
    "FinBERT scoring latency per headline",
)

Instrumentator(
    should_group_status_codes=True,
    should_ignore_untemplated=True,
    excluded_handlers=["/metrics", "/health"],
).instrument(app).expose(
    app,
    endpoint="/metrics",
    include_in_schema=False,
)

# ---------------------------------------------------------------------------
# Metrics auth middleware — must be added before other middleware
# ---------------------------------------------------------------------------

@app.middleware("http")
async def protect_metrics(request: Request, call_next):
    if request.url.path == "/metrics":
        if METRICS_TOKEN:
            auth = request.headers.get("authorization", "")
            if auth.startswith("Basic "):
                try:
                    decoded = base64.b64decode(auth[6:]).decode()
                    password = decoded.split(":", 1)[-1]
                    if password != METRICS_TOKEN:
                        return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
                except Exception:
                    return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            elif auth.startswith("Bearer "):
                if auth[7:] != METRICS_TOKEN:
                    return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
            else:
                return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


# ---------------------------------------------------------------------------
# Cache-Control middleware — allows CDN intermediaries and client browsers to
# cache hot read-only endpoints (public API, status, dashboard data) for short
# windows.  Writes and authed/personal endpoints get explicit no-store so they
# never leak between users.
# ---------------------------------------------------------------------------

_PUBLIC_GET_PREFIXES = ("/v1/",)
_PUBLIC_GET_PATHS = {"/", "/status", "/health", "/leaderboard", "/track-record", "/brief/latest", "/brief/archive"}
_PUBLIC_GET_DATA_PREFIXES = ("/dashboard/", "/correlation/", "/headlines/", "/prices/", "/summary/", "/brief/")

@app.middleware("http")
async def cache_control(request: Request, call_next):
    response = await call_next(request)
    if request.method != "GET" or response.status_code >= 400:
        return response
    path = request.url.path
    # Skip if endpoint already set Cache-Control explicitly
    if "cache-control" in (k.lower() for k in response.headers.keys()):
        return response
    if any(path.startswith(p) for p in _PUBLIC_GET_PREFIXES):
        # Developer API — 60s browser, 5min CDN, 10min stale-while-revalidate
        response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300, stale-while-revalidate=600"
        response.headers["Vary"] = "Accept-Encoding, X-API-Key"
    elif path in _PUBLIC_GET_PATHS:
        response.headers["Cache-Control"] = "public, max-age=30, s-maxage=60, stale-while-revalidate=300"
        response.headers["Vary"] = "Accept-Encoding"
    elif any(path.startswith(p) for p in _PUBLIC_GET_DATA_PREFIXES):
        response.headers["Cache-Control"] = "public, max-age=60, s-maxage=300, stale-while-revalidate=600"
        response.headers["Vary"] = "Accept-Encoding"
    return response

# ---------------------------------------------------------------------------

app.state.limiter = limiter
app.add_middleware(GZipMiddleware, minimum_size=1000)


@app.middleware("http")
async def add_request_id(request: Request, call_next):
    """Stamp every response with a request id so support tickets and logs can
    be correlated ("what happened to req_ab12…?").  Cheap: one uuid per hit."""
    request.state.request_id = f"req_{uuid.uuid4().hex[:16]}"
    response = await call_next(request)
    response.headers["X-Request-Id"] = request.state.request_id
    return response


# ---------------------------------------------------------------------------
# /v1 error envelope
# ---------------------------------------------------------------------------
# Every /v1 error responds with the same shape — {"error": {"type", "message"}}
# — instead of FastAPI's bare {"detail": "..."}, so SDKs can branch on
# `error.type` (Stripe/Anthropic-style) rather than parsing prose. Scoped to
# /v1/* only: dashboard/admin/webhook routes keep their existing {"detail"}
# shape since the frontend already parses that.

def _v1_error_type(status_code: int) -> str:
    return {
        400: "invalid_request_error",
        401: "authentication_error",
        403: "permission_error",
        404: "not_found_error",
        422: "invalid_request_error",
        429: "rate_limit_error",
    }.get(status_code, "api_error")


def _is_v1_path(request: Request) -> bool:
    return request.url.path.startswith("/v1/")


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(request: Request, exc: StarletteHTTPException):
    if not _is_v1_path(request):
        # Preserve FastAPI's default shape for every non-/v1 route.
        return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail}, headers=exc.headers)
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"type": _v1_error_type(exc.status_code), "message": exc.detail}},
        headers=exc.headers,
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    if not _is_v1_path(request):
        return JSONResponse(status_code=422, content={"detail": exc.errors()})
    # exc.errors() is a list of pydantic error dicts — collapse to one
    # readable message rather than exposing the raw structure.
    first = exc.errors()[0] if exc.errors() else {}
    loc = " -> ".join(str(p) for p in first.get("loc", []) if p != "query")
    message = f"{loc}: {first.get('msg')}" if loc else (first.get("msg") or "Invalid request")
    return JSONResponse(
        status_code=422,
        content={"error": {"type": "invalid_request_error", "message": message}},
    )


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    message = f"Rate limit exceeded. {exc.detail}"
    content = (
        {"error": {"type": "rate_limit_error", "message": message}}
        if _is_v1_path(request)
        else {"detail": message}
    )
    response = JSONResponse(status_code=429, content=content)
    # Our custom handler bypasses slowapi's default, so re-inject the
    # X-RateLimit-*/Retry-After headers it would have set.
    try:
        response = request.app.state.limiter._inject_headers(response, request.state.view_rate_limit)
    except Exception:
        pass
    return response

app.add_middleware(SlowAPIMiddleware)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://app.sentimentfx.org",
        "https://sentimentfx.org",
        "https://www.sentimentfx.org",
        "https://developers.sentimentfx.org",
        "http://localhost:5173",
        "https://status.sentimentfx.org",
    ],
    allow_methods=["*"],
    allow_headers=["*"],
    # Let browser-based API clients read the rate-limit and correlation headers.
    expose_headers=[
        "X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset",
        "Retry-After", "X-Request-Id",
    ],
)

TICKERS = ["BTC", "ETH", "SOL", "XRP", "DOGE", "EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"]

TICKER_CATEGORIES = {
    "crypto":      {"BTC", "ETH", "SOL", "XRP", "DOGE"},
    "fx":          {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"},
    "stocks":      {"AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
                    "JPM", "BAC", "GS", "V", "MA",
                    "XOM", "JNJ", "AMD", "NFLX", "WMT", "UBER", "CRM", "PLTR"},
    "etfs":        {"SPY", "QQQ", "GLD", "SLV", "USO", "ARKK"},
    "commodities": {"GC=F", "SI=F", "CL=F", "NG=F"},
}


def _category_for(ticker: str) -> str:
    for cat, members in TICKER_CATEGORIES.items():
        if ticker in members:
            return cat
    return "other"

# Stripe price IDs for Data tier
DATA_PRICE_IDS = {
    "price_1TUqVG2NzVdYK0wrKrPTE28e",
    "price_1TUqVx2NzVdYK0wryheortJg",
}
# Stripe price IDs for the Brief-only tier (£~10/mo).  Sourced from env so the
# user can spin up Stripe prices independently and just set BRIEF_PRICE_ID_*
# without a redeploy.  Empty set if not configured — checkout still works for
# Pro/Data, but the brief tier can't be purchased until prices are wired.
BRIEF_PRICE_IDS = {
    p for p in (
        os.getenv("BRIEF_PRICE_ID_MONTHLY"),
        os.getenv("BRIEF_PRICE_ID_YEARLY"),
    ) if p
}
PRO_MONTHLY_ALLOWANCE = 1000
DATA_MONTHLY_ALLOWANCE = 5000

# Tiers that get full access to the morning brief archive content.  Free
# users see the AI-summary paragraph + ticker list only; anyone in this set
# sees the full per-ticker breakdown that subscribers received by email.
_BRIEF_FULL_ACCESS_TIERS = {"brief", "pro", "data"}


# ---------------------------------------------------------------------------
# API key helpers
# ---------------------------------------------------------------------------

def _hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()

def _make_key() -> str:
    return "sfx_" + secrets.token_hex(24)

def _create_stripe_customer(email: str):
    try:
        customer = stripe.Customer.create(email=email)
        subscription = stripe.Subscription.create(
            customer=customer.id,
            items=[{"price": "price_1TO3DG2NzVdYK0wrxIRggage"}],
        )
        return customer.id, subscription.id
    except Exception as e:
        print(f"Stripe error: {e}")
        return None, None


# ---------------------------------------------------------------------------
# Auth dependencies
# ---------------------------------------------------------------------------

async def require_super_admin(authorization: str = Header(None)):
    """Email-allowlisted admin gate.  Uses Supabase JWT verification (same path
    as require_pro) but checks the user's email against the ADMIN_EMAILS env
    var (comma-separated, case-insensitive).  Cheaper than provisioning a
    separate admin token and works with the existing dashboard login flow —
    just sign in normally with an allowlisted email."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization token")
    token = authorization.split(" ")[1]
    try:
        from supabase import create_client
        supabase_client = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_KEY"),
        )
        user_resp = supabase_client.auth.get_user(token)
        user = user_resp.user
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")
        allowlist = {
            e.strip().lower()
            for e in (os.getenv("ADMIN_EMAILS") or "").split(",")
            if e.strip()
        }
        if not allowlist:
            # If unset, deny — never allow open admin access by misconfiguration.
            raise HTTPException(status_code=403, detail="Admin allowlist not configured")
        if (user.email or "").lower() not in allowlist:
            raise HTTPException(status_code=403, detail="Admin only")
        return user
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Auth error: {str(e)}")


async def require_pro(authorization: str = Header(None)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization token")

    token = authorization.split(" ")[1]

    try:
        from supabase import create_client
        supabase_client = create_client(
            os.getenv("SUPABASE_URL"),
            os.getenv("SUPABASE_SERVICE_KEY")
        )
        user_resp = supabase_client.auth.get_user(token)
        user = user_resp.user
        if not user:
            raise HTTPException(status_code=401, detail="Invalid token")

        profile = supabase_client.table("profiles").select("tier").eq("id", user.id).single().execute()
        tier = profile.data.get("tier")

        if tier not in ("pro", "data"):
            raise HTTPException(status_code=403, detail="Pro subscription required")

        return user
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Auth error: {str(e)}")


async def require_admin(secret: str = None):
    admin_secret = os.getenv("ADMIN_SECRET")
    if not admin_secret or secret != admin_secret:
        raise HTTPException(status_code=401, detail="Unauthorized")


def get_api_key(x_api_key: str = Header(None), db: Session = Depends(get_db)):
    if not x_api_key:
        raise HTTPException(status_code=401, detail="Missing API key")

    key_hash = _hash_key(x_api_key)
    key = db.query(models.APIKey).filter(
        models.APIKey.key_hash == key_hash,
        models.APIKey.active == True
    ).first()
    if not key:
        raise HTTPException(status_code=401, detail="Invalid API key")
    return key


# ---------------------------------------------------------------------------
# Scheduled jobs
# ---------------------------------------------------------------------------

# Backtest-gated alerts: two thresholds a ticker must clear for an alert to
# fire.  A ticker whose OOS net is ≤0 or whose walk-forward positive-fold
# ratio is <70% is deemed to lack "our own backtest thinks the edge is real"
# — we suppress the alert rather than deliver a signal we cannot defend.
# The numbers are refreshed daily by _refresh_signal_quality(); check_alerts
# just reads the cached SignalQuality row (hot path stays fast).
_SIGNAL_QUALITY_MIN_OOS_NET_PCT   = 0.0    # strictly positive
_SIGNAL_QUALITY_MIN_WF_POS_RATIO  = 0.7    # ≥70% of walk-forward folds net-positive


def _signal_quality_gate(db, ticker: str) -> tuple[bool, str, dict | None]:
    """Consult the cached SignalQuality snapshot for `ticker`.

    Returns (gate_ok, reason, snapshot_dict).  When no row exists (fresh
    ticker, first-day-of-deploy, refresh crashed), we fail CLOSED with a
    reason of "no snapshot yet" so alerts don't fire on unvalidated tickers.
    Startup runs _refresh_signal_quality once so this only happens for
    genuinely-new tickers or after a wiped DB.
    """
    row = db.query(models.SignalQuality).filter(
        models.SignalQuality.ticker == ticker.upper()
    ).first()
    if row is None:
        return False, "no snapshot yet — will retry after next daily refresh", None
    snapshot = {
        "gate_ok": row.gate_ok,
        "oos_net_pct": row.oos_net_pct,
        "wf_pct_folds_positive": row.wf_pct_folds_positive,
        "wf_folds_total": row.wf_folds_total,
        "wf_folds_positive": row.wf_folds_positive,
        "n_trades_full": row.n_trades_full,
        "reason": row.reason,
        "computed_at": row.computed_at.isoformat() + "Z" if row.computed_at else None,
    }
    return bool(row.gate_ok), row.reason or ("gate passed" if row.gate_ok else "gate failed"), snapshot


def check_alerts(db):
    alerts = db.query(models.Alert).filter(models.Alert.active == True).all()

    for alert in alerts:
        recent = db.query(models.Headline).filter(
            models.Headline.ticker == alert.ticker,
            models.Headline.published_at >= datetime.utcnow() - timedelta(hours=24)
        ).all()

        if not recent:
            continue

        filtered = [h.sentiment_score for h in recent if abs(h.sentiment_score) > 0.05]
        avg_score = sum(filtered) / len(filtered) if filtered else 0


        triggered = (
            alert.direction == "above" and avg_score >= alert.threshold
        ) or (
            alert.direction == "below" and avg_score <= alert.threshold
        )

        if triggered:
            # Backtest gate — suppress alerts on tickers our own backtest can't
            # validate.  A user who set an alert on a gated ticker gets nothing
            # this cycle; the log entry names the reason so we can inspect via
            # /admin/signal-quality and (eventually) surface it in the UI.
            gate_ok, gate_reason, gate_snapshot = _signal_quality_gate(db, alert.ticker)
            if not gate_ok:
                print(f"[ALERT-GATE] {alert.ticker}: suppressing alert — {gate_reason}")
                continue
            # Build the full trade card BEFORE sending — if the card fails to
            # build (eg. backtest DB queries crash), we'd rather log and skip
            # than fall back to a bare-data email that under-delivers value.
            try:
                card = build_trade_card(db, alert.ticker)
            except Exception as e:
                print(f"Alert trade-card build error for {alert.ticker}: {e}")
                continue

            # Subject reflects the recommendation, not the bare threshold —
            # this is what shows up in the user's inbox preview and is the
            # single biggest pull on whether they open the email.
            if card["direction"] == "NEUTRAL":
                subject = f"SentimentFX: {alert.ticker} alert triggered"
            else:
                conf = card["confidence"].upper()
                subject = f"SentimentFX: {card['direction']} signal on {alert.ticker} · {conf} confidence"

            try:
                resend.api_key = os.getenv("RESEND_API_KEY")
                resend.Emails.send({
                    "from": "SentimentFX <hello@sentimentfx.org>",
                    "to": alert.email,
                    "subject": subject,
                    "html": format_trade_card_html(card),
                    # Plain-text fallback for clients that prefer it (or strip HTML).
                    # Mail providers also use this to score deliverability.
                    "text": format_trade_card_text(card),
                })
                alert.active = False
                alert.fired_at = datetime.utcnow()

                # Log an outcome row for every fired alert that recommended a
                # trade — feeds the future /track-record page.  Skip NEUTRAL
                # cards (no trade to settle).  Entry price = latest known close
                # on the Price table at fire time.  Settlement happens later
                # via the settle_alert_outcomes scheduler.
                if card["direction"] in ("LONG", "SHORT"):
                    latest_price = db.query(models.Price).filter(
                        models.Price.ticker == alert.ticker
                    ).order_by(models.Price.date.desc()).first()
                    entry_price = latest_price.close_price if latest_price else None
                    if entry_price is None:
                        print(f"[ALERT-OUTCOME] {alert.ticker}: no price on record, skipping outcome log")
                    else:
                        db.add(models.AlertOutcome(
                            alert_id=alert.id,
                            user_id=alert.user_id,
                            email=alert.email,
                            ticker=alert.ticker,
                            direction=card["direction"],
                            confidence=card["confidence"],
                            hold_days=card.get("exit_in_days") or 7,
                            fired_at=datetime.utcnow(),
                            entry_price=entry_price,
                            card_snapshot=json.dumps(card, default=str),
                        ))
                db.commit()
            except Exception as e:
                print(f"Alert email error: {e}")


def settle_alert_outcomes():
    """Settle alert outcomes whose hold period has elapsed.

    For each unsettled AlertOutcome where fired_at + hold_days <= now, look up
    the latest Price row for that ticker and record the exit + signed return.
    Stocks settle on the next trading day after the target if the target falls
    on a weekend — we just take whichever close is most recent, which biases
    toward "actual realisable price" rather than ghost weekend marks.

    Runs once a day; one missed settlement is fine — picked up next pass.
    """
    db = SessionLocal()
    settled_count = 0
    try:
        now = datetime.utcnow()
        # Pull unsettled outcomes that have ripened
        pending = db.query(models.AlertOutcome).filter(
            models.AlertOutcome.settled == False,
            models.AlertOutcome.entry_price.isnot(None),
        ).all()

        for outcome in pending:
            target_exit = outcome.fired_at + timedelta(days=outcome.hold_days)
            if now < target_exit:
                continue   # not ripe yet

            # Find latest price AT OR AFTER target_exit.  If no price has been
            # logged that late, fall back to the most recent overall — this
            # happens for low-coverage tickers; better an imperfect settlement
            # than leaving rows unsettled forever.
            exit_row = db.query(models.Price).filter(
                models.Price.ticker == outcome.ticker,
                models.Price.date >= target_exit,
            ).order_by(models.Price.date.asc()).first()
            if exit_row is None:
                exit_row = db.query(models.Price).filter(
                    models.Price.ticker == outcome.ticker,
                ).order_by(models.Price.date.desc()).first()
            if exit_row is None:
                continue   # ticker has zero price data — punt

            exit_price = exit_row.close_price
            entry = outcome.entry_price

            if outcome.direction == "LONG":
                ret = (exit_price - entry) / entry * 100
            elif outcome.direction == "SHORT":
                ret = (entry - exit_price) / entry * 100
            else:
                ret = 0.0   # shouldn't happen — NEUTRAL outcomes aren't logged

            outcome.exit_price = exit_price
            outcome.exit_at = exit_row.date
            outcome.return_pct = round(ret, 3)
            outcome.settled = True
            settled_count += 1

        if settled_count:
            db.commit()
        print(f"[OUTCOMES] settled {settled_count} alert outcome(s)")
    except Exception as e:
        db.rollback()
        print(f"[OUTCOMES] settle error: {e}")
    finally:
        db.close()


def refresh_subscription_gauge():
    """Query Stripe for active subscription counts and update the gauge."""
    try:
        monthly_count = 0
        annual_count = 0
        subscriptions = stripe.Subscription.list(status="active", limit=100)
        for sub in subscriptions.auto_paging_iter():
            for item in sub["items"]["data"]:
                interval = item["price"].get("recurring", {}).get("interval", "")
                if interval == "month":
                    monthly_count += 1
                elif interval == "year":
                    annual_count += 1
        ACTIVE_SUBSCRIPTIONS.labels(plan="pro_monthly").set(monthly_count)
        ACTIVE_SUBSCRIPTIONS.labels(plan="pro_annual").set(annual_count)
    except Exception as e:
        print(f"Subscription gauge refresh error: {e}")


def _ingest_headlines(db, headlines: list, label: str) -> int:
    """Dedup + FinBERT-score + save a batch of headline dicts.  Returns the
    number of NEW rows actually inserted (existing URLs are silently skipped).
    Used by both the 15-min RSS job and the hourly omnibus job so the ingest
    semantics stay identical regardless of which scheduler fires."""
    saved = 0
    for h in headlines:
        exists = db.query(models.Headline).filter(
            models.Headline.url == h["url"]
        ).first()
        if exists:
            continue
        t0 = time.time()
        sentiment = analyse_sentiment(h["title"])
        FINBERT_LATENCY.observe(time.time() - t0)
        db.add(models.Headline(
            ticker=h["ticker"],
            title=h["title"],
            source=h["source"],
            url=h["url"],
            sentiment_score=sentiment["score"],
            sentiment_label=sentiment["label"],
            published_at=h["published_at"],
            body=h.get("body"),
            source_type=h.get("source_type"),
        ))
        HEADLINES_INGESTED.labels(source=h["source"], ticker=h["ticker"]).inc()
        saved += 1
    return saved


def scrape_rss_only():
    """RSS-only headline ingestion.  Runs every 15 minutes — no prices, no
    GNews, no alerts.  RSS has no quota and FinBERT only fires on NEW rows
    (dedup happens first), so 4× polling cost is essentially zero on idle
    ticks and just discovers fresh headlines ~45 min sooner on average."""
    global last_scrape_time, last_scrape_duration
    start = time.time()
    print(f"[RSS-15M] fired at {datetime.utcnow()}")
    db = SessionLocal()
    any_failure = False
    try:
        for ticker in TICKERS:
            try:
                headlines = fetch_rss_headlines(ticker)
                saved = _ingest_headlines(db, headlines, "RSS")
                db.commit()
                print(f"[RSS-15M] {ticker}: +{saved} new / {len(headlines)} fetched")
            except Exception as e:
                any_failure = True
                db.rollback()
                print(f"[RSS-15M] {ticker} error: {e}")

        for ticker in BACKGROUND_TICKERS:
            try:
                headlines = fetch_background_headlines(ticker)
                saved = _ingest_headlines(db, headlines, "BACKGROUND")
                db.commit()
                print(f"[RSS-15M] {ticker}: +{saved} new / {len(headlines)} fetched")
            except Exception as e:
                any_failure = True
                db.rollback()
                print(f"[RSS-15M] {ticker} error: {e}")

        # Widened deal sources.  Each is best-effort and fully isolated: a
        # failure logs, flags the run, and rolls back its own ticker, but never
        # aborts the RSS job above.  All emit the same headline dict shape, so
        # _ingest_headlines dedups + FinBERT-scores them identically.
        #   - AI:         ScraperAI replay configs (server-rendered, RSS-less news)
        #   - STOCKTWITS: finance-native social via the public JSON API
        #   - X:          experimental; returns [] unless X_ENABLED (see scraper.py)
        for ticker in list(TICKERS) + BACKGROUND_TICKERS:
            for fetch, label in ((fetch_ai_headlines, "AI"),
                                 (fetch_stocktwits_headlines, "STOCKTWITS"),
                                 (fetch_x_headlines, "X")):
                try:
                    headlines = fetch(ticker)
                    if not headlines:
                        continue
                    saved = _ingest_headlines(db, headlines, label)
                    db.commit()
                    print(f"[RSS-15M] {ticker} [{label}]: +{saved} new / {len(headlines)} fetched")
                except Exception as e:
                    any_failure = True
                    db.rollback()
                    print(f"[RSS-15M] {ticker} [{label}] error: {e}")
    finally:
        db.close()

    elapsed = round(time.time() - start, 2)
    status = "failure" if any_failure else "success"
    SCRAPER_RUNS.labels(status=status).inc()
    SCRAPER_DURATION.observe(elapsed)
    last_scrape_time = datetime.now(timezone.utc).isoformat()
    last_scrape_duration = elapsed
    print(f"[RSS-15M] complete in {elapsed}s (status={status})")


def scrape_all():
    """Hourly omnibus: GNews (gated off via GNEWS_ENABLED by default), price
    refresh for primary + background tickers, and alert checks.  RSS is
    handled by scrape_rss_only at higher cadence — this job intentionally
    does NOT re-scrape RSS to avoid wasting FinBERT calls on guaranteed-empty
    dedups every hour at minute=0."""
    global last_scrape_time, last_scrape_duration
    start = time.time()
    print(f"[SCHEDULER] fired at {datetime.utcnow()}")
    db = SessionLocal()
    any_failure = False
    try:
        latest_prices = fetch_latest_prices_all()
    except Exception as e:
        print(f"[SCHEDULER] price fetch error: {e}")
        latest_prices = {}

    for ticker in TICKERS:
        try:
            # GNews returns [] unless GNEWS_ENABLED=true — see scraper.fetch_headlines
            headlines = fetch_headlines(ticker)
            saved_count = _ingest_headlines(db, headlines, "GNEWS")

            prices = latest_prices.get(ticker)
            if prices:
                existing = db.query(models.Price).filter(
                    models.Price.ticker == prices["ticker"],
                    models.Price.date == prices["date"]
                ).first()
                if existing:
                    # Live spot ticks have no OHLC of their own — accumulate
                    # today's high/low across repeated hourly ticks instead of
                    # overwriting, so by end-of-day the placeholder row has
                    # approximated a real intraday range rather than staying a
                    # flat doji. `open_price` is left as whatever the first
                    # tick of the day set it to.
                    existing.close_price = prices["close_price"]
                    existing.volume = prices["volume"]
                    existing.high_price = max(existing.high_price or prices["close_price"], prices["close_price"])
                    existing.low_price = min(existing.low_price or prices["close_price"], prices["close_price"])
                else:
                    price = models.Price(
                        ticker=prices["ticker"],
                        close_price=prices["close_price"],
                        open_price=prices["close_price"],
                        high_price=prices["close_price"],
                        low_price=prices["close_price"],
                        volume=prices["volume"],
                        date=prices["date"]
                    )
                    db.add(price)

            db.commit()
            print(f"[SCHEDULER] {ticker} committed: {saved_count} new / {len(headlines)} fetched")
        except Exception as e:
            any_failure = True
            db.rollback()
            print(f"[SCHEDULER] {ticker} error: {e}")

    for ticker in BACKGROUND_TICKERS:
        try:
            price_data = fetch_latest_stock_price(ticker)
            if price_data:
                existing_price = db.query(models.Price).filter(
                    models.Price.ticker == price_data["ticker"],
                    models.Price.date == price_data["date"]
                ).first()
                if existing_price:
                    existing_price.close_price = price_data["close_price"]
                    existing_price.high_price = max(existing_price.high_price or price_data["close_price"], price_data["close_price"])
                    existing_price.low_price = min(existing_price.low_price or price_data["close_price"], price_data["close_price"])
                else:
                    db.add(models.Price(
                        **price_data,
                        open_price=price_data["close_price"],
                        high_price=price_data["close_price"],
                        low_price=price_data["close_price"],
                    ))

            db.commit()
            print(f"[BACKGROUND] {ticker} price refreshed")
        except Exception as e:
            any_failure = True
            db.rollback()
            print(f"[BACKGROUND] {ticker} error: {e}")

    try:
        check_alerts(db)
    except Exception as e:
        print(f"[SCHEDULER] alert check error: {e}")
    finally:
        db.close()

    elapsed = round(time.time() - start, 2)
    status = "failure" if any_failure else "success"
    SCRAPER_RUNS.labels(status=status).inc()
    SCRAPER_DURATION.observe(elapsed)
    last_scrape_time = datetime.now(timezone.utc).isoformat()
    last_scrape_duration = elapsed
    print(f"Scheduled scrape complete in {elapsed}s (status={status})")


def scrape_intraday_prices():
    """Hourly top-up of 1h OHLCV bars for candlestick charts. Isolated from
    scrape_all's daily price refresh — a failure here never touches the daily
    Price table or the alert/sentiment pipeline. Short period="2d" window per
    tick (see candles.INTRADAY_REFRESH_PERIOD); full history is filled once by
    POST /admin/intraday/backfill.
    """
    start = time.time()
    db = SessionLocal()
    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]
    saved = 0
    try:
        for ticker in all_tickers:
            try:
                bars = fetch_intraday_prices(ticker, period=INTRADAY_REFRESH_PERIOD)
                for bar in bars:
                    existing = db.query(models.IntradayPrice).filter(
                        models.IntradayPrice.ticker == bar["ticker"],
                        models.IntradayPrice.ts == bar["ts"],
                    ).first()
                    if existing:
                        existing.close_price = bar["close_price"]
                        existing.open_price = bar["open_price"]
                        existing.high_price = bar["high_price"]
                        existing.low_price = bar["low_price"]
                        existing.volume = bar["volume"]
                    else:
                        db.add(models.IntradayPrice(**bar))
                        saved += 1
                db.commit()
            except Exception as e:
                db.rollback()
                print(f"[INTRADAY] {ticker} error: {e}")
    finally:
        db.close()
    print(f"[INTRADAY] done in {round(time.time() - start, 2)}s — {saved} new bars across {len(all_tickers)} tickers")


scheduler = BackgroundScheduler()
scheduler.add_job(
    lambda: send_morning_briefs(next(get_db())),
    CronTrigger(hour=7, minute=0, timezone="Europe/London"),
    id="morning_brief",
    replace_existing=True,
)
def reset_monthly_api_usage():
    db = SessionLocal()
    try:
        db.query(models.APIKey).update({"calls_this_month": 0})
        db.commit()
        print("[SCHEDULER] Monthly API usage reset complete")
    except Exception as e:
        print(f"[SCHEDULER] Monthly reset error: {e}")
    finally:
        db.close()


# RSS scrape runs at minute 0/15/30/45.  max_instances=1+coalesce=True means
# a slow run (Reddit/FXStreet latency spikes can push a tick past 15min) won't
# pile up — APScheduler skips the overlap rather than spawning a parallel job.
scheduler.add_job(
    scrape_rss_only,
    CronTrigger(minute="*/15"),
    id="rss_scrape",
    max_instances=1,
    coalesce=True,
    replace_existing=True,
)
# Hourly omnibus stays at minute=0; if RSS run is still going it'll overlap
# briefly (different DB sessions, dedup-by-URL handles any contention), which
# is cheaper than staggering and having to reason about cross-job ordering.
scheduler.add_job(scrape_all, CronTrigger(minute=0))
# Intraday 1h candle top-up. minute=5 so it lands just after each hour closes
# and doesn't collide with scrape_all's minute=0 tick.
scheduler.add_job(
    scrape_intraday_prices,
    CronTrigger(minute=5),
    id="intraday_scrape",
    max_instances=1,
    coalesce=True,
    replace_existing=True,
)
scheduler.add_job(refresh_subscription_gauge, CronTrigger(minute=30))
scheduler.add_job(reset_monthly_api_usage, CronTrigger(day=1, hour=0, minute=0, timezone="UTC"))
# Settle ripened alert outcomes once a day.  06:00 UTC = after crypto close in
# all timezones we care about and well after equity weekly opens have settled.
scheduler.add_job(settle_alert_outcomes, CronTrigger(hour=6, minute=15, timezone="UTC"))
# Refresh SignalQuality daily at 05:00 UTC — before the 06:15 outcome-settlement
# job and well before the first business-hours alerts so gate decisions are
# never more than 24h stale.  See _run_signal_quality_refresh docstring.
#
# Wrapped in a lambda so the name lookup happens at call time, not at import
# time — `_run_signal_quality_refresh` and its helpers live near the admin
# endpoints ~2600 lines below, so a bare reference here would NameError at
# module load.  (morning_brief above also wraps in a lambda, though for a
# different reason — it passes a DB session argument.)
scheduler.add_job(lambda: _run_signal_quality_refresh(),
                  CronTrigger(hour=5, minute=0, timezone="UTC"),
                  id="signal_quality_refresh",
                  replace_existing=True)
scheduler.start()

# First-deploy safety net: if the SignalQuality table is empty (fresh table on
# first release, or DB wipe), populate it once in the background so alerts
# don't fail-closed on every ticker until tomorrow's 05:00 UTC job.  Uses the
# same background task machinery as the admin refresh route.
def _bootstrap_signal_quality_if_empty():
    db = SessionLocal()
    try:
        has_any = db.query(models.SignalQuality).first() is not None
    finally:
        db.close()
    if not has_any:
        print("[SIGNAL-QUALITY] table empty — running one-time bootstrap")
        _run_signal_quality_refresh()
    else:
        print("[SIGNAL-QUALITY] table populated — scheduler will refresh at 05:00 UTC daily")

# Kick the bootstrap in a scheduler one-shot so app start-up doesn't block on
# a 30-60s backtest sweep.  If the process dies mid-bootstrap, the next start
# just tries again; the refresh is idempotent (upsert semantics).
# _bootstrap_signal_quality_if_empty is defined above so passing it directly
# is fine; the name it references inside its body (_run_signal_quality_refresh)
# is only resolved when the scheduler actually fires the job 30s later.
# run_date must be timezone-aware: APScheduler interprets naive datetimes in
# the scheduler's *local* timezone, so a naive utcnow() on a non-UTC machine
# (e.g. local dev on BST) lands in the past and the one-shot is silently
# dropped as a misfire.
scheduler.add_job(_bootstrap_signal_quality_if_empty, "date",
                  run_date=datetime.now(timezone.utc) + timedelta(seconds=30))


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"message": "Crypto Sentiment API"}


@app.post("/scrape/all")
def scrape_all_endpoint(admin=Depends(require_admin)):
    scrape_all()
    return {"message": "Scrape complete", "last_scrape": last_scrape_time, "duration_seconds": last_scrape_duration}


@app.post("/scrape/{ticker}")
def scrape(ticker: str, db: Session = Depends(get_db), admin=Depends(require_admin)):
    headlines = fetch_headlines(ticker.upper()) + fetch_rss_headlines(ticker.upper())

    if not headlines:
        raise HTTPException(status_code=404, detail="No headlines found")

    saved = []
    for h in headlines:
        exists = db.query(models.Headline).filter(
            models.Headline.url == h["url"]
        ).first()
        if exists:
            continue
        t0 = time.time()
        sentiment = analyse_sentiment(h["title"])
        FINBERT_LATENCY.observe(time.time() - t0)
        headline = models.Headline(
            ticker=h["ticker"],
            title=h["title"],
            source=h["source"],
            url=h["url"],
            sentiment_score=sentiment["score"],
            sentiment_label=sentiment["label"],
            published_at=h["published_at"]
        )
        db.add(headline)
        saved.append(headline)
        HEADLINES_INGESTED.labels(source=h["source"], ticker=h["ticker"]).inc()

    prices = fetch_latest_price(ticker.upper())
    if prices:
        print(f"[DEBUG] querying date={prices['date']} type={type(prices['date'])}")
        existing = db.query(models.Price).filter(
            models.Price.ticker == prices["ticker"],
            models.Price.date == prices["date"]
        ).first()
        print(f"[DEBUG] existing={existing}")
        if existing:
            existing.close_price = prices["close_price"]
            existing.volume = prices["volume"]
            existing.high_price = max(existing.high_price or prices["close_price"], prices["close_price"])
            existing.low_price = min(existing.low_price or prices["close_price"], prices["close_price"])
        else:
            db.add(models.Price(
                ticker=prices["ticker"],
                close_price=prices["close_price"],
                open_price=prices["close_price"],
                high_price=prices["close_price"],
                low_price=prices["close_price"],
                volume=prices["volume"],
                date=prices["date"]
            ))

    db.commit()
    return {"message": f"Saved {len(saved)} headlines for {ticker}"}


def _backfill_prices_all():
    db = SessionLocal()
    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]
    summary = {"saved": {}, "errors": {}}
    try:
        for ticker in all_tickers:
            try:
                prices = fetch_prices(ticker)
                if not prices:
                    summary["errors"][ticker] = "no data"
                    continue
                new_count = 0
                for p in prices:
                    exists = db.query(models.Price).filter(
                        models.Price.ticker == p["ticker"],
                        models.Price.date == p["date"],
                    ).first()
                    if exists:
                        continue
                    db.add(models.Price(
                        ticker=p["ticker"],
                        close_price=p["close_price"],
                        open_price=p["open_price"],
                        high_price=p["high_price"],
                        low_price=p["low_price"],
                        volume=p["volume"],
                        date=p["date"],
                    ))
                    new_count += 1
                db.commit()
                summary["saved"][ticker] = new_count
                print(f"[BACKFILL-PRICES] {ticker}: +{new_count} new / {len(prices)} fetched")
            except Exception as e:
                db.rollback()
                summary["errors"][ticker] = str(e)
                print(f"[BACKFILL-PRICES] {ticker} error: {e}")
    finally:
        db.close()
    print(f"[BACKFILL-PRICES] done — saved={sum(summary['saved'].values())} across {len(summary['saved'])} tickers, errors={len(summary['errors'])}")


# Must be registered before /prices/{ticker} so FastAPI doesn't capture "all"
# as a ticker name.
@app.post("/prices/all")
def save_all_prices(background_tasks: BackgroundTasks, admin=Depends(require_admin)):
    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]
    background_tasks.add_task(_backfill_prices_all)
    return {
        "message": f"Queued price backfill for {len(all_tickers)} tickers — check logs for progress",
        "tickers": all_tickers,
    }


def _backfill_ohlc_all():
    """One-time reconciliation, distinct from `_backfill_prices_all`.  That
    function is insert-only (`if exists: continue`) so it will never correct
    an already-stored row — which means every daily Price row written before
    the open/high/low columns existed (all of history) or written from a live
    spot tick (open=high=low=close placeholder) would stay wrong/flat forever.
    This walks the same yfinance daily download and UPDATEs matching
    ticker+date rows in place. Safe to re-run; a no-op once history is clean.
    Not on a schedule — run once via POST /admin/prices/backfill-ohlc after
    this feature deploys.
    """
    db = SessionLocal()
    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]
    summary = {"updated": {}, "errors": {}}
    try:
        for ticker in all_tickers:
            try:
                prices = fetch_prices(ticker)
                if not prices:
                    summary["errors"][ticker] = "no data"
                    continue
                by_date = {p["date"]: p for p in prices}
                rows = db.query(models.Price).filter(models.Price.ticker == ticker).all()
                updated = 0
                for row in rows:
                    p = by_date.get(row.date)
                    if not p:
                        continue
                    row.open_price = p["open_price"]
                    row.high_price = p["high_price"]
                    row.low_price = p["low_price"]
                    updated += 1
                db.commit()
                summary["updated"][ticker] = updated
                print(f"[BACKFILL-OHLC] {ticker}: reconciled {updated}/{len(rows)} rows")
            except Exception as e:
                db.rollback()
                summary["errors"][ticker] = str(e)
                print(f"[BACKFILL-OHLC] {ticker} error: {e}")
    finally:
        db.close()
    print(f"[BACKFILL-OHLC] done — updated={sum(summary['updated'].values())} across {len(summary['updated'])} tickers, errors={len(summary['errors'])}")


@app.post("/admin/prices/backfill-ohlc")
def backfill_ohlc(background_tasks: BackgroundTasks, admin=Depends(require_admin)):
    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]
    background_tasks.add_task(_backfill_ohlc_all)
    return {
        "message": f"Queued OHLC reconciliation for {len(all_tickers)} tickers — check logs for progress",
        "tickers": all_tickers,
    }


def _backfill_intraday_all():
    """One-time deep backfill of 1h bars, bounded by yfinance's ~730-day cap
    on 60m history (see candles.INTRADAY_BACKFILL_PERIOD). Insert-only, same
    shape as _backfill_prices_all — the hourly scrape_intraday_prices job
    keeps things current going forward.
    """
    db = SessionLocal()
    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]
    summary = {"saved": {}, "errors": {}}
    try:
        for ticker in all_tickers:
            try:
                bars = fetch_intraday_prices(ticker, period=INTRADAY_BACKFILL_PERIOD)
                if not bars:
                    summary["errors"][ticker] = "no data"
                    continue
                new_count = 0
                for bar in bars:
                    exists = db.query(models.IntradayPrice).filter(
                        models.IntradayPrice.ticker == bar["ticker"],
                        models.IntradayPrice.ts == bar["ts"],
                    ).first()
                    if exists:
                        continue
                    db.add(models.IntradayPrice(**bar))
                    new_count += 1
                db.commit()
                summary["saved"][ticker] = new_count
                print(f"[BACKFILL-INTRADAY] {ticker}: +{new_count} new / {len(bars)} fetched")
            except Exception as e:
                db.rollback()
                summary["errors"][ticker] = str(e)
                print(f"[BACKFILL-INTRADAY] {ticker} error: {e}")
    finally:
        db.close()
    print(f"[BACKFILL-INTRADAY] done — saved={sum(summary['saved'].values())} across {len(summary['saved'])} tickers, errors={len(summary['errors'])}")


@app.post("/admin/intraday/purge-bad-rows")
def purge_bad_intraday_rows(dry_run: bool = True, db: Session = Depends(get_db), admin=Depends(require_admin)):
    """Delete non-intraday rows that leaked into intraday_prices.

    Yahoo only serves 60m bars for ~730 days, so any row older than that is
    provably not real hourly data — it came from a yfinance call that silently
    returned DAILY bars (see candles.INTRADAY_BACKFILL_PERIOD). Those rows sit
    at 00:00 one-per-day and corrupt both the 1h view and the 4h bucketing.

    Nothing is lost: the same daily candles live correctly in the `prices`
    table, which is what /candles?interval=1d reads. Defaults to a dry run —
    pass ?dry_run=false to actually delete.
    """
    from .candles import INTRADAY_MAX_LOOKBACK_DAYS
    cutoff = datetime.utcnow() - timedelta(days=INTRADAY_MAX_LOOKBACK_DAYS)

    doomed = db.query(models.IntradayPrice).filter(models.IntradayPrice.ts < cutoff)
    by_ticker = {}
    for row in db.query(
        models.IntradayPrice.ticker, sa_func.count(models.IntradayPrice.id)
    ).filter(models.IntradayPrice.ts < cutoff).group_by(models.IntradayPrice.ticker).all():
        by_ticker[row[0]] = row[1]
    total = sum(by_ticker.values())

    if dry_run:
        return {
            "dry_run": True,
            "cutoff": cutoff.isoformat(),
            "would_delete": total,
            "by_ticker": by_ticker,
            "hint": "re-run with ?dry_run=false to delete",
        }

    deleted = doomed.delete(synchronize_session=False)
    db.commit()
    print(f"[INTRADAY-PURGE] deleted {deleted} pre-{cutoff.date()} rows from intraday_prices")
    return {"dry_run": False, "cutoff": cutoff.isoformat(), "deleted": deleted, "by_ticker": by_ticker}


@app.post("/admin/intraday/backfill")
def backfill_intraday(background_tasks: BackgroundTasks, admin=Depends(require_admin)):
    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]
    background_tasks.add_task(_backfill_intraday_all)
    return {
        "message": f"Queued intraday backfill for {len(all_tickers)} tickers — check logs for progress",
        "tickers": all_tickers,
    }


def _backfill_candles_all():
    """One-call combo of _backfill_ohlc_all + _backfill_intraday_all — the two
    one-time migration passes candlestick charts need after this feature
    deploys. Run via POST /admin/candles/backfill-all instead of hitting the
    two endpoints separately. Both halves are independently idempotent
    (OHLC reconciliation UPDATEs in place, intraday backfill skips existing
    rows), so re-running this is always safe.
    """
    _backfill_ohlc_all()
    _backfill_intraday_all()


@app.post("/admin/candles/backfill-all")
def backfill_candles_all(background_tasks: BackgroundTasks, admin=Depends(require_admin)):
    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]
    background_tasks.add_task(_backfill_candles_all)
    return {
        "message": f"Queued OHLC reconciliation + intraday backfill for {len(all_tickers)} tickers — check logs for progress",
        "tickers": all_tickers,
    }


# Forward-fills any missing date between a ticker's earliest and latest record
# with the prior day's close (volume=0 so imputed rows are distinguishable from
# real ones).  This produces a continuous daily series — weekends and market
# holidays are filled with the most recent trading day's close.
def _forward_fill_gaps(db: Session, ticker: str) -> int:
    rows = db.query(models.Price).filter(
        models.Price.ticker == ticker
    ).order_by(models.Price.date.asc()).all()
    if len(rows) < 2:
        return 0
    inserted = 0
    prev = rows[0]
    for curr in rows[1:]:
        gap_days = (curr.date - prev.date).days
        if gap_days > 1:
            for i in range(1, gap_days):
                db.add(models.Price(
                    ticker=ticker,
                    close_price=prev.close_price,
                    open_price=prev.close_price,
                    high_price=prev.close_price,
                    low_price=prev.close_price,
                    volume=0.0,
                    date=prev.date + timedelta(days=i),
                ))
                inserted += 1
        prev = curr
    db.commit()
    return inserted


def _run_fill_gaps():
    db = SessionLocal()
    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]
    summary = {}
    try:
        for ticker in all_tickers:
            try:
                inserted = _forward_fill_gaps(db, ticker)
                summary[ticker] = inserted
                print(f"[FILL-GAPS] {ticker}: +{inserted} forward-filled")
            except Exception as e:
                db.rollback()
                summary[ticker] = f"error: {e}"
                print(f"[FILL-GAPS] {ticker} error: {e}")
    finally:
        db.close()
    total = sum(v for v in summary.values() if isinstance(v, int))
    print(f"[FILL-GAPS] done — {total} rows inserted across {len(summary)} tickers")


@app.post("/prices/fill-gaps")
def fill_price_gaps(background_tasks: BackgroundTasks, admin=Depends(require_admin)):
    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]
    background_tasks.add_task(_run_fill_gaps)
    return {
        "message": f"Queued gap fill for {len(all_tickers)} tickers — check logs",
        "tickers": all_tickers,
    }


@app.post("/prices/{ticker}")
def save_prices(ticker: str, db: Session = Depends(get_db), admin=Depends(require_admin)):
    prices = fetch_prices(ticker.upper())

    if not prices:
        raise HTTPException(status_code=404, detail="No price data found")

    for p in prices:
        exists = db.query(models.Price).filter(
            models.Price.ticker == p["ticker"],
            models.Price.date == p["date"]
        ).first()

        if exists:
            continue

        price = models.Price(
            ticker=p["ticker"],
            close_price=p["close_price"],
            open_price=p["open_price"],
            high_price=p["high_price"],
            low_price=p["low_price"],
            volume=p["volume"],
            date=p["date"]
        )
        db.add(price)

    db.commit()
    return {"message": f"Saved prices for {ticker}"}


@app.get("/sentiment/{ticker}", response_model=list[schemas.HeadlineResponse])
def get_sentiment(ticker: str, db: Session = Depends(get_db)):
    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper()
    ).all()
    return headlines


@app.get("/prices/{ticker}", response_model=list[schemas.PriceResponse])
def get_prices(ticker: str, db: Session = Depends(get_db)):
    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper()
    ).all()
    return prices


# Social chatter vs editorial news.  Reddit arrives through the general RSS
# path, so it carries no source_type and is identified by its feed title
# ("newest submissions : Bitcoin"); StockTwits/X are tagged via source_type;
# and the HN backfill stores the linked article's domain, which is sometimes
# a social platform.  Matching is deliberately exact/prefix rather than a
# loose "%reddit%" — real companies (redditinc.com, redditrecs.com) would be
# caught by that and wrongly hidden.
#
# Used for DISPLAY filtering only (see the `sources` param on /dashboard).
# Sentiment scoring, correlation, alerts and the SignalQuality gate all still
# consume every headline — verified that excluding social doesn't measurably
# change the correlation, so there's no reason to recalibrate that pipeline.
_SOCIAL_SOURCE_TYPES = ("stocktwits", "x")
_SOCIAL_DOMAINS = (
    "reddit.com", "old.reddit.com", "sh.reddit.com",
    "twitter.com", "x.com", "youtube.com",
)


def _social_headline_filter():
    """SQLAlchemy predicate matching social-chatter headlines.

    COALESCE guards the NULL trap: `NOT (NULL = 'stocktwits')` is NULL, not
    TRUE, so without it every legacy news row (source_type IS NULL) would be
    dropped by the negated filter.
    """
    return sa_or(
        sa_func.coalesce(models.Headline.source_type, "").in_(_SOCIAL_SOURCE_TYPES),
        sa_func.coalesce(models.Headline.source, "").ilike("newest submissions :%"),
        sa_func.lower(sa_func.coalesce(models.Headline.source, "")).in_(_SOCIAL_DOMAINS),
    )


def _classify_source(source: str | None, source_type: str | None) -> str:
    """Coarse label for the UI badge: 'stocktwits' | 'reddit' | 'x' | 'news'."""
    st = (source_type or "").lower()
    if st in _SOCIAL_SOURCE_TYPES:
        return st
    src = (source or "").lower()
    if src.startswith("newest submissions :") or src in ("reddit.com", "old.reddit.com", "sh.reddit.com"):
        return "reddit"
    if src in ("twitter.com", "x.com"):
        return "x"
    if src == "youtube.com":
        return "youtube"
    return "news"


@app.get("/dashboard/{ticker}")
def get_dashboard(ticker: str, days: int = 90, all: bool = False, page: int = 1, limit: int = 50, sources: str = "all", db: Session = Depends(get_db)):
    query_headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper()
    )
    query_prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper()
    )

    if not all:
        since = datetime.utcnow() - timedelta(days=days)
        query_headlines = query_headlines.filter(models.Headline.published_at >= since)
        query_prices = query_prices.filter(models.Price.date >= since)

    # `sources=news` hides Reddit/StockTwits chatter from the headline feed.
    # Default stays "all" so existing consumers (the landing page's ticker
    # chips, anything else hitting this endpoint) are unaffected — the
    # dashboard opts in explicitly. Filtering is applied before the count so
    # pagination stays correct for the filtered set.
    if sources == "news":
        query_headlines = query_headlines.filter(~_social_headline_filter())

    # prices always returned in full for the chart
    prices = query_prices.order_by(models.Price.date.desc()).all()

    # headlines paginated
    total_headlines = query_headlines.count()
    headlines = query_headlines.order_by(
        models.Headline.published_at.desc()
    ).offset((page - 1) * limit).limit(limit).all()

    return {
        "ticker": ticker.upper(),
        "days": days if not all else None,
        "pagination": {
            "page": page,
            "limit": limit,
            "total": total_headlines,
            "pages": (total_headlines + limit - 1) // limit
        },
        "sentiment": [
            {
                "date": h.published_at,
                "score": h.sentiment_score,
                "label": h.sentiment_label,
                "title": h.title,
                "source": h.source,
                "source_kind": _classify_source(h.source, h.source_type),
            } for h in headlines
        ],
        "prices": [
            {
                "date": p.date,
                "close_price": p.close_price,
                "open_price": p.open_price if p.open_price is not None else p.close_price,
                "high_price": p.high_price if p.high_price is not None else p.close_price,
                "low_price": p.low_price if p.low_price is not None else p.close_price,
                "volume": p.volume
            } for p in prices
        ]
    }


def _bucket_sentiment(headlines, bucket_fn):
    """Weighted-average sentiment per bucket — same convention as
    _build_daily_series (skip |score|<0.05 as noise, weight by |score|),
    just generalised to an arbitrary bucket_fn instead of hardcoding a
    calendar day, so it also covers the 1h/4h candle buckets. Returns
    {bucket_key: weighted_avg_sentiment}; a bucket with no headlines simply
    doesn't appear (caller should treat a missing key as "no data").
    """
    buckets = defaultdict(lambda: {"scores": [], "weights": []})
    for h in headlines:
        if abs(h.sentiment_score) < 0.05:
            continue
        key = bucket_fn(h.published_at)
        buckets[key]["scores"].append(h.sentiment_score)
        buckets[key]["weights"].append(abs(h.sentiment_score))

    result = {}
    for key, v in buckets.items():
        if not v["scores"]:
            continue
        w_sum = sum(v["weights"])
        result[key] = sum(s * w for s, w in zip(v["scores"], v["weights"])) / w_sum if w_sum else 0
    return result


@app.get("/candles/{ticker}")
def get_candles(ticker: str, interval: str = "1h", limit: int = 500, db: Session = Depends(get_db)):
    """OHLCV candles for the dashboard's candlestick chart.

    interval=1h / 4h read from the (bounded, ~730-day) IntradayPrice table;
    4h candles are bucketed from four consecutive 1h rows at read time since
    yfinance has no native 4h interval to fetch. interval=1d reads the daily
    Price table directly, so it carries the full 2019+ history the intraday
    table can't — falling back to close_price for any pre-migration row where
    open/high/low are still null (see models.Price docstring).

    Each candle also carries a `sentiment` field — the same weighted-average
    FinBERT score /dashboard uses, just bucketed to match the candle's own
    interval (day / hour / 4h) instead of always being daily. `null` means no
    headlines fell in that bucket, not a zero/neutral score.
    """
    ticker = ticker.upper()
    if interval not in ("1h", "4h", "1d"):
        raise HTTPException(status_code=400, detail="interval must be '1h', '4h', or '1d'")
    limit = max(1, min(limit, 2000))

    if interval == "1d":
        rows = db.query(models.Price).filter(
            models.Price.ticker == ticker
        ).order_by(models.Price.date.desc()).limit(limit).all()
        candles = [
            {
                "ts": r.date,
                "open": r.open_price if r.open_price is not None else r.close_price,
                "high": r.high_price if r.high_price is not None else r.close_price,
                "low": r.low_price if r.low_price is not None else r.close_price,
                "close": r.close_price,
                "volume": r.volume,
            }
            for r in reversed(rows)
        ]
    else:
        # 4h buckets 4 consecutive 1h rows, so fetch proportionally more raw
        # bars to end up with roughly `limit` buckets.
        raw_limit = limit if interval == "1h" else limit * 4
        rows = list(reversed(db.query(models.IntradayPrice).filter(
            models.IntradayPrice.ticker == ticker
        ).order_by(models.IntradayPrice.ts.desc()).limit(raw_limit).all()))

        if interval == "1h":
            candles = [
                {
                    "ts": r.ts,
                    "open": r.open_price,
                    "high": r.high_price,
                    "low": r.low_price,
                    "close": r.close_price,
                    "volume": r.volume,
                }
                for r in rows
            ]
        else:
            buckets = {}
            for r in rows:
                bucket_ts = r.ts.replace(hour=(r.ts.hour // 4) * 4, minute=0, second=0, microsecond=0)
                buckets.setdefault(bucket_ts, []).append(r)
            candles = []
            for bucket_ts in sorted(buckets.keys()):
                bars = buckets[bucket_ts]
                candles.append({
                    "ts": bucket_ts,
                    "open": bars[0].open_price,
                    "high": max(b.high_price for b in bars),
                    "low": min(b.low_price for b in bars),
                    "close": bars[-1].close_price,
                    "volume": sum(b.volume for b in bars),
                })
            candles = candles[-limit:]

    if candles:
        bucket_fn = {
            "1d": lambda dt: dt.replace(hour=0, minute=0, second=0, microsecond=0),
            "1h": lambda dt: dt.replace(minute=0, second=0, microsecond=0),
            "4h": lambda dt: dt.replace(hour=(dt.hour // 4) * 4, minute=0, second=0, microsecond=0),
        }[interval]
        headlines = db.query(models.Headline).filter(
            models.Headline.ticker == ticker,
            models.Headline.published_at >= candles[0]["ts"],
        ).all()
        sentiment_by_bucket = _bucket_sentiment(headlines, bucket_fn)
        for c in candles:
            c["sentiment"] = sentiment_by_bucket.get(bucket_fn(c["ts"]))

    return {"ticker": ticker, "interval": interval, "candles": candles}


@app.get("/stats")
def get_stats(db: Session = Depends(get_db)):
    tickers = [r[0] for r in db.query(models.Headline.ticker).distinct().all()]
    return {
        "total_headlines": db.query(models.Headline).count(),
        "total_prices": db.query(models.Price).count(),
        "tickers": tickers
    }


@app.delete("/cleanup/duplicates")
def cleanup_duplicates(db: Session = Depends(get_db), admin=Depends(require_admin)):
    all_headlines = db.query(models.Headline).order_by(models.Headline.id).all()

    seen_urls = set()
    deleted = 0

    for headline in all_headlines:
        if headline.url in seen_urls:
            db.delete(headline)
            deleted += 1
        else:
            seen_urls.add(headline.url)

    db.commit()
    return {"message": f"Deleted {deleted} duplicate headlines"}


@app.delete("/cleanup/duplicate-prices")
def cleanup_duplicate_prices(db: Session = Depends(get_db), admin=Depends(require_admin)):
    all_prices = db.query(models.Price).order_by(models.Price.id).all()

    seen = set()
    deleted = 0

    for price in all_prices:
        key = (price.ticker, str(price.date))
        if key in seen:
            db.delete(price)
            deleted += 1
        else:
            seen.add(key)

    db.commit()
    return {"message": f"Deleted {deleted} duplicate prices"}


@app.get("/correlation/{ticker}")
def get_correlation(ticker: str, db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=180)

    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper(),
        models.Headline.published_at >= since
    ).order_by(models.Headline.published_at).all()

    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper(),
        models.Price.date >= since
    ).order_by(models.Price.date).all()

    if len(headlines) < 30 or len(prices) < 30:
        return {"message": "Not enough data yet", "headlines": len(headlines), "prices": len(prices)}

    # Aggregate sentiment per day with volume + confidence weighting
    daily = defaultdict(lambda: {"scores": [], "weights": []})
    for h in headlines:
        if abs(h.sentiment_score) < 0.05:
            continue
        d = h.published_at.date()
        weight = abs(h.sentiment_score)  # confidence proxy
        daily[d]["scores"].append(h.sentiment_score)
        daily[d]["weights"].append(weight)

    daily_sentiment = {}
    daily_volume = {}
    for d, v in daily.items():
        if not v["scores"]:
            continue
        w_sum = sum(v["weights"])
        daily_sentiment[d] = sum(s * w for s, w in zip(v["scores"], v["weights"])) / w_sum if w_sum else 0
        daily_volume[d] = len(v["scores"])

    # Daily returns (next-day forward returns)
    sorted_prices = sorted(prices, key=lambda p: p.date)
    daily_return = {}
    for i in range(len(sorted_prices) - 1):
        d = sorted_prices[i].date.date()
        prev_close = sorted_prices[i].close_price
        next_close = sorted_prices[i + 1].close_price
        if prev_close and prev_close > 0:
            daily_return[d] = (next_close - prev_close) / prev_close * 100

    # Sentiment shift = today's sentiment minus 7-day rolling avg
    sorted_dates = sorted(daily_sentiment.keys())
    sentiment_shift = {}
    for i, d in enumerate(sorted_dates):
        window_start = max(0, i - 7)
        window = [daily_sentiment[sorted_dates[j]] for j in range(window_start, i)]
        if len(window) >= 3:
            sentiment_shift[d] = daily_sentiment[d] - (sum(window) / len(window))

    # Align all features against next-day returns
    common = sorted(set(sentiment_shift.keys()) & set(daily_return.keys()) & set(daily_volume.keys()))
    if len(common) < 30:
        return {"message": "Not enough overlapping data yet", "overlap_days": len(common)}

    shifts = np.array([sentiment_shift[d] for d in common])
    levels = np.array([daily_sentiment[d] for d in common])
    volumes = np.array([daily_volume[d] for d in common])
    returns = np.array([daily_return[d] for d in common])

    def safe_corr(x, y):
        if len(x) < 10 or np.std(x) == 0 or np.std(y) == 0:
            return None, None, None
        r, p = pearsonr(x, y)
        # Fisher z 95% CI
        n = len(x)
        if n < 4 or abs(r) >= 1:
            return round(r, 3), round(p, 4), None
        z = 0.5 * math.log((1 + r) / (1 - r))
        se = 1 / math.sqrt(n - 3)
        lo = math.tanh(z - 1.96 * se)
        hi = math.tanh(z + 1.96 * se)
        return round(r, 3), round(p, 4), [round(lo, 3), round(hi, 3)]

    shift_r, shift_p, shift_ci = safe_corr(shifts, returns)
    level_r, level_p, level_ci = safe_corr(levels, returns)
    volume_r, volume_p, volume_ci = safe_corr(volumes, returns)

    # Momentum baseline: yesterday's return vs today's return
    sorted_ret_dates = sorted(daily_return.keys())
    yest, today = [], []
    for i in range(1, len(sorted_ret_dates)):
        prev_d = sorted_ret_dates[i - 1]
        curr_d = sorted_ret_dates[i]
        if (curr_d - prev_d).days == 1:
            yest.append(daily_return[prev_d])
            today.append(daily_return[curr_d])
    momentum_r, momentum_p, _ = safe_corr(np.array(yest), np.array(today)) if len(yest) >= 10 else (None, None, None)

    # Headline signal = sentiment shift correlation
    primary_r = shift_r if shift_r is not None else 0
    primary_p = shift_p if shift_p is not None else 1
    primary_ci = shift_ci

    # Signal strength — classified on |r| magnitude only, no p-value gate
    if abs(primary_r) >= 0.25:
        strength = "strong"
    elif abs(primary_r) >= 0.10:
        strength = "weak"
    else:
        strength = "inconclusive"

    direction = "negative (contrarian)" if primary_r < 0 else "positive (momentum)"

    # Beats baseline?
    beats_momentum = (
        momentum_r is not None
        and primary_r is not None
        and abs(primary_r) > abs(momentum_r)
    )

    return {
        "ticker": ticker.upper(),
        "window_days": 180,
        "sample_size": len(common),
        "primary_signal": {
            "type": "sentiment_shift_vs_next_day_return",
            "correlation": primary_r,
            "p_value": primary_p,
            "ci_95": primary_ci,
            "strength": strength,
            "direction": direction,
        },
        "secondary_signals": {
            "sentiment_level_vs_next_day_return": {
                "correlation": level_r, "p_value": level_p, "ci_95": level_ci
            },
            "news_volume_vs_next_day_return": {
                "correlation": volume_r, "p_value": volume_p, "ci_95": volume_ci
            },
        },
        "baseline": {
            "momentum_autocorrelation": momentum_r,
            "momentum_p_value": momentum_p,
            "primary_beats_momentum": bool(beats_momentum) if beats_momentum is not None else None,
        },
        "interpretation": (
            f"Sentiment shifts show a {strength} {direction} signal "
            f"(r={primary_r}, p={primary_p}, n={len(common)}). "
            f"{'Outperforms' if beats_momentum else 'Does not outperform'} momentum baseline."
        ),
    }

@app.get("/signal/{ticker}")
def get_signal(ticker: str, db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=180)
    today = datetime.utcnow().date()

    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper(),
        models.Headline.published_at >= since
    ).order_by(models.Headline.published_at).all()

    if len(headlines) < 10:
        return {"message": "Not enough data yet"}

    # Build daily weighted sentiment (reuse same logic as correlation)
    daily = defaultdict(lambda: {"scores": [], "weights": []})
    for h in headlines:
        if abs(h.sentiment_score) < 0.05:
            continue
        d = h.published_at.date()
        daily[d]["scores"].append(h.sentiment_score)
        daily[d]["weights"].append(abs(h.sentiment_score))

    daily_sentiment = {}
    daily_volume = {}
    for d, v in daily.items():
        if not v["scores"]:
            continue
        w_sum = sum(v["weights"])
        daily_sentiment[d] = sum(s * w for s, w in zip(v["scores"], v["weights"])) / w_sum if w_sum else 0
        daily_volume[d] = len(v["scores"])

    sorted_dates = sorted(daily_sentiment.keys())
    if len(sorted_dates) < 8:
        return {"message": "Not enough data yet"}

    # Compute sentiment shifts for all days
    all_shifts = {}
    for i, d in enumerate(sorted_dates):
        window_start = max(0, i - 7)
        window = [daily_sentiment[sorted_dates[j]] for j in range(window_start, i)]
        if len(window) >= 3:
            all_shifts[d] = daily_sentiment[d] - (sum(window) / len(window))

    if not all_shifts:
        return {"message": "Not enough data yet"}

    # Today's values — use most recent day if today has no data yet
    most_recent = sorted_dates[-1]
    today_sentiment = daily_sentiment.get(most_recent)
    today_volume = daily_volume.get(most_recent, 0)
    today_shift = all_shifts.get(most_recent)

    if today_shift is None:
        return {"message": "Not enough data to compute today's shift"}

    # Shift percentile — where does today's shift rank historically?
    all_shift_values = sorted(all_shifts.values())
    n_shifts = len(all_shift_values)
    percentile = round(sum(1 for s in all_shift_values if s <= today_shift) / n_shifts * 100)

    # How unusual is this? Find last time shift was this large
    larger_shifts = [
        d for d, s in sorted(all_shifts.items())
        if d < most_recent and abs(s) >= abs(today_shift)
    ]
    last_similar = larger_shifts[-1] if larger_shifts else None
    days_since_similar = (most_recent - last_similar).days if last_similar else None

    # Shift magnitude label
    if percentile >= 90:
        magnitude = "extreme"
    elif percentile >= 75:
        magnitude = "significant"
    elif percentile >= 50:
        magnitude = "moderate"
    else:
        magnitude = "minor"

    shift_direction = "up" if today_shift > 0 else "down"

    # Sentiment label
    sentiment_label = (
        "strongly bullish" if today_sentiment > 0.3
        else "bullish" if today_sentiment > 0.1
        else "strongly bearish" if today_sentiment < -0.3
        else "bearish" if today_sentiment < -0.1
        else "neutral"
    )

    # Plain-English summary. The dashboard displays sentiment on a 0-100
    # scale (display-only rescale — the `today`/`sentiment` fields returned
    # below stay on FinBERT's native -1..1 range for API/alert consumers),
    # so this human-readable sentence embeds the same 0-100 numbers a reader
    # sees everywhere else on the page instead of the raw -1..1 values.
    sign = "+" if today_shift > 0 else ""
    display_sentiment = round((today_sentiment + 1) * 50)
    display_shift = round(today_shift * 50)
    summary = f"{ticker.upper()} sentiment is {sentiment_label} ({display_sentiment}/100) "
    summary += f"with a {magnitude} {'upward' if shift_direction == 'up' else 'downward'} shift "
    summary += f"of {sign}{display_shift} vs the 7-day average "
    summary += f"({percentile}th percentile of all daily shifts). "
    if days_since_similar and days_since_similar > 7:
        summary += f"The last shift of this size was {days_since_similar} days ago. "
    if today_volume < 3:
        summary += f"Low confidence — only {today_volume} article{'s' if today_volume != 1 else ''} today."
    else:
        summary += f"Based on {today_volume} article{'s' if today_volume != 1 else ''}."

    return {
        "ticker": ticker.upper(),
        "date": str(most_recent),
        "today": {
            "sentiment": round(today_sentiment, 3),
            "shift": round(today_shift, 3),
            "shift_direction": shift_direction,
            "shift_percentile": percentile,
            "shift_magnitude": magnitude,
            "article_count": today_volume,
            "sentiment_label": sentiment_label,
        },
        "context": {
            "days_since_similar_shift": days_since_similar,
            "total_days_analysed": n_shifts,
        },
        "summary": summary,
    }

@app.get("/export/sentiment/{ticker}")
async def export_sentiment(ticker: str, days: int = 90, db: Session = Depends(get_db), user=Depends(require_pro)):
    days = min(days, 90)
    since = datetime.utcnow() - timedelta(days=days)
    query = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper(),
        models.Headline.published_at >= since
    )
    headlines = query.order_by(models.Headline.published_at).all()

    if not headlines:
        raise HTTPException(status_code=404, detail="No data found")

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["date", "ticker", "title", "source", "sentiment_score", "sentiment_label", "url"])
    for h in headlines:
        writer.writerow([
            h.published_at.isoformat(),
            h.ticker,
            h.title,
            h.source,
            h.sentiment_score,
            h.sentiment_label,
            h.url
        ])

    output.seek(0)
    filename = f"sentimentfx_{ticker.lower()}_sentiment{'_' + str(days) + 'd' if days else '_all'}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.get("/export/prices/{ticker}")
async def export_prices(ticker: str, days: int = 90, db: Session = Depends(get_db), user=Depends(require_pro)):
    days = min(days, 90)
    since = datetime.utcnow() - timedelta(days=days)
    query = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper(),
        models.Price.date >= since
    )
    prices = query.order_by(models.Price.date).all()

    if not prices:
        raise HTTPException(status_code=404, detail="No data found")

    # Crypto is genuinely GBP (yfinance BTC-GBP etc). FX pairs are a raw
    # exchange rate, not a currency amount. Everything else (stocks, ETFs,
    # commodity futures) is stored in native USD -- prices.py never converts
    # non-crypto tickers. Mirrors brief.py's _price_currency() -- keep in sync.
    category = _category_for(ticker.upper())
    price_column = "close_price_gbp" if category == "crypto" else "close_price_rate" if category == "fx" else "close_price_usd"

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["date", "ticker", price_column, "volume"])
    for p in prices:
        writer.writerow([
            p.date.isoformat(),
            p.ticker,
            p.close_price,
            p.volume
        ])

    output.seek(0)
    filename = f"sentimentfx_{ticker.lower()}_prices{'_' + str(days) + 'd' if days else '_all'}.csv"
    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post("/waitlist")
def join_waitlist(data: schemas.WaitlistCreate, db: Session = Depends(get_db)):
    existing = db.query(models.WaitlistEmail).filter(
        models.WaitlistEmail.email == data.email
    ).first()
    if existing:
        return {"message": "Already on the waitlist!"}

    entry = models.WaitlistEmail(email=data.email)
    db.add(entry)
    db.commit()

    try:
        resend.api_key = os.getenv("RESEND_API_KEY")
        resend.Emails.send({
            "from": "SentimentFX <hello@sentimentfx.org>",
            "to": data.email,
            "subject": "You're on the SentimentFX waitlist",
            "html": """
<!DOCTYPE html>
<html>
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
</head>
<body style="margin:0;padding:0;background:#080c10;font-family:'Courier New',monospace;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#080c10;padding:40px 20px;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
          <tr>
            <td style="border-bottom:1px solid #21262d;padding-bottom:20px;margin-bottom:32px;">
              <span style="font-size:13px;font-weight:600;letter-spacing:0.2em;color:#f0b429;text-transform:uppercase;">
                SentimentFX
              </span>
            </td>
          </tr>
          <tr>
            <td style="padding:40px 0 32px;">
              <p style="font-size:10px;letter-spacing:0.2em;color:#f0b429;text-transform:uppercase;margin:0 0 20px;">
                - Waitlist Confirmed
              </p>
              <h1 style="font-size:28px;font-weight:600;color:#e6edf3;margin:0 0 16px;line-height:1.2;letter-spacing:-0.01em;">
                You're on the list.
              </h1>
              <p style="font-size:14px;color:#7d8590;margin:0 0 32px;line-height:1.7;">
                We'll reach out when early access opens.
              </p>
              <table cellpadding="0" cellspacing="0" style="margin-bottom:32px;">
                <tr>
                  <td style="background:#f0b429;border-radius:2px;">
                    <a href="https://app.sentimentfx.org"
                       style="display:inline-block;padding:12px 28px;font-size:11px;font-weight:600;letter-spacing:0.1em;color:#080c10;text-decoration:none;text-transform:uppercase;">
                      View Live Dashboard
                    </a>
                  </td>
                </tr>
              </table>
              <p style="font-size:12px;color:#7d8590;margin:0;line-height:1.6;">
                No spam. We'll only email you when something worth knowing happens.
              </p>
            </td>
          </tr>
          <tr>
            <td style="border-top:1px solid #21262d;padding-top:20px;">
              <p style="font-size:10px;color:#7d8590;margin:0;letter-spacing:0.05em;line-height:1.7;">
                SentimentFX - Crypto sentiment intelligence<br>
                <a href="mailto:hello@sentimentfx.org" style="color:#f0b429;text-decoration:none;">hello@sentimentfx.org</a>
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
            """
        })
    except Exception as e:
        print(f"Email error: {e}")

    return {"message": "You're on the list!"}


@app.get("/waitlist/count")
def waitlist_count(db: Session = Depends(get_db)):
    count = db.query(models.WaitlistEmail).count()
    return {"count": count}


def _run_hn_backfill(ticker_list: list, days: int, chunk_days: int = 14,
                     start_days_ago: int = 0):
    db = SessionLocal()
    summary = {}
    try:
        for ticker in ticker_list:
            try:
                headlines = fetch_hn_headlines(
                    ticker, days=days,
                    chunk_days=chunk_days,
                    start_days_ago=start_days_ago,
                )
                saved = 0
                for h in headlines:
                    exists = db.query(models.Headline).filter(
                        models.Headline.url == h["url"]
                    ).first()
                    if exists:
                        continue
                    t0 = time.time()
                    sentiment = analyse_sentiment(h["title"])
                    FINBERT_LATENCY.observe(time.time() - t0)
                    db.add(models.Headline(
                        ticker=h["ticker"],
                        title=h["title"],
                        source=h["source"],
                        url=h["url"],
                        sentiment_score=sentiment["score"],
                        sentiment_label=sentiment["label"],
                        published_at=h["published_at"],
                    ))
                    HEADLINES_INGESTED.labels(source="hn", ticker=h["ticker"]).inc()
                    saved += 1
                db.commit()
                summary[ticker] = saved
                print(f"[HN-BACKFILL] {ticker}: +{saved} new of {len(headlines)} fetched")
            except Exception as e:
                db.rollback()
                summary[ticker] = f"error: {e}"
                print(f"[HN-BACKFILL] {ticker} error: {e}")
    finally:
        db.close()
    total = sum(v for v in summary.values() if isinstance(v, int))
    print(f"[HN-BACKFILL] done — +{total} new headlines across {len(summary)} tickers")


@app.post("/backfill/hn")
@app.post("/backfill")   # default backfill path is now HN Algolia (GDELT was retired — see CLAUDE.md)
def backfill_hn(
    background_tasks: BackgroundTasks,
    tickers: str = "all",
    days: int = 365,
    chunk_days: int = 14,
    start_days_ago: int = 0,
    admin=Depends(require_admin),
):
    if tickers.lower() == "all":
        ticker_list = list(HN_QUERIES.keys())
    else:
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    unknown = [t for t in ticker_list if t not in HN_QUERIES]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unknown tickers: {','.join(unknown)}")

    chunk_days     = max(1, min(chunk_days, 90))
    start_days_ago = max(0, min(start_days_ago, 3650))   # 10y back, plenty
    days           = max(1, min(days, 3650))
    background_tasks.add_task(
        _run_hn_backfill, ticker_list, days, chunk_days, start_days_ago,
    )
    range_label = (
        f"{start_days_ago}d–{start_days_ago + days}d ago"
        if start_days_ago else f"last {days} days"
    )
    return {
        "message": f"HN backfill queued for {len(ticker_list)} ticker(s) over {range_label} "
                   f"({chunk_days}d chunks) — check Railway logs for progress",
        "tickers": ticker_list,
        "days": days,
        "start_days_ago": start_days_ago,
        "chunk_days": chunk_days,
    }


@app.get("/admin/coverage")
def admin_coverage(secret: str = None, db: Session = Depends(get_db)):
    """Per-ticker oldest/newest headline + count.  Used to decide what gap to
    backfill: if BTC's oldest is 180 days ago, run /backfill with
    start_days_ago=180 to extend backward without re-fetching what's covered.
    Token-gated via the existing ADMIN_SECRET so it stays out of the public API.
    """
    if not os.getenv("ADMIN_SECRET") or secret != os.getenv("ADMIN_SECRET"):
        raise HTTPException(status_code=401, detail="Unauthorized")
    from sqlalchemy import func as _f
    rows = db.query(
        models.Headline.ticker,
        _f.count(models.Headline.id).label("count"),
        _f.min(models.Headline.published_at).label("oldest"),
        _f.max(models.Headline.published_at).label("newest"),
    ).group_by(models.Headline.ticker).all()
    now = datetime.utcnow()
    out = {}
    for ticker, count, oldest, newest in rows:
        oldest_days_ago = (now - oldest).days if oldest else None
        newest_days_ago = (now - newest).days if newest else None
        out[ticker] = {
            "count": count,
            "oldest": oldest.isoformat() + "Z" if oldest else None,
            "newest": newest.isoformat() + "Z" if newest else None,
            "oldest_days_ago": oldest_days_ago,
            "newest_days_ago": newest_days_ago,
        }
    return {"computed_at": now.isoformat() + "Z", "tickers": out}


@app.post("/alerts")
async def create_alert(request: Request, db: Session = Depends(get_db), user=Depends(require_pro)):
    body = await request.json()
    ticker = body.get("ticker", "").upper()
    threshold = float(body.get("threshold", 0.3))
    direction = body.get("direction", "above")

    if ticker not in TICKERS:
        raise HTTPException(status_code=400, detail="Unknown ticker")
    if direction not in ("above", "below"):
        raise HTTPException(status_code=400, detail="Direction must be 'above' or 'below'")

    alert = models.Alert(
        user_id=user.id,
        email=user.email,
        ticker=ticker,
        threshold=threshold,
        direction=direction,
        active=True
    )
    db.add(alert)
    db.commit()
    return {"message": f"Alert created for {ticker}"}


@app.get("/alerts")
def get_alerts(db: Session = Depends(get_db), user=Depends(require_pro)):
    alerts = db.query(models.Alert).filter(models.Alert.user_id == user.id).all()
    return alerts


@app.delete("/alerts/{alert_id}")
def delete_alert(alert_id: int, db: Session = Depends(get_db), user=Depends(require_pro)):
    alert = db.query(models.Alert).filter(
        models.Alert.id == alert_id,
        models.Alert.user_id == user.id
    ).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")
    db.delete(alert)
    db.commit()
    return {"message": "Alert deleted"}


@app.get("/alerts/preview/{ticker}")
def preview_trade_card(
    ticker: str,
    format: str = "json",
    db: Session = Depends(get_db),
    user=Depends(require_pro),
):
    """Render the trade-card that WOULD be sent if an alert fired right now.

    Used by the dashboard's alert-setup UI ("what will this alert look like?")
    and by any user who wants to inspect a ticker's current actionable signal
    without configuring a real alert.  Pro-only because the card is the
    main value-add of the Pro tier — exposing it free would undercut alerts.

    `format=html` returns the rendered email body for a faithful preview;
    default JSON returns the underlying card so the frontend can render
    a native UI on top of the same data.
    """
    if ticker.upper() not in TICKERS and ticker.upper() not in BACKGROUND_TICKERS:
        raise HTTPException(status_code=404, detail="Unknown ticker")
    card = build_trade_card(db, ticker)
    if format == "html":
        return Response(format_trade_card_html(card), media_type="text/html")
    if format == "text":
        return Response(format_trade_card_text(card), media_type="text/plain")
    return card


@app.post("/create-checkout-session")
def create_checkout_session(price_id: str, db: Session = Depends(get_db)):
    # Map price → tier.  Brief tier is the cheapest, no API allowance; Data
    # tier is the most expensive, gets the largest allowance; default is Pro.
    if price_id in BRIEF_PRICE_IDS:
        tier = "brief"
    elif price_id in DATA_PRICE_IDS:
        tier = "data"
    else:
        tier = "pro"
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            allow_promotion_codes=True,
            metadata={"tier": tier},
            success_url="https://app.sentimentfx.org?success=true",
            cancel_url="https://app.sentimentfx.org?cancelled=true",
        )
        return {"url": session.url}
    except Exception as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/prices/stripe")
def get_stripe_prices():
    prices = stripe.Price.list(active=True, expand=["data.product"])
    return {"prices": [
        {
            "id": p.id,
            "nickname": p.nickname,
            "unit_amount": p.unit_amount,
            "currency": p.currency,
            "interval": p.recurring.interval if p.recurring else None,
            "product_name": p.product.name if p.product else None,
        }
        for p in prices.data
    ]}


@app.post("/webhook")
async def stripe_webhook(request: Request):
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    try:
        event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
    except stripe.error.SignatureVerificationError:
        raise HTTPException(status_code=400, detail="Invalid signature")

    from supabase import create_client
    supabase_client = create_client(
        os.getenv("SUPABASE_URL"),
        os.getenv("SUPABASE_SERVICE_KEY")
    )

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session["customer_details"]["email"]
        tier = session.get("metadata", {}).get("tier", "pro")
        if customer_email:
            supabase_client.table("profiles").update({"tier": tier}).eq("email", customer_email).execute()
            # Brief tier intentionally gets zero API allowance — it's a
            # content product, not a data product.  Pro/Data get allowances.
            if tier == "data":
                allowance = DATA_MONTHLY_ALLOWANCE
            elif tier == "pro":
                allowance = PRO_MONTHLY_ALLOWANCE
            else:
                allowance = 0
            webhook_db = SessionLocal()
            try:
                api_key = webhook_db.query(models.APIKey).filter(models.APIKey.email == customer_email).first()
                if api_key:
                    api_key.monthly_allowance = allowance
                    webhook_db.commit()
            finally:
                webhook_db.close()
        refresh_subscription_gauge()

    elif event["type"] == "customer.subscription.deleted":
        subscription = event["data"]["object"]
        customer_id = subscription["customer"]
        customer = stripe.Customer.retrieve(customer_id)
        customer_email = customer.get("email")
        if customer_email:
            supabase_client.table("profiles").update({"tier": "free"}).eq("email", customer_email).execute()
        refresh_subscription_gauge()

    elif event["type"] == "invoice.payment_failed":
        invoice = event["data"]["object"]
        customer_email = invoice.get("customer_email")
        if customer_email:
            try:
                    resend.api_key = os.getenv("RESEND_API_KEY")
                    resend.Emails.send({
                        "from": "SentimentFX <hello@sentimentfx.org>",
                        "to": customer_email,
                        "subject": "SentimentFX – Payment failed",
                        "html": """
    <!DOCTYPE html>
    <html>
    <body style="margin:0;padding:0;background:#080c10;font-family:'Courier New',monospace;">
    <table width="100%" cellpadding="0" cellspacing="0" style="background:#080c10;padding:40px 20px;">
        <tr>
        <td align="center">
            <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
            <tr>
                <td style="border-bottom:1px solid #21262d;padding-bottom:20px;">
                <span style="font-size:13px;font-weight:600;letter-spacing:0.2em;color:#f0b429;text-transform:uppercase;">SentimentFX</span>
                </td>
            </tr>
            <tr>
                <td style="padding:40px 0 32px;">
                <p style="font-size:10px;letter-spacing:0.2em;color:#f0b429;text-transform:uppercase;margin:0 0 20px;">— Payment Failed</p>
                <h1 style="font-size:28px;font-weight:600;color:#e6edf3;margin:0 0 16px;line-height:1.2;">We couldn't process your payment</h1>
                <p style="font-size:14px;color:#7d8590;margin:0 0 24px;line-height:1.7;">
                    Your SentimentFX Pro subscription payment failed. Please update your payment details to keep access to Pro features.
                </p>
                <p style="font-size:13px;color:#7d8590;margin:0 0 32px;line-height:1.7;">
                    If this was a mistake or your card has been updated, you can retry from your billing portal.
                </p>
                <table cellpadding="0" cellspacing="0" style="margin-bottom:32px;">
                    <tr>
                    <td style="background:#f0b429;border-radius:2px;">
                        <a href="https://app.sentimentfx.org" style="display:inline-block;padding:12px 28px;font-size:11px;font-weight:600;letter-spacing:0.1em;color:#080c10;text-decoration:none;text-transform:uppercase;">
                        Update Payment →
                        </a>
                    </td>
                    </tr>
                </table>
                </td>
            </tr>
            <tr>
                <td style="border-top:1px solid #21262d;padding-top:20px;">
                <p style="font-size:10px;color:#7d8590;margin:0;letter-spacing:0.05em;line-height:1.7;">
                    SentimentFX · Crypto sentiment intelligence<br>
                    <a href="mailto:hello@sentimentfx.org" style="color:#f0b429;text-decoration:none;">hello@sentimentfx.org</a>
                </p>
                </td>
            </tr>
            </table>
        </td>
        </tr>
    </table>
    </body>
    </html>
                """
            })
            except Exception as e:
                print(f"Payment failed email error: {e}")
        
    return {"status": "ok"}


# ---------------------------------------------------------------------------
# API key management
# ---------------------------------------------------------------------------

@app.post("/api/keys/generate")
async def generate_api_key(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    email = body.get("email")

    if not email:
        raise HTTPException(status_code=400, detail="Email required")

    existing = db.query(models.APIKey).filter(models.APIKey.email == email).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A key already exists for this email. Use /api/keys/regenerate to get a new one."
        )

    stripe_customer_id, stripe_subscription_id = _create_stripe_customer(email)

    key = _make_key()
    api_key = models.APIKey(
        key_hash=_hash_key(key),
        key_prefix=key[:12],
        email=email,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
    )
    db.add(api_key)
    db.commit()

    # Plaintext key returned ONCE - never stored again after this point
    return {
        "key": key,
        "prefix": key[:12],
        "message": "API key generated. Save this key - it will not be shown again."
    }


@app.post("/api/keys/regenerate")
async def regenerate_api_key(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    email = body.get("email")

    if not email:
        raise HTTPException(status_code=400, detail="Email required")

    existing = db.query(models.APIKey).filter(models.APIKey.email == email).first()
    if not existing:
        raise HTTPException(status_code=404, detail="No key found for this email")

    new_key = _make_key()
    existing.key_hash = _hash_key(new_key)
    existing.key_prefix = new_key[:12]
    existing.active = True
    db.commit()

    return {
        "key": new_key,
        "prefix": new_key[:12],
        "message": "Key regenerated. Your old key is now invalid. Save this key - it will not be shown again."
    }


@app.get("/api/keys/info")
async def get_key_info(request: Request, db: Session = Depends(get_db)):
    """Returns non-sensitive key info by email - prefix and usage only, never the full key."""
    email = request.query_params.get("email")
    if not email:
        raise HTTPException(status_code=400, detail="Email required")

    existing = db.query(models.APIKey).filter(models.APIKey.email == email).first()
    if not existing:
        raise HTTPException(status_code=404, detail="No key found for this email")

    return {
        "prefix": existing.key_prefix,
        "calls_used": existing.calls_used,
        "free_remaining": max(0, existing.free_calls - existing.calls_used),
        "active": existing.active,
        "unlimited": bool(getattr(existing, "unlimited", False)),
    }


@app.post("/api/keys/generate-linked")
async def generate_linked_api_key(db: Session = Depends(get_db), user=Depends(require_pro)):
    """Generate an API key linked to the authenticated Supabase user."""
    email = user.email
    if not email:
        raise HTTPException(status_code=400, detail="No email on account")

    existing = db.query(models.APIKey).filter(models.APIKey.email == email).first()
    if existing:
        raise HTTPException(
            status_code=409,
            detail="A key already exists for this account."
        )

    try:
        from supabase import create_client
        supabase_client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
        profile = supabase_client.table("profiles").select("tier").eq("id", user.id).single().execute()
        tier = profile.data.get("tier", "free")
    except Exception:
        tier = "pro"

    allowance_map = {"pro": 1000, "data": 5000}
    monthly_allowance = allowance_map.get(tier, 1000)

    stripe_customer_id, stripe_subscription_id = _create_stripe_customer(email)

    key = _make_key()
    api_key = models.APIKey(
        key_hash=_hash_key(key),
        key_prefix=key[:12],
        email=email,
        stripe_customer_id=stripe_customer_id,
        stripe_subscription_id=stripe_subscription_id,
        monthly_allowance=monthly_allowance,
        free_calls=0,
    )
    db.add(api_key)
    db.commit()

    return {
        "key": key,
        "prefix": key[:12],
        "message": "API key generated. Save this key — it will not be shown again."
    }


@app.get("/api/keys/me")
async def get_my_key_info(db: Session = Depends(get_db), user=Depends(require_pro)):
    """Return API key info for the authenticated user (no full key)."""
    email = user.email
    if not email:
        raise HTTPException(status_code=400, detail="No email on account")

    existing = db.query(models.APIKey).filter(models.APIKey.email == email).first()
    if not existing:
        return {"has_key": False}

    # Sync monthly_allowance with current Supabase tier — handles keys created before this column existed
    try:
        from supabase import create_client
        _sc = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
        _profile = _sc.table("profiles").select("tier").eq("id", user.id).single().execute()
        _tier = _profile.data.get("tier", "free")
        _expected = {"pro": 1000, "data": 5000}.get(_tier, 0)
        if existing.monthly_allowance != _expected or existing.free_calls != 0:
            existing.monthly_allowance = _expected
            existing.free_calls = 0
            db.commit()
    except Exception:
        pass

    total_allowance = existing.free_calls + existing.monthly_allowance
    return {
        "has_key": True,
        "prefix": existing.key_prefix,
        "calls_used": existing.calls_used,
        "calls_this_month": existing.calls_this_month,
        "free_calls": existing.free_calls,
        "monthly_allowance": existing.monthly_allowance,
        "total_monthly": total_allowance,
        "active": existing.active,
        "unlimited": bool(getattr(existing, "unlimited", False)),
    }


@app.post("/api/keys/regenerate-linked")
async def regenerate_linked_api_key(db: Session = Depends(get_db), user=Depends(require_pro)):
    """Regenerate the API key for the authenticated user."""
    email = user.email
    if not email:
        raise HTTPException(status_code=400, detail="No email on account")

    existing = db.query(models.APIKey).filter(models.APIKey.email == email).first()
    if not existing:
        raise HTTPException(status_code=404, detail="No key found. Generate one first.")

    new_key = _make_key()
    existing.key_hash = _hash_key(new_key)
    existing.key_prefix = new_key[:12]
    existing.active = True
    db.commit()

    return {
        "key": new_key,
        "prefix": new_key[:12],
        "message": "Key regenerated. Your old key is now invalid. Save this key — it will not be shown again."
    }


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------

def track_usage(api_key: models.APIKey, db: Session, count: int = 1, endpoint: str = "unknown"):
    api_key.calls_used += count
    api_key.calls_this_month += count
    API_CALLS.labels(endpoint=endpoint).inc(count)

    # Internal / dogfood keys skip metering entirely — counters still tick for
    # observability (calls_used, calls_this_month, Prometheus API_CALLS) so we
    # can see internal traffic on the same dashboards.  What they DON'T do is
    # emit the Stripe MeterEvent, so they never bill regardless of volume.
    if getattr(api_key, "unlimited", False):
        db.commit()
        return

    total_allowance = api_key.free_calls + api_key.monthly_allowance
    if api_key.calls_this_month > total_allowance and api_key.stripe_customer_id:
        try:
            stripe.billing.MeterEvent.create(
                event_name="api_call",
                payload={
                    "stripe_customer_id": api_key.stripe_customer_id,
                    "value": str(count),
                }
            )
        except Exception as e:
            print(f"Stripe meter error: {e}")

    db.commit()


# ---------------------------------------------------------------------------
# Public v1 API
# ---------------------------------------------------------------------------

# Per-endpoint rate limits, surfaced in /v1/usage so integrators can discover
# them programmatically.  KEEP IN SYNC with the @limiter.limit decorators below.
_V1_RATE_LIMITS = {
    "/v1/sentiment/{ticker}":   "30/minute",
    "/v1/summary/{ticker}":     "20/minute",
    "/v1/prices/{ticker}":      "20/minute",
    "/v1/correlation/{ticker}": "10/minute",
    "/v1/usage":                "60/minute",
}


@app.get("/v1/usage", summary="Get API key usage", description="Introspect the calling API key: consumption this month, included allowance, overage billing status, and rate limits. Free — does not consume API credits.")
@limiter.limit("60/minute")
def api_usage(request: Request, response: Response, api_key=Depends(get_api_key)):
    now = datetime.utcnow()
    # Counters reset by the monthly cron at 00:00 UTC on the 1st.
    resets_at = datetime(now.year + (1 if now.month == 12 else 0),
                         1 if now.month == 12 else now.month + 1, 1)

    included = (api_key.free_calls or 0) + (api_key.monthly_allowance or 0)
    used = api_key.calls_this_month or 0

    if api_key.unlimited:
        plan = "unlimited"
    elif api_key.stripe_customer_id:
        plan = "metered"          # overage bills via Stripe after the allowance
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
        "rate_limits": _V1_RATE_LIMITS,
        "key_created_at": api_key.created_at.isoformat() + "Z" if api_key.created_at else None,
    }


@app.get("/v1/sentiment/{ticker}", summary="Get latest sentiment", description="Returns the latest FinBERT-scored headlines for a given ticker. Use `limit` to control how many results are returned (max 100). Each call costs 1 API credit per 25 headlines.")
@limiter.limit("30/minute")
def api_sentiment(request: Request, response: Response, ticker: str, limit: int = 25, db: Session = Depends(get_db), api_key=Depends(get_api_key)):
    import math
    calls = math.ceil(limit / 25)
    track_usage(api_key, db, calls, endpoint="sentiment")

    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper()
    ).order_by(models.Headline.published_at.desc()).limit(limit).all()

    if not headlines:
        raise HTTPException(status_code=404, detail="No data found")

    return {
        "ticker": ticker.upper(),
        "limit": limit,
        "calls_used": calls,
        "data": [
            {
                "date": h.published_at,
                "title": h.title,
                "source": h.source,
                "sentiment_score": h.sentiment_score,
                "sentiment_label": h.sentiment_label,
            } for h in headlines
        ]
    }


@app.get("/v1/summary/{ticker}", summary="Get daily sentiment summary", description="Returns aggregated daily sentiment scores for a given ticker over the specified number of days. Each day costs 1 API credit.")
@limiter.limit("20/minute")
def api_summary(request: Request, response: Response, ticker: str, days: int = 30, db: Session = Depends(get_db), api_key=Depends(get_api_key)):
    track_usage(api_key, db, days, endpoint="summary")

    since = datetime.utcnow() - timedelta(days=days)
    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper(),
        models.Headline.published_at >= since
    ).order_by(models.Headline.published_at.desc()).all()

    if not headlines:
        raise HTTPException(status_code=404, detail="No data found")

    by_date = {}
    for h in headlines:
        date = str(h.published_at.date())
        if date not in by_date:
            by_date[date] = []
        by_date[date].append(h.sentiment_score)

    summary = [
        {
            "date": date,
            "avg_sentiment": round(sum(scores) / len(scores), 4),
            "article_count": len(scores),
            "label": "positive" if sum(scores)/len(scores) > 0.1 else "negative" if sum(scores)/len(scores) < -0.1 else "neutral"
        }
        for date, scores in by_date.items()
    ]

    summary.sort(key=lambda x: x["date"], reverse=True)

    return {
        "ticker": ticker.upper(),
        "days": days,
        "calls_used": days,
        "data": summary
    }


@app.get("/v1/prices/{ticker}", summary="Get historical prices", description="Returns daily close prices for a given ticker over the specified number of days, in the ticker's native currency (GBP for crypto, USD for stocks/ETFs/commodities, or a raw exchange rate for FX pairs) -- see the `currency` field on each row. Each day costs 1 API credit.")
@limiter.limit("20/minute")
def api_prices(request: Request, response: Response, ticker: str, days: int = 30, db: Session = Depends(get_db), api_key=Depends(get_api_key)):
    track_usage(api_key, db, days, endpoint="prices")

    since = datetime.utcnow() - timedelta(days=days)
    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper(),
        models.Price.date >= since
    ).order_by(models.Price.date.desc()).all()

    if not prices:
        raise HTTPException(status_code=404, detail="No data found")

    # Crypto is genuinely GBP (yfinance BTC-GBP etc). FX pairs are a raw
    # exchange rate, not a currency amount. Everything else (stocks, ETFs,
    # commodity futures) is stored in native USD -- prices.py never converts
    # non-crypto tickers. Mirrors brief.py's _price_currency() -- keep in sync.
    category = _category_for(ticker.upper())
    currency = "GBP" if category == "crypto" else "RATE" if category == "fx" else "USD"

    return {
        "ticker": ticker.upper(),
        "days": days,
        "calls_used": days,
        "currency": currency,
        "data": [
            {
                "date": p.date,
                "close_price": p.close_price,
                "volume": p.volume,
            } for p in prices
        ]
    }


@app.get("/v1/correlation/{ticker}", summary="Get sentiment-price correlation", description="Returns a 180-day Pearson correlation analysis between sentiment shifts and next-day price returns, including signal strength, direction, and 95% confidence interval. Costs 1 API credit.")
@limiter.limit("10/minute")
def api_correlation(request: Request, response: Response, ticker: str, db: Session = Depends(get_db), api_key=Depends(get_api_key)):
    track_usage(api_key, db, endpoint="correlation")
    since = datetime.utcnow() - timedelta(days=180)

    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper(),
        models.Headline.published_at >= since
    ).order_by(models.Headline.published_at).all()

    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper(),
        models.Price.date >= since
    ).order_by(models.Price.date).all()

    if len(headlines) < 30 or len(prices) < 30:
        raise HTTPException(status_code=404, detail="Not enough data")

    daily = defaultdict(lambda: {"scores": [], "weights": []})
    for h in headlines:
        if abs(h.sentiment_score) < 0.05:
            continue
        d = h.published_at.date()
        weight = abs(h.sentiment_score)
        daily[d]["scores"].append(h.sentiment_score)
        daily[d]["weights"].append(weight)

    daily_sentiment = {}
    daily_volume = {}
    for d, v in daily.items():
        if not v["scores"]:
            continue
        w_sum = sum(v["weights"])
        daily_sentiment[d] = sum(s * w for s, w in zip(v["scores"], v["weights"])) / w_sum if w_sum else 0
        daily_volume[d] = len(v["scores"])

    sorted_prices = sorted(prices, key=lambda p: p.date)
    daily_return = {}
    for i in range(len(sorted_prices) - 1):
        d = sorted_prices[i].date.date()
        prev_close = sorted_prices[i].close_price
        next_close = sorted_prices[i + 1].close_price
        if prev_close and prev_close > 0:
            daily_return[d] = (next_close - prev_close) / prev_close * 100

    sorted_dates = sorted(daily_sentiment.keys())
    sentiment_shift = {}
    for i, d in enumerate(sorted_dates):
        window_start = max(0, i - 7)
        window = [daily_sentiment[sorted_dates[j]] for j in range(window_start, i)]
        if len(window) >= 3:
            sentiment_shift[d] = daily_sentiment[d] - (sum(window) / len(window))

    common = sorted(set(sentiment_shift.keys()) & set(daily_return.keys()) & set(daily_volume.keys()))
    if len(common) < 30:
        raise HTTPException(status_code=404, detail="Not enough overlapping data")

    shifts = np.array([sentiment_shift[d] for d in common])
    returns = np.array([daily_return[d] for d in common])

    def safe_corr(x, y):
        if len(x) < 10 or np.std(x) == 0 or np.std(y) == 0:
            return None, None, None
        r, p = pearsonr(x, y)
        n = len(x)
        if n < 4 or abs(r) >= 1:
            return round(r, 3), round(p, 4), None
        z = 0.5 * math.log((1 + r) / (1 - r))
        se = 1 / math.sqrt(n - 3)
        lo = math.tanh(z - 1.96 * se)
        hi = math.tanh(z + 1.96 * se)
        return round(r, 3), round(p, 4), [round(lo, 3), round(hi, 3)]

    shift_r, shift_p, shift_ci = safe_corr(shifts, returns)

    if abs(shift_r) >= 0.25:
        strength = "strong"
    elif abs(shift_r) >= 0.10:
        strength = "weak"
    else:
        strength = "inconclusive"

    direction = "negative (contrarian)" if shift_r < 0 else "positive (momentum)"

    return {
        "ticker": ticker.upper(),
        "calls_used": 1,
        "window_days": 180,
        "sample_size": len(common),
        "primary_signal": {
            "type": "sentiment_shift_vs_next_day_return",
            "correlation": shift_r,
            "p_value": shift_p,
            "ci_95": shift_ci,
            "strength": strength,
            "direction": direction,
        },
        "interpretation": (
            f"Sentiment shifts show a {strength} {direction} signal "
            f"(r={shift_r}, p={shift_p}, n={len(common)})"
        ),
    }

@app.get("/status")
def get_status(db: Session = Depends(get_db)):
    ticker_stats = {}
    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]
    for t in all_tickers:
        headline_count = db.query(models.Headline).filter(models.Headline.ticker == t).count()
        price_count = db.query(models.Price).filter(models.Price.ticker == t).count()
        latest_headline = db.query(models.Headline).filter(
            models.Headline.ticker == t
        ).order_by(models.Headline.published_at.desc()).first()
        latest_price = db.query(models.Price).filter(
            models.Price.ticker == t
        ).order_by(models.Price.date.desc()).first()
        ticker_stats[t] = {
            "category": _category_for(t),
            "headlines": headline_count,
            "prices": price_count,
            "latest_headline": latest_headline.published_at.isoformat() if latest_headline else None,
            "latest_price": latest_price.date.isoformat() if latest_price else None,
            "latest_price_value": latest_price.close_price if latest_price else None,
        }
    resolved_scrape_time = last_scrape_time
    if not resolved_scrape_time:
        latest = db.query(models.Headline).order_by(models.Headline.published_at.desc()).first()
        if latest:
            resolved_scrape_time = latest.published_at.isoformat()

    return {
        "status": "operational",
        "last_scrape": resolved_scrape_time,
        "last_scrape_duration_seconds": last_scrape_duration,
        "tickers": ticker_stats,
        "total_headlines": db.query(models.Headline).count(),
        "total_prices": db.query(models.Price).count(),
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }

@app.get("/health")
def health(db: Session = Depends(get_db)):
    """Liveness + readiness for uptime monitors and Railway health checks.

    503 when the DB is unreachable or the scheduler died (both mean the
    service can't do its job even if HTTP still answers).  `scrape_age_s`
    is informational — stale scrapes degrade freshness but don't merit a
    restart, so they don't flip the status code.
    """
    from sqlalchemy import text as _sql_text

    checks = {"db": "ok", "scheduler": "ok"}
    status_code = 200

    try:
        db.execute(_sql_text("SELECT 1"))
    except Exception as e:
        checks["db"] = f"error: {type(e).__name__}"
        status_code = 503

    if not scheduler.running:
        checks["scheduler"] = "not running"
        status_code = 503

    scrape_age_s = None
    if last_scrape_time:
        try:
            _last = datetime.fromisoformat(last_scrape_time)
            scrape_age_s = int((datetime.now(timezone.utc) - _last).total_seconds())
        except Exception:
            pass

    return JSONResponse(status_code=status_code, content={
        "status": "ok" if status_code == 200 else "degraded",
        "checks": checks,
        "scrape_age_s": scrape_age_s,
    })

@app.post("/api/keys/regenerate/request")
async def request_key_regenerate(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    email = body.get("email")

    if not email:
        raise HTTPException(status_code=400, detail="Email required")

    existing = db.query(models.APIKey).filter(models.APIKey.email == email).first()
    if not existing:
        # Don't reveal whether email exists — return same response either way
        return {"message": "If a key exists for this email, a reset link has been sent."}

    # Invalidate any existing unused tokens for this email
    db.query(models.KeyResetToken).filter(
        models.KeyResetToken.email == email,
        models.KeyResetToken.used == False
    ).delete()
    db.commit()

    # Generate token
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    expires_at = datetime.utcnow() + timedelta(minutes=30)

    reset_token = models.KeyResetToken(
        email=email,
        token_hash=token_hash,
        expires_at=expires_at
    )
    db.add(reset_token)
    db.commit()

    # Send email
    try:
        resend.api_key = os.getenv("RESEND_API_KEY")
        reset_url = f"https://developers.sentimentfx.org?reset={token}"
        resend.Emails.send({
            "from": "SentimentFX <hello@sentimentfx.org>",
            "to": email,
            "subject": "Reset your SentimentFX API key",
            "html": f"""
<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#080c10;font-family:'Courier New',monospace;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#080c10;padding:40px 20px;">
    <tr>
      <td align="center">
        <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
          <tr>
            <td style="border-bottom:1px solid #21262d;padding-bottom:20px;">
              <span style="font-size:13px;font-weight:600;letter-spacing:0.2em;color:#f0b429;text-transform:uppercase;">SentimentFX</span>
            </td>
          </tr>
          <tr>
            <td style="padding:40px 0 32px;">
              <p style="font-size:10px;letter-spacing:0.2em;color:#f0b429;text-transform:uppercase;margin:0 0 20px;">— API Key Reset</p>
              <h1 style="font-size:24px;font-weight:600;color:#e6edf3;margin:0 0 16px;">Reset your API key</h1>
              <p style="font-size:14px;color:#7d8590;margin:0 0 24px;line-height:1.7;">
                Click the button below to regenerate your API key. Your current key will be immediately invalidated.
                This link expires in <strong style="color:#e6edf3;">30 minutes</strong>.
              </p>
              <p style="font-size:13px;color:#7d8590;margin:0 0 32px;">
                If you didn't request this, you can safely ignore this email. Your key will not change.
              </p>
              <table cellpadding="0" cellspacing="0" style="margin-bottom:32px;">
                <tr>
                  <td style="background:#f0b429;border-radius:2px;">
                    <a href="{reset_url}" style="display:inline-block;padding:12px 28px;font-size:11px;font-weight:600;letter-spacing:0.1em;color:#080c10;text-decoration:none;text-transform:uppercase;">
                      Reset API Key →
                    </a>
                  </td>
                </tr>
              </table>
              <p style="font-size:11px;color:#7d8590;margin:0;">
                Or copy this link: <span style="color:#58a6ff;">{reset_url}</span>
              </p>
            </td>
          </tr>
          <tr>
            <td style="border-top:1px solid #21262d;padding-top:20px;">
              <p style="font-size:10px;color:#7d8590;margin:0;line-height:1.7;">
                SentimentFX · Crypto sentiment intelligence<br>
                <a href="mailto:hello@sentimentfx.org" style="color:#f0b429;text-decoration:none;">hello@sentimentfx.org</a>
              </p>
            </td>
          </tr>
        </table>
      </td>
    </tr>
  </table>
</body>
</html>
            """
        })
    except Exception as e:
        print(f"Reset email error: {e}")

    return {"message": "If a key exists for this email, a reset link has been sent."}


@app.post("/api/keys/regenerate/confirm")
async def confirm_key_regenerate(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    token = body.get("token")

    if not token:
        raise HTTPException(status_code=400, detail="Token required")

    token_hash = hashlib.sha256(token.encode()).hexdigest()

    reset_token = db.query(models.KeyResetToken).filter(
        models.KeyResetToken.token_hash == token_hash,
        models.KeyResetToken.used == False,
        models.KeyResetToken.expires_at > datetime.utcnow()
    ).first()

    if not reset_token:
        raise HTTPException(status_code=400, detail="Invalid or expired reset link")

    existing = db.query(models.APIKey).filter(
        models.APIKey.email == reset_token.email
    ).first()

    if not existing:
        raise HTTPException(status_code=404, detail="No key found for this email")

    # Regenerate key
    new_key = _make_key()
    existing.key_hash = _hash_key(new_key)
    existing.key_prefix = new_key[:12]
    existing.active = True

    # Mark token as used
    reset_token.used = True
    db.commit()

    return {
        "key": new_key,
        "prefix": new_key[:12],
        "message": "Key regenerated. Save this key - it will not be shown again."
    }

@app.get("/divergence/{ticker}")
def get_divergence(ticker: str, db: Session = Depends(get_db)):
    since = datetime.utcnow() - timedelta(days=45)

    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper(),
        models.Headline.published_at >= since
    ).order_by(models.Headline.published_at).all()

    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper(),
        models.Price.date >= since
    ).order_by(models.Price.date).all()

    if len(headlines) < 10 or len(prices) < 14:
        return {"message": "Not enough data"}

    # Build daily weighted sentiment
    daily = defaultdict(lambda: {"scores": [], "weights": []})
    for h in headlines:
        if abs(h.sentiment_score) < 0.05:
            continue
        d = h.published_at.date()
        daily[d]["scores"].append(h.sentiment_score)
        daily[d]["weights"].append(abs(h.sentiment_score))

    daily_sentiment = {}
    for d, v in daily.items():
        if not v["scores"]:
            continue
        w_sum = sum(v["weights"])
        daily_sentiment[d] = sum(s * w for s, w in zip(v["scores"], v["weights"])) / w_sum if w_sum else 0

    daily_price = {p.date.date(): p.close_price for p in prices}

    common = sorted(set(daily_sentiment.keys()) & set(daily_price.keys()))

    if len(common) < 14:
        return {"message": "Not enough overlapping data"}

    THRESHOLD_S = 0.02   # minimum meaningful sentiment shift
    THRESHOLD_P = 0.5    # minimum meaningful price change (%)

    def _check_window(window):
        p7, r7 = window[:7], window[7:]
        ps = sum(daily_sentiment[d] for d in p7) / 7
        rs = sum(daily_sentiment[d] for d in r7) / 7
        pp = sum(daily_price[d] for d in p7) / 7
        rp = sum(daily_price[d] for d in r7) / 7
        sc = rs - ps
        pc = (rp - pp) / pp * 100 if pp > 0 else 0
        s_up = sc > THRESHOLD_S
        s_dn = sc < -THRESHOLD_S
        p_up = pc > THRESHOLD_P
        p_dn = pc < -THRESHOLD_P
        if s_up and p_dn:
            return "bullish", sc, pc
        if s_dn and p_up:
            return "bearish", sc, pc
        return "none", sc, pc

    divergence_type, sentiment_change, price_change_pct = _check_window(common[-14:])

    # Count consecutive days (sliding window back)
    streak = 0
    if divergence_type != "none":
        for shift in range(min(len(common) - 14, 30)):
            end = len(common) - shift
            if end < 14:
                break
            dtype, _, _ = _check_window(common[end - 14:end])
            if dtype == divergence_type:
                streak += 1
            else:
                break

    magnitude = round(min(abs(sentiment_change) * abs(price_change_pct) / 5, 1.0), 3)

    sent_dir = "up" if sentiment_change > THRESHOLD_S else "down" if sentiment_change < -THRESHOLD_S else "flat"
    price_dir = "up" if price_change_pct > THRESHOLD_P else "down" if price_change_pct < -THRESHOLD_P else "flat"
    sign_s = "+" if sentiment_change >= 0 else ""
    sign_p = "+" if price_change_pct >= 0 else ""
    # Dashboard-display 0-100 scale for the human-readable summary only — see
    # the matching comment in get_signal(); sentiment_change_7d below stays
    # on the native -1..1 scale for API/alert consumers.
    display_sentiment_change = round(sentiment_change * 50)

    if divergence_type == "bullish":
        summary = (
            f"{ticker.upper()} sentiment is rising ({sign_s}{display_sentiment_change}) "
            f"while price is falling ({sign_p}{round(price_change_pct, 1)}%) over the last 7 days. "
            f"Bullish divergence — narrative improvement has not yet been reflected in price."
        )
    elif divergence_type == "bearish":
        summary = (
            f"{ticker.upper()} sentiment is falling ({sign_s}{display_sentiment_change}) "
            f"while price is rising ({sign_p}{round(price_change_pct, 1)}%) over the last 7 days. "
            f"Bearish divergence — price is rising against deteriorating sentiment."
        )
    else:
        summary = (
            f"{ticker.upper()} sentiment ({sign_s}{display_sentiment_change}) and price "
            f"({sign_p}{round(price_change_pct, 1)}%) are moving in alignment over the last 7 days — "
            f"no meaningful divergence detected."
        )

    return {
        "ticker": ticker.upper(),
        "date": str(common[-1]),
        "divergence": divergence_type,
        "sentiment_direction": sent_dir,
        "price_direction": price_dir,
        "sentiment_change_7d": round(sentiment_change, 4),
        "price_change_7d": round(price_change_pct, 2),
        "streak_days": streak,
        "magnitude": magnitude,
        "summary": summary,
    }


@app.get("/leaderboard")
def get_leaderboard(db: Session = Depends(get_db)):
    """Public market-wide sentiment movers board.

    Returns one row per tracked ticker (all 42 across crypto/FX/stocks/ETFs/
    commodities) ranked by absolute 24h sentiment change.  Designed as an SEO
    landing surface — no auth, no paywall, served with the standard public-data
    Cache-Control so the CDN absorbs traffic spikes.

    Two bulk queries (headlines for last 48h, prices for last 5d) feed in-Python
    aggregation rather than 42 × N round-trips.  Sentiment is volume-weighted
    (|score| weighting) to match the existing /divergence convention so the
    leaderboard and divergence card agree on what "today's sentiment" means.
    """
    now = datetime.utcnow()
    cutoff_now = now - timedelta(hours=24)
    cutoff_prev = now - timedelta(hours=48)
    price_cutoff = now - timedelta(days=5)

    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]

    # Pull only the columns we need; published_at index on Headline keeps this cheap.
    headline_rows = db.query(
        models.Headline.ticker,
        models.Headline.sentiment_score,
        models.Headline.published_at,
    ).filter(models.Headline.published_at >= cutoff_prev).all()

    now_bucket = defaultdict(lambda: {"num": 0.0, "den": 0.0, "cnt": 0})
    prev_bucket = defaultdict(lambda: {"num": 0.0, "den": 0.0, "cnt": 0})
    for ticker, score, ts in headline_rows:
        bucket = now_bucket if ts >= cutoff_now else prev_bucket
        w = abs(score) if score is not None else 0.0
        if w == 0:
            continue   # neutral-scored articles contribute nothing to a weighted mean
        b = bucket[ticker]
        b["num"] += score * w
        b["den"] += w
        b["cnt"] += 1

    def _wavg(b):
        return b["num"] / b["den"] if b["den"] > 0 else None

    # Prices: last 5d gives ample slack for weekend/holiday gaps when finding a
    # "prior" close ≥24h before latest (24h crypto + 3-day equity weekend covered).
    price_rows = db.query(
        models.Price.ticker,
        models.Price.date,
        models.Price.close_price,
    ).filter(models.Price.date >= price_cutoff).order_by(
        models.Price.ticker, models.Price.date.desc()
    ).all()
    price_index = defaultdict(list)
    for ticker, date, close in price_rows:
        price_index[ticker].append((date, close))

    rows = []
    for t in all_tickers:
        s_now = _wavg(now_bucket[t])
        s_prev = _wavg(prev_bucket[t])
        sentiment_change = (s_now - s_prev) if (s_now is not None and s_prev is not None) else None

        prices = price_index.get(t, [])
        latest_close = prices[0][1] if prices else None
        prior_close = None
        if prices:
            latest_date = prices[0][0]
            # Walk back until we find a close that's at least ~20h older — robust
            # against intraday duplicate rows from the latest-price refresh path.
            for d, c in prices[1:]:
                if (latest_date - d) >= timedelta(hours=20):
                    prior_close = c
                    break
        price_change_pct = (
            (latest_close - prior_close) / prior_close * 100
            if (latest_close is not None and prior_close)
            else None
        )

        rows.append({
            "ticker": t,
            "category": _category_for(t),
            "sentiment_24h": round(s_now, 4) if s_now is not None else None,
            "sentiment_change_24h": round(sentiment_change, 4) if sentiment_change is not None else None,
            "article_count_24h": now_bucket[t]["cnt"],
            "price": round(latest_close, 4) if latest_close is not None else None,
            "price_change_24h_pct": round(price_change_pct, 2) if price_change_pct is not None else None,
        })

    # Default sort: tickers with NO 24h sentiment change sink to the bottom; the
    # rest sorted by absolute change descending so the biggest movers (in either
    # direction) lead.  Frontend can re-sort client-side for the other views.
    rows.sort(key=lambda r: (
        r["sentiment_change_24h"] is None,
        -abs(r["sentiment_change_24h"]) if r["sentiment_change_24h"] is not None else 0.0,
    ))

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_hours": 24,
        "default_sort": "abs_sentiment_change_24h_desc",
        "rows": rows,
    }


@app.get("/backtest/{ticker}")
def get_backtest(
    ticker: str,
    signal: str = "divergence",
    hold_days: int = 7,
    direction_mode: str = "momentum",
    costs_bps: int | None = None,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    size_pct: float = 100.0,
    threshold_s: float | None = None,
    threshold_p: float | None = None,
    shift_thresh: float | None = None,
    db: Session = Depends(get_db),
):
    """Interactive backtest for any tracked ticker (crypto/FX/stocks/ETFs/
    commodities alike — the same query path serves all 42).

    `direction_mode`, `costs_bps`, `by_regime`, `walk_forward` mirror what
    /admin/backtest-board has always supported — this endpoint used to run
    its own momentum-only, cost-blind simulation in parallel (former marker:
    BACKTEST_THRESHOLDS_DUPLICATED); it now shares `_simulate_trades` with
    the admin board so both stay in sync automatically.

    `stop_loss_pct`/`take_profit_pct` (magnitude, e.g. 5.0 = 5%) exit a trade
    early if price moves against/for you by that much before hold_days is up.
    `size_pct` (1-100) scales how much of the equity curve each trade risks.
    `threshold_s`/`threshold_p`/`shift_thresh` override the signal-detection
    thresholds instead of the hardcoded defaults. All five are optional and
    default to today's production behaviour — see _simulate_trades.

    `summary` now reports both `gross` and `net` (cost-adjusted) blocks, same
    convention as the admin board — `net` is the honest number.
    """
    hold_days = max(1, min(hold_days, 30))
    if signal not in ("divergence", "shift"):
        raise HTTPException(status_code=400, detail="signal must be 'divergence' or 'shift'")
    if direction_mode not in ("momentum", "contrarian"):
        raise HTTPException(status_code=400, detail="direction_mode must be 'momentum' or 'contrarian'")
    if costs_bps is not None:
        costs_bps = max(0, min(costs_bps, 500))
    size_pct = max(1.0, min(size_pct, 100.0))
    if stop_loss_pct is not None:
        stop_loss_pct = max(0.5, min(stop_loss_pct, 90.0))
    if take_profit_pct is not None:
        take_profit_pct = max(0.5, min(take_profit_pct, 500.0))
    if threshold_s is not None:
        threshold_s = max(0.001, min(threshold_s, 1.0))
    if threshold_p is not None:
        threshold_p = max(0.01, min(threshold_p, 50.0))
    if shift_thresh is not None:
        shift_thresh = max(0.001, min(shift_thresh, 1.0))

    since = datetime.utcnow() - timedelta(days=365)

    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper(),
        models.Headline.published_at >= since
    ).order_by(models.Headline.published_at).all()

    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper(),
        models.Price.date >= since
    ).order_by(models.Price.date).all()

    if len(headlines) < 20 or len(prices) < 30:
        return {"message": "Not enough data", "trades": [], "equity_curve": []}

    daily_sentiment, daily_price, common = _build_daily_series(headlines, prices)
    sorted_price_dates = sorted(daily_price.keys())

    if len(common) < 20:
        return {"message": "Not enough overlapping data", "trades": [], "equity_curve": []}

    costs_pct = _costs_pct_for(ticker, costs_bps)
    size_frac = size_pct / 100.0

    trades_raw = _simulate_trades(
        daily_sentiment, daily_price, common, signal, hold_days,
        direction_mode=direction_mode,
        threshold_s=threshold_s, threshold_p=threshold_p, shift_thresh=shift_thresh,
        stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct, size_pct=size_pct,
    )

    if not trades_raw:
        return {
            "ticker": ticker.upper(), "signal": signal, "hold_days": hold_days,
            "message": "No trades generated — signal did not fire in this window.",
            "trades": [], "equity_curve": [],
        }

    costs_sized = costs_pct * size_frac

    def _agg(returns, hold_days_):
        n = len(returns)
        winning = sum(1 for r in returns if r > 0)
        running = peak = 100.0
        max_dd = 0.0
        for r in returns:
            running *= (1 + r / 100)
            peak = max(peak, running)
            max_dd = min(max_dd, (running - peak) / peak * 100)
        r_arr = np.array(returns)
        sharpe = None
        if n >= 3 and np.std(r_arr) > 0:
            sharpe = round(float(np.mean(r_arr) / np.std(r_arr) * np.sqrt(252 / hold_days_)), 2)
        return {
            "total_trades": n,
            "winning_trades": winning,
            "win_rate": round(winning / n, 3),
            "avg_return_pct": round(float(np.mean(r_arr)), 2),
            "total_return_pct": round(running - 100, 2),
            "max_drawdown_pct": round(max_dd, 2),
            "sharpe": sharpe,
        }

    sized_returns = [t["sized_return_pct"] for t in trades_raw]
    net_returns = [r - costs_sized for r in sized_returns]
    gross_summary = _agg(sized_returns, hold_days)
    net_summary = _agg(net_returns, hold_days)

    first_price = daily_price[sorted_price_dates[0]]
    last_price = daily_price[sorted_price_dates[-1]]
    buy_hold = round((last_price - first_price) / first_price * 100, 2)
    for s in (gross_summary, net_summary):
        s["buy_hold_return_pct"] = buy_hold
        s["alpha_pct"] = round(s["total_return_pct"] - buy_hold, 2)

    trades = [{
        "entry_date": str(t["entry_date"]),
        "exit_date": str(t["exit_date"]),
        "entry_price": round(t["entry_price"], 2),
        "exit_price": round(t["exit_price"], 2),
        "return_pct": round(t["sized_return_pct"], 2),
        "exit_reason": t["exit_reason"],
    } for t in trades_raw]

    # Daily equity curve — net-of-cost, sized. Tracks the live price ratio
    # during an open trade (so drawdown mid-hold is visible, not just
    # entry/exit jumps) scaled by size_frac; the round-trip cost is applied
    # once, as a lump deduction, at the exit day — matching how net_summary
    # compounds discrete per-trade net returns.
    portfolio_cash = 100.0
    pending = None  # (exit_date, exit_price, portfolio_at_entry, entry_price)
    trade_by_entry = {t["entry_date"]: (t["exit_date"], t["exit_price"]) for t in trades_raw}

    equity_curve = []
    for d in sorted_price_dates:
        price = daily_price[d]

        if pending and d >= pending[0]:
            _, p_exit_price, p_entry_val, p_entry_price = pending
            gross_mult = p_exit_price / p_entry_price
            sized_mult = 1 + size_frac * (gross_mult - 1) - size_frac * costs_pct / 100
            portfolio_cash = p_entry_val * sized_mult
            pending = None

        if d in trade_by_entry and pending is None:
            xd, xp = trade_by_entry[d]
            pending = (xd, xp, portfolio_cash, price)

        if pending:
            _, _, p_entry_val, p_entry_price = pending
            current = p_entry_val * (1 + size_frac * (price / p_entry_price - 1))
        else:
            current = portfolio_cash

        equity_curve.append({
            "date": str(d),
            "portfolio": round(current, 2),
            "buy_hold": round(100.0 * price / first_price, 2),
        })

    # Optional depth blocks: regime breakdown + walk-forward stability.
    # Both reuse the helpers used by the admin board so a single bug-fix on
    # either keeps the public single-ticker endpoint in sync.  Cost is one
    # extra simulation pass each; cheap relative to the headline/price queries
    # we already did.
    by_regime = _regime_split_stats(
        daily_sentiment, daily_price, common, signal, hold_days, costs_pct,
        direction_mode=direction_mode,
        threshold_s=threshold_s, threshold_p=threshold_p, shift_thresh=shift_thresh,
        stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct, size_pct=size_pct,
    )
    walk_fwd = _walk_forward(
        daily_sentiment, daily_price, common, signal, hold_days, costs_pct,
        direction_mode=direction_mode,
        threshold_s=threshold_s, threshold_p=threshold_p, shift_thresh=shift_thresh,
        stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct, size_pct=size_pct,
    )

    return {
        "ticker": ticker.upper(),
        "signal": signal,
        "hold_days": hold_days,
        "direction_mode": direction_mode,
        "window_days": (sorted_price_dates[-1] - sorted_price_dates[0]).days,
        "summary": {
            "gross": gross_summary,
            "net": net_summary,
            "costs_pct_per_trade": costs_pct,
        },
        "by_regime": by_regime,
        "walk_forward": walk_fwd,
        "trades": trades,
        "equity_curve": equity_curve,
    }


# Round-trip transaction-cost defaults by asset category, expressed in basis
# points (1 bp = 0.01%).  These approximate retail-broker reality across one
# full entry+exit.  Override per-request with ?costs_bps=N on the admin board.
#
#  - crypto:      ~10 bps taker fee + ~5 bps slippage, both sides ≈ 30 bps RT
#  - fx:          spread on EUR/USD ~0.7 pips ≈ 7-8 bps + retail markup → ~15 bps RT
#  - stocks:      0 commission retail, ~3 bps spread per side → ~6 bps RT
#  - etfs:        major ETFs trade tighter than single names → ~4 bps RT
#  - commodities: futures spread + commission → ~12 bps RT
#
# These are conservative — real fills on a small retail account may be worse;
# real fills on a market-making prop account would be better.  The point is
# to remove the cost-blind backtest artifact, not to pin to a specific broker.
_TICKER_COSTS_BPS_DEFAULT = {
    "crypto":      30,
    "fx":          15,
    "stocks":       6,
    "etfs":         4,
    "commodities": 12,
}

# In-memory cache for the admin backtest board.  Computing all 42 tickers
# synchronously takes ~13s on Railway (180d headlines + 180d prices per
# ticker, then trade simulation).  Results don't change minute-to-minute
# so a 1h TTL is fine.  Cache is process-local; survives within a Railway
# instance but resets on redeploy — acceptable for an admin tool.
_BACKTEST_BOARD_CACHE = {"key": None, "computed_at": None, "data": None}
_BACKTEST_BOARD_TTL_SECONDS = 3600


def _costs_pct_for(ticker: str, override_bps: int | None) -> float:
    """Return RT cost as a percentage (not bps).  override_bps wins if set."""
    bps = override_bps if override_bps is not None else \
          _TICKER_COSTS_BPS_DEFAULT.get(_category_for(ticker), 10)
    return bps / 100.0   # 30 bps → 0.30%


def _simulate_trades(
    daily_sentiment: dict,
    daily_price: dict,
    common: list,
    signal: str,
    hold_days: int,
    direction_mode: str = "momentum",
    threshold_s: float | None = None,
    threshold_p: float | None = None,
    shift_thresh: float | None = None,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    size_pct: float = 100.0,
) -> list:
    """Shared trade-simulation core for get_backtest, _compact_backtest_summary
    and _regime_split_stats — the single place the divergence/shift signal
    rules and the entry/exit walk live now, replacing three near-duplicate
    copies of the same logic (former marker: BACKTEST_THRESHOLDS_DUPLICATED).

    Returns a list of raw per-trade dicts: entry_date, exit_date, entry_price,
    exit_price, gross_return_pct, sized_return_pct (gross scaled by
    size_pct/100 — equal to gross when size_pct=100, the default), regime
    (bull/bear/chop/unknown, by entry-day trailing trend), exit_reason
    ("hold_days"/"stop_loss"/"take_profit"). Callers aggregate, apply costs,
    and bucket as needed — this function only knows signal generation and
    trade mechanics.

    `direction_mode`: "momentum" (default) longs on bullish signals;
    "contrarian" longs on bearish signals instead (buy the panic). Both
    long-only so P&L stays directly comparable.

    `threshold_s`/`threshold_p`/`shift_thresh` override the divergence/shift
    signal-detection thresholds (defaults 0.02 / 0.5 / 0.05 when None —
    exactly the constants this logic always used).

    `stop_loss_pct`/`take_profit_pct` are OPTIONAL early-exit thresholds
    (e.g. 5.0 = a 5% adverse/favourable move from entry). When both are None
    — the only configuration the production alert gate / SignalQuality ever
    uses — the exit date is computed exactly as before this function existed:
    a straight jump to the first price on/after entry_date + hold_days. SL/TP
    only changes anything when a caller opts in, so this is a behaviour-
    preserving extraction for every existing caller.

    `size_pct` (0-100, default 100) scales each trade's contribution to
    compounding — the untraded remainder is implicitly flat cash. At the
    default 100 this is a no-op (sized_return_pct == gross_return_pct).
    """
    if len(common) < 14:
        return []

    sorted_price_dates = sorted(daily_price.keys())
    ts = threshold_s if threshold_s is not None else 0.02
    tp_thr = threshold_p if threshold_p is not None else 0.5
    st = shift_thresh if shift_thresh is not None else 0.05

    signal_series = {}
    if signal == "divergence":
        for i in range(14, len(common) + 1):
            window = common[i - 14:i]
            p7, r7 = window[:7], window[7:]
            ps = sum(daily_sentiment[d] for d in p7) / 7
            rs = sum(daily_sentiment[d] for d in r7) / 7
            pp = sum(daily_price[d] for d in p7) / 7
            rp = sum(daily_price[d] for d in r7) / 7
            sc = rs - ps
            pc = (rp - pp) / pp * 100 if pp > 0 else 0
            if sc > ts and pc < -tp_thr:
                signal_series[common[i - 1]] = "bullish"
            elif sc < -ts and pc > tp_thr:
                signal_series[common[i - 1]] = "bearish"
    else:   # "shift"
        for i, d in enumerate(common):
            prior = [daily_sentiment[common[j]] for j in range(max(0, i - 7), i)]
            if len(prior) >= 3:
                shift = daily_sentiment[d] - sum(prior) / len(prior)
                if shift > st:
                    signal_series[d] = "bullish"
                elif shift < -st:
                    signal_series[d] = "bearish"

    # Long-only simulation, no overlap.  Entry on next available price after
    # signal day. Baseline exit is the first price on/after entry+hold_days
    # (window edge case: may fall outside `common`, which is intentional so a
    # signal firing near the end of a window doesn't get truncated). SL/TP,
    # when set, can only pull that exit EARLIER — never later — by scanning
    # the dates strictly between entry and the baseline exit.
    #
    # `entry_trigger` flips with direction_mode: momentum acts on bullish
    # signals (take the trend), contrarian acts on bearish signals (fade the
    # crash).
    entry_trigger = "bearish" if direction_mode == "contrarian" else "bullish"
    size_frac = max(0.0, min(size_pct, 100.0)) / 100.0

    trades = []
    in_trade_until = None
    for d in sorted(signal_series.keys()):
        if in_trade_until and d <= in_trade_until:
            continue
        if signal_series[d] != entry_trigger:
            continue
        entry_date = next((pd for pd in sorted_price_dates if pd > d), None)
        if not entry_date:
            continue
        entry_price = daily_price[entry_date]

        target = entry_date + timedelta(days=hold_days)
        baseline_exit_date = next((pd for pd in sorted_price_dates if pd >= target), sorted_price_dates[-1])

        exit_date, exit_price, exit_reason = baseline_exit_date, daily_price[baseline_exit_date], "hold_days"
        if stop_loss_pct is not None or take_profit_pct is not None:
            for pd in sorted_price_dates:
                if pd <= entry_date or pd > baseline_exit_date:
                    continue
                px = daily_price[pd]
                move_pct = (px - entry_price) / entry_price * 100
                if stop_loss_pct is not None and move_pct <= -abs(stop_loss_pct):
                    exit_date, exit_price, exit_reason = pd, px, "stop_loss"
                    break
                if take_profit_pct is not None and move_pct >= abs(take_profit_pct):
                    exit_date, exit_price, exit_reason = pd, px, "take_profit"
                    break

        gross_ret = (exit_price - entry_price) / entry_price * 100
        regime = _regime_for_date(daily_price, sorted_price_dates, entry_date) or "unknown"
        trades.append({
            "entry_date": entry_date,
            "exit_date": exit_date,
            "entry_price": entry_price,
            "exit_price": exit_price,
            "gross_return_pct": gross_ret,
            "sized_return_pct": gross_ret * size_frac,
            "regime": regime,
            "exit_reason": exit_reason,
        })
        in_trade_until = exit_date

    return trades


def _compact_backtest_summary(
    daily_sentiment: dict,
    daily_price: dict,
    common: list,
    signal: str,
    hold_days: int,
    costs_pct: float,
    direction_mode: str = "momentum",
    threshold_s: float | None = None,
    threshold_p: float | None = None,
    shift_thresh: float | None = None,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    size_pct: float = 100.0,
):
    """Pure trade-sim + summary stats over a date window.  No equity curve,
    no per-trade list.  Returns a dict with both `gross` and `net` summaries,
    or None if the window can't generate any trades.

    The `net` block subtracts `costs_pct` (scaled by size_pct, since costs are
    only paid on the capital actually traded) from each trade's gross return
    BEFORE compounding — so the net total_return is the geometric truth, not
    just gross minus (n × costs).

    Trade simulation itself lives in `_simulate_trades` — this function just
    aggregates its output into gross/net stats blocks.
    """
    trades = _simulate_trades(
        daily_sentiment, daily_price, common, signal, hold_days,
        direction_mode=direction_mode,
        threshold_s=threshold_s, threshold_p=threshold_p, shift_thresh=shift_thresh,
        stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct, size_pct=size_pct,
    )
    if not trades:
        return None

    size_frac = max(0.0, min(size_pct, 100.0)) / 100.0
    costs_sized = costs_pct * size_frac

    def _stats(returns):
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
    return {
        "gross": _stats(sized_returns),
        "net": _stats([r - costs_sized for r in sized_returns]),
        "costs_pct_per_trade": costs_pct,
    }


# ---------------------------------------------------------------------------
# Backtest depth helpers — walk-forward + regime tagging
# ---------------------------------------------------------------------------
# A single IS/OOS split is one number that can luck out on a friendly regime.
# Walk-forward slides a fixed-size test window through history so we get N
# stability points instead.  Regime tagging buckets trade returns by the
# trailing 60-day market trend so a strategy that's positive on average but
# bleeds in bears can't hide behind the average.
#
# Neither feature retrains anything — the strategy thresholds (THRESHOLD_S,
# THRESHOLD_P, SHIFT_THRESH) are static.  We're measuring stability of a
# fixed rule across time, not optimising it.

# Regime definition: log-return slope over `lookback` days, annualised.
# We classify a day as bull/bear/chop based on the trailing trend, then tag
# each TRADE by its entry-day regime.  Thresholds are deliberately generous
# (±15% annualised) so most days fall into bull/bear, not chop.
_REGIME_LOOKBACK_DAYS  = 60
_REGIME_BULL_THRESHOLD =  0.15    # annualised log-return
_REGIME_BEAR_THRESHOLD = -0.15


def _regime_for_date(daily_price: dict, sorted_price_dates: list, d) -> str | None:
    """Classify date `d` as 'bull'/'bear'/'chop' based on trailing 60d trend.

    Returns None if we don't have enough lookback (early window edge).  We use
    log returns so the threshold is scale-free across BTC (£70k) and DOGE (£0.05);
    the same +15% annualised drift means the same thing on both.
    """
    if d not in daily_price:
        return None
    end_idx = sorted_price_dates.index(d) if d in sorted_price_dates else None
    if end_idx is None or end_idx < _REGIME_LOOKBACK_DAYS:
        return None
    start_date = sorted_price_dates[end_idx - _REGIME_LOOKBACK_DAYS]
    start_p = daily_price[start_date]
    end_p   = daily_price[d]
    if start_p <= 0 or end_p <= 0:
        return None
    days = (d - start_date).days or 1
    # log-return over the lookback, annualised to 252 trading-day equivalents
    log_ret = math.log(end_p / start_p)
    annualised = log_ret * (252 / days)
    if annualised >= _REGIME_BULL_THRESHOLD:
        return "bull"
    if annualised <= _REGIME_BEAR_THRESHOLD:
        return "bear"
    return "chop"


def _regime_split_stats(
    daily_sentiment: dict,
    daily_price: dict,
    common: list,
    signal: str,
    hold_days: int,
    costs_pct: float,
    direction_mode: str = "momentum",
    threshold_s: float | None = None,
    threshold_p: float | None = None,
    shift_thresh: float | None = None,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    size_pct: float = 100.0,
) -> dict | None:
    """Bucket _simulate_trades' output by each trade's entry-day regime so
    the per-regime net stats are visible.

    Returns {'bull': {...}, 'bear': {...}, 'chop': {...}} where each value is
    a stats dict like the `net` block of _compact_backtest_summary, or None
    if no trades fired in that regime.  Returns None overall if the window
    can't generate any trades at all (matches _compact_backtest_summary).
    """
    trades = _simulate_trades(
        daily_sentiment, daily_price, common, signal, hold_days,
        direction_mode=direction_mode,
        threshold_s=threshold_s, threshold_p=threshold_p, shift_thresh=shift_thresh,
        stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct, size_pct=size_pct,
    )
    if not trades:
        return None

    size_frac = max(0.0, min(size_pct, 100.0)) / 100.0
    costs_sized = costs_pct * size_frac

    buckets = {"bull": [], "bear": [], "chop": [], "unknown": []}
    for t in trades:
        buckets[t["regime"]].append(t["sized_return_pct"])

    def _stats(returns):
        if not returns:
            return None
        n = len(returns)
        winning = sum(1 for r in returns if r > 0)
        compounded = 100.0
        for r in returns:
            compounded *= (1 + (r - costs_sized) / 100)
        return {
            "trades": n,
            "win_rate": round(winning / n, 3),
            "avg_return_pct": round(sum(returns) / n, 2),
            "total_return_pct": round(compounded - 100, 2),
        }

    return {regime: _stats(rs) for regime, rs in buckets.items() if rs}


def _walk_forward(
    daily_sentiment: dict,
    daily_price: dict,
    common: list,
    signal: str,
    hold_days: int,
    costs_pct: float,
    direction_mode: str = "momentum",
    window_days: int = 45,
    step_days: int = 15,
    threshold_s: float | None = None,
    threshold_p: float | None = None,
    shift_thresh: float | None = None,
    stop_loss_pct: float | None = None,
    take_profit_pct: float | None = None,
    size_pct: float = 100.0,
) -> dict | None:
    """Slide a fixed-size test window through `common` and report per-fold net
    stats + stability summary.

    Unlike train/test ML walk-forward, we don't retrain anything: thresholds
    are static, we're measuring stability of a fixed rule across regimes.
    A flat-positive ribbon across folds is the credibility signal; alternating
    +30% / -25% folds means the edge depends on which window you start from.

    `window_days` must be ≥ 30 — anything shorter and the signal-detection
    14-day burn-in dominates.  `step_days` controls overlap; step < window
    means folds share data (typical), step ≥ window means disjoint.

    Returns None if we can't fit even one fold.
    """
    if len(common) < window_days:
        return None
    window_days = max(30, window_days)
    step_days   = max(1, step_days)

    folds = []
    fold_returns = []
    i = 0
    while i + window_days <= len(common):
        fold_window = common[i : i + window_days]
        fold = _compact_backtest_summary(
            daily_sentiment, daily_price, fold_window,
            signal, hold_days, costs_pct, direction_mode,
            threshold_s=threshold_s, threshold_p=threshold_p, shift_thresh=shift_thresh,
            stop_loss_pct=stop_loss_pct, take_profit_pct=take_profit_pct, size_pct=size_pct,
        )
        if fold is not None:
            folds.append({
                "start": str(fold_window[0]),
                "end":   str(fold_window[-1]),
                "net":   fold["net"],
                "gross": fold["gross"],
            })
            fold_returns.append(fold["net"]["total_return_pct"])
        else:
            # Track empty folds too — a strategy that fires nothing for 3
            # consecutive folds is also a stability signal.
            folds.append({
                "start": str(fold_window[0]),
                "end":   str(fold_window[-1]),
                "net":   None,
                "gross": None,
            })
        i += step_days

    if not fold_returns:
        return {"folds": folds, "stability": None}

    arr = np.array(fold_returns)
    pos_folds = int((arr > 0).sum())
    stability = {
        "fold_window_days": window_days,
        "fold_step_days":   step_days,
        "folds_total":      len(folds),
        "folds_with_trades": len(fold_returns),
        "folds_positive":   pos_folds,
        "folds_negative":   len(fold_returns) - pos_folds,
        "pct_folds_positive": round(pos_folds / len(fold_returns), 3) if fold_returns else 0,
        "mean_net_return_pct": round(float(arr.mean()), 2),
        "std_net_return_pct":  round(float(arr.std()), 2) if len(arr) >= 2 else 0.0,
        "best_fold_pct":  round(float(arr.max()), 2),
        "worst_fold_pct": round(float(arr.min()), 2),
    }
    return {"folds": folds, "stability": stability}


def _build_daily_series(headlines, prices):
    """Build the (daily_sentiment, daily_price, common_dates) triple used by
    every backtest variant.  Pulled out so admin-board doesn't redo the work
    three times per ticker."""
    daily = defaultdict(lambda: {"scores": [], "weights": []})
    for h in headlines:
        if abs(h.sentiment_score) < 0.05:
            continue
        d = h.published_at.date()
        daily[d]["scores"].append(h.sentiment_score)
        daily[d]["weights"].append(abs(h.sentiment_score))

    daily_sentiment = {}
    for d, v in daily.items():
        if not v["scores"]:
            continue
        w_sum = sum(v["weights"])
        daily_sentiment[d] = sum(s * w for s, w in zip(v["scores"], v["weights"])) / w_sum if w_sum else 0

    daily_price = {p.date.date(): p.close_price for p in prices}
    common = sorted(set(daily_sentiment.keys()) & set(daily_price.keys()))
    return daily_sentiment, daily_price, common


# ---------------------------------------------------------------------------
# Signal-quality refresh — writes the SignalQuality gate consulted by check_alerts
# ---------------------------------------------------------------------------
# Runs the same helpers as /admin/backtest-board but pinned to the production
# alert config (shift signal, 7d hold, momentum, default per-category costs).
# Writes one SignalQuality row per ticker.  Registered as a daily scheduler job
# so alerts always fire against evidence <=24h old, and re-run on startup if
# the table is empty (first deploy after this feature ships).
#
# Not exposed as an endpoint — admin can force a refresh via
# POST /admin/signal-quality/refresh (below), but the scheduler is the normal
# operator.  Deliberately sequential (not concurrent) — 42 tickers times ~1s
# each is under a minute and avoids DB session juggling.

def _run_signal_quality_refresh() -> dict:
    """Compute + upsert SignalQuality for every tracked ticker.

    Returns a summary dict with per-ticker outcomes so the admin endpoint can
    show what changed.  Callers get their own SessionLocal so the function can
    be invoked from both the scheduler thread and a FastAPI BackgroundTask.
    """
    from sqlalchemy import func as _f  # noqa: F401 — kept for parity with other refreshers
    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]

    # Production alert config, pinned.  Change here + on the alert side
    # together — mismatched configs would gate the wrong strategy.
    SIGNAL = "shift"
    HOLD_DAYS = 7
    DIRECTION_MODE = "momentum"

    since = datetime.utcnow() - timedelta(days=180)
    db = SessionLocal()
    outcomes = {}
    try:
        for t in all_tickers:
            try:
                costs_pct = _costs_pct_for(t, None)
                headlines = db.query(models.Headline).filter(
                    models.Headline.ticker == t,
                    models.Headline.published_at >= since,
                ).order_by(models.Headline.published_at).all()
                prices = db.query(models.Price).filter(
                    models.Price.ticker == t,
                    models.Price.date >= since,
                ).order_by(models.Price.date).all()

                if len(headlines) < 20 or len(prices) < 30:
                    _upsert_signal_quality(db, t, gate_ok=False,
                        reason=f"Insufficient data ({len(headlines)} headlines, {len(prices)} prices)",
                    )
                    outcomes[t] = "insufficient data"
                    continue

                daily_sentiment, daily_price, common = _build_daily_series(headlines, prices)
                if len(common) < 20:
                    _upsert_signal_quality(db, t, gate_ok=False,
                        reason=f"Only {len(common)} overlapping day-pairs (need 20)",
                    )
                    outcomes[t] = "insufficient overlap"
                    continue

                # 2/3 IS · 1/3 OOS — same split as the admin board.
                split = (len(common) * 2) // 3
                oos_window = common[split:]

                oos = _compact_backtest_summary(
                    daily_sentiment, daily_price, oos_window,
                    SIGNAL, HOLD_DAYS, costs_pct, DIRECTION_MODE,
                )
                full = _compact_backtest_summary(
                    daily_sentiment, daily_price, common,
                    SIGNAL, HOLD_DAYS, costs_pct, DIRECTION_MODE,
                )
                wf = _walk_forward(
                    daily_sentiment, daily_price, common,
                    SIGNAL, HOLD_DAYS, costs_pct, DIRECTION_MODE,
                )

                oos_net = oos["net"]["total_return_pct"] if oos else None
                wf_stab = wf.get("stability") if wf else None
                wf_pct  = wf_stab["pct_folds_positive"] if wf_stab else None

                # Both gates must clear.  Missing values = gate fails with the
                # specific missing-piece as the reason.
                if oos_net is None:
                    reason = "No OOS trades — strategy didn't fire in test window"
                    passed = False
                elif oos_net <= _SIGNAL_QUALITY_MIN_OOS_NET_PCT:
                    reason = f"OOS net {oos_net:+.2f}% ≤ {_SIGNAL_QUALITY_MIN_OOS_NET_PCT:+.2f}%"
                    passed = False
                elif wf_pct is None:
                    reason = "No walk-forward folds — window too short"
                    passed = False
                elif wf_pct < _SIGNAL_QUALITY_MIN_WF_POS_RATIO:
                    reason = f"WF positive ratio {wf_pct:.2f} < {_SIGNAL_QUALITY_MIN_WF_POS_RATIO:.2f}"
                    passed = False
                else:
                    reason = f"Passed (OOS net {oos_net:+.2f}%, WF {wf_stab['folds_positive']}/{wf_stab['folds_with_trades']})"
                    passed = True

                _upsert_signal_quality(
                    db, t,
                    gate_ok=passed,
                    oos_net_pct=oos_net,
                    wf_pct_folds_positive=wf_pct,
                    wf_folds_total=(wf_stab or {}).get("folds_with_trades"),
                    wf_folds_positive=(wf_stab or {}).get("folds_positive"),
                    n_trades_full=(full or {}).get("gross", {}).get("trades"),
                    reason=reason,
                )
                outcomes[t] = "pass" if passed else "fail"
            except Exception as e:
                # Never let one ticker crash the whole refresh — log + continue.
                # A ticker with no row keeps whatever it had; check_alerts will
                # still consult that (possibly stale) row until the next pass.
                db.rollback()
                outcomes[t] = f"error: {e}"
                print(f"[SIGNAL-QUALITY] {t}: error — {e}")
        db.commit()
    finally:
        db.close()

    passed = sum(1 for v in outcomes.values() if v == "pass")
    failed = sum(1 for v in outcomes.values() if v == "fail")
    print(f"[SIGNAL-QUALITY] refresh done — {passed} pass, {failed} fail, "
          f"{len(outcomes) - passed - failed} other")
    return {"passed": passed, "failed": failed, "outcomes": outcomes}


def _upsert_signal_quality(db: Session, ticker: str, **fields) -> None:
    """Insert-or-update the SignalQuality row for `ticker`."""
    row = db.query(models.SignalQuality).filter(
        models.SignalQuality.ticker == ticker
    ).first()
    fields.setdefault("computed_at", datetime.utcnow())
    if row is None:
        db.add(models.SignalQuality(ticker=ticker, **fields))
    else:
        for k, v in fields.items():
            setattr(row, k, v)


@app.get("/admin/backtest-board")
def admin_backtest_board(
    signal: str = "shift",
    hold_days: int = 7,
    costs_bps: int | None = None,
    direction_mode: str = "momentum",
    wf_window: int = 45,
    wf_step:   int = 15,
    refresh: bool = False,
    db: Session = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """Per-ticker backtest leaderboard with in-sample / out-of-sample split
    and transaction-cost-adjusted returns.

    For each tracked ticker we compute three backtest windows:
      • full  — all 180d of history we have
      • IS    — first 2/3 of common dates (the "developer's view")
      • OOS   — last 1/3 of common dates (what the thresholds never saw
                when they were originally chosen)

    For each window, we report both `gross` and `net` summary stats.  `net`
    subtracts a per-trade round-trip cost (default by asset category; override
    with ?costs_bps=N to stress-test).  The headline number to look at is
    `oos.net.total_return_pct` — if that's positive, the edge probably
    survives a regime change AND transaction costs.  If it's negative, the
    backtest looks great in-sample because the thresholds happened to suit
    that window, and the strategy is unlikely to make money in production.

    Default sort is `oos.net.total_return_pct` descending — the most honest
    number we can compute leads.  Tickers without enough OOS data fall back
    to gross full ranking so they still show up.

    `?costs_bps=N` overrides ALL categories' costs with a single N value;
    useful for sensitivity analysis ("what if my real fills are 50 bps RT?").

    `?direction_mode=momentum` (default) — long on bullish signals, current
    behaviour.  `?direction_mode=contrarian` flips to long-on-bearish (buy
    the panic).  Same P&L math, same cost model — only the entry trigger
    changes.  Crypto rows turning positive under contrarian would tell you
    sentiment is a fade indicator there, not a follow indicator.

    `?refresh=true` bypasses the 1-hour cache.  The cache is keyed on the
    full (signal, hold_days, costs_bps, direction_mode) tuple so toggling
    any one re-runs cleanly without nuking the others.
    """
    if signal not in ("shift", "divergence"):
        raise HTTPException(status_code=400, detail="signal must be 'shift' or 'divergence'")
    if direction_mode not in ("momentum", "contrarian"):
        raise HTTPException(status_code=400, detail="direction_mode must be 'momentum' or 'contrarian'")
    hold_days = max(1, min(hold_days, 30))
    if costs_bps is not None:
        costs_bps = max(0, min(costs_bps, 500))   # 5% RT is already absurd
    wf_window = max(30, min(wf_window, 180))      # any narrower and the 14d burn-in dominates
    wf_step   = max(5,  min(wf_step,    90))

    cache_key = (signal, hold_days, costs_bps, direction_mode, wf_window, wf_step)
    now = datetime.utcnow()
    cached = _BACKTEST_BOARD_CACHE
    if (not refresh
        and cached.get("key") == cache_key
        and cached.get("computed_at")
        and (now - cached["computed_at"]).total_seconds() < _BACKTEST_BOARD_TTL_SECONDS):
        return cached["data"]

    since = datetime.utcnow() - timedelta(days=180)
    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]
    rows = []

    for t in all_tickers:
        try:
            headlines = db.query(models.Headline).filter(
                models.Headline.ticker == t,
                models.Headline.published_at >= since,
            ).order_by(models.Headline.published_at).all()
            prices = db.query(models.Price).filter(
                models.Price.ticker == t,
                models.Price.date >= since,
            ).order_by(models.Price.date).all()

            if len(headlines) < 20 or len(prices) < 30:
                rows.append({
                    "ticker": t, "category": _category_for(t),
                    "full": None, "in_sample": None, "out_of_sample": None,
                    "costs_pct_per_trade": _costs_pct_for(t, costs_bps),
                    "message": "Not enough data",
                })
                continue

            daily_sentiment, daily_price, common = _build_daily_series(headlines, prices)
            if len(common) < 20:
                rows.append({
                    "ticker": t, "category": _category_for(t),
                    "full": None, "in_sample": None, "out_of_sample": None,
                    "costs_pct_per_trade": _costs_pct_for(t, costs_bps),
                    "message": "Not enough overlapping data",
                })
                continue

            # 2/3 IS, 1/3 OOS — conventional split.  We don't slide the boundary
            # because the thresholds are static, so any split point works the
            # same way.  For sliding-window stability, see `walk_forward` below.
            split = (len(common) * 2) // 3
            is_window  = common[:split]
            oos_window = common[split:]
            costs_pct  = _costs_pct_for(t, costs_bps)

            full_summary = _compact_backtest_summary(
                daily_sentiment, daily_price, common, signal, hold_days, costs_pct, direction_mode)
            is_summary   = _compact_backtest_summary(
                daily_sentiment, daily_price, is_window, signal, hold_days, costs_pct, direction_mode)
            oos_summary  = _compact_backtest_summary(
                daily_sentiment, daily_price, oos_window, signal, hold_days, costs_pct, direction_mode)
            by_regime    = _regime_split_stats(
                daily_sentiment, daily_price, common, signal, hold_days, costs_pct, direction_mode)
            walk_fwd     = _walk_forward(
                daily_sentiment, daily_price, common, signal, hold_days, costs_pct, direction_mode,
                window_days=wf_window, step_days=wf_step)

            rows.append({
                "ticker": t, "category": _category_for(t),
                "full": full_summary,
                "in_sample": is_summary,
                "out_of_sample": oos_summary,
                "by_regime":    by_regime,
                "walk_forward": walk_fwd,
                "costs_pct_per_trade": costs_pct,
                "window_days": (max(common) - min(common)).days if common else 0,
            })
        except Exception as e:
            rows.append({
                "ticker": t, "category": _category_for(t),
                "full": None, "in_sample": None, "out_of_sample": None,
                "error": str(e),
            })

    # Sort by OOS net total return desc — the most honest single number.
    # Tickers with no OOS sink to the bottom but stay visible so it's obvious
    # which assets are still data-starved.
    def _oos_net_key(r):
        try:    return r["out_of_sample"]["net"]["total_return_pct"]
        except (KeyError, TypeError): return None
    rows.sort(key=lambda r: (_oos_net_key(r) is None, -(_oos_net_key(r) or 0)))

    response = {
        "computed_at": now.isoformat() + "Z",
        "signal": signal,
        "hold_days": hold_days,
        "direction_mode": direction_mode,
        "costs_bps_override": costs_bps,
        "costs_defaults_by_category_bps": _TICKER_COSTS_BPS_DEFAULT,
        "split_ratio": "2/3 IS · 1/3 OOS",
        "walk_forward_params": {"window_days": wf_window, "step_days": wf_step},
        "regime_thresholds": {
            "lookback_days": _REGIME_LOOKBACK_DAYS,
            "bull_min_annualised": _REGIME_BULL_THRESHOLD,
            "bear_max_annualised": _REGIME_BEAR_THRESHOLD,
        },
        "rows": rows,
    }
    _BACKTEST_BOARD_CACHE.update({
        "key": cache_key, "computed_at": now, "data": response,
    })
    return response


@app.get("/admin/signal-quality")
def admin_signal_quality(
    db: Session = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """Inspect the current SignalQuality gate state per ticker.

    Returns every tracked ticker's most-recent snapshot: whether the gate
    passed, the OOS-net-per-cent and walk-forward positive-fold ratio it
    passed/failed on, and a human-readable `reason`.  Sorted with passing
    tickers first (by OOS-net descending) so the "why isn't my alert
    firing?" investigation goes fastest.

    Read-only — the daily scheduler writes.  Trigger a manual refresh via
    POST /admin/signal-quality/refresh if you need fresher numbers.
    """
    rows = db.query(models.SignalQuality).all()
    out = []
    for r in rows:
        out.append({
            "ticker": r.ticker,
            "gate_ok": bool(r.gate_ok),
            "oos_net_pct": r.oos_net_pct,
            "wf_pct_folds_positive": r.wf_pct_folds_positive,
            "wf_folds_total": r.wf_folds_total,
            "wf_folds_positive": r.wf_folds_positive,
            "n_trades_full": r.n_trades_full,
            "reason": r.reason,
            "computed_at": r.computed_at.isoformat() + "Z" if r.computed_at else None,
        })
    # Passing tickers first; among each group sort by OOS net desc so the
    # sharpest edges lead.  A gate_ok=True with oos_net_pct=None can't happen
    # (see the "No OOS trades" path in _run_signal_quality_refresh), but we
    # guard anyway to keep the sort deterministic if that ever changes.
    out.sort(key=lambda r: (
        not r["gate_ok"],
        -(r["oos_net_pct"] if r["oos_net_pct"] is not None else -1e9),
    ))
    return {
        "computed_at": datetime.utcnow().isoformat() + "Z",
        "gate_thresholds": {
            "min_oos_net_pct":       _SIGNAL_QUALITY_MIN_OOS_NET_PCT,
            "min_wf_pos_fold_ratio": _SIGNAL_QUALITY_MIN_WF_POS_RATIO,
        },
        "passing": sum(1 for r in out if r["gate_ok"]),
        "failing": sum(1 for r in out if not r["gate_ok"]),
        "rows": out,
    }


@app.post("/admin/signal-quality/refresh")
def admin_signal_quality_refresh(
    background_tasks: BackgroundTasks,
    admin=Depends(require_super_admin),
):
    """Force a synchronous recompute of every ticker's SignalQuality row.

    Queued as a BackgroundTask so the HTTP call returns immediately — the
    actual refresh takes 30-60s for all 42 tickers.  Log lines land in the
    Railway console under [SIGNAL-QUALITY].
    """
    background_tasks.add_task(_run_signal_quality_refresh)
    return {"message": "Signal-quality refresh queued — check logs for progress"}


@app.post("/admin/keys/mint-unlimited")
def admin_mint_unlimited_key(
    email: str,
    rotate: bool = False,
    db: Session = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """Mint (or upgrade) an unlimited API key for `email`.

    Behaviour:
      • No existing key for the email → generate a fresh one, mark unlimited,
        return the raw value (only chance to see it — we only store the hash).
      • Existing key + rotate=False → flip `unlimited=True` in place and return
        just the prefix.  The raw key can't be recovered — the owner should
        already have it.  Use this to grant unlimited to someone whose key is
        already deployed.
      • Existing key + rotate=True → rotate the raw value, mark unlimited,
        return the new raw value.  Old key stops working immediately.

    All variants: `active=True`, `stripe_customer_id/subscription_id` left
    untouched (so a paying user's Stripe link isn't nuked when they get a
    dogfood upgrade), `monthly_allowance` unchanged (irrelevant while
    unlimited is on, but preserved so removing the flag restores previous
    limits cleanly).

    Guarded by require_super_admin — Supabase JWT for an email on the
    ADMIN_EMAILS allow-list, same gate as the backtest board.
    """
    email = (email or "").strip().lower()
    if not email:
        raise HTTPException(status_code=400, detail="email is required")

    existing = db.query(models.APIKey).filter(models.APIKey.email == email).first()

    if existing is None:
        raw = _make_key()
        row = models.APIKey(
            email=email,
            key_hash=_hash_key(raw),
            key_prefix=raw[:12],   # sfx_ + first 8 hex chars
            free_calls=0,
            monthly_allowance=0,
            unlimited=True,
            active=True,
        )
        db.add(row)
        db.commit()
        return {
            "message": f"Minted new unlimited key for {email}",
            "email": email,
            "key": raw,               # only returned once — store it now
            "key_prefix": row.key_prefix,
            "unlimited": True,
            "note": "Save the `key` value — it is not recoverable from the DB.",
        }

    if not rotate:
        existing.unlimited = True
        existing.active = True
        db.commit()
        return {
            "message": f"Upgraded existing key for {email} to unlimited",
            "email": email,
            "key_prefix": existing.key_prefix,
            "unlimited": True,
            "note": (
                "Raw key value not returned — owner already has it. "
                "Pass ?rotate=true if they've lost it and need a new one."
            ),
        }

    # rotate=True: replace the raw value, mark unlimited, return the new raw
    raw = _make_key()
    existing.key_hash  = _hash_key(raw)
    existing.key_prefix = raw[:12]
    existing.unlimited = True
    existing.active    = True
    db.commit()
    return {
        "message": f"Rotated + marked unlimited for {email}",
        "email": email,
        "key": raw,
        "key_prefix": existing.key_prefix,
        "unlimited": True,
        "note": "Old key value has been invalidated. Save the new `key` — not recoverable.",
    }


@app.get("/admin/track-record")
def admin_track_record(
    days: int = 90,
    db: Session = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """Live track record of every alert that fired, vs. its eventual outcome.

    Aggregates the AlertOutcome table.  Unsettled rows (alerts where the hold
    period hasn't elapsed yet) are reported separately and excluded from
    return/win-rate math — including them would bias toward whatever the
    most-recent regime is.

    Powers the future public /track-record page; right now it's the admin
    answer to "is the trade-card strategy actually working in real life,
    not just in backtests?".  Backtest uses 180d of historical data —
    track record uses ONLY alerts that actually fired through the system.
    """
    days = max(7, min(days, 365))
    since = datetime.utcnow() - timedelta(days=days)

    outcomes = db.query(models.AlertOutcome).filter(
        models.AlertOutcome.fired_at >= since,
    ).order_by(models.AlertOutcome.fired_at.desc()).all()

    settled = [o for o in outcomes if o.settled and o.return_pct is not None]
    pending = [o for o in outcomes if not o.settled]

    def _aggregate(rows):
        """Win rate + compounded return + avg return for a slice of settled rows."""
        if not rows:
            return {"count": 0, "win_rate": None, "avg_return_pct": None, "total_return_pct": None}
        rets = [r.return_pct for r in rows]
        wins = sum(1 for r in rets if r > 0)
        compounded = 100.0
        for r in rets:
            compounded *= (1 + r / 100)
        return {
            "count": len(rows),
            "win_rate": round(wins / len(rows), 3),
            "avg_return_pct": round(sum(rets) / len(rets), 2),
            "total_return_pct": round(compounded - 100, 2),
        }

    by_direction = {
        "LONG":  _aggregate([o for o in settled if o.direction == "LONG"]),
        "SHORT": _aggregate([o for o in settled if o.direction == "SHORT"]),
    }
    by_confidence = {
        "high":   _aggregate([o for o in settled if o.confidence == "high"]),
        "medium": _aggregate([o for o in settled if o.confidence == "medium"]),
        "low":    _aggregate([o for o in settled if o.confidence == "low"]),
    }
    by_ticker = {}
    for o in settled:
        by_ticker.setdefault(o.ticker, []).append(o)
    by_ticker_agg = {t: _aggregate(rows) for t, rows in by_ticker.items()}

    # Recent outcomes table — for the admin UI to render a "last 25" list.
    recent = []
    for o in outcomes[:25]:
        recent.append({
            "id": o.id,
            "ticker": o.ticker,
            "direction": o.direction,
            "confidence": o.confidence,
            "hold_days": o.hold_days,
            "fired_at": o.fired_at.isoformat() + "Z" if o.fired_at else None,
            "entry_price": o.entry_price,
            "settled": o.settled,
            "exit_at": o.exit_at.isoformat() + "Z" if o.exit_at else None,
            "exit_price": o.exit_price,
            "return_pct": o.return_pct,
        })

    return {
        "window_days": days,
        "overall": _aggregate(settled),
        "pending_count": len(pending),
        "by_direction": by_direction,
        "by_confidence": by_confidence,
        "by_ticker": by_ticker_agg,
        "recent": recent,
    }


@app.get("/track-record")
def public_track_record(days: int = 90, db: Session = Depends(get_db)):
    """Public, no-auth version of the alert outcome track record.

    Same aggregation as /admin/track-record but the recent-trades list strips
    user_id / email / alert_id — these would leak who set which alert.  Designed
    as the conversion surface: a visitor lands on /track-record, sees the
    headline win rate and total return, and signs up.  Cached at the CDN via
    the public-data Cache-Control rule (added to _PUBLIC_GET_PATHS).

    Reuses the same window-clamp (7..365d) and the same "settled only counts"
    convention as the admin endpoint, so the two pages can never disagree on
    the headline number.
    """
    days = max(7, min(days, 365))
    since = datetime.utcnow() - timedelta(days=days)

    outcomes = db.query(models.AlertOutcome).filter(
        models.AlertOutcome.fired_at >= since,
    ).order_by(models.AlertOutcome.fired_at.desc()).all()

    settled = [o for o in outcomes if o.settled and o.return_pct is not None]
    pending = [o for o in outcomes if not o.settled]

    def _aggregate(rows):
        if not rows:
            return {"count": 0, "win_rate": None, "avg_return_pct": None, "total_return_pct": None}
        rets = [r.return_pct for r in rows]
        wins = sum(1 for r in rets if r > 0)
        compounded = 100.0
        for r in rets:
            compounded *= (1 + r / 100)
        return {
            "count": len(rows),
            "win_rate": round(wins / len(rows), 3),
            "avg_return_pct": round(sum(rets) / len(rets), 2),
            "total_return_pct": round(compounded - 100, 2),
        }

    by_direction = {
        "LONG":  _aggregate([o for o in settled if o.direction == "LONG"]),
        "SHORT": _aggregate([o for o in settled if o.direction == "SHORT"]),
    }
    by_confidence = {
        "high":   _aggregate([o for o in settled if o.confidence == "high"]),
        "medium": _aggregate([o for o in settled if o.confidence == "medium"]),
        "low":    _aggregate([o for o in settled if o.confidence == "low"]),
    }
    by_ticker_map = {}
    for o in settled:
        by_ticker_map.setdefault(o.ticker, []).append(o)
    # Sorted list (best win rate first) is more useful to a visitor than a dict
    # — they want to see "which ticker is this working on?".  Tie-break by sample
    # size so a 1-of-1 100% ticker doesn't lead over a 7-of-10.
    by_ticker_rows = sorted(
        ({"ticker": t, "category": _category_for(t), **_aggregate(rows)} for t, rows in by_ticker_map.items()),
        key=lambda r: (r["win_rate"] is None, -(r["win_rate"] or 0), -(r["count"] or 0)),
    )

    # Recent 25 settled trades — public-safe fields only.  Unsettled rows are
    # excluded; the still-open count is exposed separately so the page can show
    # "N trades currently open" without leaking individual entries.
    recent_public = [
        {
            "ticker": o.ticker,
            "direction": o.direction,
            "confidence": o.confidence,
            "hold_days": o.hold_days,
            "fired_at": o.fired_at.isoformat() + "Z" if o.fired_at else None,
            "exit_at": o.exit_at.isoformat() + "Z" if o.exit_at else None,
            "entry_price": o.entry_price,
            "exit_price": o.exit_price,
            "return_pct": o.return_pct,
        }
        for o in outcomes if o.settled and o.return_pct is not None
    ][:25]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": days,
        "overall": _aggregate(settled),
        "pending_count": len(pending),
        "by_direction": by_direction,
        "by_confidence": by_confidence,
        "by_ticker": by_ticker_rows,
        "recent": recent_public,
    }


@app.get("/headline-impact/{ticker}")
def get_headline_impact(ticker: str, days: int = 90, limit: int = 20, db: Session = Depends(get_db)):
    days = max(7, min(days, 365))
    limit = max(5, min(limit, 100))
    since = datetime.utcnow() - timedelta(days=days + 2)  # +2 to ensure next-day price exists

    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper(),
        models.Headline.published_at >= since,
        models.Headline.sentiment_score.isnot(None)
    ).order_by(models.Headline.published_at).all()

    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper(),
        models.Price.date >= since
    ).order_by(models.Price.date).all()

    if not headlines or len(prices) < 2:
        return {"ticker": ticker.upper(), "days": days, "total_analysed": 0, "headlines": [], "summary": None}

    price_by_date = {p.date.date(): p.close_price for p in prices}
    sorted_price_dates = sorted(price_by_date.keys())

    # Precompute next-day return for each price date
    daily_return = {}
    for i in range(len(sorted_price_dates) - 1):
        d = sorted_price_dates[i]
        nd = sorted_price_dates[i + 1]
        prev = price_by_date[d]
        if prev and prev > 0:
            daily_return[d] = round((price_by_date[nd] - prev) / prev * 100, 2)

    results = []
    for h in headlines:
        if abs(h.sentiment_score) < 0.1:
            continue
        h_date = h.published_at.date()

        # Find the most recent price date on or before this headline
        price_date = max((d for d in price_by_date if d <= h_date), default=None)
        if price_date is None or price_date not in daily_return:
            continue

        next_ret = daily_return[price_date]
        if abs(next_ret) < 0.5:
            continue

        impact = abs(h.sentiment_score) * abs(next_ret)

        sent_positive = h.sentiment_score > 0
        price_up = next_ret > 0
        alignment = "confirmed" if sent_positive == price_up else "contrarian"

        results.append({
            "id": h.id,
            "title": h.title,
            "source": h.source,
            "url": h.url,
            "published_at": h.published_at.isoformat(),
            "sentiment_score": round(h.sentiment_score, 3),
            "sentiment_label": h.sentiment_label,
            "next_day_return_pct": next_ret,
            "impact_score": round(impact, 3),
            "alignment": alignment,
        })

    results.sort(key=lambda x: x["impact_score"], reverse=True)
    top = results[:limit]

    confirmed = sum(1 for r in top if r["alignment"] == "confirmed")
    contrarian = len(top) - confirmed
    confirmed_pct = round(confirmed / len(top) * 100) if top else None

    return {
        "ticker": ticker.upper(),
        "days": days,
        "total_analysed": len(results),
        "headlines": top,
        "summary": {
            "confirmed": confirmed,
            "contrarian": contrarian,
            "confirmed_pct": confirmed_pct,
        } if top else None,
    }


@app.get("/sentiment-summary/{ticker}")
def get_sentiment_summary(ticker: str, days: int = 90, all: bool = False, db: Session = Depends(get_db)):
    query = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper()
    )
    if not all:
        since = datetime.utcnow() - timedelta(days=days)
        query = query.filter(models.Headline.published_at >= since)

    headlines = query.all()

    by_date = {}
    for h in headlines:
        date = str(h.published_at.date())
        if date not in by_date:
            by_date[date] = []
        if abs(h.sentiment_score) > 0.05:
            by_date[date].append(h.sentiment_score)

    return {
        "ticker": ticker.upper(),
        "data": [
            {
                "date": date,
                "avg_sentiment": round(sum(scores) / len(scores), 3),
                "count": len(scores)
            }
            for date, scores in sorted(by_date.items())
            if len(scores) > 0
        ]
    }

def _resolve_brief_tier(authorization: str | None) -> str:
    """Soft auth: look at the Bearer JWT if present and return the user's tier.
    Returns 'free' on any failure — these endpoints are public, auth just
    upgrades what's returned.  Failures here MUST NOT raise."""
    if not authorization or not authorization.startswith("Bearer "):
        return "free"
    try:
        from supabase import create_client
        sb = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
        user = sb.auth.get_user(authorization.split(" ", 1)[1]).user
        if not user:
            return "free"
        profile = sb.table("profiles").select("tier").eq("id", user.id).single().execute()
        return (profile.data or {}).get("tier") or "free"
    except Exception:
        return "free"


def _serialize_brief(brief: "models.Brief", tier: str) -> dict:
    """Render a Brief row for the API, gating content by tier.

    Free / anonymous users see: date, ai_summary, list of tickers covered.
    That's enough to be useful as a teaser and to drive SEO; the full
    per-ticker breakdown stays gated so subscribers get something real.

    Paid (brief/pro/data): full per-ticker data plus the rendered HTML so
    the public page can show exactly what subscribers got by email.
    """
    out = {
        "date": brief.date.date().isoformat(),
        "ai_summary": brief.ai_summary,
        "tickers": (brief.tickers or "").split(",") if brief.tickers else [],
        "sent_count": brief.sent_count,
        "is_paywalled": tier not in _BRIEF_FULL_ACCESS_TIERS,
    }
    if tier in _BRIEF_FULL_ACCESS_TIERS:
        try:
            out["ticker_data"] = json.loads(brief.ticker_data) if brief.ticker_data else []
        except Exception:
            out["ticker_data"] = []
        out["content_html"] = brief.content_html
    return out


@app.get("/brief/archive")
def brief_archive(limit: int = 30, db: Session = Depends(get_db)):
    """Public list of past briefs (date + AI summary preview).  Always fully
    open — this is the SEO funnel and a "see what you're missing" surface
    for free visitors.  No per-ticker data here; that's the paid product."""
    limit = max(1, min(limit, 90))
    briefs = db.query(models.Brief).order_by(models.Brief.date.desc()).limit(limit).all()
    return {
        "briefs": [{
            "date": b.date.date().isoformat(),
            "ai_summary": b.ai_summary,
            "tickers": (b.tickers or "").split(",") if b.tickers else [],
        } for b in briefs],
    }


@app.get("/brief/latest")
def brief_latest(authorization: str | None = Header(None), db: Session = Depends(get_db)):
    """Today's brief, or the most recent one if today's hasn't generated yet.
    Tier-gated content per _serialize_brief.  Optional auth — Bearer JWT
    upgrades the response, no auth = free preview."""
    brief = db.query(models.Brief).order_by(models.Brief.date.desc()).first()
    if not brief:
        raise HTTPException(status_code=404, detail="No briefs yet")
    return _serialize_brief(brief, _resolve_brief_tier(authorization))


@app.get("/brief/{date}")
def brief_by_date(
    date: str,
    authorization: str | None = Header(None),
    db: Session = Depends(get_db),
):
    """Brief for a specific UTC date (YYYY-MM-DD).  Same gating as latest."""
    try:
        target = datetime.strptime(date, "%Y-%m-%d").replace(hour=0, minute=0, second=0, microsecond=0)
    except ValueError:
        raise HTTPException(status_code=400, detail="date must be YYYY-MM-DD")
    brief = db.query(models.Brief).filter(models.Brief.date == target).first()
    if not brief:
        raise HTTPException(status_code=404, detail="No brief for that date")
    return _serialize_brief(brief, _resolve_brief_tier(authorization))


@app.post("/api/brief/test")
def test_brief(email: str, db: Session = Depends(get_db), admin=Depends(require_admin)):
    from app.brief import fetch_ticker_data, generate_ai_summary, build_email_html, TICKERS
    import resend as resend_lib
    resend_lib.api_key = os.environ["RESEND_API_KEY"]
    ticker_data = []
    seen_urls = set()
    for ticker in TICKERS:
        data = fetch_ticker_data(db, ticker)
        if data.get("top_headline"):
            url = data["top_headline"]["url"]
            if url in seen_urls:
                data["top_headline"] = None
            else:
                seen_urls.add(url)
        ticker_data.append(data)
    ai_summary = generate_ai_summary(ticker_data)
    html = build_email_html(ticker_data, ai_summary, "https://api.sentimentfx.org/api/brief/unsubscribe?user_id=test")
    resend_lib.Emails.send({
        "from": "SentimentFX <hello@sentimentfx.org>",
        "to": email,
        "subject": f"[TEST] Morning Brief · {datetime.utcnow().strftime('%d %b')}",
        "html": html,
    })
    return {"message": f"Test brief sent to {email}"}


@app.post("/api/brief/unsubscribe")
def unsubscribe_brief(user_id: str, db: Session = Depends(get_db)):
    supabase = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])
    supabase.from_("profiles").update({"morning_brief_enabled": False}).eq("id", user_id).execute()
    return {"message": "Unsubscribed from morning brief."}


async def _get_user_from_token(authorization: str):
    """Validate Bearer token and return Supabase user."""
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing authorization token")
    token = authorization.split(" ")[1]
    from supabase import create_client
    supabase_client = create_client(os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_SERVICE_KEY"))
    user_resp = supabase_client.auth.get_user(token)
    user = user_resp.user
    if not user:
        raise HTTPException(status_code=401, detail="Invalid token")
    return user, supabase_client


@app.post("/billing-portal")
async def billing_portal(authorization: str = Header(None)):
    try:
        user, _ = await _get_user_from_token(authorization)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Auth error: {str(e)}")

    customers = stripe.Customer.list(email=user.email, limit=1)
    if not customers.data:
        raise HTTPException(status_code=404, detail="No billing account found")

    portal = stripe.billing_portal.Session.create(
        customer=customers.data[0].id,
        return_url="https://app.sentimentfx.org",
    )
    return {"url": portal.url}


@app.patch("/profile/brief")
async def update_brief_preference(request: Request, authorization: str = Header(None)):
    try:
        user, supabase_client = await _get_user_from_token(authorization)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Auth error: {str(e)}")

    body = await request.json()
    enabled = body.get("enabled")
    if not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="'enabled' must be a boolean")

    supabase_client.table("profiles").update({"morning_brief_enabled": enabled}).eq("id", user.id).execute()
    return {"morning_brief_enabled": enabled}


@app.delete("/account")
async def delete_account(authorization: str = Header(None), db: Session = Depends(get_db)):
    try:
        user, supabase_client = await _get_user_from_token(authorization)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Auth error: {str(e)}")

    # Cancel active Stripe subscription
    try:
        customers = stripe.Customer.list(email=user.email, limit=1)
        if customers.data:
            subs = stripe.Subscription.list(customer=customers.data[0].id, status="active", limit=10)
            for sub in subs.data:
                stripe.Subscription.cancel(sub.id)
    except Exception as e:
        print(f"Stripe cancel error during account deletion: {e}")

    # Delete alerts from DB
    db.query(models.Alert).filter(models.Alert.user_id == user.id).delete()

    # Deactivate API key
    api_key_row = db.query(models.APIKey).filter(models.APIKey.email == user.email).first()
    if api_key_row:
        api_key_row.active = False

    db.commit()

    # Delete Supabase auth user
    supabase_client.auth.admin.delete_user(user.id)

    return {"message": "Account deleted"}


# ---------------------------------------------------------------------------
# MCP server mount
# ---------------------------------------------------------------------------
# The MCP server exposes the same read endpoints as /v1/* but as Model Context
# Protocol tools, so partners can wire SentimentFX straight into Claude Code /
# Claude.ai desktop / Claude API apps / Cursor / Cline as native functions.
#
# Mounted at /mcp on the existing FastAPI app so it shares TLS, DNS, and the
# Railway deploy — no separate service.  Auth reuses the X-API-Key header +
# APIKey table + track_usage() billing hook (see mcp_server.py for detail),
# so partner usage bills identically whether they call /v1/* over HTTP or
# invoke the MCP tools through Claude.
#
# Late import: mcp_server imports back into main.py for `_hash_key` +
# `track_usage`, so this line has to sit at the end of main.py — after every
# name mcp_server dereferences at call-time has been defined.
from .mcp_server import mcp as _mcp_server  # noqa: E402

# streamable-http is the modern MCP transport (superseded SSE mid-2025).  The
# returned Starlette sub-app handles the /mcp POST endpoint clients hit for
# every tool call; FastMCP takes care of the JSON-RPC framing internally.
app.mount("/mcp", _mcp_server.streamable_http_app())