"""Social chatter vs editorial news -- one definition for every surface.

Reddit arrives through the general RSS path, so it carries no source_type and
is identified by its feed title ("newest submissions : Bitcoin"); StockTwits/X
are tagged via source_type; and the HN backfill stores the linked article's
domain, which is sometimes a social platform.  Matching is deliberately
exact/prefix rather than a loose "%reddit%" — real companies (redditinc.com,
redditrecs.com) would be caught by that and wrongly hidden.

Who uses it:
* /dashboard `sources=news` and the /v1 + MCP `sources=news` option -- opt-in,
  the default still returns everything.
* The Sentiment Index -- always.  Its whole positioning is "scored news, not
  social chatter", stated on every response and on sentiment-index.html, and
  Reddit is most of the crypto volume, so averaging it in made that untrue.

Sentiment scoring, correlation, alerts and the SignalQuality gate still consume
every headline -- verified that excluding social doesn't measurably change the
correlation, so there's no reason to recalibrate that pipeline.

Lives in its own module (not main.py) so sentiment_index.py and mcp_server.py
can import it without pulling in main.py and its import-time side effects.
"""
from sqlalchemy import or_ as sa_or, func as sa_func

from . import models

SOCIAL_SOURCE_TYPES = ("stocktwits", "x")
SOCIAL_DOMAINS = (
    "reddit.com", "old.reddit.com", "sh.reddit.com",
    "twitter.com", "x.com", "youtube.com",
)


def social_headline_filter():
    """SQLAlchemy predicate matching social-chatter headlines.

    COALESCE guards the NULL trap: `NOT (NULL = 'stocktwits')` is NULL, not
    TRUE, so without it every legacy news row (source_type IS NULL) would be
    dropped by the negated filter.
    """
    return sa_or(
        sa_func.coalesce(models.Headline.source_type, "").in_(SOCIAL_SOURCE_TYPES),
        sa_func.coalesce(models.Headline.source, "").ilike("newest submissions :%"),
        sa_func.lower(sa_func.coalesce(models.Headline.source, "")).in_(SOCIAL_DOMAINS),
    )


def classify_source(source: str | None, source_type: str | None) -> str:
    """Coarse label: 'news' | 'reddit' | 'stocktwits' | 'x' | 'youtube'.

    Must agree with social_headline_filter: everything it matches is
    something other than 'news', and everything else is 'news'.
    """
    st = (source_type or "").lower()
    if st in SOCIAL_SOURCE_TYPES:
        return st
    src = (source or "").lower()
    if src.startswith("newest submissions :") or src in ("reddit.com", "old.reddit.com", "sh.reddit.com"):
        return "reddit"
    if src in ("twitter.com", "x.com"):
        return "x"
    if src == "youtube.com":
        return "youtube"
    return "news"
