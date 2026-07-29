#!/usr/bin/env python
"""Offline generator for ScraperAI replay configs (SentimentFX).

Uses the real ``scraperai`` package + Claude (via ``app/scraperai_claude.py``)
to auto-detect a page's structure and write a serialised ``ScraperConfig`` JSON
into ``app/scraperai_configs/<source>.json``.  The running app never imports
this file or ``scraperai`` — at runtime the saved config is replayed by the
vendored engine in ``app/vendor/scraperai_replay/`` (see ``app/ai_sources.py``).

Run it in a scratch virtualenv that has the heavy build deps installed
(``pip install scraperai``) plus ``ANTHROPIC_API_KEY`` in the environment:

    cd backend
    python build_scraper_config.py \
        --url https://example-finance-site.com/latest \
        --ticker BTC --ticker ETH \
        --source example \
        --describe "each card's article headline and its link"

Add ``--selenium`` for JavaScript-heavy pages (needs Chrome + chromedriver) and
``--details`` to also open each article and extract its body.

This is a sibling of ``hn_backfill.py`` — a standalone admin/CLI tool, kept out
of the request path on purpose.
"""
import argparse
import json
import logging
import os
import sys

# scraperai + our Claude adapters are build-time deps only.
try:
    from scraperai import ParserAI, RequestsCrawler, Scraper, SeleniumCrawler
    from scraperai.models import ScraperConfig, WebpageType
except ImportError:
    sys.exit(
        "scraperai is not installed. This is an offline build tool — install it in a\n"
        "scratch venv with `pip install scraperai` (do NOT add it to requirements.txt;\n"
        "the running app uses the vendored replay engine instead)."
    )

from app.scraperai_claude import ClaudeJsonLM, ClaudePythonCodeLM, ClaudeVisionLM

CONFIG_DIR = os.path.join(os.path.dirname(__file__), "app", "scraperai_configs")


def _build_parser_ai() -> "ParserAI":
    return ParserAI(
        json_lm_model=ClaudeJsonLM(),
        vision_model=ClaudeVisionLM(),
        code_model=ClaudePythonCodeLM(),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Generate a ScraperAI replay config for SentimentFX.")
    ap.add_argument("--url", required=True, help="Page to analyse (a headline list / catalog page).")
    ap.add_argument("--source", required=True, help="Short label; also the output filename stem.")
    ap.add_argument("--ticker", action="append", default=[], help="Ticker(s) this source feeds (repeatable).")
    ap.add_argument("--describe", default=None, help="Hint describing the fields to extract (title, link, ...).")
    ap.add_argument("--details", action="store_true", help="Open each item and extract the full article body.")
    ap.add_argument("--selenium", action="store_true", help="Use Selenium/Chrome (JS pages); enables vision.")
    ap.add_argument("--max-pages", type=int, default=1, help="Pagination pages to walk at replay time.")
    ap.add_argument("--max-rows", type=int, default=40, help="Max items per replay.")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO)

    if not os.getenv("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY is not set — required for the Claude-backed page analysis.")

    parser = _build_parser_ai()
    crawler = SeleniumCrawler() if args.selenium else RequestsCrawler()

    print(f"[build] fetching {args.url} ({'selenium' if args.selenium else 'requests'})")
    crawler.get(args.url)
    page_source = crawler.page_source

    # Page type: prefer vision when Selenium can screenshot; else text classifier.
    if args.selenium:
        screenshot = crawler.get_screenshot_as_base64()
        page_type = parser.detect_page_type(screenshot=screenshot)
    else:
        page_type = parser.detect_page_type(page_source=page_source)
    print(f"[build] page_type = {page_type}")

    pagination = parser.detect_pagination(page_source)
    print(f"[build] pagination = {pagination}")

    catalog_item = None
    open_nested_pages = bool(args.details)

    if page_type == WebpageType.CATALOG:
        catalog_item = parser.detect_catalog_item(page_source, args.url, extra_prompt=args.describe)
        print(f"[build] catalog card_xpath = {catalog_item.card_xpath}")
        print(f"[build] catalog url_xpath  = {catalog_item.url_xpath}")
        print(f"[build] found {len(catalog_item.urls_on_page)} item URLs on the page")

        if args.details:
            # Fields come from an item's detail page (title + body).
            if not catalog_item.urls_on_page:
                sys.exit("[build] --details requested but no item URLs detected on the page.")
            detail_url = catalog_item.urls_on_page[0]
            print(f"[build] --details: analysing {detail_url}")
            crawler.get(detail_url)
            detail_source = crawler.page_source
            fields = (parser.find_fields(detail_source, args.describe)
                      if args.describe else parser.extract_fields(detail_source))
        else:
            snippet = catalog_item.html_snippet
            fields = (parser.find_fields(snippet, args.describe)
                      if args.describe else parser.extract_fields(snippet))
    elif page_type == WebpageType.DETAILS:
        open_nested_pages = False
        fields = (parser.find_fields(page_source, args.describe)
                  if args.describe else parser.extract_fields(page_source))
    else:
        sys.exit(f"[build] unsupported page_type={page_type}; ScraperAI only replays catalog/detail pages.")

    print("[build] detected fields:")
    for f in fields.static_fields:
        print(f"    - {f.field_name}: {f.field_xpath}  (e.g. {f.first_value!r})")
    for f in fields.dynamic_fields:
        print(f"    - [section] {f.section_name}: {f.name_xpath} / {f.value_xpath}")

    config = ScraperConfig(
        start_url=args.url,
        page_type=page_type,
        pagination=pagination,
        catalog_item=catalog_item,
        open_nested_pages=open_nested_pages,
        fields=fields,
        max_pages=args.max_pages,
        max_rows=args.max_rows,
    )

    os.makedirs(CONFIG_DIR, exist_ok=True)
    out_path = os.path.join(CONFIG_DIR, f"{args.source}.json")
    # Wrap the raw ScraperConfig with SentimentFX metadata so ai_sources.py knows
    # which tickers this config feeds and how to label the source.
    payload = {
        "source": args.source,
        "tickers": [t.upper() for t in args.ticker],
        "config": json.loads(config.model_dump_json()),
    }
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2)
    print(f"[build] wrote {out_path}")
    print("[build] Review the XPaths above, then test replay via app.ai_sources.fetch_ai_headlines().")


if __name__ == "__main__":
    main()
