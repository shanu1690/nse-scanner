"""Tests for nse/risk/correlation.py."""

import numpy as np
import pandas as pd
import pytest

from nse.risk.correlation import correlation_matrix


def _df(close, start="2024-01-02"):
    idx = pd.date_range(start, periods=len(close), freq="B")
    return pd.DataFrame({"Open": close, "High": close, "Low": close,
                         "Close": close, "Volume": 100000}, index=idx)


def test_correlation_matrix_identical_series_is_one():
    n = 70
    base = list(100 + np.cumsum(np.random.default_rng(0).normal(0, 1, n)))
    frames = {"A": _df(base), "B": _df(base)}
    corr = correlation_matrix(["A", "B"], frames, lookback_days=60)
    assert corr.loc["A", "B"] == pytest.approx(1.0, abs=1e-6)


def test_correlation_matrix_inverse_series_is_strongly_negative():
    n = 70
    rng = np.random.default_rng(1)
    base = 100 + np.cumsum(rng.normal(0, 1, n))
    inverse = 200 - base  # strongly anti-correlated (not exactly -1: pct_change
                          # of an affine transform of a series isn't an exact
                          # sign-flip of the original's own pct_change)
    frames = {"A": _df(list(base)), "B": _df(list(inverse))}
    corr = correlation_matrix(["A", "B"], frames, lookback_days=60)
    assert corr.loc["A", "B"] < -0.9


def test_correlation_matrix_excludes_symbols_with_too_little_history():
    n = 70
    rng = np.random.default_rng(2)
    long_hist = 100 + np.cumsum(rng.normal(0, 1, n))
    short_hist = 100 + np.cumsum(rng.normal(0, 1, 10))  # < lookback+1
    frames = {"A": _df(list(long_hist)), "B": _df(list(long_hist)), "SHORT": _df(list(short_hist))}
    corr = correlation_matrix(["A", "B", "SHORT"], frames, lookback_days=60)
    assert "SHORT" not in corr.columns
    assert {"A", "B"} <= set(corr.columns)


def test_correlation_matrix_none_when_fewer_than_two_symbols_qualify():
    n = 70
    frames = {"A": _df(list(100 + np.cumsum(np.random.default_rng(3).normal(0, 1, n))))}
    assert correlation_matrix(["A"], frames, lookback_days=60) is None


def test_correlation_matrix_none_when_symbol_missing_from_frames():
    assert correlation_matrix(["X", "Y"], {}, lookback_days=60) is None
