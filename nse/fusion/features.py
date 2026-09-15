"""Feature assembly for the fusion model (Phase 6).

Combines three legs, honestly, given the current state of each pipeline:
  - technical:   nse/backtest.py's Signal.subscores -- the same five
                 momentum.py sub-factors (trend/breakout/momentum/volume/
                 relative_strength) already validated in Phase 3. Full
                 coverage, point-in-time safe by construction.
  - regime:      nse/regime/classify.py's per-date snapshot (VIX level/
                 percentile/trend, breadth, a NIFTY trend flag). Full
                 coverage for any date the regime history spans.
  - fundamental: nse/fundamentals/factors.py's per-(symbol, as-of) factors.
                 PARTIAL coverage -- as of Phase 5, ~25/210 universe symbols
                 have any BSE XBRL data ingested at all. Missing values are
                 imputed with the TRAINING split's median plus a companion
                 `<name>_missing` indicator column (see impute_nullable) --
                 a bare 0 would look like a real, specific factor value to
                 a linear model instead of "unknown".

News/events are deliberately NOT included: nse/news/ stores headlines and
summaries only, with no numeric sentiment/materiality feature ever built
(cut from Phase 4's scope). Fabricating one under Phase 6 time pressure
would be exactly what PROJECT_BRIEF.md warns against -- an honestly empty
leg beats a manufactured one.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

import numpy as np
import pandas as pd

from .. import backtest as bt
from ..fundamentals import factors as fac

TECHNICAL_FEATURES = tuple(bt.FACTORS)
REGIME_FEATURES = ("vix_level", "vix_percentile", "vix_trend_5d",
                    "pct_above_50dma", "advance_decline_ratio", "nifty_trend_flag")
FUNDAMENTAL_FEATURES = tuple(fac.FACTOR_NAMES)
# Columns allowed to be NaN going in -- everything except the always-numeric
# technical subscores. impute_nullable() fills exactly these.
NULLABLE_FEATURES = REGIME_FEATURES + FUNDAMENTAL_FEATURES
ALL_FEATURES = TECHNICAL_FEATURES + NULLABLE_FEATURES

__all__ = [
    "TECHNICAL_FEATURES", "REGIME_FEATURES", "FUNDAMENTAL_FEATURES",
    "NULLABLE_FEATURES", "ALL_FEATURES",
    "assemble_feature_row", "build_feature_frame", "impute_nullable",
]


def _as_of_instant(date: pd.Timestamp) -> datetime:
    """A naive daily price-bar date -> the tz-aware instant the fundamentals
    store's as_of() needs. Treated as end-of-trading-day UTC: everything a
    filing/news item published up through that day could have influenced
    the decision made at that day's close, matching how Signal.date is
    itself used elsewhere (the decision bar's own close)."""
    d = date.to_pydatetime()
    return datetime(d.year, d.month, d.day, 23, 59, 59, tzinfo=timezone.utc)


def _nifty_trend_flag(regime_row) -> float:
    close = regime_row.get("nifty_close")
    ema21 = regime_row.get("nifty_ema21")
    ema50 = regime_row.get("nifty_ema50")
    if close is None or ema21 is None or ema50 is None or any(
            pd.isna(x) for x in (close, ema21, ema50)):
        return np.nan
    if close > ema21 > ema50:
        return 1.0
    if close < ema21 < ema50:
        return -1.0
    return 0.0


def assemble_feature_row(signal, regime_row: "pd.Series | None" = None,
                          fundamentals: "dict | None" = None) -> dict:
    """One row of RAW (possibly-NaN) feature values for a single
    backtest.Signal. Imputation and missing-flags are a frame-level,
    train-only-statistics step (impute_nullable) -- not done here, so a
    single row never bakes in any particular split's statistics."""
    row = {name: float(signal.subscores.get(name, 0.0)) for name in TECHNICAL_FEATURES}

    if regime_row is not None:
        for name in ("vix_level", "vix_percentile", "vix_trend_5d",
                     "pct_above_50dma", "advance_decline_ratio"):
            val = regime_row.get(name)
            row[name] = float(val) if val is not None and not pd.isna(val) else np.nan
        row["nifty_trend_flag"] = _nifty_trend_flag(regime_row)
    else:
        for name in REGIME_FEATURES:
            row[name] = np.nan

    fundamentals = fundamentals or {}
    for name in FUNDAMENTAL_FEATURES:
        val = fundamentals.get(name)
        row[name] = float(val) if val is not None else np.nan

    return row


def build_feature_frame(signals, regime_history: pd.DataFrame, *,
                         store=None, price_frames: "Optional[dict]" = None,
                         horizon: int = bt.FIXED_TARGET_HORIZON,
                         target_pct: float = bt.FIXED_TARGET_PCT):
    """Assemble (X, y, meta) from a list of backtest.Signal objects.

    A row whose forward-return label is unavailable (the signal is too
    close to the end of the cached history to know the outcome yet) is
    DROPPED, never coerced to a 0/negative label -- an unknown outcome is
    not the same thing as a loss.

    `store`/`price_frames` are optional: pass None for either to skip the
    fundamental leg entirely (every fundamental column comes back NaN,
    which impute_nullable() then flags as missing like any other gap).
    """
    rows, labels, meta_rows = [], [], []
    for s in signals:
        fwd = s.fwd_pct.get(horizon)
        if fwd is None:
            continue

        regime_row = (regime_history.loc[s.date]
                      if s.date in regime_history.index else None)

        fundamentals = None
        if store is not None:
            price = None
            if price_frames is not None:
                pf = price_frames.get(s.symbol)
                if pf is not None and s.date in pf.index:
                    price = float(pf.loc[s.date, "Close"])
            fundamentals = fac.all_factors(store, s.symbol, _as_of_instant(s.date), price=price)

        rows.append(assemble_feature_row(s, regime_row, fundamentals))
        labels.append(1 if fwd >= target_pct else 0)
        meta_rows.append({"symbol": s.symbol, "date": s.date, "block": s.block})

    X = pd.DataFrame(rows, columns=list(ALL_FEATURES))
    y = np.asarray(labels, dtype=int)
    meta = pd.DataFrame(meta_rows, columns=["symbol", "date", "block"])
    return X, y, meta


def impute_nullable(X_train: pd.DataFrame, *others: pd.DataFrame):
    """Fill NULLABLE_FEATURES columns using medians computed from X_TRAIN
    ONLY, applied identically to X_train and every frame in `others`
    (calibration/held-out splits) -- computing a fill value from a split
    that shouldn't be looked at yet would itself be a leak. Adds a
    `<name>_missing` companion column (1.0 = was missing) for every
    nullable feature, on every frame, so the model can learn "unknown" as
    its own signal rather than having a median value silently pass for a
    real observation.

    Returns (X_train_filled, *others_filled, medians_used).
    """
    medians = {}
    for col in NULLABLE_FEATURES:
        vals = X_train[col].dropna()
        medians[col] = float(vals.median()) if len(vals) else 0.0

    def _fill(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        for col in NULLABLE_FEATURES:
            out[f"{col}_missing"] = out[col].isna().astype(float)
            out[col] = out[col].fillna(medians[col])
        return out

    filled = [_fill(X_train)] + [_fill(df) for df in others]
    return (*filled, medians)
