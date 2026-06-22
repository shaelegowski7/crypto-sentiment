"""Trade-card payload — turns a triggered sentiment alert into a complete
trade decision.

A "trade card" bundles three pieces of evidence already exposed individually:
  1. Today's sentiment + shift (from the /signal endpoint logic)
  2. Recent divergence pattern (from the /divergence endpoint logic)
  3. Backtest edge for the same signal type on this ticker

…and packages them as a single payload the user can act on without
cross-referencing three dashboards.  The card outputs a recommended
direction (LONG / SHORT / NEUTRAL), a confidence band, an entry/exit plan,
and the historical win rate of the pattern.

Designed to be the source of truth for ALL outbound alert delivery channels —
email today, Telegram/Discord/morning-brief next — so each channel just
formats the same dict instead of recomputing.

Compact by design: we re-derive the sentiment series here (rather than calling
main.get_signal/get_backtest as FastAPI routes) to avoid circular imports.
The math is identical to those endpoints — if you change the convention
there, mirror it here.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from . import models


# Sentiment shift thresholds copied from main.get_backtest / get_signal so a
# trade card "fires" on the same conditions the dashboard surfaces.
_SHIFT_THRESH = 0.05
_NOISE_FLOOR = 0.05      # individual headlines below this don't count
_DIV_THRESHOLD_S = 0.02  # divergence sentiment delta
_DIV_THRESHOLD_P = 0.5   # divergence price-change %


def _daily_weighted_sentiment(headlines: list) -> dict:
    """Volume-weighted daily mean — same recipe as the rest of the app."""
    daily = defaultdict(lambda: {"scores": [], "weights": []})
    for h in headlines:
        if h.sentiment_score is None or abs(h.sentiment_score) < _NOISE_FLOOR:
            continue
        d = h.published_at.date()
        daily[d]["scores"].append(h.sentiment_score)
        daily[d]["weights"].append(abs(h.sentiment_score))

    out = {}
    for d, v in daily.items():
        if not v["scores"]:
            continue
        w_sum = sum(v["weights"])
        out[d] = sum(s * w for s, w in zip(v["scores"], v["weights"])) / w_sum if w_sum else 0
    return out


def _daily_volume(headlines: list) -> dict:
    """Count of usable (above-noise) headlines per day — drives 'low confidence' notes."""
    vol = defaultdict(int)
    for h in headlines:
        if h.sentiment_score is None or abs(h.sentiment_score) < _NOISE_FLOOR:
            continue
        vol[h.published_at.date()] += 1
    return vol


def _compact_shift_backtest(
    daily_sentiment: dict, daily_price: dict, hold_days: int = 7
) -> Optional[dict]:
    """Trimmed backtest: long-only on bullish sentiment shifts, return summary
    stats only — no per-trade list, no equity curve.  Used to attach
    historical edge to the card without paying for the full /backtest payload."""
    common = sorted(set(daily_sentiment.keys()) & set(daily_price.keys()))
    if len(common) < 20:
        return None

    sorted_price_dates = sorted(daily_price.keys())

    signal_series = {}
    for i, d in enumerate(common):
        prior = [daily_sentiment[common[j]] for j in range(max(0, i - 7), i)]
        if len(prior) >= 3:
            shift = daily_sentiment[d] - sum(prior) / len(prior)
            if shift > _SHIFT_THRESH:
                signal_series[d] = "bullish"
            elif shift < -_SHIFT_THRESH:
                signal_series[d] = "bearish"

    trades = []
    in_trade_until = None
    for d in sorted(signal_series.keys()):
        if in_trade_until and d <= in_trade_until:
            continue
        if signal_series[d] != "bullish":
            continue
        entry_date = next((pd for pd in sorted_price_dates if pd > d), None)
        if not entry_date:
            continue
        entry_price = daily_price[entry_date]
        target = entry_date + timedelta(days=hold_days)
        exit_date = next((pd for pd in sorted_price_dates if pd >= target), sorted_price_dates[-1])
        exit_price = daily_price[exit_date]
        trades.append((exit_price - entry_price) / entry_price * 100)
        in_trade_until = exit_date

    if len(trades) < 5:
        return None   # too few historical fires to claim an edge

    winning = sum(1 for r in trades if r > 0)
    return {
        "win_rate": round(winning / len(trades), 3),
        "avg_return_pct": round(sum(trades) / len(trades), 2),
        "sample_size": len(trades),
        "hold_days": hold_days,
    }


def _divergence_window(daily_sentiment: dict, daily_price: dict) -> Optional[str]:
    """Returns 'bullish' / 'bearish' / None based on the last 14d window —
    mirrors get_divergence's last-window classification."""
    common = sorted(set(daily_sentiment.keys()) & set(daily_price.keys()))
    if len(common) < 14:
        return None
    window = common[-14:]
    p7, r7 = window[:7], window[7:]
    ps = sum(daily_sentiment[d] for d in p7) / 7
    rs = sum(daily_sentiment[d] for d in r7) / 7
    pp = sum(daily_price[d] for d in p7) / 7
    rp = sum(daily_price[d] for d in r7) / 7
    sc = rs - ps
    pc = (rp - pp) / pp * 100 if pp > 0 else 0
    if sc > _DIV_THRESHOLD_S and pc < -_DIV_THRESHOLD_P:
        return "bullish"
    if sc < -_DIV_THRESHOLD_S and pc > _DIV_THRESHOLD_P:
        return "bearish"
    return None


def _confidence_band(percentile, article_count, backtest) -> str:
    """High/medium/low based on three independent dimensions — the shift is
    historically rare, today has enough articles to trust, and the backtest
    sample is sufficient with a real edge."""
    score = 0
    if percentile is not None and percentile >= 90: score += 1
    if percentile is not None and percentile >= 75: score += 1
    if article_count >= 5: score += 1
    if article_count >= 3: score += 1
    if backtest:
        if backtest["sample_size"] >= 20: score += 1
        if backtest["win_rate"] >= 0.55: score += 1
    if score >= 5: return "high"
    if score >= 3: return "medium"
    return "low"


def build_trade_card(db: Session, ticker: str, hold_days: int = 7) -> dict:
    """Produce the full trade-card payload for a given ticker.

    Always returns a dict — even when data is too thin to recommend a trade,
    the card carries `direction='NEUTRAL'` and a populated `notes` list
    explaining why.  Callers should render unconditionally rather than
    branching on data adequacy."""
    ticker = ticker.upper()
    since = datetime.utcnow() - timedelta(days=180)

    headlines = db.query(models.Headline).filter(
        models.Headline.ticker == ticker,
        models.Headline.published_at >= since,
    ).order_by(models.Headline.published_at).all()

    prices = db.query(models.Price).filter(
        models.Price.ticker == ticker,
        models.Price.date >= since,
    ).order_by(models.Price.date).all()

    notes = []
    card_skeleton = {
        "ticker": ticker,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "direction": "NEUTRAL",
        "confidence": "low",
        "sentiment_now": None,
        "sentiment_shift_24h": None,
        "shift_magnitude": None,
        "shift_percentile": None,
        "article_count_today": 0,
        "days_since_similar": None,
        "divergence": None,
        "backtest": None,
        "entry": None,
        "exit_in_days": None,
        "notes": notes,
    }

    if len(headlines) < 10:
        notes.append(f"Only {len(headlines)} headlines in 180d — not enough to compute a signal.")
        return card_skeleton

    daily_sentiment = _daily_weighted_sentiment(headlines)
    daily_volume = _daily_volume(headlines)
    sorted_dates = sorted(daily_sentiment.keys())
    if len(sorted_dates) < 8:
        notes.append("Not enough scored daily sentiment to compute today's shift.")
        return card_skeleton

    # Today's shift: latest day vs 7-day prior mean
    most_recent = sorted_dates[-1]
    all_shifts = {}
    for i, d in enumerate(sorted_dates):
        prior = [daily_sentiment[sorted_dates[j]] for j in range(max(0, i - 7), i)]
        if len(prior) >= 3:
            all_shifts[d] = daily_sentiment[d] - sum(prior) / len(prior)
    today_shift = all_shifts.get(most_recent)
    if today_shift is None:
        notes.append("Latest day lacks enough prior history to compute shift.")
        return card_skeleton

    today_sentiment = daily_sentiment[most_recent]
    today_volume = daily_volume.get(most_recent, 0)

    # Where does today's shift rank historically?
    all_shift_values = sorted(all_shifts.values())
    percentile = round(sum(1 for s in all_shift_values if s <= today_shift) / len(all_shift_values) * 100)
    abs_percentile = round(sum(1 for s in all_shift_values if abs(s) <= abs(today_shift)) / len(all_shift_values) * 100)

    if abs_percentile >= 90:   magnitude = "extreme"
    elif abs_percentile >= 75: magnitude = "significant"
    elif abs_percentile >= 50: magnitude = "moderate"
    else:                      magnitude = "minor"

    # Days since a shift of similar absolute size — flags rarity
    larger_shifts = [d for d, s in sorted(all_shifts.items())
                     if d < most_recent and abs(s) >= abs(today_shift)]
    days_since_similar = (most_recent - larger_shifts[-1]).days if larger_shifts else None

    # Direction: combines sentiment level + shift direction.  A negative-but-up
    # shift is still meaningfully bullish vs. recent baseline.
    if today_shift > _SHIFT_THRESH and today_sentiment > 0:
        direction = "LONG"
    elif today_shift < -_SHIFT_THRESH and today_sentiment < 0:
        direction = "SHORT"
    elif today_sentiment > 0.3:
        direction = "LONG"
    elif today_sentiment < -0.3:
        direction = "SHORT"
    else:
        direction = "NEUTRAL"

    # Divergence overlay — same window the /divergence endpoint uses
    daily_price = {p.date.date(): p.close_price for p in prices}
    divergence = _divergence_window(daily_sentiment, daily_price)

    # Compact backtest — historical edge of the shift signal on this ticker
    backtest = _compact_shift_backtest(daily_sentiment, daily_price, hold_days=hold_days)

    confidence = _confidence_band(abs_percentile, today_volume, backtest)

    if today_volume < 3:
        notes.append(f"Low article count today ({today_volume}) — single-story risk.")
    if backtest is None:
        notes.append("Backtest sample too small to claim a historical edge.")
    if direction == "NEUTRAL":
        notes.append("No actionable signal — sentiment and shift are inside the noise band.")

    return {
        "ticker": ticker,
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "direction": direction,
        "confidence": confidence,
        "sentiment_now": round(today_sentiment, 3),
        "sentiment_shift_24h": round(today_shift, 3),
        "shift_magnitude": magnitude,
        "shift_percentile": abs_percentile,  # absolute, so "extreme" can be bearish too
        "shift_signed_percentile": percentile,
        "article_count_today": today_volume,
        "days_since_similar": days_since_similar,
        "divergence": divergence,
        "backtest": backtest,
        "entry": "now" if direction != "NEUTRAL" else None,
        "exit_in_days": hold_days if direction != "NEUTRAL" else None,
        "notes": notes,
    }


# ─── Renderers ─────────────────────────────────────────────────────────────
# Same card, two presentations.  Adding Telegram/Discord later is one new
# renderer — no recomputation, no risk of channels drifting on the numbers.

def _direction_color(direction: str) -> str:
    return {"LONG": "#3fb950", "SHORT": "#f85149"}.get(direction, "#8b949e")


def _confidence_color(confidence: str) -> str:
    return {"high": "#3fb950", "medium": "#f0b429", "low": "#7d8590"}.get(confidence, "#7d8590")


def format_trade_card_text(card: dict) -> str:
    """Plain-text version — what we'll send to Telegram/Discord/logs.
    Designed to be useful even if the chat client strips formatting."""
    lines = []
    if card["direction"] == "NEUTRAL":
        lines.append(f"🔔 {card['ticker']} — sentiment alert (no actionable trade)")
    else:
        emoji = "📈" if card["direction"] == "LONG" else "📉"
        lines.append(f"{emoji} {card['direction']} signal — {card['ticker']}")

    if card["sentiment_shift_24h"] is not None:
        sign = "+" if card["sentiment_shift_24h"] > 0 else ""
        lines.append(
            f"Sentiment shift: {sign}{card['sentiment_shift_24h']} "
            f"({card['shift_magnitude']}, {card['shift_percentile']}th pct)"
        )
    if card["sentiment_now"] is not None:
        sign = "+" if card["sentiment_now"] > 0 else ""
        lines.append(f"Sentiment now: {sign}{card['sentiment_now']} · "
                     f"{card['article_count_today']} articles today")

    if card["divergence"]:
        lines.append(f"Divergence: {card['divergence']} (sentiment vs price)")

    if card["backtest"]:
        bt = card["backtest"]
        lines.append(
            f"Historical edge: {int(bt['win_rate']*100)}% win rate · "
            f"avg {bt['avg_return_pct']:+.2f}% over {bt['hold_days']}d (n={bt['sample_size']})"
        )

    if card["entry"]:
        lines.append(f"Entry: {card['entry']} · Exit: in {card['exit_in_days']}d · "
                     f"Confidence: {card['confidence']}")

    if card["days_since_similar"] and card["days_since_similar"] > 7:
        lines.append(f"Last shift this size: {card['days_since_similar']}d ago")

    if card["notes"]:
        lines.append("")
        lines.append("Caveats:")
        for n in card["notes"]:
            lines.append(f"  · {n}")

    lines.append("")
    lines.append("Not financial advice. Past performance does not predict future results.")
    return "\n".join(lines)


def format_trade_card_html(card: dict) -> str:
    """Email-ready HTML.  Inline styles only — most clients strip <style>.
    Matches the existing alert-email aesthetic (dark, mono, accent yellow)."""
    t = card["ticker"]
    dir_color = _direction_color(card["direction"])
    conf_color = _confidence_color(card["confidence"])

    # Top headline
    if card["direction"] == "NEUTRAL":
        eyebrow = "Alert triggered · no actionable trade"
        headline = f"{t} sentiment alert"
    else:
        eyebrow = f"Alert triggered · {card['confidence']} confidence"
        headline = f"{card['direction']} signal — {t}"

    # Compose the body
    rows = []
    if card["sentiment_now"] is not None:
        sign = "+" if card["sentiment_now"] > 0 else ""
        rows.append(("Sentiment now", f"{sign}{card['sentiment_now']:.3f}"))
    if card["sentiment_shift_24h"] is not None:
        sign = "+" if card["sentiment_shift_24h"] > 0 else ""
        rows.append((
            "24h shift",
            f"{sign}{card['sentiment_shift_24h']:.3f} "
            f"<span style=\"color:#7d8590\">· {card['shift_magnitude']} "
            f"({card['shift_percentile']}th pct)</span>",
        ))
    if card["article_count_today"] is not None:
        rows.append(("Articles today", f"{card['article_count_today']}"))
    if card["divergence"]:
        rows.append((
            "Divergence",
            f"<span style=\"color:{_direction_color('LONG' if card['divergence']=='bullish' else 'SHORT')}\">"
            f"{card['divergence']}</span> (sentiment vs price, 14d window)",
        ))
    if card["days_since_similar"] and card["days_since_similar"] > 7:
        rows.append(("Last similar shift", f"{card['days_since_similar']} days ago"))

    rows_html = "".join(
        f"<tr><td style=\"padding:6px 0;color:#7d8590;font-size:11px;letter-spacing:0.04em;"
        f"text-transform:uppercase;\">{label}</td>"
        f"<td style=\"padding:6px 0;color:#e6edf3;font-size:13px;text-align:right;\">{value}</td></tr>"
        for label, value in rows
    )

    # Historical edge card (the "worth paying for" piece)
    bt = card["backtest"]
    if bt:
        edge_html = f"""
        <table cellpadding="0" cellspacing="0" style="width:100%;background:#0d1117;border:1px solid #21262d;border-radius:2px;margin:24px 0 8px;">
          <tr>
            <td style="padding:16px 18px;">
              <p style="font-size:10px;letter-spacing:0.15em;color:#58a6ff;text-transform:uppercase;margin:0 0 10px;">Historical edge · shift signal · {bt['hold_days']}d hold</p>
              <table cellpadding="0" cellspacing="0" style="width:100%;">
                <tr>
                  <td style="font-size:22px;font-weight:600;color:#e6edf3;">{int(bt['win_rate']*100)}%</td>
                  <td style="font-size:22px;font-weight:600;color:{_direction_color('LONG' if bt['avg_return_pct']>=0 else 'SHORT')};text-align:center;">
                    {bt['avg_return_pct']:+.2f}%
                  </td>
                  <td style="font-size:22px;font-weight:600;color:#e6edf3;text-align:right;">n={bt['sample_size']}</td>
                </tr>
                <tr>
                  <td style="font-size:9px;letter-spacing:0.1em;color:#7d8590;text-transform:uppercase;padding-top:4px;">Win rate</td>
                  <td style="font-size:9px;letter-spacing:0.1em;color:#7d8590;text-transform:uppercase;padding-top:4px;text-align:center;">Avg return</td>
                  <td style="font-size:9px;letter-spacing:0.1em;color:#7d8590;text-transform:uppercase;padding-top:4px;text-align:right;">Sample</td>
                </tr>
              </table>
            </td>
          </tr>
        </table>"""
    else:
        edge_html = ""

    # Trade-plan strip
    if card["direction"] != "NEUTRAL":
        plan_html = f"""
        <table cellpadding="0" cellspacing="0" style="width:100%;background:#0d1117;border:1px solid {dir_color};border-radius:2px;margin:8px 0 24px;">
          <tr>
            <td style="padding:14px 18px;">
              <p style="font-size:10px;letter-spacing:0.15em;color:{dir_color};text-transform:uppercase;margin:0 0 8px;">Suggested trade plan</p>
              <p style="font-size:13px;color:#e6edf3;margin:0;font-family:'Courier New',monospace;">
                Direction: <strong style="color:{dir_color}">{card['direction']}</strong>
                · Entry: <strong>{card['entry']}</strong>
                · Exit: <strong>in {card['exit_in_days']}d</strong>
                · Confidence: <strong style="color:{conf_color}">{card['confidence']}</strong>
              </p>
            </td>
          </tr>
        </table>"""
    else:
        plan_html = ""

    notes_html = ""
    if card["notes"]:
        items = "".join(f"<li style=\"margin-bottom:4px;\">{n}</li>" for n in card["notes"])
        notes_html = f"""
        <p style="font-size:10px;letter-spacing:0.1em;color:#7d8590;text-transform:uppercase;margin:24px 0 8px;">Caveats</p>
        <ul style="font-size:12px;color:#7d8590;margin:0 0 8px 16px;padding:0;line-height:1.6;">{items}</ul>"""

    return f"""<!DOCTYPE html>
<html>
<body style="margin:0;padding:0;background:#080c10;font-family:'Courier New',monospace;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background:#080c10;padding:40px 20px;">
    <tr><td align="center">
      <table width="560" cellpadding="0" cellspacing="0" style="max-width:560px;width:100%;">
        <tr><td style="border-bottom:1px solid #21262d;padding-bottom:20px;">
          <span style="font-size:13px;font-weight:600;letter-spacing:0.2em;color:#f0b429;text-transform:uppercase;">SentimentFX</span>
        </td></tr>
        <tr><td style="padding:32px 0 0;">
          <p style="font-size:10px;letter-spacing:0.2em;color:#f0b429;text-transform:uppercase;margin:0 0 14px;">— {eyebrow}</p>
          <h1 style="font-size:26px;font-weight:600;color:{dir_color};margin:0 0 24px;line-height:1.2;">{headline}</h1>
          <table cellpadding="0" cellspacing="0" style="width:100%;border-top:1px solid #21262d;border-bottom:1px solid #21262d;">{rows_html}</table>
          {plan_html}
          {edge_html}
          {notes_html}
          <table cellpadding="0" cellspacing="0" style="margin:24px 0 8px;">
            <tr><td style="background:#f0b429;border-radius:2px;">
              <a href="https://app.sentimentfx.org" style="display:inline-block;padding:12px 28px;font-size:11px;font-weight:600;letter-spacing:0.1em;color:#080c10;text-decoration:none;text-transform:uppercase;">Open Dashboard →</a>
            </td></tr>
          </table>
          <p style="font-size:10px;color:#7d8590;margin:24px 0 0;line-height:1.6;">
            ⚠ Sentiment signals are probabilistic, not certain. Past backtest performance does not predict future results.
            Position-size and risk-manage accordingly. Not financial advice.
          </p>
        </td></tr>
        <tr><td style="border-top:1px solid #21262d;padding-top:20px;margin-top:32px;">
          <p style="font-size:10px;color:#7d8590;margin:0;letter-spacing:0.05em;line-height:1.7;">
            SentimentFX · Crypto sentiment intelligence<br>
            <a href="mailto:hello@sentimentfx.org" style="color:#f0b429;text-decoration:none;">hello@sentimentfx.org</a>
          </p>
        </td></tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""
