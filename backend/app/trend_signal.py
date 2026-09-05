"""Monthly diversified trend-following signal — personal use, not a product
feature. Not built on the sentiment signal (which has no validated edge —
see FinBERT/sentiment testing history). This is the one strategy that
survived out-of-sample testing: a 26-instrument, multi-horizon momentum
rule, vol-scaled and equal-weighted, rebalanced monthly.

Full validation record (holdout Sharpe, jackknife, roll-cost modelling,
leverage limits) lives outside this repo. Summary: full-sample Sharpe
~0.6-0.75 (t~2.9-3.4), native unlevered vol ~3.6%/yr, low correlation to
SPY (~-0.13). Safe leverage ceiling is 2-3x — NOT the 50-200x available on
crypto perps, which inverts the outcome via ordinary compounding math,
independent of Sharpe. Do not lever this past 3x.

`send_trend_signal_email` is the APScheduler entry point (see main.py,
monthly cron). Every run logs each instrument's signal/position to
`TrendSignalLog` BEFORE the outcome is known, whether or not an email
recipient is configured — that log is the forward-test record; a backtest
can't produce it, only time can.
"""
import os
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import resend
import yfinance as yf

from . import models

logger = logging.getLogger(__name__)

# (ticker, yfinance symbol, asset class) — the validated universe.
UNIVERSE = [
    ("SPY", "SPY", "equity"), ("IWM", "IWM", "equity"), ("QQQ", "QQQ", "equity"),
    ("EFA", "EFA", "equity"), ("EEM", "EEM", "equity"),
    ("TLT", "TLT", "bonds"), ("IEF", "IEF", "bonds"), ("SHY", "SHY", "bonds"),
    ("LQD", "LQD", "bonds"), ("HYG", "HYG", "bonds"),
    ("GLD", "GLD", "commodity"), ("SLV", "SLV", "commodity"), ("USO", "USO", "commodity"),
    ("UNG", "UNG", "commodity"), ("CPER", "CPER", "commodity"), ("CORN", "CORN", "commodity"),
    ("WEAT", "WEAT", "commodity"), ("SOYB", "SOYB", "commodity"), ("DBC", "DBC", "commodity"),
    ("FXE", "FXE", "fx"), ("FXB", "FXB", "fx"), ("FXY", "FXY", "fx"),
    ("FXA", "FXA", "fx"), ("FXF", "FXF", "fx"),
    ("BTC", "BTC-USD", "crypto"), ("ETH", "ETH-USD", "crypto"),
]

TARGET_VOL = 0.10
LEVERAGE_CAP = 2.0
LOOKBACKS = (60, 120, 250)
TREND_SIGNAL_EMAIL = os.environ.get("TREND_SIGNAL_EMAIL")


def compute_positions() -> list[dict]:
    """One row per instrument: signal in [-1, 1], vol-scaled position, and
    the inputs (price, realised vol) so the email and the DB log agree."""
    symbols = [sym for _, sym, _ in UNIVERSE]
    raw = yf.download(symbols, period="2y", interval="1d", progress=False, auto_adjust=True)
    close = raw["Close"] if isinstance(raw.columns, pd.MultiIndex) else raw[["Close"]]

    rows = []
    for ticker, sym, category in UNIVERSE:
        if sym not in close.columns:
            logger.error(f"[TREND] no price data for {sym}, skipping")
            continue
        px = close[sym].dropna()
        if len(px) < max(LOOKBACKS) + 5:
            logger.error(f"[TREND] insufficient history for {sym} ({len(px)} days), skipping")
            continue
        signal = float(np.mean([np.sign(px.pct_change(n).iloc[-1]) for n in LOOKBACKS]))
        vol_ann = float(px.pct_change().rolling(60).std().iloc[-1] * np.sqrt(252))
        size = min(TARGET_VOL / vol_ann, LEVERAGE_CAP) if vol_ann > 0 else 0.0
        rows.append({
            "ticker": ticker, "category": category,
            "signal": signal, "position": signal * size,
            "price": float(px.iloc[-1]), "vol_ann": vol_ann,
        })
    return rows


def _direction_label(position: float) -> str:
    if position > 0.05:
        return "LONG"
    if position < -0.05:
        return "SHORT"
    return "FLAT"


def _build_email_html(rows: list[dict], month_label: str) -> str:
    order = {"equity": 0, "bonds": 1, "commodity": 2, "fx": 3, "crypto": 4}
    rows_sorted = sorted(rows, key=lambda r: (order.get(r["category"], 9), r["ticker"]))
    body = "".join(
        f"<tr>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #2a2f3a;'>{r['ticker']}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #2a2f3a;color:#8b95a7;'>{r['category']}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #2a2f3a;'>{_direction_label(r['position'])}</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #2a2f3a;text-align:right;'>{r['position']:+.2f}x</td>"
        f"<td style='padding:6px 12px;border-bottom:1px solid #2a2f3a;text-align:right;color:#8b95a7;'>{r['price']:.2f}</td>"
        f"</tr>"
        for r in rows_sorted
    )
    return f"""
    <div style="font-family:system-ui,sans-serif;max-width:600px;margin:0 auto;color:#e4e7ec;background:#0f1218;padding:24px;">
      <h2 style="margin:0 0 4px;">Monthly Trend Signal — {month_label}</h2>
      <p style="color:#8b95a7;font-size:13px;margin:0 0 20px;">
        Diversified 26-instrument trend rule. Position = sign-vote across 60/120/250-day
        momentum, sized to 10% target vol (2x cap), equal-weighted. Personal forward-test
        record — not investment advice, and not built on SentimentFX's sentiment signal.
      </p>
      <table style="width:100%;border-collapse:collapse;font-size:13px;">
        <thead>
          <tr style="text-align:left;color:#8b95a7;">
            <th style="padding:6px 12px;">Ticker</th><th style="padding:6px 12px;">Class</th>
            <th style="padding:6px 12px;">Dir</th><th style="padding:6px 12px;text-align:right;">Position</th>
            <th style="padding:6px 12px;text-align:right;">Price</th>
          </tr>
        </thead>
        <tbody>{body}</tbody>
      </table>
      <p style="color:#6b7385;font-size:11px;margin-top:20px;">
        Validated ceiling: 2-3x leverage. Full backtest record covers 2005-2026,
        full-sample Sharpe ~0.6-0.75; do not increase leverage beyond what the
        strategy's own volatility supports.
      </p>
    </div>
    """


def _log_positions(db_session, rows: list[dict], month: datetime):
    for r in rows:
        db_session.add(models.TrendSignalLog(
            month=month, ticker=r["ticker"], category=r["category"],
            signal=r["signal"], position=r["position"],
            price=r["price"], vol_ann=r["vol_ann"],
        ))
    db_session.commit()


def send_trend_signal_email(db_session):
    """APScheduler entry point — monthly. Logs positions to the DB (the
    forward-test record) whether or not TREND_SIGNAL_EMAIL is set; the log
    is the point, the email is a convenience on top of it."""
    month = datetime.now(timezone.utc).replace(day=1, hour=0, minute=0, second=0, microsecond=0, tzinfo=None)
    existing = db_session.query(models.TrendSignalLog).filter(models.TrendSignalLog.month == month).first()
    if existing:
        logger.info(f"[TREND] {month.date()} already logged, skipping recompute")
        return

    try:
        rows = compute_positions()
    except Exception as e:
        logger.error(f"[TREND] failed to compute positions: {e}")
        return
    if not rows:
        logger.error("[TREND] no positions computed, aborting")
        return

    _log_positions(db_session, rows, month)
    logger.info(f"[TREND] logged {len(rows)} positions for {month.date()}")

    if not TREND_SIGNAL_EMAIL:
        logger.info("[TREND] TREND_SIGNAL_EMAIL not set, skipping email")
        return

    resend.api_key = os.environ.get("RESEND_API_KEY")
    html = _build_email_html(rows, month.strftime("%B %Y"))
    try:
        resend.Emails.send({
            "from": "SentimentFX <hello@sentimentfx.org>",
            "to": TREND_SIGNAL_EMAIL,
            "subject": f"Trend Signal — {month.strftime('%B %Y')}",
            "html": html,
        })
        logger.info(f"[TREND] email sent to {TREND_SIGNAL_EMAIL}")
    except Exception as e:
        logger.error(f"[TREND] email send failed: {e}")
