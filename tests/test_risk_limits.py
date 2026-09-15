"""Tests for nse/risk/limits.py's RiskLimits.from_config()."""

from nse.risk.limits import RiskLimits, DEFAULT_CAPITAL, DEFAULT_MAX_OPEN_IDEAS


def test_from_config_defaults_when_no_risk_section():
    limits = RiskLimits.from_config({"universe": {"symbols": []}})
    assert limits.capital == DEFAULT_CAPITAL
    assert limits.max_open_ideas == DEFAULT_MAX_OPEN_IDEAS


def test_from_config_defaults_when_config_is_none():
    limits = RiskLimits.from_config(None)
    assert limits.capital == DEFAULT_CAPITAL


def test_from_config_overrides():
    config = {"risk": {"capital": 500000, "per_trade_risk_pct": 2.0, "max_open_ideas": 5}}
    limits = RiskLimits.from_config(config)
    assert limits.capital == 500000.0
    assert limits.per_trade_risk_pct == 2.0
    assert limits.max_open_ideas == 5
    # unspecified fields keep their defaults
    assert limits.options_budget_cap == 10_000.0


def test_from_config_partial_override_keeps_other_defaults():
    config = {"risk": {"max_correlation": 0.5}}
    limits = RiskLimits.from_config(config)
    assert limits.max_correlation == 0.5
    assert limits.max_sector_pct == 30.0
