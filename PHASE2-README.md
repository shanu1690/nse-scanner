# Phase 2 — foundations layer

Three modules and 37 passing tests. Drop `nse/`, `tests/` and `config/` into your
repo root (merge, don't overwrite your existing `nse/`).

```bash
pip install pytest
python -m pytest tests/ -q      # 37 passed
```

## What's here

| Module | Purpose |
|---|---|
| `nse/pit/store.py` | Point-in-time store. As-of queries, restatement handling, strict publication-date visibility. |
| `nse/calendar/market_calendar.py` | Trading days, session hours, session-counted horizons, decision-point times. |
| `nse/quality/corporate_actions.py` | Split/bonus/dividend adjustment and unadjusted-action detection. |
| `nse/quality/validators.py` | Schema, duplicate, coverage, staleness, sanity and continuity checks with a publish veto. |

## One deliberate piece of friction

`config/holidays/2026.json` ships with `verified: false` and an empty holiday
list, so **the calendar will raise until you populate it** from the official NSE
circular and flip the flag.

This is on purpose. A calendar that quietly assumes Mon–Fri shifts forward returns
onto the wrong days across every holiday week, and the resulting backtest error is
small, systematic and nearly impossible to spot later. Failing loudly on day one
costs ten minutes. Add `cal.verify_year(<year>)` to CI so a missing next-year
calendar breaks the build in December rather than in January.

## Wiring into the existing pipeline

1. **Before scoring:** run `detect_unadjusted()` on every price series. Anything
   flagged with `suspicious_ratio` that the events pipeline can't explain should
   halt the run for that symbol, not be scored around.
2. **In the backtest loop:** fetch fundamentals through
   `PointInTimeStore.latest_as_of(symbols, fields, decision_time)`. Never read a
   fundamentals frame directly.
3. **Before publishing:** run `DataValidator.validate()` and gate the publish step
   on `report.may_publish`. On failure, leave the previous bundle live and alert.
4. **Before writing the bundle:** run `scan_bundle_for_credentials()` over the
   serialised JSON. Non-empty means abort.
5. **Horizons:** replace calendar-day arithmetic in the backtest with
   `sessions_between()` / `shift_sessions()`.

## The tests worth reading

`tests/test_pit_store.py::test_period_end_keying_would_have_leaked` documents the
bug the store exists to prevent: keyed to fiscal period end, a Q3 value looks
available on 31 Dec; keyed to publication, it isn't available until 10 Feb. That's
41 days of hindsight, applied to every fundamental factor in every backtest.

`test_restatement_returns_the_value_the_market_saw` covers the subtler version —
a February restatement must not retroactively change what January saw.
