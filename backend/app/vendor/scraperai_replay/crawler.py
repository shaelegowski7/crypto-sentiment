"""Crawlers for the replay path (vendored/adapted from ``scraperai/crawlers``).

``RequestsCrawler`` is a static-HTML crawler: it fetches a page with ``requests``
and exposes its source for XPath extraction.  It cannot execute JavaScript, so
``xpath``/``scroll`` pagination (which need a real browser) is unsupported and
simply stops after the first page; ``urls`` pagination (a precomputed URL list)
works fine.

Adaptation vs upstream: the constructor takes ``headers`` and ``timeout``.
Upstream's ``requests.get(url).text`` sends the default ``python-requests`` UA,
which several finance sites 403 — SentimentFX passes a browser UA instead
(mirroring the trick in ``app/scraper.py``).  See ``__init__.py`` for provenance.
"""
from abc import ABC, abstractmethod

import requests

from .models import Pagination

_DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class BaseCrawler(ABC):
    @abstractmethod
    def get(self, url: str):
        ...

    @property
    @abstractmethod
    def page_source(self) -> str:
        ...

    @abstractmethod
    def switch_page(self, pagination: Pagination) -> bool:
        raise NotImplementedError()


class RequestsCrawler(BaseCrawler):
    current_url: str = None

    def __init__(self, headers: dict | None = None, timeout: int = 20):
        self.__page_source = None
        self.__pagination_url_index = 0
        self.headers = {"User-Agent": _DEFAULT_UA, **(headers or {})}
        self.timeout = timeout

    def get(self, url: str):
        self.current_url = url
        res = requests.get(url, headers=self.headers, timeout=self.timeout)
        res.raise_for_status()
        self.__page_source = res.text

    @property
    def page_source(self) -> str:
        return self.__page_source

    def switch_page(self, pagination: Pagination) -> bool:
        if pagination.type == 'urls':
            if self.__pagination_url_index >= len(pagination.urls):
                return False
            self.get(pagination.urls[self.__pagination_url_index])
            self.__pagination_url_index += 1
            return True
        # xpath / scroll pagination requires a JS-capable browser, which the
        # replay path deliberately does not run.  Stop after the first page.
        return False
