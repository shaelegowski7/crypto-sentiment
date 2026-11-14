"""Social chatter vs editorial news -- one definition for every surface.

Reddit arrives through the general RSS path, so it carries no source_type and
is identified by its feed title ("newest submissions : Bitcoin"); StockTwits/X
are tagged via source_type; and the HN backfill stores the linked article's
domain, which is sometimes a social platform.  Matching is deliberately
exact/prefix rather than a loose "%reddit%" — real companies (redditinc.com,
redditrecs.com) would be caught by that and wrongly hidden.

Social posts are collected but kept separate: they are never averaged in with
news.  Everything that turns headlines into a number -- the index, daily
summaries, the dashboard chart, correlation, signals, divergence, backtests,
the SignalQuality gate, alerts, trade cards, the morning brief, exports --
reads `news_headlines(db)`.  Social posts are only reachable on purpose,
through `sources=social|all` on the dashboard's headline list (/dashboard),
where each row carries its `source_kind`.  /v1, MCP and the bulk export
serve news only: social was withdrawn there on 2026-11-14 because the
platforms' terms don't allow commercial use without a licence (see main.py).

When adding a query that averages or counts sentiment, start from
`news_headlines(db)`, not `db.query(models.Headline)`.  The plain query is
for housekeeping (dedup, row counts, cleanup) and labelled lists only.

Lives in its own module (not main.py) so sentiment_index.py, mcp_server.py,
brief.py and trade_card.py can import it without pulling in main.py and its
import-time side effects.
"""
from sqlalchemy import or_ as sa_or, func as sa_func

from . import models

SOCIAL_SOURCE_TYPES = ("stocktwits", "x")
REDDIT_DOMAINS = ("reddit.com", "old.reddit.com", "sh.reddit.com")
SOCIAL_DOMAINS = REDDIT_DOMAINS + ("twitter.com", "x.com", "youtube.com")


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


def reddit_headline_filter():
    """SQLAlchemy predicate matching Reddit posts (the RSS feed titles and the
    HN backfill rows whose linked domain is Reddit).  Reddit hasn't been
    collected since 2026-11-14, and rows stored before then must not be shown
    or served: its terms don't allow commercial use without a licence."""
    return sa_or(
        sa_func.coalesce(models.Headline.source, "").ilike("newest submissions :%"),
        sa_func.lower(sa_func.coalesce(models.Headline.source, "")).in_(REDDIT_DOMAINS),
    )


def news_headlines(db):
    """`db.query(Headline)` restricted to editorial news."""
    return db.query(models.Headline).filter(~social_headline_filter())


def filter_sources(query, sources: str):
    """Apply a `sources` choice to a Headline query: 'news' keeps editorial
    news, 'social' keeps only social posts, 'all' leaves it unchanged."""
    if sources == "news":
        return query.filter(~social_headline_filter())
    if sources == "social":
        return query.filter(social_headline_filter())
    return query


def classify_source(source: str | None, source_type: str | None) -> str:
    """Coarse label: 'news' | 'reddit' | 'stocktwits' | 'x' | 'youtube'.

    Must agree with social_headline_filter: everything it matches is
    something other than 'news', and everything else is 'news'.
    """
    st = (source_type or "").lower()
    if st in SOCIAL_SOURCE_TYPES:
        return st
    src = (source or "").lower()
    if src.startswith("newest submissions :") or src in REDDIT_DOMAINS:
        return "reddit"
    if src in ("twitter.com", "x.com"):
        return "x"
    if src == "youtube.com":
        return "youtube"
    return "news"
