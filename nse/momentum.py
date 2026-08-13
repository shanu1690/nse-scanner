"""Delivery / momentum scanner engine.

Combines trend, breakout, momentum, volume and relative-strength into a
0-100 score. The output is a ranked watchlist with entry/stop/targets.
"""

import numpy as np
import pandas as pd

from . import indicators as ind


def _band(score, lo, hi):
    return max(lo, min(hi, score))


def analyze_stock(df, bench_ret_20=None, bench_ret_60=None):
    """Return dict of signals + reasons for one symbol's indicator frame."""
    idx, row = ind.last_snapshot(df)
    if idx is None:
        return None
    r = row
    price = r["Close"]
    reasons = []
    scores = {"trend": 0.0, "breakout": 0.0, "momentum": 0.0,
              "volume": 0.0, "relative_strength": 0.0}

    # ---- Trend (0-25) --------------------------------------------------------
    ts = 0.0
    if price > r["EMA21"] > r["EMA50"]:
        ts += 15
        reasons.append("Uptrend: Price > EMA21 > EMA50")
    elif price > r["EMA50"]:
        ts += 7
        reasons.append("Above EMA50 only")
    else:
        reasons.append("Below EMA50 (no trend)")
    if r["EMA50"] > r["EMA200"]:
        ts += 7
        reasons.append("EMA50 > EMA200 (long-term bullish)")
    else:
        ts += 1
    if price > r["EMA200"]:
        ts += 3
    else:
        ts += -5
    scores["trend"] = _band(ts, 0, 25)

    # ---- Breakout (0-25) -----------------------------------------------------
    bs = 0.0
    dist_52w = (price / r["HIGH_52W"] - 1) * 100 if r["HIGH_52W"] else 999
    if dist_52w >= -2.0:
        bs += 12
        reasons.append(f"Near 52-week high ({dist_52w:+.1f}%)")
    elif dist_52w >= -5.0:
        bs += 7
        reasons.append(f"Within 5% of 52-week high")
    dc_break = False
    if len(df) >= 2:
        prev_dc = df["DC_HIGH20"].iloc[-2]
        if not np.isnan(prev_dc):
            dc_break = price > prev_dc
    if dc_break:
        bs += 8
        reasons.append("Closed above 20-day Donchian high (breakout)")
    squeeze = (r["BB_UPPER"] - r["BB_LOWER"]) / price
    if squeeze < 0.10:
        bs += 5
        reasons.append("Narrow Bollinger band (compression ready to expand)")
    scores["breakout"] = _band(bs, 0, 25)

    # ---- Momentum (0-20) -----------------------------------------------------
    ms = 0.0
    if r["MACD"] > r["MACD_SIGNAL"] and r["MACD_HIST"] > 0:
        ms += 8
        reasons.append("MACD positive & above signal")
    elif r["MACD"] > 0:
        ms += 3
    rsi_val = r["RSI14"]
    if 55 <= rsi_val <= 72:
        ms += 7
        reasons.append(f"RSI {rsi_val:.0f} in momentum sweet-spot (55-72)")
    elif rsi_val > 80:
        ms += -4
        reasons.append(f"RSI {rsi_val:.0f} overbought > 80")
    elif rsi_val < 45:
        ms += 1
    if r["ROC5"] > 0 and r["ROC20"] > 0:
        ms += 5
        reasons.append("Positive 5-day & 20-day momentum")
    scores["momentum"] = _band(ms, 0, 20)

    # ---- Volume (0-15) -------------------------------------------------------
    vs = 0.0
    vz = r["VOL_Z"]
    if vz > 2.0:
        vs += 13
        reasons.append(f"Volume {vz:.1f} std dev above 20-day avg")
    elif vz > 1.5:
        vs += 9
        reasons.append("Volume 1.5x+ average (conviction)")
    elif vz > 0.5:
        vs += 4
    scores["volume"] = _band(vs, 0, 15)

    # ---- Relative strength vs NIFTY (0-15) -----------------------------------
    rs = 0.0
    if bench_ret_20 is not None:
        alpha20 = r["ROC20"] - bench_ret_20
        if alpha20 > 3:
            rs += 8
            reasons.append(f"Beating NIFTY by {alpha20:.1f}% over 20d")
        elif alpha20 > 0:
            rs += 4
    if bench_ret_60 is not None:
        alpha60 = r["ROC60"] - bench_ret_60
        if alpha60 > 5:
            rs += 7
    scores["relative_strength"] = _band(rs, 0, 15)

    total = sum(scores.values())

    # ---- Trade levels --------------------------------------------------------
    atr_v = r["ATR14"]
    entry = price
    stop = price - 1.5 * atr_v
    target1 = price + atr_v
    target2 = price + 2.0 * atr_v
    risk_pct = (1.5 * atr_v / price) * 100
    rr1 = atr_v / (1.5 * atr_v)

    return {
        "date": idx,
        "price": price,
        "score": round(total, 1),
        "subscores": {k: round(v, 1) for k, v in scores.items()},
        "reasons": reasons,
        "rsi": rsi_val,
        "vol_z": round(float(vz), 2) if not np.isnan(vz) else None,
        "dist_52w_pct": round(dist_52w, 1),
        "atr": round(atr_v, 2),
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2),
        "risk_pct": round(risk_pct, 1),
        "reward_risk": round(rr1, 2),
    }


def analyze_fade(df, bench_ret_20=None, bench_ret_60=None):
    """Contrarian LONG alternative: ranks washed-out names that tend to bounce.
    The 3-month factor study showed every buy-strength factor was a fade signal
    (weak stocks rose more than strong ones) while only relative strength
    predicted upside. So this is a strict inversion of the momentum sub-scores
    (trend/breakout/momentum/volume reversed) with relative strength kept
    additive. High score = expected UP move (long)."""
    idx, row = ind.last_snapshot(df)
    if idx is None:
        return None
    r = row
    price = r["Close"]
    reasons = []

    # ---- raw momentum-style sub-scores (what analyze_stock would award) -------
    ts = 0.0
    if price > r["EMA21"] > r["EMA50"]:
        ts += 15
        reasons.append("Strong uptrend (price > EMA21 > EMA50) - over-extended")
    elif price > r["EMA50"]:
        ts += 7
        reasons.append("Above EMA50")
    else:
        reasons.append("Below EMA50 - washed out")
    if r["EMA50"] > r["EMA200"]:
        ts += 7
    else:
        ts += 1
    ts += 3 if price > r["EMA200"] else -5

    bs = 0.0
    dist_52w = (price / r["HIGH_52W"] - 1) * 100 if r["HIGH_52W"] else 999
    if dist_52w >= -2.0:
        bs += 12
        reasons.append(f"Near 52-week high ({dist_52w:+.1f}%) - extended")
    elif dist_52w >= -5.0:
        bs += 7
    else:
        reasons.append(f"{abs(dist_52w):.0f}% below 52-week high - beaten down")
    dc_break = False
    if len(df) >= 2:
        prev_dc = df["DC_HIGH20"].iloc[-2]
        if not np.isnan(prev_dc):
            dc_break = price > prev_dc
    if dc_break:
        bs += 8
    squeeze = (r["BB_UPPER"] - r["BB_LOWER"]) / price
    if squeeze < 0.10:
        bs += 5

    ms = 0.0
    if r["MACD"] > r["MACD_SIGNAL"] and r["MACD_HIST"] > 0:
        ms += 8
    elif r["MACD"] > 0:
        ms += 3
    rsi_val = r["RSI14"]
    if 55 <= rsi_val <= 72:
        ms += 7
    elif rsi_val > 80:
        ms += -4
    elif rsi_val < 45:
        ms += 1
        reasons.append(f"RSI {rsi_val:.0f} low - oversold region")
    if r["ROC5"] > 0 and r["ROC20"] > 0:
        ms += 5
    else:
        reasons.append("Negative 5d/20d momentum")

    vs = 0.0
    vz = r["VOL_Z"]
    if vz > 2.0:
        vs += 13
        reasons.append(f"Volume {vz:.1f} std dev above avg")
    elif vz > 1.5:
        vs += 9
    elif vz > 0.5:
        vs += 4

    rs = 0.0
    if bench_ret_20 is not None:
        alpha20 = r["ROC20"] - bench_ret_20
        if alpha20 > 3:
            rs += 8
            reasons.append(f"Still beating NIFTY by {alpha20:.1f}% over 20d")
        elif alpha20 > 0:
            rs += 4
    if bench_ret_60 is not None:
        alpha60 = r["ROC60"] - bench_ret_60
        if alpha60 > 5:
            rs += 7
    if rs > 0:
        reasons.append("Relative strength vs NIFTY positive (only factor with real edge)")

    # ---- invert the strength factors; keep relative strength additive ---------
    scores = {
        "trend": _band(25 - ts, 0, 25),
        "breakout": _band(25 - bs, 0, 25),
        "momentum": _band(20 - ms, 0, 20),
        "volume": _band(15 - vs, 0, 15),
        "relative_strength": _band(rs, 0, 15),
    }
    total = sum(scores.values())

    # ---- Trade levels (contrarian LONG: stop below, targets above) -----------
    atr_v = r["ATR14"]
    entry = price
    stop = price - 1.5 * atr_v
    target1 = price + atr_v
    target2 = price + 2.0 * atr_v
    risk_pct = (1.5 * atr_v / price) * 100
    rr1 = atr_v / (1.5 * atr_v)

    return {
        "date": idx,
        "price": price,
        "score": round(total, 1),
        "subscores": {k: round(v, 1) for k, v in scores.items()},
        "reasons": reasons,
        "rsi": rsi_val,
        "vol_z": round(float(vz), 2) if not np.isnan(vz) else None,
        "dist_52w_pct": round(dist_52w, 1),
        "atr": round(atr_v, 2),
        "entry": round(entry, 2),
        "stop": round(stop, 2),
        "target1": round(target1, 2),
        "target2": round(target2, 2),
        "risk_pct": round(risk_pct, 1),
        "reward_risk": round(rr1, 2),
        "fade": True,
    }


def scan_universe(prices: dict, bench_df=None, min_score=55.0, top_n=20, fade=False):
    """prices: {symbol: OHLCV df}. Returns ranked list of analyze_stock dicts."""
    bench20 = bench60 = None
    if bench_df is not None and len(bench_df) > 60:
        b = ind.last_snapshot(ind.add_all_indicators(bench_df))
        if b[0] is not None and b[1]:
            _, brow = b
            bench20, bench60 = brow.get("ROC20"), brow.get("ROC60")

    results = []
    for sym, df in prices.items():
        if df is None or len(df) < 60:
            continue
        try:
            if fade:
                a = analyze_fade(ind.add_all_indicators(df), bench20, bench60)
            else:
                a = analyze_stock(ind.add_all_indicators(df), bench20, bench60)
        except Exception:
            continue
        if a is None:
            continue
        a["symbol"] = sym
        results.append(a)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:top_n], results
