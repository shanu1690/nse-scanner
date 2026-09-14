"""RSS news ingestion.

RSS is the intended interface for these sources (PROJECT_BRIEF.md Section 3 /
the nse-data-sources skill), not scraping article pages -- feeds are
published specifically for syndication. Only the headline and the feed's own
short teaser are kept, truncated defensively; never the article body.

Point-in-time discipline: every item carries published_at (from the feed)
distinct from fetched_at (when this run pulled it). A feed entry with no
parseable pubDate falls back to fetched_at as a conservative estimate
(documented on the item via published_at_estimated) rather than being
silently dropped or silently mis-dated -- see nse/news/store.py.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterable, Optional

import feedparser

from .store import NewsItem

__all__ = ["FEEDS", "fetch_all", "link_symbols"]

# Verified live and current as of 2026-09-14 (fresh pubDates at check time).
# Moneycontrol's markets/results RSS URLs found so far (rss/marketreports.xml,
# rss/results.xml) resolve but return stale (April 2024) items -- omitted
# until a live current feed is found; add it here once confirmed.
FEEDS = {
    "et_markets": "https://economictimes.indiatimes.com/markets/stocks/rssfeeds/2146842.cms",
    "business_standard": "https://www.business-standard.com/rss/markets-106.rss",
    "livemint": "https://www.livemint.com/rss/markets",
}

MAX_SUMMARY_CHARS = 300  # publisher's own teaser, truncated defensively

# Tickers that collide with common English words / other tickers' substrings
# closely enough that a bare word-boundary match produces frequent false
# positives. Small guard list for v1 -- expand as false positives surface.
_AMBIGUOUS_SKIP = {"IEX", "BSE"}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def _parse_pubdate(entry: dict) -> Optional[datetime]:
    raw = entry.get("published") or entry.get("updated")
    if not raw:
        return None
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, OverflowError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _clean_summary(entry: dict) -> str:
    raw = entry.get("summary") or entry.get("description") or ""
    text = _TAG_RE.sub(" ", raw)
    text = _WS_RE.sub(" ", text).strip()
    return text[:MAX_SUMMARY_CHARS]


def link_symbols(text: str, universe: Iterable[str]) -> tuple[str, ...]:
    """v1 entity linking: whole-word ticker match against headline+teaser.

    Deliberately conservative -- few false positives, more false negatives
    -- since misattributing a news item to the wrong symbol is worse than
    missing one. Will not catch a company referred to only by full name
    ("Reliance Industries" rather than "RELIANCE"); a real ticker<->name
    alias table is future work, not built here.
    """
    upper = text.upper()
    hits = []
    for sym in universe:
        if sym in _AMBIGUOUS_SKIP:
            continue
        if re.search(rf"\b{re.escape(sym)}\b", upper):
            hits.append(sym)
    return tuple(hits)


def fetch_all(universe: Iterable[str], *, feeds: "dict[str, str] | None" = None) -> list[NewsItem]:
    """Fetch every configured feed and return NewsItem objects ready for
    NewsStore.put(). Does not touch the store -- keeps fetch and persistence
    independently testable. A feed that fails to parse contributes zero
    items rather than raising, so one broken feed doesn't block the others.
    """
    feeds = feeds if feeds is not None else FEEDS
    now = datetime.now(timezone.utc)
    universe = list(universe)
    items = []
    for source, url in feeds.items():
        try:
            parsed = feedparser.parse(url)
        except Exception:
            continue
        for entry in parsed.get("entries", []):
            link = entry.get("link")
            headline = entry.get("title")
            if not link or not headline:
                continue
            published_at = _parse_pubdate(entry)
            estimated = published_at is None
            if published_at is None:
                published_at = now
            summary = _clean_summary(entry)
            symbols = link_symbols(f"{headline} {summary}", universe)
            items.append(NewsItem(
                source=source, url=link, headline=headline, summary=summary,
                published_at=published_at, fetched_at=now, symbols=symbols,
                published_at_estimated=estimated,
            ))
    return items
