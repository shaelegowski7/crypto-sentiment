import yfinance as yf
from datetime import datetime

TICKER_MAP = {
    "BTC":  "BTC-GBP",
    "ETH":  "ETH-GBP",
    "SOL":  "SOL-GBP",
    "XRP":  "XRP-GBP",
    "DOGE": "DOGE-GBP",
}

USD_TICKERS = {}  # tickers that need USD->GBP conversion

def get_gbp_rate() -> float:
    data = yf.download("GBPUSD=X", period="2d", interval="1d", progress=False)
    if data.empty:
        return 0.79  # fallback
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
    yf_ticker = TICKER_MAP.get(ticker)
    if not yf_ticker:
        print(f"[PRICES] Unknown ticker: {ticker}")
        return None

    data = yf.download(yf_ticker, period="5d", interval="1d", progress=False)
    if data.empty:
        print(f"[PRICES] No daily price data found for {ticker}")
        return None

    gbp_rate = get_gbp_rate() if ticker in USD_TICKERS else 1.0

    try:
        latest = data.iloc[-1]
        close = float(latest["Close"].iloc[0]) if hasattr(latest["Close"], 'iloc') else float(latest["Close"])
        volume = float(latest["Volume"].iloc[0]) if hasattr(latest["Volume"], 'iloc') else float(latest["Volume"])
        date = data.index[-1].to_pydatetime()

        print(f"[PRICES] {ticker}: latest={date.date()} close={close}")
        return {
            "ticker": ticker,
            "close_price": round(close * gbp_rate, 8),
            "volume": round(volume, 2),
            "date": date
        }
    except Exception as e:
        print(f"[PRICES] {ticker}: fetch_latest error={e}")
        return None