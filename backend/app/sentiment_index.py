"""Cross-asset sentiment index — a free, public, citable number.

Deliberately NOT a paid product. Its job is distribution: give journalists,
newsletter writers, and anyone linking to SentimentFX a live number to point
at, the way alternative.me's Crypto Fear & Greed Index or CBOE's VIX get
cited constantly.

Positioning, and why it needs to be explicit: existing "sentiment" indices in
this space (alternative.me, CFGI, DSI, Finlogix, FastBull) are built from
trader POSITIONING (long/short ratios) or price/volatility-derived signals,
not from reading news text. This index is the other thing — an aggregate of
real financial headlines, scored by FinBERT (a financial-domain NLP model),
not social-media chatter or broker order-flow. That distinction is the whole
reason this is worth publishing rather than being "yet another sentiment
index" competing on alternative.me's or DSI's home turf, where they have
years of brand recognition this can't match. State it on every response and
every render of the page — losing that framing loses the differentiation.

Scale: FinBERT's sentiment_score is -1..+1 (pos_prob - neg_prob). Mapped
linearly to 0..100 so it reads in the same register readers already
recognise from Fear & Greed-style indices: 0 = extreme negative, 50 =
neutral, 100 = extreme positive.

Category weighting is deliberate: equal-weighted across the 5 asset
classes, not across all 42 tickers. Equities have 20 tickers and commodities
have 4 — a straight average across all 42 would make the "overall" number
mostly an equity number wearing a cross-asset label. Within a category,
tickers are also equal-weighted (not headline-count-weighted), so BTC/ETH's
much higher story volume doesn't drown out DOGE/XRP inside "crypto."
"""
from datetime import datetime, timedelta, timezone

from sqlalchemy import func as sa_func

from . import models

WINDOW_DAYS = 7  # trailing window per ticker -- long enough that low-volume
                 # tickers (e.g. NZDUSD) still have a handful of headlines,
                 # short enough that the number still moves week to week.

CATEGORIES = {
    "crypto":    ["BTC", "ETH", "SOL", "XRP", "DOGE"],
    "fx":        ["EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"],
    "equity":    ["AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
                  "JPM", "BAC", "GS", "V", "MA",
                  "XOM", "JNJ", "AMD", "NFLX", "WMT", "UBER", "CRM", "PLTR"],
    "etf":       ["SPY", "QQQ", "GLD", "SLV", "USO", "ARKK"],
    "commodity": ["GC=F", "SI=F", "CL=F", "NG=F"],
}

METHODOLOGY = (
    "Built from real financial news headlines scored by FinBERT (a financial-domain "
    "NLP model) -- not trader positioning, order flow, or social-media chatter. "
    "Score per ticker = mean sentiment over the trailing 7 days. Category score = "
    "equal-weighted mean across that category's tickers (headline volume doesn't "
    "skew it). Overall = equal-weighted mean across the 5 asset classes, so equities' "
    "20 tickers can't dominate a number meant to represent all of them. "
    "Scale: 0 = extreme fear, 50 = neutral, 100 = extreme greed."
)


def _label(score: float) -> str:
    # Fear/Greed vocabulary deliberately, not Negative/Positive: it's the
    # genre-standard wording (alternative.me, CNN) that readers already parse
    # instantly on sight. Using the familiar words doesn't claim to BE those
    # products -- the page is explicit that this one is built from scored
    # news text, not positioning -- it just speaks the same visual language.
    if score >= 75:
        return "Extreme Greed"
    if score >= 55:
        return "Greed"
    if score >= 45:
        return "Neutral"
    if score >= 25:
        return "Fear"
    return "Extreme Fear"


def _to_100_scale(avg_sentiment: float) -> float:
    return round((avg_sentiment + 1) / 2 * 100, 1)


def compute_index(db_session, as_of: datetime = None) -> dict:
    """One snapshot: per-ticker 7d averages in a single grouped query, then
    rolled up into category and overall scores in Python (42 rows, trivial).
    """
    as_of = as_of or datetime.now(timezone.utc)
    since = as_of - timedelta(days=WINDOW_DAYS)

    rows = db_session.query(
        models.Headline.ticker,
        sa_func.avg(models.Headline.sentiment_score),
        sa_func.count(models.Headline.id),
    ).filter(
        models.Headline.published_at >= since,
        models.Headline.published_at <= as_of,
    ).group_by(models.Headline.ticker).all()
    per_ticker = {ticker: (float(avg), int(count)) for ticker, avg, count in rows if avg is not None}

    categories = {}
    category_scores = []
    for cat_name, tickers in CATEGORIES.items():
        present = [per_ticker[t] for t in tickers if t in per_ticker]
        if not present:
            categories[cat_name] = {"score": None, "label": "No data", "tickers_with_data": 0,
                                     "tickers_total": len(tickers), "headlines": 0}
            continue
        cat_avg_sentiment = sum(avg for avg, _ in present) / len(present)
        cat_score = _to_100_scale(cat_avg_sentiment)
        category_scores.append(cat_score)
        categories[cat_name] = {
            "score": cat_score,
            "label": _label(cat_score),
            "tickers_with_data": len(present),
            "tickers_total": len(tickers),
            "headlines": sum(count for _, count in present),
        }

    overall = round(sum(category_scores) / len(category_scores), 1) if category_scores else None

    return {
        "as_of": as_of.isoformat() if as_of.tzinfo else as_of.isoformat() + "Z",
        "window_days": WINDOW_DAYS,
        "overall": overall,
        "overall_label": _label(overall) if overall is not None else "No data",
        "categories": categories,
        "methodology": METHODOLOGY,
    }
