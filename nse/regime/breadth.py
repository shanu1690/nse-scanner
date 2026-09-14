"""Point-in-time market breadth: % of the universe above its own 50-day
moving average, and the day's advance/decline ratio.

Both are computed with a rolling/expanding-window construction and then
forward-filled onto the target date index -- ffill only ever carries a PAST
value forward, never a future one, so this stays point-in-time safe the same
way nse/backtest.py's `window = full.iloc[:i+1]` truncation is: a value for
date d never depends on any bar dated after d.
"""

from __future__ import annotations

import pandas as pd

MIN_BARS_FOR_50DMA = 50
MIN_SYMBOLS_FOR_BREADTH = 10  # refuse to report breadth off a handful of names


def build_breadth_series(price_frames: dict, dates: pd.DatetimeIndex) -> pd.DataFrame:
    """`price_frames`: {symbol: OHLCV DataFrame}. Returns a DataFrame indexed
    by `dates` with columns `pct_above_50dma` and `advance_decline_ratio`,
    NaN on any date where fewer than MIN_SYMBOLS_FOR_BREADTH symbols have
    enough history to judge -- refusing to report rather than guessing off a
    thin sample, matching nse/backtest.py's sample-size-gate philosophy.
    """
    above_cols, updown_cols = {}, {}
    for sym, df in price_frames.items():
        if df is None or len(df) < 2:
            continue
        close = df["Close"]
        updown_cols[sym] = close.diff().reindex(dates, method="ffill")
        if len(df) < MIN_BARS_FOR_50DMA:
            continue  # too short for a 50DMA opinion, but still counts for A/D
        sma50 = close.rolling(MIN_BARS_FOR_50DMA).mean()
        above = (close > sma50).where(sma50.notna())
        above_cols[sym] = above.reindex(dates, method="ffill")

    if above_cols:
        above_df = pd.DataFrame(above_cols)
        n_valid = above_df.notna().sum(axis=1)
        pct_above = above_df.mean(axis=1, skipna=True).where(n_valid >= MIN_SYMBOLS_FOR_BREADTH)
    else:
        pct_above = pd.Series(index=dates, dtype=float)

    if updown_cols:
        updown_df = pd.DataFrame(updown_cols)
        adv = (updown_df > 0).sum(axis=1)
        dec = (updown_df < 0).sum(axis=1)
        total = adv + dec
        ad_ratio = (adv / total).where(total >= MIN_SYMBOLS_FOR_BREADTH)
    else:
        ad_ratio = pd.Series(index=dates, dtype=float)

    return pd.DataFrame({"pct_above_50dma": pct_above, "advance_decline_ratio": ad_ratio})
