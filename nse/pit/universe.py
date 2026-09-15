"""Point-in-time universe membership.

There is no historical Nifty/F&O constituent-date feed in this repo yet --
that is events/fundamentals ingest, Phase 4/5. Until it exists, applying
today's config.yaml universe list across a year of backtest history is a
look-ahead bias (Phase 1 audit, Risk #3): a name that joined the index or
turned F&O-eligible last month gets scored on history before it qualified.

This module does not fix that -- it can't, without real membership dates.
What it does is make the bias visible instead of silent: as_of() returns
today's static list only as an explicitly-unconfirmed fallback, and
`confirmed` tells the caller which situation it's in so every report can say
so plainly (the same "refuse to guess quietly" discipline as
nse/calendar/market_calendar.py's holiday lists).
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Optional, Sequence

__all__ = ["UniverseMembership", "MissingMembershipError"]

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "universe_history.json"


class MissingMembershipError(RuntimeError):
    """Raised in strict mode when no verified membership history is available."""


@dataclass(frozen=True)
class _Window:
    symbol: str
    valid_from: Optional[date]
    valid_to: Optional[date]

    def covers(self, day: date) -> bool:
        if self.valid_from is not None and day < self.valid_from:
            return False
        if self.valid_to is not None and day > self.valid_to:
            return False
        return True


class UniverseMembership:
    """Usage::

        um = UniverseMembership(static_universe=cfg["universe"]["symbols"])
        symbols = um.as_of(some_date)   # confirmed history if populated,
                                        # else today's static list
        um.confirmed                   # True only in the first case
    """

    def __init__(self, path: "str | Path" = DEFAULT_PATH, *,
                 static_universe: Sequence[str] = ()) -> None:
        self._path = Path(path)
        self._static = list(static_universe)
        self._windows: Optional[list[_Window]] = None
        self._verified = False

    def _load(self) -> None:
        if self._windows is not None:
            return
        if not self._path.exists():
            self._windows = []
            return
        try:
            payload = json.loads(self._path.read_text())
        except (OSError, ValueError):
            self._windows = []
            return
        self._verified = bool(payload.get("verified"))
        self._windows = [
            _Window(
                symbol=w["symbol"],
                valid_from=date.fromisoformat(w["valid_from"]) if w.get("valid_from") else None,
                valid_to=date.fromisoformat(w["valid_to"]) if w.get("valid_to") else None,
            )
            for w in payload.get("membership", [])
        ]

    @property
    def confirmed(self) -> bool:
        """True only when a verified, non-empty membership history backs as_of()."""
        self._load()
        return self._verified and bool(self._windows)

    def as_of(self, as_of_date: date, *, strict: bool = False) -> list[str]:
        """The universe as it existed on ``as_of_date``.

        strict=True raises rather than silently substituting today's static
        list -- use it wherever a caller wants a hard stop instead of a
        labeled approximation.
        """
        self._load()
        if self.confirmed:
            return sorted({w.symbol for w in self._windows if w.covers(as_of_date)})
        if strict:
            raise MissingMembershipError(
                f"no verified point-in-time universe membership at {self._path} -- "
                f"populate it from an index-reconstitution history (Phase 4/5) or "
                f"call with strict=False to accept today's static universe as an "
                f"explicitly-unconfirmed proxy"
            )
        return list(self._static)
