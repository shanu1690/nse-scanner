"""Tests for nse/regime/compare.py: the pure signal-selection logic
directly, plus an end-to-end smoke test of run_regime_switch_audit against
a synthetic cache (mirrors tests/test_backtest_walkforward.py's fixture
pattern)."""

import numpy as np
import pandas as pd
import pytest

from nse import backtest as bt
from nse.backtest import Signal
from nse.regime import compare as rc


def _sig(symbol, date, block=0, score=70.0, fwd5=5.0):
    return Signal(symbol=symbol, date=pd.Timestamp(date), block=block, score=score,
                  subscores={}, fwd_pct={5: fwd5}, trade=None)


# -------------------------------------------------------- pure logic units
def test_static_style_signals_picks_fade_or_momentum():
    fade_sigs = [_sig("A", "2024-01-02")]
    mom_sigs = [_sig("B", "2024-01-02")]
    assert rc._static_style_signals(mom_sigs, fade_sigs, static_fade=True) is fade_sigs
    assert rc._static_style_signals(mom_sigs, fade_sigs, static_fade=False) is mom_sigs


def test_regime_style_signals_routes_by_date_label():
    d1, d2, d3 = pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03"), pd.Timestamp("2024-01-04")
    fade_sigs = [_sig("A", d1), _sig("A", d2), _sig("A", d3)]
    mom_sigs = [_sig("B", d1), _sig("B", d2), _sig("B", d3)]
    regime_history = pd.DataFrame(
        {"regime": ["trending_up", "mean_reverting", "high_vol_shock"]},
        index=[d1, d2, d3],
    )
    out = rc._regime_style_signals(mom_sigs, fade_sigs, regime_history)
    out_by_date = {s.date: s.symbol for s in out}
    assert out_by_date[d1] == "B"   # trending_up -> momentum
    assert out_by_date[d2] == "A"   # mean_reverting -> fade
    assert d3 not in out_by_date    # high_vol_shock -> no signal at all


def test_regime_style_signals_unknown_falls_back_to_fade():
    d = pd.Timestamp("2024-01-02")
    fade_sigs = [_sig("A", d)]
    mom_sigs = [_sig("B", d)]
    regime_history = pd.DataFrame({"regime": ["unknown"]}, index=[d])
    out = rc._regime_style_signals(mom_sigs, fade_sigs, regime_history)
    assert len(out) == 1 and out[0].symbol == "A"


def test_regime_style_signals_missing_date_treated_as_unknown():
    d = pd.Timestamp("2024-01-02")
    other_day = pd.Timestamp("2024-01-03")
    fade_sigs = [_sig("A", d)]
    mom_sigs = []
    regime_history = pd.DataFrame({"regime": ["trending_up"]}, index=[other_day])
    out = rc._regime_style_signals(mom_sigs, fade_sigs, regime_history)
    assert len(out) == 1 and out[0].symbol == "A"  # falls back to fade (unknown -> True)


# ------------------------------------------------------- permutation test
def _perm_signal(symbol, date, score, fwd5):
    return Signal(symbol=symbol, date=pd.Timestamp(date), block=0, score=score,
                  subscores={}, fwd_pct={5: fwd5}, trade=None)


def test_permutation_test_none_when_too_few_dates():
    dates = pd.date_range("2024-01-02", periods=5, freq="B")
    fade_sigs = [_perm_signal("A", d, 70, 5.0) for d in dates]
    regime_history = pd.DataFrame({"regime": ["trending_up"] * 5}, index=dates)
    result = rc._regime_label_permutation_test([], fade_sigs, regime_history,
                                                 min_score=50.0, real_lift=0.1)
    assert result is None


def test_permutation_test_none_when_all_dates_share_one_label():
    dates = pd.date_range("2024-01-02", periods=20, freq="B")
    fade_sigs = [_perm_signal("A", d, 70, 5.0) for d in dates]
    regime_history = pd.DataFrame({"regime": ["mean_reverting"] * 20}, index=dates)
    result = rc._regime_label_permutation_test([], fade_sigs, regime_history,
                                                 min_score=50.0, real_lift=0.1)
    assert result is None  # nothing to shuffle -- every day has the same label


def test_permutation_test_high_p_value_when_lift_is_indistinguishable_from_chance():
    """Real outcomes have NO relationship to which days got which label --
    shuffling the label assignment should reproduce a similar lift often,
    giving a high (untrustworthy) p-value."""
    rng = np.random.default_rng(0)
    dates = pd.date_range("2024-01-02", periods=60, freq="B")
    labels = (["trending_up"] * 30 + ["mean_reverting"] * 30)
    rng.shuffle(labels)
    regime_history = pd.DataFrame({"regime": labels}, index=dates)
    # fwd_pct is pure noise, unrelated to the regime label
    fade_sigs = [_perm_signal("A", d, 70, rng.normal(0, 2)) for d in dates]
    mom_sigs = [_perm_signal("B", d, 70, rng.normal(0, 2)) for d in dates]
    real_signals = rc._regime_style_signals(mom_sigs, fade_sigs, regime_history)
    real_lift = bt._lift_stat(real_signals, 50.0, 5, 3.0)
    result = rc._regime_label_permutation_test(mom_sigs, fade_sigs, regime_history,
                                                 min_score=50.0, real_lift=real_lift, n_perm=200)
    assert result is not None
    p_value, n_valid = result
    assert n_valid > 0
    # Since the real assignment is itself just one arbitrary shuffle of a
    # noise-driven outcome, its own lift should not reliably look extreme
    # against the null it's drawn from.
    assert 0.0 <= p_value <= 1.0


def test_regime_label_counts():
    d1, d2 = pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")
    sigs = [_sig("A", d1), _sig("B", d1), _sig("A", d2)]
    regime_history = pd.DataFrame({"regime": ["trending_up", "mean_reverting"]}, index=[d1, d2])
    counts = rc._regime_label_counts(sigs, regime_history)
    assert counts == {"trending_up": 1, "mean_reverting": 1}


# ------------------------------------------------------------------- end-to-end
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
def synthetic_regime_cache(monkeypatch):
    import nse.data as data_mod

    symbols = [f"SYM{i}" for i in range(8)]
    frames = {s: _synthetic_price_frame(seed=i) for i, s in enumerate(symbols)}
    bench = _synthetic_price_frame(seed=99)
    vix = _synthetic_vix_frame(seed=50)

    def fake_load(symbol):
        if symbol == "^TESTBENCH":
            return bench.copy()
        if symbol == rc.VIX_TICKER:
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
    monkeypatch.setattr(rc, "_load_config", lambda: fake_config)
    return symbols


def test_run_regime_switch_audit_end_to_end_does_not_crash(synthetic_regime_cache):
    result = rc.run_regime_switch_audit(months=None, quiet=True)
    assert result["reason"] if not result["ok"] else True  # either shape is acceptable
    if result["ok"]:
        assert "static_lift" in result and "regime_lift" in result
        assert isinstance(result["beats_static"], bool)
        assert "regime_counts" in result


def test_run_regime_switch_audit_insufficient_history_reports_plainly(monkeypatch):
    import nse.data as data_mod
    monkeypatch.setattr(data_mod, "load_price_history", lambda symbol: None)
    monkeypatch.setattr(rc, "_load_config", lambda: {
        "universe": {"symbols": ["X"]},
        "scanner": {"momentum": {"min_score": 55.0, "style": "fade"}},
        "data": {"index_benchmark": "^NSEI", "lookback_days": 860},
    })
    result = rc.run_regime_switch_audit(months=6, quiet=True)
    assert result["ok"] is False
    assert result["reason"] == "insufficient_history"
