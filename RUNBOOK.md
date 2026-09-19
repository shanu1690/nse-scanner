# RUNBOOK

One-page operational reference for nse-scanner. For the *design* rationale
behind any of this, see `PROJECT_BRIEF.md` — this file is about what to do
when something needs attention, not why it was built this way.

**This is decision support, not an execution engine.** Nothing here places
orders. See `DISCLAIMER.md`.

## What runs, and when

| Job | Schedule | What it does |
|---|---|---|
| `.github/workflows/nightly.yml` | 13:00 UTC (18:30 IST) weekdays, or manual `workflow_dispatch` | Refreshes prices, runs the scanner, builds `site/data/*.json`, runs the risk gate, verifies the bundle, deploys the dashboard to GitHub Pages, emails the day's report, commits `data/latest_bundle/*.json` for intraday.yml |
| `.github/workflows/intraday.yml` | every ~15 min, 9:15–15:30 IST weekdays (best-effort; see below) | Invalidation alerts on open positions (always) + new-call admission near a decision point (Section 4.4) — pushed via ntfy. No-ops instantly outside market hours/holidays. |
| `.github/workflows/ci.yml` | every push/PR to `main` | Runs `pytest` and the frontend build — no deploy |
| `.github/workflows/secret-scan.yml` | every push/PR | Fails the build if a secret-shaped string appears in the diff |

**Public dashboard.** The repo is public (as of 2026-09-19), and `nightly.yml`
deploys the built `frontend/` + `site/data/*.json` to GitHub Pages every
night after the scan — `Settings → Pages` source is "GitHub Actions". The
live URL is `https://shanu1690.github.io/nse-scanner/`. (It was briefly
private-only and email-only for a stretch — Pages needs a public repo or a
paid plan, and a nightly Pages-deploy step failed with 404 "create
deployment" the moment the repo went private; the retired
email-report-only workflow is in this file's git history if the repo ever
goes private again.) The nightly email (below) is unaffected either way —
run it locally against the current data any time:
```bash
nse-scan site --out site --refresh --max-seconds 600   # data bundle
cd frontend && npm run build                            # -> ../site
python3 -m http.server 8000 --directory ../site         # serve it locally
```

The dashboard and email report both show **no live feed** — Section 4's
originally-specified always-on Render backend + SmartWebSocketV2 stream
was never built (out of scope for how far this project got). What DOES
run live is `intraday.yml` below: a free, cron-based approximation —
REST polling every ~15 minutes, not a persistent tick stream, and Actions
cron is best-effort (can drift several minutes, per Section 4.3's own
warning). Good enough to know within ~15 minutes that a stop was hit; not
a substitute for watching a live tape.

## Intraday monitoring (during market hours)

`nse-scan intraday-check` (run by `intraday.yml` roughly every 15 minutes,
9:15–15:30 IST weekdays) does two things, following Rule 8 — "do not
generate new calls continuously; new calls only at defined decision
points, between them only invalidation alerts":

1. **Invalidation check (every run):** fresh LTP for every open journaled
   position. Pushes via ntfy ONLY on an actual change since the last
   check — stop breached, target hit, an option going PROFIT/LOSS.
   "Still open, nothing new" is silent, by design.
2. **New-call admission (only within ~10 min of 9:45 / 11:30 / 14:30
   IST — Section 4.3's three intraday decision points):** re-checks
   candidates last night's `nse-scan site` run approved-but-capacity-
   vetoed (`data/latest_bundle/*.json`'s `vetoed_capacity`, e.g. hit the
   `max_open_ideas` cap) — never a fresh universe re-scan, which would be
   both expensive and exactly the "generate new calls continuously" Rule
   8 forbids. Re-admitted only if a slot has actually freed up AND the
   live price hasn't drifted more than 1.5% from the planned entry (the
   same "don't chase" rule already in the buy checklist).

**What it deliberately does NOT do**, and why:
- **No regime-conditional strategy switching.** `nse/regime/compare.py`
  already found switching momentum/fade on VIX/breadth does not provably
  beat the static `config.yaml` style out-of-sample — Section 4.4's own
  instruction is "if it can't [prove out], ship the static rule and say
  so." A live NIFTY read is shown as an informational note only.
- **No re-admission for sector/heat/correlation-capped candidates**, only
  `max_open_ideas` — the other three need a full live reconstruction of
  portfolio risk from the journal, which carries heterogeneous historical
  data (older entries predate Phase 8's risk fields) not safe to trust
  blindly. `max_open_ideas` only needs a count, which is safe.
- **No pre-open (8:45/9:05 IST) jobs** — those need fresh fundamentals/
  news/overnight-gap data this module doesn't fetch; last night's bundle
  stands in for "today's plan" at market open instead.

**Setup:** needs the same `SMARTAPI_*` secrets as `nightly.yml`, plus
`NTFY_TOPIC` (install the free ntfy app, subscribe to a topic name of your
choosing, put that name in the secret) — this job has nothing useful to do
without a push channel; email isn't something you check live during market
hours the way a phone push is.

Manual one-off check, printed instead of pushed:
```bash
nse-scan intraday-check
```
No-ops instantly (prints "Market closed", makes zero API calls) outside
real trading hours — safe to run any time to sanity-check it.

## Manual commands

```bash
# One-off scan, printed to the terminal
nse-scan scan --mode all

# Rebuild the dashboard's data bundle locally (what the nightly job runs)
nse-scan site --out site --refresh --max-seconds 600

# Validate a built bundle without deploying anything
nse-scan verify-bundle site

# Walk-forward backtest -- printed, or exported for the dashboard's
# Backtest tab (this is NOT run by the nightly job; see below)
nse-scan backtest --period 12
nse-scan backtest --period 12 --export site/data/backtest.json

# Same export, also embedding the Phase 6 fusion model's reliability curve/
# Brier score (a second, independent 15-20+ min walk-forward run -- off by
# default, opt in with --include-fusion; see nse/reporting.py)
nse-scan backtest --period 12 --export site/data/backtest.json --include-fusion

# Which sub-signals actually predict moves, out-of-sample
nse-scan factors --period 10

# Fusion model / regime-switching audits (Phase 6) -- 15-20+ min against
# the full universe, run these on demand, not in CI
nse-scan fusion
nse-scan regime

# Today's EOD report, from the ALREADY RISK-GATED site bundle (what the
# nightly job does -- picks already cleared Phase 7's budget cap and
# Phase 8's risk gate). This is the one to automate or trust.
nse-scan report --from-bundle site/data --email

# Ad-hoc/manual variant: regenerates picks itself, WITHOUT Phase 7/8's
# budget cap or risk gate -- fine for a quick look, never for automation
# (see nse/report.py's build_report_from_bundle docstring for why).
nse-scan report --email --ntfy

# Refresh real NSE sector classification (feeds the risk gate's sector cap)
# -- rarely needed; sector classification changes only on a real corporate
# reclassification, not nightly
nse-scan refresh-sectors

# Save today's picks to the journal + show the scorecard
nse-scan track

# One intraday monitoring cycle (what intraday.yml runs every ~15 min) --
# no-ops instantly outside market hours, safe to run any time
nse-scan intraday-check
```

`nse-scan backtest --export` is deliberately **not** part of the nightly
job: a full walk-forward run against the whole universe takes 15-20+
minutes, and folding that into the same job as the fast daily scan/email
risks the job's own 45-minute timeout. Run it yourself when you want the
(locally-built, per above) Backtest tab's numbers refreshed.

## When the nightly job fails

You'll know two ways:
1. **A GitHub issue** gets filed (or commented on, if one's already open),
   labeled `nightly-failure`. One issue is reused across consecutive
   failures rather than one per night — close it once you've fixed the
   underlying cause.
2. **An ntfy push**, only if you've set the `NTFY_TOPIC` repo secret. Not
   configuring it just means you rely on (1); the job never fails *because*
   ntfy isn't set up.

Diagnosis: open the failed run in Actions and check which step failed.

| Step failed | Likely cause | What to do |
|---|---|---|
| Connectivity check | Angel One endpoints unreachable/down | Usually transient — re-run via `workflow_dispatch`. Check status.angelone.in if it persists. |
| Build scanner data | SmartAPI token expired/rate-limited, or NSE/BSE blocked | Check the step log for `smartapi rate-limited` / `unavailable -> yfinance fallback` lines — the system degrades to yfinance automatically; a full failure here usually means BOTH providers had a bad day. Re-run. If SmartAPI is consistently failing, check `secrets.yaml`'s TOTP secret hasn't drifted (2FA re-enrollment invalidates it — see PROJECT_BRIEF.md Section 0) and that the repo secrets match. |
| Build scanner data (DataValidator FAIL, printed in the log) | Coverage below 95%, stale data, or a sanity-range violation | Read the printed `ValidationReport` — it names the exact check and offenders. This is the in-process gate; it already blocked the write, so nothing gets emailed off a bad bundle. |
| Verify bundle | A file's missing/malformed, or manifest counts disagree with the actual files | Read the printed report from `nse/quality/bundle_check.py` — same structure as DataValidator's. A `credential_scan` FAIL here is the most urgent: something credential-shaped reached a file about to be uploaded as a build artifact — stop, investigate before re-running. |
| Send daily report (email) | `EMAIL_FROM`/`EMAIL_TO`/`EMAIL_APP_PASSWORD` missing or wrong, or Gmail rejected the login | Check the three secrets exist (Settings → Secrets and variables → Actions) and that `EMAIL_APP_PASSWORD` is a Gmail **App Password** (Google Account → Security → App passwords), not the real account password — Gmail rejects the latter outright for SMTP. |

If SmartAPI credentials are actually compromised (not just expired), that's
not a "wait and retry" situation — go straight to PROJECT_BRIEF.md Section
0's rotation steps.

## Risk limits (config.yaml's `risk:` section)

Every field is optional; `nse/risk/limits.py`'s defaults apply to anything
omitted. Most relevant to tune for your own use:

- `capital`: your actual account size. Position sizing (`nse/risk/sizing.py`)
  and every risk-percentage figure in the dashboard scale off this.
- `per_trade_risk_pct` / `max_portfolio_heat_pct`: how much of `capital` one
  pick, and all open picks combined, are allowed to risk.
- `options_budget_cap`: defaults to Rs 10,000 per PROJECT_BRIEF.md Section
  5 — change only if you deliberately want a different cap, not to make
  more ideas "fit."
- `max_sector_pct` / `max_correlation`: backed by real NSE sector
  classification (`nse/sectors.py`), cached at `config/sector_map.json`.
  Refresh it with `nse-scan refresh-sectors` (rarely needed — sector
  classification changes only on a real corporate reclassification, not
  nightly). A symbol missing from the cache still shows up as "sector
  unknown" in the risk report rather than being assumed compliant.

A change here takes effect on the next `nse-scan site` run — nothing needs
rebuilding beyond that.

## Known limitations (read before trusting a number)

- **No true live feed, still.** The dashboard/email remain periodic batch
  snapshots. `intraday.yml` (see above) adds a free, ~15-minute REST-poll
  approximation for open-position monitoring during market hours — real
  value, but best-effort timing (Actions cron can drift), not the
  persistent WebSocket tick stream Section 4 originally envisioned.
- **Fundamentals coverage is thin.** ~25 of 210 universe symbols have any
  BSE XBRL data ingested; bank/NBFC coverage is uneven (BAJFINANCE has
  zero — BSE serves no fetchable document format for it at all, a genuine
  data-access dead end, not a bug in this codebase).
- **No demonstrated technical/fusion edge yet.** The walk-forward backtest,
  the Phase 6 fusion model, and the regime-switching comparison have all
  been run against real data and honestly report what they found: no
  lift over baseline that clears its own bootstrap CI, in the configurations
  tested so far. The Backtest tab shows this plainly rather than a
  cherry-picked number. See `PROJECT_BRIEF.md` Section 2's target table for
  what a "good" result would actually look like — this system isn't
  claiming to be there.
- **Backtest tab's reliability curve is opt-in, not automatic.** Phase 6's
  fusion audit (Brier score, reliability curve, lift vs the technical-score
  baseline) is wired into `nse/reporting.py`'s export, but only runs when
  you pass `--include-fusion` to `nse-scan backtest --export` -- it's a
  second, independent 15-20+ min walk-forward fit on top of the backtest's
  own, and bundling it in by default would double every export's runtime.
  Without that flag the Backtest tab simply omits the fusion section,
  same as it omits everything else that hasn't been exported yet.
- **`react-router-dom` carries two known moderate CVEs** (open redirect,
  SSR deserialization) with no non-breaking fix in the v6 line used here.
  Judged low-risk for this client-only SPA (no SSR, no untrusted redirect
  targets) and deliberately not forced into a v7 migration.

## Where things live

- `nse/` — the actual scanner (data, indicators, momentum, options, risk,
  fundamentals, news, regime, fusion, backtest, sitebuilder, intraday)
- `nse/quality/` — every publish-time gate: `validators.py` (pre-write
  DataValidator), `bundle_check.py` (post-write, Phase 10), `events.py`
  (provider-fallback / data-integrity log), `corporate_actions.py`
- `frontend/` — the React dashboard (Vite + TanStack Table + lightweight-charts)
- `config.yaml` — universe, scanner style, data provider, risk limits
- `secrets.yaml` (gitignored) / repo Secrets — credentials, never committed
- `data/` — local caches: price history, the point-in-time fundamentals/
  news stores, the journal, the data-integrity event log
- `data/latest_bundle/` — small, TRACKED copy of last night's candidate
  bundle (delivery/options/manifest.json) for `intraday.yml` to read
  during the day; committed by `nightly.yml`, unlike everything else
  under `data/cache/`
- `config/holidays/` — the NSE trading-holiday calendar `nse/calendar/
  market_calendar.py` reads (verified live against nseindia.com's own
  holiday page, one file per year, `verified: true` once checked — see
  that module's docstring for why it refuses to guess)
- `tests/` — one file per module, run via `pytest -q`
