from pydantic import BaseModel
from datetime import datetime
from typing import Optional

class HeadlineBase(BaseModel):
    ticker: str
    title: str
    source: str
    url: str
    sentiment_score: float
    sentiment_label: str
    published_at: datetime

class HeadlineCreate(HeadlineBase):
    pass

class HeadlineResponse(HeadlineBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True


class PriceBase(BaseModel):
    ticker: str
    close_price: float
    volume: float
    date: datetime

class PriceCreate(PriceBase):
    pass

class PriceResponse(PriceBase):
    id: int
    created_at: datetime

    class Config:
        from_attributes = True