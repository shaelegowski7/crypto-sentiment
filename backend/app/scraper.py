import requests
from datetime import datetime
from dotenv import load_dotenv
import os

load_dotenv()

NEWS_API_KEY = os.getenv("NEWSAPI_KEY")
BASE_URL = "https://newsapi.org/v2/everything"

TICKERS = {
    "BTC": "bitcoin",
    "ETH": "ethereum",
    "SOL": "solana",
    "XRP": "ripple XRP"
}

def fetch_headlines(ticker: str) -> list:
    query = TICKERS.get(ticker)
    
    if not query:
        print(f"Unknown ticker: {ticker}")
        return []

    params = {
        "q": query,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": 10,
        "apiKey": NEWS_API_KEY
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