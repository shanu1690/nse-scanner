"""End-to-end tests for nse/fusion/audit.py, against a synthetic cache
(same pattern as tests/test_backtest_walkforward.py's fixture) -- both with
and without a fundamentals store, and the sample-size gates."""

import numpy as np
import pandas as pd
import pytest

from nse import backtest as bt
from nse.fundamentals.factors import FACTOR_NAMES
from nse.fusion import audit as fa
from nse.pit.store import PITRecord, PointInTimeStore


def _synthetic_price_frame(n=460, seed=0):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    close = 100 + rng.normal(0, 1, n).cumsum()
    close = np.maximum(close, 1.0)
    return pd.DataFrame({
        "Open": close + rng.uniform(-0.3, 0.3, n),
        "High": close + rng.uniform(0.2, 1.2, n),
        "Low": close - rng.uniform(0.2, 1.2, n),
        "Close": close,
        "Volume": rng.integers(100_000, 200_000, n),
    }, index=idx)


def _synthetic_vix_frame(n=460, seed=50):
    rng = np.random.default_rng(seed)
    close = np.clip(14 + rng.normal(0, 1, n).cumsum() * 0.05, 8, 60)
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame({"Open": close, "High": close * 1.02, "Low": close * 0.98,
                         "Close": close, "Volume": 0}, index=idx)


@pytest.fixture
def synthetic_fusion_cache(monkeypatch):
    import nse.data as data_mod

    symbols = [f"SYM{i}" for i in range(8)]
    frames = {s: _synthetic_price_frame(seed=i) for i, s in enumerate(symbols)}
    bench = _synthetic_price_frame(seed=99)
    vix = _synthetic_vix_frame(seed=50)

    def fake_load(symbol):
        if symbol == "^TESTBENCH":
            return bench.copy()
        if symbol == fa.VIX_TICKER:
            return vix.copy()
        return frames.get(symbol, pd.DataFrame()).copy() if symbol in frames else None

    monkeypatch.setattr(data_mod, "load_price_history", fake_load)
    monkeypatch.setitem(bt._CONFIG["data"], "index_benchmark", "^TESTBENCH")
    monkeypatch.setitem(bt._CONFIG["data"], "lookback_days", 660)

    fake_config = {
        "universe": {"symbols": symbols},
        "scanner": {"momentum": {"min_score": 20.0, "style": "fade"}},
        "data": {"index_benchmark": "^TESTBENCH", "lookback_days": 660},
    }
    monkeypatch.setattr(fa, "_load_config", lambda: fake_config)
    return symbols


def test_run_fusion_audit_end_to_end_no_fundamentals_store(synthetic_fusion_cache, tmp_path):
    missing_db = tmp_path / "no_such.db"
    result = fa.run_fusion_audit(months=None, quiet=True, fundamentals_db=str(missing_db))
    assert result["ok"] is True
    assert result["n_train"] >= fa.MIN_TRAIN_ROWS
    assert result["n_calib"] >= fa.MIN_CALIB_ROWS
    assert result["n_held_out"] >= fa.MIN_HELD_OUT_ROWS
    assert 0.0 <= result["brier_calibrated"] <= 1.0
    assert result["calib_method"] in ("sigmoid", "isotonic")
    # every fundamental feature reported as 0% real coverage (no store at all)
    for name in FACTOR_NAMES:
        assert result["fundamental_coverage"][name] == 0.0
    assert len(result["demo_picks"]) > 0
    for pick in result["demo_picks"]:
        assert len(pick["reasons"]) <= fa.TOP_REASONS_N
    assert "text" in result and "Brier" in result["text"]


def test_run_fusion_audit_with_fundamentals_store_improves_coverage(synthetic_fusion_cache, tmp_path):
    db_path = tmp_path / "pit.db"
    with PointInTimeStore(str(db_path)) as store:
        from datetime import datetime, timezone
        # SYM0 gets real fundamentals across the whole window; others don't.
        for year in range(2023, 2025):
            for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
                store.put([
                    PITRecord(symbol="SYM0", field="net_profit", value=100 + year,
                              period_end=datetime(year, month, day, tzinfo=timezone.utc),
                              published_at=datetime(year, month, day, tzinfo=timezone.utc),
                              source="test"),
                    PITRecord(symbol="SYM0", field="revenue_from_operations", value=1000,
                              period_end=datetime(year, month, day, tzinfo=timezone.utc),
                              published_at=datetime(year, month, day, tzinfo=timezone.utc),
                              source="test"),
                ])
    result = fa.run_fusion_audit(months=None, quiet=True, fundamentals_db=str(db_path))
    assert result["ok"] is True
    assert result["fundamental_coverage"]["net_margin"] > 0.0


def test_run_fusion_audit_insufficient_history_reports_plainly(monkeypatch):
    import nse.data as data_mod
    monkeypatch.setattr(data_mod, "load_price_history", lambda symbol: None)
    monkeypatch.setattr(fa, "_load_config", lambda: {
        "universe": {"symbols": ["X"]},
        "scanner": {"momentum": {"min_score": 55.0, "style": "fade"}},
        "data": {"index_benchmark": "^NSEI", "lookback_days": 860},
    })
    result = fa.run_fusion_audit(months=6, quiet=True)
    assert result["ok"] is False
    assert result["reason"] == "insufficient_history"


def test_sample_size_gate_reports_each_shortfall():
    msg = fa._sample_size_gate(10, 5, 5, quiet=True)
    assert msg is not None
    assert "train=10" in msg and "calibration=5" in msg and "held-out=5" in msg


def test_sample_size_gate_none_when_all_clear():
    assert fa._sample_size_gate(500, 100, 100, quiet=True) is None


def test_same_budget_lift_zero_division_when_baseline_selects_everything():
    scores = np.array([80.0, 80.0])
    probs = np.array([0.5, 0.6])
    labels = np.array([1, 0])
    with pytest.raises(ZeroDivisionError):
        fa._same_budget_lift(scores, probs, labels, min_score=50.0)


def test_same_budget_lift_computes_hit_rate_difference():
    scores = np.array([80.0, 80.0, 10.0, 10.0])  # baseline picks first 2 (k=2)
    labels = np.array([1, 0, 0, 1])
    # fusion model correctly identifies the two true winners (index 0 and 3)
    probs = np.array([0.9, 0.1, 0.2, 0.95])
    lift = fa._same_budget_lift(scores, probs, labels, min_score=50.0)
    # baseline hit rate on {0,1} = 0.5; fusion top-2 by prob = {0,3} hit rate = 1.0
    assert lift == pytest.approx(0.5)
