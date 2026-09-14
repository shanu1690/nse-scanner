---
name: point-in-time-protocol
description: The rules that prevent look-ahead bias. Apply to every feature, label, filter and regime label before it enters scoring or backtesting.
---

# Point-in-time protocol

One rule: a feature computed for decision bar D may use only information whose
publication timestamp is strictly earlier than D.

## Checklist — every new feature must pass all nine

1. **Timestamp exists.** The source record carries `published_at`, not just
   `fetched_at`. If the source gives no publication time, you must derive a
   conservative one and document the assumption.
2. **Publication, not period.** Fundamentals key to result publication date, never
   fiscal period end. This is the single most common leak in equity backtests.
3. **No same-bar close.** A feature for a decision made at the open of D cannot use
   D's close. Be explicit about which bar the decision is made on.
4. **Restatements preserved.** Revised figures are stored as new rows. The backtest
   sees the original value the market saw, not the corrected one.
5. **Universe is as-of.** The symbol list for date D is the list as it existed on D,
   including companies later delisted. A current-constituents list is survivorship bias.
6. **Corporate actions adjusted, not retro-leaked.** Prices are split-adjusted, but
   the adjustment must not reveal a future action before its announcement.
7. **Regime labels are causal.** A regime computed from a window that includes future
   bars is a leak wearing a disguise.
8. **Normalisation is rolling.** Z-scores, ranks and percentiles use a trailing
   window, never the full-sample mean and standard deviation.
9. **Test written.** A unit test asserts that querying as-of date D returns no record
   with `published_at >= D`. Not a comment. A test.

## Red flags in review

- Any `.shift(-n)`, negative index, or `iloc[i+1]` in a feature path.
- `df.mean()` / `df.std()` over the whole frame instead of `.rolling()`.
- Joining on period end rather than publication date.
- Filtering the universe with a condition evaluated on present-day data.
- A sudden, large improvement in backtest results after a "small refactor".

## When results look great

Assume a leak. Run the label-shuffle test first: randomise the target, refit, and
confirm the edge disappears. If it does not, stop and find the leak before doing
anything else.
