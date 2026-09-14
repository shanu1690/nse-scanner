---
name: indicator-conventions
description: Exact indicator definitions, periods and conventions used across the repo, so scoring, backtesting and the UI never silently diverge.
---

# Indicator conventions

Single source of truth. If you need a different period or formula, **change this
file first**, then change the code. Divergence between the backtest and the live
scorer is silent and expensive.

## Definitions in use

- **EMA**: 20 / 50 / 200, standard exponential smoothing, `alpha = 2/(n+1)`.
- **RSI**: 14, Wilder's smoothing (not simple average).
- **MACD**: 12/26/9, EMA-based.
- **ATR**: 14, Wilder's smoothing. Used for stop distance and position sizing.
- **Bollinger**: 20 period, 2 standard deviations, population sd.
- **Donchian**: 20 period high/low channel.
- **ROC**: stated period, percentage change.
- **Volume z-score**: current volume against a trailing 20-day mean and sd —
  rolling, never full-sample.

## Rules

- **Rolling, not full-sample.** Every normalisation, z-score, rank and percentile
  uses a trailing window. Full-sample statistics leak the future.
- **Warm-up handling.** The first `n` bars of any indicator are NaN, not zero, and
  rows with insufficient history are excluded rather than filled.
- **Adjusted prices.** All indicators run on split/bonus-adjusted series. An
  unadjusted split fabricates a 50% gap that will dominate every momentum score.
- **Decision-bar discipline.** An indicator value for a decision made at the open
  of bar D uses data through D-1 only.

## Adding a new indicator

1. Define it here first, with period and smoothing method.
2. Implement with a unit test against a known-good reference series.
3. Run it through the factor audit before it may enter scoring.
4. Report its correlation with existing features. Above roughly 0.8, it adds
   parameters and no information — say so and drop it.
