"""Calibration diagnostics for the fusion model: Brier score (measured
against the base-rate-always floor PROJECT_BRIEF.md Section 1's CALIBRATION
criterion sets) and a reliability curve by decile of predicted probability.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import brier_score_loss

__all__ = ["brier_score", "base_rate_brier", "brier_against_base_rate",
           "reliability_curve", "is_monotonic"]


def brier_score(y_true, p) -> float:
    return float(brier_score_loss(y_true, p))


def base_rate_brier(y_true) -> float:
    """Brier score of the naive 'always predict the training base rate'
    model -- the floor a calibrated model must beat."""
    y_true = np.asarray(y_true, dtype=float)
    if len(y_true) == 0:
        return float("nan")
    base_rate = float(y_true.mean())
    p = np.full(len(y_true), base_rate)
    return brier_score(y_true, p)


def brier_against_base_rate(y_reference, y_eval) -> float:
    """Brier score on y_eval of a naive model that always predicts
    y_reference's positive rate. y_reference must be a DIFFERENT split than
    y_eval (e.g. the training set) -- measuring y_eval against its own mean
    would make the "naive baseline" secretly see the answer key."""
    y_reference = np.asarray(y_reference, dtype=float)
    y_eval = np.asarray(y_eval, dtype=float)
    if len(y_reference) == 0 or len(y_eval) == 0:
        return float("nan")
    rate = float(y_reference.mean())
    return brier_score(y_eval, np.full(len(y_eval), rate))


def reliability_curve(y_true, p, n_bins: int = 10) -> list:
    """Observed frequency vs mean predicted probability per bin, using
    quantile-sized bins (deciles by default) rather than fixed probability
    ranges -- with a modest held-out sample, fixed ranges leave most bins
    empty. Bin count is reduced automatically (via qcut's duplicates="drop")
    when there aren't enough distinct predicted values to support it.
    Returns a list of dicts in ascending predicted-probability order, each
    carrying its own sample count so a report can flag thin bins.
    """
    y_true = np.asarray(y_true, dtype=float)
    p = np.asarray(p, dtype=float)
    n = len(p)
    if n == 0:
        return []
    n_bins = max(1, min(n_bins, len(np.unique(p))))
    try:
        bins = pd.qcut(p, n_bins, labels=False, duplicates="drop")
    except ValueError:
        bins = np.zeros(n, dtype=int)
    out = []
    for b in sorted(pd.unique(bins)):
        mask = bins == b
        out.append({
            "bin": int(b), "n": int(mask.sum()),
            "mean_predicted": float(p[mask].mean()),
            "observed_freq": float(y_true[mask].mean()),
        })
    return out


def is_monotonic(curve: list, tolerance: float = 0.0) -> bool:
    """Whether observed_freq is non-decreasing across bins in ascending
    predicted-probability order -- the brief's reliability requirement.
    `tolerance` lets small noise-level dips still count as monotonic."""
    freqs = [b["observed_freq"] for b in curve]
    return all(freqs[i + 1] >= freqs[i] - tolerance for i in range(len(freqs) - 1))
