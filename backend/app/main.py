from fastapi import FastAPI, Depends, HTTPException
from sqlalchemy.orm import Session
from . import models, schemas
from .database import engine, get_db
from .scraper import fetch_headlines
from .sentiment import analyse_sentiment
from .prices import fetch_prices
from datetime import datetime
from fastapi.middleware.cors import CORSMiddleware
from apscheduler.schedulers.background import BackgroundScheduler
from .scraper import fetch_headlines
from .sentiment import analyse_sentiment
from .prices import fetch_prices
from . import models
from .database import SessionLocal
from scipy.stats import pearsonr
from datetime import datetime, timedelta
import numpy as np
import resend 
import os  
import requests

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

TICKERS = ["BTC", "ETH", "SOL", "XRP", "BNB", "ADA", "AVAX", "LINK", "DOGE"]

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

            prices = fetch_prices(ticker)
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
def cleanup_duplicates(db: Session = Depends(get_db)):
    # Get all headlines ordered by id
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
def cleanup_duplicate_prices(db: Session = Depends(get_db)):
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
    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker.upper()
    ).order_by(models.Headline.published_at).all()
    
    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker.upper()
    ).order_by(models.Price.date).all()

    # Average sentiment by date
    sentiment_by_date = {}
    for h in headlines:
        date = str(h.published_at.date())
        if date not in sentiment_by_date:
            sentiment_by_date[date] = []
        sentiment_by_date[date].append(h.sentiment_score)
    
    avg_sentiment = {date: sum(scores)/len(scores) 
                    for date, scores in sentiment_by_date.items()}

    # Price by date
    price_by_date = {str(p.date.date()): p.close_price for p in prices}

    # Find common dates
    common_dates = sorted(set(avg_sentiment.keys()) & set(price_by_date.keys()))

    if len(common_dates) < 5:
        return {"message": "Not enough data yet"}

    best_corr = 0
    best_lag = 0

    for lag in range(0, 4):
        s = [avg_sentiment[d] for d in common_dates[lag:]]
        p = [price_by_date[d] for d in common_dates[:len(common_dates)-lag]]
        if len(s) < 5:
            continue
        corr, _ = pearsonr(s, p)
        if abs(corr) > abs(best_corr):
            best_corr = corr
            best_lag = lag

    return {
        "ticker": ticker.upper(),
        "best_lag_days": best_lag,
        "correlation": round(best_corr, 3),
        "interpretation": f"Sentiment {best_lag} days ago has {round(abs(best_corr)*100)}% correlation with price"
    }

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
            "from": "SentimentFX <onboarding@resend.dev>",
            "to": data.email,
            "subject": "You're on the SentimentFX waitlist",
            "html": """
                <div style="background:#080c10;color:#e6edf3;padding:40px;font-family:monospace;">
                    <h1 style="color:#f0b429;letter-spacing:0.1em;">SENTIMENTFX</h1>
                    <p style="margin-top:24px;">You're on the list.</p>
                    <p style="color:#7d8590;">We'll reach out when early access opens.</p>
                    <p style="margin-top:24px;">In the meantime, check out the live dashboard:</p>
                    <a href="https://crypto-sentiment-five.vercel.app" style="color:#f0b429;">
                        crypto-sentiment-five.vercel.app
                    </a>
                    <p style="margin-top:40px;color:#7d8590;font-size:12px;">— SentimentFX team</p>
                </div>
            """
        })
    except Exception as e:
        print(f"Email error: {e}")

    return {"message": "You're on the list!"}


@app.get("/waitlist/count")
def waitlist_count(db: Session = Depends(get_db)):
    count = db.query(models.WaitlistEmail).count()
    return {"count": count}

@app.post("/backfill/{ticker}")
def backfill(ticker: str, db: Session = Depends(get_db)):
    query = {
        "BTC": "bitcoin crypto",
        "ETH": "ethereum crypto",
        "SOL": "solana crypto",
        "BNB": "binance BNB crypto",
        "ADA": "cardano crypto",
        "AVAX": "avalanche crypto",
        "LINK": "chainlink crypto",
        "DOGE": "dogecoin crypto",
    }.get(ticker.upper())

    if not query:
        raise HTTPException(status_code=404, detail="Unknown ticker")

    saved = 0
    # Go back 30 days to start — don't do all 5 years at once
    start_date = datetime.utcnow() - timedelta(days=30)

    for i in range(30):
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
            print(f"GNews response for {ticker} day {i}: {response.status_code}")
            print(f"Response: {response.json()}")
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

        except Exception as e:
            print(f"Backfill error for {ticker} day {i}: {e}")

    db.commit()
    return {"message": f"Backfilled {saved} headlines for {ticker}"}