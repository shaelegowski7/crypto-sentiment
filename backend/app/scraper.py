import requests
import feedparser
from datetime import datetime, timezone
import os

BASE_URL = "https://gnews.io/api/v4/search"

TICKERS = {
    "BTC":    "bitcoin BTC",
    "ETH":    "ethereum ETH",
    "SOL":    "solana SOL",
    "XRP":    "ripple XRP",
    "DOGE":   "dogecoin DOGE",
    "EURUSD": "euro dollar EUR USD forex",
    "GBPUSD": "pound dollar GBP USD forex",
    "USDJPY": "dollar yen USD JPY forex",
}

# Keywords used to filter general RSS feeds (e.g. cryptoslate, decrypt) so that
# articles are only saved under a ticker if the title actually mentions it.
# Tag-specific feeds (e.g. cointelegraph.com/rss/tag/solana) are always included.
TICKER_KEYWORDS = {
    "BTC":    ["bitcoin", "btc"],
    "ETH":    ["ethereum", "eth"],
    "SOL":    ["solana", "sol"],
    "XRP":    ["ripple", "xrp"],
    "DOGE":   ["dogecoin", "doge"],
    "EURUSD": ["eur/usd", "eurusd", "euro dollar", "euro ", "ecb", "european central bank"],
    "GBPUSD": ["gbp/usd", "gbpusd", "pound dollar", "british pound", "pound sterling", "sterling", "bank of england"],
    "USDJPY": ["usd/jpy", "usdjpy", "dollar yen", "japanese yen", " yen ", "bank of japan", "boj"],
}

# Feeds that are general (not ticker-specific) and need keyword filtering
GENERAL_FEEDS = {
    "https://coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
    "https://decrypt.co/feed",
    "https://www.theblock.co/rss.xml",
    "https://cryptoslate.com/feed/",
    "https://www.fxstreet.com/rss/news",
    "https://www.forexlive.com/feed/",
    "https://www.reddit.com/r/Forex/new/.rss",
}

RSS_FEEDS = {
    "BTC": [
        "https://cointelegraph.com/rss/tag/bitcoin",
        "https://coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
        "https://bitcoinmagazine.com/feed",
        "https://decrypt.co/feed",
        "https://www.theblock.co/rss.xml",
        "https://cryptoslate.com/feed/",
        "https://www.reddit.com/r/Bitcoin/new/.rss",
    ],
    "ETH": [
        "https://cointelegraph.com/rss/tag/ethereum",
        "https://decrypt.co/feed",
        "https://cryptoslate.com/feed/",
        "https://www.reddit.com/r/ethereum/new/.rss",
    ],
    "SOL": [
        "https://cointelegraph.com/rss/tag/solana",
        "https://cryptoslate.com/feed/",
        "https://www.reddit.com/r/solana/new/.rss",
    ],
    "XRP": [
        "https://cointelegraph.com/rss/tag/xrp",
        "https://cryptoslate.com/feed/",
        "https://www.reddit.com/r/XRP/new/.rss",
    ],
    "DOGE": [
        "https://cointelegraph.com/rss/tag/dogecoin",
        "https://cryptoslate.com/feed/",
        "https://www.reddit.com/r/dogecoin/new/.rss",
    ],
    "EURUSD": [
        "https://www.fxstreet.com/rss/news",
        "https://www.forexlive.com/feed/",
        "https://www.reddit.com/r/Forex/new/.rss",
    ],
    "GBPUSD": [
        "https://www.fxstreet.com/rss/news",
        "https://www.forexlive.com/feed/",
        "https://www.reddit.com/r/Forex/new/.rss",
    ],
    "USDJPY": [
        "https://www.fxstreet.com/rss/news",
        "https://www.forexlive.com/feed/",
        "https://www.reddit.com/r/Forex/new/.rss",
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
    keywords = TICKER_KEYWORDS.get(ticker.upper(), [])
    headlines = []

    for url in feeds:
        try:
            headers = {"User-Agent": "SentimentFX/1.0"} if "reddit.com" in url else {}
            feed = feedparser.parse(url, request_headers=headers)
            is_general = url in GENERAL_FEEDS
            print(f"[RSS] {ticker}: {url} entries={len(feed.entries)} general={is_general}")

            for entry in feed.entries[:10]:
                try:
                    title_lower = entry.title.lower()
                    if is_general and not any(kw in title_lower for kw in keywords):
                        continue

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