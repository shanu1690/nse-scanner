"""Tests for nse/report.py, focused on build_report_from_bundle() -- the
path that reads an already-risk-gated site data bundle (delivery.json/
options.json, as `nse-scan site` writes them) instead of regenerating
picks itself. See its docstring: the OTHER path (cmd_report's default,
_today_delivery_picks/_today_option_picks) does NOT go through Phase 7's
options budget cap or Phase 8's risk gate, which is exactly the gap
build_report_from_bundle exists to close for anything automated."""

import json
import os

import numpy as np
import pandas as pd
import pytest

from nse import report
from nse import tracker


def _synthetic_price_frame(n=10, close=100.0):
    idx = pd.date_range(end=pd.Timestamp.today().normalize(), periods=n, freq="D")
    return pd.DataFrame({
        "Open": close, "High": close * 1.01, "Low": close * 0.99,
        "Close": close, "Volume": 100_000,
    }, index=idx)


@pytest.fixture
def isolated_journal(tmp_path, monkeypatch):
    """Point tracker's journal at a throwaway file, and stub out live price
    lookups so scorecard_data() never touches the network -- every journaled
    symbol resolves to a fixed synthetic close."""
    journal_path = tmp_path / "journal.json"
    monkeypatch.setattr(tracker, "JOURNAL", str(journal_path))

    import nse.data as data_mod
    frame = _synthetic_price_frame(close=100.0)
    monkeypatch.setattr(data_mod, "load_price_history", lambda symbol: frame.copy())
    monkeypatch.setattr(data_mod, "update_price_history",
                        lambda symbol, lookback_days, force=False: frame.copy())
    return journal_path


def _write_bundle(data_dir, manifest=None, delivery=None, options=None):
    os.makedirs(data_dir, exist_ok=True)
    if manifest is not None:
        with open(os.path.join(data_dir, "manifest.json"), "w") as fh:
            json.dump(manifest, fh)
    if delivery is not None:
        with open(os.path.join(data_dir, "delivery.json"), "w") as fh:
            json.dump(delivery, fh)
    if options is not None:
        with open(os.path.join(data_dir, "options.json"), "w") as fh:
            json.dump(options, fh)


SAMPLE_MANIFEST = {
    "date": "15 Sep 2026", "provider": "smartapi", "universe_size": 210,
    "coverage_pct": 1.0,
    "risk_gate": {"delivery_approved": 1, "delivery_vetoed": 6,
                  "options_approved": 1, "options_vetoed": 6},
}

SAMPLE_DELIVERY = {
    "style": "fade",
    "picks": [{
        "symbol": "JUBLFOOD", "score": 91.0, "entry": 470.25, "stop": 452.17,
        "target1": 482.3, "target2": 494.36, "position_size": 55,
        "rupee_risk": 994.4, "risk_pct_of_capital": 0.994, "reward_risk": 1.33,
        "sector": "Consumer Services",
    }],
}

SAMPLE_OPTIONS = {
    "picks": [{
        "symbol": "AUROPHARMA", "score": 92.0, "direction": "CE", "spot": 1660.0,
        "expiry": "29-Sep-2026", "dte": 13, "strategy": "debit_spread",
        "legs": [{"action": "BUY", "side": "CE", "strike": 1660.0, "premium": 30.6},
                 {"action": "SELL", "side": "CE", "strike": 1700.0, "premium": 16.25}],
        "cost": 7892.5, "max_loss": 7892.5, "max_profit": 14107.5,
        "breakeven": 1674.35, "probability": 0.4094, "lot_size": 550,
    }],
}


def test_build_report_from_bundle_reads_vetted_picks_and_journals_them(tmp_path, isolated_journal):
    data_dir = tmp_path / "site_data"
    _write_bundle(data_dir, SAMPLE_MANIFEST, SAMPLE_DELIVERY, SAMPLE_OPTIONS)

    built = report.build_report_from_bundle(str(data_dir), lookback_days=100)

    assert built["n_delivery"] == 1
    assert built["n_options"] == 1
    assert "15 Sep 2026" in built["subject"]

    # The picks -- position size, sector, budget-capped cost -- must be
    # visible in the body, not just counted.
    assert "JUBLFOOD" in built["body"]
    assert "Consumer Services" in built["body"]
    assert "AUROPHARMA" in built["body"]
    assert "7892.50" in built["body"] or "7892.5" in built["body"]

    # The risk-gate summary line must actually appear (both plain text and
    # HTML render() paths accept `extra`/`extra_html` -- confirm they're
    # wired, not silently dropped as `render()`'s `extra` param used to be).
    assert "delivery approved" in built["body"]
    assert "budget" in built["body"].lower()
    assert "Consumer Services" in built["html_body"] or "JUBLFOOD" in built["html_body"]

    # Journaled for the scorecard / dashboard Journal tab.
    journaled = tracker.load_picks()
    symbols = {p["symbol"] for p in journaled}
    assert symbols == {"JUBLFOOD", "AUROPHARMA"}
    delivery_row = next(p for p in journaled if p["symbol"] == "JUBLFOOD")
    assert delivery_row["type"] == "delivery"
    options_row = next(p for p in journaled if p["symbol"] == "AUROPHARMA")
    assert options_row["type"] == "options"
    assert options_row["strike"] == 1660.0  # from legs[0], the bought leg


def test_build_report_from_bundle_morning_mode_prefixes_checklist(tmp_path, isolated_journal):
    data_dir = tmp_path / "site_data"
    _write_bundle(data_dir, SAMPLE_MANIFEST, SAMPLE_DELIVERY, SAMPLE_OPTIONS)

    built = report.build_report_from_bundle(str(data_dir), lookback_days=100, morning=True)
    assert built["subject"].startswith("MORNING LIST")
    assert "HOW TO BUY TODAY" in built["body"]


def test_build_report_from_bundle_handles_missing_bundle_gracefully(tmp_path, isolated_journal):
    data_dir = tmp_path / "empty_dir"
    os.makedirs(data_dir)  # no manifest/delivery/options.json at all

    built = report.build_report_from_bundle(str(data_dir), lookback_days=100)
    assert built["n_delivery"] == 0
    assert built["n_options"] == 0
    # No picks to journal -- must not crash, and must not write anything.
    assert tracker.load_picks() == []


def test_render_includes_extra_lines():
    body = report.render("Subject", "delivery table", "", "scorecard text",
                         extra=["", "a risk-gate summary line"])
    assert "a risk-gate summary line" in body
    # extra appears after the timestamp, before the delivery section.
    assert body.index("a risk-gate summary line") < body.index("delivery table")


def test_render_html_includes_extra_html():
    html = report.render_html(
        "Subject", {"headers": [], "rows": []}, {"headers": [], "rows": []},
        None, extra_html="<p>risk gate summary</p>")
    assert "risk gate summary" in html


def test_legs_summary_formats_strike_and_side():
    legs = [{"action": "BUY", "side": "CE", "strike": 1660.0},
            {"action": "SELL", "side": "CE", "strike": 1700.0}]
    assert report._legs_summary(legs) == "BUY 1660CE / SELL 1700CE"


def test_legs_summary_handles_missing_strike():
    assert report._legs_summary([{"action": "BUY", "side": "CE"}]) == "BUY ?CE"
    assert report._legs_summary(None) == ""
    assert report._legs_summary([]) == ""
