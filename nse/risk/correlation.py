"""Pairwise return correlation across candidate picks.

PROJECT_BRIEF.md Section 8 / risk-manager.md: "five picks that are the
same trade in five tickers is one position with five times the risk.
Compute pairwise correlation and cap it."
"""

from __future__ import annotations

from typing import Optional

import pandas as pd

__all__ = ["correlation_matrix"]


def correlation_matrix(symbols: list, price_frames: dict,
                        lookback_days: int) -> "Optional[pd.DataFrame]":
    """Pairwise Pearson correlation of daily returns over the trailing
    `lookback_days` cached bars, across whichever of `symbols` actually
    have enough history -- a symbol with too little history is silently
    excluded from the matrix (not from the caller's candidate list), so
    its correlation simply can't be checked rather than being guessed at.
    None if fewer than 2 symbols end up with enough history to correlate
    at all.
    """
    returns = {}
    for sym in symbols:
        df = price_frames.get(sym) if price_frames else None
        if df is None or len(df) < lookback_days + 1:
            continue
        close = df["Close"].tail(lookback_days + 1)
        returns[sym] = close.pct_change().dropna().reset_index(drop=True)
    if len(returns) < 2:
        return None
    return pd.DataFrame(returns).corr()
