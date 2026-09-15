"""The leakage tests. If these ever fail, no backtest number in this repo means anything."""
from datetime import datetime, timezone

import pytest

from nse.pit.store import LookaheadError, PITRecord, PointInTimeStore

UTC = timezone.utc


def rec(sym, field, val, period_end, published_at, source="test"):
    return PITRecord(sym, field, val, period_end, published_at, source)


@pytest.fixture
def store():
    with PointInTimeStore() as s:
        yield s


def test_query_cannot_see_records_published_after_the_decision(store):
    """The core guarantee: Q3 results filed in February are invisible in January."""
    store.put([
        rec("INFY", "revenue", 100, datetime(2025, 12, 31, tzinfo=UTC),
            datetime(2026, 2, 10, tzinfo=UTC)),
    ])
    decision = datetime(2026, 1, 15, tzinfo=UTC)
    assert store.as_of(["INFY"], ["revenue"], decision) == []

    after = datetime(2026, 2, 11, tzinfo=UTC)
    assert len(store.as_of(["INFY"], ["revenue"], after)) == 1


def test_publication_boundary_is_strict(store):
    """published_at == as_of is NOT visible. A tie goes to caution."""
    published = datetime(2026, 2, 10, 9, 0, tzinfo=UTC)
    store.put([rec("TCS", "eps", 5, datetime(2025, 12, 31, tzinfo=UTC), published)])
    assert store.as_of(["TCS"], ["eps"], published) == []
    assert len(store.as_of(["TCS"], ["eps"],
                          datetime(2026, 2, 10, 9, 0, 1, tzinfo=UTC))) == 1


def test_period_end_keying_would_have_leaked(store):
    """Documents the bug this store exists to prevent.

    Keyed to period end, this value looks available on 31 Dec. Keyed to
    publication, it is not available until 10 Feb - six weeks of hindsight.
    """
    period_end = datetime(2025, 12, 31, tzinfo=UTC)
    published = datetime(2026, 2, 10, tzinfo=UTC)
    store.put([rec("HDFCBANK", "revenue", 200, period_end, published)])
    assert store.as_of(["HDFCBANK"], ["revenue"], period_end) == []
    leaked_days = (published - period_end).days
    assert leaked_days == 41


def test_restatement_returns_the_value_the_market_saw(store):
    """A February restatement must not retroactively change what January saw."""
    period_end = datetime(2025, 9, 30, tzinfo=UTC)
    store.put([
        rec("WIPRO", "eps", 10, period_end, datetime(2025, 10, 20, tzinfo=UTC)),
        rec("WIPRO", "eps", 8, period_end, datetime(2026, 2, 1, tzinfo=UTC)),
    ])
    in_january = store.as_of(["WIPRO"], ["eps"], datetime(2026, 1, 5, tzinfo=UTC))
    assert [r["value"] for r in in_january] == ["10"]

    in_march = store.as_of(["WIPRO"], ["eps"], datetime(2026, 3, 1, tzinfo=UTC))
    assert [r["value"] for r in in_march] == ["8"]


def test_latest_as_of_picks_newest_visible_period(store):
    store.put([
        rec("SBIN", "eps", 3, datetime(2025, 6, 30, tzinfo=UTC),
            datetime(2025, 7, 25, tzinfo=UTC)),
        rec("SBIN", "eps", 4, datetime(2025, 9, 30, tzinfo=UTC),
            datetime(2025, 10, 25, tzinfo=UTC)),
    ])
    latest = store.latest_as_of(["SBIN"], ["eps"], datetime(2025, 8, 1, tzinfo=UTC))
    assert latest[("SBIN", "eps")]["value"] == "3"

    latest = store.latest_as_of(["SBIN"], ["eps"], datetime(2025, 11, 1, tzinfo=UTC))
    assert latest[("SBIN", "eps")]["value"] == "4"


def test_stale_periods_can_be_aged_out(store):
    """A company that stopped reporting must not contribute forever."""
    store.put([
        rec("ZOMBIE", "eps", 1, datetime(2023, 3, 31, tzinfo=UTC),
            datetime(2023, 5, 1, tzinfo=UTC)),
    ])
    now = datetime(2026, 1, 1, tzinfo=UTC)
    assert len(store.as_of(["ZOMBIE"], ["eps"], now)) == 1
    assert store.as_of(["ZOMBIE"], ["eps"], now, max_age_days=365) == []


def test_publication_before_period_end_is_rejected():
    with pytest.raises(LookaheadError):
        rec("X", "revenue", 1, datetime(2026, 3, 31, tzinfo=UTC),
            datetime(2026, 3, 1, tzinfo=UTC))


def test_naive_datetimes_are_rejected(store):
    with pytest.raises(ValueError, match="timezone-aware"):
        store.as_of(["X"], ["y"], datetime(2026, 1, 1))


def test_ingestion_is_idempotent(store):
    r = rec("INFY", "eps", 7, datetime(2025, 12, 31, tzinfo=UTC),
            datetime(2026, 1, 20, tzinfo=UTC))
    store.put([r])
    store.put([r])
    assert len(store.as_of(["INFY"], ["eps"], datetime(2026, 2, 1, tzinfo=UTC))) == 1
