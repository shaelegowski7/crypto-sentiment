import math
from datetime import datetime, timedelta

import yfinance as yf

from .prices import TICKER_MAP, STOCK_TICKER_MAP, FX_TICKER_MAP, USD_TICKERS, get_gbp_rate

# yfinance has no native 4h interval — only 1h (60m) bars are fetched/stored.
# 4h candles are derived at read time by bucketing four consecutive 1h rows
# (see main.py's /candles endpoint). yfinance's 60m interval is also capped at
# ~730 days of history by Yahoo itself — this module is a recent, bounded
# window, not a replacement for the daily Price table's full 2019+ history.
INTRADAY_INTERVAL = "60m"
INTRADAY_REFRESH_PERIOD = "2d"      # short window for the hourly top-up job

# Hard ceiling on how far back 60m bars can legitimately go (Yahoo's own cap).
# Anything older than this in intraday_prices is by definition not real 1h data.
INTRADAY_MAX_LOOKBACK_DAYS = 729

# Sentinel meaning "go back as far as 60m data is available". Deliberately NOT
# a period string: yfinance only accepts a fixed vocabulary of periods
# (1d/5d/1mo/3mo/6mo/1y/2y/5y/10y/ytd/max), and an unrecognised value like
# "730d" doesn't error — some versions silently fall back to DAILY bars, which
# then land in the hourly table as one row per day. That happened once and
# contaminated the table; using an explicit start date avoids the whole class
# of problem.
INTRADAY_BACKFILL_PERIOD = "__max_intraday__"


def fetch_intraday_prices(ticker: str, period: str = INTRADAY_REFRESH_PERIOD) -> list:
    """Fetch 1h OHLCV bars for `ticker` from yfinance. Same symbol-resolution
    and GBP-conversion rules as prices.fetch_prices, just at 60m granularity.

    Guards against yfinance handing back daily bars (see
    INTRADAY_BACKFILL_PERIOD): any row older than the 60m availability window
    is dropped, and a response whose bars are spaced a full day apart is
    rejected outright rather than polluting the intraday table.
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

    earliest_allowed = datetime.utcnow() - timedelta(days=INTRADAY_MAX_LOOKBACK_DAYS)
    if period == INTRADAY_BACKFILL_PERIOD:
        data = yf.download(yf_ticker, start=earliest_allowed.date(),
                           interval=INTRADAY_INTERVAL, progress=False)
    else:
        data = yf.download(yf_ticker, period=period,
                           interval=INTRADAY_INTERVAL, progress=False)
    if data.empty:
        print(f"[CANDLES] No intraday data for {ticker}")
        return []

    # Sanity gate: real 60m bars are ~1h apart. If the median gap is a day or
    # more, yfinance gave us daily bars and writing them would corrupt the
    # hourly table — bail rather than store them.
    if len(data) >= 3:
        idx = data.index
        gaps = [(idx[i + 1] - idx[i]).total_seconds() for i in range(len(idx) - 1)]
        gaps.sort()
        median_gap = gaps[len(gaps) // 2]
        if median_gap >= 23 * 3600:
            print(f"[CANDLES] {ticker}: rejected — median bar gap {median_gap/3600:.1f}h "
                  f"(expected ~1h); yfinance returned non-intraday data")
            return []

    decimals = 6 if is_fx else 8
    bars = []
    dropped_old = 0
    for ts, row in data.iterrows():
        try:
            # Belt-and-braces alongside the median-gap gate above: 60m bars
            # can't legitimately predate Yahoo's window, so anything older is
            # bad data regardless of how it got here.
            bar_dt = ts.to_pydatetime()
            if (bar_dt.replace(tzinfo=None) if bar_dt.tzinfo else bar_dt) < earliest_allowed:
                dropped_old += 1
                continue
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

    suffix = f" ({dropped_old} pre-window rows dropped)" if dropped_old else ""
    print(f"[CANDLES] {ticker}: fetched {len(bars)} 1h bars{suffix}")
    return bars
