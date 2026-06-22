from fastapi import FastAPI, Depends, HTTPException, Header, BackgroundTasks
from sqlalchemy.orm import Session
from fastapi import Request
from . import models, schemas
from .database import engine, get_db
from .scraper import fetch_headlines, fetch_rss_headlines, BACKGROUND_TICKERS, fetch_background_headlines, fetch_gdelt_headlines, GDELT_QUERIES
from .sentiment import analyse_sentiment
from .prices import fetch_prices, fetch_latest_price, fetch_latest_prices_all, fetch_latest_stock_price
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
from collections import defaultdict
from prometheus_fastapi_instrumentator import Instrumentator
from prometheus_client import Counter, Gauge, Histogram
import numpy as np
import resend
import os
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

def get_api_key_value(request: Request) -> str:
    """Rate limit by API key header, fall back to IP."""
    return request.headers.get("x-api-key") or get_remote_address(request)

limiter = Limiter(key_func=get_api_key_value)

app = FastAPI()

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
_PUBLIC_GET_PATHS = {"/", "/status", "/health", "/leaderboard"}
_PUBLIC_GET_DATA_PREFIXES = ("/dashboard/", "/correlation/", "/headlines/", "/prices/", "/summary/")

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


@app.exception_handler(RateLimitExceeded)
async def rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": f"Rate limit exceeded. {exc.detail}"}
    )

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
PRO_MONTHLY_ALLOWANCE = 1000
DATA_MONTHLY_ALLOWANCE = 5000


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
                db.commit()
            except Exception as e:
                print(f"Alert email error: {e}")


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
                    existing.close_price = prices["close_price"]
                    existing.volume = prices["volume"]
                else:
                    price = models.Price(
                        ticker=prices["ticker"],
                        close_price=prices["close_price"],
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
                else:
                    db.add(models.Price(**price_data))

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
scheduler.add_job(refresh_subscription_gauge, CronTrigger(minute=30))
scheduler.add_job(reset_monthly_api_usage, CronTrigger(day=1, hour=0, minute=0, timezone="UTC"))
scheduler.start()


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
        else:
            db.add(models.Price(
                ticker=prices["ticker"],
                close_price=prices["close_price"],
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


@app.get("/dashboard/{ticker}")
def get_dashboard(ticker: str, days: int = 90, all: bool = False, page: int = 1, limit: int = 50, db: Session = Depends(get_db)):
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
                "title": h.title
            } for h in headlines
        ],
        "prices": [
            {
                "date": p.date,
                "close_price": p.close_price,
                "volume": p.volume
            } for p in prices
        ]
    }


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

    # Plain-English summary
    sign = "+" if today_shift > 0 else ""
    summary = f"{ticker.upper()} sentiment is {sentiment_label} ({sign}{round(today_sentiment, 3)}) "
    summary += f"with a {magnitude} {'upward' if shift_direction == 'up' else 'downward'} shift "
    summary += f"of {sign}{round(today_shift, 3)} vs the 7-day average "
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

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["date", "ticker", "close_price_gbp", "volume"])
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


def _run_gdelt_backfill(ticker_list: list, days: int, windows_per_day: int = 4):
    db = SessionLocal()
    summary = {}
    try:
        for ticker in ticker_list:
            try:
                headlines = fetch_gdelt_headlines(ticker, days=days, windows_per_day=windows_per_day)
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
                    HEADLINES_INGESTED.labels(source="gdelt", ticker=h["ticker"]).inc()
                    saved += 1
                db.commit()
                summary[ticker] = saved
                print(f"[GDELT-BACKFILL] {ticker}: +{saved} new of {len(headlines)} fetched")
            except Exception as e:
                db.rollback()
                summary[ticker] = f"error: {e}"
                print(f"[GDELT-BACKFILL] {ticker} error: {e}")
    finally:
        db.close()
    total = sum(v for v in summary.values() if isinstance(v, int))
    print(f"[GDELT-BACKFILL] done — +{total} new headlines across {len(summary)} tickers")


@app.post("/backfill/gdelt")
@app.post("/backfill")   # legacy alias — GNews-backed backfill is gone, GDELT is the only path now
def backfill_gdelt(
    background_tasks: BackgroundTasks,
    tickers: str = "all",
    days: int = 30,
    windows_per_day: int = 4,
    admin=Depends(require_admin),
):
    if tickers.lower() == "all":
        ticker_list = list(GDELT_QUERIES.keys())
    else:
        ticker_list = [t.strip().upper() for t in tickers.split(",") if t.strip()]

    unknown = [t for t in ticker_list if t not in GDELT_QUERIES]
    if unknown:
        raise HTTPException(status_code=404, detail=f"Unknown tickers: {','.join(unknown)}")

    windows_per_day = max(1, min(windows_per_day, 24))
    background_tasks.add_task(_run_gdelt_backfill, ticker_list, days, windows_per_day)
    return {
        "message": f"GDELT backfill queued for {len(ticker_list)} ticker(s) over {days} days "
                   f"({windows_per_day} windows/day) — check Railway logs for progress",
        "tickers": ticker_list,
        "days": days,
        "windows_per_day": windows_per_day,
    }


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
    tier = "data" if price_id in DATA_PRICE_IDS else "pro"
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
            allowance = DATA_MONTHLY_ALLOWANCE if tier == "data" else PRO_MONTHLY_ALLOWANCE
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
        key=key,  # kept for safety during transition - remove after dropping column
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
    existing.key = new_key  # kept for safety during transition - remove after dropping column
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
        key=key,
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
    existing.key = new_key
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

@app.get("/v1/sentiment/{ticker}", summary="Get latest sentiment", description="Returns the latest FinBERT-scored headlines for a given ticker. Use `limit` to control how many results are returned (max 100). Each call costs 1 API credit per 25 headlines.")
@limiter.limit("30/minute")
def api_sentiment(request: Request, ticker: str, limit: int = 25, db: Session = Depends(get_db), api_key=Depends(get_api_key)):
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
def api_summary(request: Request, ticker: str, days: int = 30, db: Session = Depends(get_db), api_key=Depends(get_api_key)):
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


@app.get("/v1/prices/{ticker}", summary="Get historical prices", description="Returns daily close prices in GBP for a given ticker over the specified number of days. Each day costs 1 API credit.")
@limiter.limit("20/minute")
def api_prices(request: Request, ticker: str, days: int = 30, db: Session = Depends(get_db), api_key=Depends(get_api_key)):
    track_usage(api_key, db, days, endpoint="prices")

    since = datetime.utcnow() - timedelta(days=days)
    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper(),
        models.Price.date >= since
    ).order_by(models.Price.date.desc()).all()

    if not prices:
        raise HTTPException(status_code=404, detail="No data found")

    return {
        "ticker": ticker.upper(),
        "days": days,
        "calls_used": days,
        "data": [
            {
                "date": p.date,
                "close_price_gbp": p.close_price,
                "volume": p.volume,
            } for p in prices
        ]
    }


@app.get("/v1/correlation/{ticker}", summary="Get sentiment-price correlation", description="Returns a 180-day Pearson correlation analysis between sentiment shifts and next-day price returns, including signal strength, direction, and 95% confidence interval. Costs 1 API credit.")
@limiter.limit("10/minute")
def api_correlation(request: Request, ticker: str, db: Session = Depends(get_db), api_key=Depends(get_api_key)):
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
def health():
    return {"status": "ok"}

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
    existing.key = new_key
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

    if divergence_type == "bullish":
        summary = (
            f"{ticker.upper()} sentiment is rising ({sign_s}{round(sentiment_change, 3)}) "
            f"while price is falling ({sign_p}{round(price_change_pct, 1)}%) over the last 7 days. "
            f"Bullish divergence — narrative improvement has not yet been reflected in price."
        )
    elif divergence_type == "bearish":
        summary = (
            f"{ticker.upper()} sentiment is falling ({sign_s}{round(sentiment_change, 3)}) "
            f"while price is rising ({sign_p}{round(price_change_pct, 1)}%) over the last 7 days. "
            f"Bearish divergence — price is rising against deteriorating sentiment."
        )
    else:
        summary = (
            f"{ticker.upper()} sentiment ({sign_s}{round(sentiment_change, 3)}) and price "
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
def get_backtest(ticker: str, signal: str = "divergence", hold_days: int = 7, db: Session = Depends(get_db)):
    hold_days = max(1, min(hold_days, 30))
    if signal not in ("divergence", "shift"):
        raise HTTPException(status_code=400, detail="signal must be 'divergence' or 'shift'")

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

    daily_bucket = defaultdict(lambda: {"scores": [], "weights": []})
    for h in headlines:
        if abs(h.sentiment_score) < 0.05:
            continue
        d = h.published_at.date()
        daily_bucket[d]["scores"].append(h.sentiment_score)
        daily_bucket[d]["weights"].append(abs(h.sentiment_score))

    daily_sentiment = {}
    for d, v in daily_bucket.items():
        if not v["scores"]:
            continue
        w_sum = sum(v["weights"])
        daily_sentiment[d] = sum(s * w for s, w in zip(v["scores"], v["weights"])) / w_sum if w_sum else 0

    daily_price = {p.date.date(): p.close_price for p in prices}
    sorted_price_dates = sorted(daily_price.keys())

    common = sorted(set(daily_sentiment.keys()) & set(daily_price.keys()))
    if len(common) < 20:
        return {"message": "Not enough overlapping data", "trades": [], "equity_curve": []}

    THRESHOLD_S, THRESHOLD_P = 0.02, 0.5

    def _div_signal(window):
        if len(window) < 14:
            return "none"
        p7, r7 = window[:7], window[7:]
        ps = sum(daily_sentiment[d] for d in p7) / 7
        rs = sum(daily_sentiment[d] for d in r7) / 7
        pp = sum(daily_price[d] for d in p7) / 7
        rp = sum(daily_price[d] for d in r7) / 7
        sc = rs - ps
        pc = (rp - pp) / pp * 100 if pp > 0 else 0
        if sc > THRESHOLD_S and pc < -THRESHOLD_P:
            return "bullish"
        if sc < -THRESHOLD_S and pc > THRESHOLD_P:
            return "bearish"
        return "none"

    signal_series = {}
    if signal == "divergence":
        for i in range(14, len(common) + 1):
            signal_series[common[i - 1]] = _div_signal(common[i - 14:i])
    else:
        SHIFT_THRESH = 0.05
        for i, d in enumerate(common):
            prior = [daily_sentiment[common[j]] for j in range(max(0, i - 7), i)]
            if len(prior) >= 3:
                shift = daily_sentiment[d] - sum(prior) / len(prior)
                if shift > SHIFT_THRESH:
                    signal_series[d] = "bullish"
                elif shift < -SHIFT_THRESH:
                    signal_series[d] = "bearish"
                else:
                    signal_series[d] = "none"

    # Simulate long-only trades, no overlap
    trades = []
    in_trade_until = None

    for d in sorted(signal_series.keys()):
        if in_trade_until and d <= in_trade_until:
            continue
        if signal_series[d] != "bullish":
            continue

        entry_date = next((pd for pd in sorted_price_dates if pd > d), None)
        if not entry_date:
            continue
        entry_price = daily_price[entry_date]

        target = entry_date + timedelta(days=hold_days)
        exit_date = next((pd for pd in sorted_price_dates if pd >= target), sorted_price_dates[-1])
        exit_price = daily_price[exit_date]

        ret = (exit_price - entry_price) / entry_price * 100
        trades.append({
            "entry_date": str(entry_date),
            "exit_date": str(exit_date),
            "entry_price": round(entry_price, 2),
            "exit_price": round(exit_price, 2),
            "return_pct": round(ret, 2),
        })
        in_trade_until = exit_date

    if not trades:
        return {
            "ticker": ticker.upper(), "signal": signal, "hold_days": hold_days,
            "message": "No trades generated — signal did not fire in this window.",
            "trades": [], "equity_curve": [],
        }

    returns = [t["return_pct"] for t in trades]
    winning = [r for r in returns if r > 0]

    # Compounded total return
    pv = 100.0
    for r in returns:
        pv *= (1 + r / 100)
    total_return = round(pv - 100, 2)

    # Max drawdown
    peak, running, max_dd = 100.0, 100.0, 0.0
    for r in returns:
        running *= (1 + r / 100)
        peak = max(peak, running)
        max_dd = min(max_dd, (running - peak) / peak * 100)

    first_price = daily_price[sorted_price_dates[0]]
    last_price = daily_price[sorted_price_dates[-1]]
    buy_hold = round((last_price - first_price) / first_price * 100, 2)
    alpha = round(total_return - buy_hold, 2)

    r_arr = np.array(returns)
    sharpe = None
    if len(returns) >= 3 and np.std(r_arr) > 0:
        sharpe = round(float(np.mean(r_arr) / np.std(r_arr) * np.sqrt(252 / hold_days)), 2)

    # Daily equity curve: portfolio tracks price during active trades
    portfolio_cash = 100.0
    pending = None  # (exit_date, exit_price, portfolio_at_entry, entry_price)
    trade_by_entry = {
        datetime.strptime(t["entry_date"], "%Y-%m-%d").date(): (
            datetime.strptime(t["exit_date"], "%Y-%m-%d").date(),
            t["exit_price"]
        )
        for t in trades
    }

    equity_curve = []
    for d in sorted_price_dates:
        price = daily_price[d]

        if pending and d >= pending[0]:
            portfolio_cash = pending[2] * (pending[1] / pending[3])
            pending = None

        if d in trade_by_entry and pending is None:
            xd, xp = trade_by_entry[d]
            pending = (xd, xp, portfolio_cash, price)

        current = pending[2] * (price / pending[3]) if pending else portfolio_cash
        equity_curve.append({
            "date": str(d),
            "portfolio": round(current, 2),
            "buy_hold": round(100.0 * price / first_price, 2),
        })

    return {
        "ticker": ticker.upper(),
        "signal": signal,
        "hold_days": hold_days,
        "window_days": (sorted_price_dates[-1] - sorted_price_dates[0]).days,
        "summary": {
            "total_trades": len(trades),
            "winning_trades": len(winning),
            "win_rate": round(len(winning) / len(returns), 3),
            "avg_return_pct": round(float(np.mean(r_arr)), 2),
            "total_return_pct": total_return,
            "max_drawdown_pct": round(max_dd, 2),
            "buy_hold_return_pct": buy_hold,
            "alpha_pct": alpha,
            "sharpe": sharpe,
        },
        "trades": trades,
        "equity_curve": equity_curve,
    }


# In-memory cache for the admin backtest board.  Computing all 42 tickers
# synchronously takes ~13s on Railway (180d headlines + 180d prices per
# ticker, then trade simulation).  Results don't change minute-to-minute
# so a 1h TTL is fine.  Cache is process-local; survives within a Railway
# instance but resets on redeploy — acceptable for an admin tool.
_BACKTEST_BOARD_CACHE = {"key": None, "computed_at": None, "data": None}
_BACKTEST_BOARD_TTL_SECONDS = 3600


@app.get("/admin/backtest-board")
def admin_backtest_board(
    signal: str = "shift",
    hold_days: int = 7,
    refresh: bool = False,
    db: Session = Depends(get_db),
    admin=Depends(require_super_admin),
):
    """Per-ticker backtest leaderboard — which tickers' signals actually work.

    Runs the same backtest as GET /backtest/{ticker} for every tracked ticker
    and returns only the summary stats (no trades, no equity curve) so the
    admin can rank by historical edge.  Default sort is total_return_pct
    descending.  Calling with ?refresh=true bypasses the 1-hour cache.

    Tickers without enough historical data return null metrics and
    total_trades=0 — the UI sinks them to the bottom rather than dropping
    them, so it's obvious which assets still need data accumulation.
    """
    if signal not in ("shift", "divergence"):
        raise HTTPException(status_code=400, detail="signal must be 'shift' or 'divergence'")
    hold_days = max(1, min(hold_days, 30))

    cache_key = (signal, hold_days)
    now = datetime.utcnow()
    cached = _BACKTEST_BOARD_CACHE
    if (not refresh
        and cached.get("key") == cache_key
        and cached.get("computed_at")
        and (now - cached["computed_at"]).total_seconds() < _BACKTEST_BOARD_TTL_SECONDS):
        return cached["data"]

    all_tickers = list(TICKERS) + [t for t in BACKGROUND_TICKERS if t not in TICKERS]
    rows = []
    for t in all_tickers:
        try:
            # Call the existing route function directly — the Depends(get_db)
            # default is just a sentinel; passing our own db overrides it.
            result = get_backtest(t, signal=signal, hold_days=hold_days, db=db)
        except Exception as e:
            rows.append({
                "ticker": t, "category": _category_for(t),
                "win_rate": None, "avg_return_pct": None,
                "total_return_pct": None, "alpha_pct": None,
                "sharpe": None, "max_drawdown_pct": None,
                "buy_hold_return_pct": None, "total_trades": 0,
                "error": str(e),
            })
            continue

        if "summary" not in result:
            rows.append({
                "ticker": t, "category": _category_for(t),
                "win_rate": None, "avg_return_pct": None,
                "total_return_pct": None, "alpha_pct": None,
                "sharpe": None, "max_drawdown_pct": None,
                "buy_hold_return_pct": None, "total_trades": 0,
                "message": result.get("message"),
            })
            continue

        s = result["summary"]
        rows.append({
            "ticker": t,
            "category": _category_for(t),
            "win_rate": s["win_rate"],
            "avg_return_pct": s["avg_return_pct"],
            "total_return_pct": s["total_return_pct"],
            "alpha_pct": s["alpha_pct"],
            "sharpe": s["sharpe"],
            "max_drawdown_pct": s["max_drawdown_pct"],
            "buy_hold_return_pct": s["buy_hold_return_pct"],
            "total_trades": s["total_trades"],
        })

    # Sort: tickers with no backtest data sink to the bottom; rest by total
    # return desc.  Using total_return_pct (compounded) not avg_return_pct
    # because it accounts for trade frequency — a 60% win rate that fires
    # 5x means more than the same win rate firing twice.
    rows.sort(key=lambda r: (
        r.get("total_return_pct") is None,
        -(r.get("total_return_pct") or 0),
    ))

    response = {
        "computed_at": now.isoformat() + "Z",
        "signal": signal,
        "hold_days": hold_days,
        "rows": rows,
    }
    _BACKTEST_BOARD_CACHE.update({
        "key": cache_key, "computed_at": now, "data": response,
    })
    return response


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
        "subject": f"[TEST] Morning Brief — {datetime.utcnow().strftime('%d %b')}",
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