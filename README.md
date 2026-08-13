# nse-scanner

Daily India NSE scanner for **delivery / momentum stocks** and **option-trading
picks** (CE/PE with strike, premium, breakeven), plus a **walk-forward
backtest** that measures the scanner's real hit-rate — and a **free, hosted web
dashboard** (GitHub Actions nightly scan → static React app on GitHub Pages).

**Live dashboard:** <https://shanu1690.github.io/nse-scanner/> (refreshed
weekdays after market close)

> **Honesty first.** Momentum scanners and "max pain" tools sell comfort, not
> edge. This tool ranks stocks and prints entry/stop/targets, but the included
> backtest measures what that ranking *actually* predicted. Treat the tables as
> hypotheses to test, not signals to auto-trade. Options are risky, leveraged
> instruments — a wrong CE/PE pick can lose its full premium.

## Architecture

```
GitHub Actions (cron, Mon-Fri after close)
   │  nse-scan site        ← runs the real scanner, live Angel One SmartAPI
   ▼
site/data/*.json           ← report, picks, scorecard, chains, price series
   │  vite build           ← React dashboard reads the JSON (no server)
   ▼
GitHub Pages               ← free static hosting, updated automatically
```

There is no server to keep awake and nothing to pay for: a scheduled GitHub
Action runs the scan, builds the React app, and deploys to Pages.

## Local setup

```bash
# Python (scanner + site builder)
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"

# Node (frontend only)
cd frontend && npm install && cd ..
```

Credentials go in `secrets.yaml` (copy `secrets.yaml.example`):
- `smartapi.api_key` — from the Angel One developer console
  (https://smartapi.angelbroking.com → create an app, API type *Trading API*)
- `smartapi.client_id` — your Angel One client/trading ID
- `smartapi.pin` — the 4-digit trading PIN
- `smartapi.totp_secret` — base32 TOTP secret shown when you enable 2FA in the
  Angel One app

> **No static IP needed.** The SEBI/NSE static-IP rule (from 1 Apr 2026) only
> applies to order execution/GTT. This tool is read-only market data.

## Usage

```bash
.venv/bin/python scanner.py scan --mode all        # momentum/fade + options picks
.venv/bin/python scanner.py scan --mode options    # CE/PE picks w/ strikes
.venv/bin/python scanner.py scan --fade            # contrarian (washed-out) style
.venv/bin/python scanner.py watch RELIANCE         # deep-dive one symbol
.venv/bin/python scanner.py track                  # save today's picks + scorecard
.venv/bin/python scanner.py backtest --period 3 --min-score 65
.venv/bin/python scanner.py universe               # list universe
.venv/bin/python scanner.py site                   # emit JSON for the dashboard
```

(All commands are also available as `nse-scan ...` after `pip install -e .`.)

## The web dashboard

```bash
# 1. Generate the data bundle (live scan) -> site/data/*.json
.venv/bin/python scanner.py site                 # reuse cached prices (fast)
.venv/bin/python scanner.py site --refresh       # refetch every price (slower)

# 2. Build + preview the React app
cd frontend && npm run build && npm run preview
```

In development, `npm run dev` serves the app and proxies `/data/...` to the
`site/data` bundle, so you can iterate on the UI without re-running the scan.

## Hosting it for free on GitHub

1. Create a repo and push this project (never commit `secrets.yaml`).
2. **Secrets** → Settings → Secrets and variables → Actions:
   `SMARTAPI_API_KEY`, `SMARTAPI_CLIENT_ID`, `SMARTAPI_PIN`,
   `SMARTAPI_TOTP_SECRET`.
3. **Pages** → Settings → Pages → Source: **GitHub Actions**.
4. Push to `main`. The `nightly` workflow runs Mon-Fri at 13:00 UTC (18:30 IST,
   after market close), rebuilds the site, and deploys. Trigger it any time with
   *Run workflow*.

The `ci` workflow runs the Python tests and builds the frontend on every push.

## What each command does

### `scan --mode momentum`
Ranks the universe 0-100 using trend (EMA21/50/200), breakout (52-week high,
20-day Donchian, Bollinger squeeze), momentum (MACD, RSI 55-72 sweet spot,
ROC 5/20), volume (z-score vs 20-day), and relative strength vs NIFTY. Output:
entry, stop (1.5×ATR), two targets, risk %, reward:risk, and the reasons.

Two styles (default from `config.yaml: scanner.momentum.style`, override with
`--fade` / `--no-fade`):
- **momentum** (buy strength): ranks extended names at/near highs.
- **fade** (contrarian long): strict inversion of the strength factors — ranks
  washed-out, beaten-down names with positive relative strength. This is the
  default because the factor study below shows it actually beats baseline.

### `scan --mode options`
Pre-screens to the top momentum names, then pulls each option chain and scores:
- **PCR** (put-call ratio), **ATM IV**, **expected move** from straddle premium
- **Max pain** (correct liability-weighted formula; pulls spot toward it)
- IV rank, days to expiry, and strike picks (ATM + 1/2 ITM, with premium,
  breakeven, and lot size × premium = cash needed)
- Output per name: direction (CE/PE), strike, premium, breakeven

### `backtest`
Walk-forward honesty check. For every 5th day in each symbol's history, the
score is computed **only from data up to that day**, then measured against what
actually happened over the next 1/3/5 sessions. Hit-rates for +3%/+5%/+10%
moves are shown for signals vs an all-stock baseline.

### `track`
The honesty ledger. Run `track` right after a `scan`: it saves today's picks
(delivery entry/stop/targets + option strike/premium/breakeven) into
`data/journal.json`, then prints a **scorecard** that measures how every saved
pick is doing vs its plan — P&L, whether any target/stop was hit, and for
options whether spot is past breakeven. Run `track --status` any day to update
the scorecard without saving new picks.

### `factors`
Sub-signal audit: splits each factor (trend/breakout/momentum/volume/relative
strength) at its median and compares the mean 5-day forward return of the high
vs low bucket. This is how the fade style was discovered:

| factor | high bucket | low bucket | verdict |
|--------|-------------|------------|---------|
| trend | −0.21% | +0.33% | **buy-strength is a fade signal** |
| breakout | −0.29% | +0.17% | **same** |
| momentum | −0.34% | +0.42% | **same** |
| relative_strength | +0.68% | +0.00% | only factor that actually predicts upside |

**Current honest results (3-month window):**

- **FADE @ score≥65** (760 signals): 3d +3% **18.8%** vs baseline 14.6%; 5d +3%
  **22.0%** vs 18.9%; 5d +5% **13.6%** vs 10.3% — beats baseline everywhere
  beyond 1d.
- **MOMENTUM @ score≥70** (29 signals): below baseline everywhere (e.g. 5d +3%
  10.3% vs 18.9%).

Caveats: this is one ~3-month regime in a mean-reverting market; re-run
`backtest` and `factors` as the window rolls forward before trusting the edge.

## Data sources & limits

| Data | Source | Notes |
|------|--------|-------|
| Daily OHLCV | Angel One SmartAPI candles (fallback: yfinance) | rate-limited; cached per symbol |
| Option chains | Angel One SmartAPI `getMarketData` FULL | batched 50 tokens; ~1 req/s |
| Lot sizes | Angel One scrip master (CSV, ~36 MB, cached 1 day) | covers the F&O universe |
| NIFTY benchmark | SmartAPI NIFTY index candles | used for relative-strength |

Known limits:
- `d_oi` (change in open interest) is always 0 — the SmartAPI REST quotes don't
  expose it. PCR, max pain, IV and trend scoring are unaffected.
- Only symbols present in Angel's scrip master produce chains (~226 F&O names);
  anything else is skipped with a note.
- Angel's rate limiter can 403 even low-frequency calls; the client retries
  with backoff automatically.

## Configuration (`config.yaml`)
- `universe.symbols` — Nifty 50/Next 50 + F&O names (~130, NSE codes)
- `scanner.momentum.min_score` / `top_n` / `style` (`momentum`|`fade`)
- `scanner.options.top_n` — how many chains to pull
- `data.provider` — `smartapi` (default) or `nse`
- `data.lookback_days` — history depth (560 = supports EMA200/52w backtests)

## Layout
```
scanner.py          CLI entry point (thin shim over nse.cli)
nse/cli.py          CLI commands (scan/track/report/site/...)
nse/sitebuilder.py  emits site/data/*.json for the dashboard
nse/data.py         SmartAPI + yfinance price cache
nse/smartapi.py     Angel One SmartAPI session (chains, candles, lot sizes)
nse/nse_api.py      legacy NSE API (used when data.provider = nse)
nse/indicators.py   EMA/RSI/MACD/ATR/BB/Donchian/ROC/52w/VOL_Z
nse/momentum.py     momentum + fade scoring, trade levels
nse/options.py      option-chain parsing, PCR, max pain, IV, strike picks
nse/backtest.py     walk-forward validation, factor audit
nse/tracker.py      pick journal + scorecard
frontend/           React + Vite dashboard (built into site/)
.github/workflows/  ci.yml (tests+build) and nightly.yml (scan+deploy)
config.yaml         universe + thresholds
secrets.yaml.example  credential template (fill + rename to secrets.yaml)
data/journal.json   your saved picks (created by `track`)
```

## Beginner glossary
- **Entry / Stop / Target (T1, T2)** — the plan for a stock trade. Buy at entry;
  sell if it drops to stop (you were wrong); take profit at targets. Stop is
  1.5×ATR below entry — ATR is roughly how much the stock wiggles in a day.
- **FADE-BUY** — a "buy the beaten-down stock" idea: the stock fell a lot, is
  oversold, but is still stronger than the index. Thesis: it bounces.
- **Breakeven (option)** — the spot price where a call just stops losing money
  (= strike + premium). Stock must be above this at expiry to profit.
- **Max pain** — the strike where option sellers lose the least money; price
  tends to get "pulled" toward it as expiry nears.
- **PCR (put-call ratio)** — option traders' mood gauge (high = fear, low =
  greed/bullish bias).
