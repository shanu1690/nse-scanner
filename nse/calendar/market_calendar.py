"""NSE trading calendar and job scheduling times.

Design decision worth understanding: this module REFUSES to guess. If the holiday
list for a year is missing, every query for that year raises rather than assuming
Mon-Fri. A silently wrong calendar shifts returns onto the wrong days and corrupts
a backtest in a way that is almost impossible to spot afterwards. Loud failure is
cheaper.

Populate ``config/holidays/<year>.json`` from the official NSE trading holiday
circular for that year, then run ``verify_year()`` in CI.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

__all__ = [
    "IST",
    "MarketCalendar",
    "MissingCalendarError",
    "PRE_OPEN_START",
    "MARKET_OPEN",
    "MARKET_CLOSE",
    "DECISION_POINTS",
]

IST = ZoneInfo("Asia/Kolkata")

PRE_OPEN_START = time(9, 0)
PRE_OPEN_END = time(9, 15)
MARKET_OPEN = time(9, 15)
MARKET_CLOSE = time(15, 30)

#: Times at which NEW calls may be generated. Between these, the system emits
#: invalidation alerts on open ideas only. Continuous re-issuing produces
#: overtrading, which is the expensive failure mode for a beginner.
DECISION_POINTS = (time(8, 45), time(9, 5), time(9, 45), time(11, 30), time(14, 30))


class MissingCalendarError(RuntimeError):
    """Raised when the holiday list for a requested year has not been supplied."""


class MarketCalendar:
    def __init__(self, holiday_dir: str | Path = "config/holidays") -> None:
        self._dir = Path(holiday_dir)
        self._cache: dict[int, set[date]] = {}
        self._special: dict[int, dict[date, dict]] = {}

    # ---------------------------------------------------------------- loading
    def _load_year(self, year: int) -> None:
        if year in self._cache:
            return
        path = self._dir / f"{year}.json"
        if not path.exists():
            raise MissingCalendarError(
                f"No holiday list for {year} at {path}. Populate it from the official "
                f"NSE trading holiday circular. This module will not guess: an "
                f"unverified calendar corrupts backtests silently."
            )
        payload = json.loads(path.read_text())
        if not payload.get("verified"):
            raise MissingCalendarError(
                f"{path} is marked verified=false. Check it against the NSE circular "
                f"and set verified=true before using it."
            )
        self._cache[year] = {
            date.fromisoformat(d) for d in payload.get("trading_holidays", [])
        }
        self._special[year] = {
            date.fromisoformat(s["date"]): s for s in payload.get("special_sessions", [])
        }

    def verify_year(self, year: int) -> None:
        """Raise unless a verified calendar exists for ``year``. Call this in CI."""
        self._load_year(year)

    # ------------------------------------------------------------- predicates
    def is_trading_day(self, day: date) -> bool:
        self._load_year(day.year)
        if day in self._special[day.year]:
            return True  # muhurat and other special sessions trade
        if day.weekday() >= 5:
            return False
        return day not in self._cache[day.year]

    def is_open(self, moment: datetime) -> bool:
        """Is the normal session live at this instant? Pre-open does not count."""
        local = moment.astimezone(IST)
        if not self.is_trading_day(local.date()):
            return False
        session = self.session_times(local.date())
        return session[0] <= local.time() < session[1]

    def is_pre_open(self, moment: datetime) -> bool:
        local = moment.astimezone(IST)
        if not self.is_trading_day(local.date()):
            return False
        return PRE_OPEN_START <= local.time() < PRE_OPEN_END

    def session_times(self, day: date) -> tuple[time, time]:
        """Open and close for a day, honouring special sessions like muhurat."""
        self._load_year(day.year)
        special = self._special[day.year].get(day)
        if special and "open" in special and "close" in special:
            return (
                time.fromisoformat(special["open"]),
                time.fromisoformat(special["close"]),
            )
        return MARKET_OPEN, MARKET_CLOSE

    # ---------------------------------------------------------- day arithmetic
    def next_trading_day(self, day: date) -> date:
        probe = day + timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(probe):
                return probe
            probe += timedelta(days=1)
        raise MissingCalendarError(f"no trading day within 30 days of {day}")

    def previous_trading_day(self, day: date) -> date:
        probe = day - timedelta(days=1)
        for _ in range(30):
            if self.is_trading_day(probe):
                return probe
            probe -= timedelta(days=1)
        raise MissingCalendarError(f"no trading day within 30 days before {day}")

    def trading_days(self, start: date, end: date) -> list[date]:
        out, probe = [], start
        while probe <= end:
            if self.is_trading_day(probe):
                out.append(probe)
            probe += timedelta(days=1)
        return out

    def sessions_between(self, start: date, end: date) -> int:
        """Trading sessions strictly after ``start`` up to and including ``end``.

        This is the horizon unit for a '5-session' target. Calendar days are not
        sessions, and using them silently changes the holding period across
        holiday weeks.
        """
        if end < start:
            return 0
        return len([d for d in self.trading_days(start, end) if d > start])

    def shift_sessions(self, day: date, n: int) -> date:
        """The date ``n`` trading sessions after ``day`` (negative for before)."""
        step = self.next_trading_day if n >= 0 else self.previous_trading_day
        probe = day
        for _ in range(abs(n)):
            probe = step(probe)
        return probe
