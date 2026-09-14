---
name: backtest-protocol
description: How to run and report a backtest in this repo - walk-forward setup, matched baselines, confidence intervals and the required reporting template.
---

# Backtest protocol

## Setup

- **Walk-forward only.** Fit on a trailing window, test on the next unseen block,
  roll forward. No single train/test split, no k-fold on time series.
- **Costs always on.** 0.15% round trip minimum, plus a slippage assumption stated
  explicitly. Report results with costs doubled as a robustness check.
- **Matched baseline.** The comparison must share universe, period, holding period
  and target threshold. An unmatched baseline flatters by construction.
- **Fixed target definition.** Default: P(+3% within 5 sessions). If you change it,
  change it everywhere and say so.

## Reporting template — never report a number without all of this

```
Metric:        <hit rate / expectancy / profit factor>
Value:         <number>
Baseline:      <matched baseline value>
Lift:          <difference, with 95% bootstrap CI>
Sample size:   <n signals, n distinct symbols, n distinct months>
Window:        <start date to end date>
Universe:      <definition, as-of construction confirmed>
Holding:       <bars, and exit rule>
Threshold:     <score or probability cutoff>
Costs:         <assumption>
Parameters:    <count of tuned knobs>
```

## Required checks before any result is believed

- Bootstrap CI on the lift excludes zero.
- Result survives excluding the top 5 contributing symbols.
- Result survives excluding the single best month.
- Result holds in a structurally different market period.
- Parameter count is small relative to independent observations. State the ratio.

## Calibration is the real target

The system outputs a probability, so measure it as one: Brier score against a
base-rate-always model, and a reliability curve by decile that should be
monotonic. "When it says 65%, it happens about 65% of the time" is the goal.
Accuracy alone is not.

## Reference point

The existing factor audit produced fade@score>=65 giving a 22.0% hit rate for
5d/+3% against an 18.9% baseline. A +3.1pp lift is a real, honest edge and a
reasonable bar to beat. Any result above roughly 60% hit rate on this target is
presumptively broken until adversarially tested.
