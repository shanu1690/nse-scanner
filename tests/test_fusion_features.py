"""Tests for nse/fusion/features.py: feature assembly, the fundamental leg's
point-in-time date handling, label extraction, and train-only imputation."""

from datetime import datetime, timezone

import numpy as np
import pandas as pd
import pytest

from nse.backtest import Signal
from nse.fundamentals import factors as fac
from nse.fusion import features as ft
from nse.pit.store import PITRecord, PointInTimeStore


def _signal(symbol="A", date="2024-06-03", block=0, subscores=None, fwd5=None):
    subscores = subscores or {"trend": 10.0, "breakout": 5.0, "momentum": 3.0,
                               "volume": 1.0, "relative_strength": 2.0}
    fwd = {5: fwd5} if fwd5 is not None else {}
    return Signal(symbol=symbol, date=pd.Timestamp(date), block=block, score=21.0,
                  subscores=subscores, fwd_pct=fwd, trade=None)


def _regime_row(**overrides):
    row = {"nifty_close": 110.0, "nifty_ema21": 105.0, "nifty_ema50": 100.0,
           "vix_level": 13.0, "vix_percentile": 40.0, "vix_trend_5d": 0.5,
           "pct_above_50dma": 0.55, "advance_decline_ratio": 0.6}
    row.update(overrides)
    return pd.Series(row)


# -------------------------------------------------------------- feature row
def test_assemble_feature_row_technical_always_present():
    row = ft.assemble_feature_row(_signal(), regime_row=None, fundamentals=None)
    for name in ft.TECHNICAL_FEATURES:
        assert row[name] == pytest.approx(_signal().subscores[name])


def test_assemble_feature_row_regime_none_is_all_nan():
    row = ft.assemble_feature_row(_signal(), regime_row=None)
    for name in ft.REGIME_FEATURES:
        assert np.isnan(row[name])


def test_assemble_feature_row_nifty_trend_flag_up_and_down():
    up = ft.assemble_feature_row(_signal(), regime_row=_regime_row())
    assert up["nifty_trend_flag"] == 1.0
    down = ft.assemble_feature_row(
        _signal(), regime_row=_regime_row(nifty_close=90, nifty_ema21=95, nifty_ema50=100))
    assert down["nifty_trend_flag"] == -1.0


def test_assemble_feature_row_fundamentals_missing_when_none():
    row = ft.assemble_feature_row(_signal(), regime_row=_regime_row(), fundamentals=None)
    for name in ft.FUNDAMENTAL_FEATURES:
        assert np.isnan(row[name])


def test_assemble_feature_row_fundamentals_present():
    row = ft.assemble_feature_row(_signal(), regime_row=_regime_row(),
                                   fundamentals={"net_margin": 0.12})
    assert row["net_margin"] == pytest.approx(0.12)
    assert np.isnan(row["pe_ttm"])  # not supplied


# ---------------------------------------------------------------- frame build
def _regime_history(dates):
    return pd.DataFrame({
        "nifty_close": 110.0, "nifty_ema21": 105.0, "nifty_ema50": 100.0,
        "vix_level": 13.0, "vix_percentile": 40.0, "vix_trend_5d": 0.5,
        "pct_above_50dma": 0.55, "advance_decline_ratio": 0.6,
    }, index=dates)


def test_build_feature_frame_drops_rows_without_a_label():
    signals = [_signal(fwd5=4.0), _signal(date="2024-06-04", fwd5=None)]
    hist = _regime_history(pd.DatetimeIndex([pd.Timestamp("2024-06-03"), pd.Timestamp("2024-06-04")]))
    X, y, meta = ft.build_feature_frame(signals, hist)
    assert len(X) == 1  # the fwd5=None row is dropped, not labeled 0
    assert y[0] == 1


def test_build_feature_frame_labels_by_target_threshold():
    signals = [_signal(fwd5=3.5), _signal(date="2024-06-04", fwd5=1.0)]
    hist = _regime_history(pd.DatetimeIndex([pd.Timestamp("2024-06-03"), pd.Timestamp("2024-06-04")]))
    X, y, meta = ft.build_feature_frame(signals, hist, target_pct=3.0)
    assert list(y) == [1, 0]
    assert list(meta["symbol"]) == ["A", "A"]


def test_build_feature_frame_pulls_fundamentals_point_in_time():
    with PointInTimeStore() as store:
        store.put([
            PITRecord(symbol="A", field="net_profit", value=100,
                      period_end=datetime(2024, 3, 31, tzinfo=timezone.utc),
                      published_at=datetime(2024, 4, 20, tzinfo=timezone.utc), source="test"),
            PITRecord(symbol="A", field="revenue_from_operations", value=1000,
                      period_end=datetime(2024, 3, 31, tzinfo=timezone.utc),
                      published_at=datetime(2024, 4, 20, tzinfo=timezone.utc), source="test"),
        ])
        hist = _regime_history(pd.DatetimeIndex([pd.Timestamp("2024-04-19"), pd.Timestamp("2024-04-21")]))
        signals = [_signal(date="2024-04-19", fwd5=4.0), _signal(date="2024-04-21", fwd5=4.0)]
        X, y, meta = ft.build_feature_frame(signals, hist, store=store)
    # Before the filing's published_at: not visible yet
    assert np.isnan(X.loc[0, "net_margin"])
    # After: visible
    assert X.loc[1, "net_margin"] == pytest.approx(0.1)


# -------------------------------------------------------------------- impute
def test_impute_nullable_uses_train_median_only():
    X_train = pd.DataFrame({"vix_level": [10.0, np.nan, 20.0], **{f: [0.0] * 3 for f in ft.NULLABLE_FEATURES if f != "vix_level"}})
    X_test = pd.DataFrame({"vix_level": [np.nan], **{f: [0.0] for f in ft.NULLABLE_FEATURES if f != "vix_level"}})
    X_train_f, X_test_f, medians = ft.impute_nullable(X_train, X_test)
    assert medians["vix_level"] == pytest.approx(15.0)  # median of [10, 20], NaN excluded
    assert X_train_f.loc[1, "vix_level"] == pytest.approx(15.0)
    assert X_test_f.loc[0, "vix_level"] == pytest.approx(15.0)  # test uses TRAIN's median
    assert X_train_f.loc[1, "vix_level_missing"] == 1.0
    assert X_train_f.loc[0, "vix_level_missing"] == 0.0
    assert X_test_f.loc[0, "vix_level_missing"] == 1.0


def test_impute_nullable_all_missing_falls_back_to_zero():
    X_train = pd.DataFrame({f: [np.nan, np.nan] for f in ft.NULLABLE_FEATURES})
    X_train_f, medians = ft.impute_nullable(X_train)
    assert medians["pe_ttm"] == 0.0
    assert (X_train_f["pe_ttm"] == 0.0).all()
    assert (X_train_f["pe_ttm_missing"] == 1.0).all()
