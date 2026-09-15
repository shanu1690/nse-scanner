"""Walk-forward validation, rebuilt per PROJECT_BRIEF.md Section 2 and the
backtest-protocol / point-in-time-protocol skills.

What changed from the previous version, and why (Phase 1 audit references
in parentheses):

- Style/threshold selection and reporting are separated. run_factor_analysis()
  is the *selection* tool: it derives thresholds from PRIOR walk-forward
  blocks only and tests them on the NEXT block, never the same data twice
  (Risk #2/#3 -- the old version split a median within one in-sample window
  and called that "validation"). run_backtest() *tests* a given (min_score,
  fade) as an external input -- it is never re-fit inside this call.
- A held-out block is reserved that no selection step -- in either function
  -- ever sees, guarding against the walk-forward's own construction being
  hand-tuned against its own out-of-sample numbers.
- Trades are simulated against the actual stop/target2 levels analyze_stock()/
  analyze_fade() compute, entering at the NEXT bar's open (not the same
  close the score was computed from -- point-in-time-protocol checklist item
  3 / Risk #6), walking bar-by-bar through High/Low for a realistic
  path-dependent exit, net of a stated round-trip cost.
- Lift is reported with a 95% CI from a cluster bootstrap over symbols, not
  a bare percentage (Risk #5's pseudo-replication problem: signals from the
  same symbol close in time, or many symbols on the same market-wide day,
  are not independent observations).
- A sample-size gate refuses to report a walk-forward result when there
  isn't enough history to plausibly span more than one market period,
  rather than force a number out of a window too thin to mean anything.
- Skips are logged with a reason (Risk #7), and recent SmartAPI/yfinance
  provider-fallback or regime-mismatch events for a symbol (Risk #10,
  nse/quality/events.py) are surfaced as coverage-report annotations
  instead of silently vanishing into an unexplained gap.
- The reported universe is stamped as-of-confirmed or not
  (nse/pit/universe.py) -- there is no historical index/F&O membership feed
  yet, so today's static list is used only as a labeled approximation.

What did NOT change: the fixed target definition (P(+3% within 5 sessions),
per the backtest-protocol skill) for the hit-rate/lift metric, and the two
public entry points' signatures -- nse/cli.py calls run_backtest(min_score=,
months=, fade=) and run_factor_analysis(months=, min_score=) unchanged.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import yaml

from . import data as data_mod
from . import indicators as ind
from . import momentum as mom
from .pit.universe import UniverseMembership
from .quality.events import read_events

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "config.yaml")) as fh:
    _CONFIG = yaml.safe_load(fh)

# ---------------------------------------------------------------- constants
WARMUP = 252          # bars needed so HIGH_52W/EMA200/ROC60 are non-NaN
HORIZONS = (1, 3, 5)  # forward look, in sessions (row offsets -- the cached
                      # OHLCV only has trading days, so this already IS
                      # session-counted; no calendar-day arithmetic here)
BASELINE_FWD = (3, 5, 10)          # % move thresholds checked at each horizon
FIXED_TARGET_HORIZON = 5           # backtest-protocol skill's fixed target:
FIXED_TARGET_PCT = 3.0             # P(+3% within 5 sessions)

COST_ROUND_TRIP_PCT = 0.15         # skill's floor
COST_ROBUSTNESS_PCT = 0.30         # skill: "report results with costs doubled"
MAX_HOLD_BARS = 10                 # trade simulation's max holding period

# A block shorter than ~2 months can't plausibly represent a distinct market
# period; 3 rolling + 1 held-out is the minimum shape this module considers
# a walk-forward at all. That's (3+1)*42 = 168 usable trading days minimum,
# i.e. a `months` argument below ~168/22 ≈ 7.6 can NEVER clear this gate no
# matter how deep the price cache is -- `months` caps the usable window
# itself (_global_test_dates), it doesn't just pick where prices are read
# from. nse/cli.py's `backtest`/`factors` --period defaults and
# nse/reporting.py's factor_analysis_months must stay above that floor with
# real margin, not sit at the edge -- this bit both of them at their old
# defaults (6 and 3 months respectively) until caught while building
# Phase 9's backtest-export feature.
MIN_ROLLING_BLOCKS = 3
MIN_BLOCK_BARS = 42

N_BOOTSTRAP = 2000
BOOTSTRAP_SEED = 1234              # reproducible CI width run-to-run
TOP_CONTRIBUTORS_N = 5

FACTORS = ("trend", "breakout", "momentum", "volume", "relative_strength")


# ------------------------------------------------------------------ records
@dataclass
class Signal:
    """One (symbol, decision-bar) observation: a score plus everything
    needed to evaluate it two different ways -- as a raw forward-return
    label (fixed target definition) and as a simulated trade."""
    symbol: str
    date: pd.Timestamp
    block: int
    score: float
    subscores: dict
    fwd_pct: dict            # {horizon: pct move to close[i+horizon], or absent}
    trade: "dict | None"     # simulate_trade() output, or None


@dataclass
class SkipReason:
    symbol: str
    reason: str
    detail: str = ""


@dataclass
class CoverageReport:
    """The brief's COVERAGE metric, made inspectable instead of silent
    (Risk #7), cross-referenced against recent data-integrity events instead
    of leaving a provider-fallback drop looking like an unexplained gap
    (Risk #10)."""
    universe: list = field(default_factory=list)
    scored: set = field(default_factory=set)
    skips: list = field(default_factory=list)
    data_events: dict = field(default_factory=dict)

    def add_scored(self, symbol: str) -> None:
        self.scored.add(symbol)

    def add_skip(self, symbol: str, reason: str, detail: str = "") -> None:
        self.skips.append(SkipReason(symbol, reason, detail))

    def add_data_event(self, symbol: str, event) -> None:
        self.data_events.setdefault(symbol, []).append(event)

    @property
    def coverage_ratio(self) -> float:
        return len(self.scored) / len(self.universe) if self.universe else 0.0

    def render(self) -> str:
        lines = [
            f"Coverage: {len(self.scored)}/{len(self.universe)} symbols "
            f"({self.coverage_ratio:.1%}) produced at least one signal in "
            f"the test window"
        ]
        if self.universe and self.coverage_ratio < 0.95:
            lines.append("  ** below the 95% floor PROJECT_BRIEF.md Section 2 sets **")
        by_reason: dict = {}
        for s in self.skips:
            by_reason.setdefault(s.reason, []).append(s)
        for reason, items in sorted(by_reason.items()):
            lines.append(f"  {reason}: {len(items)} symbol(s)")
            for s in items[:5]:
                lines.append(f"    - {s.symbol}" + (f": {s.detail}" if s.detail else ""))
            if len(items) > 5:
                lines.append(f"    ... +{len(items) - 5} more")
        if self.data_events:
            lines.append(
                f"  recent provider/data-integrity events on "
                f"{len(self.data_events)} symbol(s) (nse/quality/events.py):"
            )
            for sym, evs in list(self.data_events.items())[:5]:
                kinds = ", ".join(sorted({e.kind for e in evs}))
                lines.append(f"    - {sym}: {kinds}")
            if len(self.data_events) > 5:
                lines.append(f"    ... +{len(self.data_events) - 5} more")
        return "\n".join(lines)


# ------------------------------------------------------------ trade sim
def simulate_trade(bars: pd.DataFrame, entry_idx: int, stop: float, target: float,
                    risk: float, max_hold_bars: int = MAX_HOLD_BARS) -> "dict | None":
    """Path-dependent stop/target simulation. Entry fills at bars.Open at
    entry_idx (the bar AFTER the decision bar -- you cannot transact at the
    close your score was just computed from). Each subsequent bar is checked
    Open-then-High/Low, worse-case-first (a bar that could hit both stop and
    target is resolved as a stop, since intrabar order is unknown and the
    conservative assumption is the standard one). Exits on time at the last
    held bar's close if neither level is touched within max_hold_bars.

    `risk` is the PLANNED risk at decision time (decision-bar price minus
    stop, i.e. 1.5xATR by construction) -- not re-derived from the actual
    fill. A gap that opens the entry bar through the stop is a real, very
    bad trade (large adverse slippage), and R-multiple accounting is
    supposed to show that as a big negative R, not discard the trade: using
    the actual (gapped) entry-to-stop distance as the denominator would
    silently drop exactly the worst outcomes, which is its own bias.

    Returns raw entry/exit prices and which rule fired -- cost is NOT
    applied here (see r_multiple()), so one simulated path can be costed at
    multiple assumptions without re-simulating. None if there's no bar to
    enter on, or the setup itself is invalid (risk/target non-finite or <= 0).
    """
    n = len(bars)
    if entry_idx >= n or entry_idx < 0:
        return None
    entry_price = float(bars["Open"].iloc[entry_idx])
    if not np.isfinite(entry_price) or entry_price <= 0:
        return None
    if not np.isfinite(risk) or risk <= 0 or not np.isfinite(target):
        return None

    last_possible = min(entry_idx + max_hold_bars - 1, n - 1)
    exit_price = exit_reason = bars_held = None
    for j in range(entry_idx, last_possible + 1):
        o = float(bars["Open"].iloc[j])
        h = float(bars["High"].iloc[j])
        l = float(bars["Low"].iloc[j])
        if not (np.isfinite(o) and np.isfinite(h) and np.isfinite(l)):
            continue
        if o <= stop:
            exit_price, exit_reason = o, "stop"
        elif l <= stop:
            exit_price, exit_reason = stop, "stop"
        elif o >= target:
            exit_price, exit_reason = o, "target"
        elif h >= target:
            exit_price, exit_reason = target, "target"
        if exit_price is not None:
            bars_held = j - entry_idx + 1
            break

    if exit_price is None:
        j = last_possible
        exit_price = float(bars["Close"].iloc[j])
        exit_reason = "time"
        bars_held = j - entry_idx + 1

    return {
        "entry_price": entry_price, "exit_price": exit_price,
        "exit_reason": exit_reason, "bars_held": bars_held, "risk": risk,
    }


def r_multiple(trade: dict, cost_pct: float) -> float:
    """Net R-multiple at a given round-trip cost (%), applied symmetrically
    at both fills (half the cost eating each side)."""
    c = cost_pct / 100.0
    entry_fill = trade["entry_price"] * (1 + c / 2)
    exit_fill = trade["exit_price"] * (1 - c / 2)
    return (exit_fill - entry_fill) / trade["risk"]


# --------------------------------------------------------- block planning
def _global_test_dates(bench_full: pd.DataFrame, months: "int | None") -> pd.DatetimeIndex:
    idx = bench_full.index
    if len(idx) <= WARMUP:
        return pd.DatetimeIndex([])
    usable = idx[WARMUP:]
    if months is not None:
        usable = usable[-(months * 22):]
    return usable


def _plan_blocks(test_dates: pd.DatetimeIndex) -> "list[pd.DatetimeIndex] | None":
    """Contiguous blocks of >= MIN_BLOCK_BARS trading days, >= MIN_ROLLING_
    BLOCKS+1 of them (the last is the held-out block). None if there isn't
    enough history for that shape at all."""
    n = len(test_dates)
    needed = (MIN_ROLLING_BLOCKS + 1) * MIN_BLOCK_BARS
    if n < needed:
        return None
    n_blocks = n // MIN_BLOCK_BARS
    edges = np.linspace(0, n, n_blocks + 1).round().astype(int)
    return [test_dates[edges[k]:edges[k + 1]] for k in range(n_blocks)]


def _block_of(d: pd.Timestamp, block_bounds: "list[tuple]") -> "int | None":
    for k, (start, end) in enumerate(block_bounds):
        if start <= d <= end:
            return k
    return None


def _sample_size_gate_message(n_available: int, bench_total_bars: int,
                               lookback_days: int) -> str:
    needed = (MIN_ROLLING_BLOCKS + 1) * MIN_BLOCK_BARS
    shortfall = max(0, needed - n_available)
    ratio = (bench_total_bars / lookback_days) if lookback_days else 0.7
    extra_days = math.ceil(shortfall / ratio) if shortfall and ratio > 0 else 0
    recommended = lookback_days + extra_days
    comfortable = lookback_days + math.ceil((shortfall + needed) / ratio) if ratio > 0 else recommended
    return (
        f"Not enough post-warmup history for a walk-forward: {n_available} usable "
        f"trading days available after the {WARMUP}-bar indicator warmup, need at "
        f"least {needed} ({MIN_ROLLING_BLOCKS} rolling blocks + 1 held-out block of "
        f"{MIN_BLOCK_BARS} trading days each -- roughly 2 months per block, the "
        f"shortest span with any real chance of covering a distinct market period).\n"
        f"Refusing to report a walk-forward result rather than force one out of a "
        f"window too thin to mean anything.\n"
        f"At the observed ~{ratio:.0%} trading-day ratio for this cache: bumping "
        f"config.yaml's data.lookback_days to roughly {recommended} clears the bare "
        f"minimum; ~{comfortable} gives a comfortable margin (double the minimum "
        f"block budget) rather than sitting right at the gate. Either way, a fresh "
        f"--refresh pull is needed before this can run."
    )


# ------------------------------------------------------------ signal scan
def _collect_signals(universe, months, fade, coverage: CoverageReport):
    """Scan every symbol in `universe`. Returns (signals, block_bounds);
    block_bounds is None when there isn't enough history to plan a
    walk-forward (the sample-size gate)."""
    bench_df = data_mod.load_price_history(_CONFIG["data"]["index_benchmark"])
    if bench_df is None or len(bench_df) <= WARMUP:
        return [], None
    bench_full = ind.add_all_indicators(bench_df)
    test_dates = _global_test_dates(bench_full, months)
    blocks = _plan_blocks(test_dates)
    if blocks is None:
        return [], None
    block_bounds = [(b[0], b[-1]) for b in blocks]

    recent_events_since = datetime.now(timezone.utc) - timedelta(days=30)
    signals: list[Signal] = []

    for sym in universe:
        for ev in read_events(symbol=sym, since=recent_events_since):
            coverage.add_data_event(sym, ev)

        df_all = data_mod.load_price_history(sym)
        if df_all is None or len(df_all) <= WARMUP:
            coverage.add_skip(sym, "insufficient_history",
                               f"{0 if df_all is None else len(df_all)} bars "
                               f"(<= warmup {WARMUP})")
            continue
        try:
            full = ind.add_all_indicators(df_all)
        except Exception as exc:  # indicator computation is not exception-free
            coverage.add_skip(sym, "indicator_exception", str(exc))
            continue

        n = len(full)
        close = full["Close"]
        produced_any = False
        for i in range(WARMUP, n - 1):
            d = full.index[i]
            if d < block_bounds[0][0] or d > block_bounds[-1][1]:
                continue
            blk = _block_of(d, block_bounds)
            if blk is None:
                continue
            window = full.iloc[: i + 1]
            bench20 = bench60 = None
            b = bench_full[bench_full.index <= d].dropna()
            if len(b):
                brow = b.iloc[-1]
                bench20, bench60 = brow.get("ROC20"), brow.get("ROC60")
            try:
                a = (mom.analyze_fade(window, bench20, bench60) if fade
                     else mom.analyze_stock(window, bench20, bench60))
            except Exception as exc:
                coverage.add_skip(sym, "scoring_exception", f"{d.date()}: {exc}")
                continue
            if a is None:
                continue
            fwd = {}
            for h in HORIZONS:
                if i + h < n:
                    fwd[h] = (close.iloc[i + h] / close.iloc[i] - 1) * 100
            planned_risk = a["price"] - a["stop"]
            trade = simulate_trade(full, i + 1, a["stop"], a["target2"], planned_risk)
            signals.append(Signal(symbol=sym, date=d, block=blk, score=a["score"],
                                   subscores=a.get("subscores", {}), fwd_pct=fwd,
                                   trade=trade))
            produced_any = True
        if produced_any:
            coverage.add_scored(sym)
        else:
            coverage.add_skip(sym, "no_signals_in_window", "")

    return signals, block_bounds


# ----------------------------------------------------------------- metrics
def _lift_stat(signals, min_score, horizon, target_pct) -> float:
    denom_all = [s for s in signals if s.fwd_pct.get(horizon) is not None]
    if not denom_all:
        raise ZeroDivisionError
    baseline = sum(1 for s in denom_all if s.fwd_pct[horizon] >= target_pct) / len(denom_all)
    sig = [s for s in denom_all if s.score >= min_score]
    if not sig:
        raise ZeroDivisionError
    hit = sum(1 for s in sig if s.fwd_pct[horizon] >= target_pct) / len(sig)
    return hit - baseline


def _mean_r_stat(signals, min_score, cost_pct) -> float:
    rs = [r_multiple(s.trade, cost_pct) for s in signals
          if s.score >= min_score and s.trade is not None]
    if not rs:
        raise ZeroDivisionError
    return float(np.mean(rs))


def bootstrap_ci(signals, stat_fn, n_boot: int = N_BOOTSTRAP,
                  seed: int = BOOTSTRAP_SEED) -> "tuple[float, float] | None":
    """Cluster bootstrap by symbol, not by individual signal row: signals
    from the same symbol close in time are highly autocorrelated, and many
    symbols often move together on the same market-wide day, so an i.i.d.
    resample of rows understates the true CI width (Risk #5). Resampling the
    SET of symbols with replacement and keeping every one of a chosen
    symbol's signals is a coarser but honest unit of resampling -- it does
    not fully address same-day cross-sectional correlation across symbols,
    but it is a real improvement over treating each row as independent.
    """
    by_symbol: dict = {}
    for s in signals:
        by_symbol.setdefault(s.symbol, []).append(s)
    symbols = list(by_symbol)
    if len(symbols) < 2:
        return None
    rng = np.random.default_rng(seed)
    stats = []
    for _ in range(n_boot):
        chosen = rng.choice(symbols, size=len(symbols), replace=True)
        resampled = [sig for sym in chosen for sig in by_symbol[sym]]
        try:
            stats.append(stat_fn(resampled))
        except (ZeroDivisionError, ValueError):
            continue
    if len(stats) < n_boot // 4:  # too many degenerate resamples to trust
        return None
    lo, hi = np.percentile(stats, [2.5, 97.5])
    return float(lo), float(hi)


def label_shuffle_control(signals, min_score, horizon=FIXED_TARGET_HORIZON,
                           target_pct=FIXED_TARGET_PCT,
                           seed: int = BOOTSTRAP_SEED) -> "float | None":
    """Point-in-time-protocol skill: 'assume a leak, run the label-shuffle
    test, confirm the edge disappears.' Permutes forward-return outcomes
    across signals -- breaking any real relationship between score and
    outcome while preserving the outcome distribution -- and recomputes the
    lift. A genuine (non-leaked) edge should collapse toward zero here."""
    rng = np.random.default_rng(seed)
    fwd_values = [s.fwd_pct.get(horizon) for s in signals]
    shuffled = list(fwd_values)
    rng.shuffle(shuffled)
    base_n = base_hit = denom = hit = 0
    for s, fwd in zip(signals, shuffled):
        if fwd is None:
            continue
        base_n += 1
        if fwd >= target_pct:
            base_hit += 1
        if s.score >= min_score:
            denom += 1
            if fwd >= target_pct:
                hit += 1
    if denom == 0 or base_n == 0:
        return None
    return hit / denom - base_hit / base_n


def _top_contributor_symbols(signals, min_score, n=TOP_CONTRIBUTORS_N):
    counts: dict = {}
    for s in signals:
        if s.score >= min_score:
            counts[s.symbol] = counts.get(s.symbol, 0) + 1
    return {sym for sym, _ in sorted(counts.items(), key=lambda kv: -kv[1])[:n]}


def _best_month(signals, min_score):
    by_month: dict = {}
    for s in signals:
        if s.score >= min_score and s.fwd_pct.get(FIXED_TARGET_HORIZON) is not None:
            by_month.setdefault(s.date.strftime("%Y-%m"), []).append(
                s.fwd_pct[FIXED_TARGET_HORIZON])
    if not by_month:
        return None
    return max(by_month, key=lambda m: np.mean(by_month[m]))


def _trade_stats(signals, min_score, cost_pct):
    trades = sorted(
        ((s.date, r_multiple(s.trade, cost_pct), s.trade["exit_reason"])
         for s in signals if s.score >= min_score and s.trade is not None),
        key=lambda t: t[0],
    )
    if not trades:
        return None
    rs = [r for _, r, _ in trades]
    wins = [r for r in rs if r > 0]
    losses = [r for r in rs if r <= 0]
    gross_win = sum(wins)
    gross_loss = -sum(losses)
    if gross_loss > 0:
        profit_factor = gross_win / gross_loss
    else:
        profit_factor = float("inf") if gross_win > 0 else float("nan")
    equity = np.cumsum(rs)
    peak = np.maximum.accumulate(equity)
    max_dd = float((equity - peak).min())
    max_consec = cur = 0
    for r in rs:
        cur = cur + 1 if r <= 0 else 0
        max_consec = max(max_consec, cur)
    exit_reasons: dict = {}
    for _, _, reason in trades:
        exit_reasons[reason] = exit_reasons.get(reason, 0) + 1
    return {
        "n_trades": len(rs), "mean_r": float(np.mean(rs)),
        "win_rate": len(wins) / len(rs), "profit_factor": profit_factor,
        "max_drawdown_r": max_dd, "max_consecutive_losers": max_consec,
        "exit_reasons": exit_reasons,
    }


# --------------------------------------------------------------- reporting
def _format_hit_rate_block(signals, min_score, fade, universe_size, as_of_confirmed) -> str:
    lines = []
    denom_all = [s for s in signals if s.fwd_pct.get(FIXED_TARGET_HORIZON) is not None]
    sig = [s for s in denom_all if s.score >= min_score]
    if not denom_all or not sig:
        return "  (no signals in this segment)"
    baseline = sum(1 for s in denom_all
                   if s.fwd_pct[FIXED_TARGET_HORIZON] >= FIXED_TARGET_PCT) / len(denom_all)
    hit = sum(1 for s in sig
              if s.fwd_pct[FIXED_TARGET_HORIZON] >= FIXED_TARGET_PCT) / len(sig)
    lift = hit - baseline
    ci = bootstrap_ci(signals, lambda batch: _lift_stat(
        batch, min_score, FIXED_TARGET_HORIZON, FIXED_TARGET_PCT))
    ci_str = f"[{ci[0]:+.1%}, {ci[1]:+.1%}]" if ci else "n/a (too few symbols to bootstrap)"
    symbols_involved = sorted({s.symbol for s in sig})
    months_involved = sorted({s.date.strftime("%Y-%m") for s in sig})
    window = f"{signals[0].date.date()} to {signals[-1].date.date()}"
    universe_note = ("YES" if as_of_confirmed else
                      "NO -- static snapshot, see nse/pit/universe.py")

    lines.append(f"  Metric:      hit rate, {FIXED_TARGET_HORIZON}d/+{FIXED_TARGET_PCT:.0f}% "
                 f"(fixed target definition)")
    lines.append(f"  Value:       {hit:.1%}")
    lines.append(f"  Baseline:    {baseline:.1%} (same universe/window/horizon/threshold)")
    lines.append(f"  Lift:        {lift:+.1%}  95% CI {ci_str} "
                 f"(cluster bootstrap by symbol, n={N_BOOTSTRAP})")
    lines.append(f"  Sample size: {len(sig)} signals, {len(symbols_involved)} symbols, "
                 f"{len(months_involved)} distinct months")
    lines.append(f"  Window:      {window}")
    lines.append(f"  Universe:    {universe_size} symbols (as-of confirmed: {universe_note})")
    lines.append(f"  Holding:     n/a for this metric (raw price label -- see trade block "
                 f"for the simulated exit rule)")
    lines.append(f"  Threshold:   score >= {min_score} ({'fade' if fade else 'momentum'} "
                 f"style) -- supplied to this call, not fit inside it")
    lines.append(f"  Costs:       n/a (this is a price label, not a simulated fill)")
    lines.append(f"  Parameters:  0 fit in this call; min_score/style are external inputs. "
                 f"Use run_factor_analysis()'s walk-forward selection to choose them "
                 f"without reusing this same data for both tuning and reporting.")
    return "\n".join(lines)


def _format_trade_block(signals, min_score) -> str:
    lines = ["", "  Trade simulation (stop/target2, next-bar-open entry, "
                  f"up to {MAX_HOLD_BARS} sessions):"]
    for cost_label, cost in (("0.15% round-trip", COST_ROUND_TRIP_PCT),
                             ("0.30% round-trip (doubled, robustness)", COST_ROBUSTNESS_PCT)):
        stats = _trade_stats(signals, min_score, cost)
        if stats is None:
            lines.append(f"    [{cost_label}] no simulated trades")
            continue
        pf = stats["profit_factor"]
        pf_str = "inf" if math.isinf(pf) else (f"{pf:.2f}" if not math.isnan(pf) else "n/a")
        lines.append(
            f"    [{cost_label}] n={stats['n_trades']} | mean R {stats['mean_r']:+.2f} | "
            f"win rate {stats['win_rate']:.1%} | profit factor {pf_str} | "
            f"max drawdown {stats['max_drawdown_r']:+.1f}R | "
            f"max consecutive losers {stats['max_consecutive_losers']} | "
            f"exits {stats['exit_reasons']}"
        )
    lines.append("    (per-trade R equity, fixed 1-unit risk sizing -- not a portfolio-level "
                 "drawdown; concurrent correlated positions are Phase 8's risk/ layer, not "
                 "modelled here)")
    return "\n".join(lines)


def _format_robustness(signals, min_score) -> str:
    lines = ["", "  Robustness checks (backtest-protocol skill's required list):"]
    base_lift = None
    try:
        base_lift = _lift_stat(signals, min_score, FIXED_TARGET_HORIZON, FIXED_TARGET_PCT)
    except ZeroDivisionError:
        lines.append("    insufficient data for any robustness check")
        return "\n".join(lines)

    top = _top_contributor_symbols(signals, min_score)
    excl_top = [s for s in signals if s.symbol not in top]
    try:
        lift_wo_top = _lift_stat(excl_top, min_score, FIXED_TARGET_HORIZON, FIXED_TARGET_PCT)
        lines.append(f"    excl. top {len(top)} symbols ({', '.join(sorted(top))}): "
                     f"lift {lift_wo_top:+.1%} (full sample: {base_lift:+.1%})")
    except ZeroDivisionError:
        lines.append(f"    excl. top {len(top)} symbols: not enough data left to recompute")

    best_month = _best_month(signals, min_score)
    if best_month:
        excl_month = [s for s in signals if s.date.strftime("%Y-%m") != best_month]
        try:
            lift_wo_month = _lift_stat(excl_month, min_score, FIXED_TARGET_HORIZON, FIXED_TARGET_PCT)
            lines.append(f"    excl. best month ({best_month}): lift {lift_wo_month:+.1%}")
        except ZeroDivisionError:
            lines.append(f"    excl. best month ({best_month}): not enough data left")

    dates = sorted({s.date for s in signals})
    if len(dates) >= 2:
        mid = dates[len(dates) // 2]
        first_half = [s for s in signals if s.date < mid]
        second_half = [s for s in signals if s.date >= mid]
        try:
            l1 = _lift_stat(first_half, min_score, FIXED_TARGET_HORIZON, FIXED_TARGET_PCT)
            l2 = _lift_stat(second_half, min_score, FIXED_TARGET_HORIZON, FIXED_TARGET_PCT)
            lines.append(f"    first half ({dates[0].date()} to {mid.date()}): {l1:+.1%}  |  "
                         f"second half ({mid.date()} to {dates[-1].date()}): {l2:+.1%}")
            lines.append("    (approximates 'holds in a structurally different period' -- "
                         "not a true regime split; that needs Phase 6's regime/ classifier)")
        except ZeroDivisionError:
            lines.append("    first/second half split: not enough data on one side")
    return "\n".join(lines)


def _format_label_shuffle(signals, min_score) -> str:
    try:
        real = _lift_stat(signals, min_score, FIXED_TARGET_HORIZON, FIXED_TARGET_PCT)
    except ZeroDivisionError:
        return ""
    shuffled = label_shuffle_control(signals, min_score)
    if shuffled is None:
        return ""
    verdict = "PASS" if abs(shuffled) < abs(real) / 2 or abs(real) < 0.005 else "REVIEW"
    return (f"\n  Label-shuffle control: real lift {real:+.1%} vs shuffled-label lift "
           f"{shuffled:+.1%} (expect ~0) -> {verdict}\n"
           f"  (point-in-time-protocol skill: run this before trusting any good-looking "
           f"result; REVIEW means the shuffle didn't collapse the way a genuine, "
           f"leak-free edge should -- treat the result as suspect until re-checked)")


# ------------------------------------------------------------------ public
def run_backtest(min_score=60.0, months=12, symbols=None, quiet=False, fade=False):
    """Test a FIXED (min_score, fade) rule walk-forward, with a genuinely
    held-out final block. This function does not select min_score/fade --
    see run_factor_analysis() for that, kept as a separate step so the same
    data is never used for both tuning and reporting (Risk #2)."""
    universe = symbols or _CONFIG["universe"]["symbols"]
    coverage = CoverageReport(universe=list(universe))
    signals, block_bounds = _collect_signals(universe, months, fade, coverage)

    if block_bounds is None:
        bench_df = data_mod.load_price_history(_CONFIG["data"]["index_benchmark"])
        bench_total = len(bench_df) if bench_df is not None else 0
        n_avail = max(0, bench_total - WARMUP)
        if months is not None:
            n_avail = min(n_avail, months * 22)
        msg = _sample_size_gate_message(n_avail, bench_total, _CONFIG["data"]["lookback_days"])
        if not quiet:
            print(msg)
        return {"ok": False, "reason": "insufficient_history", "message": msg}

    as_of_confirmed = UniverseMembership(static_universe=list(universe)).confirmed
    n_blocks = len(block_bounds)
    rolling = [s for s in signals if s.block < n_blocks - 1]
    held_out = [s for s in signals if s.block == n_blocks - 1]

    lines = [
        f"{'FADE' if fade else 'MOMENTUM'} walk-forward @ score >= {min_score} | "
        f"{n_blocks} blocks ({n_blocks - 1} rolling + 1 held-out, "
        f"~{MIN_BLOCK_BARS}+ trading days each)",
        coverage.render(),
        "",
        "=== HELD-OUT (the number that matters -- never used for anything else) ===",
        _format_hit_rate_block(held_out, min_score, fade, len(universe), as_of_confirmed),
        _format_trade_block(held_out, min_score),
        _format_robustness(held_out, min_score),
        _format_label_shuffle(held_out, min_score),
        "",
        "=== ROLLING (context: every block except the held-out one) ===",
        _format_hit_rate_block(rolling, min_score, fade, len(universe), as_of_confirmed),
        _format_trade_block(rolling, min_score),
    ]
    text = "\n".join(l for l in lines if l is not None)
    if not quiet:
        print(text)
    return {"ok": True, "text": text, "coverage": coverage, "signals": signals,
            "block_bounds": block_bounds, "held_out": held_out, "rolling": rolling}


def run_factor_analysis(months=10, min_score=55.0):
    """Walk-forward factor attribution: which sub-factors predict OOS, using
    only prior blocks' data to set each split (Risk #3 -- the old version
    split a whole-sample median and called the same-sample comparison
    'validation'). The final block is reserved and never used to derive a
    threshold, mirroring run_backtest()'s held-out discipline."""
    fade = _CONFIG["scanner"]["momentum"].get("style", "momentum") == "fade"
    universe = _CONFIG["universe"]["symbols"]
    coverage = CoverageReport(universe=list(universe))
    signals, block_bounds = _collect_signals(universe, months, fade, coverage)

    if block_bounds is None:
        bench_df = data_mod.load_price_history(_CONFIG["data"]["index_benchmark"])
        bench_total = len(bench_df) if bench_df is not None else 0
        n_avail = max(0, bench_total - WARMUP)
        if months is not None:
            n_avail = min(n_avail, months * 22)
        msg = _sample_size_gate_message(n_avail, bench_total, _CONFIG["data"]["lookback_days"])
        print(msg)
        return {"ok": False, "reason": "insufficient_history", "message": msg}

    n_blocks = len(block_bounds)
    n_rolling = n_blocks - 1  # last block reserved, never used for selection either
    print(f"Factor analysis ({'FADE' if fade else 'MOMENTUM'}, from config.yaml's "
          f"configured style): {n_blocks} blocks ({n_rolling} rolling for "
          f"trailing-window selection + 1 held-out, never used to derive a threshold)")
    print(coverage.render())
    print()

    oos_diffs = {f: [] for f in FACTORS}
    oos_score_diff = []
    for k in range(1, n_rolling):
        prior = [s for s in signals if s.block < k]
        current = [s for s in signals if s.block == k]
        if not prior or not current:
            continue
        for f in FACTORS:
            vals = [s.subscores.get(f, 0) for s in prior]
            if not vals:
                continue
            med = float(np.median(vals))
            hi = [s.fwd_pct.get(FIXED_TARGET_HORIZON) for s in current
                  if s.subscores.get(f, 0) > med and s.fwd_pct.get(FIXED_TARGET_HORIZON) is not None]
            lo = [s.fwd_pct.get(FIXED_TARGET_HORIZON) for s in current
                  if s.subscores.get(f, 0) <= med and s.fwd_pct.get(FIXED_TARGET_HORIZON) is not None]
            if hi and lo:
                oos_diffs[f].append(float(np.mean(hi) - np.mean(lo)))
        prior_scores = [s.score for s in prior]
        if prior_scores:
            med_score = float(np.median(prior_scores))
            hi = [s.fwd_pct.get(FIXED_TARGET_HORIZON) for s in current
                  if s.score >= med_score and s.fwd_pct.get(FIXED_TARGET_HORIZON) is not None]
            lo = [s.fwd_pct.get(FIXED_TARGET_HORIZON) for s in current
                  if s.score < med_score and s.fwd_pct.get(FIXED_TARGET_HORIZON) is not None]
            if hi and lo:
                oos_score_diff.append(float(np.mean(hi) - np.mean(lo)))

    rows = []
    for f in FACTORS:
        d = oos_diffs[f]
        rows.append([f, len(d), f"{np.mean(d):+.2f}%" if d else "n/a"])
    rows.append(["score (median split)", len(oos_score_diff),
                 f"{np.mean(oos_score_diff):+.2f}%" if oos_score_diff else "n/a"])

    from tabulate import tabulate
    print(tabulate(rows, headers=["factor", "n OOS rolls", "mean OOS 5d-fwd diff (hi-lo)"],
                   tablefmt="grid"))
    print()
    print("Positive diff = factor predicts upside out-of-sample, across rolls where the")
    print("split threshold was set from PRIOR blocks only. Compare against the old")
    print("in-sample version's numbers with suspicion if they were much larger -- that")
    print("gap is roughly the size of the leak this rebuild closes (Risk #3).")

    held_out = [s for s in signals if s.block == n_blocks - 1]
    all_rolling = [s for s in signals if s.block < n_blocks - 1]
    held_out_diff = None
    if held_out and all_rolling:
        scores = [s.score for s in all_rolling]
        if scores:
            med_score = float(np.median(scores))
            hi = [s.fwd_pct.get(FIXED_TARGET_HORIZON) for s in held_out
                  if s.score >= med_score and s.fwd_pct.get(FIXED_TARGET_HORIZON) is not None]
            lo = [s.fwd_pct.get(FIXED_TARGET_HORIZON) for s in held_out
                  if s.score < med_score and s.fwd_pct.get(FIXED_TARGET_HORIZON) is not None]
            if hi and lo:
                held_out_diff = float(np.mean(hi) - np.mean(lo))
                print(f"\nHeld-out block (never used above): score-median from ALL rolling "
                      f"blocks -> {held_out_diff:+.2f}% high-low {FIXED_TARGET_HORIZON}d-fwd "
                      f"diff (n_hi={len(hi)}, n_lo={len(lo)})")

    return {"ok": True, "coverage": coverage, "signals": signals,
            "block_bounds": block_bounds, "oos_diffs": oos_diffs,
            "oos_score_diff": oos_score_diff, "held_out_diff": held_out_diff}
