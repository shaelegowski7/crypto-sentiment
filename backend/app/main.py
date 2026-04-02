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

models.Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_methods=["*"],
    allow_headers=["*"],
)

TICKERS = ["BTC", "ETH", "SOL", "XRP"]

def scrape_all():
    db = SessionLocal()
    try:
        for ticker in TICKERS:
            # Scrape and save headlines
            headlines = fetch_headlines(ticker)
            for h in headlines:
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

            # Fetch and save prices
            prices = fetch_prices(ticker)
            for p in prices:
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