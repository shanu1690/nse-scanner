"""Point-in-time fundamental factor derivations (Phase 5, second half).

Every function takes `as_of` (a decision-time datetime) and reads the
PointInTimeStore via as_of() only -- never a plain dataframe indexed by
period-end. That is the single rule point-in-time-protocol checklist item 2
exists to enforce: a fundamentals fact becomes usable on its PUBLICATION
date, not its fiscal period end, and this module never takes a shortcut
around that even internally.

Growth factors compare a period to the SAME QUARTER a year earlier, not the
immediately preceding quarter: quarter-over-quarter deltas for a seasonal
business are mostly a calendar artifact, not real growth (PROJECT_BRIEF.md
Phase 5 asks for "growth" factors, and this is the honest version of that).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Optional

from nse.pit.store import PointInTimeStore

__all__ = [
    "quality_factors", "growth_factors", "balance_sheet_factors",
    "valuation_factors", "all_factors", "FACTOR_NAMES",
]

# Every factor name this module can produce, across all four groups --
# the audit machinery iterates this list rather than each function's own
# output keys, so a factor that's always None for a given symbol still
# shows up in the audit as "no usable split" instead of silently vanishing.
FACTOR_NAMES = [
    "net_margin", "effective_tax_rate", "interest_coverage",
    "revenue_growth_yoy", "net_profit_growth_yoy", "eps_growth_yoy",
    "debt_equity_ratio",
    "pe_ttm",
]


def _periods(store: PointInTimeStore, symbol: str, field: str,
             as_of: datetime, max_periods: Optional[int] = None) -> list[tuple[date, float]]:
    """Distinct (period_end, value) visible as-of, most recent first."""
    rows = store.as_of([symbol], [field], as_of)
    out = []
    for r in rows:
        try:
            pe = datetime.fromisoformat(r["period_end"]).date()
            val = float(r["value"])
        except (TypeError, ValueError):
            continue
        out.append((pe, val))
    out.sort(key=lambda t: t[0], reverse=True)
    return out[:max_periods] if max_periods else out


def _latest(store: PointInTimeStore, symbol: str, field: str,
            as_of: datetime) -> Optional[float]:
    periods = _periods(store, symbol, field, as_of, max_periods=1)
    return periods[0][1] if periods else None


def _year_ago(store: PointInTimeStore, symbol: str, field: str, as_of: datetime,
              current_period_end: date, tolerance_days: int = 45) -> Optional[float]:
    """The value whose period_end is closest to 1 year before
    current_period_end, within a tolerance window -- quarterly filing
    dates drift by a few days year to year, so an exact 365-day match
    would miss real comparisons."""
    target = current_period_end - timedelta(days=365)
    best, best_diff = None, None
    for pe, val in _periods(store, symbol, field, as_of):
        if pe == current_period_end:
            continue
        diff = abs((pe - target).days)
        if diff <= tolerance_days and (best_diff is None or diff < best_diff):
            best, best_diff = val, diff
    return best


def quality_factors(store: PointInTimeStore, symbol: str, as_of: datetime) -> dict:
    revenue = _latest(store, symbol, "revenue_from_operations", as_of)
    net_profit = _latest(store, symbol, "net_profit", as_of)
    pbt = _latest(store, symbol, "profit_before_tax", as_of)
    tax = _latest(store, symbol, "tax_expense", as_of)
    finance_costs = _latest(store, symbol, "finance_costs", as_of)

    out = {}
    if revenue and net_profit is not None:
        out["net_margin"] = net_profit / revenue
    if pbt and tax is not None:
        out["effective_tax_rate"] = tax / pbt
    if pbt is not None and finance_costs:
        out["interest_coverage"] = (pbt + finance_costs) / finance_costs
    return out


def growth_factors(store: PointInTimeStore, symbol: str, as_of: datetime) -> dict:
    out = {}
    for field, name in (
        ("revenue_from_operations", "revenue_growth_yoy"),
        ("net_profit", "net_profit_growth_yoy"),
        ("eps_basic", "eps_growth_yoy"),
    ):
        periods = _periods(store, symbol, field, as_of, max_periods=1)
        if not periods:
            continue
        current_pe, current_val = periods[0]
        prior_val = _year_ago(store, symbol, field, as_of, current_pe)
        if prior_val:
            out[name] = (current_val - prior_val) / abs(prior_val)
    return out


def balance_sheet_factors(store: PointInTimeStore, symbol: str, as_of: datetime) -> dict:
    de = _latest(store, symbol, "debt_equity_ratio", as_of)
    return {"debt_equity_ratio": de} if de is not None else {}


def valuation_factors(store: PointInTimeStore, symbol: str, as_of: datetime,
                       price: Optional[float] = None) -> dict:
    """`price` is the decision-date close, supplied by the caller (usually
    from nse/data.py's cached OHLCV) rather than fetched internally here --
    keeps this testable without a price cache and lets the caller control
    exactly which bar's close is used.

    Trailing-twelve-month EPS from the 4 most recent visible quarters. Does
    not verify they are 4 CONSECUTIVE quarters (a missed filing would still
    sum whatever 4 are visible) -- a known v1 simplification, not silently
    hidden.
    """
    if price is None or price <= 0:
        return {}
    periods = _periods(store, symbol, "eps_basic", as_of, max_periods=4)
    if len(periods) < 4:
        return {}
    ttm_eps = sum(v for _, v in periods)
    if ttm_eps <= 0:
        return {}
    return {"pe_ttm": price / ttm_eps}


def all_factors(store: PointInTimeStore, symbol: str, as_of: datetime,
                 price: Optional[float] = None) -> dict:
    out = {}
    out.update(quality_factors(store, symbol, as_of))
    out.update(growth_factors(store, symbol, as_of))
    out.update(balance_sheet_factors(store, symbol, as_of))
    out.update(valuation_factors(store, symbol, as_of, price))
    return out
