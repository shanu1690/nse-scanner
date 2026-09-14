"""Regime-conditional strategy switching vs the static style, out-of-sample.

PROJECT_BRIEF.md Section 4.4 is explicit about the bar this has to clear:
"[regime detection] must prove out-of-sample that regime-conditional
switching beats picking one style and holding it. If it can't, ship the
static rule and say so." This module runs exactly that comparison, on the
same held-out block nse/backtest.py itself never uses for anything else,
and defaults to the conservative verdict when the evidence is not clear.
"""

from __future__ import annotations

import os

import yaml

from .. import backtest as bt
from .. import data as data_mod
from .classify import build_regime_history

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# In a shock regime, Section 4.4 calls for "reduced or zero exposure", not a
# same-style call -- so this maps a regime label to which SIGNAL SET to use
# (True = fade, False = momentum), or None to generate no new signal at all
# that date (mirrors "between decision points, only invalidation alerts").
REGIME_STYLE = {
    "trending_up": False,
    "trending_down": True,
    "mean_reverting": True,
    "high_vol_shock": None,
    "unknown": True,   # no usable classification -- fall back to the static rule
}

VIX_TICKER = "^INDIAVIX"


def _load_config():
    with open(os.path.join(ROOT, "config.yaml")) as fh:
        return yaml.safe_load(fh)


def _by_date(signals):
    out: dict = {}
    for s in signals:
        out.setdefault(s.date, []).append(s)
    return out


def _static_style_signals(momentum_signals, fade_signals, static_fade: bool) -> list:
    return fade_signals if static_fade else momentum_signals


def _regime_style_signals(momentum_signals, fade_signals, regime_history) -> list:
    """For each date either signal set has a row for, use REGIME_STYLE's
    call for that date's classified regime; a high_vol_shock date
    contributes no signals at all (reduced/zero exposure)."""
    fade_by_date = _by_date(fade_signals)
    mom_by_date = _by_date(momentum_signals)
    out = []
    for d in set(fade_by_date) | set(mom_by_date):
        label = regime_history.loc[d, "regime"] if d in regime_history.index else "unknown"
        use_fade = REGIME_STYLE.get(label, True)
        if use_fade is None:
            continue
        out.extend(fade_by_date.get(d, []) if use_fade else mom_by_date.get(d, []))
    return out


def _load_price_frames(universe):
    frames = {}
    for sym in universe:
        df = data_mod.load_price_history(sym)
        if df is not None:
            frames[sym] = df
    return frames


def _regime_label_counts(signals, regime_history) -> dict:
    counts: dict = {}
    for d in {s.date for s in signals}:
        label = regime_history.loc[d, "regime"] if d in regime_history.index else "unknown"
        counts[label] = counts.get(label, 0) + 1
    return counts


def run_regime_switch_audit(months=None, quiet=False) -> dict:
    config = _load_config()
    universe = config["universe"]["symbols"]
    static_fade = config["scanner"]["momentum"].get("style", "momentum") == "fade"
    min_score = config["scanner"]["momentum"]["min_score"]

    fade_signals, block_bounds = bt._collect_signals(
        universe, months, True, bt.CoverageReport(universe=list(universe)))
    momentum_signals, block_bounds_mom = bt._collect_signals(
        universe, months, False, bt.CoverageReport(universe=list(universe)))

    if block_bounds is None or block_bounds_mom is None:
        bench_df = data_mod.load_price_history(config["data"]["index_benchmark"])
        bench_total = len(bench_df) if bench_df is not None else 0
        n_avail = max(0, bench_total - bt.WARMUP)
        if months is not None:
            n_avail = min(n_avail, months * 22)
        msg = bt._sample_size_gate_message(n_avail, bench_total, config["data"]["lookback_days"])
        if not quiet:
            print(msg)
        return {"ok": False, "reason": "insufficient_history", "message": msg}

    n_blocks = len(block_bounds)
    held_out_fade = [s for s in fade_signals if s.block == n_blocks - 1]
    held_out_mom = [s for s in momentum_signals if s.block == n_blocks - 1]

    bench_df = data_mod.load_price_history(config["data"]["index_benchmark"])
    vix_df = data_mod.load_price_history(VIX_TICKER)
    if vix_df is None:
        vix_df = data_mod.update_index_history(VIX_TICKER, config["data"]["lookback_days"])
    price_frames = _load_price_frames(universe)
    regime_history = build_regime_history(bench_df, vix_df, price_frames)

    static_signals = _static_style_signals(held_out_mom, held_out_fade, static_fade)
    regime_signals = _regime_style_signals(held_out_mom, held_out_fade, regime_history)

    def _lift(batch):
        return bt._lift_stat(batch, min_score, bt.FIXED_TARGET_HORIZON, bt.FIXED_TARGET_PCT)

    try:
        static_lift = _lift(static_signals)
    except ZeroDivisionError:
        msg = "Not enough held-out static-style signals to compute a lift; cannot compare."
        if not quiet:
            print(msg)
        return {"ok": False, "reason": "insufficient_signals", "message": msg}
    try:
        regime_lift = _lift(regime_signals)
    except ZeroDivisionError:
        msg = ("Not enough held-out regime-conditional signals to compute a lift "
               "(often means the held-out block was mostly classified high_vol_shock, "
               "which generates no new signals by design) -- cannot compare; "
               "keeping the static rule by default.")
        if not quiet:
            print(msg)
        return {"ok": False, "reason": "insufficient_signals", "message": msg}

    static_ci = bt.bootstrap_ci(static_signals, _lift)
    regime_ci = bt.bootstrap_ci(regime_signals, _lift)

    # Conservative verdict, per Section 4.4: only call it a win if the
    # regime rule's own lift is both numerically better AND its bootstrap
    # CI's lower bound clears zero (a real, not just directionally lucky,
    # edge) -- anything short of that keeps the static rule.
    beats_static = (regime_ci is not None and regime_ci[0] > 0 and regime_lift > static_lift)

    static_ci_str = f"[{static_ci[0]:+.1%}, {static_ci[1]:+.1%}]" if static_ci else "n/a"
    regime_ci_str = f"[{regime_ci[0]:+.1%}, {regime_ci[1]:+.1%}]" if regime_ci else "n/a"
    regime_counts = _regime_label_counts(held_out_fade + held_out_mom, regime_history)

    lines = [
        f"Regime-conditional switching vs static style ('{'fade' if static_fade else 'momentum'}'), "
        f"held-out block only ({bt.FIXED_TARGET_HORIZON}d/+{bt.FIXED_TARGET_PCT:.0f}% lift vs matched baseline):",
        f"  Static ('{'fade' if static_fade else 'momentum'}' always):  lift {static_lift:+.1%}  "
        f"95% CI {static_ci_str}  (n={len(static_signals)})",
        f"  Regime-conditional:                lift {regime_lift:+.1%}  "
        f"95% CI {regime_ci_str}  (n={len(regime_signals)})",
        f"  Held-out regime mix: {regime_counts}",
        f"  Verdict: {'REGIME-CONDITIONAL beats the static rule' if beats_static else 'NO clear out-of-sample improvement -- keeping the static rule'}",
        "  (bar: regime lift's 95% CI lower bound must clear zero AND exceed the "
        "static lift's point estimate -- a merely-higher point estimate is not enough)",
    ]
    text = "\n".join(lines)
    if not quiet:
        print(text)

    return {
        "ok": True, "text": text, "beats_static": beats_static,
        "static_lift": static_lift, "static_ci": static_ci,
        "regime_lift": regime_lift, "regime_ci": regime_ci,
        "regime_counts": regime_counts,
        "static_signals": static_signals, "regime_signals": regime_signals,
    }
