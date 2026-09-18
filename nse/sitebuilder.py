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
from . import sectors
from .quality.validators import DataValidator, scan_bundle_for_credentials
from .risk import RiskLimits, enforce_delivery_picks

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


def _rnd(v, digits=2):
    return round(float(v), digits) if v is not None and not pd.isna(v) else None


def _price_series(sym, full):
    """Compact OHLCV + overlay-indicator rows for the frontend candlestick
    chart (recent 250 bars). `full` is the INDICATOR-ENRICHED frame
    (ind.add_all_indicators' output), not the raw OHLCV df -- EMA/Donchian
    need to be plotted for every bar on the chart, not just read off the
    last one. Appends new fields after the original [date, o, h, l, c, v]
    shape rather than reordering it, so PriceChart.jsx's existing
    series[i][4]-style indexing keeps working unchanged.
    """
    rows = []
    for idx, r in full.tail(250).iterrows():
        rows.append([
            idx.date().isoformat(),
            _rnd(r["Open"]), _rnd(r["High"]), _rnd(r["Low"]), _rnd(r["Close"]),
            int(r.get("Volume", 0) or 0),
            _rnd(r.get("EMA21")), _rnd(r.get("EMA50")), _rnd(r.get("EMA200")),
            _rnd(r.get("DC_HIGH20")), _rnd(r.get("DC_LOW20")),
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


def _options_data(sc, prices, top_n, max_seconds, chains_out, risk_limits, n_open_start=0):
    """Phase 7's select_option_idea() (budget-capped strategy: spread
    preferred over naked long, cost/max-loss/breakeven/probability/payoff/
    thesis) for every symbol scan_options() pulled a chain for, independently
    re-verified by Phase 8's enforce_option_ideas() -- "defence in depth",
    never trusting options.py's own cap check alone -- then ranked by
    modeled probability (never premium; rule 3). cross_source_oi=False on
    the second analyze_option_chain() pass: scan_options()'s own call
    already ran cross_source_d_oi() on this same `raw` dict (mutated in
    place), so a second cross-source fetch would be a wasted network call.
    """
    from . import indicators as ind
    from . import options as opt
    from .risk import enforce_option_ideas

    sc.scan_options(prices, top_n=top_n, max_seconds=max_seconds, chains_out=chains_out)

    idea_results = []
    for sym, raw in chains_out.items():
        trend_df = prices.get(sym)
        trend = None
        if trend_df is not None and len(trend_df):
            _, trend = ind.last_snapshot(ind.add_all_indicators(trend_df))
        idea_results.append(opt.select_option_idea(
            sym, raw, trend=trend, budget=risk_limits.options_budget_cap,
            cross_source_oi=False))

    risk_result = enforce_option_ideas(idea_results, risk_limits, n_open_start=n_open_start)
    if risk_result.vetoes:
        print(risk_result.render(), file=sys.stderr)

    picks = []
    for r in opt.rank_ideas(risk_result.approved):
        idea, analysis = r["idea"], r["analysis"]
        chain = {k: analysis[k] for k in (
            "pcr", "atm_iv", "ivr", "max_pain", "expected_move_pct",
            "straddle_prem", "total_oi", "d_oi_total", "reasons") if k in analysis}
        picks.append({
            "symbol": r["symbol"], "score": analysis["score"], "direction": analysis["direction"],
            "spot": analysis["spot"], "expiry": analysis["expiry"], "dte": analysis["dte"],
            "strategy": idea["strategy"], "legs": idea["legs"],
            "cost": idea["cost"], "max_loss": idea["max_loss"], "max_profit": idea["max_profit"],
            "breakeven": idea["breakeven"], "probability": idea["probability"],
            "lot_size": idea["lot_size"], "payoff": idea["payoff"], "thesis": idea["thesis"],
            "chain": chain,
        })

    # Candidates vetoed only for CAPACITY (a slot was full, not that the idea
    # was bad) -- persisted so the intraday job (nse/intraday.py) can
    # re-admit one later in the same session if a slot frees up, without
    # re-fetching option chains. Real disqualifications (budget cap blown,
    # cost mismatch) are NOT carried forward -- those don't become valid
    # just because room opened up.
    by_symbol = {r["symbol"]: r for r in idea_results}
    vetoed_capacity = []
    for v in risk_result.vetoes:
        if v.rule != "max_open_ideas":
            continue
        r = by_symbol.get(v.symbol)
        if r is None or r.get("idea") is None:
            continue
        idea, analysis = r["idea"], r["analysis"]
        vetoed_capacity.append({
            "symbol": r["symbol"], "score": analysis["score"], "direction": analysis["direction"],
            "spot": analysis["spot"], "expiry": analysis["expiry"], "dte": analysis["dte"],
            "strategy": idea["strategy"], "legs": idea["legs"], "cost": idea["cost"],
            "max_loss": idea["max_loss"], "max_profit": idea["max_profit"],
            "breakeven": idea["breakeven"], "probability": idea["probability"],
            "lot_size": idea["lot_size"], "veto_rule": v.rule, "veto_detail": v.detail,
        })
    return picks, risk_result, vetoed_capacity


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

    # 2c. risk gate -- per-pick veto power, not an all-or-nothing block --------
    # Unlike DataValidator above (a data-integrity problem means don't trust
    # ANYTHING), a risk-limit breach is about ONE pick, not the whole bundle:
    # risk-manager.md has "veto power over any pick that breaches limits",
    # not over the publish itself. Picks that fail here are dropped from
    # delivery["picks"] (never silently -- see the rendered report) while
    # everything else still publishes. sector_map comes from nse/sectors.py's
    # cache (config/sector_map.json, refreshed via `nse-scan refresh-sectors`,
    # real NSE classification -- see that module for how it was found); a
    # symbol missing from the cache (never refreshed yet, or NSE has nothing
    # for it) still shows up in the risk report's sector_unknown list rather
    # than being silently treated as compliant.
    risk_limits = RiskLimits.from_config(sc.CONFIG)
    sector_map = sectors.sector_only_map(sectors.load_sector_map())
    picks_by_score = sorted(delivery["picks"], key=lambda p: p["score"], reverse=True)
    risk_result = enforce_delivery_picks(picks_by_score, risk_limits, price_frames=prices,
                                         sector_map=sector_map)
    print(risk_result.render(), file=sys.stderr)
    delivery["picks"] = risk_result.approved

    # Candidates vetoed only for CAPACITY (portfolio heat, sector cap, max
    # open ideas, correlation) rather than a genuine data problem -- carried
    # forward so the intraday job (nse/intraday.py) can re-admit one later
    # in the same session if a slot frees up (e.g. an earlier pick gets
    # stopped out), without re-scoring the whole universe. See Rule 8 /
    # PROJECT_BRIEF.md Section 4.4: new calls only at defined decision
    # points, but re-admitting an already-scored, already-qualified
    # candidate when room appears is not "generating a new call".
    CAPACITY_VETO_RULES = {"portfolio_heat", "max_open_ideas", "sector_cap", "correlation_cap"}
    by_symbol_score = {p["symbol"]: p for p in picks_by_score}
    delivery["vetoed_capacity"] = [
        {**by_symbol_score[v.symbol], "veto_rule": v.rule, "veto_detail": v.detail}
        for v in risk_result.vetoes
        if v.rule in CAPACITY_VETO_RULES and v.symbol in by_symbol_score
    ]

    # 3. option picks + raw chains --------------------------------------------
    chains_out = {}
    options, options_risk_result, options_vetoed_capacity = _options_data(
        sc, prices, top_n, max_seconds, chains_out, risk_limits,
        n_open_start=len(risk_result.approved))

    # 4. scorecard ------------------------------------------------------------
    scorecard = tracker.scorecard_data(sc.DATA_CFG["lookback_days"]) or {
        "del_headers": [], "del_rows": [], "opt_headers": [], "opt_rows": []}

    # 5. price series for charted symbols -------------------------------------
    delivery_levels = {p["symbol"]: p for p in delivery["picks"]}
    charted = sorted({p["symbol"] for p in delivery["picks"]}
                     | {p["symbol"] for p in options})
    price_files = []
    for sym in charted:
        df = prices.get(sym)
        if df is None or not len(df):
            continue
        full = ind.add_all_indicators(df)
        idx, row = ind.last_snapshot(full)
        lvl = delivery_levels.get(sym)
        price_files.append(_dump(
            os.path.join(data_dir, "prices", f"{sym}.json"),
            {"symbol": sym, "series": _price_series(sym, full),
             # Entry/stop/target horizontal lines -- Section 9's chart spec
             # -- only present for symbols that are an actual delivery pick.
             "levels": ({"entry": lvl["entry"], "stop": lvl["stop"],
                        "target1": lvl["target1"], "target2": lvl["target2"]}
                       if lvl else None),
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
              {"picks": options, "vetoed_capacity": options_vetoed_capacity}),
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
            "risk_gate": {
                "delivery_approved": len(risk_result.approved),
                "delivery_vetoed": len(risk_result.vetoes),
                "sector_cap_unverified_symbols": len(risk_result.sector_unknown),
                "options_approved": len(options_risk_result.approved),
                "options_vetoed": len(options_risk_result.vetoes),
            },
        }),
    ]
    if not quiet:
        print(f"Site data written to {data_dir} "
              f"({len(files) + len(price_files)} files, "
              f"{round(time.time() - t0, 1)}s)")
    return files
