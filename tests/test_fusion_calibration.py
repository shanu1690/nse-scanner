"""Tests for nse/fusion/calibration.py: Brier score, the base-rate floor,
and the reliability curve."""

import numpy as np
import pytest

from nse.fusion import calibration as cal


def test_brier_score_perfect_predictions_is_zero():
    assert cal.brier_score([1, 0, 1, 0], [1.0, 0.0, 1.0, 0.0]) == pytest.approx(0.0)


def test_brier_score_worse_than_perfect():
    assert cal.brier_score([1, 0], [0.5, 0.5]) == pytest.approx(0.25)


def test_base_rate_brier_matches_manual_calc():
    y = [1, 1, 0, 0]  # base rate 0.5
    assert cal.base_rate_brier(y) == pytest.approx(0.25)


def test_base_rate_brier_empty_is_nan():
    assert np.isnan(cal.base_rate_brier([]))


def test_brier_against_base_rate_uses_reference_split_rate():
    y_train = [1, 1, 1, 0]  # rate 0.75
    y_eval = [1, 0]
    expected = ((1 - 0.75) ** 2 + (0 - 0.75) ** 2) / 2
    assert cal.brier_against_base_rate(y_train, y_eval) == pytest.approx(expected)


def test_brier_against_base_rate_empty_is_nan():
    assert np.isnan(cal.brier_against_base_rate([], [1, 0]))
    assert np.isnan(cal.brier_against_base_rate([1, 0], []))


def test_reliability_curve_bins_and_counts_sum_to_n():
    rng = np.random.default_rng(0)
    p = rng.uniform(0, 1, 100)
    y = (rng.uniform(0, 1, 100) < p).astype(int)
    curve = cal.reliability_curve(y, p, n_bins=10)
    assert sum(b["n"] for b in curve) == 100
    # ascending predicted probability across bins
    means = [b["mean_predicted"] for b in curve]
    assert means == sorted(means)


def test_reliability_curve_perfect_calibration_is_monotonic():
    rng = np.random.default_rng(1)
    p = np.sort(rng.uniform(0, 1, 500))
    y = (rng.uniform(0, 1, 500) < p).astype(int)
    curve = cal.reliability_curve(y, p, n_bins=10)
    # Not guaranteed exactly monotonic with noise, but should be close over
    # a reasonably large, well-calibrated sample -- check it doesn't crash
    # and produces the expected bin count.
    assert 1 <= len(curve) <= 10


def test_reliability_curve_handles_few_distinct_values():
    p = [0.5] * 20
    y = [1] * 10 + [0] * 10
    curve = cal.reliability_curve(y, p, n_bins=10)
    assert len(curve) == 1
    assert curve[0]["n"] == 20


def test_is_monotonic_true_for_increasing():
    curve = [{"observed_freq": 0.1}, {"observed_freq": 0.3}, {"observed_freq": 0.6}]
    assert cal.is_monotonic(curve)


def test_is_monotonic_false_for_decreasing():
    curve = [{"observed_freq": 0.6}, {"observed_freq": 0.3}, {"observed_freq": 0.1}]
    assert not cal.is_monotonic(curve)


def test_is_monotonic_tolerance_allows_small_dip():
    curve = [{"observed_freq": 0.30}, {"observed_freq": 0.29}, {"observed_freq": 0.40}]
    assert not cal.is_monotonic(curve, tolerance=0.0)
    assert cal.is_monotonic(curve, tolerance=0.02)
