"""Point-in-time news store.

Same discipline as nse/pit/store.py: a query as-of time T returns only
articles whose published_at is STRICTLY EARLIER than T. Shaped for articles
rather than scalar fundamentals facts -- many symbols per article, a
headline plus a short publisher-provided teaser, deduplicated by URL.

Never store full article text (PROJECT_BRIEF.md's copyright boundary):
`summary` is the feed's own short description, truncated defensively by the
ingest layer, not an independent paraphrase and never the article body.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Sequence

__all__ = ["NewsItem", "NewsStore"]


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError(
            f"naive datetime {value!r}: news timestamps must be timezone-aware"
        )
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class NewsItem:
    """One RSS entry.

    ``published_at`` is the feed's own pubDate. When a feed omits it,
    ingest falls back to ``fetched_at`` (the conservative direction per the
    point-in-time-protocol checklist: a record can only look later than it
    really is, never earlier, so this can't manufacture a look-ahead leak)
    and sets ``published_at_estimated=True`` so that assumption stays
    visible rather than silently blending into real timestamps.
    """

    source: str
    url: str
    headline: str
    summary: str
    published_at: datetime
    fetched_at: datetime
    symbols: tuple[str, ...] = ()
    published_at_estimated: bool = False

    def __post_init__(self) -> None:
        if self.published_at.tzinfo is None or self.fetched_at.tzinfo is None:
            raise ValueError(
                f"{self.url}: published_at/fetched_at must be timezone-aware"
            )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS news (
    url                    TEXT PRIMARY KEY,
    source                 TEXT NOT NULL,
    headline               TEXT NOT NULL,
    summary                TEXT NOT NULL,
    published_at           TEXT NOT NULL,
    fetched_at             TEXT NOT NULL,
    published_at_estimated INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS news_symbols (
    url    TEXT NOT NULL REFERENCES news(url),
    symbol TEXT NOT NULL,
    PRIMARY KEY (url, symbol)
);
CREATE INDEX IF NOT EXISTS idx_news_symbol ON news_symbols(symbol);
CREATE INDEX IF NOT EXISTS idx_news_pubdate ON news(published_at);
"""


class NewsStore:
    """Usage::

        with NewsStore("data/news.db") as store:
            added = store.put(items)
            rows = store.as_of("RELIANCE", decision_time)
    """

    def __init__(self, path: "str | Path" = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "NewsStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def put(self, items: Sequence[NewsItem]) -> int:
        """Insert new articles. Re-inserting an already-stored URL is a
        no-op (dedup by URL), so ingestion is idempotent and safe to rerun
        on overlapping feed windows."""
        added = 0
        for it in items:
            cur = self._conn.execute(
                "INSERT OR IGNORE INTO news "
                "(url, source, headline, summary, published_at, fetched_at, "
                " published_at_estimated) VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    it.url, it.source, it.headline, it.summary,
                    _utc(it.published_at).isoformat(), _utc(it.fetched_at).isoformat(),
                    int(it.published_at_estimated),
                ),
            )
            if cur.rowcount:
                added += 1
                for sym in it.symbols:
                    self._conn.execute(
                        "INSERT OR IGNORE INTO news_symbols (url, symbol) VALUES (?, ?)",
                        (it.url, sym),
                    )
        self._conn.commit()
        return added

    def as_of(self, symbol: str, as_of: datetime, *, limit: int = 50) -> list[dict]:
        """Articles mentioning ``symbol`` with published_at strictly before
        ``as_of``, most recent first. The core point-in-time guarantee."""
        cutoff = _utc(as_of).isoformat()
        rows = self._conn.execute(
            "SELECT n.url, n.source, n.headline, n.summary, n.published_at, "
            "       n.published_at_estimated "
            "FROM news n JOIN news_symbols s ON s.url = n.url "
            "WHERE s.symbol = ? AND n.published_at < ? "
            "ORDER BY n.published_at DESC LIMIT ?",
            (symbol, cutoff, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def count(self) -> int:
        return self._conn.execute("SELECT COUNT(*) FROM news").fetchone()[0]
