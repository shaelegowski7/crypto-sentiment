from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean, Text
from .database import Base
from datetime import datetime

class Headline(Base):
    __tablename__ = "headlines"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)        # e.g. "BTC", "ETH"
    title = Column(String)                      # the news headline
    source = Column(String)                     # e.g. "BBC", "CoinDesk"
    url = Column(String)                        # link to original article
    sentiment_score = Column(Float)             # -1 (negative) to +1 (positive)
    sentiment_label = Column(String)            # "positive", "negative", "neutral"
    published_at = Column(DateTime)             # when the article was published
    created_at = Column(DateTime, default=datetime.utcnow)  # when we scraped it
    body = Column(Text, nullable=True)          # full article text (AI-scraped sources); NOT scored by FinBERT
    source_type = Column(String, nullable=True) # coarse origin: "ai", "stocktwits", "x", ... (null = legacy RSS/HN/GNews)


class Price(Base):
    __tablename__ = "prices"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)        # e.g. "BTC-USD"
    close_price = Column(Float)                # closing price that day
    # OHLC, nullable — added for candlestick charts.  Rows written from a real
    # yfinance daily download (fetch_prices) get true values; rows written from
    # a live spot-price tick (CoinGecko / single yfinance quote, no OHLC in the
    # response) or a forward-filled gap day get open=high=low=close_price as a
    # flat placeholder.  Pre-migration historical rows are NULL until the
    # one-time /admin/prices/backfill-ohlc reconciliation pass runs.
    open_price = Column(Float, nullable=True)
    high_price = Column(Float, nullable=True)
    low_price = Column(Float, nullable=True)
    volume = Column(Float)                     # trading volume
    date = Column(DateTime)                    # date of the price
    created_at = Column(DateTime, default=datetime.utcnow)

class IntradayPrice(Base):
    """1h OHLCV bars for candlestick charts.  Separate from `Price` (which
    stays daily-only and continues to drive sentiment/backtest/correlation
    unchanged) because yfinance's 60m interval only covers ~730 days of
    history — this table is deliberately a recent, bounded window, not a
    replacement for the daily table's full 2019+ history.  4h candles are
    derived at read time by bucketing four consecutive 1h rows; there's no
    native 4h interval to fetch, so no separate 4h storage.
    """
    __tablename__ = "intraday_prices"

    id = Column(Integer, primary_key=True, index=True)
    ticker = Column(String, index=True)        # e.g. "BTC"
    ts = Column(DateTime, index=True)           # bar open time, UTC
    open_price = Column(Float)
    high_price = Column(Float)
    low_price = Column(Float)
    close_price = Column(Float)
    volume = Column(Float)
    created_at = Column(DateTime, default=datetime.utcnow)


class WaitlistEmail(Base):
    __tablename__ = "waitlist"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class Alert(Base):
    __tablename__ = "alerts"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String, index=True)        # Supabase user ID
    email = Column(String)                      # where to send the alert
    ticker = Column(String, index=True)         # e.g. "BTC"
    threshold = Column(Float)                   # e.g. 0.3 or -0.3
    direction = Column(String)                  # "above" or "below"
    active = Column(Boolean, default=True)      # deactivates after firing
    created_at = Column(DateTime, default=datetime.utcnow)
    fired_at = Column(DateTime, nullable=True)  # when it last fired

class AlertOutcome(Base):
    """One row per alert that actually fired with a tradeable recommendation.

    Logged at fire time with entry_price = latest Price row.  Settled
    asynchronously by the daily scheduler once hold_days have elapsed:
    exit_price and return_pct are filled in, settled=True.  NEUTRAL trade
    cards (no recommendation) are NOT logged here — there's nothing to
    settle.  Snapshot of the trade card as JSON is kept so we can compute
    edge per-confidence-band, per-magnitude, per-divergence-state, etc.
    later without re-running the math against changed sentiment data.
    """
    __tablename__ = "alert_outcomes"

    id = Column(Integer, primary_key=True, index=True)
    alert_id = Column(Integer, index=True, nullable=True)   # nullable so a deleted alert doesn't orphan
    user_id = Column(String, index=True, nullable=True)
    email = Column(String, nullable=True)
    ticker = Column(String, index=True)

    direction = Column(String)              # "LONG" or "SHORT"
    confidence = Column(String)             # "high" / "medium" / "low"
    hold_days = Column(Integer, default=7)

    fired_at = Column(DateTime, index=True)
    entry_price = Column(Float)

    settled = Column(Boolean, default=False, index=True)
    exit_at = Column(DateTime, nullable=True)
    exit_price = Column(Float, nullable=True)
    return_pct = Column(Float, nullable=True)   # signed; LONG positive on rise, SHORT positive on fall

    card_snapshot = Column(Text, nullable=True)    # JSON dump of the build_trade_card output
    created_at = Column(DateTime, default=datetime.utcnow)


class Brief(Base):
    """One row per day's morning brief.  Generated at 07:00 Europe/London by
    the APScheduler job, saved here before emails go out so the public archive
    always has the same content subscribers received.  The `ai_summary` is
    the Claude-generated paragraph (cheap to show free, hooks readers);
    `ticker_data` and `content_html` are the paid product (full per-ticker
    breakdown with sentiment + price + headlines + divergence flags).
    """
    __tablename__ = "briefs"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(DateTime, unique=True, index=True)   # midnight UTC of the day this brief is FOR
    ai_summary = Column(Text)                          # the Claude-generated paragraph
    ticker_data = Column(Text)                         # JSON dump of per-ticker stats
    content_html = Column(Text)                        # fully rendered email body
    tickers = Column(String)                           # comma-sep ticker list for quick listing
    sent_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.utcnow)


class SignalQuality(Base):
    """Per-ticker cached snapshot of the production backtest.

    Refreshed once daily by _refresh_signal_quality().  Consulted by
    check_alerts() before firing an alert: if `gate_ok` is False the alert
    is suppressed (with the reason logged), so users only receive signals
    on tickers where our own backtest says the strategy actually earns money
    net of transaction costs.

    Config the numbers are computed against is fixed to the production alert
    settings — signal='shift', hold_days=7, direction_mode='momentum', default
    per-category costs.  If you change alert generation to use a different
    signal, update _refresh_signal_quality to match, otherwise the gate will
    be measuring the wrong strategy.

    `oos_net_pct` and `wf_pct_folds_positive` are stored so admin/dashboard
    surfaces can render "why is this ticker (or not) firing?" without
    recomputing.  `reason` is a short human-readable string for the same
    purpose — e.g. "OOS net -4.2%" or "Insufficient data (12 headlines)".
    """
    __tablename__ = "signal_quality"

    ticker = Column(String, primary_key=True, index=True)
    gate_ok = Column(Boolean, default=False, index=True)
    oos_net_pct = Column(Float, nullable=True)
    wf_pct_folds_positive = Column(Float, nullable=True)
    wf_folds_total = Column(Integer, nullable=True)
    wf_folds_positive = Column(Integer, nullable=True)
    n_trades_full = Column(Integer, nullable=True)
    reason = Column(String, nullable=True)
    computed_at = Column(DateTime, default=datetime.utcnow, index=True)


class APIKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True, nullable=True)  # dead column, no longer written to; auth runs on key_hash. Drop in a future migration.
    key_hash = Column(String, unique=True, index=True, nullable=True)
    key_prefix = Column(String, nullable=True)  # e.g. "sfx_7b83463a"
    email = Column(String, index=True)
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    calls_used = Column(Integer, default=0)
    calls_this_month = Column(Integer, default=0)
    free_calls = Column(Integer, default=100)
    monthly_allowance = Column(Integer, default=0)
    # Internal / dogfood / partner keys.  When True, track_usage still increments
    # the counters (useful for observability) but skips the Stripe meter event
    # entirely — the key never bills regardless of volume.  Rate limits still
    # apply (30/min on /v1/*), which is 43k calls/day and plenty for any
    # realistic dogfood workload.  Grant via POST /admin/keys/mint-unlimited.
    unlimited = Column(Boolean, default=False, nullable=False)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

class TrendSignalLog(Base):
    """One row per (month, instrument) for the personal diversified trend
    strategy (see trend_signal.py) — NOT the sentiment signal, NOT a
    product feature. Written by send_trend_signal_email() before the
    month's outcome is known, so this table is the forward-test record: a
    backtest can be re-run and re-tuned after the fact, this can't. Never
    updated after the month it was written for — if the rule changes, new
    months get new rows, old ones stay as-computed at the time.
    """
    __tablename__ = "trend_signal_log"

    id = Column(Integer, primary_key=True, index=True)
    month = Column(DateTime, index=True)          # first of the month this signal is for
    ticker = Column(String, index=True)
    category = Column(String)                     # equity / bonds / commodity / fx / crypto
    signal = Column(Float)                         # -1..+1, avg sign across 60/120/250d momentum
    position = Column(Float)                       # signal * vol-scaled size, cap 2x
    price = Column(Float)                          # instrument price when computed
    vol_ann = Column(Float)                        # trailing 60d annualised vol used for sizing
    created_at = Column(DateTime, default=datetime.utcnow)


class FundingReading(Base):
    """Daily snapshot of annualised perp funding across the majors (see
    funding_monitor.py) — personal use, not a product feature.

    Logged every day regardless of whether it triggers an alert, so this
    accumulates the live forward series (the research used bulk historical
    pulls). `above_threshold` drives below->above transition detection;
    `alerted` marks the rows that actually sent mail, which is what the
    cooldown check reads.
    """
    __tablename__ = "funding_readings"

    id = Column(Integer, primary_key=True, index=True)
    ts = Column(DateTime, index=True)
    ew_annualised = Column(Float)               # equal-weight across symbols, annualised
    threshold = Column(Float)                   # hurdle in force at the time
    above_threshold = Column(Boolean, default=False, index=True)
    alerted = Column(Boolean, default=False, index=True)
    detail = Column(String, nullable=True)      # "BTCUSDT:0.1234,ETHUSDT:0.0987,..."
    created_at = Column(DateTime, default=datetime.utcnow)


class KeyResetToken(Base):
    __tablename__ = "key_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True)
    token_hash = Column(String, unique=True, index=True)
    expires_at = Column(DateTime)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)