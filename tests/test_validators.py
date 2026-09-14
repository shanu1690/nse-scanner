from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

from nse.quality.validators import (
    DataValidator, Severity, scan_bundle_for_credentials,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 4, 10, 0, tzinfo=UTC)
UNIVERSE = [f"SYM{i}" for i in range(20)]


def good_frame(n=20, age_minutes=1):
    fetched = NOW - timedelta(minutes=age_minutes)
    return pd.DataFrame({
        "symbol": UNIVERSE[:n],
        "close": [100.0] * n,
        "high": [101.0] * n,
        "low": [99.0] * n,
        "volume": [10000] * n,
        "score": [50.0] * n,
        "fetched_at": [fetched] * n,
    })


def test_clean_frame_publishes():
    report = DataValidator().validate(good_frame(), universe=UNIVERSE, as_of=NOW)
    assert report.may_publish, report.render()


def test_missing_column_blocks_publish():
    df = good_frame().drop(columns=["score"])
    report = DataValidator().validate(df, universe=UNIVERSE, as_of=NOW)
    assert not report.may_publish


def test_low_coverage_blocks_and_names_the_missing():
    report = DataValidator().validate(good_frame(n=10), universe=UNIVERSE, as_of=NOW)
    assert not report.may_publish
    coverage = next(c for c in report.checks if c.name == "coverage")
    assert coverage.severity is Severity.FAIL
    assert "SYM15" in coverage.offenders


def test_stale_data_blocks_publish():
    """Stale prices presented as live are worse than no prices."""
    report = DataValidator().validate(
        good_frame(age_minutes=90), universe=UNIVERSE, as_of=NOW
    )
    assert not report.may_publish
    assert any(c.name == "staleness" and c.severity is Severity.FAIL for c in report.checks)


def test_missing_fetched_at_blocks_publish():
    df = good_frame().drop(columns=["fetched_at"])
    report = DataValidator().validate(df, universe=UNIVERSE, as_of=NOW)
    assert not report.may_publish


def test_future_timestamp_blocks_publish():
    df = good_frame()
    df.loc[0, "fetched_at"] = NOW + timedelta(hours=2)
    report = DataValidator().validate(df, universe=UNIVERSE, as_of=NOW)
    assert not report.may_publish


def test_duplicate_symbol_blocks_publish():
    df = good_frame()
    df.loc[0, "symbol"] = df.loc[1, "symbol"]
    report = DataValidator().validate(df, universe=UNIVERSE, as_of=NOW)
    assert not report.may_publish


def test_impossible_ohlc_blocks_publish():
    df = good_frame()
    df.loc[0, "close"] = 500.0     # above high
    report = DataValidator().validate(df, universe=UNIVERSE, as_of=NOW)
    assert not report.may_publish


def test_probability_outside_unit_interval_blocks_publish():
    df = good_frame()
    df["probability"] = 0.5
    df.loc[0, "probability"] = 1.7
    report = DataValidator().validate(df, universe=UNIVERSE, as_of=NOW)
    assert not report.may_publish


def test_row_count_collapse_blocks_publish():
    """200 symbols yesterday, 20 today means something upstream broke."""
    report = DataValidator().validate(
        good_frame(), universe=UNIVERSE, as_of=NOW, previous_row_count=200
    )
    assert not report.may_publish


def test_failures_are_never_downgraded_to_warnings():
    report = DataValidator().validate(good_frame(n=5), universe=UNIVERSE, as_of=NOW)
    assert report.failures and not report.may_publish
    assert "PUBLISH BLOCKED" in report.render()


def test_naive_as_of_is_rejected():
    with pytest.raises(ValueError):
        DataValidator().validate(
            good_frame(), universe=UNIVERSE, as_of=datetime(2026, 9, 4, 10, 0)
        )


def test_credential_scan_catches_leaked_keys():
    assert scan_bundle_for_credentials('{"api_key": "abc123"}')
    assert scan_bundle_for_credentials('{"feedToken": "x"}')
    assert scan_bundle_for_credentials('{"symbol": "INFY", "close": 100}') == []
