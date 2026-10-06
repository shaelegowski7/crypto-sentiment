# SentimentFX

A news-sentiment dataset for 42 markets across crypto, FX, stocks, ETFs and commodities. SentimentFX scrapes financial headlines, scores each one with FinBERT (a finance-tuned transformer model), and stores the scores next to daily and intraday prices. You can read the data on a dashboard, through a metered REST API, or as tools over MCP for Claude and other AI clients.

The product is the scored archive and live feed. It is not a price forecast: the dashboard shows correlation and backtest results so you can judge the signal yourself, and it makes no claim that sentiment predicts returns.

**Live:** [sentimentfx.org](https://sentimentfx.org) · [app.sentimentfx.org](https://app.sentimentfx.org) · [developers.sentimentfx.org](https://developers.sentimentfx.org) · [status.sentimentfx.org](https://status.sentimentfx.org)

---

## What it does

- Pulls headlines from RSS feeds every 15 minutes (CoinDesk, CoinTelegraph, Yahoo Finance, r/CryptoCurrency and others), plus RSS-less news sites through saved ScraperAI configs
- Scores each new headline once with FinBERT: `positive_prob - negative_prob`, giving -1 to +1, shown as 0–100 in the UI
- Refreshes prices for all 42 tickers every hour through yfinance (daily OHLC and intraday candles)
- Correlates daily sentiment shifts with next-day returns over 180 days, with a 95% confidence interval and a strength label
- Publishes a free cross-asset [Sentiment Index](https://sentimentfx.org/sentiment-index) (0–100, Fear → Greed) built from scored news text, not trader positioning
- Sells access through Stripe: a Pro dashboard plan, a Data plan for the API, and the full archive on request

## Coverage

| Category | Tickers |
|----------|---------|
| Crypto (5) | BTC, ETH, SOL, XRP, DOGE |
| FX (7) | EURUSD, GBPUSD, USDJPY, AUDUSD, USDCAD, USDCHF, NZDUSD |
| Stocks (20) | AAPL, MSFT, GOOGL, AMZN, META, NVDA, TSLA, JPM, BAC, GS, V, MA, XOM, JNJ, AMD, NFLX, WMT, UBER, CRM, PLTR |
| ETFs (6) | SPY, QQQ, GLD, SLV, USO, ARKK |
| Commodities (4) | GC=F (gold), SI=F (silver), CL=F (crude), NG=F (natural gas) |

**Price currency depends on category.** Crypto is quoted in GBP (`BTC-GBP` on Yahoo). FX pairs are raw exchange rates. Stocks, ETFs and commodity futures are in native USD, and no GBP conversion is applied.

## Products

### Dashboard (app.sentimentfx.org)

- Sentiment vs price chart, candlestick chart with a sentiment pane, and a 0–100 sentiment gauge
- Correlation signal, divergence card, interactive backtest simulator and headline-impact panel
- Headline feed with a per-article score. Editorial news is the default, and social sources are badged.
- Leaderboard, track record and morning-brief pages
- CSV export, email sentiment alerts and the morning brief email (Pro)

Alerts only fire for tickers whose backtest passes a quality gate: positive out-of-sample net return, and at least 70% of walk-forward folds positive. The gate is refreshed daily.

### Plans

| Plan | Price | Includes |
|------|-------|----------|
| Free | £0 | 15 top tickers (3 per category), 30-day history, candlesticks, backtests, correlation |
| Pro | £11.99/mo or £99.99/yr | All 42 tickers, full history, CSV export, alerts, morning brief, 1,000 API calls/mo |
| Data | £49.99/mo or £499.99/yr | Everything in Pro, 5,000 API calls/mo, £0.01/call after that |
| Free API key | £0 | 1,000 calls per 30 days, no card, hard stop at the limit |

The bulk archive (every scored headline and price row, as CSV or JSONL) is sold separately. Ask through the dataset form on the [developer portal](https://developers.sentimentfx.org).

### Developer API (`api.sentimentfx.org/v1/`)

Authenticate with an `X-API-Key` header. Full docs are at [developers.sentimentfx.org](https://developers.sentimentfx.org).

| Endpoint | Returns | Cost |
|----------|---------|------|
| `GET /v1/sentiment/{ticker}?limit=1-100` | Latest scored headlines | 1 credit per 25 rows returned |
| `GET /v1/summary/{ticker}?days=1-365` | Daily average sentiment | 1 credit per day returned |
| `GET /v1/prices/{ticker}?days=1-365` | Daily closes, with a `currency` field | 1 credit per day returned |
| `GET /v1/correlation/{ticker}` | 180-day Pearson r, p-value, 95% CI, strength | 1 credit |
| `GET /v1/usage` | Plan, usage, allowance, reset date, rate limits | Free |

Billing happens after the query, on rows actually returned. A 404 costs nothing, and out-of-range parameters are rejected with a 422 before any work is done. Every response carries `X-Quota-*` and `X-RateLimit-*` headers. Errors use one envelope: `{"error": {"type": "...", "message": "..."}}`.

```bash
# Get a free key (1,000 calls every 30 days, no card)
curl -X POST https://api.sentimentfx.org/api/keys/generate \
  -H "Content-Type: application/json" \
  -d '{"email": "you@example.com"}'

# Use it
curl https://api.sentimentfx.org/v1/summary/BTC?days=7 \
  -H "X-API-Key: sfx_your_key_here"
```

The Sentiment Index is public and needs no key: `GET https://api.sentimentfx.org/sentiment-index`.

### MCP server (`api.sentimentfx.org/mcp`)

The same data is exposed as six Model Context Protocol tools: `list_tickers` and `get_usage` (both free), plus `get_sentiment`, `get_summary`, `get_prices` and `get_correlation`, which bill exactly like their `/v1` equivalents. It uses streamable HTTP with the same `X-API-Key` header, or `Authorization: Bearer <key>` for clients that can't set custom headers (the Claude API's MCP connector is one). The registry manifest is [server.json](server.json), and publishing steps are in [docs/publishing-mcp-registry.md](docs/publishing-mcp-registry.md).

```json
{
  "mcpServers": {
    "sentimentfx": {
      "type": "http",
      "url": "https://api.sentimentfx.org/mcp",
      "headers": { "X-API-Key": "sfx_your_key_here" }
    }
  }
}
```

## Architecture

```
RSS feeds (15 min) ─┐
ScraperAI replay ───┼→ scraper.py / ai_sources.py → dedup by URL → FinBERT → headlines
HN Algolia (admin) ─┘

yfinance (hourly) → prices.py / candles.py → prices, candles

headlines + prices → /dashboard, /candles, /correlation, /backtest …  → React dashboard
                   → /v1/*  and  /mcp                                → API and MCP clients
                   → /sentiment-index                                → public index page
```

**Scheduled jobs** (APScheduler, inside the API process):

| Job | When | What |
|-----|------|------|
| `scrape_rss_only` | Every 15 min | RSS + ScraperAI headlines for all 42 tickers |
| `scrape_all` | Hourly, :00 | Daily price refresh for all tickers, sentiment alert checks (GNews only if enabled) |
| `scrape_intraday_prices` | Hourly, :05 | Intraday candles |
| Signal-quality refresh | Daily, 05:00 UTC | Re-runs the backtest that gates alerts |
| Alert outcome settlement | Daily, 06:15 UTC | Scores past alerts for the track record |
| Morning brief | Daily, 07:00 London | Emails the Claude-written brief to subscribers |
| API usage reset | 1st of month | Resets monthly call counts. Free keys are measured on lifetime calls, so this doesn't refill them. |

FinBERT only runs on headlines it hasn't seen. The model (~400 MB) downloads and loads on the first scoring call, not at import.

**Dormant sources.** GNews, StockTwits and X/Twitter fetchers are still in the code but switched off. Set `GNEWS_ENABLED`, `STOCKTWITS_ENABLED` or `X_ENABLED` to `true` to turn one back on. StockTwits was turned off because Cloudflare blocks the server's IP and retail chat scores poorly with a news-tuned model.

### Repo layout

| Path | What |
|------|------|
| `backend/app/main.py` | Every FastAPI route, the scheduler, Stripe webhook and billing |
| `backend/app/scraper.py`, `ai_sources.py` | Headline fetchers. ScraperAI configs live in `app/scraperai_configs/`. |
| `backend/app/sentiment.py` | FinBERT pipeline |
| `backend/app/prices.py`, `candles.py` | yfinance daily and intraday prices |
| `backend/app/sentiment_index.py` | The cross-asset index (equal-weighted across the 5 categories) |
| `backend/app/mcp_server.py` | MCP tools, mounted at `/mcp` |
| `backend/app/brief.py` | Claude-written morning brief |
| `backend/app/trend_signal.py`, `funding_monitor.py` | Personal-use tooling, not product features |
| `backend/build_scraper_config.py` | Offline tool that generates ScraperAI configs. Needs its own venv; not used at runtime. |
| `frontend/` | React + Vite dashboard |
| `landing/` | Marketing site, leaderboard, index page and per-ticker SEO pages |
| `developers/` | Developer portal and API docs |
| `status/` | Status page |

## Getting started

### Backend

```bash
cd backend
python -m venv venv
source venv/Scripts/activate        # Windows (Git Bash); venv/bin/activate on macOS/Linux
pip install -r requirements.txt
python -m uvicorn app.main:app --reload   # http://localhost:8000
```

There is no `.env.example`. Create `backend/.env` with the variables below.

| Variable | Needed for |
|----------|-----------|
| `DATABASE_URL` | PostgreSQL. Required: tables are created at import, so the app won't start without a reachable database. |
| `ANTHROPIC_API_KEY` | Morning brief. Read at import, so any value lets the app boot. |
| `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` | User accounts and tiers |
| `STRIPE_SECRET_KEY`, `STRIPE_WEBHOOK_SECRET` | Checkout and webhook |
| `RESEND_API_KEY` | Waitlist, alert and API-key emails |
| `ADMIN_SECRET` | `?token=` for admin, backfill and cleanup endpoints |
| `MCP_ALLOWED_HOSTS` | Optional. Hosts the MCP endpoint accepts. Defaults cover localhost and 127.0.0.1 on port 8000; other ports get a 421. |
| `GNEWS_ENABLED`, `GNEWS_API_KEY`, `STOCKTWITS_ENABLED`, `X_ENABLED`, `X_NITTER_BASE` | Optional. Dormant sources. |

### Frontend

```bash
cd frontend
npm install
npm run dev      # http://localhost:5173
```

Set `VITE_SUPABASE_ANON_KEY` in `frontend/.env` to enable login (`VITE_GA_ID` is optional). The API base URL is hard-coded to `https://api.sentimentfx.org` in `src/lib/constants.js`, so a local frontend talks to production unless you change it.

### Tests

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest tests/ -v
```

Use `python -m pytest`, not bare `pytest`: `tests/` has no `__init__.py`, so bare `pytest` can't import `app`. The tests are smoke-level only. They check that the app boots and the core routes answer, and they need a reachable `DATABASE_URL`. CI runs them against a throwaway Postgres on every push to `main` and every PR. There is no frontend test suite.

## Sentiment scoring

```
score = positive_probability - negative_probability
```

The range is -1 (most negative) to +1 (most positive), and the UI shows it as 0–100. Neutral headlines land near 0 instead of being forced to exactly 0, which was the problem with the old label × confidence method. FinBERT scores the title only, even when the full article body is stored.

## Deployment

- **Backend:** Railway via `Procfile` + `nixpacks.toml` at `api.sentimentfx.org`. Starts with `uvicorn app.main:app --host 0.0.0.0 --port $PORT`. Runs on CPU only.
- **Dashboard, landing, developer portal:** separate Vercel projects (`frontend/`, `landing/`, `developers/`), each auto-deploying from `main`.
