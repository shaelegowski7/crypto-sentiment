import os
import logging
from datetime import datetime, timedelta, timezone
from anthropic import Anthropic
import resend
from supabase import create_client

logger = logging.getLogger(__name__)

TICKERS = ["BTC", "ETH", "XRP"]
TICKER_NAMES = {"BTC": "Bitcoin", "ETH": "Ethereum", "XRP": "XRP"}

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

    # Top headline: highest absolute sentiment score in last 24h
    headline_result = db_session.execute(text("""
        SELECT title, url, sentiment_score FROM headlines
        WHERE ticker = :ticker AND published_at >= :since
        ORDER BY ABS(sentiment_score) DESC
        LIMIT 1
    """), {"ticker": ticker, "since": since}).fetchone()

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
        "current_sentiment": current_sentiment,
        "sentiment_delta": sentiment_delta,
        "current_price_gbp": current_price,
        "price_change_pct": price_change_pct,
        "top_headline": top_headline,
        "divergence": divergence,
    }


def generate_ai_summary(ticker_data: list[dict]) -> str:
    """Call Claude to generate a plain-English morning brief."""
    data_str = "\n".join([
        f"- {d['name']} ({d['ticker']}): sentiment {d['current_sentiment']:+.3f} "
        f"({'↑' if d['sentiment_delta'] >= 0 else '↓'}{abs(d['sentiment_delta']):.3f} vs yesterday), "
        f"price £{d['current_price_gbp']:,.2f} ({'+' if (d['price_change_pct'] or 0) >= 0 else ''}{d['price_change_pct'] or 0:.2f}%), "
        f"{'⚠️ DIVERGENCE DETECTED' if d['divergence'] else 'no divergence'}"
        for d in ticker_data
    ])

    response = anthropic_client.messages.create(
        model="claude-sonnet-4-20250514",
        max_tokens=400,
        messages=[{
            "role": "user",
            "content": f"""You are a concise crypto market analyst writing a morning brief for informed retail traders.

Here is today's sentiment and price data:
{data_str}

Write a 3–4 sentence plain-English summary covering the overall market mood, any notable moves, and one actionable observation. 
Be direct and specific. Do not use bullet points. Do not repeat the raw numbers — interpret them.
Do not use phrases like "as of this morning" or "good morning". Start with the most important signal."""
        }]
    )
    return response.content[0].text.strip()


def build_email_html(ticker_data: list[dict], ai_summary: str, unsubscribe_url: str) -> str:
    """Build the HTML email body."""

    def sentiment_color(score):
        if score >= 0.1:
            return "#16a34a"
        elif score <= -0.1:
            return "#dc2626"
        return "#d97706"

    def arrow(val):
        return "↑" if val >= 0 else "↓"

    ticker_blocks = ""
    for d in ticker_data:
        divergence_banner = ""
        if d["divergence"]:
            divergence_banner = """
            <div style="background:#fef3c7;border-left:3px solid #f59e0b;padding:6px 10px;margin-top:8px;border-radius:2px;font-size:12px;color:#92400e;">
                ⚠️ Divergence signal — sentiment and price moving in opposite directions
            </div>"""

        headline_block = ""
        if d["top_headline"]:
            h = d["top_headline"]
            score_color = sentiment_color(h["sentiment_score"])
            headline_block = f"""
            <div style="margin-top:10px;padding:8px 10px;background:#f8fafc;border-radius:4px;font-size:12px;color:#64748b;">
                Top headline: <a href="{h['url']}" style="color:#6366f1;text-decoration:none;">{h['title'][:90]}{'...' if len(h['title']) > 90 else ''}</a>
                <span style="color:{score_color};margin-left:6px;font-weight:600;">{h['sentiment_score']:+.3f}</span>
            </div>"""

        price_str = f"£{d['current_price_gbp']:,.2f}" if d["current_price_gbp"] else "N/A"
        price_change_str = f"{'+' if (d['price_change_pct'] or 0) >= 0 else ''}{d['price_change_pct'] or 0:.2f}%" if d["price_change_pct"] is not None else "N/A"
        price_color = "#16a34a" if (d["price_change_pct"] or 0) >= 0 else "#dc2626"
        sent_color = sentiment_color(d["current_sentiment"])

        ticker_blocks += f"""
        <div style="border:1px solid #e2e8f0;border-radius:8px;padding:16px;margin-bottom:12px;">
            <div style="display:flex;justify-content:space-between;align-items:center;">
                <div>
                    <span style="font-weight:700;font-size:15px;color:#0f172a;">{d['ticker']}</span>
                    <span style="color:#94a3b8;font-size:12px;margin-left:6px;">{d['name']}</span>
                </div>
                <div style="text-align:right;">
                    <div style="font-weight:700;font-size:15px;color:#0f172a;">{price_str}</div>
                    <div style="font-size:12px;color:{price_color};font-weight:600;">{price_change_str} 24h</div>
                </div>
            </div>
            <div style="margin-top:10px;display:flex;gap:12px;">
                <div style="background:#f1f5f9;border-radius:4px;padding:6px 10px;font-size:12px;">
                    <span style="color:#64748b;">Sentiment</span>
                    <span style="color:{sent_color};font-weight:700;margin-left:6px;">{d['current_sentiment']:+.3f}</span>
                    <span style="color:#94a3b8;margin-left:4px;">{arrow(d['sentiment_delta'])}{abs(d['sentiment_delta']):.3f}</span>
                </div>
            </div>
            {headline_block}
            {divergence_banner}
        </div>"""

    today = datetime.now(timezone.utc).strftime("%A, %d %B %Y")

    return f"""<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"></head>
<body style="margin:0;padding:0;background:#f8fafc;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;">
    <div style="max-width:560px;margin:0 auto;padding:24px 16px;">

        <!-- Header -->
        <div style="text-align:center;margin-bottom:24px;">
            <div style="font-size:20px;font-weight:800;color:#6366f1;letter-spacing:-0.5px;">SentimentFX</div>
            <div style="font-size:12px;color:#94a3b8;margin-top:2px;">Morning Brief · {today}</div>
        </div>

        <!-- AI Summary -->
        <div style="background:#6366f1;border-radius:10px;padding:18px 20px;margin-bottom:20px;">
            <div style="font-size:11px;font-weight:600;color:#c7d2fe;letter-spacing:0.05em;text-transform:uppercase;margin-bottom:8px;">Today's Signal</div>
            <div style="color:#ffffff;font-size:14px;line-height:1.6;">{ai_summary}</div>
        </div>

        <!-- Ticker blocks -->
        {ticker_blocks}

        <!-- Footer -->
        <div style="text-align:center;margin-top:24px;padding-top:16px;border-top:1px solid #e2e8f0;">
            <div style="font-size:11px;color:#94a3b8;">
                SentimentFX Pro · <a href="https://app.sentimentfx.org" style="color:#6366f1;text-decoration:none;">Open Dashboard</a>
            </div>
            <div style="font-size:11px;color:#cbd5e1;margin-top:6px;">
                <a href="{unsubscribe_url}" style="color:#cbd5e1;">Unsubscribe from morning brief</a>
            </div>
            <div style="font-size:10px;color:#e2e8f0;margin-top:8px;">Not financial advice.</div>
        </div>

    </div>
</body>
</html>"""


def send_morning_briefs(db_session):
    """Main entry point — called by APScheduler."""
    resend.api_key = os.environ["RESEND_API_KEY"]
    supabase = get_supabase()

    # Fetch all Pro users with morning brief enabled
    result = supabase.from_("profiles").select("id, morning_brief_enabled").eq("tier", "pro").eq("morning_brief_enabled", True).execute()
    profiles = result.data

    if not profiles:
        logger.info("No Pro users with morning brief enabled.")
        return

    # Fetch user emails from auth.users via service key
    user_ids = [p["id"] for p in profiles]
    users_result = supabase.auth.admin.list_users()
    users_map = {u.id: u.email for u in users_result if u.id in user_ids and u.email}

    if not users_map:
        logger.info("No emails resolved for Pro users.")
        return

    # Fetch ticker data once for all users
    ticker_data = []
    for ticker in TICKERS:
        try:
            data = fetch_ticker_data(db_session, ticker)
            ticker_data.append(data)
        except Exception as e:
            logger.error(f"Failed to fetch data for {ticker}: {e}")

    if not ticker_data:
        logger.error("No ticker data available for morning brief.")
        return

    # Generate AI summary once (same for all users)
    try:
        ai_summary = generate_ai_summary(ticker_data)
    except Exception as e:
        logger.error(f"Claude API call failed: {e}")
        ai_summary = "Market data is available in your dashboard. Sentiment and price data for BTC, ETH, and XRP are shown below."

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
                "subject": f"☀️ Morning Brief — {datetime.now(timezone.utc).strftime('%d %b')}",
                "html": html,
            })
            sent += 1
        except Exception as e:
            logger.error(f"Failed to send brief to {email}: {e}")
            failed += 1

    logger.info(f"Morning brief sent: {sent} success, {failed} failed.")