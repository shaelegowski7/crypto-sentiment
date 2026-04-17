from fastapi import FastAPI, Depends, HTTPException, Header, BackgroundTasks
from sqlalchemy.orm import Session
from fastapi import Request
from . import models, schemas
from .database import engine, get_db
from .scraper import fetch_headlines
from .sentiment import analyse_sentiment
from .prices import fetch_prices, fetch_latest_price
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from .scraper import fetch_headlines
from .sentiment import analyse_sentiment
from .prices import fetch_prices, fetch_latest_price
from . import models
from .database import SessionLocal
from scipy.stats import pearsonr
from datetime import datetime, timedelta
from fastapi.responses import StreamingResponse
import numpy as np
import resend 
import os  
import requests
import stripe
import csv
import io
stripe.api_key = os.getenv("STRIPE_SECRET_KEY")

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

TICKERS = ["BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "AVAX", "LINK", "DOGE"]


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


async def require_admin(authorization: str = Header(None)):
    secret = os.getenv("ADMIN_SECRET")
    if not secret or authorization != f"Bearer {secret}":
        raise HTTPException(status_code=401, detail="Unauthorized")


def scrape_all():
    db = SessionLocal()
    try:
        for ticker in TICKERS:
            headlines = fetch_headlines(ticker)
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
        print("Scheduled scrape complete")
    except Exception as e:
        print(f"Scheduler error: {e}")
    finally:
        db.close()


scheduler = BackgroundScheduler()
scheduler.add_job(scrape_all, "interval", hours=1)
scheduler.start()

@app.get("/")
def root():
    return {"message": "Crypto Sentiment API"}


@app.post("/scrape/{ticker}")
def scrape(ticker: str, db: Session = Depends(get_db)):
    headlines = fetch_headlines(ticker.upper())

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
def save_prices(ticker: str, db: Session = Depends(get_db)):
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
def get_dashboard(ticker: str, db: Session = Depends(get_db)):
    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper()
    ).all()
    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper()
    ).all()

    return {
        "ticker": ticker.upper(),
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
    from scipy.stats import pearsonr
    import numpy as np

    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper()
    ).order_by(models.Headline.published_at).all()

    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper()
    ).order_by(models.Price.date).all()

    if len(headlines) < 10 or len(prices) < 10:
        return {"message": "Not enough data yet"}

    sentiment_by_date = {}
    for h in headlines:
        date = str(h.published_at.date())
        if date not in sentiment_by_date:
            sentiment_by_date[date] = []
        sentiment_by_date[date].append(h.sentiment_score)

    avg_sentiment = {
        date: sum(scores) / len(scores)
        for date, scores in sentiment_by_date.items()
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
                — Waitlist Confirmed
              </p>
              <h1 style="font-size:28px;font-weight:600;color:#e6edf3;margin:0 0 16px;line-height:1.2;letter-spacing:-0.01em;">
                You're on the list.
              </h1>
              <p style="font-size:14px;color:#7d8590;margin:0 0 32px;line-height:1.7;">
                We'll reach out when early access opens — you'll get founder pricing
                and first access to the full signal suite.
              </p>
              <table cellpadding="0" cellspacing="0" style="margin-bottom:32px;">
                <tr>
                  <td style="padding-right:6px;">
                    <span style="font-size:10px;letter-spacing:0.08em;padding:3px 10px;border:1px solid rgba(63,185,80,0.4);border-radius:2px;color:#3fb950;background:rgba(63,185,80,0.06);">BTC +0.76</span>
                  </td>
                  <td style="padding-right:6px;">
                    <span style="font-size:10px;letter-spacing:0.08em;padding:3px 10px;border:1px solid rgba(248,81,73,0.4);border-radius:2px;color:#f85149;background:rgba(248,81,73,0.06);">ETH −0.54</span>
                  </td>
                  <td style="padding-right:6px;">
                    <span style="font-size:10px;letter-spacing:0.08em;padding:3px 10px;border:1px solid rgba(63,185,80,0.4);border-radius:2px;color:#3fb950;background:rgba(63,185,80,0.06);">SOL +0.61</span>
                  </td>
                  <td>
                    <span style="font-size:10px;letter-spacing:0.08em;padding:3px 10px;border:1px solid rgba(248,81,73,0.4);border-radius:2px;color:#f85149;background:rgba(248,81,73,0.06);">XRP −0.39</span>
                  </td>
                </tr>
              </table>
              <table cellpadding="0" cellspacing="0" style="margin-bottom:32px;">
                <tr>
                  <td style="background:#f0b429;border-radius:2px;">
                    <a href="https://app.sentimentfx.org"
                       style="display:inline-block;padding:12px 28px;font-size:11px;font-weight:600;letter-spacing:0.1em;color:#080c10;text-decoration:none;text-transform:uppercase;">
                      View Live Dashboard →
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
        print(f"Email error: {e}")

    return {"message": "You're on the list!"}


@app.get("/waitlist/count")
def waitlist_count(db: Session = Depends(get_db)):
    count = db.query(models.WaitlistEmail).count()
    return {"count": count}

TICKER_QUERIES = {
    "BTC": "bitcoin crypto",
    "ETH": "ethereum crypto",
    "SOL": "solana crypto",
    "BNB": "binance BNB crypto",
    "XRP": "ripple XRP crypto",
    "ADA": "cardano crypto",
    "AVAX": "avalanche crypto",
    "LINK": "chainlink crypto",
    "DOGE": "dogecoin crypto"
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
    return {"message": f"Backfill started for {ticker} ({days} days, offset {offset}) — check Railway logs for progress"}

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