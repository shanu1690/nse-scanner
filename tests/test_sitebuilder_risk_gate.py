"""Phase 8 wiring: nse/risk/'s veto engine gates nse/sitebuilder.py's
delivery picks (per-pick veto, not an all-or-nothing PublishBlocked like
DataValidator), and _options_data() independently re-verifies the Rs
10,000 budget cap before anything reaches options.json.
"""

import json
from types import SimpleNamespace

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


def _fake_idea_result(symbol, cost, direction="CE", lot_size=100, probability=0.5,
                       max_profit=None):
    return {
        "symbol": symbol, "reason": None, "rejected": [],
        "idea": {
            "strategy": "long", "legs": [{"action": "BUY", "side": direction,
                                          "strike": 100, "premium": cost / lot_size}],
            "cost": cost, "max_loss": cost, "max_profit": max_profit, "breakeven": 105.0,
            "probability": probability, "lot_size": lot_size, "payoff": [], "thesis": "t",
        },
        "analysis": {"score": 80.0, "direction": direction, "spot": 100.0,
                     "expiry": "25-Sep-2026", "dte": 10},
    }


def test_options_data_drops_pick_over_budget(monkeypatch):
    """_options_data() now reads select_option_idea() output for every
    chain scan_options() fetched (via chains_out), then independently
    re-verifies the budget through enforce_option_ideas() -- Phase 8's
    defence-in-depth -- rather than reading anything scan_options() itself
    returns. Monkeypatching select_option_idea() tests that integration
    directly instead of needing analyze_option_chain()'s real scoring
    formula to happen to produce a particular direction."""
    import nse.options as opt
    from nse.risk import RiskLimits

    def fake_scan_options(prices, top_n=None, max_seconds=None, chains_out=None):
        chains_out["SYM0"] = {"records": {}}  # content unused -- select_option_idea is faked
        chains_out["SYM1"] = {"records": {}}
        return []

    def fake_select_option_idea(symbol, raw, trend=None, budget=10_000.0, cross_source_oi=True):
        if symbol == "SYM0":
            return _fake_idea_result("SYM0", cost=50_000.0, lot_size=1000)  # over cap
        return _fake_idea_result("SYM1", cost=2_000.0, lot_size=100)  # within cap

    sc = SimpleNamespace(scan_options=fake_scan_options)
    monkeypatch.setattr(opt, "select_option_idea", fake_select_option_idea)

    limits = RiskLimits(options_budget_cap=10_000.0)
    picks, risk_result = sb._options_data(sc, prices={}, top_n=5, max_seconds=None,
                                          chains_out={}, risk_limits=limits)

    published = {p["symbol"] for p in picks}
    assert "SYM0" not in published  # over budget -- dropped
    assert "SYM1" in published
    assert any(v.rule == "options_budget_cap" for v in risk_result.vetoes)
    for p in picks:
        assert p["cost"] <= 10_000.0


def test_options_data_ranks_by_probability_not_premium(monkeypatch):
    import nse.options as opt
    from nse.risk import RiskLimits

    def fake_scan_options(prices, top_n=None, max_seconds=None, chains_out=None):
        chains_out["CHEAP_LOW_PROB"] = {"records": {}}
        chains_out["PRICEY_HIGH_PROB"] = {"records": {}}
        return []

    def fake_select_option_idea(symbol, raw, trend=None, budget=10_000.0, cross_source_oi=True):
        if symbol == "CHEAP_LOW_PROB":
            return _fake_idea_result(symbol, cost=500.0, lot_size=100, probability=0.2)
        return _fake_idea_result(symbol, cost=8000.0, lot_size=100, probability=0.8)

    sc = SimpleNamespace(scan_options=fake_scan_options)
    monkeypatch.setattr(opt, "select_option_idea", fake_select_option_idea)

    picks, _ = sb._options_data(sc, prices={}, top_n=5, max_seconds=None,
                                chains_out={}, risk_limits=RiskLimits())
    assert [p["symbol"] for p in picks] == ["PRICEY_HIGH_PROB", "CHEAP_LOW_PROB"]


def test_options_data_skips_refused_ideas(monkeypatch):
    import nse.options as opt
    from nse.risk import RiskLimits

    def fake_scan_options(prices, top_n=None, max_seconds=None, chains_out=None):
        chains_out["SYM0"] = {"records": {}}
        return []

    def fake_select_option_idea(symbol, raw, trend=None, budget=10_000.0, cross_source_oi=True):
        return {"symbol": symbol, "idea": None, "reason": "no directional edge",
               "rejected": [], "analysis": {"score": 50.0, "direction": "NEUTRAL"}}

    sc = SimpleNamespace(scan_options=fake_scan_options)
    monkeypatch.setattr(opt, "select_option_idea", fake_select_option_idea)

    picks, risk_result = sb._options_data(sc, prices={}, top_n=5, max_seconds=None,
                                          chains_out={}, risk_limits=RiskLimits())
    assert picks == [] and not risk_result.vetoes  # refused, not vetoed -- nothing to re-check
