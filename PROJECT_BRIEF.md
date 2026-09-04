# Master Prompt — NSE Scanner v2 (Delivery + Options Decision-Support System)

> How to use this file:
> 1. Save it in your repo root as `PROJECT_BRIEF.md` and commit it.
> 2. Add it to a Claude Project (see §8) so every chat starts with this context.
> 3. Run the phase prompts (§6) inside **Claude Code** on a local clone — that is the only
>    setup where the model can actually read, edit, test and PR your files.

---

## 1. The prompt (paste this as the opening message)

```
You are the lead engineer on `nse-scanner` (github.com/shanu1690/nse-scanner), an
existing, working Python + React system that scans the NSE for delivery/swing
candidates and F&O option ideas using Angel One SmartAPI, backtests them
walk-forward, and publishes a static dashboard via GitHub Actions to GitHub Pages.

READ FIRST, THEN PLAN. Before writing any code, read: README.md, config.yaml,
nse/momentum.py, nse/options.py, nse/backtest.py, nse/data.py, nse/smartapi.py,
nse/sitebuilder.py, and frontend/. Summarise the current scoring pipeline back to
me in <=15 lines and list every assumption you had to guess. Do not start coding
until I confirm that summary.

YOUR JOB is to extend this codebase along four axes it currently lacks:
  A. Fundamental analysis layer (SmartAPI provides none — see §3 for sources)
  B. News / corporate-actions / events layer, with point-in-time timestamps
  C. A combined ranking model that fuses technical + fundamental + event signals
     into a CALIBRATED PROBABILITY, not a 0-100 vibe score
  D. A materially better dashboard: real charts, drill-down, evidence-per-pick

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
5. Secrets stay in secrets.yaml / GitHub Actions Secrets. Never log, print,
   commit, or embed API keys, client IDs, PINs or TOTP secrets. Never write
   credentials into site/data/*.json — that bundle is PUBLIC.
6. Respect data-source terms of service and rate limits. Store links and your
   own summaries of news, never full article text.
7. Small, reviewable commits. One concern per PR. Tests before refactors.

SUCCESS CRITERIA — read carefully, these replace "90% accuracy":
The stated goal of >90% hit rate is not achievable on directional equity or
option picks, and this repo's own `factors` audit already demonstrates why. Do
not optimise toward it. Optimise toward these instead:

  - CALIBRATION: the model outputs P(+3% within 5 sessions). Measured Brier
    score must beat the base-rate-always model, and the reliability curve must
    be monotonic across deciles. This is the honest version of "accuracy".
  - EDGE: hit-rate lift over the matched baseline, with a bootstrap confidence
    interval that excludes zero. Current best is fade@65 → 22.0% vs 18.9%
    baseline (5d/+3%). Beating that meaningfully and stably is the bar.
  - EXPECTANCY: positive mean R per trade after 0.15% round-trip costs and
    slippage assumptions. Report profit factor and max consecutive losers.
  - STABILITY: performance does not collapse when the walk-forward window rolls
    forward, and is not driven by <5 symbols or one month.
  - COVERAGE: >=95% of the configured universe produces a scoreable row daily,
    with an explicit reason logged for every skip.
  - RELIABILITY: nightly job success rate >99%, dashboard build never publishes
    a partially-populated bundle.

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

## 3. Data sources to add (SmartAPI covers none of B or C)

| Layer | Source | Notes to hand the model |
|---|---|---|
| Prices, chains, lots | Angel SmartAPI (already wired) | `d_oi` is always 0 in REST quotes — fix by cross-sourcing OI change |
| Corporate announcements | NSE + BSE announcement endpoints | need browser-like headers + cookie priming; cache aggressively |
| Corporate actions | NSE corporate-actions feed | splits/bonus/dividend must adjust the price series or scores break |
| Results calendar | NSE/BSE board-meeting feed | drives an earnings blackout flag |
| Fundamentals | Screener.in, Tickertape, or a paid API | **check ToS before scraping**; prefer an official/paid feed if you will run this daily |
| Raw filings | BSE XBRL / annual reports | slowest but most reliable; good for a quarterly refresh job |
| News | RSS from Moneycontrol / ET Markets / Business Standard / LiveMint | store URL + timestamp + your own summary only |
| Bulk/block deals, FII-DII | NSE daily reports | useful, cheap, underused signal |

Instruct the model: every fetched record is stored with `source`, `fetched_at`,
and `published_at`, and the backtest may only see records where
`published_at < decision_bar_time`.

---

## 4. Target architecture (extends what you have)

```
nse/
  data.py            (exists)  price cache + provider abstraction
  smartapi.py        (exists)  Angel session
  indicators.py      (exists)  EMA/RSI/MACD/ATR/BB/Donchian/ROC/VOL_Z
  momentum.py        (exists)  momentum + fade scoring
  options.py         (exists)  chain parsing, PCR, max pain, IV, strikes
  backtest.py        (exists)  walk-forward + factor audit
  tracker.py         (exists)  journal + scorecard
  sitebuilder.py     (exists)  JSON bundle for the dashboard

  fundamentals/      NEW  fetchers, normaliser, point-in-time store, quality+
                          growth+valuation factors
  events/            NEW  announcements, corporate actions, results calendar,
                          blackout windows, corp-action price adjustment
  news/              NEW  RSS ingest, dedupe, entity linking to NSE symbol,
                          sentiment + materiality scoring, event-study features
  fusion/            NEW  feature assembly, calibrated model (start with
                          logistic regression — interpretable, hard to overfit),
                          isotonic/Platt calibration, SHAP-style reason strings
  risk/              NEW  position sizing, sector caps, correlation cap,
                          portfolio-level exposure limits
  quality/           NEW  data-integrity checks, leakage tests, bundle validator

frontend/            EXTEND  lightweight-charts candles + overlays, probability
                             gauge, evidence panel per pick, backtest tab,
                             journal/scorecard tab, options payoff diagram
```

**Model choice guidance for the prompt:** start with logistic regression or a
depth-3 gradient boosted tree on <25 features. With a few thousand point-in-time
observations, anything deeper memorises noise. Interpretability is a feature —
every pick must render "why" in plain English on the dashboard.

---

## 5. Agents, subagents and skills

Create these as Claude Code subagents in `.claude/agents/` (each file: name,
description, tools, and a system prompt). Keep each one narrow — a subagent with
five jobs behaves like no subagent at all.

| Agent | File | Mandate |
|---|---|---|
| `pipeline-orchestrator` | `.claude/agents/pipeline-orchestrator.md` | Owns the nightly sequence, decides what reruns on partial failure, never publishes a partial bundle |
| `data-integrity` | `data-integrity.md` | Schema + freshness + corporate-action adjustment checks; **has veto power over publishing** |
| `technical-analyst` | `technical-analyst.md` | Owns indicators.py + momentum.py; adds regime filters and relative strength refinements |
| `fundamental-analyst` | `fundamental-analyst.md` | Owns fundamentals/; point-in-time discipline is its top priority |
| `news-event-analyst` | `news-event-analyst.md` | Owns news/ + events/; sentiment, materiality, earnings blackout, summarise-never-copy |
| `options-strategist` | `options-strategist.md` | Owns options.py; IV rank, expected move, spreads vs naked, fixes the `d_oi` gap |
| `risk-manager` | `risk-manager.md` | Sizing, caps, drawdown limits; **veto power over any pick that breaches limits** |
| `adversarial-validator` | `adversarial-validator.md` | Explicitly tasked with *breaking* claimed edges: hunt leakage, shuffle labels, test on held-out regimes. Its success is finding bugs, not confirming results |
| `frontend-engineer` | `frontend-engineer.md` | React/Vite dashboard, charts, accessibility, mobile layout |

Skills in `.claude/skills/` (reusable procedure docs, not agents):

- `smartapi-usage` — TOTP session flow, batching (50 tokens), ~1 req/s, 403 backoff, scrip-master caching
- `nse-data-sources` — endpoints, headers, cookie priming, retry etiquette, ToS notes
- `point-in-time-protocol` — the leakage rules, with a checklist every new feature must pass
- `backtest-protocol` — walk-forward setup, baselines, bootstrap CIs, reporting template
- `indicator-conventions` — exact formulas/periods used, so nothing silently diverges
- `ui-design-system` — colours, typography, chart conventions, dark mode, number formatting

The two veto agents (`data-integrity`, `risk-manager`) matter more than they
look. They're what stops a confident-but-broken pipeline from publishing.

---

## 6. Phase prompts (run in order, one per session)

**Phase 0 — Audit.**
"Read the repo and produce: current pipeline map, a leakage audit of
backtest.py, a list of every place a future value could reach a past decision,
and the top 10 risks ranked by severity. No code changes."

**Phase 1 — Foundations.**
"Add quality/ with schema + freshness validators and a corporate-action price
adjustment step. Add the point-in-time store interface. Write failing tests
first. Do not touch scoring logic yet."

**Phase 2 — Events + news.**
"Implement events/ and news/ per §3. Every record carries published_at. Add an
earnings-blackout flag. Add event-study features (gap, drift after
announcement). Prove with a test that a backtest at date D cannot see a record
published after D."

**Phase 3 — Fundamentals.**
"Implement fundamentals/ with quality, growth, valuation and balance-sheet
factors, keyed to publication date. Run the factor audit on each new factor
exactly as factors/ does today, and report which ones are noise."

**Phase 4 — Fusion + calibration.**
"Build fusion/: assemble features, fit a logistic model, calibrate, and output
P(+3% in 5 sessions) plus the top-5 contributing reasons per symbol. Report
Brier score, reliability curve by decile, and lift vs baseline with bootstrap
CIs. Then hand the result to adversarial-validator and report what it found."

**Phase 5 — Options rework.**
"Fix the d_oi gap. Add IV rank vs 1-year history, expected move, and a
strategy selector (long CE/PE vs debit spread vs avoid) driven by IV rank and
days to expiry. Show cash required, breakeven, max loss, and a payoff diagram
per pick."

**Phase 6 — Risk.**
"Add risk/: per-trade risk %, ATR sizing, sector cap, correlation cap,
portfolio heat limit, and a hard rule that no pick is published if the
portfolio breaches limits."

**Phase 7 — Dashboard.**
"Rebuild the frontend around: (1) ranked picks with probability + evidence
panel, (2) lightweight-charts candles with EMA/Donchian/entry/stop/target
overlays, (3) options tab with payoff diagrams, (4) backtest tab showing the
honest metrics from §2, (5) journal/scorecard tab. Mobile-first, dark mode,
fast on a static bundle."

**Phase 8 — Hardening.**
"Add end-to-end tests, bundle validation before deploy, alerting on nightly
failure, and a one-page RUNBOOK.md."

---

## 7. Guardrails to keep in the repo

- `DISCLAIMER.md`: research and education tool; not investment advice; not SEBI
  registered; options can lose 100% of premium.
- Every published pick carries the model's probability and its uncertainty, never
  a bare "BUY".
- The scorecard is published whether it looks good or bad. That honesty ledger is
  the most valuable file in the project.
- Before ever adding order placement, re-check current SEBI/exchange rules for
  API-based execution (static IP, registration, algo approval). Read-only data
  and order execution sit under very different rulebooks.

---

## 8. Claude Project setup

I can't create the Project for you, but here's the config. In Claude, go to
Projects → Create project.

**Name:** NSE Scanner v2 — Delivery + Options Research

**Project instructions (paste):**
```
This project extends github.com/shanu1690/nse-scanner, a Python + React NSE
scanner using Angel One SmartAPI, into a fused technical + fundamental + event
research system. I am a senior fullstack developer; skip basics, be direct, and
challenge my assumptions.

Standing rules for every conversation in this project:
- Decision support only. Never design or add order execution.
- Never report a performance number without sample size, window, baseline and
  holding period. Assume any impressive result is a leak until proven otherwise.
- Do not optimise toward a target accuracy figure. Optimise calibration,
  expectancy and out-of-sample stability.
- Point-in-time discipline is absolute: fundamentals keyed to publication date,
  news to publish timestamp.
- Never output secrets. site/data/*.json is public.
- Summarise news in your own words with a source link; never paste article text.
- Prefer simple, interpretable models. Every pick must explain itself.
- When you disagree with my instruction, say so before implementing it.
```

**Add to project knowledge:** this file, your `README.md`, `config.yaml`, and the
latest `backtest` + `factors` output so the model always argues against real
numbers rather than imagined ones.

---

## 9. What to build first

Phases 0–2. The audit and the point-in-time plumbing are unglamorous and they
determine whether every number the system prints afterwards means anything. A
fusion model built on leaky features will show you 90% accuracy, and it will be
the most expensive number in the project.
