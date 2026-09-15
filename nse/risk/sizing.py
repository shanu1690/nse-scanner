"""ATR-based position sizing: the stop distance -- already set by
momentum.py's analyze_stock()/analyze_fade() as entry - 1.5*ATR14 -- is
what determines position size, never a fixed share count. This is the
literal mandate in PROJECT_BRIEF.md Section 8 / risk-manager.md: "ATR-based
position sizing so stop distance determines size rather than a fixed
quantity."
"""

from __future__ import annotations

from typing import Optional

__all__ = ["position_size", "reward_risk_ratio"]


def position_size(entry: "float | None", stop: "float | None",
                   capital: float, risk_pct: float) -> "Optional[dict]":
    """Shares to buy so a stop-out loses (up to share-count rounding) at
    most risk_pct% of capital. Returns None -- never a fallback quantity --
    when the stop doesn't define a real, positive, sizeable risk (missing
    stop, entry<=stop, non-positive capital/risk_pct, or the risk-per-share
    is too large to buy even one share within budget). Callers must treat
    None as "cannot size this pick, veto it" (PROJECT_BRIEF.md Section 8 /
    risk-manager.md: "a pick without a stop is not a pick").
    """
    if entry is None or stop is None:
        return None
    if capital is None or risk_pct is None or capital <= 0 or risk_pct <= 0:
        return None
    risk_per_share = entry - stop
    if risk_per_share <= 0:
        return None
    risk_amount = capital * (risk_pct / 100.0)
    shares = int(risk_amount // risk_per_share)
    if shares <= 0:
        return None
    rupee_risk = round(shares * risk_per_share, 2)
    return {
        "shares": shares,
        "risk_per_share": round(risk_per_share, 4),
        "rupee_risk": rupee_risk,
        "risk_pct_of_capital": round(rupee_risk / capital * 100, 3),
        "position_value": round(shares * entry, 2),
    }


def reward_risk_ratio(entry: "float | None", stop: "float | None",
                       target: "float | None") -> "Optional[float]":
    """reward:risk for a long trade (entry/stop/target already produced by
    momentum.py). None when the stop doesn't define a positive risk (same
    "cannot size, don't guess" contract as position_size)."""
    if entry is None or stop is None or target is None:
        return None
    risk = entry - stop
    if risk <= 0:
        return None
    reward = target - entry
    if reward <= 0:
        return 0.0
    return round(reward / risk, 2)
