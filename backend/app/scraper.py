import requests
import feedparser
from datetime import datetime, timezone
import os

BASE_URL = "https://gnews.io/api/v4/search"

TICKERS = {
    "BTC":  "bitcoin BTC",
    "ETH":  "ethereum ETH",
    "SOL":  "solana SOL",
    "XRP":  "ripple XRP",
    "DOGE": "dogecoin DOGE",
}

RSS_FEEDS = {
    "BTC": [
        "https://cointelegraph.com/rss/tag/bitcoin",
        "https://coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
        "https://bitcoinmagazine.com/feed",
        "https://decrypt.co/feed",
        "https://www.theblock.co/rss.xml",
        "https://cryptoslate.com/feed/",
    ],
    "ETH": [
        "https://cointelegraph.com/rss/tag/ethereum",
        "https://decrypt.co/feed",
        "https://cryptoslate.com/feed/",
    ],
    "SOL": [
        "https://cointelegraph.com/rss/tag/solana",
        "https://cryptoslate.com/feed/",
    ],
    "XRP": [
        "https://cointelegraph.com/rss/tag/xrp",
        "https://cryptoslate.com/feed/",
    ],
    "DOGE": [
        "https://cointelegraph.com/rss/tag/dogecoin",
        "https://cryptoslate.com/feed/",
    ],
}

def fetch_headlines(ticker: str) -> list:
    query = TICKERS.get(ticker)
    if not query:
        print(f"[GNEWS] Unknown ticker: {ticker}")
        return []

    api_key = os.getenv("GNEWS_API_KEY")
    if not api_key:
        print(f"[GNEWS] ERROR: GNEWS_API_KEY not set")
        return []

    params = {
        "q": query,
        "lang": "en",
        "max": 25,
        "apikey": api_key
    }

    try:
        response = requests.get(BASE_URL, params=params, timeout=10)
        articles = response.json().get("articles", [])
        print(f"[GNEWS] {ticker}: status={response.status_code} articles={len(articles)}")

        if response.status_code != 200:
            print(f"[GNEWS] {ticker}: error={response.text}")
            return []

    except Exception as e:
        print(f"[GNEWS] {ticker}: exception={e}")
        return []

    headlines = []
    for article in articles:
        try:
            headlines.append({
                "ticker": ticker,
                "title": article["title"],
                "source": article["source"]["name"],
                "url": article["url"],
                "published_at": datetime.strptime(article["publishedAt"], "%Y-%m-%dT%H:%M:%SZ")
            })
        except Exception as e:
            print(f"[GNEWS] {ticker}: parse error={e}")
            continue

    return headlines


def fetch_rss_headlines(ticker: str) -> list:
    feeds = RSS_FEEDS.get(ticker.upper(), [])
    headlines = []

    for url in feeds:
        try:
            feed = feedparser.parse(url)
            print(f"[RSS] {ticker}: {url} entries={len(feed.entries)}")

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
                    print(f"[RSS] {ticker}: entry parse error={e}")
                    continue

        except Exception as e:
            print(f"[RSS] {ticker}: feed error ({url})={e}")
            continue

    return headlines