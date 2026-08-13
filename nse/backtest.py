"""Walk-forward validation: did the scanner actually find movers?

For every point in each symbol's history, we compute the momentum score using
ONLY data available up to that day, then measure what actually happened over
the next 1/3/5 sessions. Output = real hit-rates for +3%/+5%/+10% moves,
vs a random-stock baseline so you can judge whether the edge is real.
"""

import os

import numpy as np
import pandas as pd
import yaml

from . import data as data_mod
from . import indicators as ind
from . import momentum as mom

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "config.yaml")) as fh:
    _CONFIG = yaml.safe_load(fh)

BASELINE_FWD = (3, 5, 10)
HORIZONS = (1, 3, 5)
WARMUP = 252  # bars needed so HIGH_52W/EMA200/ROC60 are non-NaN at test start


def _backtest_symbol(symbol, df, min_score, step=5, test_start=WARMUP, bench_full=None, fade=False):
    full = ind.add_all_indicators(df)
    close = full["Close"]
    n = len(full)
    records = []

    for i in range(test_start, n - 6, step):
        window = full.iloc[: i + 1]
        bench20 = bench60 = None
        if bench_full is not None:
            b = bench_full[bench_full.index <= full.index[i]].dropna()
            if len(b):
                brow = b.iloc[-1]
                bench20, bench60 = brow.get("ROC20"), brow.get("ROC60")
        if fade:
            a = mom.analyze_fade(window, bench20, bench60)
        else:
            a = mom.analyze_stock(window, bench20, bench60)
        if a is None:
            continue
        score = a["score"]
        fwd = {}
        for h in HORIZONS:
            if i + h < n:
                fwd[h] = (close.iloc[i + h] / close.iloc[i] - 1) * 100
        records.append({
            "symbol": symbol,
            "date": full.index[i],
            "score": score,
            "subscores": a.get("subscores", {}),
            "reasons": a.get("reasons", []),
            **{f"fwd{h}": fwd.get(h) for h in HORIZONS},
        })
    return records


def _collect_records(min_score=55.0, months=3, symbols=None, fade=False):
    """Walk-forward records with score + subscores + forward returns."""
    universe = symbols or _CONFIG["universe"]["symbols"]
    lookback = months * 22
    bench_df = data_mod.load_price_history(_CONFIG["data"]["index_benchmark"])
    bench_full = ind.add_all_indicators(bench_df) if bench_df is not None else None
    all_records = []
    for sym in universe:
        df_all = data_mod.load_price_history(sym)
        if df_all is None or len(df_all) < WARMUP + lookback:
            continue
        df = df_all.iloc[-(WARMUP + lookback):]
        try:
            all_records.extend(_backtest_symbol(sym, df, min_score, bench_full=bench_full, fade=fade))
        except Exception:
            continue
    return all_records


def run_factor_analysis(months=3, min_score=55.0):
    """Which sub-factors actually predict forward moves? Splits each factor at
    its median and compares mean 5d forward return of the high vs low bucket.
    Honest check before trusting any composite score."""
    records = _collect_records(months=months, min_score=min_score)
    if not records:
        print("Not enough data.")
        return
    df = pd.DataFrame(records)
    df["fwd5"] = df["fwd5"].fillna(0.0)

    rows = []
    for comp in ("trend", "breakout", "momentum", "volume", "relative_strength"):
        vals = pd.to_numeric(df["subscores"].apply(lambda s: s.get(comp, 0)))
        med = vals.median()
        hi = df.loc[vals > med, "fwd5"]
        lo = df.loc[vals <= med, "fwd5"]
        rows.append([comp, f"{med:.0f}",
                     f"{hi.mean():+.2f}% (n={len(hi)})",
                     f"{lo.mean():+.2f}% (n={len(lo)})",
                     f"{hi.mean() - lo.mean():+.2f}%"])

    score_hi = df.loc[df["score"] >= min_score, "fwd5"]
    score_lo = df.loc[df["score"] < min_score, "fwd5"]
    rows.append(["score>=threshold", f"{min_score:.0f}",
                 f"{score_hi.mean():+.2f}% (n={len(score_hi)})",
                 f"{score_lo.mean():+.2f}% (n={len(score_lo)})",
                 f"{score_hi.mean() - score_lo.mean():+.2f}%"])

    print(f"Factor analysis: {months} months | {len(df)} walk-forward records")
    print("Mean 5-day forward return by factor bucket (split at median):\n")
    from tabulate import tabulate
    print(tabulate(rows, headers=["factor", "med", "high bucket", "low bucket", "diff"],
                   tablefmt="grid", floatfmt=".2f"))
    print("\nPositive diff = factor predicts upside. Negative = it's a fade signal.")
    print("Re-weight momentum.py accordingly, then re-run `backtest` to confirm.")


def run_backtest(min_score=60.0, months=6, symbols=None, quiet=False, fade=False):
    universe = symbols or _CONFIG["universe"]["symbols"]
    lookback = months * 22  # test-window bars (warm-up bars are prepended)
    hit = {f"fwd{h}": {t: 0 for t in BASELINE_FWD} for h in HORIZONS}
    baseline = {h: {t: 0 for t in BASELINE_FWD} for h in HORIZONS}
    n_signals = 0
    n_baseline = 0

    bench_df = data_mod.load_price_history(_CONFIG["data"]["index_benchmark"])
    bench_full = ind.add_all_indicators(bench_df) if bench_df is not None else None

    for sym in universe:
        df_all = data_mod.load_price_history(sym)
        if df_all is None or len(df_all) < WARMUP + lookback:
            continue
        df = df_all.iloc[-(WARMUP + lookback):]
        try:
            records = _backtest_symbol(sym, df, min_score, bench_full=bench_full, fade=fade)
        except Exception:
            continue
        for rec in records:
            n_baseline += 1
            for h in HORIZONS:
                fwd = rec.get(f"fwd{h}")
                if fwd is None:
                    continue
                for t in BASELINE_FWD:
                    if fwd >= t:
                        baseline[h][t] += 1
            if rec["score"] >= min_score:
                n_signals += 1
                for h in HORIZONS:
                    fwd = rec.get(f"fwd{h}")
                    if fwd is None:
                        continue
                    for t in BASELINE_FWD:
                        if fwd >= t:
                            hit[f"fwd{h}"][t] += 1

    if n_signals == 0 or n_baseline == 0:
        print("Not enough data to backtest. Run `python scanner.py scan` once first.")
        return

    print(f"Backtest: {months} months | {'FADE' if fade else 'MOMENTUM'} | threshold score >= {min_score}")
    print(f"Signals tested: {n_signals} | all-stock baseline: {n_baseline}\n")

    header = ["Horizon", "+3% hit", "+5% hit", "+10% hit", "avg sign"]
    rows = []
    for h in HORIZONS:
        hh = hit[f"fwd{h}"]
        bb = baseline[h]
        rows.append([
            f"{h}d (signal)", f"{hh[3]/n_signals*100:.1f}%", f"{hh[5]/n_signals*100:.1f}%",
            f"{hh[10]/n_signals*100:.1f}%", "-",
        ])
        rows.append([
            f"{h}d (baseline)", f"{bb[3]/n_baseline*100:.1f}%",
            f"{bb[5]/n_baseline*100:.1f}%", f"{bb[10]/n_baseline*100:.1f}%", "-",
        ])
    from tabulate import tabulate
    print(tabulate(rows, headers=header, tablefmt="grid"))
    print("\nInterpretation: if signal hit-rates clearly beat the baseline, the")
    print("scanner has a real edge. If not, raise min_score and re-test.")
    print("Tip: `python scanner.py factors` shows which sub-signals predict best.")
