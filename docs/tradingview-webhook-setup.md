# TradingView alert webhook

`POST /webhooks/tradingview/{secret}` receives TradingView alert deliveries,
parses what it can, and stores every delivery in the `tradingview_signals`
table. It does not place trades, call a broker, or touch TradingView back —
see the scope note in `main.py` above the route.

## Setup

1. Generate a long random secret and set it as `TRADINGVIEW_WEBHOOK_SECRET`
   in the backend's environment (Railway config vars, or `.env` locally).
   Without this set, every delivery is rejected with 404 — there's no
   fallback that accepts unauthenticated requests.
2. In TradingView, create an alert on any chart/indicator/strategy.
3. Enable **Webhook URL** under Notifications and set it to:
   ```
   https://api.sentimentfx.org/webhooks/tradingview/<TRADINGVIEW_WEBHOOK_SECRET>
   ```
4. In the alert's **Message** field, template a JSON body using TradingView's
   placeholders so the fields the receiver understands get filled in:
   ```json
   {"ticker": "{{ticker}}", "action": "buy", "price": {{close}}}
   ```
   Plain text still gets stored (with `parsed_ok: false`) rather than
   rejected — a message that doesn't match this shape doesn't lose the
   alert, it just doesn't populate `ticker`/`action`/`price`.

## Checking what's arrived

`GET /admin/tradingview-signals` (admin-only, same `ADMIN_EMAILS` gate as
`/admin/backtest-board`) lists the most recent deliveries — parsed fields,
raw body, source IP, timestamp.

## What this deliberately doesn't do

This is a receiver, not a trading bot. A row landing in `tradingview_signals`
doesn't trigger anything downstream — no order, no notification, no
automatic action. Wiring a stored signal into something that moves money or
calls a broker/exchange API is a separate, explicit decision to make later,
with its own safeguards — not something this endpoint does on its own.
