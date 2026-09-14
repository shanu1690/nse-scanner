from datetime import datetime, timedelta, timezone

import pytest

from nse.quality.events import DataEvent, log_event, read_events


def test_log_and_read_roundtrip(tmp_path):
    path = tmp_path / "events.jsonl"
    log_event("INFY", "provider_fallback", "smartapi rate-limited", path=path)
    log_event("TCS", "unadjusted_split_rejected", "1 jump detected", path=path)

    events = read_events(path=path)
    assert [e.symbol for e in events] == ["INFY", "TCS"]
    assert events[0].kind == "provider_fallback"
    assert events[0].ts.tzinfo is not None


def test_read_filters_by_symbol(tmp_path):
    path = tmp_path / "events.jsonl"
    log_event("INFY", "provider_fallback", "x", path=path)
    log_event("TCS", "provider_fallback", "y", path=path)
    assert [e.symbol for e in read_events(path=path, symbol="TCS")] == ["TCS"]


def test_read_filters_by_since(tmp_path):
    path = tmp_path / "events.jsonl"
    old = DataEvent("INFY", "provider_fallback", "old",
                     ts=datetime.now(timezone.utc) - timedelta(days=2))
    with open(path, "w") as fh:
        fh.write(old.to_json() + "\n")
    log_event("INFY", "provider_fallback", "new", path=path)

    recent = read_events(path=path, since=datetime.now(timezone.utc) - timedelta(hours=1))
    assert [e.message for e in recent] == ["new"]


def test_missing_file_returns_empty(tmp_path):
    assert read_events(path=tmp_path / "nope.jsonl") == []


def test_malformed_line_is_skipped_not_fatal(tmp_path):
    path = tmp_path / "events.jsonl"
    with open(path, "w") as fh:
        fh.write("not json at all\n")
    log_event("INFY", "provider_fallback", "ok", path=path)
    events = read_events(path=path)
    assert len(events) == 1
    assert events[0].message == "ok"


def test_unknown_kind_rejected():
    with pytest.raises(ValueError, match="unknown DataEvent kind"):
        DataEvent("INFY", "bogus_kind", "msg", ts=datetime.now(timezone.utc))


def test_naive_timestamp_rejected():
    with pytest.raises(ValueError, match="timezone-aware"):
        DataEvent("INFY", "provider_fallback", "msg", ts=datetime.now())


def test_log_event_never_raises_on_bad_path():
    # A path under a file (not a directory) can't be mkdir'd into -- log_event
    # must swallow this, not crash the fetch it's annotating.
    import tempfile
    with tempfile.NamedTemporaryFile() as f:
        log_event("INFY", "provider_fallback", "msg", path=f"{f.name}/sub/events.jsonl")
