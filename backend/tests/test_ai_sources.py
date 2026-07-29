"""Offline test for the vendored ScraperAI replay engine + ai_sources mapping.

No network, no DB, no LLM: we hand-build a catalog ``ScraperConfig`` and feed a
static HTML fixture through a fake crawler, then assert the vendored engine
extracts the fields and that ``ai_sources`` maps them into the standard headline
dict shape. This is the runtime path that the 15-minute scrape job exercises;
config *generation* (the LLM/Selenium half) is not tested here — it's an offline
admin tool that never runs in prod.
"""
from app.vendor.scraperai_replay import (
    CatalogItem,
    Pagination,
    Scraper,
    ScraperConfig,
    StaticField,
    WebpageFields,
    WebpageType,
)
from app.vendor.scraperai_replay.crawler import BaseCrawler

FIXTURE_HTML = """
<html><body>
  <div class="feed">
    <article class="card">
      <a class="headline" href="/news/btc-rips-higher">Bitcoin rips higher on ETF inflows</a>
    </article>
    <article class="card">
      <a class="headline" href="https://example.com/news/eth-upgrade">Ethereum upgrade ships tonight</a>
    </article>
    <article class="card">
      <a class="headline" href="/news/sol-outage">Solana network sees brief outage</a>
    </article>
  </div>
</body></html>
"""


class _FakeCrawler(BaseCrawler):
    """Returns the fixture HTML for any URL; no pagination."""

    def __init__(self, html: str):
        self._html = html

    def get(self, url: str):
        self._current = url

    @property
    def page_source(self) -> str:
        return self._html

    def switch_page(self, pagination) -> bool:
        return False


def _catalog_config() -> ScraperConfig:
    return ScraperConfig(
        start_url="https://example.com/news",
        page_type=WebpageType.CATALOG,
        pagination=Pagination(type="none"),
        catalog_item=CatalogItem(
            card_xpath='//article[@class="card"]',
            url_xpath='.//a/@href',
            html_snippet="",
            urls_on_page=[],
        ),
        open_nested_pages=False,
        fields=WebpageFields(
            static_fields=[
                StaticField(field_name="Title", field_xpath='.//a[@class="headline"]'),
                StaticField(field_name="Link", field_xpath='.//a[@class="headline"]/@href'),
            ],
            dynamic_fields=[],
        ),
        max_pages=1,
        max_rows=40,
    )


def test_vendored_scraper_extracts_catalog_fields():
    scraper = Scraper(_catalog_config(), _FakeCrawler(FIXTURE_HTML))
    rows = list(scraper.scrape())
    assert len(rows) == 3
    titles = [r["Title"] for r in rows]
    assert "Bitcoin rips higher on ETF inflows" in titles
    # href fields come through as extracted values
    assert rows[0]["Link"] == "/news/btc-rips-higher"


def test_ai_sources_maps_rows_to_headlines(monkeypatch):
    import app.ai_sources as ai

    raw = {
        "source": "example",
        "tickers": ["BTC"],
        "config": _catalog_config().model_dump(),
    }
    src = ai.AISource("example.json", raw)

    # Replace the network crawler with our fixture crawler.
    monkeypatch.setattr(ai, "RequestsCrawler", lambda headers=None: _FakeCrawler(FIXTURE_HTML))

    headlines = ai._rows_to_headlines(src, "BTC")
    assert len(headlines) == 3
    h = headlines[0]
    assert h["ticker"] == "BTC"
    assert h["source"] == "example"
    assert h["source_type"] == "ai"
    assert h["title"] == "Bitcoin rips higher on ETF inflows"
    # relative link resolved against start_url; absolute link preserved
    assert h["url"] == "https://example.com/news/btc-rips-higher"
    assert headlines[1]["url"] == "https://example.com/news/eth-upgrade"


def test_ai_sources_keyword_filter_drops_offtopic(monkeypatch):
    import app.ai_sources as ai

    raw = {
        "source": "example",
        "tickers": ["BTC"],
        "keywords": ["bitcoin"],  # only keep titles mentioning bitcoin
        "config": _catalog_config().model_dump(),
    }
    src = ai.AISource("example.json", raw)
    monkeypatch.setattr(ai, "RequestsCrawler", lambda headers=None: _FakeCrawler(FIXTURE_HTML))

    headlines = ai._rows_to_headlines(src, "BTC")
    assert len(headlines) == 1
    assert "bitcoin" in headlines[0]["title"].lower()
