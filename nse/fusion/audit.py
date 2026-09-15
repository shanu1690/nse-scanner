"""End-to-end fusion audit (Phase 6): assemble technical + regime +
fundamental features, fit + calibrate a logistic model on genuinely
disjoint train/calibration/held-out block groups (reusing nse/backtest.py's
walk-forward block planning and sample-size gate), and report:

  - Brier score, measured against the base-rate-always floor
    (PROJECT_BRIEF.md Section 1's CALIBRATION criterion)
  - a reliability curve by decile of predicted probability
  - lift vs the existing technical-score baseline, cluster-bootstrap CI'd,
    both rules given the SAME number of picks on the held-out block so the
    comparison is "same budget, which selection method finds more true
    positives" rather than an arbitrary probability cutoff
  - top-5 contributing reasons for a sample of the held-out block's
    highest-probability picks

The regime-conditional-vs-static STYLE comparison (momentum vs fade) is a
separate, independent deliverable -- see nse/regime/compare.py -- because
it answers a different question (which SCORING STYLE to trust when) than
this module's calibrated-probability model does.
"""

from __future__ import annotations

import os

import numpy as np
import pandas as pd
import yaml

from .. import backtest as bt
from .. import data as data_mod
from ..pit.store import PointInTimeStore
from ..regime.classify import build_regime_history
from . import calibration as cal
from . import features as ft
from .model import FusionModel

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
VIX_TICKER = "^INDIAVIX"

# Row-count floors below which a logistic fit / calibration / held-out
# evaluation isn't trustworthy -- same "refuse rather than force a number"
# philosophy as backtest.py's block-count gate, applied to the fusion
# model's own train/calib/held-out row counts.
MIN_TRAIN_ROWS = 200
MIN_CALIB_ROWS = 40
MIN_HELD_OUT_ROWS = 40
ISOTONIC_MIN_ROWS = 1000  # below this, sigmoid/Platt is the safer calibration choice
TOP_REASONS_N = 5
TOP_PICKS_DEMO_N = 5


def _load_config():
    with open(os.path.join(ROOT, "config.yaml")) as fh:
        return yaml.safe_load(fh)


def _load_price_frames(universe):
    frames = {}
    for sym in universe:
        df = data_mod.load_price_history(sym)
        if df is not None:
            frames[sym] = df
    return frames


def _default_fundamentals_db():
    return os.path.join(ROOT, "data", "pit.db")


def _sample_size_gate(n_train, n_calib, n_held, quiet) -> "str | None":
    shortfalls = []
    if n_train < MIN_TRAIN_ROWS:
        shortfalls.append(f"train={n_train} (need >={MIN_TRAIN_ROWS})")
    if n_calib < MIN_CALIB_ROWS:
        shortfalls.append(f"calibration={n_calib} (need >={MIN_CALIB_ROWS})")
    if n_held < MIN_HELD_OUT_ROWS:
        shortfalls.append(f"held-out={n_held} (need >={MIN_HELD_OUT_ROWS})")
    if not shortfalls:
        return None
    msg = ("Not enough labeled rows for a trustworthy fusion fit: " + ", ".join(shortfalls) +
           ". Refusing to report a fusion result rather than fit/calibrate/evaluate off a "
           "sample too thin to mean anything.")
    if not quiet:
        print(msg)
    return msg


def _score_lookup(signals):
    return {(s.symbol, s.date): s.score for s in signals}


def _same_budget_lift(scores: np.ndarray, probs: np.ndarray, labels: np.ndarray,
                       min_score: float) -> float:
    """Hit rate of the fusion model's top-K predicted-probability rows minus
    the hit rate of the existing technical-score rule's own rows, where K is
    however many rows the score>=min_score rule itself selects -- "same
    number of picks, which selection method finds more true positives",
    rather than comparing against an arbitrary probability cutoff."""
    baseline_mask = scores >= min_score
    k = int(baseline_mask.sum())
    if k == 0 or k == len(scores):
        raise ZeroDivisionError
    baseline_rate = labels[baseline_mask].mean()
    order = np.argsort(-probs)
    fusion_mask = np.zeros(len(scores), dtype=bool)
    fusion_mask[order[:k]] = True
    fusion_rate = labels[fusion_mask].mean()
    return float(fusion_rate - baseline_rate)


def run_fusion_audit(months=None, quiet: bool = False, fundamentals_db: "str | None" = None) -> dict:
    config = _load_config()
    universe = config["universe"]["symbols"]
    min_score = config["scanner"]["momentum"]["min_score"]
    fade = config["scanner"]["momentum"].get("style", "momentum") == "fade"

    coverage = bt.CoverageReport(universe=list(universe))
    signals, block_bounds = bt._collect_signals(universe, months, fade, coverage)

    if block_bounds is None:
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
    if n_blocks < 4:
        msg = (f"Only {n_blocks} walk-forward block(s) available; the fusion audit needs "
               f"train + calibration + held-out as three DISJOINT block groups (at least "
               f"2 rolling + 1 calibration + 1 held-out = 4 blocks total -- the same shape "
               f"backtest.py's own gate already requires).")
        if not quiet:
            print(msg)
        return {"ok": False, "reason": "insufficient_blocks", "message": msg}

    train_block_idxs = set(range(0, n_blocks - 2))
    calib_block_idx = n_blocks - 2
    held_out_block_idx = n_blocks - 1

    train_signals = [s for s in signals if s.block in train_block_idxs]
    calib_signals = [s for s in signals if s.block == calib_block_idx]
    held_out_signals = [s for s in signals if s.block == held_out_block_idx]

    # ---- regime history (full coverage, no fundamentals dependency) -----
    bench_df = data_mod.load_price_history(config["data"]["index_benchmark"])
    vix_df = data_mod.load_price_history(VIX_TICKER)
    if vix_df is None:
        vix_df = data_mod.update_index_history(VIX_TICKER, config["data"]["lookback_days"])
    price_frames = _load_price_frames(universe)
    regime_history = build_regime_history(bench_df, vix_df, price_frames)

    # ---- fundamentals store (optional -- honestly absent if not ingested)
    db_path = fundamentals_db or _default_fundamentals_db()
    store = PointInTimeStore(db_path) if os.path.exists(db_path) else None
    if store is None and not quiet:
        print(f"(no fundamentals store found at {db_path} -- fusion runs on technical + "
              f"regime features only; every fundamental column is reported as missing, "
              f"not silently dropped)")

    try:
        X_train, y_train, meta_train = ft.build_feature_frame(
            train_signals, regime_history, store=store, price_frames=price_frames)
        X_calib, y_calib, meta_calib = ft.build_feature_frame(
            calib_signals, regime_history, store=store, price_frames=price_frames)
        X_held, y_held, meta_held = ft.build_feature_frame(
            held_out_signals, regime_history, store=store, price_frames=price_frames)
    finally:
        if store is not None:
            store.close()

    gate_msg = _sample_size_gate(len(X_train), len(X_calib), len(X_held), quiet)
    if gate_msg is not None:
        return {"ok": False, "reason": "insufficient_rows", "message": gate_msg,
                "n_train": len(X_train), "n_calib": len(X_calib), "n_held_out": len(X_held)}

    X_train_f, X_calib_f, X_held_f, medians = ft.impute_nullable(X_train, X_calib, X_held)
    fundamental_coverage = {
        name: float(1.0 - X_train[name].isna().mean()) for name in ft.FUNDAMENTAL_FEATURES
    }

    model = FusionModel().fit(X_train_f, y_train)
    calib_method = "isotonic" if len(X_calib_f) >= ISOTONIC_MIN_ROWS else "sigmoid"
    model.calibrate(X_calib_f, y_calib, method=calib_method)

    p_held = model.predict_proba(X_held_f)
    p_held_base = model.base_predict_proba(X_held_f)

    brier_calibrated = cal.brier_score(y_held, p_held)
    brier_uncalibrated = cal.brier_score(y_held, p_held_base)
    brier_floor = cal.brier_against_base_rate(y_train, y_held)
    beats_floor = brier_calibrated < brier_floor

    reliability = cal.reliability_curve(y_held, p_held, n_bins=10)
    monotonic = cal.is_monotonic(reliability, tolerance=0.03)

    score_lookup = _score_lookup(held_out_signals)
    held_scores = np.array([score_lookup[(r.symbol, r.date)]
                             for r in meta_held.itertuples()])

    try:
        point_lift = _same_budget_lift(held_scores, p_held, y_held, min_score)
    except ZeroDivisionError:
        point_lift = None

    lift_ci = None
    if point_lift is not None:
        EvalRow = _eval_row_type()
        held_symbols = meta_held["symbol"].to_numpy()
        eval_rows = [EvalRow(symbol=held_symbols[i], score=held_scores[i],
                              prob=p_held[i], label=int(y_held[i]))
                     for i in range(len(p_held))]

        def _stat(rows):
            scores = np.array([r.score for r in rows])
            probs = np.array([r.prob for r in rows])
            labels = np.array([r.label for r in rows])
            return _same_budget_lift(scores, probs, labels, min_score)

        lift_ci = bt.bootstrap_ci(eval_rows, _stat)

    # ---- top-5 reasons demo: the held-out block's highest-probability picks
    order = np.argsort(-p_held)[:TOP_PICKS_DEMO_N]
    demo_picks = []
    for idx in order:
        contributions = model.top_contributions(X_held_f.iloc[idx], n=TOP_REASONS_N)
        demo_picks.append({
            "symbol": meta_held.iloc[idx]["symbol"],
            "date": meta_held.iloc[idx]["date"],
            "probability": float(p_held[idx]),
            "reasons": contributions,
        })

    text = _format_report(
        n_train=len(X_train_f), n_calib=len(X_calib_f), n_held=len(X_held_f),
        calib_method=calib_method, brier_calibrated=brier_calibrated,
        brier_uncalibrated=brier_uncalibrated, brier_floor=brier_floor,
        beats_floor=beats_floor, reliability=reliability, monotonic=monotonic,
        point_lift=point_lift, lift_ci=lift_ci, min_score=min_score,
        fundamental_coverage=fundamental_coverage, demo_picks=demo_picks,
        has_fundamentals=store is not None, coverage=coverage,
    )
    if not quiet:
        print(text)

    return {
        "ok": True, "text": text, "model": model,
        "n_train": len(X_train_f), "n_calib": len(X_calib_f), "n_held_out": len(X_held_f),
        "calib_method": calib_method,
        "brier_calibrated": brier_calibrated, "brier_uncalibrated": brier_uncalibrated,
        "brier_floor": brier_floor, "beats_floor": beats_floor,
        "reliability": reliability, "monotonic": monotonic,
        "point_lift": point_lift, "lift_ci": lift_ci,
        "fundamental_coverage": fundamental_coverage, "demo_picks": demo_picks,
        "coverage": coverage, "has_fundamentals": store is not None,
    }


def _eval_row_type():
    from collections import namedtuple
    return namedtuple("FusionEvalRow", "symbol score prob label")


def _format_report(*, n_train, n_calib, n_held, calib_method, brier_calibrated,
                    brier_uncalibrated, brier_floor, beats_floor, reliability,
                    monotonic, point_lift, lift_ci, min_score, fundamental_coverage,
                    demo_picks, has_fundamentals, coverage) -> str:
    lines = [
        f"Fusion model audit -- train={n_train} / calibration={n_calib} / "
        f"held-out={n_held} rows (disjoint walk-forward blocks)",
        coverage.render(),
        "",
        f"Fundamental leg: {'ingested store found' if has_fundamentals else 'NO STORE FOUND -- technical+regime only'}",
    ]
    if has_fundamentals:
        for name, cov in fundamental_coverage.items():
            lines.append(f"    {name:25s} {cov:.1%} of training rows had a real value "
                         f"(rest imputed + flagged missing)")
    lines.append("")
    lines.append(f"Calibration method: {calib_method} "
                 f"(isotonic needs >={ISOTONIC_MIN_ROWS} calibration rows; sigmoid/Platt used below that)")
    lines.append(f"  Brier (calibrated):    {brier_calibrated:.4f}")
    lines.append(f"  Brier (uncalibrated):  {brier_uncalibrated:.4f}")
    lines.append(f"  Brier (base-rate floor, from TRAIN's rate applied to held-out): {brier_floor:.4f}")
    lines.append(f"  {'BEATS' if beats_floor else 'DOES NOT beat'} the base-rate floor "
                 f"-- {'a real' if beats_floor else 'no'} calibration edge over guessing the historical rate")
    lines.append("")
    lines.append(f"Reliability curve (held-out, {len(reliability)} bin(s)):")
    for b in reliability:
        lines.append(f"    bin {b['bin']}: n={b['n']:4d}  mean predicted {b['mean_predicted']:.3f}  "
                     f"observed {b['observed_freq']:.3f}")
    lines.append(f"  Monotonic (within 3pp noise tolerance): {'YES' if monotonic else 'NO'}")
    lines.append("")
    if point_lift is None:
        lines.append("Lift vs technical-score baseline: not computable (no signals cleared "
                     "the score threshold in the held-out block)")
    else:
        ci_str = f"[{lift_ci[0]:+.1%}, {lift_ci[1]:+.1%}]" if lift_ci else "n/a (too few symbols to bootstrap)"
        lines.append(f"Lift vs technical-score baseline (same pick COUNT, held-out block):")
        lines.append(f"  Fusion top-K probability picks vs score>={min_score} picks: "
                     f"{point_lift:+.1%}  95% CI {ci_str}")
        lines.append("  (CI excluding zero = the fusion model's ranking finds real signal "
                     "beyond the technical score alone; CI spanning zero = no demonstrated "
                     "improvement yet)")
    lines.append("")
    lines.append(f"Sample reasons (top {len(demo_picks)} held-out picks by probability):")
    for pick in demo_picks:
        lines.append(f"  {pick['symbol']} ({pick['date'].date()}) P={pick['probability']:.2f}")
        for r in pick["reasons"]:
            lines.append(f"      {r['label']}: pushes probability {r['direction']} "
                         f"({r['contribution']:+.2f})")
    return "\n".join(lines)
