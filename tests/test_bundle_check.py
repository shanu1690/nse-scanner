"""Tests for nse/quality/bundle_check.py: the post-write "verify what's
about to be deployed" gate (Phase 10), including a genuine end-to-end test
against nse/sitebuilder.py's real build() output.
"""

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from nse.quality.bundle_check import verify_bundle
from nse.quality.validators import Severity

from tests.test_sitebuilder_publish_gate import _synthetic_prices, _wire_cli


def _write(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        if isinstance(obj, str):
            fh.write(obj)
        else:
            json.dump(obj, fh)


def _valid_bundle(tmp_path, delivery_n=2, option_n=1, coverage=1.0, universe_size=None):
    d = str(tmp_path)
    _write(os.path.join(d, "delivery.json"), {"picks": [{"symbol": f"S{i}"} for i in range(delivery_n)]})
    _write(os.path.join(d, "options.json"), {"picks": [{"symbol": f"O{i}"} for i in range(option_n)]})
    _write(os.path.join(d, "scorecard.json"), {"del_rows": [], "opt_rows": []})
    manifest = {"delivery_picks": delivery_n, "option_picks": option_n, "coverage_pct": coverage}
    if universe_size is not None:
        manifest["universe_size"] = universe_size
    _write(os.path.join(d, "manifest.json"), manifest)
    return d


# ----------------------------------------------------------------- happy path
def test_valid_bundle_may_publish(tmp_path):
    d = _valid_bundle(tmp_path)
    report = verify_bundle(d)
    assert report.may_publish
    assert not report.failures


# --------------------------------------------------------------- missing file
def test_missing_required_file_fails(tmp_path):
    d = _valid_bundle(tmp_path)
    os.remove(os.path.join(d, "options.json"))
    report = verify_bundle(d)
    assert not report.may_publish
    fail = next(c for c in report.failures if c.name == "required_files")
    assert "options.json" in fail.offenders


# ------------------------------------------------------------------- malformed
def test_malformed_required_json_fails(tmp_path):
    d = _valid_bundle(tmp_path)
    _write(os.path.join(d, "manifest.json"), "{not valid json")
    report = verify_bundle(d)
    assert not report.may_publish
    assert any(c.name == "json_parse" for c in report.failures)


def test_malformed_non_required_json_still_fails(tmp_path):
    """A corrupted prices/X.json would break the dashboard just as badly as
    a corrupted delivery.json -- the scan isn't limited to the 4 required
    files."""
    d = _valid_bundle(tmp_path)
    _write(os.path.join(d, "prices", "X.json"), "{broken")
    report = verify_bundle(d)
    assert not report.may_publish
    fail = next(c for c in report.failures if c.name == "json_parse")
    assert any("prices" in o for o in fail.offenders)


# ------------------------------------------------------------ manifest consistency
def test_manifest_count_mismatch_fails(tmp_path):
    d = _valid_bundle(tmp_path, delivery_n=2)
    manifest = json.load(open(os.path.join(d, "manifest.json")))
    manifest["delivery_picks"] = 5  # doesn't match delivery.json's real 2 picks
    _write(os.path.join(d, "manifest.json"), manifest)
    report = verify_bundle(d)
    assert not report.may_publish
    fail = next(c for c in report.failures if c.name == "manifest_consistency")
    assert any("delivery_picks" in o for o in fail.offenders)


# ------------------------------------------------------------------------ coverage
def test_coverage_below_floor_fails(tmp_path):
    d = _valid_bundle(tmp_path, coverage=0.5)
    report = verify_bundle(d)
    assert not report.may_publish
    assert any(c.name == "coverage" for c in report.failures)


def test_coverage_missing_is_warn_not_fail(tmp_path):
    d = _valid_bundle(tmp_path)
    manifest = json.load(open(os.path.join(d, "manifest.json")))
    del manifest["coverage_pct"]
    _write(os.path.join(d, "manifest.json"), manifest)
    report = verify_bundle(d)
    assert report.may_publish  # WARN doesn't veto
    assert any(c.name == "coverage" and c.severity is Severity.WARN for c in report.checks)


# -------------------------------------------------------------------- credentials
def test_credential_shaped_content_fails(tmp_path):
    d = _valid_bundle(tmp_path)
    _write(os.path.join(d, "delivery.json"),
          {"picks": [{"symbol": "S0"}], "debug_note": "api_key=abc123"})
    # keep manifest's count matching (1 pick) so this doesn't also trip consistency
    manifest = json.load(open(os.path.join(d, "manifest.json")))
    manifest["delivery_picks"] = 1
    _write(os.path.join(d, "manifest.json"), manifest)
    report = verify_bundle(d)
    assert not report.may_publish
    fail = next(c for c in report.failures if c.name == "credential_scan")
    assert any("delivery.json" in o for o in fail.offenders)


# -------------------------------------------------------------------- end-to-end
def test_verify_bundle_against_real_sitebuilder_output(tmp_path, monkeypatch):
    """The genuine end-to-end check: run the real build() pipeline (Phase 2's
    DataValidator + Phase 8's risk gate + the actual _dump() writer), then
    verify_bundle() against what it actually wrote -- the whole pipeline's
    own output should be self-consistent by construction."""
    import nse.cli as cli_mod
    import nse.sitebuilder as sb

    symbols = [f"SYM{i}" for i in range(6)]
    prices = _synthetic_prices(symbols)
    _wire_cli(monkeypatch, symbols, prices, prices["SYM0"])

    out_dir = tmp_path / "site"
    sb.build(str(out_dir), top_n=5, quiet=True)

    # events_path points at a file that doesn't exist -- keeps this test
    # hermetic (read_events() -> []) instead of reading this session's
    # real, global data/cache/_events.jsonl.
    report = verify_bundle(str(out_dir / "data"), events_path=str(tmp_path / "no_events.jsonl"))
    assert report.may_publish, report.render()


# -------------------------------------------------------------- feed health
def _write_events(path, events):
    """events: list of (symbol, kind, hours_ago)."""
    import json as _json
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        for symbol, kind, hours_ago in events:
            ts = datetime.now(timezone.utc) - timedelta(hours=hours_ago)
            fh.write(_json.dumps({"symbol": symbol, "kind": kind, "message": "test",
                                  "ts": ts.isoformat()}) + "\n")


def test_feed_health_pass_when_fallback_rate_low(tmp_path):
    d = _valid_bundle(tmp_path / "data", universe_size=10)
    events_path = str(tmp_path / "events.jsonl")
    _write_events(events_path, [("S0", "provider_fallback", 1)])  # 1/10 = 10%
    report = verify_bundle(d, events_path=events_path)
    assert report.may_publish
    check = next(c for c in report.checks if c.name == "feed_health")
    assert check.severity is Severity.PASS


def test_feed_health_warns_when_fallback_rate_high(tmp_path):
    d = _valid_bundle(tmp_path / "data", universe_size=10)
    events_path = str(tmp_path / "events.jsonl")
    _write_events(events_path, [(f"S{i}", "provider_fallback", 1) for i in range(5)])  # 5/10 = 50%
    report = verify_bundle(d, events_path=events_path)
    assert report.may_publish  # WARN, not a veto
    check = next(c for c in report.checks if c.name == "feed_health")
    assert check.severity is Severity.WARN
    assert len(check.offenders) == 5


def test_feed_health_ignores_events_outside_the_window(tmp_path):
    d = _valid_bundle(tmp_path / "data", universe_size=10)
    events_path = str(tmp_path / "events.jsonl")
    _write_events(events_path, [(f"S{i}", "provider_fallback", 48) for i in range(5)])  # 48h ago, outside default 24h
    report = verify_bundle(d, events_path=events_path, events_since_hours=24.0)
    check = next(c for c in report.checks if c.name == "feed_health")
    assert check.severity is Severity.PASS


def test_feed_health_ignores_non_fallback_event_kinds(tmp_path):
    d = _valid_bundle(tmp_path / "data", universe_size=10)
    events_path = str(tmp_path / "events.jsonl")
    _write_events(events_path, [(f"S{i}", "unadjusted_split_rejected", 1) for i in range(5)])
    report = verify_bundle(d, events_path=events_path)
    check = next(c for c in report.checks if c.name == "feed_health")
    assert check.severity is Severity.PASS


def test_feed_health_warns_when_universe_size_missing(tmp_path):
    d = _valid_bundle(tmp_path / "data")  # no universe_size
    report = verify_bundle(d, events_path=str(tmp_path / "no_events.jsonl"))
    assert report.may_publish
    check = next(c for c in report.checks if c.name == "feed_health")
    assert check.severity is Severity.WARN


def test_cmd_verify_bundle_accepts_site_root_or_data_dir(tmp_path):
    """cmd_verify_bundle's auto-detection: passing the site root (with a
    data/ subdir) or the data/ dir itself both work."""
    from nse.quality.bundle_check import verify_bundle
    data_dir = _valid_bundle(tmp_path / "data")
    # verify_bundle itself only takes the data dir directly -- the site-root
    # vs data-dir auto-detection lives in cmd_verify_bundle (cli.py), tested
    # here at the level verify_bundle can be tested without argparse.
    report = verify_bundle(data_dir)
    assert report.may_publish


class _Args:
    def __init__(self, path):
        self.path = path


def test_cmd_verify_bundle_site_root_exits_zero_on_pass(tmp_path, capsys):
    from nse.cli import cmd_verify_bundle
    _valid_bundle(tmp_path / "data")  # site root = tmp_path, data/ subdir under it
    cmd_verify_bundle(_Args(str(tmp_path)))  # must not raise/exit
    assert "PUBLISH ALLOWED" in capsys.readouterr().out


def test_cmd_verify_bundle_exits_nonzero_on_fail(tmp_path, capsys):
    from nse.cli import cmd_verify_bundle
    d = _valid_bundle(tmp_path / "data")
    os.remove(os.path.join(d, "options.json"))
    with pytest.raises(SystemExit) as exc:
        cmd_verify_bundle(_Args(str(tmp_path)))
    assert exc.value.code != 0
    assert "PUBLISH BLOCKED" in capsys.readouterr().out


def test_cmd_verify_bundle_data_dir_directly(tmp_path, capsys):
    from nse.cli import cmd_verify_bundle
    data_dir = _valid_bundle(tmp_path)  # no "data" subdir -- pass it directly
    cmd_verify_bundle(_Args(data_dir))
    assert "PUBLISH ALLOWED" in capsys.readouterr().out
