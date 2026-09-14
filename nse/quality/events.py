"""Structured, queryable record of provider-fallback / data-integrity events.

nse/smartapi.py and nse/data.py already print these to stderr for live-tail
visibility (the Risk #1 and Risk #10 fixes), but a print line disappears the
moment the run ends. This module is the same information, appended to a
small JSONL log, so a later consumer -- backtest.py's coverage report, first
of all -- can attribute a gap to something already caught instead of
recording an unexplained skip (Risk #7).
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

__all__ = ["DataEvent", "log_event", "read_events", "DEFAULT_EVENTS_PATH"]

DEFAULT_EVENTS_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "cache" / "_events.jsonl"
)

_VALID_KINDS = {
    "provider_fallback",         # SmartAPI -> yfinance, any cause
    "unadjusted_split_rejected", # candles() caught a split/bonus-shaped jump
    "regime_mismatch_drop",      # _clean_combined() dropped a stale segment
}


@dataclass(frozen=True)
class DataEvent:
    symbol: str
    kind: str
    message: str
    ts: datetime

    def __post_init__(self) -> None:
        if self.kind not in _VALID_KINDS:
            raise ValueError(
                f"unknown DataEvent kind {self.kind!r}, expected one of "
                f"{sorted(_VALID_KINDS)}"
            )
        if self.ts.tzinfo is None:
            raise ValueError("DataEvent.ts must be timezone-aware")

    def to_json(self) -> str:
        d = asdict(self)
        d["ts"] = self.ts.astimezone(timezone.utc).isoformat()
        return json.dumps(d)

    @staticmethod
    def from_json(line: str) -> "DataEvent":
        d = json.loads(line)
        return DataEvent(
            symbol=d["symbol"], kind=d["kind"], message=d["message"],
            ts=datetime.fromisoformat(d["ts"]),
        )


def log_event(symbol: str, kind: str, message: str, *,
              path: "str | Path | None" = None) -> None:
    """Append one event. Never raises on an I/O problem -- this is a
    diagnostic side-channel and must never be the reason a fetch fails.

    ``path`` defaults to DEFAULT_EVENTS_PATH looked up at call time (not
    bound into the signature) so tests can monkeypatch the module constant
    and have every un-parameterised call site pick it up.
    """
    try:
        event = DataEvent(symbol=symbol, kind=kind, message=message,
                           ts=datetime.now(timezone.utc))
        path = Path(path) if path is not None else DEFAULT_EVENTS_PATH
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a") as fh:
            fh.write(event.to_json() + "\n")
    except (OSError, ValueError):
        pass


def read_events(*, path: "str | Path | None" = None,
                 symbol: Optional[str] = None,
                 since: Optional[datetime] = None) -> list[DataEvent]:
    """All events, optionally filtered by symbol and/or a minimum timestamp.
    A malformed line is skipped rather than aborting the whole read -- this
    log is diagnostic, and one bad line from a crashed write shouldn't hide
    every event before or after it."""
    path = Path(path) if path is not None else DEFAULT_EVENTS_PATH
    if not path.exists():
        return []
    out: list[DataEvent] = []
    with open(path) as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                ev = DataEvent.from_json(line)
            except (ValueError, KeyError, json.JSONDecodeError):
                continue
            if symbol is not None and ev.symbol != symbol:
                continue
            if since is not None and ev.ts < since:
                continue
            out.append(ev)
    return out
