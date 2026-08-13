"""Options scanner: parses NSE equity option chains and derives:
- OI build-up / unwinding direction
- PCR and max pain (market positioning)
- IV rank (rich/cheap vs own history, via local snapshots)
- Expected move (ATM straddle + IV-based)
- Concrete strike picks with premium & breakeven
"""

import csv
import io
import json
import os
from datetime import datetime, timedelta

import requests

import numpy as np
import pandas as pd

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
IV_DIR = os.path.join(DATA_DIR, "iv_history")
os.makedirs(IV_DIR, exist_ok=True)

LOT_SIZE_CACHE = os.path.join(DATA_DIR, "cache", "lot_sizes.json")
LOT_SIZE_TTL = timedelta(days=7)
_LOT_STATE = {"sizes": None, "attempted": False}


LOT_CSV_URL = "https://nsearchives.nseindia.com/content/fo/fo_mktlots.csv"
LOT_CSV_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/csv",
    "Accept-Language": "en-US,en;q=0.9",
}


def _smartapi_enabled():
    """True when config.yaml -> data.provider is 'smartapi'."""
    try:
        with open(os.path.join(DATA_DIR, "..", "config.yaml")) as fh:
            import yaml
            cfg = yaml.safe_load(fh) or {}
        return (cfg.get("data") or {}).get("provider", "nse") == "smartapi"
    except (OSError, ValueError):
        return False


def load_lot_sizes():
    """symbol -> option lot size (cached 7 days).

    With data.provider=smartapi the Scrip Master lot sizes are used (most
    accurate, includes index lots). Otherwise (and as a fallback) NSE's
    published fo_mktlots.csv is used: one row per underlying with per-month lot
    columns, nearest-expiry month carrying the currently applicable lot size.
    NSE retired /api/contract-master (now 404).

    Returns a dict, or None if the data cannot be fetched.
    """
    try:
        with open(LOT_SIZE_CACHE) as fh:
            payload = json.load(fh)
        stamp = datetime.fromisoformat(payload["_fetched_at"])
        if datetime.now() - stamp <= LOT_SIZE_TTL and payload.get("data"):
            return payload["data"]
    except (OSError, KeyError, ValueError):
        pass
    if _smartapi_enabled():
        try:
            from nse.smartapi import SmartAPISession, SmartAPIUnavailable
            lots = SmartAPISession().lot_sizes()
            if lots:
                _cache_lots(lots)
                return lots
        except (SmartAPIUnavailable, ImportError, OSError):
            pass
    try:
        resp = requests.get(LOT_CSV_URL, headers=LOT_CSV_HEADERS, timeout=25)
        resp.raise_for_status()
    except (requests.RequestException, ValueError):
        return None
    rows = csv.reader(io.StringIO(resp.text))
    try:
        header = [c.strip() for c in next(rows)]
    except StopIteration:
        return None
    if "SYMBOL" not in header:
        return None
    lots = {}
    for row in rows:
        if len(row) < 3:
            continue
        sym = row[1].strip()
        for cell in row[2:]:
            cell = cell.strip()
            if cell:
                try:
                    lots[sym] = int(cell)
                except ValueError:
                    pass
                break  # nearest-expiry month = current lot size
    if not lots:
        return None
    _cache_lots(lots)
    return lots


def _cache_lots(lots):
    try:
        with open(LOT_SIZE_CACHE, "w") as fh:
            json.dump({"_fetched_at": datetime.now().isoformat(), "data": lots}, fh)
    except OSError:
        pass


def lot_size_for(symbol):
    """Lot size for one symbol; resolved once per process, None if unavailable."""
    if not _LOT_STATE["attempted"]:
        _LOT_STATE["sizes"] = load_lot_sizes()
        _LOT_STATE["attempted"] = True
    return (_LOT_STATE["sizes"] or {}).get(symbol)


def _row_expiry(item):
    """Field is `expiryDate` (legacy) or `expiryDates` (v3, may be a list)."""
    val = item.get("expiryDate") or item.get("expiryDates")
    if isinstance(val, list):
        return val[0] if val else None
    return val


def _chain_to_frame(records, expiry):
    rows = []
    for item in records["data"]:
        if _row_expiry(item) != expiry:
            continue
        strike = item.get("strikePrice")
        for side in ("CE", "PE"):
            leg = item.get(side)
            if not leg or leg.get("openInterest") in (None, "-", 0):
                continue
            rows.append({
                "strike": strike,
                "side": side,
                "oi": float(leg.get("openInterest", 0) or 0),
                "d_oi": float(leg.get("changeinOpenInterest", 0) or 0),
                "volume": float(leg.get("totalTradedVolume", 0) or 0),
                "iv": float(leg.get("impliedVolatility", 0) or 0),
                "premium": float(leg.get("lastPrice", 0) or 0),
            })
    if not rows:
        return None
    return pd.DataFrame(rows)


def _max_pain(records, expiry):
    """Strike where option WRITERS lose the least (sum of liabilities minimised).

    Liability of calls at strike S = sum(max(S-K,0) * CE_OI(K))
    Liability of puts  at strike S = sum(max(K-S,0) * PE_OI(K))
    """
    items = []
    for item in records["data"]:
        if _row_expiry(item) != expiry:
            continue
        s = item.get("strikePrice")
        ce_oi = float((item.get("CE") or {}).get("openInterest", 0) or 0)
        pe_oi = float((item.get("PE") or {}).get("openInterest", 0) or 0)
        items.append((s, ce_oi, pe_oi))
    if not items:
        return None
    best, best_cost = None, float("inf")
    for s in sorted({i[0] for i in items}):
        cost = 0.0
        for k, ce_oi, pe_oi in items:
            cost += max(s - k, 0) * ce_oi + max(k - s, 0) * pe_oi
        if cost < best_cost:
            best, best_cost = s, cost
    return best


def _load_iv_history(symbol):
    path = os.path.join(IV_DIR, f"{symbol}.json")
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return []


def _save_iv_history(symbol, history):
    path = os.path.join(IV_DIR, f"{symbol}.json")
    with open(path, "w") as fh:
        json.dump(history, fh)


MIN_IVR_DAYS = 3      # minimum history to start showing an IVR value
FULL_IVR_DAYS = 20     # history needed for IVR to influence the score


def iv_rank(symbol, current_iv, today):
    history = _load_iv_history(symbol)
    today_str = today.strftime("%Y-%m-%d")
    if not any(h["date"] == today_str for h in history):
        history.append({"date": today_str, "iv": round(current_iv, 2)})
        _save_iv_history(symbol, history[-180:])
    past = [h["iv"] for h in history if h["date"] != today_str]
    if len(past) < MIN_IVR_DAYS:
        return None, 0, history
    pct = np.mean([v <= current_iv for v in past]) * 100
    return round(pct, 0), len(past), history


def analyze_option_chain(symbol, raw, trend=None):
    """raw: NSE /api/option-chain-equities JSON. trend: momentum snapshot dict.

    Returns an analysis dict with signal, reasons and strike picks.
    """
    records = raw.get("records") or {}
    expiries = records.get("expiryDates") or []
    if not expiries:
        return {"symbol": symbol, "error": "no expiry dates"}
    expiry = expiries[0]  # nearest expiry
    spot = float(records.get("underlyingValue", 0) or 0)
    if not spot:
        return {"symbol": symbol, "error": "no underlying value"}

    frame = _chain_to_frame(records, expiry)
    if frame is None:
        return {"symbol": symbol, "error": "chain empty"}

    ce = frame[frame["side"] == "CE"]
    pe = frame[frame["side"] == "PE"]
    total_ce_oi = ce["oi"].sum()
    total_pe_oi = pe["oi"].sum()
    pcr = (total_pe_oi / total_ce_oi) if total_ce_oi else np.nan
    d_ce = ce["d_oi"].sum()
    d_pe = pe["d_oi"].sum()
    d_total = d_ce + d_pe

    max_pain = _max_pain(records, expiry)

    # ATM straddle premium & IV-based expected move
    atm_strike = min(ce["strike"], key=lambda s: abs(s - spot))
    atm_ce = ce[ce["strike"] == atm_strike]
    atm_pe = pe[pe["strike"] == atm_strike]
    atm_iv = float(np.mean([atm_ce["iv"].values[0], atm_pe["iv"].values[0]])) \
        if len(atm_ce) and len(atm_pe) else np.nan
    dte = max((pd.Timestamp(expiry) - pd.Timestamp.today()).days, 1)
    iv_move_pct = atm_iv * np.sqrt(dte / 365) if not np.isnan(atm_iv) else np.nan
    straddle_prem = float(atm_ce["premium"].sum() + atm_pe["premium"].sum()) \
        if len(atm_ce) and len(atm_pe) else np.nan

    if np.isnan(atm_iv):
        ivr, ivr_days = None, 0
    else:
        ivr, ivr_days, _ = iv_rank(symbol, atm_iv, pd.Timestamp.today())

    # ---- Directional scoring -------------------------------------------------
    reasons = []
    score = 50.0

    if trend is not None:
        price = trend.get("price", spot)
        if price > trend.get("EMA21", price) and trend.get("EMA21", price) > trend.get("EMA50", price):
            score += 15
            reasons.append("Underlying in uptrend (EMA21 > EMA50)")
        elif price < trend.get("EMA50", price):
            score -= 15
            reasons.append("Underlying below EMA50 (downtrend)")
        else:
            score += 3
    else:
        score += 5

    if not np.isnan(pcr):
        if pcr < 0.7:
            score += 10
            reasons.append(f"PCR {pcr:.2f} < 0.7 (put writers active / bullish tone)")
        elif pcr <= 1.0:
            score += 5
        elif pcr <= 1.3:
            score -= 5
            reasons.append(f"PCR {pcr:.2f} elevated (call selling pressure)")
        else:
            score -= 10
            reasons.append(f"PCR {pcr:.2f} > 1.3 (heavy put buying / bearish)")

    price_move = trend.get("ROC5", 0) if trend else 0
    if d_total > 0 and price_move > 0:
        score += 12
        reasons.append(f"OI building +{d_total:,.0f} while price rising (fresh longs)")
    elif d_total > 0 and price_move < 0:
        score -= 12
        reasons.append(f"OI building +{d_total:,.0f} while price falling (short build-up)")
    elif d_total < 0 and price_move < 0:
        score += 5
        reasons.append("OI falling on price fall (short covering)")
    else:
        reasons.append(f"OI change {d_total:+,.0f}")

    if max_pain:
        if spot > max_pain:
            score += 5
            reasons.append(f"Spot above max pain {max_pain} (gravity pulls up)")
        else:
            score -= 5
            reasons.append(f"Spot below max pain {max_pain} (gravity pulls down)")

    if ivr is not None and ivr_days >= FULL_IVR_DAYS:
        if ivr > 75:
            reasons.append(f"ATM IV in {ivr:.0f}th pctile — premium rich; favour spreads")
        elif ivr < 25:
            reasons.append(f"ATM IV in {ivr:.0f}th pctile — premium cheap")
        else:
            reasons.append(f"ATM IV in {ivr:.0f}th pctile (neutral)")

    direction = "CE" if score >= 65 else ("PE" if score <= 35 else "NEUTRAL")
    lean = ""
    if direction == "NEUTRAL":
        lean = "Slight CE lean" if score >= 50 else ("Slight PE lean" if score > 35 else "No edge yet")
        reasons.append(lean)

    # ---- Strike picks --------------------------------------------------------
    step = 1
    strikes = sorted(frame["strike"].unique())
    if len(strikes) > 1:
        step = int(abs(strikes[1] - strikes[0]))
    picks = []
    if direction == "CE":
        for off in (0, 1, 2):
            stk = atm_strike + off * step
            leg = ce[ce["strike"] == stk]
            if not len(leg):
                continue
            prem = float(leg["premium"].values[0])
            picks.append({
                "strike": stk, "premium": prem,
                "breakeven": round(stk + prem, 2),
                "iv": float(leg["iv"].values[0]),
            })
    elif direction == "PE":
        for off in (0, 1, 2):
            stk = atm_strike - off * step
            leg = pe[pe["strike"] == stk]
            if not len(leg):
                continue
            prem = float(leg["premium"].values[0])
            picks.append({
                "strike": stk, "premium": prem,
                "breakeven": round(stk - prem, 2),
                "iv": float(leg["iv"].values[0]),
            })

    lot_size = lot_size_for(symbol)
    amount_per_lot = None
    if lot_size and picks:
        amount_per_lot = round(picks[0]["premium"] * lot_size, 0)

    return {
        "symbol": symbol,
        "spot": round(spot, 2),
        "expiry": expiry,
        "dte": dte,
        "score": round(score, 1),
        "direction": direction,
        "lean": lean,
        "pcr": round(pcr, 2) if not np.isnan(pcr) else None,
        "max_pain": max_pain,
        "atm_strike": atm_strike,
        "atm_iv": round(atm_iv, 1) if not np.isnan(atm_iv) else None,
        "ivr": ivr,
        "ivr_days": ivr_days,
        "expected_move_pct": round(iv_move_pct, 1) if not np.isnan(iv_move_pct) else None,
        "straddle_prem": round(straddle_prem, 2) if not np.isnan(straddle_prem) else None,
        "total_oi": int(total_ce_oi + total_pe_oi),
        "d_oi_total": int(d_total),
        "lot_size": lot_size,
        "amount_per_lot": amount_per_lot,
        "picks": picks[:3],
        "reasons": reasons,
    }
