"""Tests for nse/reporting.py: the structured, JSON-safe backtest snapshot
export used by the dashboard's Backtest tab (Phase 9)."""

import json
import math

import numpy as np
import pandas as pd
import pytest

from nse import backtest as bt
from nse import reporting
from nse.fusion import audit as fa


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
    """Same shape as tests/test_fusion_audit.py's fixture of the same name --
    a synthetic price/VIX cache plus a fake config, so _fusion_block() /
    build_backtest_snapshot(include_fusion=True) can run the real fusion
    audit end-to-end without hitting the network or the real 210-symbol
    universe."""
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
    monkeypatch.setitem(bt._CONFIG["universe"], "symbols", symbols)
    monkeypatch.setitem(bt._CONFIG["scanner"]["momentum"], "style", "momentum")

    fake_config = {
        "universe": {"symbols": symbols},
        "scanner": {"momentum": {"min_score": 20.0, "style": "fade"}},
        "data": {"index_benchmark": "^TESTBENCH", "lookback_days": 660},
    }
    monkeypatch.setattr(fa, "_load_config", lambda: fake_config)
    return symbols


@pytest.fixture
def synthetic_cache(monkeypatch):
    import nse.data as data_mod

    symbols = [f"SYM{i}" for i in range(8)]
    frames = {s: _synthetic_price_frame(seed=i) for i, s in enumerate(symbols)}
    bench = _synthetic_price_frame(seed=99)

    def fake_load(symbol):
        if symbol == "^TESTBENCH":
            return bench.copy()
        return frames.get(symbol, pd.DataFrame()).copy() if symbol in frames else None

    monkeypatch.setattr(data_mod, "load_price_history", fake_load)
    monkeypatch.setattr(bt, "data_mod", data_mod)
    monkeypatch.setitem(bt._CONFIG["data"], "index_benchmark", "^TESTBENCH")
    monkeypatch.setitem(bt._CONFIG["data"], "lookback_days", 660)
    monkeypatch.setitem(bt._CONFIG["universe"], "symbols", symbols)
    monkeypatch.setitem(bt._CONFIG["scanner"]["momentum"], "style", "momentum")
    return symbols


def test_safe_float_maps_non_finite_to_none():
    assert reporting._safe_float(float("inf")) is None
    assert reporting._safe_float(float("nan")) is None
    assert reporting._safe_float(None) is None
    assert reporting._safe_float(1.5) == 1.5


def test_build_backtest_snapshot_end_to_end(synthetic_cache):
    snapshot = reporting.build_backtest_snapshot(months=None, min_score=55.0, fade=False)
    assert snapshot["ok"] is True
    assert snapshot["style"] == "momentum"
    assert snapshot["n_blocks"] >= 4
    assert snapshot["coverage"]["ratio"] > 0.9

    ho = snapshot["held_out"]
    assert ho["window"] is not None
    # hit_rate may be None if the synthetic held-out block has no signals
    # clearing the threshold, but the key must always be present.
    assert "hit_rate" in ho and "trades" in ho and "robustness" in ho and "label_shuffle" in ho
    assert "cost_0_15pct" in ho["trades"] and "cost_0_30pct" in ho["trades"]

    assert snapshot["factor_analysis"] is not None
    assert set(snapshot["factor_analysis"]) == set(bt.FACTORS)


def test_build_backtest_snapshot_is_json_serializable(synthetic_cache):
    snapshot = reporting.build_backtest_snapshot(months=None, min_score=55.0, fade=False)
    text = json.dumps(snapshot)
    assert "Infinity" not in text and "NaN" not in text
    # round-trips cleanly
    assert json.loads(text)["ok"] is True


def test_build_backtest_snapshot_insufficient_history_reports_plainly(monkeypatch):
    import nse.data as data_mod
    monkeypatch.setattr(data_mod, "load_price_history", lambda symbol: None)
    monkeypatch.setattr(bt, "data_mod", data_mod)
    monkeypatch.setitem(bt._CONFIG["universe"], "symbols", ["X"])
    snapshot = reporting.build_backtest_snapshot(months=6, min_score=60.0, fade=True)
    assert snapshot["ok"] is False
    assert "message" in snapshot


def test_export_backtest_snapshot_writes_file(tmp_path, synthetic_cache):
    path = tmp_path / "backtest.json"
    snapshot = reporting.export_backtest_snapshot(str(path), months=None, min_score=55.0, fade=False)
    assert path.exists()
    on_disk = json.loads(path.read_text())
    assert on_disk["ok"] == snapshot["ok"]


def test_trade_block_handles_infinite_profit_factor(synthetic_cache):
    # Directly exercise _trade_block's inf-handling with a synthetic
    # all-winners signal set (profit_factor -> inf in _trade_stats).
    from nse.backtest import Signal
    signals = [
        Signal(symbol="A", date=pd.Timestamp("2024-01-02"), block=0, score=70.0,
              subscores={}, fwd_pct={5: 5.0},
              trade={"entry_price": 100.0, "exit_price": 110.0, "exit_reason": "target",
                     "bars_held": 3, "risk": 5.0}),
    ]
    block = reporting._trade_block(signals, min_score=60.0)
    pf = block["cost_0_15pct"]["profit_factor"]
    assert pf is None or math.isfinite(pf)


# --------------------------------------------------------------- fusion block

def test_fusion_omitted_by_default(synthetic_cache):
    snapshot = reporting.build_backtest_snapshot(months=None, min_score=55.0, fade=False)
    assert snapshot["fusion"] is None


def test_fusion_block_end_to_end_is_json_safe(synthetic_fusion_cache, tmp_path):
    missing_db = tmp_path / "no_such.db"
    block = reporting._fusion_block(None, fundamentals_db=str(missing_db))
    assert block["ok"] is True
    assert block["n_train"] >= fa.MIN_TRAIN_ROWS
    assert block["calib_method"] in ("sigmoid", "isotonic")
    assert isinstance(block["reliability"], list) and len(block["reliability"]) > 0
    for b in block["reliability"]:
        assert set(b) == {"bin", "n", "mean_predicted", "observed_freq"}
    assert block["has_fundamentals"] is False
    assert len(block["demo_picks"]) > 0
    for pick in block["demo_picks"]:
        # date must already be a plain string, not a Timestamp -- json.dumps
        # would TypeError on a Timestamp.
        assert isinstance(pick["date"], str)
    text = json.dumps(block)
    assert "Infinity" not in text and "NaN" not in text
    round_tripped = json.loads(text)
    assert round_tripped["reliability"] == block["reliability"]


def test_fusion_block_reports_refusal_plainly(monkeypatch):
    import nse.data as data_mod
    monkeypatch.setattr(data_mod, "load_price_history", lambda symbol: None)
    monkeypatch.setattr(fa, "_load_config", lambda: {
        "universe": {"symbols": ["X"]},
        "scanner": {"momentum": {"min_score": 55.0, "style": "fade"}},
        "data": {"index_benchmark": "^NSEI", "lookback_days": 860},
    })
    block = reporting._fusion_block(6)
    assert block["ok"] is False
    assert "message" in block
    assert set(block) == {"ok", "message"}


def test_build_backtest_snapshot_include_fusion_wires_it_in(synthetic_fusion_cache, tmp_path):
    missing_db = tmp_path / "no_such.db"
    # build_backtest_snapshot's own walk-forward needs config['scanner']['momentum']
    # from bt._CONFIG (patched by synthetic_fusion_cache), independent of the
    # fake config _fusion_block's run_fusion_audit reads via fa._load_config.
    snapshot = reporting.build_backtest_snapshot(
        months=None, min_score=20.0, fade=True,
        include_fusion=True, fusion_months=None, fundamentals_db=str(missing_db))
    assert snapshot["ok"] is True
    assert snapshot["fusion"] is not None
    assert snapshot["fusion"]["ok"] is True
    text = json.dumps(snapshot)
    assert "Infinity" not in text and "NaN" not in text
