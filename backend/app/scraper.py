import requests
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")
BASE_URL = "https://gnews.io/api/v4/search"

TICKERS = {
    "BTC": "bitcoin price BTC market",
    "ETH": "ethereum price ETH market",
    "SOL": "solana price SOL market",
    "XRP": "ripple XRP price market",
    "BNB": "BNB binance price market",
    "ADA": "cardano ADA price market",
    "AVAX": "avalanche AVAX price market",
    "LINK": "chainlink LINK price market",
    "DOGE": "dogecoin DOGE price market"
}

def fetch_headlines(ticker: str) -> list:
    query = TICKERS.get(ticker)

    if not query:
        print(f"Unknown ticker: {ticker}")
        return []

    params = {
        "q": query,
        "lang": "en",
        "max": 10,
        "apikey": GNEWS_API_KEY
    }

    response = requests.get(BASE_URL, params=params)

    if response.status_code != 200:
        print(f"Error fetching news: {response.status_code}")
        return []

    articles = response.json().get("articles", [])

    headlines = []
    for article in articles:
        headlines.append({
            "ticker": ticker,
            "title": article["title"],
            "source": article["source"]["name"],
            "url": article["url"],
            "published_at": datetime.strptime(
                article["publishedAt"], "%Y-%m-%dT%H:%M:%SZ"
            )
        })

    return headlines