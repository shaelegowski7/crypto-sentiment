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
    volume = Column(Float)                     # trading volume
    date = Column(DateTime)                    # date of the price
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

class KeyResetToken(Base):
    __tablename__ = "key_reset_tokens"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, index=True)
    token_hash = Column(String, unique=True, index=True)
    expires_at = Column(DateTime)
    used = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)