"""Risk limits configuration (Phase 8). Numbers only -- see enforce.py for
how these are actually applied. Defaults are conservative, round numbers,
not fit to any backtest (this is a mechanical safety layer, not a model).
"""

from __future__ import annotations

from dataclasses import dataclass

DEFAULT_CAPITAL = 100_000.0           # Rs -- overridden by config.yaml's risk.capital
DEFAULT_PER_TRADE_RISK_PCT = 1.0      # % of capital risked per delivery pick (stop-out loss)
DEFAULT_MAX_SECTOR_PCT = 30.0         # % of capital's risk budget concentrated in one sector
DEFAULT_MAX_CORRELATION = 0.70        # pairwise daily-return correlation cap across open picks
DEFAULT_MAX_PORTFOLIO_HEAT_PCT = 6.0  # total risk %, all open ideas combined
DEFAULT_MAX_OPEN_IDEAS = 10           # delivery + options combined
DEFAULT_OPTIONS_BUDGET_CAP = 10_000.0     # Rs -- PROJECT_BRIEF.md Section 5's hard cap
DEFAULT_CORRELATION_LOOKBACK_DAYS = 60

__all__ = ["RiskLimits"]


@dataclass(frozen=True)
class RiskLimits:
    capital: float = DEFAULT_CAPITAL
    per_trade_risk_pct: float = DEFAULT_PER_TRADE_RISK_PCT
    max_sector_pct: float = DEFAULT_MAX_SECTOR_PCT
    max_correlation: float = DEFAULT_MAX_CORRELATION
    max_portfolio_heat_pct: float = DEFAULT_MAX_PORTFOLIO_HEAT_PCT
    max_open_ideas: int = DEFAULT_MAX_OPEN_IDEAS
    options_budget_cap: float = DEFAULT_OPTIONS_BUDGET_CAP
    correlation_lookback_days: int = DEFAULT_CORRELATION_LOOKBACK_DAYS

    @staticmethod
    def from_config(config: dict) -> "RiskLimits":
        """Reads an OPTIONAL `risk:` section from config.yaml; every field
        has a sane default so an absent section (today's config.yaml has
        none) still produces a fully-specified, conservative limit set
        rather than failing."""
        cfg = (config or {}).get("risk") or {}
        return RiskLimits(
            capital=float(cfg.get("capital", DEFAULT_CAPITAL)),
            per_trade_risk_pct=float(cfg.get("per_trade_risk_pct", DEFAULT_PER_TRADE_RISK_PCT)),
            max_sector_pct=float(cfg.get("max_sector_pct", DEFAULT_MAX_SECTOR_PCT)),
            max_correlation=float(cfg.get("max_correlation", DEFAULT_MAX_CORRELATION)),
            max_portfolio_heat_pct=float(cfg.get("max_portfolio_heat_pct", DEFAULT_MAX_PORTFOLIO_HEAT_PCT)),
            max_open_ideas=int(cfg.get("max_open_ideas", DEFAULT_MAX_OPEN_IDEAS)),
            options_budget_cap=float(cfg.get("options_budget_cap", DEFAULT_OPTIONS_BUDGET_CAP)),
            correlation_lookback_days=int(cfg.get("correlation_lookback_days",
                                                   DEFAULT_CORRELATION_LOOKBACK_DAYS)),
        )
