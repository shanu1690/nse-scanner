"""Tests for nse/regime/: breadth (point-in-time market breadth) and
classify (the fixed regime rule + its point-in-time history builder)."""

import numpy as np
import pandas as pd
import pytest

from nse.regime import breadth as br
from nse.regime import classify as cl


def _flat_df(values, start="2024-01-02"):
    idx = pd.date_range(start, periods=len(values), freq="B")
    return pd.DataFrame({"Open": values, "High": values, "Low": values,
                          "Close": values, "Volume": 100000}, index=idx)


# ------------------------------------------------------------------- breadth
def test_pct_above_50dma_needs_minimum_bars():
    dates = pd.date_range("2024-06-01", periods=5, freq="B")
    short = _flat_df([100] * 10)  # < MIN_BARS_FOR_50DMA
    out = br.build_breadth_series({"A": short}, dates)
    assert out["pct_above_50dma"].isna().all()


def test_pct_above_50dma_refuses_below_symbol_floor():
    dates = pd.date_range("2024-06-01", periods=3, freq="B")
    frames = {f"S{i}": _flat_df(list(range(100, 100 + 60))) for i in range(3)}  # only 3 < floor of 10
    out = br.build_breadth_series(frames, dates)
    assert out["pct_above_50dma"].isna().all()


def test_pct_above_50dma_counts_correctly():
    # 12 symbols (clears the >=10 floor): 8 trending up (close rising, above
    # its own 50DMA by construction), 4 flat-then-dropping (below their 50DMA
    # on the test date).
    n = 60
    up = np.linspace(100, 160, n)          # rising -> last close above SMA50
    down = np.concatenate([np.full(50, 100.0), np.linspace(100, 40, n - 50)])  # sharp late drop -> below SMA50
    frames = {}
    for i in range(8):
        frames[f"UP{i}"] = _flat_df(up)
    for i in range(4):
        frames[f"DN{i}"] = _flat_df(down)
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    out = br.build_breadth_series(frames, dates)
    last_pct = out["pct_above_50dma"].iloc[-1]
    assert last_pct == pytest.approx(8 / 12)


def test_advance_decline_ratio():
    n = 5
    dates = pd.date_range("2024-01-02", periods=n, freq="B")
    frames = {}
    for i in range(7):
        frames[f"ADV{i}"] = _flat_df([100, 101, 102, 103, 104])  # every day an advance
    for i in range(4):
        frames[f"DEC{i}"] = _flat_df([100, 99, 98, 97, 96])      # every day a decline
    out = br.build_breadth_series(frames, dates)
    assert out["advance_decline_ratio"].iloc[-1] == pytest.approx(7 / 11)


# ------------------------------------------------------------------ classify
def test_classify_row_trending_up():
    row = {"vix_percentile": 40.0, "vix_trend_5d": 0.5,
           "nifty_close": 110, "nifty_ema21": 105, "nifty_ema50": 100,
           "pct_above_50dma": 0.6}
    assert cl.classify_row(row) == "trending_up"


def test_classify_row_trending_down():
    row = {"vix_percentile": 40.0, "vix_trend_5d": 0.5,
           "nifty_close": 90, "nifty_ema21": 95, "nifty_ema50": 100,
           "pct_above_50dma": 0.3}
    assert cl.classify_row(row) == "trending_down"


def test_classify_row_high_vol_shock_overrides_trend():
    # Otherwise a clean uptrend, but VIX is in its top quintile AND rising --
    # shock takes priority over trend.
    row = {"vix_percentile": 85.0, "vix_trend_5d": 3.0,
           "nifty_close": 110, "nifty_ema21": 105, "nifty_ema50": 100,
           "pct_above_50dma": 0.6}
    assert cl.classify_row(row) == "high_vol_shock"


def test_classify_row_high_vix_but_falling_is_not_shock():
    # High VIX percentile alone isn't enough -- it must also be rising
    # (a VIX that's high but actively cooling isn't an active shock).
    row = {"vix_percentile": 85.0, "vix_trend_5d": -2.0,
           "nifty_close": 110, "nifty_ema21": 105, "nifty_ema50": 100,
           "pct_above_50dma": 0.6}
    assert cl.classify_row(row) == "trending_up"


def test_classify_row_mean_reverting_when_trend_and_breadth_disagree():
    # Price structure says uptrend but breadth doesn't confirm it (<55%
    # above 50DMA) -- falls through to mean_reverting rather than forcing
    # a trend label breadth doesn't support.
    row = {"vix_percentile": 40.0, "vix_trend_5d": 0.0,
           "nifty_close": 110, "nifty_ema21": 105, "nifty_ema50": 100,
           "pct_above_50dma": 0.40}
    assert cl.classify_row(row) == "mean_reverting"


def test_classify_row_unknown_when_nifty_trend_inputs_missing():
    row = {"vix_percentile": 40.0, "vix_trend_5d": 0.0,
           "nifty_close": np.nan, "nifty_ema21": 105, "nifty_ema50": 100,
           "pct_above_50dma": 0.6}
    assert cl.classify_row(row) == "unknown"


def test_build_regime_history_shape_and_columns():
    n = 300
    rng = np.random.default_rng(0)
    nifty = _flat_df(100 + rng.normal(0, 1, n).cumsum())
    vix = _flat_df(np.clip(12 + rng.normal(0, 1, n).cumsum() * 0.1, 8, None))
    frames = {f"S{i}": _flat_df(100 + rng.normal(0, 1, n).cumsum() + i) for i in range(15)}
    hist = cl.build_regime_history(nifty, vix, frames)
    assert len(hist) == n
    for col in ("nifty_close", "vix_level", "vix_percentile", "pct_above_50dma", "regime"):
        assert col in hist.columns
    assert set(hist["regime"].unique()) <= set(cl.REGIME_LABELS)


def test_build_regime_history_never_uses_a_future_bar():
    """The regime label at date d must be identical whether or not the
    input frames extend beyond d -- proof there's no look-ahead."""
    n = 300
    rng = np.random.default_rng(1)
    nifty_full_series = 100 + rng.normal(0, 1, n).cumsum()
    vix_full_series = np.clip(12 + rng.normal(0, 1, n).cumsum() * 0.1, 8, None)
    nifty_full = _flat_df(nifty_full_series)
    vix_full = _flat_df(vix_full_series)
    frames_full = {f"S{i}": _flat_df(100 + rng.normal(0, 1, n).cumsum() + i) for i in range(15)}

    cutoff = 200
    hist_full = cl.build_regime_history(nifty_full, vix_full, frames_full)

    nifty_trunc = nifty_full.iloc[:cutoff]
    vix_trunc = vix_full.iloc[:cutoff]
    frames_trunc = {k: v.iloc[:cutoff] for k, v in frames_full.items()}
    hist_trunc = cl.build_regime_history(nifty_trunc, vix_trunc, frames_trunc)

    d = hist_trunc.index[-1]
    assert hist_trunc.loc[d, "regime"] == hist_full.loc[d, "regime"]
    full_breadth = hist_full.loc[d, "pct_above_50dma"]
    trunc_breadth = hist_trunc.loc[d, "pct_above_50dma"]
    if pd.isna(full_breadth):
        assert pd.isna(trunc_breadth)
    else:
        assert trunc_breadth == pytest.approx(full_breadth)
