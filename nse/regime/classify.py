"""Point-in-time market regime classification.

PROJECT_BRIEF.md Section 4.4 is explicit about the risk here: "regime
detection is itself a prediction problem and adds a large overfitting
surface." So this is a fixed, hand-picked rule over a small number of
well-understood inputs -- not a second model fit to data -- and the
thresholds below are round, defensible numbers (55%/45% breadth, 80th VIX
percentile), not values tuned against any backtest. nse/regime/compare.py is
what's actually responsible for proving (or failing to prove) that switching
on this rule beats the static style choice out-of-sample; this module only
produces the label.

Every input is computed using bars up to and including the row's own date --
see nse/regime/breadth.py's docstring for why forward-filling stays
point-in-time safe.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .. import indicators as ind
from .breadth import build_breadth_series

REGIME_LABELS = ("trending_up", "trending_down", "mean_reverting",
                  "high_vol_shock", "unknown")

VIX_SHOCK_PERCENTILE = 80.0   # VIX in the top quintile of its own trailing history
BREADTH_UP = 0.55             # >=55% of the universe above its 50DMA
BREADTH_DOWN = 0.45           # <=45% of the universe above its 50DMA
VIX_PERCENTILE_MIN_PERIODS = 60  # refuse a percentile off a thin VIX history


def _expanding_percentile(values: np.ndarray) -> float:
    """% of all prior-and-current values <= the current (last) one --
    an expanding, causal percentile rank: the value at index i only ever
    looks at values[0:i+1]."""
    return float((values <= values[-1]).mean() * 100)


def classify_row(row) -> str:
    """The regime label for one already-assembled row of inputs (see
    build_regime_history). Missing essential inputs -> "unknown" rather than
    a guessed label, so downstream consumers can fall back to the static
    style instead of acting on a classification with nothing behind it."""
    vix_pct, vix_trend = row.get("vix_percentile"), row.get("vix_trend_5d")
    if vix_pct is not None and not pd.isna(vix_pct):
        rising_or_unknown = vix_trend is None or pd.isna(vix_trend) or vix_trend > 0
        if vix_pct >= VIX_SHOCK_PERCENTILE and rising_or_unknown:
            return "high_vol_shock"

    close, ema21, ema50 = row.get("nifty_close"), row.get("nifty_ema21"), row.get("nifty_ema50")
    if close is None or ema21 is None or ema50 is None or any(pd.isna(x) for x in (close, ema21, ema50)):
        return "unknown"

    breadth = row.get("pct_above_50dma")
    breadth_ok_up = breadth is None or pd.isna(breadth) or breadth >= BREADTH_UP
    breadth_ok_down = breadth is None or pd.isna(breadth) or breadth <= BREADTH_DOWN

    if close > ema21 > ema50 and breadth_ok_up:
        return "trending_up"
    if close < ema21 < ema50 and breadth_ok_down:
        return "trending_down"
    return "mean_reverting"


def build_regime_history(nifty_df: pd.DataFrame, vix_df: pd.DataFrame,
                          price_frames: dict,
                          dates: "pd.DatetimeIndex | None" = None) -> pd.DataFrame:
    """One row per NIFTY trading day: every input plus the resulting label.
    `dates`, if given, restricts the returned rows to that index (via
    reindex -- never extends beyond what was actually computed)."""
    nifty_full = ind.add_all_indicators(nifty_df)
    vix_close = vix_df["Close"].reindex(nifty_full.index, method="ffill")
    vix_percentile = vix_close.expanding(min_periods=VIX_PERCENTILE_MIN_PERIODS).apply(
        _expanding_percentile, raw=True)
    vix_trend_5d = vix_close.pct_change(5) * 100

    breadth = build_breadth_series(price_frames, nifty_full.index)

    merged = pd.DataFrame({
        "nifty_close": nifty_full["Close"],
        "nifty_ema21": nifty_full["EMA21"],
        "nifty_ema50": nifty_full["EMA50"],
        "vix_level": vix_close,
        "vix_percentile": vix_percentile,
        "vix_trend_5d": vix_trend_5d,
        "pct_above_50dma": breadth["pct_above_50dma"],
        "advance_decline_ratio": breadth["advance_decline_ratio"],
    })
    merged["regime"] = merged.apply(classify_row, axis=1)

    if dates is not None:
        merged = merged.reindex(dates)
    return merged
