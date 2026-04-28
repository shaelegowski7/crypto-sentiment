from sqlalchemy import Column, Integer, String, Float, DateTime, Boolean
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

class APIKey(Base):
    __tablename__ = "api_keys"

    id = Column(Integer, primary_key=True, index=True)
    key = Column(String, unique=True, index=True)
    email = Column(String, index=True)
    stripe_customer_id = Column(String, nullable=True)
    stripe_subscription_id = Column(String, nullable=True)
    calls_used = Column(Integer, default=0)
    free_calls = Column(Integer, default=100)
    active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)