"""nse-scanner CLI.

Usage:
  nse-scan scan --mode all|momentum|options [--refresh] [--top N] [--fade|--no-fade]
  nse-scan backtest [--period 6m] [--min-score 60] [--fade|--no-fade]
  nse-scan factors [--period 3] [--min-score 55]
  nse-scan fusion [--period N] [--fundamentals-db path]
  nse-scan regime [--period N]
  nse-scan track [--status] [--top N] [--fade|--no-fade]
  nse-scan watch SYMBOL
  nse-scan universe            # list current universe
  nse-scan site                # emit JSON data for the web dashboard
"""


import argparse
import os
import sys
import time

import yaml

from nse import data as data_mod
from nse import indicators as ind
from nse import momentum as mom
from nse import nse_api
from nse import options as opt
from nse import scoring

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
with open(os.path.join(ROOT, "config.yaml")) as fh:
    CONFIG = yaml.safe_load(fh)

SYMBOLS = CONFIG["universe"]["symbols"]
SCAN_CFG = CONFIG["scanner"]
MOM_CFG = SCAN_CFG["momentum"]
OPT_CFG = SCAN_CFG["options"]
DATA_CFG = CONFIG["data"]

def _load_prices(refresh=False):
    bench = data_mod.update_index_history(DATA_CFG["index_benchmark"],
                                          DATA_CFG["lookback_days"])
    prices = {}
    for sym in SYMBOLS:
        try:
            prices[sym] = data_mod.update_price_history(sym, DATA_CFG["lookback_days"])
        except (ValueError, RuntimeError) as exc:
            print(f"  ! {sym}: {exc}", file=sys.stderr)
    return prices, bench


def _load_cached_prices():
    bench = data_mod.load_price_history(DATA_CFG["index_benchmark"])
    prices = {}
    for sym in SYMBOLS:
        df = data_mod.load_price_history(sym)
        if df is not None and len(df) >= 60:
            prices[sym] = df
    return prices, bench


def _prefer_fresh_prices(include_momentum, update_missing=True):
    cached, bench = _load_cached_prices()
    missing = [s for s in SYMBOLS if s not in cached]
    if missing and update_missing:
        print(f"Updating {len(missing)} symbols missing from cache (Yahoo rate limits; may need 2 runs)...")
        for s in missing:
            try:
                cached[s] = data_mod.update_price_history(s, DATA_CFG["lookback_days"])
            except (ValueError, RuntimeError) as exc:
                print(f"  ! {s}: {exc}", file=sys.stderr)
            time.sleep(1.2)
    elif missing:
        print(f"Note: {len(missing)} symbols missing from cache (skipping update).")
    return cached, bench


def cmd_scan(args):
    from tabulate import tabulate
    fade = args.fade if args.fade is not None else MOM_CFG.get("style", "momentum") == "fade"
    if args.refresh:
        prices, bench = _load_prices()
    else:
        prices, bench = _prefer_fresh_prices(True)
    print(f"Universe: {len(prices)} symbols with history\n")

    tables = []
    if args.mode in ("momentum", "all"):
        style = "FADE / CONTRARIAN (mean-reversion)" if fade else "DELIVERY / MOMENTUM"
        print("=" * 70)
        print(style)
        print("=" * 70)
        ranked, all_results = mom.scan_universe(
            prices, bench, min_score=MOM_CFG["min_score"],
            top_n=args.top or MOM_CFG["top_n"], fade=fade,
        )
        if not ranked:
            print("No signals above threshold.")
        else:
            header, rows = scoring.delivery_table(all_results, MOM_CFG["min_score"])
            print(tabulate(rows, headers=header, tablefmt="grid"))
            tables.append({"title": "Delivery scan", "headers": header,
                           "rows": rows, "kind": "delivery"})
            print()
            print("Top picks with reasons:")
            for r in ranked[:8]:
                print(scoring.explain(r))
                print()

    if args.mode in ("options", "all"):
        print("=" * 70)
        print("OPTIONS SCAN (nearest expiry, F&O universe)")
        print("=" * 70)
        option_results = scan_options(prices, top_n=args.top or OPT_CFG["top_n"])
        if option_results:
            header, rows = scoring.options_table(option_results)
            print(tabulate(rows, headers=header, tablefmt="grid"))
            tables.append({"title": "Options scan", "headers": header,
                           "rows": rows, "kind": "options"})
            print()
            print("Signal reasons:")
            for r in option_results[:10]:
                if not r.get("error"):
                    print(scoring.explain({**r, "reasons": r["reasons"]}))
                    print()
        else:
            print("No option chains available (market may be closed / rate limited).")

    return tables


def _new_options_session():
    """Return (session, exception_type) for the configured data provider."""
    if DATA_CFG.get("provider", "nse") == "smartapi":
        from nse import smartapi
        return smartapi.get_shared_session(), smartapi.SmartAPIUnavailable
    return nse_api.NSESession(cache_ttl_minutes=15), nse_api.NSEUnavailable


def scan_options(prices, top_n=12, max_seconds=None, chains_out=None):
    """Pull option chains for the most promising F&O names only (rate-limit aware).

    max_seconds: hard time budget for the report path so a blocked provider
    never hangs the EOD email.
    chains_out: optional dict filled with {symbol: raw_chain} for the web
    dashboard's chain viewer.
    """
    api, exc_type = _new_options_session()
    t0 = time.time()
    try:
        # Pre-screen: rank universe by momentum score, then scan the top names.
        ranked, _ = mom.scan_universe(prices, None, min_score=0, top_n=top_n * 2)
        selected = [r["symbol"] for r in ranked[: top_n + 4]]

        results = []
        for sym in selected:
            if max_seconds and time.time() - t0 > max_seconds:
                print(f"  ! time budget reached, stopping at {len(results)} chains",
                      file=sys.stderr)
                break
            try:
                raw = api.option_chain_equity(sym)
                if chains_out is not None:
                    chains_out[sym] = raw
                trend_df = prices.get(sym)
                trend = None
                if trend_df is not None:
                    idx, row = ind.last_snapshot(ind.add_all_indicators(trend_df))
                    trend = row
                a = opt.analyze_option_chain(sym, raw, trend=trend)
                results.append(a)
            except (exc_type, ValueError) as exc:
                print(f"  ! {sym}: {exc}", file=sys.stderr)
                time.sleep(1)
        return results
    finally:
        api.close()


def cmd_backtest(args):
    from nse.backtest import run_backtest
    fade = args.fade if args.fade is not None else MOM_CFG.get("style", "momentum") == "fade"
    run_backtest(min_score=args.min_score, months=args.period, fade=fade)


def cmd_factors(args):
    from nse.backtest import run_factor_analysis
    run_factor_analysis(months=args.period, min_score=args.min_score)


def cmd_fusion(args):
    from nse.fusion import run_fusion_audit
    result = run_fusion_audit(months=args.period, fundamentals_db=args.fundamentals_db)
    if not result["ok"]:
        sys.exit(1)


def cmd_regime(args):
    from nse.regime import run_regime_switch_audit
    result = run_regime_switch_audit(months=args.period)
    if not result["ok"]:
        sys.exit(1)


def cmd_watch(args):
    symbol = args.symbol.upper()
    if symbol not in SYMBOLS:
        print(f"{symbol} not in universe; attempting fetch anyway.")
    df = data_mod.update_price_history(symbol, DATA_CFG["lookback_days"])
    bench = data_mod.update_index_history(DATA_CFG["index_benchmark"],
                                          DATA_CFG["lookback_days"])
    from nse import indicators as ind
    full = ind.add_all_indicators(df)
    idx, row = ind.last_snapshot(full)
    print(f"=== {symbol} as of {idx.date()} ===")
    print(f"CMP {row['Close']:.2f} | RSI {row['RSI14']:.1f} | ATR {row['ATR14']:.2f}")
    print(f"EMA21 {row['EMA21']:.2f} | EMA50 {row['EMA50']:.2f} | EMA200 {row['EMA200']:.2f}")
    print(f"52w high {row['HIGH_52W']:.2f} | 52w low {row['LOW_52W']:.2f}")
    print(f"ROC 1d {row['ROC1']:.1f}% | 5d {row['ROC5']:.1f}% | 20d {row['ROC20']:.1f}%")
    print(f"Volume z {row['VOL_Z']:.1f} | MACD hist {row['MACD_HIST']:.3f}")
    print()
    if symbol.replace("^NSEI", "") and row["HIGH_52W"]:
        print(f"52w distance: {(row['Close']/row['HIGH_52W']-1)*100:.1f}%")

    print("\nDelivery analysis:")
    a = mom.analyze_stock(full)
    if a:
        print(scoring.explain({**a, "symbol": symbol}))

    print("\nOptions (nearest expiry):")
    api, exc_type = _new_options_session()
    try:
        raw = api.option_chain_equity(symbol)
        o = opt.analyze_option_chain(symbol, raw, trend=row)
        if o.get("error"):
            print(f"  {o['error']}")
        else:
            for k in ("spot", "expiry", "dte", "pcr", "max_pain", "atm_iv",
                      "ivr", "expected_move_pct", "straddle_prem", "d_oi_total", "direction"):
                print(f"  {k}: {o[k]}")
            print("  picks:")
            for p in o.get("picks", []):
                print(f"    strike {p['strike']} prem {p['premium']} breakeven {p['breakeven']} iv {p['iv']}")
            for reason in o["reasons"]:
                print(f"  * {reason}")
    except (exc_type, ValueError) as exc:
        print(f"  n/a: {exc}")
    finally:
        api.close()


def cmd_news(args):
    """Fetch the configured RSS feeds and store new items (point-in-time,
    deduped by URL). With --symbol, also prints what's stored for it."""
    import datetime

    from nse.news import rss_ingest
    from nse.news.store import NewsStore

    db_path = args.db or os.path.join(ROOT, "data", "news.db")
    items = rss_ingest.fetch_all(SYMBOLS)
    with NewsStore(db_path) as store:
        added = store.put(items)
        print(f"Fetched {len(items)} items from {len(rss_ingest.FEEDS)} feeds, "
              f"{added} new (deduped by URL). Store: {db_path}")
        if args.symbol:
            sym = args.symbol.upper()
            rows = store.as_of(sym, datetime.datetime.now(datetime.timezone.utc),
                               limit=args.limit)
            if not rows:
                print(f"No stored news for {sym}.")
            for r in rows:
                est = " (estimated timestamp)" if r["published_at_estimated"] else ""
                print(f"\n  [{r['published_at']}{est}] {r['source']}: {r['headline']}")
                print(f"    {r['summary']}")
                print(f"    {r['url']}")


def cmd_universe(_args):
    print(f"Universe: {len(SYMBOLS)} symbols")
    for s in SYMBOLS:
        print(f"  {s}")


def cmd_site(args):
    """Emit JSON data for the web dashboard into <out>/data."""
    from nse import sitebuilder
    sitebuilder.build(
        out_dir=args.out,
        top_n=args.top or OPT_CFG["top_n"],
        refresh=args.refresh,
        max_seconds=args.max_seconds,
    )


def _today_delivery_picks(prices, bench, fade, top_n):
    from nse import tracker
    ranked, _ = mom.scan_universe(
        prices, bench, min_score=MOM_CFG["min_score"], top_n=top_n, fade=fade,
    )
    out = []
    for r in ranked:
        out.append({
            "type": "delivery", "symbol": r["symbol"], "score": r["score"],
            "entry": r["entry"], "stop": r["stop"],
            "target1": r["target1"], "target2": r["target2"],
            "style": "fade" if fade else "momentum",
        })
    return out


def _today_option_picks(prices, top_n, max_seconds=None):
    out = []
    results = scan_options(prices, top_n=top_n, max_seconds=max_seconds)
    for r in results:
        if r.get("error") or not r.get("picks"):
            continue
        pick = r["picks"][0]
        out.append({
            "type": "options", "symbol": r["symbol"], "score": r["score"],
            "direction": r["direction"], "strike": pick["strike"],
            "premium": pick["premium"], "breakeven": pick["breakeven"],
            "spot": r["spot"], "expiry": r["expiry"],
            "lot_size": r.get("lot_size"),
            "amount_per_lot": r.get("amount_per_lot"),
        })
    return out


def cmd_track(args):
    from nse import tracker
    fade = args.fade if args.fade is not None else MOM_CFG.get("style", "momentum") == "fade"
    if not args.status:
        prices, bench = _prefer_fresh_prices(True)
        picks = _today_delivery_picks(prices, bench, fade,
                                      args.top or MOM_CFG["top_n"])
        picks += _today_option_picks(prices, args.top or OPT_CFG["top_n"])
        added = tracker.save_picks(picks)
        print(f"Tracked today's picks: {len(added)} new "
              f"({len(picks)} generated total for today).")
        print()
    print(tracker.scorecard(DATA_CFG["lookback_days"]))


def cmd_report(args):
    from nse import report, tracker
    fade = args.fade if args.fade is not None else MOM_CFG.get("style", "momentum") == "fade"
    prices, bench = _prefer_fresh_prices(True, update_missing=False)
    delivery = _today_delivery_picks(prices, bench, fade, args.top or MOM_CFG["top_n"])
    options = _today_option_picks(prices, args.top or OPT_CFG["top_n"], max_seconds=420)
    tracker.save_picks(delivery + options)
    scorecard = tracker.scorecard(DATA_CFG["lookback_days"])
    scorecard_rows = tracker.scorecard_data(DATA_CFG["lookback_days"])

    from tabulate import tabulate
    dheaders = ["SYMBOL", "SCORE", "ENTRY", "STOP", "T1", "T2"]
    droids = [[p["symbol"], p["score"], p["entry"], p["stop"], p["target1"], p["target2"]]
              for p in delivery]
    dtable = tabulate(droids, headers=dheaders, tablefmt="grid",
                      floatfmt=".2f") if delivery else ""
    oheaders = ["SYMBOL", "DIR", "STRIKE", "PREM", "BREAKEVEN", "EXPIRY",
                "LOTSIZE", "TOTAL_AMT"]
    orows = [[o["symbol"], o["direction"], o["strike"], o["premium"],
              o["breakeven"], o["expiry"],
              o["lot_size"] if o.get("lot_size") else "-",
              o["amount_per_lot"] if o.get("amount_per_lot") is not None else "-"]
             for o in options]
    otable = tabulate(orows, headers=oheaders, tablefmt="grid",
                      floatfmt=".2f") if options else ""

    subject = f"NSE Scanner report - {time.strftime('%d %b %Y')}"
    body = report.render(subject, dtable, otable, scorecard)
    morning = getattr(args, "morning", False)
    if morning:
        subject = f"MORNING LIST - buy today - {time.strftime('%d %b %Y')}"
        body = (
            "HOW TO BUY TODAY (before you place any order):\n"
            "  1. Pick only 1-2 names from the delivery list below.\n"
            "  2. Place a LIMIT order at or below the ENTRY price.\n"
            "  3. SKIP any stock that opens more than ~1.5% above ENTRY "
            "(the move already happened - do not chase).\n"
            "  4. Place your STOP and TARGETS from the table, then leave it alone.\n"
            "  5. Options: only if you fully understand premium loss. "
            "Entry = spot near today's open, exit before breakeven erodes.\n"
            "==========================================================\n\n"
            + body
        )
    path = report.save_report(body, subject)
    print(f"Report saved: {path}")

    html_body = report.render_html(
        subject,
        {"headers": dheaders, "rows": droids,
         "fmt": ["text"] + ["num"] * 5},
        {"headers": oheaders, "rows": orows,
         "fmt": ["text", "text"] + ["num"] * 6},
        scorecard_rows, morning=morning,
    )

    cfg = report.load_secrets()
    sent = []
    if args.email:
        to = report.send_email(subject, body, cfg["email"], html_body=html_body)
        sent.append(f"email -> {to}")
    if args.ntfy:
        status = report.send_ntfy(subject, body, cfg["ntfy"])
        sent.append(f"ntfy push (http {status})")
    if sent:
        print("Sent:", ", ".join(sent))
    else:
        print("Not sent (no --email/--ntfy). Re-run with --email to mail the report.")


def main():
    parser = argparse.ArgumentParser(prog="scanner.py", description="NSE delivery + options scanner")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_scan = sub.add_parser("scan", help="run the daily scanner")
    p_scan.add_argument("--mode", choices=["all", "momentum", "options"], default="all")
    p_scan.add_argument("--refresh", action="store_true", help="force refresh price cache")
    p_scan.add_argument("--top", type=int, default=None)
    p_scan.add_argument("--fade", action=argparse.BooleanOptionalAction, default=None,
                        help="fade/contrarian style instead of momentum (default from config)")
    p_scan.set_defaults(func=cmd_scan)

    p_bt = sub.add_parser("backtest", help="validate the scanner historically")
    p_bt.add_argument("--period", type=int, default=6, help="months of history (default 6)")
    p_bt.add_argument("--min-score", type=float, default=60.0)
    p_bt.add_argument("--fade", action=argparse.BooleanOptionalAction, default=None,
                      help="backtest fade/contrarian style (default from config)")
    p_bt.set_defaults(func=cmd_backtest)

    p_f = sub.add_parser("factors", help="which sub-signals actually predict moves")
    p_f.add_argument("--period", type=int, default=3, help="months of history (default 3)")
    p_f.add_argument("--min-score", type=float, default=55.0)
    p_f.set_defaults(func=cmd_factors)

    p_fu = sub.add_parser("fusion", help="Phase 6: calibrated fusion model audit (Brier, "
                          "reliability, lift vs the technical-score baseline)")
    p_fu.add_argument("--period", type=int, default=None,
                      help="months of history (default: all available)")
    p_fu.add_argument("--fundamentals-db", default=None,
                      help="fundamentals PointInTimeStore path (default: data/pit.db)")
    p_fu.set_defaults(func=cmd_fusion)

    p_rg = sub.add_parser("regime", help="Phase 6: regime-conditional style switching vs "
                          "the static rule, out-of-sample")
    p_rg.add_argument("--period", type=int, default=None,
                      help="months of history (default: all available)")
    p_rg.set_defaults(func=cmd_regime)

    p_w = sub.add_parser("watch", help="deep-dive one symbol")
    p_w.add_argument("symbol")
    p_w.set_defaults(func=cmd_watch)

    p_u = sub.add_parser("universe", help="list universe")
    p_u.set_defaults(func=cmd_universe)

    p_news = sub.add_parser("news", help="fetch RSS news and store it (point-in-time)")
    p_news.add_argument("--db", default=None, help="news store path (default: data/news.db)")
    p_news.add_argument("--symbol", default=None,
                        help="print stored news for one symbol after fetching")
    p_news.add_argument("--limit", type=int, default=10)
    p_news.set_defaults(func=cmd_news)

    p_site = sub.add_parser("site", help="emit JSON data for the web dashboard")
    p_site.add_argument("--out", default="site", help="output dir (default: site)")
    p_site.add_argument("--top", type=int, default=None)
    p_site.add_argument("--max-seconds", type=int, default=420,
                        help="time budget for live option chains (default 420)")
    p_site.add_argument("--refresh", action="store_true",
                        help="force-refetch every symbol's price history")
    p_site.set_defaults(func=cmd_site)

    p_t = sub.add_parser("track", help="save today's picks + show their scorecard")
    p_t.add_argument("--status", action="store_true",
                     help="skip saving; just refresh and show the scorecard")
    p_t.add_argument("--top", type=int, default=None)
    p_t.add_argument("--fade", action=argparse.BooleanOptionalAction, default=None,
                     help="fade/contrarian style instead of momentum (default from config)")
    p_t.set_defaults(func=cmd_track)

    p_r = sub.add_parser("report", help="build today's EOD report and deliver it")
    p_r.add_argument("--email", action="store_true", help="mail the report via SMTP")
    p_r.add_argument("--ntfy", action="store_true", help="push to phone via ntfy.sh")
    p_r.add_argument("--top", type=int, default=None)
    p_r.add_argument("--fade", action=argparse.BooleanOptionalAction, default=None,
                     help="fade/contrarian style instead of momentum (default from config)")
    p_r.add_argument("--morning", action="store_true",
                     help="morning list mode: adds buy-today checklist + subject")
    p_r.set_defaults(func=cmd_report)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
