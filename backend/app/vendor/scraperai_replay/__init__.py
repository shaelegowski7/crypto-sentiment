"""Minimal, pure-``lxml`` replay engine vendored from ScraperAI.

ScraperAI (https://github.com/scraperai/scraperai) is a two-phase tool: an
LLM + Selenium *build* phase auto-detects a page's structure and emits a
serialisable ``ScraperConfig`` (a bundle of XPaths), and a cheap *replay* phase
re-runs that config with ``requests`` + ``lxml`` to yield rows.

SentimentFX only needs the replay phase at runtime.  We vendor just that slice
here so the production image does NOT have to install the full ``scraperai``
package, which pins ``langchain==0.1.16`` and ``numpy==1.26.4`` and drags in
``selenium``/``openai``/``pandas`` — the numpy pin in particular conflicts with
our ``torch``/``transformers`` stack.  The only third-party dependency of this
package is ``lxml``.

Config *generation* still uses the real upstream ``scraperai`` — see
``backend/build_scraper_config.py``, which is an offline/admin tool run in its
own virtualenv, never imported by the running app.

Upstream ScraperAI is licensed GPL-3.0 (author: Iakov Kaiumov).  SentimentFX
uses it server-side only and does not distribute it, so GPL distribution
obligations are not triggered.  This attribution is retained per the licence.
"""
from .models import (
    ScraperConfig,
    WebpageType,
    Pagination,
    CatalogItem,
    WebpageFields,
    StaticField,
    DynamicField,
)
from .crawler import BaseCrawler, RequestsCrawler
from .scraper import Scraper

__all__ = [
    "Scraper",
    "ScraperConfig",
    "WebpageType",
    "Pagination",
    "CatalogItem",
    "WebpageFields",
    "StaticField",
    "DynamicField",
    "BaseCrawler",
    "RequestsCrawler",
]
