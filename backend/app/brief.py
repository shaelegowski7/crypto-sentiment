import os
import json
import logging
from datetime import datetime, timedelta, timezone
from anthropic import Anthropic
import resend
from supabase import create_client

logger = logging.getLogger(__name__)

# Mirrors FREE_TICKERS in frontend/src/App.jsx (top 3 by headline count in
# each of the 5 categories) so the brief's coverage matches what a free-tier
# user can already see on the dashboard — one consistent "top tickers" set
# across the product, not a separate curation.
TICKERS = [
    "BTC", "ETH", "SOL",             # Crypto
    "EURUSD", "USDJPY", "AUDUSD",    # FX
    "GOOGL", "AAPL", "NVDA",         # Stocks
    "SPY", "QQQ", "USO",             # ETFs
    "CL=F", "GC=F", "NG=F",          # Commodities
]
TICKER_NAMES = {
    "BTC": "Bitcoin", "ETH": "Ethereum", "SOL": "Solana",
    "EURUSD": "EUR/USD", "USDJPY": "USD/JPY", "AUDUSD": "AUD/USD",
    "GOOGL": "Alphabet", "AAPL": "Apple", "NVDA": "Nvidia",
    "SPY": "S&P 500 (SPY)", "QQQ": "Nasdaq-100 (QQQ)", "USO": "Oil Fund (USO)",
    "CL=F": "Crude Oil", "GC=F": "Gold", "NG=F": "Natural Gas",
}

# Local copy, not imported from main.py, to avoid a circular import (main.py
# imports send_morning_briefs at module level). Only used to pick the right
# price label below.
_CRYPTO_TICKERS = {"BTC", "ETH", "SOL", "XRP", "DOGE"}
_FX_TICKERS = {"EURUSD", "GBPUSD", "USDJPY", "AUDUSD", "USDCAD", "USDCHF", "NZDUSD"}


def _price_currency(ticker: str) -> str:
    """Crypto prices are stored in GBP — the yfinance ticker itself is
    GBP-quoted (e.g. BTC-GBP), so no conversion is needed. FX pairs are a
    raw exchange rate, not a currency amount. Everything else (stocks,
    ETFs, commodity futures) comes back from yfinance in native USD —
    prices.py doesn't convert non-crypto tickers (USD_TICKERS is empty) —
    so labelling those GBP would be wrong."""
    if ticker in _CRYPTO_TICKERS:
        return "GBP"
    if ticker in _FX_TICKERS:
        return "RATE"
    return "USD"


def _fmt_price(price: float | None, ticker: str) -> str:
    if price is None:
        return "N/A"
    currency = _price_currency(ticker)
    if currency == "GBP":
        return f"£{price:,.2f}"
    if currency == "USD":
        return f"${price:,.2f}"
    return f"{price:.2f}" if ticker == "USDJPY" else f"{price:.4f}"  # RATE


anthropic_client = Anthropic()  # reads ANTHROPIC_API_KEY from env


def get_supabase():
    return create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])


def fetch_ticker_data(db_session, ticker: str) -> dict:
    """Fetch sentiment, price, and top headline for a ticker over last 24h."""
    from sqlalchemy import text

    now = datetime.now(timezone.utc)
    since = now - timedelta(hours=24)
    since_48 = now - timedelta(hours=48)

    # Sentiment: avg score last 24h and prior 24h for delta
    sentiment_result = db_session.execute(text("""
        SELECT
            AVG(CASE WHEN published_at >= :since THEN sentiment_score END) AS current_sentiment,
            AVG(CASE WHEN published_at < :since AND published_at >= :since_48 THEN sentiment_score END) AS prev_sentiment
        FROM headlines
        WHERE ticker = :ticker AND published_at >= :since_48
    """), {"ticker": ticker, "since": since, "since_48": since_48}).fetchone()

    current_sentiment = round(float(sentiment_result.current_sentiment or 0), 3)
    prev_sentiment = round(float(sentiment_result.prev_sentiment or 0), 3)
    sentiment_delta = round(current_sentiment - prev_sentiment, 3)

    # Price: latest and 24h ago
    price_result = db_session.execute(text("""
        SELECT close_price, date FROM prices
        WHERE ticker = :ticker
        ORDER BY date DESC
        LIMIT 2
    """), {"ticker": ticker}).fetchall()

    current_price = float(price_result[0].close_price) if price_result else None
    prev_price = float(price_result[1].close_price) if len(price_result) > 1 else None
    price_change_pct = None
    if current_price and prev_price and prev_price != 0:
        price_change_pct = round((current_price - prev_price) / prev_price * 100, 2)

    # Top headline: highest absolute sentiment score in last 24h, fall back to 7d
    since_7d = now - timedelta(days=7)
    headline_result = db_session.execute(text("""
        SELECT title, url, sentiment_score FROM headlines
        WHERE ticker = :ticker AND published_at >= :since_7d
        ORDER BY
            CASE WHEN published_at >= :since THEN 0 ELSE 1 END,
            ABS(sentiment_score) DESC
        LIMIT 1
    """), {"ticker": ticker, "since": since, "since_7d": since_7d}).fetchone()

    top_headline = None
    if headline_result:
        top_headline = {
            "title": headline_result.title,
            "url": headline_result.url,
            "sentiment_score": round(float(headline_result.sentiment_score), 3),
        }

    # Divergence: sentiment and price moving opposite directions
    divergence = False
    if sentiment_delta is not None and price_change_pct is not None:
        divergence = (sentiment_delta > 0.05 and price_change_pct < 0) or \
                     (sentiment_delta < -0.05 and price_change_pct > 0)

    return {
        "ticker": ticker,
        "name": TICKER_NAMES[ticker],
        "currency": _price_currency(ticker),
        "current_sentiment": current_sentiment,
        "sentiment_delta": sentiment_delta,
        "current_price": current_price,
        "price_change_pct": price_change_pct,
        "top_headline": top_headline,
        "divergence": divergence,
    }


def generate_ai_summary(ticker_data: list[dict]) -> str:
    """Call Claude to generate a plain-English morning brief."""
    data_str = "\n".join([
        f"- {d['name']} ({d['ticker']}): sentiment {d['current_sentiment']:+.3f} "
        f"({'↑' if d['sentiment_delta'] >= 0 else '↓'}{abs(d['sentiment_delta']):.3f} vs yesterday), "
        f"price {_fmt_price(d['current_price'], d['ticker'])} "
        f"({'+' if (d['price_change_pct'] or 0) >= 0 else ''}{d['price_change_pct'] or 0:.2f}%), "
        f"{'⚠️ DIVERGENCE DETECTED' if d['divergence'] else 'no divergence'}"
        for d in ticker_data
    ])

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=500,
        messages=[{
            "role": "user",
            "content": f"""You are a concise financial market analyst writing a morning brief for informed retail traders, covering crypto, FX, equities, ETFs and commodities.

Here is today's sentiment and price data across {len(ticker_data)} assets:
{data_str}

Write a 4–6 sentence plain-English summary. With this many assets, don't try to mention every one —
pick out the 2–3 most notable signals (biggest divergences, sharpest sentiment moves, clearest cross-asset theme)
and interpret them, then close with one actionable observation.
Be direct and specific. Do not use bullet points. Do not use em dashes or double hyphens as punctuation —
write plain sentences with commas and periods instead. Do not repeat the raw numbers verbatim — interpret them.
Do not use phrases like "as of this morning" or "good morning". Start with the most important signal."""
        }]
    )
    return response.content[0].text.strip()


def build_email_html(ticker_data: list[dict], ai_summary: str, unsubscribe_url: str) -> str:
    """Build the HTML email body."""
    MONO = "'IBM Plex Mono','Courier New',monospace"
    SANS = "'IBM Plex Sans',system-ui,sans-serif"

    def sentiment_color(score):
        if score >= 0.1:
            return "#42c768"
        elif score <= -0.1:
            return "#f26d64"
        return "#f0b429"

    def arrow(val):
        return "↑" if val >= 0 else "↓"

    ticker_blocks = ""
    for d in ticker_data:
        divergence_banner = ""
        if d["divergence"]:
            divergence_banner = f"""
            <div style="background:rgba(240,180,41,0.08);border-left:2px solid #f0b429;padding:8px 10px;margin-top:10px;font-family:{MONO};font-size:10px;color:#f0b429;letter-spacing:0.06em;">
                &#9888; DIVERGENCE: SENTIMENT AND PRICE MOVING IN OPPOSITE DIRECTIONS
            </div>"""

        headline_block = ""
        if d["top_headline"]:
            h = d["top_headline"]
            score_color = sentiment_color(h["sentiment_score"])
            headline_block = f"""
            <div style="margin-top:10px;padding:8px 10px;background:#171c26;font-size:12px;color:#8b95a7;font-family:{SANS};">
                <a href="{h['url']}" style="color:#6cb2ff;text-decoration:none;">{h['title'][:90]}{'...' if len(h['title']) > 90 else ''}</a>
                <span style="font-family:{MONO};color:{score_color};margin-left:8px;font-size:11px;">{h['sentiment_score']:+.3f}</span>
            </div>"""

        price_str = _fmt_price(d["current_price"], d["ticker"])
        price_change_str = f"{'+' if (d['price_change_pct'] or 0) >= 0 else ''}{d['price_change_pct'] or 0:.2f}%" if d["price_change_pct"] is not None else "N/A"
        price_color = "#42c768" if (d["price_change_pct"] or 0) >= 0 else "#f26d64"
        sent_color = sentiment_color(d["current_sentiment"])

        ticker_blocks += f"""
        <div style="background:#11151d;border:1px solid #1c1f26;padding:16px;margin-bottom:10px;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;">
                <div>
                    <span style="font-family:{MONO};font-size:13px;font-weight:600;color:#e8ecf3;">{d['ticker']}</span>
                    <span style="font-family:{SANS};font-size:11px;color:#8b95a7;margin-left:8px;">{d['name']}</span>
                </div>
                <div style="text-align:right;">
                    <div style="font-family:{MONO};font-size:13px;font-weight:500;color:#e8ecf3;">{price_str}</div>
                    <div style="font-family:{MONO};font-size:11px;color:{price_color};">{price_change_str}</div>
                </div>
            </div>
            <div style="background:#171c26;padding:8px 10px;display:inline-block;">
                <span style="font-family:{MONO};font-size:10px;color:#8b95a7;letter-spacing:0.08em;">SENTIMENT</span>
                <span style="font-family:{MONO};font-size:13px;font-weight:600;color:{sent_color};margin-left:8px;">{d['current_sentiment']:+.3f}</span>
                <span style="font-family:{MONO};font-size:11px;color:#8b95a7;margin-left:6px;">{arrow(d['sentiment_delta'])}{abs(d['sentiment_delta']):.3f}</span>
            </div>
            {headline_block}
            {divergence_banner}
        </div>"""

    today = datetime.now(timezone.utc).strftime("%A, %d %B %Y").upper()

    return f"""<!DOCTYPE html>
<html>
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width,initial-scale=1">
  <link href="https://fonts.googleapis.com/css2?family=IBM+Plex+Mono:wght@400;500;600&family=IBM+Plex+Sans:wght@300;400;500&display=swap" rel="stylesheet">
</head>
<body style="margin:0;padding:0;background:#0b0e14;font-family:{SANS};">
  <div style="max-width:560px;margin:0 auto;padding:32px 20px;">

    <div style="border-bottom:1px solid #1c1f26;padding-bottom:20px;margin-bottom:24px;">
      <div style="font-family:{MONO};font-size:16px;font-weight:600;color:#f0b429;letter-spacing:0.06em;">SENTIMENTFX</div>
      <div style="font-family:{MONO};font-size:10px;color:#8b95a7;margin-top:4px;letter-spacing:0.1em;">MORNING BRIEF &middot; {today}</div>
    </div>

    <div style="background:#11151d;border:1px solid #2a2f3a;border-left:3px solid #f0b429;padding:16px 18px;margin-bottom:24px;">
      <div style="font-family:{MONO};font-size:9px;font-weight:600;color:#f0b429;letter-spacing:0.12em;margin-bottom:10px;">TODAY&#39;S SIGNAL</div>
      <div style="color:#e8ecf3;font-size:14px;line-height:1.7;font-weight:300;">{ai_summary}</div>
    </div>

    {ticker_blocks}

    <div style="border-top:1px solid #1c1f26;padding-top:20px;margin-top:8px;">
      <div style="font-family:{MONO};font-size:10px;color:#8b95a7;letter-spacing:0.06em;">
        <a href="https://app.sentimentfx.org" style="color:#6cb2ff;text-decoration:none;">OPEN DASHBOARD</a>
        &nbsp;&middot;&nbsp;
        <a href="{unsubscribe_url}" style="color:#8b95a7;text-decoration:none;">UNSUBSCRIBE</a>
      </div>
      <div style="font-family:{MONO};font-size:9px;color:#2a2f3a;margin-top:8px;letter-spacing:0.05em;">NOT FINANCIAL ADVICE</div>
    </div>

  </div>
</body>
</html>"""


def _save_brief_to_db(db_session, today_utc, ai_summary, ticker_data, content_html, sent_count):
    """Persist (or update if regenerated same day) the brief row.

    Public archive endpoints read from this table, so the on-disk content
    must match what subscribers received.  Idempotent per UTC date: running
    the test endpoint twice overwrites the same row rather than creating
    duplicates.  Imported lazily so this module stays importable when
    models/database aren't initialised (e.g. doc-gen contexts).
    """
    from .models import Brief
    existing = db_session.query(Brief).filter(Brief.date == today_utc).first()
    if existing:
        existing.ai_summary = ai_summary
        existing.ticker_data = json.dumps(ticker_data, default=str)
        existing.content_html = content_html
        existing.tickers = ",".join(d["ticker"] for d in ticker_data)
        existing.sent_count = sent_count
    else:
        db_session.add(Brief(
            date=today_utc,
            ai_summary=ai_summary,
            ticker_data=json.dumps(ticker_data, default=str),
            content_html=content_html,
            tickers=",".join(d["ticker"] for d in ticker_data),
            sent_count=sent_count,
        ))
    db_session.commit()


def send_morning_briefs(db_session):
    """Main entry point — called by APScheduler at 07:00 Europe/London.

    Now ALWAYS generates + persists the brief, even when no subscribers are
    receiving email.  Reasoning: the public archive at sentimentfx.org/brief
    needs daily content to function as an SEO funnel, and the AI generation
    cost (one Claude call per day) is trivial.  Subscribers still get email
    on top of persistence; this function just no longer bails early when
    the recipient list is empty.
    """
    resend.api_key = os.environ.get("RESEND_API_KEY")
    supabase = get_supabase()

    # Fetch ticker data first — needed regardless of subscriber count
    ticker_data = []
    seen_headline_urls = set()
    for ticker in TICKERS:
        try:
            data = fetch_ticker_data(db_session, ticker)
            if data.get("top_headline"):
                url = data["top_headline"]["url"]
                if url in seen_headline_urls:
                    data["top_headline"] = None
                else:
                    seen_headline_urls.add(url)
            ticker_data.append(data)
        except Exception as e:
            logger.error(f"Failed to fetch data for {ticker}: {e}")

    if not ticker_data:
        logger.error("No ticker data available for morning brief — skipping entirely.")
        return

    # Generate AI summary
    try:
        ai_summary = generate_ai_summary(ticker_data)
    except Exception as e:
        logger.error(f"Claude API call failed: {e}")
        ai_summary = "Market data is available in your dashboard. Sentiment and price data for today's tracked assets are shown below."

    # Resolve recipients — any tier with morning_brief_enabled=True qualifies.
    # 'brief' and 'pro' and 'data' all get the email; 'free' doesn't.
    result = supabase.from_("profiles").select("id, tier, morning_brief_enabled") \
        .in_("tier", ["brief", "pro", "data"]).eq("morning_brief_enabled", True).execute()
    profiles = result.data or []
    users_map = {}
    if profiles:
        user_ids = [p["id"] for p in profiles]
        users_result = supabase.auth.admin.list_users()
        users_map = {u.id: u.email for u in users_result if u.id in user_ids and u.email}

    # Build the canonical HTML once (used by both email and the saved archive
    # row).  Unsubscribe link is recipient-specific, so the saved version uses
    # a generic preferences link instead.
    archive_html = build_email_html(
        ticker_data, ai_summary,
        unsubscribe_url="https://app.sentimentfx.org/?preferences=brief",
    )

    sent = 0
    failed = 0
    for profile in profiles:
        user_id = profile["id"]
        email = users_map.get(user_id)
        if not email:
            continue
        unsubscribe_url = f"https://api.sentimentfx.org/api/brief/unsubscribe?user_id={user_id}"
        html = build_email_html(ticker_data, ai_summary, unsubscribe_url)
        try:
            resend.Emails.send({
                "from": "SentimentFX <hello@sentimentfx.org>",
                "to": email,
                "subject": f"☀️ Morning Brief · {datetime.now(timezone.utc).strftime('%d %b')}",
                "html": html,
            })
            sent += 1
        except Exception as e:
            logger.error(f"Failed to send brief to {email}: {e}")
            failed += 1

    # Persist regardless of recipient count — the archive is now a product.
    # Date is the UTC midnight of today so query-by-date is unambiguous.
    today_utc = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0).replace(tzinfo=None)
    try:
        _save_brief_to_db(db_session, today_utc, ai_summary, ticker_data, archive_html, sent)
    except Exception as e:
        logger.error(f"Failed to persist brief to DB: {e}")

    logger.info(f"Morning brief: persisted, sent {sent} emails ({failed} failed).")
