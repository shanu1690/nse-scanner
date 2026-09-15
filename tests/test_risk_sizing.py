"""Tests for nse/risk/sizing.py: ATR-based position sizing (stop distance
determines size, never a fixed quantity) and reward:risk."""

import pytest

from nse.risk import sizing as sz


def test_position_size_basic():
    # risk_per_share = 100-95 = 5; risk_amount = 100000*1% = 1000 -> 200 shares
    out = sz.position_size(entry=100.0, stop=95.0, capital=100_000.0, risk_pct=1.0)
    assert out["shares"] == 200
    assert out["risk_per_share"] == pytest.approx(5.0)
    assert out["rupee_risk"] == pytest.approx(1000.0)
    assert out["risk_pct_of_capital"] == pytest.approx(1.0)
    assert out["position_value"] == pytest.approx(20000.0)


def test_position_size_none_when_stop_above_entry():
    assert sz.position_size(entry=100.0, stop=105.0, capital=100_000.0, risk_pct=1.0) is None


def test_position_size_none_when_stop_equals_entry():
    assert sz.position_size(entry=100.0, stop=100.0, capital=100_000.0, risk_pct=1.0) is None


def test_position_size_none_when_missing_stop():
    assert sz.position_size(entry=100.0, stop=None, capital=100_000.0, risk_pct=1.0) is None


def test_position_size_none_when_capital_non_positive():
    assert sz.position_size(entry=100.0, stop=95.0, capital=0, risk_pct=1.0) is None
    assert sz.position_size(entry=100.0, stop=95.0, capital=-500, risk_pct=1.0) is None


def test_position_size_none_when_risk_pct_non_positive():
    assert sz.position_size(entry=100.0, stop=95.0, capital=100_000.0, risk_pct=0) is None


def test_position_size_none_when_risk_per_share_too_large_for_even_one_share():
    # risk_amount = 100000 * 0.01% = 10; risk_per_share = 5000 -> 0 shares
    out = sz.position_size(entry=6000.0, stop=1000.0, capital=100_000.0, risk_pct=0.01)
    assert out is None


def test_reward_risk_ratio_basic():
    assert sz.reward_risk_ratio(entry=100.0, stop=95.0, target=110.0) == pytest.approx(2.0)


def test_reward_risk_ratio_none_when_stop_invalid():
    assert sz.reward_risk_ratio(entry=100.0, stop=105.0, target=110.0) is None


def test_reward_risk_ratio_zero_when_target_below_entry():
    assert sz.reward_risk_ratio(entry=100.0, stop=95.0, target=98.0) == 0.0


def test_reward_risk_ratio_none_when_missing_inputs():
    assert sz.reward_risk_ratio(entry=100.0, stop=95.0, target=None) is None
