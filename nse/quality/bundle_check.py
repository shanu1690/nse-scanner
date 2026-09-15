"""Post-write bundle validation -- Phase 10's "bundle validation before
deploy" gate.

Distinct from DataValidator (nse/quality/validators.py), which checks the
pre-write scored-picks DataFrame *inside* `nse-scan site`: this checks the
ACTUAL FILES that run wrote to disk, as a separate, independent pass run
right before a CI deploy step -- defence in depth, the same principle
already applied to the options budget cap (nse/risk/enforce.py) and the
per-file credential scan in sitebuilder._dump(). A bug in either of those
in-process checks, or a file that appears in the bundle directory through
some path that never went through them, still gets caught here.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

from .events import read_events
from .validators import CheckResult, Severity, ValidationReport, scan_bundle_for_credentials

__all__ = ["REQUIRED_FILES", "MIN_COVERAGE", "FEED_DEGRADED_FALLBACK_RATE", "verify_bundle"]

REQUIRED_FILES = ("delivery.json", "options.json", "scorecard.json", "manifest.json")
# Same floor DataValidator's own coverage check uses (nse/quality/
# validators.py) -- restated here, not imported, because this module reads
# it back OFF the manifest rather than recomputing it, and the two checks
# should agree without a hidden coupling between the modules.
MIN_COVERAGE = 0.95

# PROJECT_BRIEF.md Phase 10 asks for "alerting on ... feed disconnection".
# This architecture has no live feed (Section 4's SmartWebSocketV2 backend
# is a separate, unbuilt phase) -- the honest analog for a batch/static-site
# pipeline is a high PROVIDER_FALLBACK rate: SmartAPI failing for most of
# the universe, silently absorbed by the yfinance fallback (Risk #1/#10's
# whole point was that this fallback "looks identical to a normal
# successful fetch" otherwise -- coverage stays high even when the PRIMARY
# feed is down). WARN, not FAIL: the fallback existing and working is the
# system behaving correctly, not corruption -- this is a health signal to
# look at, not a reason to block a publish that's otherwise fine.
FEED_DEGRADED_FALLBACK_RATE = 0.30


def _walk_json_files(data_dir: str):
    for root, _dirs, files in os.walk(data_dir):
        for fname in files:
            if fname.endswith(".json"):
                yield os.path.join(root, fname)


def verify_bundle(data_dir: str, *, events_path: Optional[str] = None,
                   events_since_hours: float = 24.0) -> ValidationReport:
    """The full post-write check. Call this right before a deploy step --
    see `nse-scan verify-bundle` / .github/workflows/nightly.yml.

    `events_path`/`events_since_hours` control the feed-health check only
    (see FEED_DEGRADED_FALLBACK_RATE); `events_path=None` uses
    nse.quality.events's own default location."""
    report = ValidationReport()

    missing = [f for f in REQUIRED_FILES if not os.path.exists(os.path.join(data_dir, f))]
    if missing:
        report.add(CheckResult("required_files", Severity.FAIL,
                               f"{len(missing)} required file(s) missing", offenders=missing))
    else:
        report.add(CheckResult("required_files", Severity.PASS,
                               f"all {len(REQUIRED_FILES)} required files present"))

    # Every .json file anywhere in the bundle must parse -- not just the
    # required ones, since a corrupted prices/XYZ.json or chains/XYZ.json
    # would break the dashboard just as badly.
    parsed: dict = {}
    malformed = []
    all_json_paths = list(_walk_json_files(data_dir))
    for path in all_json_paths:
        rel = os.path.relpath(path, data_dir)
        try:
            with open(path) as fh:
                text = fh.read()
            parsed[rel] = json.loads(text)
        except (OSError, ValueError) as exc:
            malformed.append(f"{rel}: {exc}")
    if malformed:
        report.add(CheckResult("json_parse", Severity.FAIL,
                               f"{len(malformed)}/{len(all_json_paths)} JSON file(s) failed to parse",
                               offenders=malformed))
    else:
        report.add(CheckResult("json_parse", Severity.PASS,
                               f"all {len(all_json_paths)} JSON files parse"))

    if missing or malformed:
        # A missing/corrupt required file means the checks below (which
        # read specific keys out of them) can't run safely -- stop here
        # rather than crash on a KeyError and report a confusing traceback
        # instead of the real problem.
        return report

    manifest = parsed["manifest.json"]
    delivery = parsed["delivery.json"]
    options = parsed["options.json"]

    mismatches = []
    if manifest.get("delivery_picks") != len(delivery.get("picks", [])):
        mismatches.append(f"delivery_picks: manifest says {manifest.get('delivery_picks')}, "
                          f"delivery.json actually has {len(delivery.get('picks', []))}")
    if manifest.get("option_picks") != len(options.get("picks", [])):
        mismatches.append(f"option_picks: manifest says {manifest.get('option_picks')}, "
                          f"options.json actually has {len(options.get('picks', []))}")
    if mismatches:
        report.add(CheckResult("manifest_consistency", Severity.FAIL,
                               "manifest.json's own counts disagree with the files it describes "
                               "-- looks like a partial or stale write", offenders=mismatches))
    else:
        report.add(CheckResult("manifest_consistency", Severity.PASS,
                               "manifest.json's counts match the actual files"))

    coverage = manifest.get("coverage_pct")
    if coverage is None:
        report.add(CheckResult("coverage", Severity.WARN,
                               "manifest.json has no coverage_pct recorded"))
    elif coverage < MIN_COVERAGE:
        report.add(CheckResult("coverage", Severity.FAIL,
                               f"coverage {coverage:.1%} is below the {MIN_COVERAGE:.0%} floor"))
    else:
        report.add(CheckResult("coverage", Severity.PASS, f"coverage {coverage:.1%}"))

    # Independent second pass over every JSON file actually on disk -- not
    # a replacement for sitebuilder._dump()'s own per-file scan (which ran
    # before any of these bytes were written), a check that nothing
    # credential-shaped is present in what's ABOUT to be deployed, however
    # it got there.
    leaked = []
    for rel, _obj in parsed.items():
        path = os.path.join(data_dir, rel)
        try:
            with open(path) as fh:
                text = fh.read()
        except OSError:
            continue
        hits = scan_bundle_for_credentials(text)
        if hits:
            leaked.append(f"{rel}: {hits}")
    if leaked:
        report.add(CheckResult("credential_scan", Severity.FAIL,
                               "credential-shaped content found in a file about to be published",
                               offenders=leaked))
    else:
        report.add(CheckResult("credential_scan", Severity.PASS,
                               "no credential-shaped content found"))

    universe_size = manifest.get("universe_size")
    if universe_size:
        since = datetime.now(timezone.utc) - timedelta(hours=events_since_hours)
        try:
            events = read_events(path=events_path, since=since)
        except Exception:
            events = []
        fallback_symbols = {e.symbol for e in events if e.kind == "provider_fallback"}
        rate = len(fallback_symbols) / universe_size
        if rate >= FEED_DEGRADED_FALLBACK_RATE:
            report.add(CheckResult(
                "feed_health", Severity.WARN,
                f"{len(fallback_symbols)}/{universe_size} symbols ({rate:.0%}) fell back off "
                f"the primary provider in the last {events_since_hours:.0f}h -- the fallback "
                f"is working as designed, but this rate suggests the PRIMARY feed is degraded, "
                f"not just an occasional miss",
                offenders=sorted(fallback_symbols)[:20]))
        else:
            report.add(CheckResult(
                "feed_health", Severity.PASS,
                f"{len(fallback_symbols)}/{universe_size} symbols ({rate:.0%}) on provider fallback"))
    else:
        report.add(CheckResult("feed_health", Severity.WARN,
                               "manifest.json has no universe_size -- can't compute a fallback rate"))

    return report
