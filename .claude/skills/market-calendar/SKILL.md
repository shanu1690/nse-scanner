---
name: market-calendar
description: NSE trading calendar handling - sessions, holidays, expiry schedule and the timing of every scheduled job.
---

# Market calendar

All times IST. Cron entries below are UTC (IST = UTC+5:30).

## Sessions

- Pre-open auction: 09:00–09:15
- Normal trading: 09:15–15:30
- Closing session: 15:40–16:00

Never assume Mon–Fri means open. Check the NSE holiday list, and handle special
sessions (muhurat trading, occasional Saturday mock/live sessions).

## Job schedule

| Job | IST | Cron (UTC) | Purpose |
|---|---|---|---|
| Pre-open prep | 08:45 | `15 3 * * 1-5` | fundamentals, news, events, overnight gaps, candidate list |
| Pre-open finalise | 09:05 | `35 3 * * 1-5` | fold in pre-open auction data, publish plan before 09:15 |
| Intraday refresh | 09:45 / 11:30 / 14:30 | separate entries | recalculate at decision points only |
| EOD | 18:30 | `0 13 * * 1-5` | nightly scan, journal, scorecard |

## Scheduling caveat

GitHub Actions cron is best-effort and can be delayed 5–15 minutes under load.
That is unacceptable for the 09:05 deadline. Once the backend service exists,
schedule there instead and keep Actions for the EOD batch only.

## Decision points, not continuous calls

New calls are generated only at the scheduled decision points. Between them, the
system emits invalidation alerts on open ideas (stop breached, target hit, thesis
broken) and nothing else. Continuous re-issuing produces overtrading.

## Expiry handling

Weekly expiries are restricted to one per exchange under current SEBI rules.
Derive the expiry schedule from the scrip master rather than assuming a weekday,
and handle expiry-day and holiday-shifted expiries explicitly.
