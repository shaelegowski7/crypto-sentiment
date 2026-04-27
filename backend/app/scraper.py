import requests
import feedparser
from datetime import datetime, timezone
from dotenv import load_dotenv
import os

load_dotenv()

GNEWS_API_KEY = os.getenv("GNEWS_API_KEY")
BASE_URL = "https://gnews.io/api/v4/search"

TICKERS = {
    "BTC":  "bitcoin BTC",
    "ETH":  "ethereum ETH",
    "SOL":  "solana SOL",
    "XRP":  "ripple XRP",
    "DOGE": "dogecoin DOGE",
}

RSS_FEEDS = {
    "BTC":  [
        "https://cointelegraph.com/rss/tag/bitcoin",
        "https://coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
    ],
    "ETH":  ["https://cointelegraph.com/rss/tag/ethereum"],
    "SOL":  ["https://cointelegraph.com/rss/tag/solana"],
    "XRP":  ["https://cointelegraph.com/rss/tag/xrp"],
    "DOGE": ["https://cointelegraph.com/rss/tag/dogecoin"],
}

def fetch_headlines(ticker: str) -> list:
    query = TICKERS.get(ticker)

    if not query:
        print(f"Unknown ticker: {ticker}")
        return []

    params = {
        "q": query,
        "lang": "en",
        "max": 25,
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


def fetch_rss_headlines(ticker: str) -> list:
    feeds = RSS_FEEDS.get(ticker.upper(), [])
    headlines = []

    for url in feeds:
        feed = feedparser.parse(url)
        for entry in feed.entries[:10]:
            try:
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                pub_date = datetime(*published[:6], tzinfo=timezone.utc) if published else datetime.now(timezone.utc)

                headlines.append({
                    "ticker": ticker.upper(),
                    "title": entry.title,
                    "source": feed.feed.get("title", url),
                    "url": entry.link,
                    "published_at": pub_date
                })
            except Exception as e:
                print(f"RSS parse error ({url}): {e}")
                continue

    return headlines