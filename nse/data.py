"""Data layer: historical OHLCV via yfinance with incremental local CSV cache."""

import os
import sys
import time

import pandas as pd

from nse.quality.corporate_actions import detect_unadjusted
from nse.quality.events import log_event

DATA_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data"))
HIST_DIR = os.path.join(DATA_DIR, "cache")

LOOKBACK_DAYS = 560  # fetch ~560 calendar days so EMA200/52w-high backtests work

_PROVIDER = None


def _provider():
    """data.provider from config.yaml ('smartapi' | 'nse'), cached."""
    global _PROVIDER
    if _PROVIDER is None:
        try:
            import yaml
            with open(os.path.join(HIST_DIR, "..", "..", "config.yaml")) as fh:
                cfg = yaml.safe_load(fh) or {}
            _PROVIDER = (cfg.get("data") or {}).get("provider", "nse")
        except (OSError, ValueError):
            _PROVIDER = "nse"
    return _PROVIDER


def _symbol_ns(symbol):
    return symbol if symbol.endswith(".NS") else f"{symbol}.NS"


def _yahoo_ticker(symbol):
    if symbol == "^NSEI":
        return "^NSEI"
    return _symbol_ns(symbol)


def _fetch_smartapi(symbol, start):
    """Download history via Angel One SmartAPI; None (fallback) if unavailable."""
    from nse import smartapi
    session = smartapi.get_shared_session()
    return session.candles(symbol, pd.Timestamp(start).date(),
                           pd.Timestamp.today())


def _clean_combined(old_df, fresh_df, symbol, source_label):
    """Merge the existing cache with this run's fresh full-window fetch,
    refusing to persist a blend that looks like two adjustment regimes
    stitched together.

    Each side can be internally clean on its own and still disagree with
    the other -- e.g. a cache built under yfinance (split/dividend-adjusted)
    merged with a newer SmartAPI fetch that has a hole in it (a failed
    chunk request), leaving the stale adjusted value sitting next to
    freshly-raw neighbours right at the gap. `fresh_df` always covers the
    full lookback window by itself, so when the blend looks wrong the safe
    move is to drop the stale half rather than guess which side is right.
    """
    if old_df is None or not len(old_df):
        return fresh_df
    combined = pd.concat([old_df, fresh_df])
    combined = combined[~combined.index.duplicated(keep="last")]
    combined = combined[combined.index.notna()].sort_index()
    if detect_unadjusted(combined.rename(columns=str.lower)).empty:
        return combined
    message = (f"cached history disagrees with the fresh {source_label} fetch "
               f"(looks like an adjusted/unadjusted mismatch) -> dropping the "
               f"stale cache, keeping only this run's fetch")
    print(f"  ! {symbol}: {message}", file=sys.stderr)
    log_event(symbol, "regime_mismatch_drop", message)
    return fresh_df


def update_price_history(symbol, lookback_days=LOOKBACK_DAYS, force=False):
    """Download (or refresh) daily OHLCV for one symbol into cache CSV.

    Returns the DataFrame. Reuses the local file when it is already up to date
    (or forces a fresh download when force=True, used by the pick tracker).
    """
    import yfinance as yf

    path = os.path.join(HIST_DIR, f"{symbol}.csv")
    df = None
    if os.path.exists(path):
        try:
            df = pd.read_csv(path, parse_dates=["Date"], index_col="Date")
            if getattr(df.index, "tz", None) is not None:
                df.index = df.index.tz_localize(None)
        except (ValueError, OSError):
            df = None

    start = pd.Timestamp.today() - pd.Timedelta(days=lookback_days)
    expected_bars = int(lookback_days * 0.6)  # 5 trading days / 7 calendar
    if (not force and df is not None and len(df)
            and df.index.max() >= start.normalize()
            and len(df) >= expected_bars):
        return df

    if _provider() == "smartapi":
        try:
            smart = _fetch_smartapi(symbol, start)
        except (ImportError, OSError, RuntimeError) as exc:
            print(f"  ! smartapi {symbol}: {exc} -> yfinance fallback",
                  file=sys.stderr)
            log_event(symbol, "provider_fallback", str(exc))
            smart = None
        if smart is not None and len(smart):
            combined = _clean_combined(df, smart, symbol, "SmartAPI")
            combined.to_csv(path)
            return combined
        print(f"  ! smartapi {symbol}: unavailable -> yfinance fallback", file=sys.stderr)

    ticker = _yahoo_ticker(symbol)
    last_err = None
    for attempt in range(3):
        try:
            raw = yf.download(
                ticker,
                start=start.strftime("%Y-%m-%d"),
                auto_adjust=True,
                progress=False,
                threads=True,
            )
            if raw is not None and not raw.empty:
                break
            last_err = ValueError(f"empty data for {symbol}")
        except Exception as exc:  # yfinance raises on invalid/throttled symbols
            last_err = exc
        time.sleep(5 * (attempt + 1))
    else:
        if df is not None and len(df):
            return df
        raise last_err or ValueError(f"No data for {symbol}")

    if isinstance(raw.columns, pd.MultiIndex):
        raw = raw.droplevel(1, axis=1)
    raw = raw.rename(columns={"Open": "Open", "High": "High", "Low": "Low",
                              "Close": "Close", "Volume": "Volume"})
    raw = raw[~raw.index.duplicated(keep="last")].sort_index()

    combined = _clean_combined(df, raw, symbol, "yfinance")
    combined.to_csv(path)
    return combined


def load_price_history(symbol):
    """Load cached history without hitting the network (fast, offline-safe)."""
    path = os.path.join(HIST_DIR, f"{symbol}.csv")
    if not os.path.exists(path):
        return None
    return pd.read_csv(path, parse_dates=["Date"], index_col="Date")


def update_many(symbols, lookback_days=LOOKBACK_DAYS, delay=0.5, quiet=True):
    """Sequential update for a universe; returns {symbol: df} for fresh symbols."""
    out = {}
    for s in symbols:
        try:
            out[s] = update_price_history(s, lookback_days)
            if not quiet:
                print(f"  updated {s}")
            time.sleep(delay)
        except (ValueError, RuntimeError) as exc:
            if not quiet:
                print(f"  SKIP {s}: {exc}")
    return out


def update_index_history(symbol="^NSEI", lookback_days=LOOKBACK_DAYS, force=False):
    """NIFTY 50 benchmark for relative-strength. Returns DataFrame (cached)."""
    return update_price_history(symbol, lookback_days, force=force)
