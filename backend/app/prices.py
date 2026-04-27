import yfinance as yf
from datetime import datetime

TICKER_MAP = {
    "BTC":  "BTC-GBP",
    "ETH":  "ETH-GBP",
    "SOL":  "SOL-GBP",
    "XRP":  "XRP-GBP",
    "DOGE": "DOGE-GBP",
    "LTC":  "LTC-GBP",
    "TRX":  "TRX-GBP",
    "XLM":  "XLM-GBP",
    "PEPE": "PEPE-USD",  # no GBP pair, convert manually
}

USD_TICKERS = {"PEPE"}  # tickers that need USD->GBP conversion

def get_gbp_rate() -> float:
    data = yf.download("GBPUSD=X", period="2d", interval="1d", progress=False)
    if data.empty:
        return 0.79  # fallback
    return round(1 / float(data["Close"].iloc[-1].iloc[0]), 6)


def fetch_prices(ticker: str, period: str = "2y") -> list:
    yf_ticker = TICKER_MAP.get(ticker)
    if not yf_ticker:
        print(f"Unknown ticker: {ticker}")
        return []

    data = yf.download("GBPUSD=X", period="2d", interval="1h", progress=False)
    if data.empty:
        print(f"No price data found for {ticker}")
        return []

    gbp_rate = get_gbp_rate() if ticker in USD_TICKERS else 1.0

    prices = []
    for date, row in data.iterrows():
        prices.append({
            "ticker": ticker,
            "close_price": round(float(row["Close"].iloc[0]) * gbp_rate, 8),
            "volume": round(float(row["Volume"].iloc[0]), 2),
            "date": date.to_pydatetime()
        })

    return prices


def fetch_latest_price(ticker: str) -> dict | None:
    yf_ticker = TICKER_MAP.get(ticker)
    if not yf_ticker:
        print(f"Unknown ticker: {ticker}")
        return None

    data = yf.download(yf_ticker, period="2d", interval="1h", progress=False)
    if data.empty:
        print(f"No intraday price data found for {ticker}")
        return None

    gbp_rate = get_gbp_rate() if ticker in USD_TICKERS else 1.0

    latest = data.iloc[-1]
    return {
        "ticker": ticker,
        "close_price": round(float(latest["Close"].iloc[0]) * gbp_rate, 8),
        "volume": round(float(latest["Volume"].iloc[0]), 2),
        "date": data.index[-1].to_pydatetime()
    }