"""Tests for nse/fusion/model.py: fit/calibrate/predict on a synthetic
problem with a genuine, known signal, plus the contribution reporting."""

import numpy as np
import pandas as pd
import pytest

from nse.fusion.model import FusionModel


def _synthetic(n=800, seed=0):
    """y depends strongly on 'trend' and weakly on 'volume', not at all on
    'noise' -- lets tests assert the model actually learns the real signal
    and that contribution reporting flags the right feature."""
    rng = np.random.default_rng(seed)
    trend = rng.normal(0, 1, n)
    volume = rng.normal(0, 1, n)
    noise = rng.normal(0, 1, n)
    logit = 1.5 * trend + 0.3 * volume
    p = 1 / (1 + np.exp(-logit))
    y = (rng.uniform(0, 1, n) < p).astype(int)
    X = pd.DataFrame({"trend": trend, "volume": volume, "noise": noise})
    return X, y


def test_fit_predict_proba_beats_coin_flip_on_held_out():
    X, y = _synthetic(n=1000)
    X_train, y_train = X.iloc[:600], y[:600]
    X_test, y_test = X.iloc[600:800], y[600:800]
    X_calib, y_calib = X.iloc[800:], y[800:]

    model = FusionModel().fit(X_train, y_train)
    model.calibrate(X_calib, y_calib, method="sigmoid")
    p = model.predict_proba(X_test)

    # AUC-style separation check without pulling in sklearn.metrics.roc_auc:
    # mean predicted prob for actual positives should clearly exceed actual
    # negatives on a signal this strong.
    assert p[y_test == 1].mean() > p[y_test == 0].mean() + 0.1


def test_calibrate_before_fit_raises():
    X, y = _synthetic(n=50)
    model = FusionModel()
    with pytest.raises(RuntimeError):
        model.calibrate(X, y)


def test_predict_proba_works_without_calibration():
    X, y = _synthetic(n=200)
    model = FusionModel().fit(X, y)
    p = model.predict_proba(X)
    assert ((p >= 0) & (p <= 1)).all()


def test_base_predict_proba_unaffected_by_calibration_shape():
    X, y = _synthetic(n=600)
    model = FusionModel().fit(X.iloc[:400], y[:400])
    before = model.base_predict_proba(X.iloc[400:])
    model.calibrate(X.iloc[400:500], y[400:500])
    after = model.base_predict_proba(X.iloc[400:])
    np.testing.assert_allclose(before, after)  # calibration doesn't touch the base model


def test_top_contributions_flags_the_dominant_real_feature():
    X, y = _synthetic(n=1000, seed=3)
    model = FusionModel().fit(X, y)
    # A row with a strongly positive 'trend' and near-zero everything else
    row = pd.Series({"trend": 3.0, "volume": 0.0, "noise": 0.0})
    top = model.top_contributions(row, n=2)
    assert top[0]["feature"] == "trend"
    assert top[0]["direction"] == "up"


def test_top_contributions_unknown_feature_falls_back_to_raw_name():
    X, y = _synthetic(n=200)
    model = FusionModel().fit(X, y)
    row = X.iloc[0]
    top = model.top_contributions(row, n=3)
    names = {c["feature"] for c in top}
    assert names <= {"trend", "volume", "noise"}
    for c in top:
        assert isinstance(c["label"], str) and c["label"]
