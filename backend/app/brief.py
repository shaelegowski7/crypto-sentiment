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
        model="claude-sonnet-4-6",
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
    MONO = "'IBM Plex Mono','Courier New',monospace"
    SANS = "'IBM Plex Sans',system-ui,sans-serif"

    def sentiment_color(score):
        if score >= 0.1:
            return "#3fb950"
        elif score <= -0.1:
            return "#f85149"
        return "#f0b429"

    def arrow(val):
        return "↑" if val >= 0 else "↓"

    ticker_blocks = ""
    for d in ticker_data:
        divergence_banner = ""
        if d["divergence"]:
            divergence_banner = f"""
            <div style="background:rgba(240,180,41,0.08);border-left:2px solid #f0b429;padding:8px 10px;margin-top:10px;font-family:{MONO};font-size:10px;color:#f0b429;letter-spacing:0.06em;">
                &#9888; DIVERGENCE &mdash; SENTIMENT AND PRICE MOVING IN OPPOSITE DIRECTIONS
            </div>"""

        headline_block = ""
        if d["top_headline"]:
            h = d["top_headline"]
            score_color = sentiment_color(h["sentiment_score"])
            headline_block = f"""
            <div style="margin-top:10px;padding:8px 10px;background:#161b22;font-size:12px;color:#7d8590;font-family:{SANS};">
                <a href="{h['url']}" style="color:#58a6ff;text-decoration:none;">{h['title'][:90]}{'...' if len(h['title']) > 90 else ''}</a>
                <span style="font-family:{MONO};color:{score_color};margin-left:8px;font-size:11px;">{h['sentiment_score']:+.3f}</span>
            </div>"""

        is_fx = d["current_price_gbp"] is not None and d["ticker"] in ("EURUSD", "GBPUSD", "USDJPY")
        if d["current_price_gbp"] is not None:
            price_str = f"{d['current_price_gbp']:.4f}" if is_fx else f"£{d['current_price_gbp']:,.2f}"
        else:
            price_str = "N/A"
        price_change_str = f"{'+' if (d['price_change_pct'] or 0) >= 0 else ''}{d['price_change_pct'] or 0:.2f}%" if d["price_change_pct"] is not None else "N/A"
        price_color = "#3fb950" if (d["price_change_pct"] or 0) >= 0 else "#f85149"
        sent_color = sentiment_color(d["current_sentiment"])

        ticker_blocks += f"""
        <div style="background:#0d1117;border:1px solid #21262d;padding:16px;margin-bottom:10px;">
            <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:12px;">
                <div>
                    <span style="font-family:{MONO};font-size:13px;font-weight:600;color:#e6edf3;">{d['ticker']}</span>
                    <span style="font-family:{SANS};font-size:11px;color:#7d8590;margin-left:8px;">{d['name']}</span>
                </div>
                <div style="text-align:right;">
                    <div style="font-family:{MONO};font-size:13px;font-weight:500;color:#e6edf3;">{price_str}</div>
                    <div style="font-family:{MONO};font-size:11px;color:{price_color};">{price_change_str}</div>
                </div>
            </div>
            <div style="background:#161b22;padding:8px 10px;display:inline-block;">
                <span style="font-family:{MONO};font-size:10px;color:#7d8590;letter-spacing:0.08em;">SENTIMENT</span>
                <span style="font-family:{MONO};font-size:13px;font-weight:600;color:{sent_color};margin-left:8px;">{d['current_sentiment']:+.3f}</span>
                <span style="font-family:{MONO};font-size:11px;color:#7d8590;margin-left:6px;">{arrow(d['sentiment_delta'])}{abs(d['sentiment_delta']):.3f}</span>
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
<body style="margin:0;padding:0;background:#080c10;font-family:{SANS};">
  <div style="max-width:560px;margin:0 auto;padding:32px 20px;">

    <div style="border-bottom:1px solid #21262d;padding-bottom:20px;margin-bottom:24px;">
      <div style="font-family:{MONO};font-size:16px;font-weight:600;color:#f0b429;letter-spacing:0.06em;">SENTIMENTFX</div>
      <div style="font-family:{MONO};font-size:10px;color:#7d8590;margin-top:4px;letter-spacing:0.1em;">MORNING BRIEF &middot; {today}</div>
    </div>

    <div style="background:#0d1117;border:1px solid #30363d;border-left:3px solid #f0b429;padding:16px 18px;margin-bottom:24px;">
      <div style="font-family:{MONO};font-size:9px;font-weight:600;color:#f0b429;letter-spacing:0.12em;margin-bottom:10px;">TODAY&#39;S SIGNAL</div>
      <div style="color:#e6edf3;font-size:14px;line-height:1.7;font-weight:300;">{ai_summary}</div>
    </div>

    {ticker_blocks}

    <div style="border-top:1px solid #21262d;padding-top:20px;margin-top:8px;">
      <div style="font-family:{MONO};font-size:10px;color:#7d8590;letter-spacing:0.06em;">
        <a href="https://app.sentimentfx.org" style="color:#58a6ff;text-decoration:none;">OPEN DASHBOARD</a>
        &nbsp;&middot;&nbsp;
        <a href="{unsubscribe_url}" style="color:#7d8590;text-decoration:none;">UNSUBSCRIBE</a>
      </div>
      <div style="font-family:{MONO};font-size:9px;color:#30363d;margin-top:8px;letter-spacing:0.05em;">NOT FINANCIAL ADVICE</div>
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