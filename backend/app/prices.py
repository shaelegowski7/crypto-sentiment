import yfinance as yf
from datetime import datetime

TICKER_MAP = {
    "BTC": "BTC-GBP",
    "ETH": "ETH-GBP",
    "SOL": "SOL-GBP",
    "XRP": "XRP-GBP"
}

def fetch_prices(ticker: str, period: str = "30d") -> list:
    yf_ticker = TICKER_MAP.get(ticker)

    if not yf_ticker:
        print(f"Unknown ticker: {ticker}")
        return []

    data = yf.download(yf_ticker, period=period, interval="1d", progress=False)

    if data.empty:
        print(f"No price data found for {ticker}")
        return []

    prices = []
    for date, row in data.iterrows():
        prices.append({
            "ticker": ticker,
            "close_price": round(float(row["Close"].iloc[0]), 2),
            "volume": round(float(row["Volume"].iloc[0]), 2),
            "date": date.to_pydatetime()
        })

    return prices