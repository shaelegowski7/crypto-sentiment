from fastapi import FastAPI, Depends, HTTPException, Header, BackgroundTasks
from sqlalchemy.orm import Session
from fastapi import Request
from . import models, schemas
from .database import engine, get_db
from .scraper import fetch_headlines, fetch_rss_headlines
from .sentiment import analyse_sentiment
from .prices import fetch_prices, fetch_latest_price
from datetime import datetime, timedelta
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from . import models
import math
from .database import SessionLocal
from scipy.stats import pearsonr
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from fastapi.responses import JSONResponse
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

TICKERS = ["BTC", "ETH", "SOL", "XRP", "DOGE"]


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
            try:
                resend.api_key = os.getenv("RESEND_API_KEY")
                resend.Emails.send({
                    "from": "SentimentFX <hello@sentimentfx.org>",
                    "to": alert.email,
                    "subject": f"SentimentFX Alert: {alert.ticker} sentiment {alert.direction} {alert.threshold}",
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
              <p style="font-size:10px;letter-spacing:0.2em;color:#f0b429;text-transform:uppercase;margin:0 0 20px;">— Alert Triggered</p>
              <h1 style="font-size:28px;font-weight:600;color:#e6edf3;margin:0 0 16px;line-height:1.2;">{alert.ticker} Sentiment Alert</h1>
              <p style="font-size:14px;color:#7d8590;margin:0 0 16px;line-height:1.7;">
                Your alert condition has been met.
              </p>
              <p style="font-family:'Courier New',monospace;font-size:13px;color:#e6edf3;margin:0 0 32px;">
                24h avg sentiment: <strong style="color:#f0b429;">{round(avg_score, 4)}</strong><br>
                Condition: sentiment {alert.direction} {alert.threshold}
              </p>
              <table cellpadding="0" cellspacing="0" style="margin-bottom:32px;">
                <tr>
                  <td style="background:#f0b429;border-radius:2px;">
                    <a href="https://app.sentimentfx.org" style="display:inline-block;padding:12px 28px;font-size:11px;font-weight:600;letter-spacing:0.1em;color:#080c10;text-decoration:none;text-transform:uppercase;">
                      View Dashboard -&gt;
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


def scrape_all():
    global last_scrape_time, last_scrape_duration
    start = time.time()
    print(f"[SCHEDULER] fired at {datetime.utcnow()}")
    db = SessionLocal()
    try:
        for ticker in TICKERS:
            headlines = fetch_headlines(ticker) + fetch_rss_headlines(ticker)

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
                HEADLINES_INGESTED.labels(source=h["source"], ticker=h["ticker"]).inc()

            prices = fetch_latest_price(ticker)
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
                    
        
        time.sleep(2)
        db.commit()
        check_alerts(db)
        last_scrape_time = datetime.utcnow().isoformat()
        last_scrape_duration = round(time.time() - start, 2)
        SCRAPER_RUNS.labels(status="success").inc()
        SCRAPER_DURATION.observe(time.time() - start)
        print("Scheduled scrape complete")
    except Exception as e:
        SCRAPER_RUNS.labels(status="failure").inc()
        print(f"Scheduler error: {e}")
    finally:
        db.close()


scheduler = BackgroundScheduler()
scheduler.add_job(scrape_all, CronTrigger(minute=0))
scheduler.add_job(refresh_subscription_gauge, CronTrigger(minute=30))  # refresh sub gauge every hour at :30
scheduler.start()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@app.get("/")
def root():
    return {"message": "Crypto Sentiment API"}


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

    # Signal strength label
    if primary_ci and primary_ci[0] * primary_ci[1] <= 0:
        strength = "inconclusive"
    elif primary_p < 0.05 and abs(primary_r) >= 0.2:
        strength = "strong"
    elif primary_p < 0.10 and abs(primary_r) >= 0.1:
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
async def export_sentiment(ticker: str, days: int = 0, db: Session = Depends(get_db), user=Depends(require_pro)):
    query = db.query(models.Headline).filter(models.Headline.ticker == ticker.upper())
    if days > 0:
        since = datetime.utcnow() - timedelta(days=days)
        query = query.filter(models.Headline.published_at >= since)
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
async def export_prices(ticker: str, days: int = 0, db: Session = Depends(get_db), user=Depends(require_pro)):
    query = db.query(models.Price).filter(models.Price.ticker == ticker.upper())
    if days > 0:
        since = datetime.utcnow() - timedelta(days=days)
        query = query.filter(models.Price.date >= since)
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


TICKER_QUERIES = {
    "BTC":  "bitcoin crypto",
    "ETH":  "ethereum crypto",
    "SOL":  "solana crypto",
    "XRP":  "ripple XRP crypto",
    "DOGE": "dogecoin crypto",
}


def run_backfill(ticker: str, days: int, offset: int):
    db = SessionLocal()
    query = TICKER_QUERIES.get(ticker.upper())
    if not query:
        print(f"Backfill: unknown ticker {ticker}")
        return

    saved = 0
    start_date = datetime.utcnow() - timedelta(days=offset + days)

    for i in range(days):
        from_date = (start_date + timedelta(days=i)).strftime("%Y-%m-%dT%H:%M:%SZ")
        to_date = (start_date + timedelta(days=i+1)).strftime("%Y-%m-%dT%H:%M:%SZ")

        params = {
            "q": query,
            "lang": "en",
            "max": 25,
            "from": from_date,
            "to": to_date,
            "sortby": "publishedAt",
            "apikey": os.getenv("GNEWS_API_KEY")
        }

        try:
            response = requests.get("https://gnews.io/api/v4/search", params=params)
            print(f"Backfill {ticker} day {i}: {response.status_code}")
            articles = response.json().get("articles", [])

            for article in articles:
                exists = db.query(models.Headline).filter(
                    models.Headline.url == article["url"]
                ).first()
                if exists:
                    continue

                t0 = time.time()
                sentiment = analyse_sentiment(article["title"])
                FINBERT_LATENCY.observe(time.time() - t0)
                headline = models.Headline(
                    ticker=ticker.upper(),
                    title=article["title"],
                    source=article["source"]["name"],
                    url=article["url"],
                    sentiment_score=sentiment["score"],
                    sentiment_label=sentiment["label"],
                    published_at=datetime.strptime(article["publishedAt"], "%Y-%m-%dT%H:%M:%SZ")
                )
                db.add(headline)
                saved += 1
                HEADLINES_INGESTED.labels(source=article["source"]["name"], ticker=ticker.upper()).inc()

            db.commit()

        except Exception as e:
            print(f"Backfill error for {ticker} day {i}: {e}")

    db.close()
    print(f"Backfill complete for {ticker}: {saved} headlines saved")


@app.post("/backfill/{ticker}")
def backfill(ticker: str, background_tasks: BackgroundTasks, days: int = 30, offset: int = 0, admin=Depends(require_admin)):
    if ticker.upper() not in TICKER_QUERIES:
        raise HTTPException(status_code=404, detail="Unknown ticker")
    background_tasks.add_task(run_backfill, ticker, days, offset)
    return {"message": f"Backfill started for {ticker} ({days} days, offset {offset}) - check Railway logs for progress"}


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


@app.post("/create-checkout-session")
def create_checkout_session(price_id: str, db: Session = Depends(get_db)):
    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            line_items=[{"price": price_id, "quantity": 1}],
            mode="subscription",
            allow_promotion_codes=True,
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
        if customer_email:
            supabase_client.table("profiles").update({"tier": "pro"}).eq("email", customer_email).execute()
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


# ---------------------------------------------------------------------------
# Usage tracking
# ---------------------------------------------------------------------------

def track_usage(api_key: models.APIKey, db: Session, count: int = 1, endpoint: str = "unknown"):
    api_key.calls_used += count
    API_CALLS.labels(endpoint=endpoint).inc(count)

    if api_key.calls_used > api_key.free_calls and api_key.stripe_customer_id:
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

    if shift_ci and shift_ci[0] * shift_ci[1] <= 0:
        strength = "inconclusive"
    elif shift_p < 0.05 and abs(shift_r) >= 0.2:
        strength = "strong"
    elif shift_p < 0.10 and abs(shift_r) >= 0.1:
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
    for t in TICKERS:
        headline_count = db.query(models.Headline).filter(models.Headline.ticker == t).count()
        price_count = db.query(models.Price).filter(models.Price.ticker == t).count()
        latest_headline = db.query(models.Headline).filter(
            models.Headline.ticker == t
        ).order_by(models.Headline.published_at.desc()).first()
        latest_price = db.query(models.Price).filter(
            models.Price.ticker == t
        ).order_by(models.Price.date.desc()).first()
        ticker_stats[t] = {
            "headlines": headline_count,
            "prices": price_count,
            "latest_headline": latest_headline.published_at.isoformat() if latest_headline else None,
            "latest_price": latest_price.date.isoformat() if latest_price else None,
            "latest_price_value": latest_price.close_price if latest_price else None,
        }
    return {
        "status": "operational",
        "last_scrape": last_scrape_time,
        "last_scrape_duration_seconds": last_scrape_duration,
        "tickers": ticker_stats,
        "total_headlines": db.query(models.Headline).count(),
        "total_prices": db.query(models.Price).count(),
        "timestamp": datetime.utcnow().isoformat(),
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