import json
from datetime import date, datetime

import pytest

from nse.calendar.market_calendar import (
    IST, MarketCalendar, MissingCalendarError,
)


@pytest.fixture
def cal(tmp_path):
    (tmp_path / "2026.json").write_text(json.dumps({
        "year": 2026,
        "verified": True,
        "trading_holidays": ["2026-01-26", "2026-03-06"],
        "special_sessions": [
            {"date": "2026-11-08", "name": "Muhurat", "open": "18:00", "close": "19:00"}
        ],
    }))
    return MarketCalendar(tmp_path)


def test_missing_year_raises_rather_than_guessing(tmp_path):
    """A calendar that quietly assumes Mon-Fri corrupts backtests invisibly."""
    cal = MarketCalendar(tmp_path)
    with pytest.raises(MissingCalendarError, match="will not guess"):
        cal.is_trading_day(date(2027, 5, 3))


def test_unverified_calendar_is_refused(tmp_path):
    (tmp_path / "2026.json").write_text(json.dumps(
        {"year": 2026, "verified": False, "trading_holidays": []}
    ))
    with pytest.raises(MissingCalendarError, match="verified"):
        MarketCalendar(tmp_path).is_trading_day(date(2026, 5, 4))


def test_weekends_and_holidays_are_closed(cal):
    assert cal.is_trading_day(date(2026, 9, 4)) is True      # Friday
    assert cal.is_trading_day(date(2026, 9, 5)) is False     # Saturday
    assert cal.is_trading_day(date(2026, 1, 26)) is False    # holiday


def test_special_session_trades_and_has_its_own_hours(cal):
    muhurat = date(2026, 11, 8)                              # a Sunday
    assert muhurat.weekday() == 6
    assert cal.is_trading_day(muhurat) is True
    assert cal.session_times(muhurat) == (
        datetime.strptime("18:00", "%H:%M").time(),
        datetime.strptime("19:00", "%H:%M").time(),
    )


def test_is_open_respects_session_hours(cal):
    friday = date(2026, 9, 4)
    assert cal.is_open(datetime(2026, 9, 4, 11, 0, tzinfo=IST))
    assert not cal.is_open(datetime(2026, 9, 4, 9, 5, tzinfo=IST))    # pre-open
    assert not cal.is_open(datetime(2026, 9, 4, 16, 0, tzinfo=IST))
    assert cal.is_pre_open(datetime(2026, 9, 4, 9, 5, tzinfo=IST))
    assert cal.is_trading_day(friday)


def test_day_arithmetic_skips_holidays(cal):
    # 26 Jan 2026 is a Monday holiday; the previous session is Friday the 23rd.
    assert cal.next_trading_day(date(2026, 1, 23)) == date(2026, 1, 27)
    assert cal.previous_trading_day(date(2026, 1, 27)) == date(2026, 1, 23)


def test_sessions_are_counted_not_calendar_days(cal):
    """A 5-session horizon is not 5 calendar days across a holiday week."""
    start, end = date(2026, 1, 22), date(2026, 1, 29)
    assert (end - start).days == 7
    assert cal.sessions_between(start, end) == 4    # 23, 27, 28, 29


def test_shift_sessions_matches_horizon_semantics(cal):
    assert cal.shift_sessions(date(2026, 1, 23), 1) == date(2026, 1, 27)
    assert cal.shift_sessions(date(2026, 1, 27), -1) == date(2026, 1, 23)
