---
name: options-budget-rules
description: The Rs 10,000 options budget cap and the beginner-safety rules that follow from it. Apply anywhere option ideas are generated, filtered, ranked or displayed.
---

# Options budget rules

User is a **beginner** at options. Budget cap is **Rs 10,000 total entry cost per
idea**, computed as `lot_size x premium x lots` plus estimated charges.

## Lot sizes

Read from the daily scrip master refresh. **Never hardcode.** NSE revises lot sizes
roughly every six months, and SEBI's minimum contract value rules (index derivative
contracts at roughly Rs 15 lakh notional) push them upward over time.

## The honest consequence

At current contract values, one lot of an at-the-money option typically costs well
above Rs 10,000. The cap therefore biases the eligible set toward cheap OTM and
near-expiry contracts, which are the lowest-probability and fastest-decaying
instruments available. The budget limits rupee loss while raising loss frequency.

Design against this rather than hiding it.

## Rules

1. **Filter, do not force.** No qualifying idea means output "no qualifying option
   trade today", with a count of rejected candidates and the reasons. An empty list
   is a correct answer, not a gap to fill.
2. **Prefer debit spreads.** Defined risk, materially cheaper entry, often fits the
   budget where a single ATM leg does not. Rank above naked longs when both qualify.
3. **Rank by probability, never by premium.** Ascending-cost sorting surfaces the
   worst contracts first.
4. **Block outright:** same-day expiry; open interest below the liquidity floor;
   bid-ask spread consuming a large share of premium; any structure requiring
   option writing.
5. **Never sell or write options.** Undefined risk and margin far above budget.
6. **Beginner display.** Plain English only on the default view: total cost, "you
   can lose 100% of this", breakeven price, what must happen and by when, days to
   expiry, payoff diagram. Greeks behind a toggle.
7. **Paper-trade gate.** Every idea is journalled with a simulated fill, and the
   running scorecard sits next to the picks — good or bad.

## Design note worth revisiting

The Rs 10,000 figure is doing double duty as a budget limit and a risk control. It
is better at the first than the second. A cap defined as a percentage of total
trading capital, combined with a contract-quality floor, would push toward better
contracts rather than merely cheaper ones. Raise this with the user before
finalising the options module.
