"""Technical indicators computed on a daily OHLCV DataFrame."""

import numpy as np
import pandas as pd


def ema(series, span):
    return series.ewm(span=span, adjust=False).mean()


def sma(series, window):
    return series.rolling(window).mean()


def rsi(close, period=14):
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(100.0)


def macd(close, fast=12, slow=26, signal=9):
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return macd_line, signal_line, hist


def atr(df, period=14):
    high, low, close = df["High"], df["Low"], df["Close"]
    tr = pd.concat(
        [high - low,
         (high - close.shift()).abs(),
         (low - close.shift()).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def bollinger(close, window=20, num_std=2):
    mid = sma(close, window)
    std = close.rolling(window).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    return upper, mid, lower


def donchian(high, low, window=20):
    return high.rolling(window).max(), low.rolling(window).min()


def volume_zscore(volume, window=20):
    mean = volume.rolling(window).mean()
    std = volume.rolling(window).std()
    return (volume - mean) / std.replace(0, np.nan)


def roc(close, periods):
    out = {}
    for p in periods:
        out[p] = (close / close.shift(p) - 1) * 100
    return out


def add_all_indicators(df):
    """Attach every indicator we use downstream as new columns. In-place-ish."""
    out = df.copy()
    close = out["Close"]
    out["EMA9"] = ema(close, 9)
    out["EMA21"] = ema(close, 21)
    out["EMA50"] = ema(close, 50)
    out["EMA200"] = ema(close, 200)
    out["RSI14"] = rsi(close)
    m_line, s_line, hist = macd(close)
    out["MACD"] = m_line
    out["MACD_SIGNAL"] = s_line
    out["MACD_HIST"] = hist
    out["ATR14"] = atr(out)
    up, mid, low = bollinger(close)
    out["BB_UPPER"], out["BB_MID"], out["BB_LOWER"] = up, mid, low
    dh, dl = donchian(out["High"], out["Low"])
    out["DC_HIGH20"], out["DC_LOW20"] = dh, dl
    out["VOL_Z"] = volume_zscore(out["Volume"])
    for p in (1, 5, 20, 60):
        out[f"ROC{p}"] = roc(close, [p])[p]
    out["HIGH_52W"] = out["High"].rolling(252).max()
    out["LOW_52W"] = out["Low"].rolling(252).min()
    return out


def last_snapshot(df):
    """Return the most recent row as (label, dict). Indicator NaNs (warmup,
    zero-std windows) are forward-filled; the row must have a real Close."""
    work = df.replace([np.inf, -np.inf], np.nan).ffill()
    if work.empty:
        return None, None
    row = work.iloc[-1]
    if row.get("Close") is None or pd.isna(row.get("Close")):
        return None, None
    return work.index[-1], row.to_dict()
