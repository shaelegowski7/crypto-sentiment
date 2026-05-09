import yfinance as yf
import requests
from datetime import datetime, timezone

TICKER_MAP = {
    "BTC":  "BTC-GBP",
    "ETH":  "ETH-GBP",
    "SOL":  "SOL-GBP",
    "XRP":  "XRP-GBP",
    "DOGE": "DOGE-GBP",
}

COINGECKO_MAP = {
    "BTC":  "bitcoin",
    "ETH":  "ethereum",
    "SOL":  "solana",
    "XRP":  "ripple",
    "DOGE": "dogecoin",
}

USD_TICKERS = {}

FX_TICKER_MAP = {
    "EURUSD": "EURUSD=X",
    "GBPUSD": "GBPUSD=X",
    "USDJPY": "USDJPY=X",
    "AUDUSD": "AUDUSD=X",
    "USDCAD": "USDCAD=X",
    "USDCHF": "USDCHF=X",
    "NZDUSD": "NZDUSD=X",
}

def get_gbp_rate() -> float:
    data = yf.download("GBPUSD=X", period="2d", interval="1d", progress=False)
    if data.empty:
        return 0.79
    return round(1 / float(data["Close"].iloc[-1].iloc[0]), 6)


def fetch_prices(ticker: str) -> list:
    is_fx = ticker in FX_TICKER_MAP
    yf_ticker = FX_TICKER_MAP.get(ticker) if is_fx else TICKER_MAP.get(ticker)
    if not yf_ticker:
        print(f"[PRICES] Unknown ticker: {ticker}")
        return []

    gbp_rate = get_gbp_rate() if ticker in USD_TICKERS else 1.0

    data = yf.download(yf_ticker, start="2019-01-01", interval="1d", progress=False)
    if data.empty:
        print(f"[PRICES] No data found for {ticker}")
        return []

    prices = []
    for date, row in data.iterrows():
        try:
            close = float(row["Close"].iloc[0]) if hasattr(row["Close"], 'iloc') else float(row["Close"])
            volume = 0.0 if is_fx else (float(row["Volume"].iloc[0]) if hasattr(row["Volume"], 'iloc') else float(row["Volume"]))
            prices.append({
                "ticker": ticker,
                "close_price": round(close * gbp_rate, 6 if is_fx else 8),
                "volume": round(volume, 2),
                "date": date.to_pydatetime()
            })
        except Exception as e:
            print(f"[PRICES] {ticker}: row parse error={e}")
            continue

    print(f"[PRICES] {ticker}: fetched {len(prices)} days")
    return prices


def fetch_latest_prices_all() -> dict:
    """Fetch latest prices for all tickers (crypto via CoinGecko, FX via yfinance)."""
    today = datetime.now(timezone.utc).date()
    result = {}

    coin_ids = ",".join(COINGECKO_MAP.values())
    try:
        res = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": coin_ids,
                "vs_currencies": "gbp",
                "include_24hr_vol": "true",
            },
            timeout=10
        )
        print(f"[COINGECKO] batch response: {res.status_code}")
        data = res.json()
        for ticker, coin_id in COINGECKO_MAP.items():
            if coin_id not in data:
                print(f"[PRICES] {ticker}: missing from CoinGecko response")
                continue
            price = data[coin_id]["gbp"]
            volume = data[coin_id].get("gbp_24h_vol", 0)
            result[ticker] = {
                "ticker": ticker,
                "close_price": round(price, 8),
                "volume": round(volume, 2),
                "date": today,
            }
            print(f"[PRICES] {ticker}: live={today} close={price}")
    except Exception as e:
        print(f"[PRICES] CoinGecko batch error={e}")

    for ticker, yf_ticker in FX_TICKER_MAP.items():
        try:
            data = yf.download(yf_ticker, period="5d", interval="1d", progress=False)
            if data.empty:
                print(f"[PRICES] {ticker}: no yfinance data")
                continue
            close_series = data["Close"]
            close = float(close_series.iloc[-1].iloc[0]) if hasattr(close_series.iloc[-1], 'iloc') else float(close_series.iloc[-1])
            result[ticker] = {
                "ticker": ticker,
                "close_price": round(close, 6),
                "volume": 0.0,
                "date": today,
            }
            print(f"[PRICES] {ticker}: live={today} close={close}")
        except Exception as e:
            print(f"[PRICES] {ticker}: yfinance error={e}")

    return result


def fetch_latest_price(ticker: str) -> dict | None:
    t = ticker.upper()
    if t in FX_TICKER_MAP:
        try:
            data = yf.download(FX_TICKER_MAP[t], period="5d", interval="1d", progress=False)
            if data.empty:
                return None
            close_series = data["Close"]
            close = float(close_series.iloc[-1].iloc[0]) if hasattr(close_series.iloc[-1], 'iloc') else float(close_series.iloc[-1])
            today = datetime.now(timezone.utc).date()
            print(f"[PRICES] {t}: live={today} close={close}")
            return {"ticker": t, "close_price": round(close, 6), "volume": 0.0, "date": today}
        except Exception as e:
            print(f"[PRICES] {t}: yfinance error={e}")
            return None

    coin_id = COINGECKO_MAP.get(t)
    if not coin_id:
        print(f"[PRICES] Unknown ticker: {ticker}")
        return None

    try:
        res = requests.get(
            "https://api.coingecko.com/api/v3/simple/price",
            params={
                "ids": coin_id,
                "vs_currencies": "gbp",
                "include_24hr_vol": "true",
            },
            timeout=10
        )
        print(f"[COINGECKO] response: {res.status_code} {res.text}")
        data = res.json()
        price = data[coin_id]["gbp"]
        volume = data[coin_id].get("gbp_24h_vol", 0)
        today = datetime.now(timezone.utc).date()

        print(f"[PRICES] {ticker}: live={today} close={price}")
        return {
            "ticker": ticker.upper(),
            "close_price": round(price, 8),
            "volume": round(volume, 2),
            "date": today,
        }
    except Exception as e:
        print(f"[PRICES] {ticker}: CoinGecko error={e}")
        return None