"""Tests for nse/risk/enforce.py: the veto engine itself."""

import numpy as np
import pandas as pd
import pytest

from nse.risk.enforce import enforce_delivery_picks, enforce_option_ideas
from nse.risk.limits import RiskLimits


def _pick(symbol, entry=100.0, stop=95.0, target1=105.0, target2=110.0):
    return {"symbol": symbol, "score": 70.0, "entry": entry, "stop": stop,
            "target1": target1, "target2": target2}


# ------------------------------------------------------------- basic gating
def test_approves_a_clean_pick_and_attaches_sizing():
    limits = RiskLimits(capital=100_000.0, per_trade_risk_pct=1.0)
    result = enforce_delivery_picks([_pick("A")], limits)
    assert len(result.approved) == 1
    assert not result.vetoes
    a = result.approved[0]
    assert a["position_size"] == 200  # 1000 risk / 5 per share
    assert a["rupee_risk"] == pytest.approx(1000.0)
    assert a["reward_risk"] == pytest.approx(2.0)  # (110-100)/(100-95)


def test_vetoes_pick_missing_stop():
    limits = RiskLimits()
    pick = {"symbol": "A", "score": 70.0, "entry": 100.0, "stop": None}
    result = enforce_delivery_picks([pick], limits)
    assert not result.approved
    assert result.vetoes[0].rule == "missing_required_field"


def test_vetoes_invalid_stop_distance():
    limits = RiskLimits()
    result = enforce_delivery_picks([_pick("A", entry=100.0, stop=105.0)], limits)
    assert not result.approved
    assert result.vetoes[0].rule == "invalid_stop_distance"


# --------------------------------------------------------------- max open ideas
def test_max_open_ideas_vetoes_beyond_cap():
    limits = RiskLimits(capital=1_000_000.0, per_trade_risk_pct=0.1, max_open_ideas=2)
    picks = [_pick("A"), _pick("B"), _pick("C")]
    result = enforce_delivery_picks(picks, limits)
    assert len(result.approved) == 2
    assert result.vetoes[0].symbol == "C"
    assert result.vetoes[0].rule == "max_open_ideas"


def test_max_open_ideas_counts_already_open():
    limits = RiskLimits(capital=1_000_000.0, per_trade_risk_pct=0.1, max_open_ideas=1)
    open_ideas = [{"symbol": "EXISTING", "rupee_risk": 500.0, "sector": None}]
    result = enforce_delivery_picks([_pick("A")], limits, open_ideas=open_ideas)
    assert not result.approved
    assert result.vetoes[0].rule == "max_open_ideas"


# ---------------------------------------------------------------- portfolio heat
def test_portfolio_heat_vetoes_when_cap_exceeded():
    # capital 100k, risk_pct 1% -> 1000 rupee risk per pick; heat cap 1.5%
    # -> first pick fits (1.0%), second would take heat to 2.0% > 1.5%.
    limits = RiskLimits(capital=100_000.0, per_trade_risk_pct=1.0, max_portfolio_heat_pct=1.5,
                        max_open_ideas=10)
    result = enforce_delivery_picks([_pick("A"), _pick("B")], limits)
    assert [a["symbol"] for a in result.approved] == ["A"]
    assert result.vetoes[0].symbol == "B"
    assert result.vetoes[0].rule == "portfolio_heat"


def test_portfolio_heat_includes_already_open_risk():
    limits = RiskLimits(capital=100_000.0, per_trade_risk_pct=1.0, max_portfolio_heat_pct=1.5)
    open_ideas = [{"symbol": "EXISTING", "rupee_risk": 1000.0, "sector": None}]
    result = enforce_delivery_picks([_pick("A")], limits, open_ideas=open_ideas)
    # existing 1.0% + new 1.0% = 2.0% > 1.5% cap
    assert not result.approved
    assert result.vetoes[0].rule == "portfolio_heat"


# --------------------------------------------------------------------- sector cap
def test_sector_cap_vetoes_when_exceeded():
    limits = RiskLimits(capital=100_000.0, per_trade_risk_pct=1.0, max_sector_pct=1.5,
                        max_portfolio_heat_pct=100.0)
    sector_map = {"A": "Banking", "B": "Banking"}
    result = enforce_delivery_picks([_pick("A"), _pick("B")], limits, sector_map=sector_map)
    assert [a["symbol"] for a in result.approved] == ["A"]
    assert result.vetoes[0].symbol == "B"
    assert result.vetoes[0].rule == "sector_cap"


def test_sector_cap_not_enforced_when_symbol_unmapped():
    limits = RiskLimits(capital=100_000.0, per_trade_risk_pct=1.0, max_sector_pct=0.5,
                        max_portfolio_heat_pct=100.0)
    result = enforce_delivery_picks([_pick("A")], limits, sector_map={})
    assert len(result.approved) == 1  # not vetoed -- sector unknown, not assumed compliant
    assert "A" in result.sector_unknown


def test_sector_cap_different_sectors_both_pass():
    limits = RiskLimits(capital=100_000.0, per_trade_risk_pct=1.0, max_sector_pct=1.5,
                        max_portfolio_heat_pct=100.0)
    sector_map = {"A": "Banking", "B": "IT"}
    result = enforce_delivery_picks([_pick("A"), _pick("B")], limits, sector_map=sector_map)
    assert len(result.approved) == 2


# ---------------------------------------------------------------- correlation cap
def _corr_frame(close, start="2024-01-02"):
    idx = pd.date_range(start, periods=len(close), freq="B")
    return pd.DataFrame({"Open": close, "High": close, "Low": close,
                         "Close": close, "Volume": 100000}, index=idx)


def test_correlation_cap_vetoes_highly_correlated_second_pick():
    n = 70
    rng = np.random.default_rng(0)
    base = list(100 + np.cumsum(rng.normal(0, 1, n)))
    frames = {"A": _corr_frame(base), "B": _corr_frame(base)}  # identical -> corr = 1.0
    limits = RiskLimits(capital=1_000_000.0, per_trade_risk_pct=0.1, max_correlation=0.7,
                        max_portfolio_heat_pct=100.0, correlation_lookback_days=60)
    result = enforce_delivery_picks([_pick("A"), _pick("B")], limits, price_frames=frames)
    assert [a["symbol"] for a in result.approved] == ["A"]
    assert result.vetoes[0].symbol == "B"
    assert result.vetoes[0].rule == "correlation_cap"


def test_correlation_cap_allows_uncorrelated_picks():
    n = 70
    rng = np.random.default_rng(1)
    a_series = list(100 + np.cumsum(rng.normal(0, 1, n)))
    b_series = list(100 + np.cumsum(rng.normal(0, 1, n)))  # independent draw
    frames = {"A": _corr_frame(a_series), "B": _corr_frame(b_series)}
    limits = RiskLimits(capital=1_000_000.0, per_trade_risk_pct=0.1, max_correlation=0.99,
                        max_portfolio_heat_pct=100.0, correlation_lookback_days=60)
    result = enforce_delivery_picks([_pick("A"), _pick("B")], limits, price_frames=frames)
    assert len(result.approved) == 2


def test_correlation_cap_skipped_without_price_frames():
    limits = RiskLimits(capital=1_000_000.0, per_trade_risk_pct=0.1, max_correlation=0.01,
                        max_portfolio_heat_pct=100.0)
    result = enforce_delivery_picks([_pick("A"), _pick("B")], limits, price_frames=None)
    assert len(result.approved) == 2  # nothing to check without price history -- not vetoed


# ---------------------------------------------------------- required-fields render
def test_render_groups_by_rule_and_reports_sector_unknown():
    limits = RiskLimits(capital=100_000.0, per_trade_risk_pct=1.0, max_sector_pct=0.5,
                        max_portfolio_heat_pct=100.0)
    result = enforce_delivery_picks([_pick("A"), _pick("B")], limits, sector_map={})
    text = result.render()
    assert "2 approved, 0 vetoed" in text
    assert "sector cap NOT enforced" in text


# =========================================================== options budget gate
def _idea_result(symbol, cost, lot_size=100, buy_prem=20.0, sell_prem=0.0):
    legs = [{"action": "BUY", "side": "CE", "strike": 1000, "premium": buy_prem}]
    if sell_prem:
        legs.append({"action": "SELL", "side": "CE", "strike": 1020, "premium": sell_prem})
    return {"symbol": symbol, "idea": {"cost": cost, "lot_size": lot_size, "legs": legs}}


def test_options_budget_approves_within_cap():
    limits = RiskLimits(options_budget_cap=10_000.0)
    # net premium 20 * lot 100 = 2000, matches reported cost
    result = enforce_option_ideas([_idea_result("A", cost=2000.0, buy_prem=20.0)], limits)
    assert len(result.approved) == 1


def test_options_budget_vetoes_over_cap():
    limits = RiskLimits(options_budget_cap=10_000.0)
    result = enforce_option_ideas([_idea_result("A", cost=15000.0, buy_prem=150.0)], limits)
    assert not result.approved
    assert result.vetoes[0].rule == "options_budget_cap"


def test_options_budget_vetoes_on_cost_mismatch():
    # reported cost disagrees with legs*lot_size -- defense in depth
    limits = RiskLimits(options_budget_cap=10_000.0)
    result = enforce_option_ideas([_idea_result("A", cost=999.0, buy_prem=20.0)], limits)
    assert not result.approved
    assert result.vetoes[0].rule == "cost_mismatch"


def test_options_budget_skips_refused_ideas():
    limits = RiskLimits()
    result = enforce_option_ideas([{"symbol": "A", "idea": None}], limits)
    assert not result.approved and not result.vetoes


def test_options_budget_respects_n_open_start():
    limits = RiskLimits(max_open_ideas=1)
    result = enforce_option_ideas([_idea_result("A", cost=2000.0, buy_prem=20.0)], limits,
                                  n_open_start=1)
    assert not result.approved
    assert result.vetoes[0].rule == "max_open_ideas"


def test_options_budget_spread_net_debit_recomputed_correctly():
    limits = RiskLimits(options_budget_cap=10_000.0)
    # BUY 20 - SELL 12 = net 8 * lot 100 = 800
    result = enforce_option_ideas([_idea_result("A", cost=800.0, buy_prem=20.0, sell_prem=12.0)], limits)
    assert len(result.approved) == 1
