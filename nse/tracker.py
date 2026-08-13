"""Pick journal: save today's scanner picks and scorecard them on later runs.

This is the "honesty ledger" - you save what the scanner recommended, then every
time you run `track` it measures how those picks actually did (P&L vs entry,
stop/target hits, option breakevens) instead of letting the good calls be
remembered and the bad ones forgotten.
"""

import datetime
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JOURNAL = os.path.join(ROOT, "data", "journal.json")

from tabulate import tabulate


def _pid(p):
    return f"{p['saved']}:{p['symbol']}:{p['type']}"


def load_picks():
    if not os.path.exists(JOURNAL):
        return []
    with open(JOURNAL) as fh:
        return json.load(fh)


def save_picks(picks, date=None):
    """Append today's picks, de-duplicated by (date, symbol, type)."""
    date = date or datetime.date.today().isoformat()
    existing = load_picks()
    ids = {_pid(p) for p in existing}
    added = []
    for p in picks:
        p = dict(p)
        p["saved"] = date
        if _pid(p) not in ids:
            existing.append(p)
            added.append(p)
    with open(JOURNAL, "w") as fh:
        json.dump(existing, fh, indent=2)
    return added


def _fresh_close(symbol, lookback_days):
    """Latest close, refreshing only when the cached copy is stale.

    Refreshing every journal pick live is slow and trips provider rate
    limits, so a recent cache (updated within the last few days) is reused.
    """
    from . import data as data_mod
    df = data_mod.load_price_history(symbol)
    stale = df is None or not len(df)
    if not stale:
        age = (datetime.date.today() - df.index.max().date()).days
        stale = age > 3
    if stale:
        try:
            df = data_mod.update_price_history(symbol, lookback_days, force=True)
        except Exception:
            df = data_mod.load_price_history(symbol)
    if df is None or not len(df):
        return None, None
    return float(df["Close"].iloc[-1]), df.index[-1]


def _status_delivery(p, cur, today):
    entry, stop, t1, t2 = p["entry"], p["stop"], p["target1"], p["target2"]
    pnl = (cur / entry - 1) * 100
    if cur >= t2:
        status = "TGT2 HIT"
    elif cur >= t1:
        status = "T1 HIT"
    elif cur <= stop:
        status = "STOPPED OUT"
    else:
        status = "OPEN"
    return pnl, status


def _status_option(p, cur, today):
    direc = p.get("direction", "CE")
    be = p["breakeven"]
    strike = p["strike"]
    if direc == "CE":
        to_be = (be / cur - 1) * 100 if cur else float("inf")
        status = "PROFIT" if cur >= be else ("LOSS" if cur < strike else "OPEN")
    else:
        to_be = (cur / be - 1) * 100 if cur else float("inf")
        status = "PROFIT" if cur <= be else ("LOSS" if cur > strike else "OPEN")
    days_left = None
    if p.get("expiry"):
        try:
            exp = datetime.date.fromisoformat(p["expiry"])
            days_left = (exp - today).days
        except ValueError:
            pass
    return to_be, status, days_left


def scorecard_data(lookback_days):
    """Return structured scorecard rows: {"del_headers", "del_rows",
    "opt_headers", "opt_rows"} or None when the journal is empty."""
    picks = load_picks()
    if not picks:
        return None
    today = datetime.date.today()
    del_rows, opt_rows = [], []
    for p in picks:
        cur, last_d = _fresh_close(p["symbol"], lookback_days)
        if cur is None:
            row = [p["saved"], p["symbol"], "no data", "-"]
            (del_rows if p["type"] == "delivery" else opt_rows).append(row)
            continue
        if p["type"] == "delivery":
            pnl, status = _status_delivery(p, cur, today)
            del_rows.append([
                p["saved"], p["symbol"], p["entry"], round(cur, 2),
                p["stop"], p["target1"], p["target2"], f"{pnl:+.1f}%", status,
            ])
        else:
            to_be, status, days_left = _status_option(p, cur, today)
            dl = f"{days_left}d" if days_left is not None else "-"
            opt_rows.append([
                p["saved"], p["symbol"], p.get("direction", "-"), p["strike"],
                p["premium"], p["breakeven"], round(cur, 2),
                f"{to_be:+.1f}%" if to_be != float("inf") else "-", status, dl,
            ])
    return {
        "del_headers": ["SAVED", "SYMBOL", "ENTRY", "CMP", "STOP", "T1", "T2",
                        "P&L", "STATUS"],
        "del_rows": del_rows,
        "opt_headers": ["SAVED", "SYMBOL", "DIR", "STRIKE", "PREM", "BREAKEVEN",
                        "SPOT", "TO_BE%", "STATUS", "EXPIRY"],
        "opt_rows": opt_rows,
    }


def scorecard(lookback_days):
    data = scorecard_data(lookback_days)
    if data is None:
        return "No tracked picks yet. Run `python scanner.py track` after a scan."
    out = []
    if data["del_rows"]:
        out.append("DELIVERY / FADE PICKS (long, 3-5 day idea)")
        out.append(tabulate(data["del_rows"], headers=data["del_headers"],
                            tablefmt="grid", floatfmt=".2f"))
    if data["opt_rows"]:
        out.append("OPTION PICKS (underlying spot vs breakeven)")
        out.append(tabulate(data["opt_rows"], headers=data["opt_headers"],
                            tablefmt="grid", floatfmt=".2f"))
    out.append("")
    out.append("How to read STATUS:")
    out.append("  delivery: T1/T2 HIT = hit target(s), STOPPED OUT = thesis broke,")
    out.append("            OPEN = still running. P&L is vs the ENTRY price.")
    out.append("  options:  PROFIT = spot already past breakeven, LOSS = spot on")
    out.append("            wrong side, OPEN = not decided yet. TO_BE% = how much")
    out.append("            more the stock must move to reach breakeven by expiry.")
    return "\n".join(out)
