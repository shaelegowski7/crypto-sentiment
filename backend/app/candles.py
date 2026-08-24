import math
import yfinance as yf

from .prices import TICKER_MAP, STOCK_TICKER_MAP, FX_TICKER_MAP, USD_TICKERS, get_gbp_rate

# yfinance has no native 4h interval — only 1h (60m) bars are fetched/stored.
# 4h candles are derived at read time by bucketing four consecutive 1h rows
# (see main.py's /candles endpoint). yfinance's 60m interval is also capped at
# ~730 days of history by Yahoo itself — this module is a recent, bounded
# window, not a replacement for the daily Price table's full 2019+ history.
INTRADAY_INTERVAL = "60m"
INTRADAY_BACKFILL_PERIOD = "730d"   # max practical lookback for 60m bars
INTRADAY_REFRESH_PERIOD = "2d"      # short window for the hourly top-up job


def fetch_intraday_prices(ticker: str, period: str = INTRADAY_REFRESH_PERIOD) -> list:
    """Fetch 1h OHLCV bars for `ticker` from yfinance. Same symbol-resolution
    and GBP-conversion rules as prices.fetch_prices, just at 60m granularity.
    """
    is_fx = ticker in FX_TICKER_MAP
    is_stock = ticker in STOCK_TICKER_MAP
    if is_fx:
        yf_ticker = FX_TICKER_MAP[ticker]
    elif is_stock:
        yf_ticker = STOCK_TICKER_MAP[ticker]
    else:
        yf_ticker = TICKER_MAP.get(ticker)
    if not yf_ticker:
        print(f"[CANDLES] Unknown ticker: {ticker}")
        return []

    gbp_rate = get_gbp_rate() if ticker in USD_TICKERS else 1.0

    data = yf.download(yf_ticker, period=period, interval=INTRADAY_INTERVAL, progress=False)
    if data.empty:
        print(f"[CANDLES] No intraday data for {ticker}")
        return []

    decimals = 6 if is_fx else 8
    bars = []
    for ts, row in data.iterrows():
        try:
            close = float(row["Close"].iloc[0]) if hasattr(row["Close"], 'iloc') else float(row["Close"])
            open_ = float(row["Open"].iloc[0]) if hasattr(row["Open"], 'iloc') else float(row["Open"])
            high = float(row["High"].iloc[0]) if hasattr(row["High"], 'iloc') else float(row["High"])
            low = float(row["Low"].iloc[0]) if hasattr(row["Low"], 'iloc') else float(row["Low"])
            volume = 0.0 if is_fx else (float(row["Volume"].iloc[0]) if hasattr(row["Volume"], 'iloc') else float(row["Volume"]))

            if not math.isfinite(close):
                continue
            if not math.isfinite(volume):
                volume = 0.0
            if not math.isfinite(open_):
                open_ = close
            if not math.isfinite(high):
                high = close
            if not math.isfinite(low):
                low = close

            bars.append({
                "ticker": ticker,
                "close_price": round(close * gbp_rate, decimals),
                "open_price": round(open_ * gbp_rate, decimals),
                "high_price": round(high * gbp_rate, decimals),
                "low_price": round(low * gbp_rate, decimals),
                "volume": round(volume, 2),
                "ts": ts.to_pydatetime(),
            })
        except Exception as e:
            print(f"[CANDLES] {ticker}: row parse error={e}")
            continue

    print(f"[CANDLES] {ticker}: fetched {len(bars)} 1h bars")
    return bars
