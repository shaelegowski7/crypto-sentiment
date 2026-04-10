
# Crypto Sentiment Dashboard 

Full-stack dashboard tracking news sentiment vs. crypto prices in real time, powered by FinBERT AI.


## Live Links
- Landing page: [sentimentfx.vercel.app](https://sentimentfx.vercel.app)
- Dashboard: [crypto-sentiment-five.vercel.app](https://crypto-sentiment-five.vercel.app)




## Tech Stack
- **Backend:** Python, FastAPI, PostgreSQL, FinBERT
- **Frontend:** React, Recharts
- **Data:** GNews API, yfinance
- **Deployed:** Railway (backend) + Vercel (frontend)

## Features
-  Real-time news scraping for BTC, ETH, SOL, XRP
-  FinBERT sentiment analysis (finance-specific AI model)
-  Sentiment vs price charts in GBP
-  Auto-scrapes every hour
-  PostgreSQL database

## Getting Started

### Backend
bash
cd backend
python -m venv venv
source venv/Scripts/activate  # Windows
pip install -r requirements.txt
cp .env.example .env  # fill in your keys
python -m uvicorn app.main:app --reload
```

### Frontend
bash
cd frontend
npm install
npm run dev
```

