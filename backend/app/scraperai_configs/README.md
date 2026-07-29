# ScraperAI replay configs

Each `*.json` file here is a saved ScraperAI config that `app/ai_sources.py`
replays at runtime (pure `lxml` + `requests`, no LLM) to pull headlines from a
site that has no RSS/JSON feed. The 15-minute scrape job runs every config whose
`tickers` list includes the ticker being scraped.

**These files are generated offline — not by the running app.** To add a source:

```bash
cd backend
# in a scratch venv with the heavy build deps + an Anthropic key:
pip install scraperai
export ANTHROPIC_API_KEY=sk-ant-...
python build_scraper_config.py \
    --url https://some-finance-site.com/news \
    --ticker BTC --ticker ETH \
    --source somefinance \
    --describe "each card's article headline and its link"
```

That writes `somefinance.json` here. Review the printed XPaths, commit the file,
and the next deploy picks it up. See the module docstring in
[`app/ai_sources.py`](../ai_sources.py) and the CLI in
[`build_scraper_config.py`](../../build_scraper_config.py).

## File shape

```jsonc
{
  "source": "somefinance",           // label stored as Headline.source
  "tickers": ["BTC", "ETH"],         // which tickers this feeds (empty = never runs)
  "config": { /* serialised ScraperConfig: XPaths, pagination, ... */ },

  // optional, add by hand after reviewing:
  "keywords": ["bitcoin", "btc"],    // title must contain one, else the row is dropped
  "field_map": { "title": "Headline", "url": "Link", "body": "Body" },
  "headers":  { "User-Agent": "..." }
}
```

`ai_sources.py` maps extracted fields to `title` / `url` / `body` by name
heuristics (`title`/`headline`/`name`, `url`/`link`/`href`,
`body`/`content`/`article`); use `field_map` when the auto-detected field names
don't match those.

## Gotchas

- **Replay is `requests`-only** — JavaScript-rendered pages won't work at
  runtime even if `--selenium` was used to *build* the config. Target
  server-rendered pages.
- **`url` is required per row** — it's the dedup key; rows without a link are
  dropped so they aren't re-ingested every run.
- Full article bodies (`--details`) are stored in `Headline.body` but are **not**
  scored by FinBERT (which is headline-tuned, 512-token). The title is scored,
  same as every other source.
