from datetime import date

import numpy as np
import pandas as pd

from nse.quality.corporate_actions import (
    CorporateAction, adjust_ohlcv, adjustment_series, detect_unadjusted,
)


def frame(closes, start="2026-01-01"):
    idx = pd.date_range(start, periods=len(closes), freq="D")
    return pd.DataFrame(
        {"open": closes, "high": [c * 1.01 for c in closes],
         "low": [c * 0.99 for c in closes], "close": closes,
         "volume": [1000] * len(closes)},
        index=idx,
    )


def test_split_removes_the_fake_gap():
    """The whole point: an unadjusted 1:2 split looks like a -50% crash."""
    df = frame([100, 100, 100, 50, 50])
    raw_move = df["close"].pct_change().min()
    assert raw_move < -0.45

    action = CorporateAction("X", date(2026, 1, 4), "split", 0.5)
    adjusted = adjust_ohlcv(df, [action])
    assert abs(adjusted["close"].pct_change().min()) < 1e-9
    assert adjusted["close"].iloc[0] == 50.0


def test_volume_scales_inversely_so_z_scores_do_not_spike():
    df = frame([100, 100, 50, 50])
    adjusted = adjust_ohlcv(df, [CorporateAction("X", date(2026, 1, 3), "split", 0.5)])
    assert adjusted["volume"].iloc[0] == 2000
    assert adjusted["volume"].iloc[-1] == 1000


def test_multiple_actions_compound():
    idx = pd.date_range("2026-01-01", periods=6, freq="D")
    factor = adjustment_series(idx, [
        CorporateAction("X", date(2026, 1, 3), "split", 0.5),
        CorporateAction("X", date(2026, 1, 5), "bonus", 0.5),
    ])
    assert factor.iloc[0] == 0.25
    assert factor.iloc[2] == 0.5
    assert factor.iloc[4] == 1.0


def test_detects_unadjusted_split_and_marks_the_ratio():
    df = frame([100, 100, 50, 50])
    flagged = detect_unadjusted(df)
    assert len(flagged) == 1
    assert bool(flagged["suspicious_ratio"].iloc[0]) is True


def test_known_actions_are_not_flagged():
    df = frame([100, 100, 50, 50])
    action = CorporateAction("X", date(2026, 1, 3), "split", 0.5)
    assert detect_unadjusted(df, known_actions=[action]).empty


def test_ordinary_volatility_is_not_flagged():
    df = frame([100, 103, 99, 101, 104])
    assert detect_unadjusted(df).empty


def test_adjusted_series_produces_no_spurious_flags():
    df = frame([100, 100, 50, 50])
    action = CorporateAction("X", date(2026, 1, 3), "split", 0.5)
    assert detect_unadjusted(adjust_ohlcv(df, [action])).empty
