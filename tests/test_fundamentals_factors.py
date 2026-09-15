"""Tests for point-in-time fundamental factor derivations
(nse/fundamentals/factors.py), including the point-in-time boundary itself:
a factor computed as-of a date must never see a filing published on or
after that date.
"""

from datetime import date, datetime, timezone

import pytest

from nse.fundamentals import factors as fac
from nse.pit.store import PITRecord, PointInTimeStore

UTC = timezone.utc


def _rec(symbol, field, value, period_end, published_at):
    return PITRecord(symbol=symbol, field=field, value=value,
                      period_end=datetime(*period_end, tzinfo=UTC),
                      published_at=datetime(*published_at, tzinfo=UTC),
                      source="test")


@pytest.fixture
def store():
    with PointInTimeStore() as s:
        yield s


def _seed_two_quarters(store, symbol="RELIANCE"):
    """Two quarters, one year apart, with realistic-shaped figures."""
    store.put([
        # Q1 FY26 (period end 30-06-2025), published 17-07-2025
        _rec(symbol, "revenue_from_operations", 200000, (2025, 6, 30), (2025, 7, 17)),
        _rec(symbol, "net_profit", 15000, (2025, 6, 30), (2025, 7, 17)),
        _rec(symbol, "profit_before_tax", 20000, (2025, 6, 30), (2025, 7, 17)),
        _rec(symbol, "tax_expense", 5000, (2025, 6, 30), (2025, 7, 17)),
        _rec(symbol, "finance_costs", 2000, (2025, 6, 30), (2025, 7, 17)),
        _rec(symbol, "eps_basic", 10.0, (2025, 6, 30), (2025, 7, 17)),
        _rec(symbol, "debt_equity_ratio", 0.5, (2025, 6, 30), (2025, 7, 17)),
        # Q1 FY27 (period end 30-06-2026), published 17-07-2026 -- +10% YoY revenue/profit
        _rec(symbol, "revenue_from_operations", 220000, (2026, 6, 30), (2026, 7, 17)),
        _rec(symbol, "net_profit", 16500, (2026, 6, 30), (2026, 7, 17)),
        _rec(symbol, "profit_before_tax", 22000, (2026, 6, 30), (2026, 7, 17)),
        _rec(symbol, "tax_expense", 5500, (2026, 6, 30), (2026, 7, 17)),
        _rec(symbol, "finance_costs", 2200, (2026, 6, 30), (2026, 7, 17)),
        _rec(symbol, "eps_basic", 11.0, (2026, 6, 30), (2026, 7, 17)),
        _rec(symbol, "debt_equity_ratio", 0.45, (2026, 6, 30), (2026, 7, 17)),
    ])


# ------------------------------------------------------------- quality_factors
def test_quality_factors(store):
    _seed_two_quarters(store)
    as_of = datetime(2026, 8, 1, tzinfo=UTC)
    q = fac.quality_factors(store, "RELIANCE", as_of)
    assert q["net_margin"] == pytest.approx(16500 / 220000)
    assert q["effective_tax_rate"] == pytest.approx(5500 / 22000)
    assert q["interest_coverage"] == pytest.approx((22000 + 2200) / 2200)


def test_quality_factors_empty_when_nothing_published_yet(store):
    _seed_two_quarters(store)
    as_of = datetime(2025, 1, 1, tzinfo=UTC)  # before either filing
    assert fac.quality_factors(store, "RELIANCE", as_of) == {}


# -------------------------------------------------------------- growth_factors
def test_growth_factors_compares_same_quarter_year_ago(store):
    _seed_two_quarters(store)
    as_of = datetime(2026, 8, 1, tzinfo=UTC)
    g = fac.growth_factors(store, "RELIANCE", as_of)
    assert g["revenue_growth_yoy"] == pytest.approx((220000 - 200000) / 200000)
    assert g["net_profit_growth_yoy"] == pytest.approx((16500 - 15000) / 15000)
    assert g["eps_growth_yoy"] == pytest.approx((11.0 - 10.0) / 10.0)


def test_growth_factors_absent_with_only_one_quarter_visible(store):
    _seed_two_quarters(store)
    as_of = datetime(2025, 8, 1, tzinfo=UTC)  # only the first quarter is out yet
    g = fac.growth_factors(store, "RELIANCE", as_of)
    assert g == {}


# --------------------------------------------------------- point-in-time boundary
def test_factors_never_see_a_filing_published_on_or_after_as_of(store):
    _seed_two_quarters(store)
    # Exactly at the second filing's published_at: must not be visible yet
    as_of = datetime(2026, 7, 17, tzinfo=UTC)
    q = fac.quality_factors(store, "RELIANCE", as_of)
    assert q["net_margin"] == pytest.approx(15000 / 200000)  # still the OLD quarter

    # One microsecond later: now visible
    as_of_after = datetime(2026, 7, 17, 0, 0, 0, 1, tzinfo=UTC)
    q_after = fac.quality_factors(store, "RELIANCE", as_of_after)
    assert q_after["net_margin"] == pytest.approx(16500 / 220000)


# ------------------------------------------------------- balance_sheet_factors
def test_balance_sheet_factors(store):
    _seed_two_quarters(store)
    as_of = datetime(2026, 8, 1, tzinfo=UTC)
    b = fac.balance_sheet_factors(store, "RELIANCE", as_of)
    assert b == {"debt_equity_ratio": pytest.approx(0.45)}


def test_balance_sheet_factors_empty_when_no_data(store):
    assert fac.balance_sheet_factors(store, "NOSUCH", datetime(2026, 1, 1, tzinfo=UTC)) == {}


# ------------------------------------------------------------ valuation_factors
def _seed_four_quarters_eps(store, symbol="TCS"):
    quarters = [
        ((2025, 6, 30), (2025, 7, 15), 8.0),
        ((2025, 9, 30), (2025, 10, 15), 8.5),
        ((2025, 12, 31), (2026, 1, 15), 9.0),
        ((2026, 3, 31), (2026, 4, 15), 9.5),
    ]
    store.put([_rec(symbol, "eps_basic", eps, pe, pub) for pe, pub, eps in quarters])


def test_valuation_factors_ttm_pe(store):
    _seed_four_quarters_eps(store)
    as_of = datetime(2026, 5, 1, tzinfo=UTC)
    v = fac.valuation_factors(store, "TCS", as_of, price=350.0)
    ttm_eps = 8.0 + 8.5 + 9.0 + 9.5
    assert v["pe_ttm"] == pytest.approx(350.0 / ttm_eps)


def test_valuation_factors_needs_price(store):
    _seed_four_quarters_eps(store)
    as_of = datetime(2026, 5, 1, tzinfo=UTC)
    assert fac.valuation_factors(store, "TCS", as_of, price=None) == {}


def test_valuation_factors_needs_four_quarters(store):
    _seed_two_quarters(store)  # only 2 quarters seeded, not consecutive
    as_of = datetime(2026, 8, 1, tzinfo=UTC)
    assert fac.valuation_factors(store, "RELIANCE", as_of, price=100.0) == {}


# ------------------------------------------------------------------- all_factors
def test_all_factors_merges_every_group(store):
    _seed_two_quarters(store)
    as_of = datetime(2026, 8, 1, tzinfo=UTC)
    a = fac.all_factors(store, "RELIANCE", as_of, price=None)
    assert "net_margin" in a
    assert "revenue_growth_yoy" in a
    assert "debt_equity_ratio" in a
    assert "pe_ttm" not in a  # no price supplied, and only 2 quarters anyway
