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

def get_gbp_rate() -> float:
    data = yf.download("GBPUSD=X", period="2d", interval="1d", progress=False)
    if data.empty:
        return 0.79
    return round(1 / float(data["Close"].iloc[-1].iloc[0]), 6)


def fetch_prices(ticker: str) -> list:
    yf_ticker = TICKER_MAP.get(ticker)
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
            volume = float(row["Volume"].iloc[0]) if hasattr(row["Volume"], 'iloc') else float(row["Volume"])
            prices.append({
                "ticker": ticker,
                "close_price": round(close * gbp_rate, 8),
                "volume": round(volume, 2),
                "date": date.to_pydatetime()
            })
        except Exception as e:
            print(f"[PRICES] {ticker}: row parse error={e}")
            continue

    print(f"[PRICES] {ticker}: fetched {len(prices)} days")
    return prices


def fetch_latest_price(ticker: str) -> dict | None:
    coin_id = COINGECKO_MAP.get(ticker.upper())
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