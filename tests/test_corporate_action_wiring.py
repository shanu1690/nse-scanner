"""Phase 2 wiring: nse/quality/corporate_actions.py plugged into the price path.

Critical Risk #1 (Phase 1 audit): SmartAPI's getCandleData returns raw,
un-split/bonus-adjusted candles. Section 1 of PHASE2-INTEGRATION-EXAMPLES.md
says to gate price loading with detect_unadjusted(); here that gate lives in
SmartAPISession.candles() itself (nse/smartapi.py), reusing the class's
existing "every failure raises SmartAPIUnavailable, callers fall back to
yfinance" contract instead of inventing a new one.

Risk #10: a symbol's local cache can end up as a patchwork of adjusted
(yfinance) and unadjusted (SmartAPI) segments stitched together across runs.
nse/data.py's _clean_combined() guards the merge step itself.
"""

import pandas as pd
import pytest

import nse.quality.events as events_mod
from nse.data import _clean_combined
from nse.quality.events import read_events
from nse.smartapi import SmartAPISession, SmartAPIUnavailable


def _candles(dates, closes):
    return [
        {"timestamp": d, "open": c, "high": c * 1.01, "low": c * 0.99,
         "close": c, "volume": 1000}
        for d, c in zip(dates, closes)
    ]


class _FakeObj:
    def __init__(self, rows):
        self._rows = rows

    def getCandleData(self, req):
        return {"data": self._rows}


def _session_with_candles(rows):
    sess = SmartAPISession()
    sess._obj = _FakeObj(rows)  # skips _ensure_login (obj already set)
    sess._equity_contract = lambda symbol: {"token": "1234"}
    return sess


def test_candles_raises_on_unadjusted_split(tmp_path, monkeypatch):
    """A raw 1:2 split in SmartAPI's own candle window must not reach the cache,
    and the rejection is recorded as a DataEvent, not just printed."""
    monkeypatch.setattr(events_mod, "DEFAULT_EVENTS_PATH", tmp_path / "events.jsonl")
    dates = [f"2026-01-{d:02d}T00:00:00" for d in range(1, 6)]
    closes = [100, 100, 100, 50, 50]  # unadjusted split on day 4
    sess = _session_with_candles(_candles(dates, closes))
    with pytest.raises(SmartAPIUnavailable, match="unadjusted-looking"):
        sess.candles("TESTSYM", "2026-01-01", "2026-01-05")

    events = read_events(symbol="TESTSYM")
    assert len(events) == 1
    assert events[0].kind == "unadjusted_split_rejected"


def test_candles_pass_through_on_ordinary_moves():
    """Normal volatility (no clean-fraction jump) must not be blocked."""
    dates = [f"2026-01-{d:02d}T00:00:00" for d in range(1, 6)]
    closes = [100, 103, 99, 101, 104]
    sess = _session_with_candles(_candles(dates, closes))
    df = sess.candles("TESTSYM", "2026-01-01", "2026-01-05")
    assert len(df) == 5
    assert df["Close"].iloc[-1] == 104


def _frame(dates, close):
    idx = pd.DatetimeIndex(dates, name="Date")
    return pd.DataFrame({
        "Open": close, "High": [c * 1.01 for c in close],
        "Low": [c * 0.99 for c in close], "Close": close,
        "Volume": [1000] * len(close),
    }, index=idx)


def test_clean_combined_returns_fresh_when_no_prior_cache():
    fresh = _frame(pd.date_range("2026-01-01", periods=5), [10, 11, 12, 13, 14])
    out = _clean_combined(None, fresh, "TESTSYM", "smartapi")
    assert out is fresh


def test_clean_combined_merges_consistent_segments():
    old = _frame(pd.date_range("2026-01-01", periods=3), [10, 10.2, 10.1])
    fresh = _frame(pd.date_range("2026-01-04", periods=3), [10.3, 10.4, 10.5])
    out = _clean_combined(old, fresh, "TESTSYM", "smartapi")
    assert len(out) == 6
    assert list(out["Close"]) == [10, 10.2, 10.1, 10.3, 10.4, 10.5]


def test_clean_combined_drops_stale_cache_on_regime_mismatch(tmp_path, monkeypatch, capsys):
    """Old (adjusted-scale) cache butting against a freshly-raw fetch at a gap
    must not be silently persisted -- the stale half is dropped instead, and
    the drop is recorded as a DataEvent, not just printed."""
    monkeypatch.setattr(events_mod, "DEFAULT_EVENTS_PATH", tmp_path / "events.jsonl")
    old = _frame(pd.date_range("2026-01-01", periods=5), [100, 100, 100, 100, 100])
    fresh = _frame(pd.date_range("2026-01-06", periods=5), [200, 200, 201, 199, 200])
    out = _clean_combined(old, fresh, "TESTSYM", "SmartAPI")
    assert out is fresh
    assert "adjusted/unadjusted mismatch" in capsys.readouterr().err

    events = read_events(symbol="TESTSYM")
    assert len(events) == 1
    assert events[0].kind == "regime_mismatch_drop"


def test_provider_fallback_does_not_leave_a_seam_on_disk(tmp_path, monkeypatch, capsys):
    """End-to-end: SmartAPI rejects a symbol (detected split) mid-history, the
    code falls back to yfinance -- does the switch itself fabricate a seam
    at the old-cache/new-fetch boundary, the same class of bug
    _clean_combined() exists to catch? It should not, because the fallback
    merge in update_price_history() runs through that same guard.

    Both the SmartAPI rejection and the merge-guard's own detection appear in
    stderr AND are recorded as DataEvents -- closing the "surfaced only as
    log text, no structured/counted record" gap this test used to document.
    """
    import nse.data as data_mod

    symbol = "TESTSYM"
    monkeypatch.setattr(data_mod, "HIST_DIR", str(tmp_path))
    monkeypatch.setattr(data_mod, "_PROVIDER", "smartapi")
    monkeypatch.setattr(events_mod, "DEFAULT_EVENTS_PATH", tmp_path / "events.jsonl")

    # Stale on-disk cache: raw/pre-split scale, entirely before the new fetch's
    # coverage window (the "old regime" segment).
    old_path = tmp_path / f"{symbol}.csv"
    old = _frame(pd.date_range("2020-01-01", periods=5), [200, 200, 200, 200, 200])
    old.to_csv(old_path)

    # SmartAPI rejects the fetch outright (simulating candles() having just
    # caught the split within its own window per test_candles_raises_on_
    # unadjusted_split above -- exercised in isolation there, not re-derived
    # here).
    def fake_fetch_smartapi(sym, start):
        raise SmartAPIUnavailable(
            f"{sym}: 1 unadjusted-looking price jump(s) in SmartAPI candles"
        )

    monkeypatch.setattr(data_mod, "_fetch_smartapi", fake_fetch_smartapi)

    # yfinance fallback: freshly-adjusted, half the old cache's scale --
    # exactly what a real split/bonus adjustment looks like.
    fresh = _frame(pd.date_range("2020-01-11", periods=5), [100, 100, 100, 100, 100])

    def fake_download(*args, **kwargs):
        return fresh

    import yfinance
    monkeypatch.setattr(yfinance, "download", fake_download)

    result = data_mod.update_price_history(symbol, lookback_days=5, force=True)

    err = capsys.readouterr().err
    assert "unadjusted-looking" in err          # the SmartAPI rejection reason
    assert "adjusted/unadjusted mismatch" in err  # the merge-guard's own catch

    # The seam was caught: the stale pre-split segment was dropped, not
    # stitched onto the fresh series.
    assert len(result) == 5
    assert result.index.min() == pd.Timestamp("2020-01-11")
    assert (result["Close"] == 100).all()

    on_disk = pd.read_csv(old_path, parse_dates=["Date"], index_col="Date")
    assert len(on_disk) == 5
    assert (on_disk["Close"] == 100).all()

    events = {e.kind for e in read_events(symbol=symbol)}
    assert events == {"provider_fallback", "regime_mismatch_drop"}
