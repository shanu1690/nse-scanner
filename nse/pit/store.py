"""Point-in-time store.

One rule governs this module: a query as of time T returns only records whose
``published_at`` is STRICTLY EARLIER than T.

Fundamentals become knowable on their publication date, not their fiscal period
end. Keying to period end leaks weeks of hindsight into every backtest and
manufactures a spectacular fake edge. Restatements are stored as new rows, never
overwrites, so a backtest sees the value the market actually saw at the time.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

__all__ = ["PITRecord", "PointInTimeStore", "LookaheadError"]


class LookaheadError(RuntimeError):
    """Raised when a record would be visible before it was published."""


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise ValueError(
            f"naive datetime {value!r}: point-in-time timestamps must be timezone-aware"
        )
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class PITRecord:
    """A single observation, tagged with when the world could first see it.

    ``period_end`` is what the value describes (fiscal quarter end, event date).
    ``published_at`` is when it became public. These are never the same thing and
    must never be conflated.
    """

    symbol: str
    field: str
    value: float | int | str | None
    period_end: datetime
    published_at: datetime
    source: str
    fetched_at: datetime | None = None

    def __post_init__(self) -> None:
        if self.published_at < self.period_end:
            raise LookaheadError(
                f"{self.symbol}/{self.field}: published_at {self.published_at.isoformat()} "
                f"precedes period_end {self.period_end.isoformat()} - a value cannot be "
                f"published before the period it describes has ended"
            )


_SCHEMA = """
CREATE TABLE IF NOT EXISTS pit (
    symbol       TEXT NOT NULL,
    field        TEXT NOT NULL,
    value        TEXT,
    period_end   TEXT NOT NULL,
    published_at TEXT NOT NULL,
    source       TEXT NOT NULL,
    fetched_at   TEXT,
    PRIMARY KEY (symbol, field, period_end, published_at, source)
);
CREATE INDEX IF NOT EXISTS idx_pit_lookup ON pit (symbol, field, published_at);
"""


class PointInTimeStore:
    """Append-only store with as-of querying.

    Usage::

        store = PointInTimeStore("data/pit.db")
        store.put([PITRecord(...)])
        rows = store.as_of(["INFY"], ["revenue"], decision_time)
    """

    def __init__(self, path: str | Path = ":memory:") -> None:
        self._conn = sqlite3.connect(str(path))
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "PointInTimeStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def put(self, records: Iterable[PITRecord]) -> int:
        """Insert records. A restatement is a new row, not an update.

        Re-inserting an identical row is a no-op, so ingestion is idempotent and
        safe to rerun.
        """
        rows = [
            (
                r.symbol,
                r.field,
                None if r.value is None else str(r.value),
                _utc(r.period_end).isoformat(),
                _utc(r.published_at).isoformat(),
                r.source,
                _utc(r.fetched_at).isoformat() if r.fetched_at else None,
            )
            for r in records
        ]
        cur = self._conn.executemany(
            "INSERT OR IGNORE INTO pit "
            "(symbol, field, value, period_end, published_at, source, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        self._conn.commit()
        return cur.rowcount

    def as_of(
        self,
        symbols: Sequence[str],
        fields: Sequence[str],
        as_of: datetime,
        *,
        max_age_days: int | None = None,
    ) -> list[dict]:
        """Every period visible at ``as_of``, one row per (symbol, field, period_end).

        Where a period has been restated, the latest restatement *that was already
        published* is returned. ``max_age_days`` drops periods whose publication is
        older than the cutoff, so a company that stopped reporting does not keep
        contributing a stale value forever.
        """
        if not symbols or not fields:
            return []
        cutoff = _utc(as_of).isoformat()
        q_sym = ",".join("?" * len(symbols))
        q_fld = ",".join("?" * len(fields))
        sql = f"""
            SELECT symbol, field, value, period_end, published_at, source
              FROM pit p
             WHERE symbol IN ({q_sym})
               AND field  IN ({q_fld})
               AND published_at < ?
               AND published_at = (
                     SELECT MAX(p2.published_at) FROM pit p2
                      WHERE p2.symbol = p.symbol AND p2.field = p.field
                        AND p2.period_end = p.period_end
                        AND p2.published_at < ?
                   )
             ORDER BY symbol, field, period_end
        """
        rows = self._conn.execute(sql, [*symbols, *fields, cutoff, cutoff]).fetchall()
        out = [dict(r) for r in rows]
        if max_age_days is not None:
            floor = _utc(as_of).timestamp() - max_age_days * 86400
            out = [
                r
                for r in out
                if datetime.fromisoformat(r["published_at"]).timestamp() >= floor
            ]
        return out

    def latest_as_of(
        self,
        symbols: Sequence[str],
        fields: Sequence[str],
        as_of: datetime,
        *,
        max_age_days: int | None = None,
    ) -> dict[tuple[str, str], dict]:
        """Most recent visible period per (symbol, field). The usual query for scoring."""
        latest: dict[tuple[str, str], dict] = {}
        for row in self.as_of(symbols, fields, as_of, max_age_days=max_age_days):
            key = (row["symbol"], row["field"])
            prev = latest.get(key)
            if prev is None or row["period_end"] > prev["period_end"]:
                latest[key] = row
        return latest

    def visible_count(self, as_of: datetime) -> int:
        """How many rows the world could see at ``as_of``. Useful in run reports."""
        cutoff = _utc(as_of).isoformat()
        return self._conn.execute(
            "SELECT COUNT(*) FROM pit WHERE published_at < ?", (cutoff,)
        ).fetchone()[0]
