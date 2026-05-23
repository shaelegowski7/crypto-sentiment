import requests
import feedparser
import time
from datetime import datetime, timedelta, timezone
import os

BASE_URL = "https://gnews.io/api/v4/search"
GDELT_DOC_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

TICKERS = {
    "BTC":    "bitcoin BTC",
    "ETH":    "ethereum ETH",
    "SOL":    "solana SOL",
    "XRP":    "ripple XRP",
    "DOGE":   "dogecoin DOGE",
    "EURUSD": "euro dollar EUR USD forex",
    "GBPUSD": "pound dollar GBP USD forex",
    "USDJPY": "dollar yen USD JPY forex",
    "AUDUSD": "australian dollar AUD USD forex",
    "USDCAD": "canadian dollar CAD USD forex",
    "USDCHF": "swiss franc CHF USD forex",
    "NZDUSD": "new zealand dollar NZD USD forex",
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
    "AUDUSD": ["aud/usd", "audusd", "australian dollar", "aussie dollar", "reserve bank of australia", "rba"],
    "USDCAD": ["usd/cad", "usdcad", "canadian dollar", "loonie", "bank of canada"],
    "USDCHF": ["usd/chf", "usdchf", "swiss franc", "swissie", "swiss national bank", "snb"],
    "NZDUSD": ["nzd/usd", "nzdusd", "new zealand dollar", "kiwi dollar", "reserve bank of new zealand", "rbnz"],
}

# Feeds that are general (not ticker-specific) and need keyword filtering
GENERAL_FEEDS = {
    "https://coindesk.com/arc/outboundfeeds/rss/?outputType=xml",
    "https://decrypt.co/feed",
    "https://www.theblock.co/rss.xml",
    "https://cryptoslate.com/feed/",
    "https://blockworks.co/feed/",
    "https://beincrypto.com/feed/",
    "https://www.newsbtc.com/feed/",
    "https://bitcoinist.com/feed/",
    "https://thedefiant.io/feed",
    "https://www.fxstreet.com/rss/news",
    "https://www.forexlive.com/feed/",
    "https://www.dailyfx.com/feeds/market-news",
    "https://www.actionforex.com/feed/",
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
        "https://blockworks.co/feed/",
        "https://beincrypto.com/feed/",
        "https://www.newsbtc.com/feed/",
        "https://bitcoinist.com/feed/",
        "https://www.reddit.com/r/Bitcoin/new/.rss",
    ],
    "ETH": [
        "https://cointelegraph.com/rss/tag/ethereum",
        "https://decrypt.co/feed",
        "https://cryptoslate.com/feed/",
        "https://blockworks.co/feed/",
        "https://beincrypto.com/feed/",
        "https://www.newsbtc.com/feed/",
        "https://thedefiant.io/feed",
        "https://www.reddit.com/r/ethereum/new/.rss",
    ],
    "SOL": [
        "https://cointelegraph.com/rss/tag/solana",
        "https://cryptoslate.com/feed/",
        "https://blockworks.co/feed/",
        "https://beincrypto.com/feed/",
        "https://thedefiant.io/feed",
        "https://www.reddit.com/r/solana/new/.rss",
    ],
    "XRP": [
        "https://cointelegraph.com/rss/tag/xrp",
        "https://cryptoslate.com/feed/",
        "https://beincrypto.com/feed/",
        "https://www.reddit.com/r/XRP/new/.rss",
    ],
    "DOGE": [
        "https://cointelegraph.com/rss/tag/dogecoin",
        "https://cryptoslate.com/feed/",
        "https://beincrypto.com/feed/",
        "https://www.reddit.com/r/dogecoin/new/.rss",
    ],
    "EURUSD": [
        "https://www.fxstreet.com/rss/news",
        "https://www.forexlive.com/feed/",
        "https://www.dailyfx.com/feeds/market-news",
        "https://www.actionforex.com/feed/",
        "https://www.reddit.com/r/Forex/new/.rss",
    ],
    "GBPUSD": [
        "https://www.fxstreet.com/rss/news",
        "https://www.forexlive.com/feed/",
        "https://www.dailyfx.com/feeds/market-news",
        "https://www.actionforex.com/feed/",
        "https://www.reddit.com/r/Forex/new/.rss",
    ],
    "USDJPY": [
        "https://www.fxstreet.com/rss/news",
        "https://www.forexlive.com/feed/",
        "https://www.dailyfx.com/feeds/market-news",
        "https://www.actionforex.com/feed/",
        "https://www.reddit.com/r/Forex/new/.rss",
    ],
    "AUDUSD": [
        "https://www.fxstreet.com/rss/news",
        "https://www.forexlive.com/feed/",
        "https://www.dailyfx.com/feeds/market-news",
        "https://www.actionforex.com/feed/",
        "https://www.reddit.com/r/Forex/new/.rss",
    ],
    "USDCAD": [
        "https://www.fxstreet.com/rss/news",
        "https://www.forexlive.com/feed/",
        "https://www.dailyfx.com/feeds/market-news",
        "https://www.actionforex.com/feed/",
        "https://www.reddit.com/r/Forex/new/.rss",
    ],
    "USDCHF": [
        "https://www.fxstreet.com/rss/news",
        "https://www.forexlive.com/feed/",
        "https://www.dailyfx.com/feeds/market-news",
        "https://www.actionforex.com/feed/",
        "https://www.reddit.com/r/Forex/new/.rss",
    ],
    "NZDUSD": [
        "https://www.fxstreet.com/rss/news",
        "https://www.forexlive.com/feed/",
        "https://www.dailyfx.com/feeds/market-news",
        "https://www.actionforex.com/feed/",
        "https://www.reddit.com/r/Forex/new/.rss",
    ],
}

BACKGROUND_TICKERS = [
    # Stocks
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
    "JPM", "BAC", "GS", "V", "MA",
    "XOM", "JNJ", "AMD", "NFLX", "WMT", "UBER", "CRM", "PLTR",
    # ETFs
    "SPY", "QQQ", "GLD", "SLV", "USO", "ARKK",
    # Commodities (futures)
    "GC=F", "SI=F", "CL=F", "NG=F",
]


def fetch_background_headlines(ticker: str) -> list:
    from urllib.parse import quote
    url = f"https://finance.yahoo.com/rss/headline?s={quote(ticker, safe='')}"
    headlines = []
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "SentimentFX/1.0"})
        print(f"[BACKGROUND] {ticker}: {len(feed.entries)} entries")
        for entry in feed.entries[:25]:
            try:
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                pub_date = datetime(*published[:6], tzinfo=timezone.utc) if published else datetime.now(timezone.utc)
                headlines.append({
                    "ticker": ticker.upper(),
                    "title": entry.title,
                    "source": feed.feed.get("title", "Yahoo Finance"),
                    "url": entry.link,
                    "published_at": pub_date,
                })
            except Exception as e:
                print(f"[BACKGROUND] {ticker}: entry parse error={e}")
                continue
    except Exception as e:
        print(f"[BACKGROUND] {ticker}: feed error={e}")
    return headlines


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
            browser_ua = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
            ua = browser_ua if "fxstreet.com" in url or "dailyfx.com" in url else "SentimentFX/1.0"
            feed = feedparser.parse(url, request_headers={"User-Agent": ua})
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


# ---------------------------------------------------------------------------
# GDELT 2.0 DOC API — free, no auth, deep historical coverage.  Used for
# backfill rather than the hourly scrape since per-day queries are too slow
# for live polling but give us years of history without quota concerns.
# ---------------------------------------------------------------------------

GDELT_QUERIES = {
    # Crypto
    "BTC":  '("Bitcoin" OR "BTC") sourcelang:english',
    "ETH":  '("Ethereum" OR "ether token") sourcelang:english',
    "SOL":  '("Solana" OR "SOL token") sourcelang:english',
    "XRP":  '("XRP" OR "Ripple Labs") sourcelang:english',
    "DOGE": '("Dogecoin" OR "DOGE coin") sourcelang:english',

    # FX
    "EURUSD": '("EUR/USD" OR "euro dollar exchange") sourcelang:english',
    "GBPUSD": '("GBP/USD" OR "pound dollar exchange" OR "sterling dollar") sourcelang:english',
    "USDJPY": '("USD/JPY" OR "dollar yen exchange") sourcelang:english',
    "AUDUSD": '("AUD/USD" OR "Australian dollar US") sourcelang:english',
    "USDCAD": '("USD/CAD" OR "dollar Canadian exchange") sourcelang:english',
    "USDCHF": '("USD/CHF" OR "Swiss franc dollar") sourcelang:english',
    "NZDUSD": '("NZD/USD" OR "kiwi dollar exchange") sourcelang:english',

    # Stocks (full company name is the lowest-noise filter on GDELT)
    "AAPL":  '"Apple Inc" sourcelang:english',
    "MSFT":  '"Microsoft Corporation" sourcelang:english',
    "GOOGL": '("Alphabet Inc" OR "Google parent") sourcelang:english',
    "AMZN":  '"Amazon.com" sourcelang:english',
    "META":  '"Meta Platforms" sourcelang:english',
    "NVDA":  '"Nvidia Corporation" sourcelang:english',
    "TSLA":  '"Tesla Inc" sourcelang:english',
    "JPM":   '("JPMorgan Chase" OR "JPMorgan") sourcelang:english',
    "BAC":   '"Bank of America" sourcelang:english',
    "GS":    '"Goldman Sachs" sourcelang:english',
    "V":     '"Visa Inc" sourcelang:english',
    "MA":    '"Mastercard Incorporated" sourcelang:english',
    "XOM":   '("Exxon Mobil" OR "ExxonMobil") sourcelang:english',
    "JNJ":   '"Johnson and Johnson" sourcelang:english',
    "AMD":   '("Advanced Micro Devices" OR "AMD chip") sourcelang:english',
    "NFLX":  '"Netflix Inc" sourcelang:english',
    "WMT":   '"Walmart Inc" sourcelang:english',
    "UBER":  '"Uber Technologies" sourcelang:english',
    "CRM":   '"Salesforce" sourcelang:english',
    "PLTR":  '"Palantir Technologies" sourcelang:english',

    # ETFs
    "SPY":  '("SPY ETF" OR "S&P 500 ETF") sourcelang:english',
    "QQQ":  '("QQQ ETF" OR "Nasdaq 100 ETF" OR "Invesco QQQ") sourcelang:english',
    "GLD":  '("SPDR Gold Shares" OR "GLD ETF") sourcelang:english',
    "SLV":  '("iShares Silver" OR "SLV ETF") sourcelang:english',
    "USO":  '("US Oil Fund" OR "USO ETF") sourcelang:english',
    "ARKK": '("ARK Innovation" OR "ARKK ETF") sourcelang:english',

    # Commodities (futures)
    "GC=F": '("gold futures" OR "gold price") sourcelang:english',
    "SI=F": '("silver futures" OR "silver price") sourcelang:english',
    "CL=F": '("crude oil futures" OR "WTI crude") sourcelang:english',
    "NG=F": '("natural gas futures" OR "natural gas price") sourcelang:english',
}


def fetch_gdelt_headlines(ticker: str, days: int = 30, max_per_day: int = 75) -> list:
    """Pull historical headlines from GDELT 2.0 in 1-day chunks.

    GDELT caps a single query at 250 records, so we chunk by day to capture
    high-volume tickers without truncation.  Headlines are returned in the
    same shape as fetch_headlines() / fetch_rss_headlines() so they slot into
    the existing ingestion pipeline.
    """
    query = GDELT_QUERIES.get(ticker.upper())
    if not query:
        print(f"[GDELT] no query configured for {ticker}")
        return []

    headlines = []
    seen_urls = set()
    now = datetime.now(timezone.utc).replace(hour=23, minute=59, second=59, microsecond=0)

    for day_offset in range(days):
        day_end = now - timedelta(days=day_offset)
        day_start = day_end - timedelta(days=1, seconds=-1)

        params = {
            "query": query,
            "mode": "artlist",
            "format": "json",
            "maxrecords": max_per_day,
            "sort": "datedesc",
            "startdatetime": day_start.strftime("%Y%m%d%H%M%S"),
            "enddatetime":   day_end.strftime("%Y%m%d%H%M%S"),
        }

        try:
            res = requests.get(
                GDELT_DOC_URL,
                params=params,
                timeout=20,
                headers={"User-Agent": "SentimentFX/1.0 (+https://sentimentfx.org)"},
            )
            if res.status_code != 200:
                print(f"[GDELT] {ticker} {day_start.date()}: HTTP {res.status_code}")
                time.sleep(1)
                continue

            # GDELT sometimes returns empty body when there are zero matches.
            if not res.text.strip():
                continue

            data = res.json()
        except ValueError:
            print(f"[GDELT] {ticker} {day_start.date()}: non-JSON response")
            continue
        except Exception as e:
            print(f"[GDELT] {ticker} {day_start.date()} error: {e}")
            continue

        for art in data.get("articles", []):
            url = art.get("url")
            title = (art.get("title") or "").strip()
            if not url or not title or url in seen_urls:
                continue
            seen_urls.add(url)

            seendate = art.get("seendate", "")
            try:
                pub_dt = datetime.strptime(seendate, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            except ValueError:
                pub_dt = day_start

            headlines.append({
                "ticker": ticker.upper(),
                "title": title,
                "source": art.get("domain") or "GDELT",
                "url": url,
                "published_at": pub_dt,
            })

        # Polite: GDELT has no published rate limit but bursting it gets you
        # silently throttled.  300ms per call ≈ 200/min, well below anything
        # they'd care about.
        time.sleep(0.3)

    print(f"[GDELT] {ticker}: fetched {len(headlines)} headlines over {days} days")
    return headlines