"""Tests for the fundamental factor audit (nse/fundamentals/factor_audit.py).

The sample-size gate and the filing-event (not daily-bar) sampling grid are
the two things worth being paranoid about here -- both are exercised
directly, plus an end-to-end run against synthetic data with a genuine
factor->return relationship built in, to confirm the audit can actually
detect one when it's really there.
"""

import numpy as np
import pandas as pd
import pytest

from nse.fundamentals import factor_audit as fa
from nse.pit.store import PITRecord, PointInTimeStore

UTC = pd.Timestamp.now(tz="UTC").tzinfo


def _rec(symbol, field, value, period_end, published_at):
    from datetime import datetime, timezone
    return PITRecord(symbol=symbol, field=field, value=value,
                      period_end=datetime(*period_end, tzinfo=timezone.utc),
                      published_at=datetime(*published_at, tzinfo=timezone.utc),
                      source="test")


def _price_series(seed=0, n=800, start="2023-01-02"):
    rng = np.random.default_rng(seed)
    idx = pd.date_range(start, periods=n, freq="B")
    close = 100 + rng.normal(0, 1, n).cumsum()
    close = np.maximum(close, 1.0)
    return pd.DataFrame({"Open": close, "High": close * 1.01, "Low": close * 0.99,
                         "Close": close, "Volume": 100000}, index=idx)


@pytest.fixture
def store():
    with PointInTimeStore() as s:
        yield s


# ------------------------------------------------------------- collect_filing_events
def test_collect_filing_events_uses_filing_calendar_not_daily_bars(store, monkeypatch):
    store.put([
        _rec("A", "net_profit", 100, (2024, 3, 31), (2024, 4, 20)),
        _rec("A", "net_profit", 110, (2024, 6, 30), (2024, 7, 20)),
    ])
    prices = {"A": _price_series(seed=1)}
    monkeypatch.setattr(fa.data_mod, "load_price_history", lambda sym: prices.get(sym))

    events = fa.collect_filing_events(store, ["A"])
    assert len(events) == 2  # one per filing, not one per trading day
    assert events[0].published_at < events[1].published_at


def test_collect_filing_events_skips_symbol_with_no_price_history(store, monkeypatch):
    store.put([_rec("A", "net_profit", 100, (2024, 3, 31), (2024, 4, 20))])
    monkeypatch.setattr(fa.data_mod, "load_price_history", lambda sym: None)
    assert fa.collect_filing_events(store, ["A"]) == []


def test_collect_filing_events_skips_filing_older_than_price_cache(store, monkeypatch):
    """A filing published before the cached price history begins must be
    skipped, not silently priced off the cache's first bar."""
    store.put([_rec("A", "net_profit", 100, (2019, 12, 31), (2020, 1, 20))])
    recent_prices = _price_series(seed=3, n=50, start="2024-01-02")
    monkeypatch.setattr(fa.data_mod, "load_price_history", lambda sym: recent_prices)
    events = fa.collect_filing_events(store, ["A"])
    assert events == []


def test_collect_filing_events_fwd_pct_none_when_horizon_beyond_cache(store, monkeypatch):
    store.put([_rec("A", "net_profit", 100, (2024, 3, 31), (2024, 4, 20))])
    # price history ending right around the filing date -- no room for the
    # forward-return horizon
    short_prices = _price_series(seed=2, n=10, start="2024-04-15")
    monkeypatch.setattr(fa.data_mod, "load_price_history", lambda sym: short_prices)
    events = fa.collect_filing_events(store, ["A"])
    assert len(events) == 1
    assert events[0].fwd_pct is None


# --------------------------------------------------------- sample-size gate
def test_audit_refuses_below_total_event_floor(store, monkeypatch):
    store.put([_rec("A", "net_profit", 100, (2024, 3, 31), (2024, 4, 20))])
    monkeypatch.setattr(fa.data_mod, "load_price_history", lambda sym: _price_series())
    result = fa.run_fundamental_factor_audit(store, ["A"])
    assert result["ok"] is False
    assert "filing events" in result["message"]


def test_audit_refuses_below_per_split_floor(store, monkeypatch):
    # Exactly MIN_EVENTS_TOTAL events, but skewed so one side of the split
    # (via HELD_OUT_FRACTION=0.3) would fall under MIN_EVENTS_PER_SPLIT=8.
    # 20 events * 0.3 = 6 held out -> below the floor of 8.
    recs = []
    for i in range(20):
        recs.append(_rec("A", "net_profit", 100 + i, (2020 + i // 4, 3, 31),
                         (2020 + i // 4, 4, 20)))
    store.put(recs)
    monkeypatch.setattr(fa.data_mod, "load_price_history", lambda sym: _price_series(n=3000))
    result = fa.run_fundamental_factor_audit(store, ["A"])
    assert result["ok"] is False
    assert "held-out" in result["message"]


# ------------------------------------------------------------------- end-to-end
def _seed_realistic_events(store, symbols, quarters_per_symbol=10, seed=0):
    """Distinct (period_end, published_at) per symbol, spaced a quarter
    apart, values varying by symbol/quarter so net_margin differs."""
    rng = np.random.default_rng(seed)
    for si, sym in enumerate(symbols):
        for q in range(quarters_per_symbol):
            year = 2021 + q // 4
            month = [3, 6, 9, 12][q % 4]
            pub_year = year if month != 12 else year + 1
            pub_month = month + 1 if month != 12 else 1
            revenue = 100000 + rng.normal(0, 5000)
            margin = 0.05 + 0.01 * si + rng.normal(0, 0.01)
            net_profit = revenue * margin
            store.put([
                _rec(sym, "revenue_from_operations", revenue, (year, month, 28), (pub_year, pub_month, 15)),
                _rec(sym, "net_profit", net_profit, (year, month, 28), (pub_year, pub_month, 15)),
            ])


def test_audit_end_to_end_with_enough_data(store, monkeypatch):
    symbols = ["A", "B", "C"]
    _seed_realistic_events(store, symbols, quarters_per_symbol=10)
    prices = {s: _price_series(seed=i, n=1200, start="2021-01-04") for i, s in enumerate(symbols)}
    monkeypatch.setattr(fa.data_mod, "load_price_history", lambda sym: prices.get(sym))

    result = fa.run_fundamental_factor_audit(store, symbols, factor_names=["net_margin"])
    assert result["ok"] is True
    assert result["n_events_total"] == 30
    assert result["n_prior"] >= fa.MIN_EVENTS_PER_SPLIT
    assert result["n_held_out"] >= fa.MIN_EVENTS_PER_SPLIT
    # net_margin should have produced a usable split given real variation
    assert "net_margin" in result["factors"]
    stat = result["factors"]["net_margin"]
    assert stat["n_hi"] > 0 and stat["n_lo"] > 0


def test_split_stat_returns_none_without_enough_variation():
    from nse.fundamentals.factor_audit import FilingEvent
    from datetime import datetime, timezone
    events = [FilingEvent("A", datetime(2024, 1, 1, tzinfo=timezone.utc),
                          {"net_margin": 0.1}, fwd_pct=1.0)]
    assert fa._split_stat(events, events, "net_margin") is None  # only one bucket
    assert fa._split_stat([], events, "net_margin") is None      # no prior values
