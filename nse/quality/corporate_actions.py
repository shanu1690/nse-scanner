"""Corporate action adjustment and unadjusted-action detection.

An unadjusted 1:2 split fabricates a -50% single-bar move. Every momentum,
breakout and volatility feature in the repo will read that as a real event, and
the fade scorer will read it as the strongest mean-reversion signal it has ever
seen. This module exists to make that impossible.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import numpy as np
import pandas as pd

__all__ = ["CorporateAction", "adjustment_series", "adjust_ohlcv", "detect_unadjusted"]


@dataclass(frozen=True)
class CorporateAction:
    """``ex_date`` is the first session trading at the new price.

    ``ratio`` is the price multiplier applied to history BEFORE the ex-date:
      - 1:2 split  -> ratio 0.5
      - 1:1 bonus  -> ratio 0.5
      - dividend   -> ratio (close - dividend) / close, computed by the caller
    """

    symbol: str
    ex_date: date
    action_type: str
    ratio: float

    def __post_init__(self) -> None:
        if not 0 < self.ratio <= 10:
            raise ValueError(
                f"{self.symbol}: implausible adjustment ratio {self.ratio} "
                f"for {self.action_type} on {self.ex_date}"
            )


def adjustment_series(index: pd.DatetimeIndex, actions: list[CorporateAction]) -> pd.Series:
    """Cumulative back-adjustment factor per bar.

    Bars on or after an ex-date get factor 1.0; earlier bars are scaled by the
    product of all subsequent action ratios. Applied to prices only, never to
    forward returns computed after adjustment.
    """
    factor = pd.Series(1.0, index=index)
    for action in sorted(actions, key=lambda a: a.ex_date):
        ex = pd.Timestamp(action.ex_date)
        if index.tz is not None:
            ex = ex.tz_localize(index.tz) if ex.tz is None else ex.tz_convert(index.tz)
        factor.loc[index < ex] *= action.ratio
    return factor


def adjust_ohlcv(df: pd.DataFrame, actions: list[CorporateAction]) -> pd.DataFrame:
    """Return a split/bonus/dividend adjusted copy of an OHLCV frame.

    Volume is scaled inversely so that price x volume stays consistent across
    the action, which keeps volume z-scores from spiking on a split.
    """
    if not actions:
        return df.copy()
    out = df.copy()
    factor = adjustment_series(out.index, actions)
    for col in ("open", "high", "low", "close"):
        if col in out.columns:
            out[col] = out[col] * factor
    if "volume" in out.columns:
        out["volume"] = out["volume"] / factor
    out.attrs["adjusted_for"] = [
        (a.action_type, a.ex_date.isoformat(), a.ratio) for a in actions
    ]
    return out


def detect_unadjusted(
    df: pd.DataFrame,
    *,
    threshold: float = 0.20,
    known_actions: list[CorporateAction] | None = None,
) -> pd.DataFrame:
    """Flag single-bar moves large enough to suggest an unhandled corporate action.

    This is a safety net, not a classifier. It cannot tell a real 25% crash from
    an unadjusted 1:1.33 bonus, and it is not supposed to: it raises the bar for
    a human or the events pipeline to explain. Bars matching a known action are
    excluded, so what remains is precisely the set nobody has accounted for.

    Ratios near common action multiples (0.5, 0.333, 0.25, 0.2) are marked
    ``suspicious_ratio``, which is the strong signal.
    """
    if "close" not in df.columns or len(df) < 2:
        return df.iloc[0:0].assign(gap=[], suspicious_ratio=[])

    ratio = df["close"] / df["close"].shift(1)
    gap = ratio - 1.0
    flagged = df[gap.abs() > threshold].copy()
    flagged["gap"] = gap[gap.abs() > threshold]
    flagged["ratio"] = ratio[gap.abs() > threshold]

    common = np.array([0.5, 1 / 3, 0.25, 0.2, 2.0, 3.0, 4.0, 5.0])
    flagged["suspicious_ratio"] = [
        bool(np.any(np.abs(common - r) < 0.02)) for r in flagged["ratio"]
    ]

    if known_actions:
        known = set()
        for a in known_actions:
            ts = pd.Timestamp(a.ex_date)
            if flagged.index.tz is not None:
                ts = ts.tz_localize(flagged.index.tz) if ts.tz is None else ts
            known.add(ts.normalize())
        flagged = flagged[
            ~pd.Index([i.normalize() for i in flagged.index]).isin(known)
        ]

    return flagged
