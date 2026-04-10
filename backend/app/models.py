from sqlalchemy import Column, Integer, String, Float, DateTime
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