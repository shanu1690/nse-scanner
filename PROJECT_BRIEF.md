# Master Prompt — NSE Scanner v2 (Delivery + Options Decision-Support System)

> How to use this file:
> 1. Save it in your repo root as `PROJECT_BRIEF.md` and commit it.
> 2. Add it to a Claude Project (setup steps are in Section 11 below) so every chat
>    starts with this context.
> 3. Run the phase prompts (Section 8) inside **Claude Code** on a local clone — that is
>    the only setup where the model can actually read, edit, test and PR your files.

---

## 0. Security remediation — do this before writing a single line of new code

Credentials were committed to a public repository. Treat them as compromised.
Making the repo private does not undo exposure: GitHub indexes public code,
forks keep history, and scrapers harvest new commits within minutes.

**Do these yourself, in this order. Do not delegate credential rotation to any tool.**

1. **Rotate everything.**
   - Angel One developer console: delete the exposed API key, create a new one.
   - Change the trading PIN in the Angel One app.
   - Disable and re-enable 2FA so a new TOTP secret is issued. The old secret
     keeps generating valid codes until you do this.
   - Review account activity for anything you did not initiate.
2. **Scrub git history.** `git filter-repo` (or BFG) to purge the secrets from
   every commit, then force-push. Delete forks. Rotation in step 1 is what
   actually protects you; this step just stops re-exposure.
3. **Verify.** Run `gitleaks detect` and/or `trufflehog git file://.` over the
   full history. Older commits often hold secrets you have forgotten about.
4. **Go private.** Note the consequence: GitHub Pages from a private repo needs a
   paid plan. Since Section 4 moves you to a backend service anyway, host the
   dashboard there, or on Cloudflare Pages / Netlify.
5. **Prevent recurrence.** Add `secrets.yaml` to `.gitignore` (verify it is
   actually ignored), install a pre-commit secret-scanning hook, and keep all
   credentials in environment variables / GitHub Actions Secrets / Render env vars.

Add this to CI so it can never happen silently again: a job that fails the build
if a secret pattern appears in any diff.

---

## 1. The prompt (paste this as the opening message)

```
You are the lead engineer on `nse-scanner`, an existing, working Python + React
system that scans the NSE for delivery/swing candidates and F&O option ideas
using Angel One SmartAPI, backtests them walk-forward, and publishes a dashboard.

READ FIRST, THEN PLAN. Before writing any code, read: README.md, config.yaml,
nse/momentum.py, nse/options.py, nse/backtest.py, nse/data.py, nse/smartapi.py,
nse/sitebuilder.py, and frontend/. Summarise the current scoring pipeline back to
me in <=15 lines and list every assumption you had to guess. Do not start coding
until I confirm that summary.

YOUR JOB is to extend this codebase along six axes it currently lacks:
  A. Fundamental analysis layer (SmartAPI provides none — use the data-sources
     table in PROJECT_BRIEF.md)
  B. News / corporate-actions / events layer, with point-in-time timestamps
  C. A combined ranking model that fuses technical + fundamental + event signals
     into a CALIBRATED PROBABILITY, not a 0-100 vibe score
  D. A live, continuously-updating market feed (the current design is batch-only
     and structurally cannot stream — see PROJECT_BRIEF.md Section 4)
  E. A pre-open scheduler plus a manual trigger, and regime-aware recalculation
     of calls at fixed decision points
  F. An interactive dashboard: real charts, client-side filtering and sorting,
     drill-down, evidence-per-pick

NON-NEGOTIABLE CONSTRAINTS:
1. This is DECISION SUPPORT, not an execution engine. No order placement, no
   broker write APIs, no auto-trading. Read-only market data only. If you ever
   think order placement is needed, stop and ask.
2. NO LOOK-AHEAD BIAS, ever. Every feature must be computable from data
   timestamped strictly before the decision bar. Fundamentals use the RESULT
   PUBLICATION DATE, not the fiscal quarter end. News uses the article publish
   timestamp. Any function that touches future data in a backtest path is a bug
   of the highest severity — write the test that proves it can't happen.
3. Do not tune until the backtest looks good. Every parameter change must be
   justified by out-of-sample performance across at least two distinct market
   regimes, and you must report the parameter count and the sample size. If a
   config has more knobs than the signal has independent observations, say so.
4. Never claim an accuracy figure without stating: sample size, window,
   universe, holding period, threshold, and the matched baseline.
5. Credentials live ONLY in environment variables / Actions Secrets / Render env
   vars. Never log, print, commit, or embed API keys, client IDs, PINs or TOTP
   secrets. Never send credentials to the browser — the frontend must never talk
   to SmartAPI directly. Any public data bundle is assumed world-readable.
6. Respect data-source terms of service and rate limits. Store links and your
   own summaries of news, never full article text.
7. OPTIONS BUDGET IS A HARD CAP: no option idea may be shown whose entry cost
   exceeds Rs 10,000 total. The user is a beginner. See PROJECT_BRIEF.md
   Section 5 — this constraint changes which strategies are even eligible, and
   "no qualifying trade today" is a valid and expected output.
8. Do not generate new calls continuously. Continuous re-issuing produces
   overtrading. New calls only at defined decision points; between them, only
   invalidation alerts on open ideas.
9. Small, reviewable commits. One concern per PR. Tests before refactors.

SUCCESS CRITERIA — read carefully, these replace "90% accuracy":
The stated goal of >90% hit rate is not achievable on directional equity or
option picks, and this repo's own `factors` audit already demonstrates why. Do
not optimise toward it. Optimise toward these instead:

  - CALIBRATION: the model outputs P(+3% within 5 sessions). Measured Brier
    score must beat the base-rate-always model, and the reliability curve must
    be monotonic across deciles. This is the honest version of "accuracy".
  - EDGE: hit-rate lift over the matched baseline, with a bootstrap confidence
    interval that excludes zero. Current best is fade@65 -> 22.0% vs 18.9%
    baseline (5d/+3%). Beating that meaningfully and stably is the bar.
  - EXPECTANCY: positive mean R per trade after 0.15% round-trip costs and
    slippage assumptions. Report profit factor and max consecutive losers.
  - STABILITY: performance does not collapse when the walk-forward window rolls
    forward, and is not driven by <5 symbols or one month.
  - COVERAGE: >=95% of the configured universe produces a scoreable row daily,
    with an explicit reason logged for every skip.
  - RELIABILITY: scheduled job success rate >99%, feed uptime during market
    hours >99%, and the dashboard never publishes a partially-populated bundle.

WORKING STYLE: Ask before assuming. Show me the diff plan before large edits.
When a result looks too good, your first move is to hunt for the leak, not to
report the number.
```

---

## 2. Realistic target table (put this on the dashboard, not "90%")

| Metric | Meaning | Realistic good result |
|---|---|---|
| Hit-rate lift (5d, +3%) | vs matched baseline | +3 to +6 pp, CI excluding 0 |
| Brier score | probability calibration | beats base rate by 5–10% |
| Win rate (swing, 1:1.5 R) | delivery picks | 45–55% with positive expectancy |
| Win rate (long options) | CE/PE buying | 35–45%, profit comes from convexity |
| Profit factor | gross win / gross loss | 1.2–1.5 is genuinely good |
| Max drawdown | on the pick portfolio | tracked and stated, never hidden |

A system that clears these is a good system. A backtest showing 90% is a bug report.

---

## 3. Data sources to add

| Layer | Source | Notes to hand the model |
|---|---|---|
| Live ticks | SmartAPI **SmartWebSocketV2** | replaces REST polling; this is the fix for the stale feed |
| Prices, chains, lots | Angel SmartAPI REST (already wired) | `d_oi` is always 0 in REST quotes — fix by cross-sourcing OI change |
| Lot sizes | Angel scrip master, refreshed daily | NSE revises lot sizes roughly half-yearly; never hardcode them |
| OI change | NSE option-chain feed | fills the SmartAPI `d_oi` gap |
| Corporate announcements | NSE + BSE announcement endpoints | need browser-like headers + cookie priming; cache aggressively |
| Corporate actions | NSE corporate-actions feed | splits/bonus/dividend must adjust the price series or scores break |
| Results calendar | NSE/BSE board-meeting feed | drives an earnings blackout flag |
| Fundamentals | Screener.in, Tickertape, or a paid API | **check ToS before scraping**; prefer an official/paid feed for daily use |
| Raw filings | BSE XBRL / annual reports | slowest but most reliable; good for a quarterly refresh job |
| News | RSS from Moneycontrol / ET Markets / Business Standard / LiveMint | store URL + timestamp + your own summary only |
| Volatility regime | India VIX | drives regime detection and options strategy selection |
| Breadth | Advance/decline, % above 50DMA, FII-DII, bulk/block deals | cheap and underused regime inputs |

Every fetched record is stored with `source`, `fetched_at`, and `published_at`.
The backtest may only see records where `published_at < decision_bar_time`.

---

## 4. Live feed, scheduling and dynamic recalculation

### 4.1 Why the feed is stale today

The current design is a nightly GitHub Action that writes static JSON consumed by
a static Pages site. There is no running process, so there is nothing that *can*
hold a live connection. This is an architecture limitation, not a bug to patch.

The frontend must **not** be "fixed" by calling SmartAPI from the browser. That
would put credentials in client-side JavaScript.

### 4.2 The fix: a small always-on backend

Add a Render web service that:

- holds credentials as environment variables (never in the repo)
- maintains a **SmartWebSocketV2** subscription for the active universe during
  market hours, with auto-reconnect, heartbeat monitoring, and a REST fallback
  poller if the socket drops
- keeps last-tick state in memory (optionally Redis/Key-Value for restarts)
- exposes a **read-only** endpoint to the dashboard: SSE or WebSocket for live
  prices, plus REST for the ranked picks bundle
- exposes **no** endpoint that can place, modify or cancel an order

Operational notes to give the model: Render's free tier idles out and will kill
the socket, so a paid instance is required for genuine live behaviour. Angel
enforces subscription limits per connection and can 403 aggressively — batch
subscriptions and back off. Log connection state so a silent death is visible on
the dashboard rather than showing stale prices as if they were live.

**Staleness must be visible.** Every price on the UI carries an age indicator,
and anything older than a threshold renders visibly degraded. A dashboard that
shows stale numbers confidently is worse than one that shows nothing.

### 4.3 Scheduler

| Job | Time (IST) | Cron (UTC) | Purpose |
|---|---|---|---|
| Pre-open prep | 08:45 | `15 3 * * 1-5` | fundamentals, news, events, overnight gaps; build the candidate list |
| Pre-open finalise | 09:05 | `35 3 * * 1-5` | fold in pre-open auction data, publish the day's plan before 09:15 |
| Intraday refresh | 09:45 / 11:30 / 14:30 | separate entries | recalculate at decision points only |
| EOD | 18:30 | `0 13 * * 1-5` | existing nightly scan, journal, scorecard |

Two triggers, both required:
- **Scheduled**: cron. If jobs stay on GitHub Actions, note that Actions cron is
  best-effort and can be delayed 5–15 minutes under load — unacceptable for an
  09:05 deadline. Once the Render service exists, schedule there instead.
- **Manual**: `workflow_dispatch` for Actions, plus an authenticated "Run scan
  now" button on the dashboard hitting the backend. Rate-limit it, show progress
  and last-run timestamp, and make it idempotent.

Also required: a market-calendar check so the scheduler skips weekends, NSE
holidays, and handles special/muhurat sessions.

### 4.4 Dynamic, regime-aware calls

"Update calls based on the current market situation" splits into two mechanisms
that must not be conflated:

**Regime detection** (drives which model runs):
- India VIX level and 5-day trend
- NIFTY position vs EMA20/50/200, and its slope
- Breadth: advance/decline ratio, % of universe above 50DMA
- Sector rotation: relative strength ranking across sectors
- Trend-vs-mean-reversion classifier over a rolling window

Regime output selects the strategy: momentum in trending regimes, fade in
mean-reverting ones, reduced or zero exposure in high-VIX shock regimes. This is
the principled version of the momentum/fade switch the factor audit found.

**The honest caveat to hand the model:** regime detection is itself a prediction
problem and adds a large overfitting surface. It must prove out-of-sample that
regime-conditional switching beats picking one style and holding it. If it can't,
ship the static rule and say so.

**Invalidation monitoring** (drives what happens to open ideas):
Between decision points, do not generate new calls. Only monitor: stop breached,
target hit, thesis broken (volume collapse, adverse gap, unexpected news, sector
breakdown). These fire as alerts on existing ideas, not as fresh calls.

---

## 5. Options rules for a Rs 10,000 budget

**Hard cap: total entry cost of any displayed option idea must be <= Rs 10,000**,
computed as `lot_size x premium x lots` plus estimated charges, using lot sizes
read from the daily scrip master.

### 5.1 The constraint the model must confront

SEBI raised the minimum notional value of index derivative contracts to roughly
Rs 15 lakh, implemented via larger lot sizes, and NSE revises those sizes roughly
every six months. At that contract value, a single lot of an at-the-money option
typically costs well above Rs 10,000.

The consequence, stated plainly: a Rs 10,000 cap pushes the eligible set toward
cheap out-of-the-money and near-expiry contracts — the lowest-probability, fastest-
decaying instruments available. The budget makes losses small in rupees while
making them more likely in frequency. The system must not paper over this.

### 5.2 Design rules

1. **Filter, don't force.** If nothing clears the cap with acceptable
   probability, output "no qualifying option trade today". Print how many
   candidates were rejected and why. An empty list is a correct answer.
2. **Prefer defined-risk debit spreads.** A bull call / bear put spread costs far
   less than a naked long option and often fits the budget where a single ATM
   leg does not. Rank spreads above naked longs when both qualify.
3. **Rank by probability, not by cheapness.** Never sort the options list by
   ascending premium — that surfaces the worst contracts first.
4. **Refuse the traps.** Block same-day-expiry ideas, contracts below a minimum
   open-interest / bid-ask-spread quality bar, and anything where the spread
   itself eats a large share of the premium.
5. **Never recommend selling/writing options.** Undefined risk, and margin far
   exceeds the budget. Not eligible at this level.
6. **Beginner mode is the default UI.** Every option idea displays, in plain
   English: total cost, maximum possible loss (say "you can lose 100% of this"),
   breakeven price, what must happen and by when, days to expiry, and a payoff
   diagram. No Greeks on the default view; put them behind a toggle.
7. **Paper-trade gate.** Track every option idea in the journal with a simulated
   fill before any of it is treated as actionable, and show the running
   scorecard next to the picks.

---

## 6. Target architecture

```
backend/             NEW  Render service: SmartWebSocketV2 client, tick cache,
                          scheduler, read-only SSE/REST API, manual-trigger
                          endpoint, health + staleness reporting

nse/
  data.py            (exists)  price cache + provider abstraction
  smartapi.py        (exists)  Angel session
  indicators.py      (exists)  EMA/RSI/MACD/ATR/BB/Donchian/ROC/VOL_Z
  momentum.py        (exists)  momentum + fade scoring
  options.py         (exists)  chain parsing, PCR, max pain, IV, strikes
  backtest.py        (exists)  walk-forward + factor audit
  tracker.py         (exists)  journal + scorecard
  sitebuilder.py     (exists)  data bundle for the dashboard

  fundamentals/      NEW  fetchers, normaliser, point-in-time store, quality+
                          growth+valuation factors
  events/            NEW  announcements, corporate actions, results calendar,
                          blackout windows, corp-action price adjustment
  news/              NEW  RSS ingest, dedupe, entity linking to NSE symbol,
                          sentiment + materiality scoring, event-study features
  regime/            NEW  VIX/breadth/trend classifier, strategy selection
  fusion/            NEW  feature assembly, calibrated model (start with
                          logistic regression — interpretable, hard to overfit),
                          isotonic/Platt calibration, reason strings
  risk/              NEW  position sizing, sector caps, correlation cap,
                          options budget enforcement, portfolio exposure limits
  quality/           NEW  data-integrity checks, leakage tests, staleness
                          detection, bundle validator

frontend/            EXTEND  see Section 9
```

**Model choice guidance:** start with logistic regression or a depth-3 gradient
boosted tree on <25 features. With a few thousand point-in-time observations,
anything deeper memorises noise. Interpretability is a feature — every pick must
render "why" in plain English.

---

## 7. Agents, subagents and skills

Create these as Claude Code subagents in `.claude/agents/`. Keep each narrow — a
subagent with five jobs behaves like no subagent at all.

| Agent | Mandate |
|---|---|
| `pipeline-orchestrator` | Owns the scheduled sequence, decides what reruns on partial failure, never publishes a partial bundle |
| `data-integrity` | Schema, freshness, staleness and corporate-action checks; **veto power over publishing** |
| `feed-engineer` | Owns backend/: websocket lifecycle, reconnect, fallback polling, staleness reporting |
| `technical-analyst` | Owns indicators.py + momentum.py; regime filters and relative-strength refinements |
| `fundamental-analyst` | Owns fundamentals/; point-in-time discipline is its top priority |
| `news-event-analyst` | Owns news/ + events/; sentiment, materiality, earnings blackout, summarise-never-copy |
| `regime-analyst` | Owns regime/; must prove regime switching beats a static rule out-of-sample |
| `options-strategist` | Owns options.py; IV rank, expected move, spreads vs naked, the `d_oi` gap, **and the Rs 10,000 cap** |
| `risk-manager` | Sizing, caps, drawdown limits, budget enforcement; **veto power over any pick that breaches limits** |
| `adversarial-validator` | Explicitly tasked with *breaking* claimed edges: hunt leakage, shuffle labels, test on held-out regimes. Its success is finding bugs, not confirming results |
| `security-auditor` | Secret scanning in CI, dependency audit, ensures no credential ever reaches the browser or a public artifact |
| `frontend-engineer` | React dashboard, charts, filters, accessibility, mobile layout |

Skills in `.claude/skills/`:

- `smartapi-usage` — TOTP session flow, websocket subscription limits, batching, ~1 req/s REST, 403 backoff, scrip-master caching
- `nse-data-sources` — endpoints, headers, cookie priming, retry etiquette, ToS notes
- `point-in-time-protocol` — leakage rules with a checklist every new feature must pass
- `backtest-protocol` — walk-forward setup, baselines, bootstrap CIs, reporting template
- `indicator-conventions` — exact formulas/periods, so nothing silently diverges
- `options-budget-rules` — the Section 5 rules, applied identically everywhere
- `market-calendar` — trading days, holidays, special sessions, expiry schedule
- `ui-design-system` — colours, typography, chart conventions, dark mode, number formatting

The three veto agents (`data-integrity`, `risk-manager`, `security-auditor`)
matter more than they look. They're what stops a confident-but-broken pipeline
from publishing.

---

## 8. Phase prompts (run in order, one per session)

**Phase 0 — Security.**
"Implement Section 0. Add secret scanning to CI, a pre-commit hook, and a test
that fails if any credential-shaped string appears in a published artifact.
Report every file and commit that ever contained a secret. Do not rotate
credentials yourself — list exactly what I must rotate manually."

**Phase 1 — Audit.**
"Read the repo and produce: current pipeline map, a leakage audit of
backtest.py, a list of every place a future value could reach a past decision,
and the top 10 risks ranked by severity. No code changes."

**Phase 2 — Foundations.**
"Add quality/ with schema, freshness and staleness validators, plus a
corporate-action price adjustment step. Add the point-in-time store interface
and the market-calendar helper. Write failing tests first. Do not touch scoring
logic yet."

**Phase 3 — Live feed + scheduler.**
"Build backend/ per Section 4: SmartWebSocketV2 client with reconnect and REST
fallback, in-memory tick cache, read-only SSE endpoint, health/staleness
reporting, scheduled jobs at the Section 4.3 times, and an authenticated manual
trigger. Credentials from env vars only. Prove with a test that no endpoint can
place an order and no credential can reach a response body."

**Phase 4 — Events + news.**
"Implement events/ and news/ using the data-sources table. Every record carries
published_at. Add an earnings-blackout flag and event-study features (gap, drift
after announcement). Prove with a test that a backtest at date D cannot see a
record published after D."

**Phase 5 — Fundamentals.**
"Implement fundamentals/ with quality, growth, valuation and balance-sheet
factors, keyed to publication date. Run the factor audit on each new factor
exactly as factors/ does today, and report which ones are noise."

**Phase 6 — Regime + fusion + calibration.**
"Build regime/ and fusion/: classify the regime, assemble features, fit a
logistic model, calibrate, and output P(+3% in 5 sessions) plus the top-5
contributing reasons per symbol. Report Brier score, reliability curve by decile,
and lift vs baseline with bootstrap CIs. Separately report whether
regime-conditional switching beats the best static style out-of-sample — if it
does not, say so and keep the static rule. Then hand everything to
adversarial-validator and report what it found."

**Phase 7 — Options rework under the budget cap.**
"Fix the d_oi gap. Add IV rank vs 1-year history, expected move, and a strategy
selector (debit spread vs long CE/PE vs avoid) driven by IV rank and days to
expiry. Enforce every rule in Section 5. Show cost, max loss, breakeven, payoff
diagram, and plain-English thesis per idea. Verify the cap holds against live
scrip-master lot sizes, and confirm the system outputs an empty list rather than
a bad fit."

**Phase 8 — Risk.**
"Add risk/: per-trade risk %, ATR sizing, sector cap, correlation cap, portfolio
heat limit, options budget enforcement, and a hard rule that no pick is published
if the portfolio breaches limits."

**Phase 9 — Dashboard.**
"Rebuild the frontend per Section 9."

**Phase 10 — Hardening.**
"Add end-to-end tests, bundle validation before deploy, alerting on job failure
and feed disconnection, and a one-page RUNBOOK.md."

---

## 9. Dashboard: interactivity, filters and sorting

**Data table (delivery picks):**
- Sortable on every column: probability, score, price, change %, RS, volume
  z-score, sector, risk %, reward:risk
- Multi-column sort with visible priority order
- Filters: probability range, score range, price band, sector multi-select,
  market cap band, volume threshold, "fits my budget", "no earnings within N
  days", "in my watchlist", long/fade style
- Global search across symbol and company name
- Filter state encoded in the URL so a view can be bookmarked and shared
- Saved filter presets, persisted locally
- Column show/hide and reorder; CSV export of the current filtered view
- Row expands to an evidence panel: contributing factors, recent news headlines
  with links, upcoming events, fundamental snapshot

**Charts:**
- `lightweight-charts` candlesticks with EMA20/50/200, Donchian channel, volume
- Entry / stop / T1 / T2 drawn as horizontal levels
- Timeframe switcher; live last-price overlay from the SSE feed with an age badge
- Options payoff diagram with breakeven marked and max loss shaded

**Options tab:** budget-fit filter on by default, sorted by probability, each row
showing total cost against the Rs 10,000 cap.

**Backtest tab:** the Section 2 metrics, reliability curve, factor audit table,
and the regime-switching comparison.

**Journal tab:** open ideas with live P&L, closed ideas with outcome, and the
running scorecard — published whether it looks good or bad.

**Cross-cutting:** mobile-first, dark mode, keyboard navigation, skeleton loading
states, an explicit "data as of HH:MM:SS" stamp, and a visible degraded state
when the feed is stale or disconnected.

Implementation notes: TanStack Table for filtering/sorting, virtualised rows if
the universe grows past a few hundred, and all filtering client-side so it stays
instant.

---

## 10. Guardrails to keep in the repo

- `DISCLAIMER.md`: research and education tool; not investment advice; not SEBI
  registered; options can lose 100% of premium.
- Every published pick carries the model's probability and its uncertainty, never
  a bare "BUY".
- The scorecard is published whether it looks good or bad. That honesty ledger is
  the most valuable file in the project.
- Stale data is shown as stale, never as live.
- Before ever adding order placement, re-check current SEBI/exchange rules for
  API-based execution (static IP, registration, algo approval). Read-only data
  and order execution sit under very different rulebooks.

---

## 11. Claude Project setup

In Claude, go to Projects -> Create project.

**Name:** NSE Scanner v2 — Delivery + Options Research

**Project instructions (paste):**
```
This project extends my nse-scanner repo, a Python + React NSE scanner using
Angel One SmartAPI, into a fused technical + fundamental + event research system
with a live feed. I am a senior fullstack developer but a BEGINNER at options.
Skip programming basics, be direct, and challenge my assumptions.

Standing rules for every conversation in this project:
- Decision support only. Never design or add order execution.
- Never report a performance number without sample size, window, baseline and
  holding period. Assume any impressive result is a leak until proven otherwise.
- Do not optimise toward a target accuracy figure. Optimise calibration,
  expectancy and out-of-sample stability.
- Point-in-time discipline is absolute: fundamentals keyed to publication date,
  news to publish timestamp.
- Options: hard cap of Rs 10,000 entry cost per idea, defined-risk structures
  preferred, never option writing, always show max loss in plain English.
  "No qualifying trade today" is a valid answer — never fill the slot with a
  low-probability contract just to have something to show.
- Never output secrets, and never design anything that sends credentials to a
  browser or a public artifact.
- Summarise news in your own words with a source link; never paste article text.
- Prefer simple, interpretable models. Every pick must explain itself.
- When you disagree with my instruction, say so before implementing it.
```

**Add to project knowledge:** this file, your `README.md`, `config.yaml`, and the
latest `backtest` + `factors` output so the model always argues against real
numbers rather than imagined ones.

---

## 12. What to build first

Phase 0, today. Everything else can wait a day; exposed broker credentials cannot.

Then Phases 1–3. The audit, the point-in-time plumbing and the live feed are
unglamorous and they determine whether every number the system prints afterwards
means anything. A fusion model built on leaky features will show you 90%
accuracy, and it will be the most expensive number in the project.
