"""JSON export of backtest/factor-analysis results, for the dashboard's
Backtest tab (Phase 9). Deliberately a SEPARATE, on-demand snapshot rather
than something the nightly `nse-scan site` build regenerates: a full
walk-forward run against the whole universe takes 15-20+ minutes, and
wiring that into the same CI job as the fast daily price-refresh/publish
risks the job's own timeout on a slow day. `nse-scan backtest --export
PATH` writes this snapshot whenever you choose to refresh it; the
dashboard reads whatever's there (or says plainly that nothing's been
exported yet) rather than pretending a fast build produced a slow answer.

This does NOT reparse backtest.py's rendered text -- it reuses the same
public(-ish) helper functions (_lift_stat, bootstrap_ci, _trade_stats,
label_shuffle_control, ...) backtest.py's own reporting functions call, so
the numbers here are exactly the ones a `nse-scan backtest` run would print,
just structured instead of formatted.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Optional

from . import backtest as bt

__all__ = ["build_backtest_snapshot", "export_backtest_snapshot"]

# Must clear backtest.py's MIN_ROLLING_BLOCKS/MIN_BLOCK_BARS floor (~7.6
# months) with real margin, same reasoning as nse/cli.py's `factors`
# --period default -- see nse/backtest.py's comment on those constants.
FACTOR_ANALYSIS_MONTHS = 10


def _safe_float(x) -> Optional[float]:
    """None for inf/nan -- strict JSON has no representation for either,
    and Python's json module would otherwise emit the non-standard
    `Infinity`/`NaN` tokens, which JS's JSON.parse rejects."""
    if x is None:
        return None
    try:
        x = float(x)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _hit_rate_block(signals, min_score) -> Optional[dict]:
    denom_all = [s for s in signals if s.fwd_pct.get(bt.FIXED_TARGET_HORIZON) is not None]
    sig = [s for s in denom_all if s.score >= min_score]
    if not denom_all or not sig:
        return None
    baseline = sum(1 for s in denom_all
                   if s.fwd_pct[bt.FIXED_TARGET_HORIZON] >= bt.FIXED_TARGET_PCT) / len(denom_all)
    hit = sum(1 for s in sig
              if s.fwd_pct[bt.FIXED_TARGET_HORIZON] >= bt.FIXED_TARGET_PCT) / len(sig)
    ci = bt.bootstrap_ci(signals, lambda b: bt._lift_stat(
        b, min_score, bt.FIXED_TARGET_HORIZON, bt.FIXED_TARGET_PCT))
    return {
        "hit_rate": hit, "baseline": baseline, "lift": hit - baseline,
        "ci": [ci[0], ci[1]] if ci else None,
        "n_signals": len(sig), "n_symbols": len({s.symbol for s in sig}),
        "n_months": len({s.date.strftime("%Y-%m") for s in sig}),
    }


def _trade_block(signals, min_score) -> dict:
    out = {}
    for label, cost in (("cost_0_15pct", bt.COST_ROUND_TRIP_PCT),
                        ("cost_0_30pct", bt.COST_ROBUSTNESS_PCT)):
        stats = bt._trade_stats(signals, min_score, cost)
        if stats is None:
            out[label] = None
            continue
        out[label] = {
            "n_trades": stats["n_trades"], "mean_r": _safe_float(stats["mean_r"]),
            "win_rate": stats["win_rate"], "profit_factor": _safe_float(stats["profit_factor"]),
            "max_drawdown_r": stats["max_drawdown_r"],
            "max_consecutive_losers": stats["max_consecutive_losers"],
            "exit_reasons": stats["exit_reasons"],
        }
    return out


def _robustness_block(signals, min_score) -> Optional[dict]:
    try:
        base_lift = bt._lift_stat(signals, min_score, bt.FIXED_TARGET_HORIZON, bt.FIXED_TARGET_PCT)
    except ZeroDivisionError:
        return None

    out = {"base_lift": base_lift}

    top = bt._top_contributor_symbols(signals, min_score)
    excl_top = [s for s in signals if s.symbol not in top]
    try:
        out["excl_top_contributors"] = {
            "symbols": sorted(top), "lift": bt._lift_stat(
                excl_top, min_score, bt.FIXED_TARGET_HORIZON, bt.FIXED_TARGET_PCT)}
    except ZeroDivisionError:
        out["excl_top_contributors"] = None

    best_month = bt._best_month(signals, min_score)
    if best_month:
        excl_month = [s for s in signals if s.date.strftime("%Y-%m") != best_month]
        try:
            out["excl_best_month"] = {
                "month": best_month, "lift": bt._lift_stat(
                    excl_month, min_score, bt.FIXED_TARGET_HORIZON, bt.FIXED_TARGET_PCT)}
        except ZeroDivisionError:
            out["excl_best_month"] = None
    else:
        out["excl_best_month"] = None

    dates = sorted({s.date for s in signals})
    if len(dates) >= 2:
        mid = dates[len(dates) // 2]
        first_half = [s for s in signals if s.date < mid]
        second_half = [s for s in signals if s.date >= mid]
        try:
            out["first_second_half"] = {
                "first": bt._lift_stat(first_half, min_score, bt.FIXED_TARGET_HORIZON, bt.FIXED_TARGET_PCT),
                "second": bt._lift_stat(second_half, min_score, bt.FIXED_TARGET_HORIZON, bt.FIXED_TARGET_PCT),
            }
        except ZeroDivisionError:
            out["first_second_half"] = None
    else:
        out["first_second_half"] = None

    return out


def _label_shuffle_block(signals, min_score) -> Optional[dict]:
    try:
        real = bt._lift_stat(signals, min_score, bt.FIXED_TARGET_HORIZON, bt.FIXED_TARGET_PCT)
    except ZeroDivisionError:
        return None
    shuffled = bt.label_shuffle_control(signals, min_score)
    if shuffled is None:
        return None
    verdict = "PASS" if abs(shuffled) < abs(real) / 2 or abs(real) < 0.005 else "REVIEW"
    return {"real_lift": real, "shuffled_lift": shuffled, "verdict": verdict}


def build_backtest_snapshot(months: "int | None" = 6, min_score: float = 60.0,
                             fade: "bool | None" = None) -> dict:
    """The full structured payload for backtest.json. `fade=None` reads the
    configured style from config.yaml, matching run_backtest()'s own CLI
    default."""
    generated_at = datetime.now(timezone.utc).isoformat()
    fade = bt._CONFIG["scanner"]["momentum"].get("style", "momentum") == "fade" if fade is None else fade
    universe = bt._CONFIG["universe"]["symbols"]

    result = bt.run_backtest(min_score=min_score, months=months, quiet=True, fade=fade)
    if not result["ok"]:
        return {"ok": False, "generated_at": generated_at, "message": result["message"]}

    held_out, rolling = result["held_out"], result["rolling"]
    n_blocks = len(result["block_bounds"])

    factor_result = bt.run_factor_analysis(months=FACTOR_ANALYSIS_MONTHS, min_score=55.0)
    factors = None
    if factor_result.get("ok"):
        factors = {
            f: {"n_oos_rolls": len(diffs), "mean_diff_pct": _safe_float(
                sum(diffs) / len(diffs) if diffs else None)}
            for f, diffs in factor_result["oos_diffs"].items()
        }

    return {
        "ok": True,
        "generated_at": generated_at,
        "style": "fade" if fade else "momentum",
        "min_score": min_score,
        "months": months,
        "universe_size": len(universe),
        "n_blocks": n_blocks,
        "coverage": {
            "scored": len(result["coverage"].scored),
            "universe": len(result["coverage"].universe),
            "ratio": result["coverage"].coverage_ratio,
        },
        "held_out": {
            "hit_rate": _hit_rate_block(held_out, min_score),
            "trades": _trade_block(held_out, min_score),
            "robustness": _robustness_block(held_out, min_score),
            "label_shuffle": _label_shuffle_block(held_out, min_score),
            "window": [held_out[0].date.date().isoformat(), held_out[-1].date.date().isoformat()]
                      if held_out else None,
        },
        "rolling": {
            "hit_rate": _hit_rate_block(rolling, min_score),
            "trades": _trade_block(rolling, min_score),
        },
        "factor_analysis": factors,
    }


def export_backtest_snapshot(path: str, months: "int | None" = 6, min_score: float = 60.0,
                              fade: "bool | None" = None) -> dict:
    snapshot = build_backtest_snapshot(months=months, min_score=min_score, fade=fade)
    with open(path, "w") as fh:
        json.dump(snapshot, fh, indent=2)
    return snapshot
