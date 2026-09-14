"""Tests for the rebuilt walk-forward engine (nse/backtest.py).

Trade simulation and the statistics helpers are tested directly against
hand-built inputs (fast, exact, no dependence on how analyze_stock() scores
anything). The full run_backtest()/run_factor_analysis() entry points are
also exercised end-to-end against synthetic OHLCV sized to clear the
sample-size gate, to catch wiring mistakes the unit tests alone wouldn't.
"""

import numpy as np
import pandas as pd
import pytest

from nse import backtest as bt


# --------------------------------------------------------------- simulate_trade
def _bars(rows):
    """rows: list of (open, high, low, close)."""
    idx = pd.date_range("2024-01-01", periods=len(rows), freq="B")
    o, h, l, c = zip(*rows)
    return pd.DataFrame({"Open": o, "High": h, "Low": l, "Close": c}, index=idx)


def test_simulate_trade_hits_target_intrabar():
    bars = _bars([
        (100, 101, 99, 100),   # entry bar: opens at 100
        (100, 112, 99, 105),   # high touches target (110) intrabar
        (105, 106, 104, 105),
    ])
    trade = bt.simulate_trade(bars, entry_idx=0, stop=95, target=110, risk=5)
    assert trade["exit_reason"] == "target"
    assert trade["exit_price"] == 110
    assert trade["bars_held"] == 2
    assert trade["entry_price"] == 100
    assert trade["risk"] == 5


def test_simulate_trade_hits_stop_intrabar():
    bars = _bars([
        (100, 101, 99, 100),
        (100, 102, 90, 95),   # low breaches stop (95) intrabar
    ])
    trade = bt.simulate_trade(bars, entry_idx=0, stop=95, target=110, risk=5)
    assert trade["exit_reason"] == "stop"
    assert trade["exit_price"] == 95
    assert trade["bars_held"] == 2


def test_simulate_trade_gap_below_stop_fills_at_open_not_stop():
    """The planned risk (5, from a 100 decision price vs a 95 stop) is passed
    in explicitly -- it must NOT be re-derived from the gapped entry price,
    or this trade (a real, very bad outcome) would look like an invalid
    setup and get silently discarded instead of counted."""
    bars = _bars([(80, 82, 78, 81)])  # opens already below stop=95
    trade = bt.simulate_trade(bars, entry_idx=0, stop=95, target=110, risk=5)
    assert trade["exit_reason"] == "stop"
    assert trade["exit_price"] == 80  # worse than the stop level, not the stop level
    assert trade["risk"] == 5         # the planned risk, unchanged by the gap


def test_simulate_trade_gap_above_target_fills_at_open():
    bars = _bars([(115, 116, 114, 115)])  # opens already above target=110
    trade = bt.simulate_trade(bars, entry_idx=0, stop=95, target=110, risk=5)
    assert trade["exit_reason"] == "target"
    assert trade["exit_price"] == 115  # better than the target level


def test_simulate_trade_ambiguous_bar_resolves_conservatively_as_stop():
    bars = _bars([(100, 111, 94, 100)])  # both stop and target touched in one bar
    trade = bt.simulate_trade(bars, entry_idx=0, stop=95, target=110, risk=5)
    assert trade["exit_reason"] == "stop"


def test_simulate_trade_time_exit_when_neither_touched():
    bars = _bars([(100, 101, 99, 100)] * 5)
    trade = bt.simulate_trade(bars, entry_idx=0, stop=50, target=200, risk=50, max_hold_bars=3)
    assert trade["exit_reason"] == "time"
    assert trade["bars_held"] == 3
    assert trade["exit_price"] == 100  # close of the 3rd held bar


def test_simulate_trade_none_when_no_entry_bar_available():
    bars = _bars([(100, 101, 99, 100)])
    assert bt.simulate_trade(bars, entry_idx=5, stop=95, target=110, risk=5) is None


def test_simulate_trade_none_when_risk_not_positive():
    bars = _bars([(100, 101, 99, 100)])
    assert bt.simulate_trade(bars, entry_idx=0, stop=100, target=110, risk=0) is None
    assert bt.simulate_trade(bars, entry_idx=0, stop=100, target=110, risk=-5) is None


def test_r_multiple_costs_reduce_a_winning_trade():
    trade = {"entry_price": 100.0, "exit_price": 110.0, "risk": 5.0}
    gross = bt.r_multiple(trade, cost_pct=0.0)
    net = bt.r_multiple(trade, cost_pct=0.15)
    assert gross == pytest.approx(2.0)
    assert net < gross
    net_doubled = bt.r_multiple(trade, cost_pct=0.30)
    assert net_doubled < net


# --------------------------------------------------------------- block planning
def test_plan_blocks_none_when_insufficient_history():
    dates = pd.date_range("2024-01-01", periods=50, freq="B")
    assert bt._plan_blocks(dates) is None


def test_plan_blocks_returns_min_shape_when_just_enough():
    needed = (bt.MIN_ROLLING_BLOCKS + 1) * bt.MIN_BLOCK_BARS
    dates = pd.date_range("2024-01-01", periods=needed, freq="B")
    blocks = bt._plan_blocks(dates)
    assert blocks is not None
    assert len(blocks) == bt.MIN_ROLLING_BLOCKS + 1
    # contiguous and covering every date exactly once
    total = sum(len(b) for b in blocks)
    assert total == needed


def test_block_of_finds_the_right_block():
    dates = pd.date_range("2024-01-01", periods=90, freq="B")
    blocks = bt._plan_blocks(dates) if len(dates) >= (bt.MIN_ROLLING_BLOCKS + 1) * bt.MIN_BLOCK_BARS else None
    # construct bounds manually regardless of gate, to test _block_of in isolation
    bounds = [(dates[0], dates[29]), (dates[30], dates[59]), (dates[60], dates[89])]
    assert bt._block_of(dates[0], bounds) == 0
    assert bt._block_of(dates[45], bounds) == 1
    assert bt._block_of(dates[89], bounds) == 2
    assert bt._block_of(dates[0] - pd.Timedelta(days=1), bounds) is None


def test_sample_size_gate_message_is_actionable():
    msg = bt._sample_size_gate_message(n_available=50, bench_total_bars=390, lookback_days=560)
    assert "50 usable trading days" in msg
    assert "lookback_days" in msg
    assert "refresh" in msg.lower()


# --------------------------------------------------------------- Signal-level stats
def _sig(symbol, day, score, fwd5, r=None, exit_reason="target"):
    trade = None
    if r is not None:
        # back out entry/exit/risk that gives exactly r at 0 cost, for exact assertions
        trade = {"entry_price": 100.0, "exit_price": 100.0 + r * 10.0, "risk": 10.0,
                  "exit_reason": exit_reason, "bars_held": 3}
    return bt.Signal(symbol=symbol, date=pd.Timestamp(day), block=0, score=score,
                      subscores={}, fwd_pct={5: fwd5}, trade=trade)


def test_lift_stat_matches_hand_computed_value():
    signals = [
        _sig("A", "2024-01-01", 70, 5.0),   # signal, hit
        _sig("A", "2024-01-02", 70, -1.0),  # signal, miss
        _sig("B", "2024-01-01", 40, 5.0),   # baseline-only, hit
        _sig("B", "2024-01-02", 40, -1.0),  # baseline-only, miss
    ]
    # baseline hit rate = 2/4 = 0.5; signal (score>=60) hit rate = 1/2 = 0.5 -> lift 0
    assert bt._lift_stat(signals, 60, 5, 3.0) == pytest.approx(0.0)


def test_bootstrap_ci_returns_none_with_fewer_than_two_symbols():
    signals = [_sig("A", "2024-01-01", 70, 5.0), _sig("A", "2024-01-02", 70, -1.0)]
    assert bt.bootstrap_ci(signals, lambda b: bt._lift_stat(b, 60, 5, 3.0)) is None


def test_bootstrap_ci_brackets_a_real_signal():
    rng = np.random.default_rng(0)
    signals = []
    for sym_i in range(10):
        for day in range(20):
            score = 70 if rng.random() < 0.5 else 30
            # genuine relationship: high score -> more likely to hit +3%
            fwd = rng.normal(4 if score >= 60 else 0, 2)
            signals.append(_sig(f"S{sym_i}", f"2024-{1 + day // 28:02d}-{1 + day % 28:02d}", score, fwd))
    ci = bt.bootstrap_ci(signals, lambda b: bt._lift_stat(b, 60, 5, 3.0), n_boot=500)
    assert ci is not None
    lo, hi = ci
    assert lo < hi
    real = bt._lift_stat(signals, 60, 5, 3.0)
    assert lo - 0.2 <= real <= hi + 0.2  # generous tolerance, just checking sanity


def test_label_shuffle_collapses_a_genuine_relationship_towards_zero():
    rng = np.random.default_rng(1)
    signals = []
    for sym_i in range(8):
        for day in range(30):
            score = 70 if rng.random() < 0.5 else 30
            fwd = rng.normal(6 if score >= 60 else 0, 1.5)
            signals.append(_sig(f"S{sym_i}", f"2024-{1 + day // 28:02d}-{1 + day % 28:02d}", score, fwd))
    real = bt._lift_stat(signals, 60, 5, 3.0)
    shuffled = bt.label_shuffle_control(signals, 60, seed=99)
    assert real > 0.1  # the constructed relationship is real and sizeable
    assert abs(shuffled) < real / 2  # shuffling should collapse it


def test_top_contributor_symbols_ranks_by_qualifying_signal_count():
    signals = (
        [_sig("A", f"2024-01-{i:02d}", 70, 5.0) for i in range(1, 4)]
        + [_sig("B", "2024-01-01", 70, 5.0)]
        + [_sig("C", "2024-01-01", 30, 5.0)]  # doesn't qualify (score < threshold)
    )
    top = bt._top_contributor_symbols(signals, min_score=60, n=1)
    assert top == {"A"}


def test_best_month_picks_the_highest_mean_forward_return():
    signals = [
        _sig("A", "2024-01-05", 70, 1.0),
        _sig("A", "2024-02-05", 70, 9.0),
    ]
    assert bt._best_month(signals, min_score=60) == "2024-02"


def test_trade_stats_profit_factor_and_drawdown():
    signals = [
        _sig("A", "2024-01-01", 70, 5.0, r=2.0),
        _sig("A", "2024-01-02", 70, 5.0, r=-1.0),
        _sig("A", "2024-01-03", 70, 5.0, r=-1.0),
        _sig("A", "2024-01-04", 70, 5.0, r=3.0),
    ]
    stats = bt._trade_stats(signals, min_score=60, cost_pct=0.0)
    assert stats["n_trades"] == 4
    assert stats["mean_r"] == pytest.approx(0.75)
    assert stats["win_rate"] == pytest.approx(0.5)
    assert stats["profit_factor"] == pytest.approx(5.0 / 2.0)
    assert stats["max_consecutive_losers"] == 2
    # equity path: 2, 1, 0, 3 -> peak tracks [2,2,2,3] -> drawdown min at -2
    assert stats["max_drawdown_r"] == pytest.approx(-2.0)


def test_trade_stats_none_when_no_trades():
    assert bt._trade_stats([], min_score=60, cost_pct=0.15) is None


# --------------------------------------------------------------- end-to-end
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


@pytest.fixture
def synthetic_cache(tmp_path, monkeypatch):
    """Enough history (>= 420 bars) to clear the sample-size gate, for every
    symbol plus the benchmark, all served from load_price_history()."""
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
    return symbols


def test_run_backtest_end_to_end_clears_the_gate(synthetic_cache):
    result = bt.run_backtest(min_score=55.0, months=None, symbols=synthetic_cache,
                              quiet=True, fade=False)
    assert result["ok"] is True
    assert len(result["block_bounds"]) >= bt.MIN_ROLLING_BLOCKS + 1
    assert len(result["held_out"]) > 0
    assert len(result["rolling"]) > 0
    # held-out block strictly later than every rolling-block date
    assert max(s.date for s in result["rolling"]) < min(s.date for s in result["held_out"])
    assert result["coverage"].coverage_ratio > 0.9


def test_run_factor_analysis_end_to_end_clears_the_gate(synthetic_cache):
    result = bt.run_factor_analysis(months=None, min_score=55.0)
    assert result["ok"] is True
    assert len(result["block_bounds"]) >= bt.MIN_ROLLING_BLOCKS + 1
    # oos_diffs computed per-factor without raising
    assert set(result["oos_diffs"]) == set(bt.FACTORS)


def test_run_backtest_reports_insufficient_history_plainly(monkeypatch):
    import nse.data as data_mod
    monkeypatch.setattr(data_mod, "load_price_history", lambda symbol: None)
    result = bt.run_backtest(min_score=60.0, months=6, symbols=["X"], quiet=True)
    assert result["ok"] is False
    assert result["reason"] == "insufficient_history"
    assert "lookback_days" in result["message"]


def test_coverage_report_flags_skips_and_low_coverage():
    cov = bt.CoverageReport(universe=["A", "B", "C", "D"])
    cov.add_scored("A")
    cov.add_skip("B", "insufficient_history", "10 bars")
    cov.add_skip("C", "scoring_exception", "boom")
    cov.add_skip("D", "insufficient_history", "5 bars")
    text = cov.render()
    assert "1/4" in text
    assert "below the 95%" in text
    assert "insufficient_history: 2 symbol(s)" in text
