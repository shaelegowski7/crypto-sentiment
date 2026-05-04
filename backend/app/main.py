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
from .database import SessionLocal
from scipy.stats import pearsonr
from fastapi.responses import StreamingResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from slowapi.middleware import SlowAPIMiddleware
from fastapi.responses import JSONResponse
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

last_scrape_time = None
last_scrape_duration = None

stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

models.Base.metadata.create_all(bind=engine)

def get_api_key_value(request: Request) -> str:
    """Rate limit by API key header, fall back to IP."""
    return request.headers.get("x-api-key") or get_remote_address(request)

limiter = Limiter(key_func=get_api_key_value)

app = FastAPI()

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
                sentiment = analyse_sentiment(h["title"])
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

            prices = fetch_latest_price(ticker)
            if prices:
                exists = db.query(models.Price).filter(
                    models.Price.ticker == prices["ticker"],
                    models.Price.date == prices["date"]
                ).first()
                if not exists:
                    price = models.Price(
                        ticker=prices["ticker"],
                        close_price=prices["close_price"],
                        volume=prices["volume"],
                        date=prices["date"]
                    )
                    db.add(price)

        db.commit()
        check_alerts(db)
        last_scrape_time = datetime.utcnow().isoformat()
        last_scrape_duration = round(time.time() - start, 2)
        print("Scheduled scrape complete")
    except Exception as e:
        print(f"Scheduler error: {e}")
    finally:
        db.close()


scheduler = BackgroundScheduler()
scheduler.add_job(scrape_all, CronTrigger(minute=0))
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
        sentiment = analyse_sentiment(h["title"])
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

    since = datetime.utcnow() - timedelta(days=90)

    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper(),
        models.Headline.published_at >= since
    ).order_by(models.Headline.published_at).all()

    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper(),
        models.Price.date >= since
    ).order_by(models.Price.date).all()

    if len(headlines) < 10 or len(prices) < 10:
        return {"message": "Not enough data yet"}

    sentiment_by_date = {}
    for h in headlines:
        date = str(h.published_at.date())
        if date not in sentiment_by_date:
            sentiment_by_date[date] = []
        if abs(h.sentiment_score) > 0.05:
            sentiment_by_date[date].append(h.sentiment_score)

    avg_sentiment = {
        date: sum(scores) / len(scores)
        for date, scores in sentiment_by_date.items()
        if len(scores) > 0
    }

    price_by_date = {}
    sorted_prices = sorted(prices, key=lambda p: p.date)
    for i in range(1, len(sorted_prices)):
        date = str(sorted_prices[i].date.date())
        prev_price = sorted_prices[i-1].close_price
        curr_price = sorted_prices[i].close_price
        if prev_price > 0:
            price_by_date[date] = (curr_price - prev_price) / prev_price * 100

    common_dates = sorted(set(avg_sentiment.keys()) & set(price_by_date.keys()))

    if len(common_dates) < 10:
        return {"message": "Not enough overlapping data yet"}

    best_corr = 0
    best_lag = 0
    all_lags = {}

    for lag in range(0, 8):
        s = [avg_sentiment[d] for d in common_dates[lag:]]
        p = [price_by_date[d] for d in common_dates[:len(common_dates) - lag]]
        if len(s) < 10:
            continue
        corr, pvalue = pearsonr(s, p)
        all_lags[lag] = round(corr, 3)
        if abs(corr) > abs(best_corr):
            best_corr = corr
            best_lag = lag

    direction = "negative (contrarian)" if best_corr < 0 else "positive (momentum)"

    return {
        "ticker": ticker.upper(),
        "best_lag_days": best_lag,
        "correlation": round(best_corr, 3),
        "all_lags": all_lags,
        "interpretation": f"Sentiment {best_lag} days ago has {round(abs(best_corr) * 100)}% correlation with price returns",
        "signal_type": direction
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

                sentiment = analyse_sentiment(article["title"])
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

    if event["type"] == "checkout.session.completed":
        session = event["data"]["object"]
        customer_email = session["customer_details"]["email"]

        if customer_email:
            from supabase import create_client
            supabase_client = create_client(
                os.getenv("SUPABASE_URL"),
                os.getenv("SUPABASE_SERVICE_KEY")
            )
            supabase_client.table("profiles").update({"tier": "pro"}).eq("email", customer_email).execute()

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

def track_usage(api_key: models.APIKey, db: Session, count: int = 1):
    api_key.calls_used += count

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

@app.get("/v1/sentiment/{ticker}")
@limiter.limit("30/minute")
def api_sentiment(request: Request, ticker: str, limit: int = 25, db: Session = Depends(get_db), api_key=Depends(get_api_key)):
    import math
    calls = math.ceil(limit / 25)
    track_usage(api_key, db, calls)

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


@app.get("/v1/summary/{ticker}")
@limiter.limit("20/minute")
def api_summary(request: Request, ticker: str, days: int = 30, db: Session = Depends(get_db), api_key=Depends(get_api_key)):
    track_usage(api_key, db, days)

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


@app.get("/v1/prices/{ticker}")
@limiter.limit("20/minute")
def api_prices(request: Request, ticker: str, days: int = 30, db: Session = Depends(get_db), api_key=Depends(get_api_key)):
    track_usage(api_key, db, days)

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


@app.get("/v1/correlation/{ticker}")
@limiter.limit("10/minute")
def api_correlation(request: Request, ticker: str, db: Session = Depends(get_db), api_key=Depends(get_api_key)):
    track_usage(api_key, db)
    since = datetime.utcnow() - timedelta(days=90)

    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper(),
        models.Headline.published_at >= since
    ).order_by(models.Headline.published_at).all()

    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper(),
        models.Price.date >= since
    ).order_by(models.Price.date).all()

    if len(headlines) < 10 or len(prices) < 10:
        raise HTTPException(status_code=404, detail="Not enough data")

    sentiment_by_date = {}
    for h in headlines:
        date = str(h.published_at.date())
        if date not in sentiment_by_date:
            sentiment_by_date[date] = []
        if abs(h.sentiment_score) > 0.05:
            sentiment_by_date[date].append(h.sentiment_score)

    avg_sentiment = {
        date: sum(scores) / len(scores)
        for date, scores in sentiment_by_date.items()
        if len(scores) > 0
    }

    price_by_date = {}
    sorted_prices = sorted(prices, key=lambda p: p.date)
    for i in range(1, len(sorted_prices)):
        date = str(sorted_prices[i].date.date())
        prev_price = sorted_prices[i-1].close_price
        curr_price = sorted_prices[i].close_price
        if prev_price > 0:
            price_by_date[date] = (curr_price - prev_price) / prev_price * 100

    common_dates = sorted(set(avg_sentiment.keys()) & set(price_by_date.keys()))

    if len(common_dates) < 10:
        raise HTTPException(status_code=404, detail="Not enough overlapping data")

    best_corr = 0
    best_lag = 0
    all_lags = {}

    for lag in range(0, 8):
        s = [avg_sentiment[d] for d in common_dates[lag:]]
        p = [price_by_date[d] for d in common_dates[:len(common_dates) - lag]]
        if len(s) < 10:
            continue
        corr, _ = pearsonr(s, p)
        all_lags[lag] = round(corr, 3)
        if abs(corr) > abs(best_corr):
            best_corr = corr
            best_lag = lag

    return {
        "ticker": ticker.upper(),
        "calls_used": 1,
        "best_lag_days": best_lag,
        "correlation": round(best_corr, 3),
        "all_lags": all_lags,
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
        ticker_stats[t] = {
            "headlines": headline_count,
            "prices": price_count,
            "latest_headline": latest_headline.published_at.isoformat() if latest_headline else None,
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