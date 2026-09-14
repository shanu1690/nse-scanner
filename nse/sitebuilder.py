"""Build static JSON artifacts for the web dashboard.

`nse-scan site` runs the same pipeline as the CLI report, then writes JSON
under <out>/data/ that the React frontend reads. There is no server: a
GitHub Actions cron job runs this, the frontend is built, and the result is
deployed to GitHub Pages.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone

import pandas as pd

from . import momentum as mom
from .quality.validators import DataValidator, scan_bundle_for_credentials

# Nightly is a fresh-checkout CI job with a 45-min job timeout (build + npm +
# deploy) -- there's no live feed yet (that's Phase 3) to hold to a minutes-
# level staleness bar. What this threshold actually catches today is a build
# that has been silently stuck/retrying for an implausibly long time.
MAX_BUILD_STALENESS_MINUTES = 30


class PublishBlocked(RuntimeError):
    """Raised when DataValidator or the credential scan vetoes a publish.

    The contract (nse/quality/validators.py): a FAIL blocks publishing and
    the previous bundle stays live. In this repo's script-based, CI-driven
    pipeline "stays live" means the CI step fails before the later upload/
    deploy steps run, rather than some background service catching a False.
    """


def _out(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    return path


def _dump(path, obj):
    """Write one JSON file -- but never one carrying a credential-shaped key.

    This is the single choke point every file in the bundle passes through,
    so wiring the credential scan here (rather than at one hand-picked call
    site) covers prices/*.json and chains/*.json too, now and for any call
    site added later.
    """
    payload = json.dumps(obj, indent=1, allow_nan=False)
    leaked = scan_bundle_for_credentials(payload)
    if leaked:
        raise PublishBlocked(
            f"credential-shaped key(s) {leaked} found in {path} -- refusing "
            f"to write it or anything published alongside it"
        )
    with open(_out(path), "w") as fh:
        fh.write(payload)
    return path


def _price_series(sym, df):
    """Compact OHLCV rows for the frontend chart (recent 250 bars)."""
    rows = []
    for idx, r in df.tail(250).iterrows():
        rows.append([
            idx.date().isoformat(),
            round(float(r["Open"]), 2),
            round(float(r["High"]), 2),
            round(float(r["Low"]), 2),
            round(float(r["Close"]), 2),
            int(r.get("Volume", 0) or 0),
        ])
    return rows


def _chain_rows(raw):
    """Compact {strike, expiry, CE/PE OI+IV+premium} rows for the chain viewer."""
    expiry = None
    records = ((raw or {}).get("records") or {})
    expiries = records.get("expiryDates") or []
    if expiries:
        expiry = expiries[0]
    rows = []
    for item in records.get("data") or []:
        ce, pe = item.get("CE") or {}, item.get("PE") or {}
        def leg(l):
            return {
                "oi": float(l.get("openInterest", 0) or 0),
                "d_oi": float(l.get("changeinOpenInterest", 0) or 0),
                "iv": float(l.get("impliedVolatility", 0) or 0),
                "prem": float(l.get("lastPrice", 0) or 0),
            }
        rows.append({
            "strike": float(item.get("strikePrice", 0) or 0),
            "ce": leg(ce),
            "pe": leg(pe),
        })
    return {"expiry": expiry, "spot": records.get("underlyingValue"),
            "rows": rows}


def _delivery_data(sc, prices, bench, fade, top_n):
    """Returns (publishable delivery payload, every symbol that got scored).

    The second value is deliberately the full scanned population, not the
    top_n/min_score-filtered picks list -- a strategy correctly filtering
    140 of 150 names below threshold on a quiet day is not a data-quality
    problem, and validating coverage against the picks list would treat it
    as one on every single run.
    """
    ranked, results = mom.scan_universe(
        prices, bench, min_score=sc.MOM_CFG["min_score"],
        top_n=top_n, fade=fade)
    picks = []
    for r in ranked:
        if r.get("score", 0) < sc.MOM_CFG["min_score"]:
            continue
        picks.append({
            "symbol": r["symbol"],
            "score": round(r["score"], 1),
            "entry": round(r["entry"], 2),
            "stop": round(r["stop"], 2),
            "target1": round(r["target1"], 2),
            "target2": round(r["target2"], 2),
            "reasons": r.get("reasons", []),
        })
    return {"style": "fade" if fade else "momentum", "picks": picks}, results


def _validation_frame(scored_results, prices, fetched_at):
    """The scored-universe frame DataValidator checks before anything publishes.

    analyze_stock()/analyze_fade() don't carry raw volume/high/low (only a
    volume z-score), so those come from each symbol's own OHLCV frame's last
    bar -- the same bar the score was computed from.
    """
    rows = []
    for r in scored_results:
        px = prices.get(r["symbol"])
        if px is None or not len(px):
            continue
        last = px.iloc[-1]
        rows.append({
            "symbol": r["symbol"],
            "close": float(last["Close"]),
            "high": float(last["High"]),
            "low": float(last["Low"]),
            "volume": float(last.get("Volume", 0) or 0),
            "score": r["score"],
            "fetched_at": fetched_at,
        })
    return pd.DataFrame(rows)


def _previous_scored_rows(data_dir):
    """scored_rows from the last successful build's manifest, if any.

    In CI (fresh checkout every run) this is always None -- the continuity
    check only has teeth across successive local/persistent-disk runs until
    the bundle itself is served from somewhere that persists between them.
    """
    try:
        with open(os.path.join(data_dir, "manifest.json")) as fh:
            return json.load(fh).get("scored_rows")
    except (OSError, ValueError):
        return None


def _options_data(sc, prices, top_n, max_seconds, chains_out):
    results = sc.scan_options(prices, top_n=top_n, max_seconds=max_seconds,
                              chains_out=chains_out)
    picks = []
    for r in results:
        if r.get("error") or not r.get("picks"):
            continue
        pick = r["picks"][0]
        chain = {k: r[k] for k in (
            "pcr", "atm_iv", "ivr", "max_pain", "dte", "expected_move_pct",
            "straddle_prem", "total_oi", "d_oi_total", "reasons") if k in r}
        picks.append({
            "symbol": r["symbol"], "score": r["score"],
            "direction": r["direction"],
            "spot": r["spot"], "expiry": r["expiry"],
            "strike": pick["strike"], "premium": pick["premium"],
            "breakeven": pick["breakeven"],
            "lot_size": r.get("lot_size"),
            "amount_per_lot": r.get("amount_per_lot"),
            "chain": chain,
        })
    return picks


def build(out_dir, top_n=12, refresh=False, max_seconds=420, quiet=False):
    """Generate the full data bundle under <out_dir>/data. Returns file list."""
    from . import cli as sc
    from . import data as data_mod
    from . import indicators as ind
    from . import tracker

    t0 = time.time()
    t0_dt = datetime.now(timezone.utc)
    data_dir = os.path.join(out_dir, "data")

    # 1. prices --------------------------------------------------------------
    if refresh:
        if not quiet:
            print("Refreshing price history for the whole universe...",
                  file=sys.stderr)
        bench = data_mod.update_index_history(
            sc.DATA_CFG["index_benchmark"], sc.DATA_CFG["lookback_days"],
            force=True)
        prices = {}
        for sym in sc.SYMBOLS:
            try:
                prices[sym] = data_mod.update_price_history(
                    sym, sc.DATA_CFG["lookback_days"], force=True)
            except (ValueError, RuntimeError) as exc:
                print(f"  ! {sym}: {exc}", file=sys.stderr)
    else:
        prices, bench = sc._prefer_fresh_prices(True)

    fade = sc.MOM_CFG.get("style", "momentum") == "fade"

    # 2. delivery picks -------------------------------------------------------
    delivery, scored_results = _delivery_data(sc, prices, bench, fade, top_n)

    # 2b. data-integrity gate -- veto power over publishing --------------------
    # Run this before anything is written (including the options chain fetch
    # below, which is a live network call per symbol -- no point paying for
    # it if the run isn't going to publish) and before *any* _dump() call, so
    # a FAIL leaves every existing file in <out_dir>/data untouched.
    validation_frame = _validation_frame(scored_results, prices, t0_dt)
    report = DataValidator(max_staleness_minutes=MAX_BUILD_STALENESS_MINUTES).validate(
        validation_frame, universe=sc.SYMBOLS, as_of=datetime.now(timezone.utc),
        previous_row_count=_previous_scored_rows(data_dir),
    )
    print(report.render(), file=sys.stderr)
    if not report.may_publish:
        raise PublishBlocked(
            f"data validation failed -- leaving the existing bundle at "
            f"{data_dir} untouched (see the report above)"
        )

    # 3. option picks + raw chains --------------------------------------------
    chains_out = {}
    options = _options_data(sc, prices, top_n, max_seconds, chains_out)

    # 4. scorecard ------------------------------------------------------------
    scorecard = tracker.scorecard_data(sc.DATA_CFG["lookback_days"]) or {
        "del_headers": [], "del_rows": [], "opt_headers": [], "opt_rows": []}

    # 5. price series for charted symbols -------------------------------------
    charted = sorted({p["symbol"] for p in delivery["picks"]}
                     | {p["symbol"] for p in options})
    price_files = []
    for sym in charted:
        df = prices.get(sym)
        if df is None or not len(df):
            continue
        idx, row = ind.last_snapshot(ind.add_all_indicators(df))
        ema = []
        for i in range(max(0, len(df) - 250), len(df)):
            ema.append([
                df.index[i].date().isoformat(),
                round(float(df["Close"].iloc[i]), 2),
            ])
        price_files.append(_dump(
            os.path.join(data_dir, "prices", f"{sym}.json"),
            {"symbol": sym, "series": _price_series(sym, df),
             "last": {
                 "date": idx.date().isoformat(),
                 "close": round(float(row["Close"]), 2),
                 "ema21": round(float(row["EMA21"]), 2),
                 "ema50": round(float(row["EMA50"]), 2),
                 "ema200": round(float(row["EMA200"]), 2),
                 "rsi14": round(float(row["RSI14"]), 1),
                 "high_52w": round(float(row["HIGH_52W"]), 2),
                 "low_52w": round(float(row["LOW_52W"]), 2),
                 "roc5": round(float(row["ROC5"]), 1),
                 "roc20": round(float(row["ROC20"]), 1),
             }}))
    for sym, raw in chains_out.items():
        _dump(os.path.join(data_dir, "chains", f"{sym}.json"),
              _chain_rows(raw))

    # 6. benchmark -------------------------------------------------------------
    if bench is not None and len(bench):
        _dump(os.path.join(data_dir, "bench.json"),
              {"symbol": sc.DATA_CFG["index_benchmark"],
               "series": _price_series(sc.DATA_CFG["index_benchmark"], bench)})

    files = [
        _dump(os.path.join(data_dir, "delivery.json"), delivery),
        _dump(os.path.join(data_dir, "options.json"),
              {"picks": options}),
        _dump(os.path.join(data_dir, "scorecard.json"), scorecard),
        _dump(os.path.join(data_dir, "manifest.json"), {
            "generated": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "date": time.strftime("%d %b %Y"),
            "provider": sc.DATA_CFG.get("provider", "nse"),
            "universe_size": len(sc.SYMBOLS),
            "delivery_picks": len(delivery["picks"]),
            "option_picks": len(options),
            "scorecard_rows": (len(scorecard["del_rows"])
                               + len(scorecard["opt_rows"])),
            "build_seconds": round(time.time() - t0, 1),
            # For _previous_scored_rows() on the *next* run's continuity
            # check, and as a direct readout of the brief's COVERAGE metric.
            "scored_rows": len(validation_frame),
            "coverage_pct": next(
                (c.detail.get("coverage") for c in report.checks
                 if c.name == "coverage"), None),
        }),
    ]
    if not quiet:
        print(f"Site data written to {data_dir} "
              f"({len(files) + len(price_files)} files, "
              f"{round(time.time() - t0, 1)}s)")
    return files
