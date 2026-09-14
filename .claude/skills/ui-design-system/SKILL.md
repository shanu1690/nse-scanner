---
name: ui-design-system
description: Dashboard visual and interaction conventions - colour, typography, number formatting, chart style, states and accessibility.
---

# UI design system

## Principles

1. **Honesty over polish.** Stale data looks stale. Uncertainty is shown, not
   smoothed away. A confident-looking dashboard on broken data is the failure mode
   this project is most exposed to.
2. **Evidence on demand.** Every pick expands to show why: contributing factors,
   headlines with links, upcoming events, fundamental snapshot.
3. **Beginner-legible.** Plain English on the default view; jargon behind toggles.

## Colour

Semantic, not decorative. Green/red for direction, but never as the only signal —
pair with sign and arrow for colour-blind users. Reserve a distinct alert colour
for degraded/stale states so it cannot be confused with a price move.

Dark mode is the default. Ensure WCAG AA contrast in both themes.

## Typography and numbers

- Tabular figures for all numeric columns so digits align.
- Indian numbering (lakh/crore) for rupee amounts; explicit `Rs` prefix.
- Consistent decimal places per column. Percentages to 1dp, prices to 2dp.
- Probabilities as percentages with their uncertainty, never bare.
- Every timestamp shows IST and a relative age ("2m ago").

## Charts

`lightweight-charts` for price. Candlesticks, EMA20/50/200, Donchian, volume
histogram. Entry/stop/target as labelled horizontal levels. Timeframe switcher.
Live last-price overlay carries an age badge. Options payoff diagram marks
breakeven and shades max loss.

## States — all four required for every data view

- **Loading**: skeleton rows, never a spinner over stale content.
- **Empty**: explains why it is empty ("no qualifying option trade today — 14
  candidates rejected"), never a blank panel.
- **Degraded**: feed disconnected or data stale, visibly marked, with last-good
  timestamp.
- **Error**: what failed and what the user can do.

## Interaction

Mobile-first. Keyboard navigable with visible focus rings. Filter state in the URL
so views are shareable. Filtering stays client-side and instant; virtualise past a
few hundred rows.
