"""Composite ranking + human-readable output helpers."""

from tabulate import tabulate

from .options import FULL_IVR_DAYS


def _ivr_cell(r):
    """IVR% cell: '62 (5d)' while the rank is still maturing, '62' once matured."""
    if r.get("ivr") is None:
        return "-"
    days = r.get("ivr_days") or 0
    if days < FULL_IVR_DAYS:
        return f"{r['ivr']:.0f} ({days}d)"
    return f"{r['ivr']:.0f}"


def delivery_table(results, min_score=55.0):
    """Structured (headers, rows) for the delivery watchlist. Cells are strings."""
    keeps = [r for r in results if r["score"] >= min_score]
    header = ["SYMBOL", "SCORE", "CMP", "ENTRY", "STOP", "TGT2",
              "R:R", "RISK%", "52W_DIST%", "VOL_Z", "ACTION"]
    rows = []
    for r in keeps:
        if r.get("fade"):
            action = "FADE-BUY" if r["score"] >= 70 else ("FADE-WATCH" if r["score"] >= min_score else "AVOID")
        else:
            action = "BUY" if r["score"] >= 70 else ("WATCH" if r["score"] >= min_score else "AVOID")
        rows.append([
            r["symbol"], f"{r['score']:.2f}", f"{r['price']:.2f}",
            f"{r['entry']:.2f}", f"{r['stop']:.2f}", f"{r['target2']:.2f}",
            f"{r['reward_risk']:.1f}", f"{r['risk_pct']:.2f}",
            f"{r['dist_52w_pct']:.2f}", f"{r['vol_z']:.2f}", action,
        ])
    return header, rows


def format_delivery(results, min_score=55.0):
    """Return (table, keeps) for the delivery watchlist."""
    header, rows = delivery_table(results, min_score)
    keeps = [r for r in results if r["score"] >= min_score]
    return tabulate(rows, headers=header, tablefmt="grid"), keeps


def options_table(results, top_n=None):
    """Structured (headers, rows) for the options scan. Cells are strings."""
    header = ["SYMBOL", "SCORE", "DIR", "SPOT", "EXPIRY", "DTE", "PCR",
              "ATM_IV", "IVR%", "EXP_MOVE%", "dOI", "LOTSIZE", "TOTAL_AMT",
              "PICK", "PREM", "BREAKEVEN"]
    rows = []
    for r in (results[:top_n] if top_n else results):
        if r.get("error"):
            continue
        pick = r["picks"][0] if r.get("picks") else {}
        rows.append([
            r["symbol"], f"{r['score']:.1f}", r["direction"], f"{r['spot']:.2f}",
            r["expiry"], str(r["dte"]),
            f"{r['pcr']:.2f}" if r.get("pcr") is not None else "-",
            f"{r['atm_iv']:.1f}" if r.get("atm_iv") is not None else "-",
            _ivr_cell(r),
            f"{r['expected_move_pct']:.1f}" if r.get("expected_move_pct") is not None else "-",
            f"{r['d_oi_total']:,.0f}" if r.get("d_oi_total") is not None else "-",
            str(r.get("lot_size")) if r.get("lot_size") else "-",
            f"{r['amount_per_lot']:,.0f}" if r.get("amount_per_lot") is not None else "-",
            str(pick.get("strike")) if pick else "-",
            f"{pick['premium']:.2f}" if pick else "-",
            f"{pick['breakeven']:.2f}" if pick else "-",
        ])
    return header, rows


def format_options(results, top_n=12):
    """Return (table, keeps) for the options scan."""
    header, rows = options_table(results, top_n)
    keeps = [r for r in results if not r.get("error")]
    return tabulate(rows, headers=header, tablefmt="grid"), keeps


def explain(result):
    """Compact per-symbol reasons block."""
    lines = [f"--- {result['symbol']} (score {result['score']}) ---"]
    lines += [f"  * {reason}" for reason in result["reasons"]]
    if "entry" in result:
        lines.append(f"  Trade: buy {result['entry']} / stop {result['stop']} "
                     f"/ T1 {result['target1']} / T2 {result['target2']} "
                     f"(risk {result['risk_pct']}%, R:R {result['reward_risk']})")
    return "\n".join(lines)


def merge_watchlist(delivery, options):
    """Combine both scans into a single action table."""
    d_by = {r["symbol"]: r for r in delivery}
    o_by = {r["symbol"]: r for r in options if not r.get("error")}
    symbols = sorted(set(d_by) | set(o_by))
    rows = []
    for sym in symbols:
        d, o = d_by.get(sym), o_by.get(sym)
        action = "-"
        if o and o["direction"] in ("CE", "PE"):
            action = f"{o['direction']} option"
        elif d and d["score"] >= 70:
            action = "DELIVERY BUY"
        elif d and d["score"] >= 55:
            action = "WATCH"
        rows.append([
            sym,
            d["score"] if d else "-",
            o["direction"] if o else "-",
            o["pcr"] if o else "-",
            o["ivr"] if o else "-",
            action,
        ])
    header = ["SYMBOL", "DEL_SCORE", "OPT_DIR", "PCR", "IVR%", "RECOMMENDATION"]
    return tabulate(rows, headers=header, tablefmt="grid")
