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

import numpy as np
import yaml

from .. import backtest as bt
from .. import data as data_mod
from .classify import build_regime_history

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

N_LABEL_PERMUTATIONS = 1000
# How often a random reassignment of the SAME regime labels to the SAME
# dates must fail to match the real lift, before "regime beats static" is
# trusted. Added after a live run: the bootstrap-CI bar alone (regime_ci[0]
# > 0 and regime_lift > static_lift) passed on a real held-out block whose
# permutation p-value came back at 0.056 -- i.e. random label-to-date
# reassignment produced a lift this large about 1 time in 18, which is not
# a result worth calling a win. See _regime_label_permutation_test().
PERMUTATION_P_THRESHOLD = 0.05

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


def _regime_label_permutation_test(momentum_signals, fade_signals, regime_history,
                                    min_score: float, real_lift: float,
                                    n_perm: int = N_LABEL_PERMUTATIONS,
                                    seed: int = bt.BOOTSTRAP_SEED) -> "tuple[float, int] | None":
    """Reassign the SAME multiset of regime labels to the SAME dates at
    random (preserving how many days were trending_up/mean_reverting/
    trending_down/high_vol_shock -- only WHICH day got which label is
    shuffled), rebuild the regime-routed signal set under that shuffled
    assignment, and see how often a lift at least as large as `real_lift`
    turns up by chance. This is the regime-specific analogue of
    backtest.py's label_shuffle_control(): that test shuffles OUTCOMES to
    check a score isn't paired with the wrong future; this one shuffles
    LABEL ASSIGNMENT to check the regime rule isn't just getting credit for
    a fortunate date split. Returns (p_value, n_valid_permutations), or
    None if there are too few distinct dates/labels to permute at all.
    """
    dates = sorted(set(regime_history.index) &
                   (set(s.date for s in fade_signals) | set(s.date for s in momentum_signals)))
    if len(dates) < 10:
        return None
    real_labels = [regime_history.loc[d, "regime"] for d in dates]
    if len(set(real_labels)) < 2:
        return None  # every day classified the same way -- nothing to shuffle

    rng = np.random.default_rng(seed)
    null_lifts = []
    for _ in range(n_perm):
        shuffled = rng.permutation(real_labels)
        label_map = dict(zip(dates, shuffled))
        fake_history = regime_history.copy()
        fake_history["regime"] = [label_map.get(d, fake_history.loc[d, "regime"])
                                   for d in fake_history.index]
        try:
            signals = _regime_style_signals(momentum_signals, fade_signals, fake_history)
            null_lifts.append(bt._lift_stat(signals, min_score, bt.FIXED_TARGET_HORIZON,
                                             bt.FIXED_TARGET_PCT))
        except ZeroDivisionError:
            continue
    if not null_lifts:
        return None
    null_lifts = np.array(null_lifts)
    p_value = float((null_lifts >= real_lift).mean())
    return p_value, len(null_lifts)


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
    perm_result = _regime_label_permutation_test(
        held_out_mom, held_out_fade, regime_history, min_score, regime_lift)

    # Conservative verdict, per Section 4.4: the regime rule's own lift must
    # be numerically better, its bootstrap CI's lower bound must clear zero,
    # AND the label-permutation test must show the real lift isn't something
    # a random date-to-label reassignment produces almost as often anyway --
    # added after a live run passed the first two bars with a permutation
    # p-value of 0.056 (see PERMUTATION_P_THRESHOLD). Anything short of all
    # three keeps the static rule.
    passes_ci_bar = (regime_ci is not None and regime_ci[0] > 0 and regime_lift > static_lift)
    passes_permutation_bar = perm_result is not None and perm_result[0] < PERMUTATION_P_THRESHOLD
    beats_static = passes_ci_bar and passes_permutation_bar

    static_ci_str = f"[{static_ci[0]:+.1%}, {static_ci[1]:+.1%}]" if static_ci else "n/a"
    regime_ci_str = f"[{regime_ci[0]:+.1%}, {regime_ci[1]:+.1%}]" if regime_ci else "n/a"
    regime_counts = _regime_label_counts(held_out_fade + held_out_mom, regime_history)
    if perm_result is not None:
        p_value, n_valid_perm = perm_result
        perm_str = f"p={p_value:.3f} (n={n_valid_perm} label permutations)"
    else:
        perm_str = "n/a (too few distinct dates/labels to permute)"

    if beats_static:
        verdict = "REGIME-CONDITIONAL beats the static rule"
    elif passes_ci_bar and not passes_permutation_bar:
        verdict = ("NO -- cleared the bootstrap-CI bar but FAILED the label-permutation "
                   "check (a random date-to-label reassignment produces a lift this large "
                   "too often to trust) -- keeping the static rule")
    else:
        verdict = "NO clear out-of-sample improvement -- keeping the static rule"

    lines = [
        f"Regime-conditional switching vs static style ('{'fade' if static_fade else 'momentum'}'), "
        f"held-out block only ({bt.FIXED_TARGET_HORIZON}d/+{bt.FIXED_TARGET_PCT:.0f}% lift vs matched baseline):",
        f"  Static ('{'fade' if static_fade else 'momentum'}' always):  lift {static_lift:+.1%}  "
        f"95% CI {static_ci_str}  (n={len(static_signals)})",
        f"  Regime-conditional:                lift {regime_lift:+.1%}  "
        f"95% CI {regime_ci_str}  (n={len(regime_signals)})",
        f"  Label-permutation test: {perm_str}  "
        f"(threshold: p < {PERMUTATION_P_THRESHOLD})",
        f"  Held-out regime mix: {regime_counts}",
        f"  Verdict: {verdict}",
        "  (bar: regime lift's 95% CI lower bound must clear zero, exceed the static "
        "lift's point estimate, AND survive the label-permutation check -- a merely-"
        "higher point estimate with a CI that clears zero is not enough on its own, "
        "since that alone was fooled by a fortunate date split in a real run)",
    ]
    text = "\n".join(lines)
    if not quiet:
        print(text)

    return {
        "ok": True, "text": text, "beats_static": beats_static,
        "passes_ci_bar": passes_ci_bar, "passes_permutation_bar": passes_permutation_bar,
        "permutation_p_value": perm_result[0] if perm_result else None,
        "static_lift": static_lift, "static_ci": static_ci,
        "regime_lift": regime_lift, "regime_ci": regime_ci,
        "regime_counts": regime_counts,
        "static_signals": static_signals, "regime_signals": regime_signals,
    }
