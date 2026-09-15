# RUNBOOK

One-page operational reference for nse-scanner. For the *design* rationale
behind any of this, see `PROJECT_BRIEF.md` — this file is about what to do
when something needs attention, not why it was built this way.

**This is decision support, not an execution engine.** Nothing here places
orders. See `DISCLAIMER.md`.

## What runs, and when

| Job | Schedule | What it does |
|---|---|---|
| `.github/workflows/nightly.yml` | 13:00 UTC (18:30 IST) weekdays, or manual `workflow_dispatch` | Refreshes prices, runs the scanner, builds `site/data/*.json`, runs the risk gate, verifies the bundle, builds the frontend, deploys to GitHub Pages |
| `.github/workflows/ci.yml` | every push/PR to `main` | Runs `pytest` and the frontend build — no deploy |
| `.github/workflows/secret-scan.yml` | every push/PR | Fails the build if a secret-shaped string appears in the diff |

The dashboard itself is a static site with **no live feed** — Section 4's
SmartWebSocketV2 backend was never built (out of scope for how far this
project got); "data as of" on the dashboard means "when the last nightly
run finished," not real-time. The staleness badge (`api.js`'s
`manifestAgeMinutes`) reflects exactly that.

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

# Which sub-signals actually predict moves, out-of-sample
nse-scan factors --period 10

# Fusion model / regime-switching audits (Phase 6) -- 15-20+ min against
# the full universe, run these on demand, not in CI
nse-scan fusion
nse-scan regime

# Today's EOD report, optionally mailed/pushed
nse-scan report --email --ntfy

# Refresh real NSE sector classification (feeds the risk gate's sector cap)
# -- rarely needed; sector classification changes only on a real corporate
# reclassification, not nightly
nse-scan refresh-sectors

# Save today's picks to the journal + show the scorecard
nse-scan track
```

`nse-scan backtest --export` is deliberately **not** part of the nightly
job: a full walk-forward run against the whole universe takes 15-20+
minutes, and folding that into the same CI job as the fast daily publish
risks the job's own 45-minute timeout. Run it yourself when you want the
Backtest tab's numbers refreshed, commit the resulting `backtest.json`
under `site/data/`, and it'll ship on the next deploy.

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
| Build scanner data (DataValidator FAIL, printed in the log) | Coverage below 95%, stale data, or a sanity-range violation | Read the printed `ValidationReport` — it names the exact check and offenders. This is the in-process gate; it already blocked the write, so the old bundle is still live on Pages. |
| Verify bundle before deploy | A file's missing/malformed, manifest counts disagree with the actual files, or (WARN only, doesn't fail this step) a high provider-fallback rate | Read the printed report from `nse/quality/bundle_check.py` — same structure as DataValidator's. A `credential_scan` FAIL here is the most urgent: something credential-shaped reached a file about to go world-readable — stop, do not manually deploy, investigate before re-running. |
| Build frontend / npm ci | A dependency version drifted or a real code error | Reproduce locally: `cd frontend && npm ci && npm run build`. |
| Deploy to GitHub Pages | GitHub Pages outage, or Pages source misconfigured | Check Settings → Pages has "GitHub Actions" as the source. |

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

- **No live feed.** Everything is a periodic batch snapshot. "Stale" on the
  dashboard means "old nightly run," not "feed disconnected" in the
  real-time sense Section 4 originally envisioned.
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
- **Backtest tab has no reliability curve yet.** That lives in Phase 6's
  fusion module output, which isn't wired into `nse/reporting.py`'s export
  — a separate, larger serialization effort against a different,
  compute-heavy pipeline.
- **`react-router-dom` carries two known moderate CVEs** (open redirect,
  SSR deserialization) with no non-breaking fix in the v6 line used here.
  Judged low-risk for this client-only SPA (no SSR, no untrusted redirect
  targets) and deliberately not forced into a v7 migration.

## Where things live

- `nse/` — the actual scanner (data, indicators, momentum, options, risk,
  fundamentals, news, regime, fusion, backtest, sitebuilder)
- `nse/quality/` — every publish-time gate: `validators.py` (pre-write
  DataValidator), `bundle_check.py` (post-write, Phase 10), `events.py`
  (provider-fallback / data-integrity log), `corporate_actions.py`
- `frontend/` — the React dashboard (Vite + TanStack Table + lightweight-charts)
- `config.yaml` — universe, scanner style, data provider, risk limits
- `secrets.yaml` (gitignored) / repo Secrets — credentials, never committed
- `data/` — local caches: price history, the point-in-time fundamentals/
  news stores, the journal, the data-integrity event log
- `tests/` — one file per module, run via `pytest -q`
