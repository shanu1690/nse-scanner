"""Phase 8 wiring: nse/risk/'s veto engine gates nse/sitebuilder.py's
delivery picks (per-pick veto, not an all-or-nothing PublishBlocked like
DataValidator), and _options_data() independently re-verifies the Rs
10,000 budget cap before anything reaches options.json.
"""

import json

import pandas as pd
import pytest

import nse.cli as cli_mod
import nse.sitebuilder as sb
import nse.tracker as tracker_mod

from tests.test_sitebuilder_publish_gate import _synthetic_prices, _wire_cli


def test_delivery_picks_carry_risk_sizing_fields(tmp_path, monkeypatch):
    symbols = [f"SYM{i}" for i in range(5)]
    prices = _synthetic_prices(symbols)
    _wire_cli(monkeypatch, symbols, prices, prices["SYM0"])
    monkeypatch.setitem(cli_mod.CONFIG, "risk", {
        "capital": 1_000_000.0, "per_trade_risk_pct": 0.5,
        "max_portfolio_heat_pct": 100.0, "max_open_ideas": 50,
    })

    out_dir = tmp_path / "site"
    sb.build(str(out_dir), top_n=5, quiet=True)

    delivery = json.loads((out_dir / "data" / "delivery.json").read_text())
    assert delivery["picks"]
    for pick in delivery["picks"]:
        assert "position_size" in pick and pick["position_size"] > 0
        assert "rupee_risk" in pick and pick["rupee_risk"] > 0
        assert "sector" in pick  # None -- no verified sector map, not silently omitted


def test_tight_portfolio_heat_cap_vetoes_some_picks_and_records_it(tmp_path, monkeypatch):
    symbols = [f"SYM{i}" for i in range(8)]
    prices = _synthetic_prices(symbols)
    _wire_cli(monkeypatch, symbols, prices, prices["SYM0"])
    # 8 picks, each risking 1% of capital by default -> 8% total heat if all
    # approved; cap it at 2% so only ~2 can fit.
    monkeypatch.setitem(cli_mod.CONFIG, "risk", {
        "capital": 100_000.0, "per_trade_risk_pct": 1.0,
        "max_portfolio_heat_pct": 2.0, "max_open_ideas": 50,
    })

    out_dir = tmp_path / "site"
    sb.build(str(out_dir), top_n=8, quiet=True)

    manifest = json.loads((out_dir / "data" / "manifest.json").read_text())
    delivery = json.loads((out_dir / "data" / "delivery.json").read_text())
    assert manifest["risk_gate"]["delivery_vetoed"] > 0
    assert len(delivery["picks"]) == manifest["risk_gate"]["delivery_approved"]
    assert len(delivery["picks"]) < 8


def test_options_data_drops_pick_over_budget(tmp_path, monkeypatch):
    symbols = [f"SYM{i}" for i in range(3)]
    prices = _synthetic_prices(symbols)
    _wire_cli(monkeypatch, symbols, prices, prices["SYM0"])

    def fake_scan_options(*a, **k):
        return [
            {"symbol": "SYM0", "score": 80.0, "direction": "CE", "spot": 100.0,
             "expiry": "25-Sep-2026", "lot_size": 1000,
             "picks": [{"strike": 100, "premium": 50.0, "breakeven": 150.0, "iv": 20.0}]},
            # cost = 50*1000 = 50,000 -- well over the Rs 10,000 default cap
            {"symbol": "SYM1", "score": 75.0, "direction": "PE", "spot": 100.0,
             "expiry": "25-Sep-2026", "lot_size": 100,
             "picks": [{"strike": 100, "premium": 20.0, "breakeven": 80.0, "iv": 20.0}]},
            # cost = 20*100 = 2,000 -- within cap
        ]
    monkeypatch.setattr(cli_mod, "scan_options", fake_scan_options)

    out_dir = tmp_path / "site"
    sb.build(str(out_dir), top_n=5, quiet=True)

    options = json.loads((out_dir / "data" / "options.json").read_text())
    symbols_published = {p["symbol"] for p in options["picks"]}
    assert "SYM0" not in symbols_published  # over budget -- dropped
    assert "SYM1" in symbols_published
    for p in options["picks"]:
        assert p["amount_per_lot"] <= 10_000.0


def test_options_data_drops_pick_with_no_lot_size(tmp_path, monkeypatch):
    symbols = [f"SYM{i}" for i in range(2)]
    prices = _synthetic_prices(symbols)
    _wire_cli(monkeypatch, symbols, prices, prices["SYM0"])

    def fake_scan_options(*a, **k):
        return [{"symbol": "SYM0", "score": 80.0, "direction": "CE", "spot": 100.0,
                 "expiry": "25-Sep-2026", "lot_size": None,
                 "picks": [{"strike": 100, "premium": 5.0, "breakeven": 105.0, "iv": 20.0}]}]
    monkeypatch.setattr(cli_mod, "scan_options", fake_scan_options)

    out_dir = tmp_path / "site"
    sb.build(str(out_dir), top_n=5, quiet=True)

    options = json.loads((out_dir / "data" / "options.json").read_text())
    assert options["picks"] == []
