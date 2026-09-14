"""Options scanner: parses NSE equity option chains and derives:
- OI build-up / unwinding direction
- PCR and max pain (market positioning)
- IV rank (rich/cheap vs own history, via local snapshots)
- Expected move (ATM straddle + IV-based)
- Concrete strike picks with premium & breakeven
- (Phase 7) a concrete strategy idea under the Rs 10,000 budget cap --
  see select_option_idea() and PROJECT_BRIEF.md Section 5
"""

import csv
import io
import json
import math
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
                # bid/ask -- NSE's v3 JSON names these buyPrice1/sellPrice1
                # (best bid/ask, depth level 1). SmartAPI-sourced chains
                # don't populate these (Angel's REST quote doesn't expose
                # depth in the fields this repo currently reads), so they
                # come back 0 there -- the quality-bar check below treats
                # 0/0 as "unknown, not necessarily bad" rather than a fail.
                "bid": float(leg.get("buyPrice1", 0) or 0),
                "ask": float(leg.get("sellPrice1", 0) or 0),
            })
    if not rows:
        return None
    return pd.DataFrame(rows)


def cross_source_d_oi(symbol, raw, *, session_factory=None):
    """Fix the documented d_oi gap (PROJECT_BRIEF.md Section 3: "d_oi is
    always 0 in SmartAPI REST quotes -- fix by cross-sourcing OI change").

    Angel SmartAPI's getMarketData(FULL) REST quotes have no change-in-OI
    field at all (see nse/smartapi.py's own docstring), so every leg of a
    SmartAPI-sourced chain reports changeinOpenInterest=0. NSE's own public
    option-chain JSON computes this figure itself and that endpoint already
    works reliably in this repo (nse/nse_api.py's NSESession is the "nse"
    provider fallback) -- so when a chain shows EVERY leg's d_oi at exactly
    zero (SmartAPI's signature; a real chain with genuinely flat OI change
    across every single strike/side on a trading day would itself be
    implausible), fetch NSE's public chain for the same symbol/expiry and
    copy its real changeinOpenInterest onto matching strikes.

    Mutates and returns `raw`. Best-effort: any failure (network, no
    matching strikes, NSE blocked) leaves d_oi at 0 -- exactly today's
    behaviour, not a regression -- and is logged via nse.quality.events for
    coverage accounting rather than silently vanishing. `session_factory`
    lets callers/tests inject a fake NSE session instead of a live one.
    """
    from nse.quality.events import log_event

    records = raw.get("records") or {}
    data = records.get("data") or []
    legs = [leg for item in data for leg in (item.get("CE"), item.get("PE")) if leg]
    if not legs or not all((leg.get("changeinOpenInterest") or 0) == 0 for leg in legs):
        return raw  # already has real d_oi (or chain is empty) -- nothing to do

    expiry = _row_expiry(data[0]) if data else None
    try:
        if session_factory is not None:
            session = session_factory()
        else:
            from nse.nse_api import NSESession
            session = NSESession()
        try:
            nse_raw = session.option_chain_equity(symbol, expiry=expiry)
        finally:
            close = getattr(session, "close", None)
            if close:
                close()
    except Exception as exc:  # best-effort -- any failure mode, never propagate
        log_event(symbol, "d_oi_cross_source_unavailable", str(exc))
        return raw

    nse_data = ((nse_raw or {}).get("records") or {}).get("data") or []
    doi_lookup = {}
    for item in nse_data:
        strike = item.get("strikePrice")
        for side in ("CE", "PE"):
            leg = item.get(side)
            if leg is not None:
                doi_lookup[(strike, side)] = leg.get("changeinOpenInterest", 0) or 0

    patched = 0
    for item in data:
        strike = item.get("strikePrice")
        for side in ("CE", "PE"):
            leg = item.get(side)
            key = (strike, side)
            if leg is not None and key in doi_lookup:
                leg["changeinOpenInterest"] = doi_lookup[key]
                patched += 1
    if patched == 0:
        log_event(symbol, "d_oi_cross_source_unavailable",
                  "NSE chain fetched but no matching strikes")
    return raw


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


def analyze_option_chain(symbol, raw, trend=None, cross_source_oi=True):
    """raw: NSE /api/option-chain-equities JSON. trend: momentum snapshot dict.

    Returns an analysis dict with signal, reasons and strike picks.

    cross_source_oi=True (default) runs cross_source_d_oi() first, which
    may perform an extra live NSE fetch when (and only when) the supplied
    chain shows the SmartAPI d_oi gap (every leg's change-in-OI at exactly
    0). Pass False to skip that -- e.g. in tests, or when a caller has
    already cross-sourced/doesn't want the extra network round-trip.
    """
    if cross_source_oi:
        raw = cross_source_d_oi(symbol, raw)
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
        # Internal reuse for select_option_idea() below -- not part of the
        # stable display contract; a leading underscore flags "may change,
        # don't build a UI on this key directly".
        "_frame": frame,
        "_step": step,
    }


# ===========================================================================
# Phase 7: a concrete strategy idea under the Rs 10,000 budget cap.
# PROJECT_BRIEF.md Section 5's rules, enforced here:
#   1. Filter, don't force -- "no qualifying option trade today" is valid.
#   2. Prefer defined-risk debit spreads over naked longs when both qualify.
#   3. Rank by probability, not cheapness (see rank_ideas()).
#   4. Refuse the traps -- near-expiry, thin OI, wide bid-ask spread.
#   5. Never recommend selling/writing options (nothing here ever does).
#   6. Beginner mode: cost, max loss, breakeven, thesis, DTE, payoff data.
#   7. Paper-trade gate is tracker.py's job, not this module's.
# ===========================================================================

MAX_BUDGET_RS = 10_000.0
MIN_DTE_DAYS = 2                    # rule 4: block same-day/next-day expiry
MIN_LEG_OI = 500                    # rule 4: minimum open interest per leg
MAX_SPREAD_COST_FRACTION = 0.15     # rule 4: bid-ask spread vs premium ceiling
SPREAD_WIDTH_STEPS = (1, 2, 3, 4)   # short leg this many strikes beyond the long leg
PAYOFF_POINTS = 21                  # samples across the payoff diagram's price range
PAYOFF_RANGE_PCT = 0.15             # +/- range around spot the diagram covers


def _norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def prob_finish_itm(spot, strike, iv_pct, dte_days, side, r: float = 0.0):
    """Black-Scholes risk-neutral probability of finishing in-the-money at
    expiry (N(d2) for a call, N(-d2) for a put) -- a standard, explainable
    proxy for "probability of profit at expiry" a beginner tool can state
    plainly. This is NOT a claim of real-world statistical edge (it's the
    market-implied probability under the option's own IV), which is the
    honest thing to call it: see the plain-English thesis text.

    r=0 (no risk-free-rate term): at the day-to-a-few-months tenors this
    tool deals in, the rate barely moves the estimate, and dropping it
    keeps the formula (and its explanation to a non-quant user) simpler.

    Returns None when any input can't safely support the model (non-
    positive spot/strike/dte, missing/zero IV).
    """
    if not spot or spot <= 0 or not strike or strike <= 0:
        return None
    if not dte_days or dte_days <= 0:
        return None
    if not iv_pct or iv_pct <= 0:
        return None
    sigma = iv_pct / 100.0
    t = dte_days / 365.0
    try:
        d1 = (math.log(spot / strike) + (r + 0.5 * sigma * sigma) * t) / (sigma * math.sqrt(t))
        d2 = d1 - sigma * math.sqrt(t)
    except (ValueError, ZeroDivisionError):
        return None
    if side == "CE":
        return _norm_cdf(d2)
    if side == "PE":
        return _norm_cdf(-d2)
    return None


def _leg_quality(leg: dict) -> "tuple[bool, str | None]":
    """Rule 4's per-leg quality bar. A leg with no bid/ask data (thin names
    often lack depth) is NOT auto-rejected on the spread check -- there is
    nothing to check -- but the OI floor always applies."""
    oi = leg.get("oi", 0) or 0
    if oi < MIN_LEG_OI:
        return False, f"OI {oi:,.0f} below the {MIN_LEG_OI:,.0f} minimum"
    bid, ask, premium = leg.get("bid", 0), leg.get("ask", 0), leg.get("premium", 0)
    if bid and ask and premium:
        spread = ask - bid
        if spread > MAX_SPREAD_COST_FRACTION * premium:
            return False, (f"bid-ask spread {spread:.2f} exceeds "
                           f"{MAX_SPREAD_COST_FRACTION:.0%} of premium {premium:.2f}")
    return True, None


def _leg_row(frame, side, strike):
    rows = frame[(frame["side"] == side) & (frame["strike"] == strike)]
    return rows.iloc[0].to_dict() if len(rows) else None


def _build_naked_long(frame, direction, atm_strike, step, spot, dte, lot_size,
                       budget, rejected: list) -> "dict | None":
    """The cheapest quality-passing, in-budget long CE/PE at or slightly
    OTM from ATM (checked nearest-strike first)."""
    for off in (0, 1, 2, 3):
        strike = atm_strike + off * step if direction == "CE" else atm_strike - off * step
        leg = _leg_row(frame, direction, strike)
        if leg is None:
            continue
        cost = round(leg["premium"] * lot_size, 2)
        if cost > budget:
            rejected.append({"kind": "over_budget", "strategy": "long", "strike": strike,
                             "detail": f"cost Rs {cost:,.0f} > budget Rs {budget:,.0f}"})
            continue
        ok, why = _leg_quality(leg)
        if not ok:
            rejected.append({"kind": "quality", "strategy": "long", "strike": strike, "detail": why})
            continue
        breakeven = round(strike + leg["premium"], 2) if direction == "CE" else round(strike - leg["premium"], 2)
        prob = prob_finish_itm(spot, breakeven, leg["iv"], dte, direction)
        return {
            "legs": [{"action": "BUY", "side": direction, "strike": strike,
                      "premium": leg["premium"]}],
            "cost": cost, "max_loss": cost, "max_profit": None,  # unlimited/uncapped upside
            "breakeven": breakeven, "probability": prob, "iv": leg["iv"],
        }
    return None


def _build_debit_spread(frame, direction, atm_strike, step, spot, dte, lot_size,
                         budget, rejected: list) -> "dict | None":
    """Narrowest quality-passing, in-budget debit spread: buy at/near ATM,
    sell further OTM in the same direction. Narrowest-first because it's
    both the cheapest defined-risk structure available and the closest
    analogue to the naked long it's meant to replace under rule 2."""
    long_strike = atm_strike
    long_leg = _leg_row(frame, direction, long_strike)
    if long_leg is None:
        return None
    for width in SPREAD_WIDTH_STEPS:
        short_strike = long_strike + width * step if direction == "CE" else long_strike - width * step
        short_leg = _leg_row(frame, direction, short_strike)
        if short_leg is None:
            continue
        net_debit = long_leg["premium"] - short_leg["premium"]
        if net_debit <= 0:
            continue  # not a real debit spread (inverted/zero-cost quotes -- skip, don't trust it)
        cost = round(net_debit * lot_size, 2)
        if cost > budget:
            rejected.append({"kind": "over_budget", "strategy": "spread",
                             "strike": f"{long_strike}/{short_strike}",
                             "detail": f"cost Rs {cost:,.0f} > budget Rs {budget:,.0f}"})
            continue
        ok_long, why_long = _leg_quality(long_leg)
        ok_short, why_short = _leg_quality(short_leg)
        if not (ok_long and ok_short):
            rejected.append({"kind": "quality", "strategy": "spread",
                             "strike": f"{long_strike}/{short_strike}",
                             "detail": why_long or why_short})
            continue
        width_pts = abs(short_strike - long_strike)
        max_profit = round((width_pts - net_debit) * lot_size, 2)
        if max_profit <= 0:
            continue  # width too narrow to be worth the debit -- not a sane spread
        breakeven = round(long_strike + net_debit, 2) if direction == "CE" else round(long_strike - net_debit, 2)
        prob = prob_finish_itm(spot, breakeven, long_leg["iv"], dte, direction)
        return {
            "legs": [
                {"action": "BUY", "side": direction, "strike": long_strike, "premium": long_leg["premium"]},
                {"action": "SELL", "side": direction, "strike": short_strike, "premium": short_leg["premium"]},
            ],
            "cost": cost, "max_loss": cost, "max_profit": max_profit,
            "breakeven": breakeven, "probability": prob, "iv": long_leg["iv"],
        }
    return None


def _payoff_diagram(idea: dict, spot: float, lot_size: int) -> list:
    """[{"price": underlying_price, "pnl": rupee_pnl_at_that_price}, ...]
    across a range around spot -- rule 6's payoff diagram, as data rather
    than an image (Phase 9's dashboard renders it)."""
    lo = spot * (1 - PAYOFF_RANGE_PCT)
    hi = spot * (1 + PAYOFF_RANGE_PCT)
    prices = np.linspace(lo, hi, PAYOFF_POINTS)
    points = []
    for price in prices:
        pnl = 0.0
        for leg in idea["legs"]:
            intrinsic = max(price - leg["strike"], 0) if leg["side"] == "CE" else max(leg["strike"] - price, 0)
            leg_pnl = (intrinsic - leg["premium"]) * lot_size
            pnl += leg_pnl if leg["action"] == "BUY" else -leg_pnl
        points.append({"price": round(float(price), 2), "pnl": round(pnl, 2)})
    return points


def _plain_english_thesis(symbol, analysis, idea, strategy) -> str:
    breakeven = idea["breakeven"]
    dte = analysis["dte"]
    expiry = analysis["expiry"]
    max_loss = idea["max_loss"]
    direction_word = "above" if idea["legs"][0]["side"] == "CE" else "below"
    prob_str = f"~{idea['probability']:.0%}" if idea.get("probability") is not None else "n/a"
    structure = {"debit_spread": "this defined-risk debit spread",
                 "long": f"this long {idea['legs'][0]['side']}"}.get(strategy, "this trade")
    return (
        f"{symbol} needs to close {direction_word} {breakeven} by {expiry} "
        f"({dte} trading day{'s' if dte != 1 else ''} away) for {structure} to profit. "
        f"Modeled probability of finishing there: {prob_str} (market-implied from current IV, "
        f"not a guarantee). Maximum possible loss if wrong: Rs {max_loss:,.0f} -- "
        f"you can lose 100% of the amount paid for this position."
    )


def select_option_idea(symbol, raw, trend=None, budget: float = MAX_BUDGET_RS,
                        cross_source_oi: bool = True) -> dict:
    """One concrete, budget-capped strategy idea for `symbol`, or a clear
    refusal with reasons -- PROJECT_BRIEF.md Section 5, rule 1: "no
    qualifying option trade today" is a valid and expected output, and how
    many candidates were rejected (and why) is always inspectable via the
    returned `rejected` list, never silently dropped.
    """
    analysis = analyze_option_chain(symbol, raw, trend=trend, cross_source_oi=cross_source_oi)
    if analysis.get("error"):
        return {"symbol": symbol, "idea": None, "reason": analysis["error"],
                "rejected": [], "analysis": analysis}

    rejected: list = []
    direction = analysis["direction"]
    if direction == "NEUTRAL":
        return {"symbol": symbol, "idea": None, "reason": "no directional edge",
                "rejected": rejected, "analysis": analysis}

    dte = analysis["dte"]
    if dte < MIN_DTE_DAYS:
        rejected.append({"kind": "near_expiry",
                         "detail": f"{dte}d to expiry < {MIN_DTE_DAYS}d minimum"})
        return {"symbol": symbol, "idea": None, "reason": "near-expiry blocked",
                "rejected": rejected, "analysis": analysis}

    lot_size = analysis["lot_size"]
    if not lot_size:
        rejected.append({"kind": "no_lot_size", "detail": "lot size unavailable"})
        return {"symbol": symbol, "idea": None, "reason": "lot size unavailable",
                "rejected": rejected, "analysis": analysis}

    frame, step, spot = analysis["_frame"], analysis["_step"], analysis["spot"]
    atm_strike = analysis["atm_strike"]

    # Rule 2: a defined-risk debit spread is tried FIRST and wins whenever
    # it qualifies; a naked long is only used when no spread does.
    spread = _build_debit_spread(frame, direction, atm_strike, step, spot, dte,
                                  lot_size, budget, rejected)
    if spread is not None:
        idea, strategy = spread, "debit_spread"
    else:
        naked = _build_naked_long(frame, direction, atm_strike, step, spot, dte,
                                   lot_size, budget, rejected)
        if naked is None:
            return {"symbol": symbol, "idea": None, "reason": "no qualifying option trade today",
                    "rejected": rejected, "analysis": analysis}
        idea, strategy = naked, "long"

    idea["strategy"] = strategy
    idea["dte"] = dte
    idea["expiry"] = analysis["expiry"]
    idea["lot_size"] = lot_size
    idea["payoff"] = _payoff_diagram(idea, spot, lot_size)
    idea["thesis"] = _plain_english_thesis(symbol, analysis, idea, strategy)
    return {"symbol": symbol, "idea": idea, "reason": None,
            "rejected": rejected, "analysis": analysis}


def rank_ideas(ideas: list) -> list:
    """Sort select_option_idea() results by modeled probability, descending
    -- rule 3: "never sort the options list by ascending premium". Three
    tiers, worst last: a real probability (0% is still a real, ranked
    number) > a qualifying idea whose probability couldn't be modeled >
    no qualifying idea at all.
    """
    def _key(result):
        idea = result.get("idea")
        if idea is None:
            return -2.0
        prob = idea.get("probability")
        return prob if prob is not None else -1.0
    return sorted(ideas, key=_key, reverse=True)
