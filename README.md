# SentimentFX

Full-stack crypto sentiment intelligence platform. Scrapes financial news headlines, scores them with FinBERT (a finance-specific transformer model), and correlates sentiment against live GBP crypto prices to surface predictive signals.

**Live:** [sentimentfx.org](https://sentimentfx.org) · [app.sentimentfx.org](https://app.sentimentfx.org) · [developers.sentimentfx.org](https://developers.sentimentfx.org)

---

## What it does

- Scrapes headlines every hour from GNews API + CoinTelegraph/CoinDesk RSS feeds
- Scores each headline with FinBERT (`positive_prob - negative_prob` → range -1 to +1)
- Fetches daily GBP prices via yfinance
- Computes Pearson correlation across 0–7 day lags to find the best predictive signal
- Exposes a public metered API with Stripe billing for developers

## Tech Stack

| Layer | Technology |
|-------|-----------|
| Backend | Python 3.11, FastAPI, SQLAlchemy |
| AI Model | FinBERT (ProsusAI/finbert) |
| Database | PostgreSQL |
| Auth | Supabase (email + Google OAuth) |
| Payments | Stripe (subscriptions + metered API billing) |
| Frontend | React, Recharts, Vite |
| Data | GNews API, feedparser (RSS), yfinance |
| Email | Resend |
| Deploy | Railway (backend), Vercel (frontend) |

## Features

**Dashboard**
- Sentiment vs price chart with 7/30/90/ALL day ranges
- Pearson correlation with lag analysis (0–7 days)
- Momentum vs contrarian signal detection
- Paginated headline feed with per-article sentiment scores
- GBP/USD currency toggle
- CSV export (Pro)

**Tickers:** BTC, ETH, SOL, XRP, DOGE

**Access tiers**
- Free — BTC only, 30 day history
- Pro (£11.99/mo or £99.99/yr) — all tickers, full history, CSV export, sentiment alerts

**Developer API** (`api.sentimentfx.org/v1/`)
- `/v1/sentiment/{ticker}` — scored headlines
- `/v1/summary/{ticker}` — daily averaged sentiment
- `/v1/prices/{ticker}` — daily GBP closing prices
- `/v1/correlation/{ticker}` — Pearson r across lag periods
- Metered billing at £0.01/call after 100 free calls
- Rate limited via slowapi

## Architecture

```
GNews API + RSS feeds
        ↓
   scraper.py → FinBERT (sentiment.py) → headlines table
                                              ↓
yfinance → prices.py → prices table    /dashboard/{ticker}
                                              ↓
                                       React + Recharts
```

APScheduler runs `scrape_all()` on a cron trigger at the top of every hour.

## Getting Started

### Backend

```bash
cd backend
python -m venv venv
source venv/Scripts/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env          # fill in your keys
python -m uvicorn app.main:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```


## Sentiment Scoring

Uses FinBERT's output probabilities directly:

```
score = positive_probability - negative_probability
```

Range: -1 (maximally negative) to +1 (maximally positive). Neutral headlines score near 0. This avoids the label × confidence collapse that zeros out neutral predictions.

## API Reference

Full docs at [developers.sentimentfx.org](https://developers.sentimentfx.org)

```bash
# Generate a free API key
curl -X POST https://api.sentimentfx.org/api/keys/generate \
  -H "Content-Type: application/json" \
  -d '{"email": "your@email.com"}'

# Use the key
curl https://api.sentimentfx.org/v1/summary/BTC \
  -H "X-API-Key: sfx_your_key_here"
```

## Deployment

- **Backend:** Railway via `Procfile` + `nixpacks.toml`. Starts with `uvicorn app.main:app --host 0.0.0.0 --port $PORT`.
- **Frontend/Landing/Docs:** Vercel, auto-deploys from `main`.