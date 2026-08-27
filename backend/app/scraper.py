import requests
import feedparser
import html
import time
from datetime import datetime, timedelta, timezone
import os

BASE_URL = "https://gnews.io/api/v4/search"
HN_ALGOLIA_URL = "https://hn.algolia.com/api/v1/search_by_date"

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
    "https://www.reddit.com/r/CryptoCurrency/new/.rss",
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
        "https://www.reddit.com/r/CryptoCurrency/new/.rss",
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
        "https://www.reddit.com/r/CryptoCurrency/new/.rss",
    ],
    "SOL": [
        "https://cointelegraph.com/rss/tag/solana",
        "https://cryptoslate.com/feed/",
        "https://blockworks.co/feed/",
        "https://beincrypto.com/feed/",
        "https://thedefiant.io/feed",
        "https://www.reddit.com/r/solana/new/.rss",
        "https://www.reddit.com/r/CryptoCurrency/new/.rss",
    ],
    "XRP": [
        "https://cointelegraph.com/rss/tag/xrp",
        "https://cryptoslate.com/feed/",
        "https://beincrypto.com/feed/",
        "https://www.reddit.com/r/XRP/new/.rss",
        "https://www.reddit.com/r/CryptoCurrency/new/.rss",
    ],
    "DOGE": [
        "https://cointelegraph.com/rss/tag/dogecoin",
        "https://cryptoslate.com/feed/",
        "https://beincrypto.com/feed/",
        "https://www.reddit.com/r/dogecoin/new/.rss",
        "https://www.reddit.com/r/CryptoCurrency/new/.rss",
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
    # GNews is off by default — the free tier (100 calls/day) can't cover 42
    # tickers hourly, and RSS already over-covers the same publishers.  Code is
    # kept intact so we can flip it back on with a single env var if a paid tier
    # ever makes sense.  Set GNEWS_ENABLED=true to re-enable.
    if os.getenv("GNEWS_ENABLED", "").lower() not in ("1", "true", "yes"):
        return []

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
# Hacker News Algolia search — free, no auth, generous rate limit, indexed
# back to 2007.  Replaces GDELT as the backfill source: GDELT's hard 1-req/5s
# rate limit plus multi-minute IP penalties made a 42-ticker historical sweep
# take days and frequently abort mid-run.  HN Algolia returns a 1000-hit JSON
# page per request with no auth — we can backfill a year of 42 tickers in
# ~10 minutes.
#
# Coverage trade-off vs GDELT: HN is excellent for crypto and tech stocks
# (where the front page is heavy financial discussion), moderate for FX and
# commodities, light for non-tech stocks.  Live RSS continues to fill the
# gaps going forward.
# ---------------------------------------------------------------------------

# Pre-filter queries sent to HN Algolia.  Each ticker maps to a LIST of search
# strings — Algolia's `query` parameter treats "Foo OR Bar" as a literal phrase,
# not a boolean, so to get OR semantics we run multiple queries and merge by URL.
# Keep each entry short (1-2 words) — Algolia full-text matches title and body,
# so a single noun casts a wide enough net; HN_KEYWORDS handles relevance.
HN_QUERIES = {
    # Crypto
    "BTC":  ["Bitcoin"],
    "ETH":  ["Ethereum", "ETH"],
    "SOL":  ["Solana"],
    "XRP":  ["XRP", "Ripple"],
    "DOGE": ["Dogecoin"],

    # FX — slashes get tokenized away by Algolia, so query bare currency names
    "EURUSD": ["ECB", "euro dollar", "eurozone"],
    "GBPUSD": ["sterling", "pound dollar", "Bank of England"],
    "USDJPY": ["yen", "Bank of Japan"],
    "AUDUSD": ["Aussie dollar", "RBA"],
    "USDCAD": ["Canadian dollar", "Bank of Canada"],
    "USDCHF": ["Swiss franc"],
    "NZDUSD": ["kiwi dollar", "RBNZ"],

    # Stocks — broad on purpose; HN_KEYWORDS handles the relevance filter
    "AAPL":  ["Apple"],
    "MSFT":  ["Microsoft"],
    "GOOGL": ["Google", "Alphabet"],
    "AMZN":  ["Amazon"],
    "META":  ["Meta", "Facebook"],
    "NVDA":  ["Nvidia"],
    "TSLA":  ["Tesla"],
    "JPM":   ["JPMorgan"],
    "BAC":   ["Bank of America"],
    "GS":    ["Goldman Sachs"],
    "V":     ["Visa"],
    "MA":    ["Mastercard"],
    "XOM":   ["Exxon"],
    "JNJ":   ["Johnson and Johnson"],
    "AMD":   ["AMD"],
    "NFLX":  ["Netflix"],
    "WMT":   ["Walmart"],
    "UBER":  ["Uber"],
    "CRM":   ["Salesforce"],
    "PLTR":  ["Palantir"],

    # ETFs
    "SPY":  ["S&P 500", "SPY ETF"],
    "QQQ":  ["Nasdaq 100", "QQQ"],
    "GLD":  ["SPDR Gold", "GLD ETF"],
    "SLV":  ["iShares Silver", "SLV ETF"],
    "USO":  ["US Oil Fund", "USO ETF"],
    "ARKK": ["ARK Innovation", "ARKK"],

    # Commodities (futures)
    "GC=F": ["gold price", "gold futures"],
    "SI=F": ["silver price", "silver futures"],
    "CL=F": ["crude oil", "WTI"],
    "NG=F": ["natural gas"],
}


# Title-level relevance filter applied AFTER HN returns hits.  HN Algolia's
# full-text search matches the story body too, so "Apple" can hit a story
# titled "How I quit my job" if the comments mention Apple — we drop those.
# Substring match against lowercased title.  Whitespace-padded variants ("eth ",
# " eth") avoid matching the suffix in "method"/"breath" etc.
HN_KEYWORDS = {
    "BTC":  ["bitcoin", "btc"],
    "ETH":  ["ethereum", "eth ", " eth", "ether "],
    "SOL":  ["solana"],
    "XRP":  ["xrp", "ripple"],
    "DOGE": ["dogecoin", "doge"],

    "EURUSD": ["eur/usd", "eurusd", "euro dollar", "ecb"],
    "GBPUSD": ["gbp/usd", "gbpusd", "pound sterling", "british pound", "bank of england"],
    "USDJPY": ["usd/jpy", "usdjpy", "japanese yen", " yen ", "boj"],
    "AUDUSD": ["aud/usd", "audusd", "aussie dollar", "rba"],
    "USDCAD": ["usd/cad", "usdcad", "canadian dollar", "bank of canada"],
    "USDCHF": ["usd/chf", "usdchf", "swiss franc"],
    "NZDUSD": ["nzd/usd", "nzdusd", "kiwi dollar", "rbnz"],

    "AAPL":  ["apple", "aapl", "iphone", "macbook", "ios "],
    "MSFT":  ["microsoft", "msft", "azure", "windows 11", "satya"],
    "GOOGL": ["google", "alphabet", "googl", "youtube", "android", "pixel"],
    "AMZN":  ["amazon", "amzn", "aws ", "bezos"],
    "META":  ["meta ", "facebook", "instagram", "whatsapp", "zuckerberg"],
    "NVDA":  ["nvidia", "nvda", "h100", "h200", "blackwell"],
    "TSLA":  ["tesla", "tsla", "elon", "model y", "model 3"],
    "JPM":   ["jpmorgan", "jp morgan", "dimon"],
    "BAC":   ["bank of america"],
    "GS":    ["goldman sachs"],
    "V":     ["visa inc", "visa card", "visa network"],
    "MA":    ["mastercard"],
    "XOM":   ["exxon", "exxonmobil"],
    "JNJ":   ["johnson and johnson", "j&j"],
    "AMD":   [" amd ", "amd ryzen", "ryzen", "radeon"],
    "NFLX":  ["netflix", "nflx"],
    "WMT":   ["walmart", "wmt"],
    "UBER":  ["uber "],
    "CRM":   ["salesforce", "benioff"],
    "PLTR":  ["palantir", "pltr"],

    "SPY":  ["s&p 500", "sp500", "spy etf"],
    "QQQ":  ["nasdaq 100", "qqq"],
    "GLD":  ["gold etf", "spdr gold", "gld etf"],
    "SLV":  ["silver etf", "ishares silver", "slv etf"],
    "USO":  ["us oil fund", "uso etf"],
    "ARKK": ["ark innovation", "cathie wood", "arkk"],

    "GC=F": ["gold price", "gold futures", "gold market"],
    "SI=F": ["silver price", "silver futures"],
    "CL=F": ["crude oil", "wti crude", "oil price"],
    "NG=F": ["natural gas"],
}


# HN Algolia pacing.  Official rate limit is ~10k req/h per IP; we pace at
# 0.3s between calls (~12k/h burst, ~3k/h sustained) to stay polite.  When
# Algolia does throttle it sends a clean 429 with a `Retry-After` header.
_HN_BASE_DELAY     = 0.3
_HN_MAX_RETRIES    = 5
_HN_BACKOFF_BASE   = 5
_HN_BACKOFF_CAP    = 60
_HN_HITS_PER_PAGE  = 1000   # Algolia hard cap per request


def _hn_get(params: dict, label: str) -> dict | None:
    """Single HN Algolia call with retry + exponential backoff on 429 / timeout."""
    delay = _HN_BACKOFF_BASE
    for attempt in range(_HN_MAX_RETRIES):
        try:
            res = requests.get(
                HN_ALGOLIA_URL,
                params=params,
                timeout=20,
                headers={"User-Agent": "SentimentFX/1.0 (+https://sentimentfx.org)"},
            )
            if res.status_code == 200:
                try:
                    return res.json()
                except ValueError:
                    print(f"[HN] {label}: invalid JSON")
                    return None
            if res.status_code == 429:
                wait = int(res.headers.get("Retry-After") or delay)
                print(f"[HN] {label}: 429 throttled, sleeping {wait}s (attempt {attempt + 1}/{_HN_MAX_RETRIES})")
                time.sleep(wait)
                delay = min(delay * 2, _HN_BACKOFF_CAP)
                continue
            print(f"[HN] {label}: HTTP {res.status_code}")
            return None
        except (requests.exceptions.Timeout, requests.exceptions.ConnectionError) as e:
            kind = "timeout" if isinstance(e, requests.exceptions.Timeout) else "connection error"
            print(f"[HN] {label}: {kind}, sleeping {delay}s (attempt {attempt + 1}/{_HN_MAX_RETRIES})")
            time.sleep(delay)
            delay = min(delay * 2, _HN_BACKOFF_CAP)
            continue
        except Exception as e:
            print(f"[HN] {label} error: {e}")
            return None
    print(f"[HN] {label}: giving up after {_HN_MAX_RETRIES} retries")
    return None


def _hn_extract_source(url: str) -> str:
    """Strip url down to a publisher-style source string (e.g. 'coindesk.com').

    Algolia hits without a URL are HN-native posts (Ask HN, self-text); for
    those we fall back to 'news.ycombinator.com' so they still group under one
    source in the headlines table.
    """
    from urllib.parse import urlparse
    try:
        host = (urlparse(url).hostname or "news.ycombinator.com").lower()
        return host[4:] if host.startswith("www.") else host
    except Exception:
        return "news.ycombinator.com"


def fetch_hn_headlines(
    ticker: str,
    days: int = 365,
    start_days_ago: int = 0,
    chunk_days: int = 14,
    max_per_chunk: int = _HN_HITS_PER_PAGE,
) -> list:
    """Pull historical headlines for ``ticker`` from Hacker News Algolia.

    Algolia returns up to 1000 hits per request, ordered by ``created_at_i``
    descending.  For high-volume keywords (Bitcoin, Apple) a single 1-year
    query would silently truncate at 1000, so we walk in ``chunk_days`` sized
    time windows and accumulate.  Hits are filtered post-hoc against
    HN_KEYWORDS so the title actually mentions the ticker — Algolia full-text
    matches the comment body too, which is too noisy without the filter.

    ``start_days_ago`` skips the most recent N days.  Use it to fill a
    historical gap without re-fetching what we already have: e.g.
    ``days=180, start_days_ago=180`` covers 180→360 days ago.

    Headlines come back in the same shape as the other fetchers so they slot
    straight into the existing ingestion pipeline.
    """
    queries = HN_QUERIES.get(ticker.upper())
    if not queries:
        print(f"[HN] no query configured for {ticker}")
        return []
    keywords = HN_KEYWORDS.get(ticker.upper(), [])

    now = datetime.now(timezone.utc)
    end = now - timedelta(days=start_days_ago)
    start = end - timedelta(days=days)

    headlines = []
    seen_urls = set()
    consecutive_failures = 0
    aborted = False

    cur_end = end
    while cur_end > start and not aborted:
        cur_start = max(cur_end - timedelta(days=chunk_days), start)
        any_capped = False
        chunk_kept = 0
        chunk_raw  = 0

        # Run each query for this time chunk and merge by URL.  Algolia rejects
        # boolean OR inside the `query` param ("Foo OR Bar" → 0 hits, treated
        # as literal phrase), so we fan out per keyword instead.  Cost is
        # ~len(queries) requests per chunk; we accept that for clean semantics.
        for q in queries:
            label = f"{ticker} {cur_start.date()}..{cur_end.date()} q={q!r}"
            params = {
                "query": q,
                "tags": "story",
                "numericFilters": (
                    f"created_at_i>{int(cur_start.timestamp())},"
                    f"created_at_i<={int(cur_end.timestamp())}"
                ),
                "hitsPerPage": max_per_chunk,
            }

            data = _hn_get(params, label)
            if data is None:
                consecutive_failures += 1
                if consecutive_failures >= 5:
                    print(f"[HN] {ticker}: 5 consecutive failures, aborting backfill")
                    aborted = True
                    break
                time.sleep(_HN_BASE_DELAY)
                continue
            consecutive_failures = 0

            hits = data.get("hits", []) or []
            chunk_raw += len(hits)
            if len(hits) >= max_per_chunk:
                any_capped = True

            for hit in hits:
                title = (hit.get("title") or hit.get("story_title") or "").strip()
                if not title:
                    continue
                title_lower = title.lower()
                # Drop HN meta-posts that pollute sentiment regardless of keyword match
                if title_lower.startswith(("show hn", "ask hn", "tell hn")):
                    continue
                if keywords and not any(kw in title_lower for kw in keywords):
                    continue

                url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
                if url in seen_urls:
                    continue
                seen_urls.add(url)

                created_i = hit.get("created_at_i")
                pub_dt = (
                    datetime.fromtimestamp(created_i, tz=timezone.utc)
                    if isinstance(created_i, (int, float)) else cur_start
                )

                headlines.append({
                    "ticker": ticker.upper(),
                    "title": title,
                    "source": _hn_extract_source(hit.get("url") or ""),
                    "url": url,
                    "published_at": pub_dt,
                })
                chunk_kept += 1

            time.sleep(_HN_BASE_DELAY)

        if aborted:
            break

        print(f"[HN] {ticker} {cur_start.date()}..{cur_end.date()}: "
              f"{chunk_raw} hits, {chunk_kept} kept (across {len(queries)} queries)")

        # Algolia caps at 1000 hits/request.  If any per-query response hit the
        # cap, we're silently missing older articles in this window — halve the
        # chunk and re-cover the same range.  Floor at 1 day to avoid loops.
        if any_capped and (cur_end - cur_start) > timedelta(days=1):
            chunk_days = max(1, chunk_days // 2)
            print(f"[HN] {ticker}: chunk hit Algolia 1000-cap, shrinking to {chunk_days}d windows")
            continue

        cur_end = cur_start

    print(f"[HN] {ticker}: fetched {len(headlines)} headlines over {days} days")
    return headlines


# ---------------------------------------------------------------------------
# StockTwits — finance-native social feed.  Its public JSON API returns a
# per-symbol message stream; we use it directly rather than AI-scraping the
# React SPA (which needs a headless browser and breaks on every redesign).
# No auth for the read endpoint.  Best-effort: any non-200 just yields [] and
# never breaks the scrape job.
#
# Cloudflare: this endpoint sits behind Cloudflare, which serves a "Just a
# moment..." interstitial (HTTP 403) to clients it doesn't like.  It answered
# normally for two days after launch and then began 403ing from the Railway
# host while still answering fine from a residential IP with the *same*
# headers — i.e. the block keys off the datacenter IP, not the User-Agent.
# Browser-realistic headers below are the cheap half of the mitigation; the
# circuit breaker is the half that actually matters, because without it we
# spent ~3,000 requests a day on a wall.  If STOCKTWITS_ENABLED is off or the
# breaker is open, no request is made at all.
# ---------------------------------------------------------------------------
STOCKTWITS_URL = "https://api.stocktwits.com/api/2/streams/symbol/{symbol}.json"

# Off by default since 2026-08-27 -- same treatment as GNews above, and for a
# similar reason: the source costs more than it returns.
#
# Two independent problems, either of which is disqualifying:
#   1. Cloudflare 403s the Railway IP (see the note above), so it fetches
#      nothing in production anyway.
#   2. More importantly, the content is poor input for FinBERT even when it
#      DOES arrive.  FinBERT is tuned on financial news; retail chat scores
#      near-neutral with confident misreads at the tails (sd 0.317 vs 0.489
#      for editorial headlines), and cashtag spam is attributed to every
#      ticker it names -- "$LUMN you $INTC cant $NVDA handle $AMZN the Truth"
#      was stored as an AMZN headline.  That dilutes the corpus, which is now
#      the product.
#
# Set STOCKTWITS_ENABLED=true to re-enable; the fetcher, breaker and tests
# are all still here and working.
STOCKTWITS_ENABLED = os.getenv("STOCKTWITS_ENABLED", "").lower() in ("true", "1", "yes")

# Circuit breaker.  After this many consecutive failures we stop calling out
# for the cooldown window.  Cloudflare blocks are IP-wide and persist for
# hours, so retrying every 15 minutes across 32 symbols is pure waste — and
# sustained hammering is plausibly what got the IP flagged in the first place.
STOCKTWITS_FAILURE_THRESHOLD = 6
STOCKTWITS_COOLDOWN_SECS = 3600

_stocktwits_consecutive_failures = 0
_stocktwits_blocked_until = 0.0

# One session so the connection (and any cookie Cloudflare hands out) is
# reused across the per-symbol loop rather than renegotiated 32 times.
_stocktwits_session = requests.Session()
_stocktwits_session.headers.update({
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": "https://stocktwits.com/",
    "Origin": "https://stocktwits.com",
})

# Map our tickers to StockTwits symbols.  Crypto uses the ".X" suffix; stocks
# use the plain symbol.  FX pairs and commodity futures aren't covered on
# StockTwits, so they're intentionally absent (→ no request made).
STOCKTWITS_SYMBOLS = {
    "BTC": "BTC.X", "ETH": "ETH.X", "SOL": "SOL.X", "XRP": "XRP.X", "DOGE": "DOGE.X",
    "AAPL": "AAPL", "MSFT": "MSFT", "GOOGL": "GOOGL", "AMZN": "AMZN", "META": "META",
    "NVDA": "NVDA", "TSLA": "TSLA", "JPM": "JPM", "BAC": "BAC", "GS": "GS",
    "V": "V", "MA": "MA", "XOM": "XOM", "JNJ": "JNJ", "AMD": "AMD", "NFLX": "NFLX",
    "WMT": "WMT", "UBER": "UBER", "CRM": "CRM", "PLTR": "PLTR",
    "SPY": "SPY", "QQQ": "QQQ", "GLD": "GLD", "SLV": "SLV", "USO": "USO", "ARKK": "ARKK",
}


def fetch_stocktwits_headlines(ticker: str) -> list:
    """Pull recent StockTwits messages for ``ticker`` in the standard shape.

    Each message's body becomes the ``title`` (FinBERT scores it like a
    headline); the StockTwits permalink is the dedup ``url``.  Returns [] for
    unsupported tickers or on any API error.
    """
    global _stocktwits_consecutive_failures, _stocktwits_blocked_until

    if not STOCKTWITS_ENABLED:
        return []

    symbol = STOCKTWITS_SYMBOLS.get(ticker.upper())
    if not symbol:
        return []

    now = time.time()
    if now < _stocktwits_blocked_until:
        return []          # breaker open — stay quiet, don't log per ticker

    try:
        res = _stocktwits_session.get(STOCKTWITS_URL.format(symbol=symbol), timeout=15)
        if res.status_code != 200:
            _stocktwits_consecutive_failures += 1
            print(f"[STOCKTWITS] {ticker} ({symbol}): HTTP {res.status_code} "
                  f"(failure {_stocktwits_consecutive_failures}/{STOCKTWITS_FAILURE_THRESHOLD})")
            if _stocktwits_consecutive_failures >= STOCKTWITS_FAILURE_THRESHOLD:
                _stocktwits_blocked_until = now + STOCKTWITS_COOLDOWN_SECS
                _stocktwits_consecutive_failures = 0
                print(f"[STOCKTWITS] circuit breaker OPEN — pausing all StockTwits "
                      f"requests for {STOCKTWITS_COOLDOWN_SECS // 60} min")
            return []
        messages = res.json().get("messages", []) or []
        _stocktwits_consecutive_failures = 0
    except Exception as e:
        _stocktwits_consecutive_failures += 1
        print(f"[STOCKTWITS] {ticker} ({symbol}): error={e} "
              f"(failure {_stocktwits_consecutive_failures}/{STOCKTWITS_FAILURE_THRESHOLD})")
        if _stocktwits_consecutive_failures >= STOCKTWITS_FAILURE_THRESHOLD:
            _stocktwits_blocked_until = now + STOCKTWITS_COOLDOWN_SECS
            _stocktwits_consecutive_failures = 0
            print(f"[STOCKTWITS] circuit breaker OPEN — pausing all StockTwits "
                  f"requests for {STOCKTWITS_COOLDOWN_SECS // 60} min")
        return []

    headlines = []
    for msg in messages:
        try:
            # StockTwits returns entity-escaped bodies ("You&#39;re welcome",
            # "AT&amp;T"), and those escapes were being stored verbatim and fed
            # to FinBERT as-is.  Unescape before scoring.
            body = html.unescape(msg.get("body") or "").strip()
            if not body:
                continue
            username = (msg.get("user") or {}).get("username", "user")
            msg_id = msg.get("id")
            url = f"https://stocktwits.com/{username}/message/{msg_id}"

            created = msg.get("created_at")  # e.g. "2026-07-24T12:34:56Z"
            try:
                pub_dt = datetime.strptime(created, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                pub_dt = datetime.now(timezone.utc)

            headlines.append({
                "ticker": ticker.upper(),
                "title": body,
                "source": "StockTwits",
                "url": url,
                "published_at": pub_dt,
                "source_type": "stocktwits",
            })
        except Exception as e:
            print(f"[STOCKTWITS] {ticker}: message parse error={e}")
            continue

    print(f"[STOCKTWITS] {ticker} ({symbol}): {len(headlines)} messages")
    return headlines


# ---------------------------------------------------------------------------
# X / Twitter — EXPERIMENTAL, off by default.
#
# X has no free API and aggressive anti-bot on the web app, so there is no
# robust unauthenticated path.  This fetcher is a clearly-isolated stub: it
# returns [] unless X_ENABLED is set AND an X_NITTER_BASE (a reachable Nitter
# mirror) is configured, in which case it reads that mirror's per-query RSS.
# Nitter instances are mostly dead, so expect [] in practice — this exists so
# the wiring is ready if a reliable source appears, not as a working source
# today.  Kept fully separate so it can never affect the other fetchers.
# ---------------------------------------------------------------------------
X_QUERIES = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana", "XRP": "xrp", "DOGE": "dogecoin",
}


def fetch_x_headlines(ticker: str) -> list:
    """Experimental X/Twitter fetch via a configured Nitter mirror. Usually []."""
    if os.getenv("X_ENABLED", "").lower() not in ("1", "true", "yes"):
        return []
    base = os.getenv("X_NITTER_BASE", "").rstrip("/")
    query = X_QUERIES.get(ticker.upper())
    if not base or not query:
        return []

    from urllib.parse import quote
    url = f"{base}/search/rss?f=tweets&q={quote(query)}"
    headlines = []
    try:
        feed = feedparser.parse(url, request_headers={"User-Agent": "SentimentFX/1.0"})
        for entry in feed.entries[:15]:
            try:
                published = entry.get("published_parsed") or entry.get("updated_parsed")
                pub_dt = datetime(*published[:6], tzinfo=timezone.utc) if published else datetime.now(timezone.utc)
                headlines.append({
                    "ticker": ticker.upper(),
                    "title": entry.title,
                    "source": "X",
                    "url": entry.link,
                    "published_at": pub_dt,
                    "source_type": "x",
                })
            except Exception as e:
                print(f"[X] {ticker}: entry parse error={e}")
                continue
        print(f"[X] {ticker}: {len(headlines)} tweets (experimental)")
    except Exception as e:
        print(f"[X] {ticker}: error={e}")
    return headlines
