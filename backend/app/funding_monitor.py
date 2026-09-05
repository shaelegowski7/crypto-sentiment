"""Basis-carry season monitor — personal use, not a product feature.

Delta-neutral basis carry (long spot + short perp, collect funding) is a
real structural edge but a CONDITIONAL one: it paid +19-37%/yr in crypto
bull phases and sits below risk-free in chop. Measured post-spot-ETF it's
only ~+2%/yr over T-bills, i.e. currently not worth the exchange risk.

The decisive property that makes it worth monitoring rather than
forecasting: you never have to predict when it returns. Funding is
directly observable — you just read today's rate. This job reads it daily
and emails when it crosses back above the hurdle, so the dormant period
doesn't require manual checking.

Alerts fire on the BELOW->ABOVE transition only, with a cooldown, so a
sustained good regime doesn't mail every morning. Every reading is logged
to `FundingReading` regardless, which accumulates the live series (the
research used historical data pulled in bulk; this is the forward record).

Annualisation is computed from actual timestamps rather than assuming 3
fundings/day — some symbols moved to shorter intervals and the assumption
would silently misprice those.
"""
import os
import logging
from datetime import datetime, timedelta, timezone

import requests
import resend

from . import models

logger = logging.getLogger(__name__)

BASE = "https://fapi.binance.com/fapi/v1/fundingRate"
SYMBOLS = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "XRPUSDT", "DOGEUSDT"]
LOOKBACK_DAYS = 7
# Hurdle. Post-ETF analysis put breakeven-vs-risk-free well below this; 12%
# is where the trade paid enough to be worth the operational + counterparty
# risk in the historical record.
THRESHOLD = float(os.environ.get("FUNDING_ALERT_THRESHOLD", "0.12"))
ALERT_COOLDOWN_DAYS = 14
FUNDING_ALERT_EMAIL = os.environ.get("TREND_SIGNAL_EMAIL")


def fetch_recent_funding(symbol: str, days: int = LOOKBACK_DAYS) -> list[dict]:
    start = int((datetime.now(timezone.utc) - timedelta(days=days)).timestamp() * 1000)
    r = requests.get(BASE, params={"symbol": symbol, "startTime": start, "limit": 1000}, timeout=20)
    r.raise_for_status()
    return r.json()


def current_annualised_funding() -> dict:
    """Per-symbol and equal-weight annualised funding over the lookback.

    Annualised from the actual observed span rather than an assumed
    payments-per-day, so a symbol on a non-8h schedule isn't mispriced.
    """
    per_symbol, rates = {}, []
    for sym in SYMBOLS:
        try:
            rows = fetch_recent_funding(sym)
        except Exception as e:
            logger.error(f"[FUNDING] {sym} fetch failed: {e}")
            continue
        if len(rows) < 2:
            continue
        total = sum(float(x["fundingRate"]) for x in rows)
        span_ms = int(rows[-1]["fundingTime"]) - int(rows[0]["fundingTime"])
        span_days = span_ms / 86_400_000
        if span_days <= 0:
            continue
        ann = total * (365.0 / span_days)
        per_symbol[sym] = ann
        rates.append(ann)
    ew = sum(rates) / len(rates) if rates else None
    return {"per_symbol": per_symbol, "ew": ew}


def _build_email_html(snapshot: dict) -> str:
    ew = snapshot["ew"]
    rows = "".join(
        f"<tr><td style='padding:6px 12px;border-bottom:1px solid #2a2f3a;'>{sym.replace('USDT','')}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #2a2f3a;text-align:right;'>{v*100:+.2f}%</td></tr>"
        for sym, v in sorted(snapshot["per_symbol"].items(), key=lambda kv: -kv[1])
    )
    return f"""
    <div style="font-family:system-ui,sans-serif;max-width:560px;margin:0 auto;color:#e4e7ec;background:#0f1218;padding:24px;">
      <h2 style="margin:0 0 4px;">Basis carry is back in season</h2>
      <p style="color:#8b95a7;font-size:13px;margin:0 0 18px;">
        Trailing {LOOKBACK_DAYS}-day funding across the majors is
        <strong style="color:#4ade80;">{ew*100:+.2f}%/yr</strong> annualised,
        above the {THRESHOLD*100:.0f}% hurdle. The delta-neutral trade (long spot,
        short perp) collects this while carrying no directional exposure.
      </p>
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead><tr style="text-align:left;color:#8b95a7;">
          <th style="padding:6px 12px;">Symbol</th>
          <th style="padding:6px 12px;text-align:right;">Annualised funding</th>
        </tr></thead>
        <tbody>{rows}</tbody>
      </table>
      <p style="color:#6b7385;font-size:11px;margin-top:18px;">
        Reminder from the research: the funding stream's low volatility makes its
        Sharpe look absurd and that number is meaningless here. The real risks are
        fat-tailed and operational — exchange insolvency, short-leg liquidation,
        withdrawal freezes. Size on those. Do not chase yield onto smaller venues;
        that trades away the only risk you cannot hedge.
      </p>
    </div>
    """


def check_funding_season(db_session):
    """APScheduler entry point — daily. Logs the reading always; emails only
    on a below->above transition outside the cooldown."""
    snapshot = current_annualised_funding()
    ew = snapshot["ew"]
    if ew is None:
        logger.error("[FUNDING] no readings available, skipping")
        return

    above = ew > THRESHOLD
    now = datetime.utcnow()
    db_session.add(models.FundingReading(
        ts=now, ew_annualised=ew, threshold=THRESHOLD, above_threshold=above,
        detail=",".join(f"{k}:{v:.4f}" for k, v in snapshot["per_symbol"].items()),
    ))
    db_session.commit()
    logger.info(f"[FUNDING] EW annualised {ew*100:+.2f}%/yr, above={above}")

    if not above:
        return

    # Transition check: was the previous reading below? And are we outside cooldown?
    prev = db_session.query(models.FundingReading).filter(
        models.FundingReading.ts < now
    ).order_by(models.FundingReading.ts.desc()).first()
    if prev is not None and prev.above_threshold:
        logger.info("[FUNDING] already in season at last check, no alert")
        return

    recent_alert = db_session.query(models.FundingReading).filter(
        models.FundingReading.alerted == True,  # noqa: E712
        models.FundingReading.ts > now - timedelta(days=ALERT_COOLDOWN_DAYS),
    ).first()
    if recent_alert is not None:
        logger.info("[FUNDING] within alert cooldown, suppressing")
        return

    if not FUNDING_ALERT_EMAIL:
        logger.info("[FUNDING] crossed hurdle but TREND_SIGNAL_EMAIL unset, no email")
        return

    resend.api_key = os.environ.get("RESEND_API_KEY")
    try:
        resend.Emails.send({
            "from": "SentimentFX <hello@sentimentfx.org>",
            "to": FUNDING_ALERT_EMAIL,
            "subject": f"Basis carry back in season — {ew*100:+.1f}%/yr",
            "html": _build_email_html(snapshot),
        })
        db_session.query(models.FundingReading).filter(
            models.FundingReading.ts == now
        ).update({"alerted": True})
        db_session.commit()
        logger.info(f"[FUNDING] alert sent to {FUNDING_ALERT_EMAIL}")
    except Exception as e:
        logger.error(f"[FUNDING] email send failed: {e}")
