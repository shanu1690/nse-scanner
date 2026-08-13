"""Offline smoke tests: no network, no credentials needed."""

import json
import os

import nse.smartapi as smartapi
import nse.sitebuilder as sitebuilder


def test_fmt_nse_expiry():
    assert smartapi._fmt_nse_expiry("25AUG2026") == "25-Aug-2026"
    assert smartapi._fmt_nse_expiry("garbage") == "garbage"


def test_rate_limit_detection():
    assert smartapi._is_rate_limit(
        Exception("Access denied because of exceeding access rate"))
    assert smartapi._is_rate_limit(Exception("AB1021 Too many requests"))
    assert not smartapi._is_rate_limit(Exception("invalid token"))


def test_credentials_env_override(monkeypatch):
    monkeypatch.setenv("SMARTAPI_API_KEY", "envkey")
    monkeypatch.setenv("SMARTAPI_PIN", "0105")
    monkeypatch.delenv("SMARTAPI_CLIENT_ID", raising=False)
    monkeypatch.delenv("SMARTAPI_TOTP_SECRET", raising=False)
    creds = smartapi._load_credentials()
    assert creds.get("api_key") == "envkey"
    assert creds.get("pin") == "0105"


def test_price_series():
    import pandas as pd
    df = pd.DataFrame({
        "Open": [1, 2, 3], "High": [2, 3, 4], "Low": [0.5, 1.5, 2.5],
        "Close": [1.5, 2.5, 3.5], "Volume": [100, 200, 300],
    }, index=pd.date_range("2026-08-01", periods=3))
    series = sitebuilder._price_series("X", df)
    assert series[0][0] == "2026-08-01"
    assert series[-1][4] == 3.5


def test_chain_rows():
    raw = {
        "records": {
            "underlyingValue": 100.0,
            "expiryDates": ["25-Aug-2026"],
            "data": [{
                "strikePrice": 100,
                "CE": {"openInterest": 10, "changeinOpenInterest": 1,
                       "impliedVolatility": 20, "lastPrice": 5},
                "PE": {"openInterest": 15, "changeinOpenInterest": 0,
                       "impliedVolatility": 22, "lastPrice": 6},
            }],
        }
    }
    out = sitebuilder._chain_rows(raw)
    assert out["spot"] == 100.0
    assert out["rows"][0]["ce"]["oi"] == 10
    assert out["rows"][0]["pe"]["iv"] == 22


def test_sitebuilder_emits_json(tmp_path):
    out = tmp_path / "site"
    # Build a tiny fake bundle directly through the emit helpers.
    sitebuilder._dump(str(out / "data" / "manifest.json"), {"ok": True})
    parsed = json.loads((out / "data" / "manifest.json").read_text())
    assert parsed == {"ok": True}
    assert os.path.exists(out / "data" / "manifest.json")


def test_last_snapshot_ffills_zero_std_volume():
    # Regression: constant volume makes VOL_Z NaN on the final rows, which used
    # to nuke the whole last row via dropna() (bench relative-strength broke).
    import numpy as np
    import pandas as pd
    from nse import indicators as ind

    n = 250
    df = pd.DataFrame({
        "Open": np.linspace(100, 200, n),
        "High": np.linspace(101, 201, n),
        "Low": np.linspace(99, 199, n),
        "Close": np.linspace(100, 200, n),
        "Volume": [1_000_000] * n,  # constant -> zero std -> NaN z-score
    }, index=pd.date_range("2025-09-01", periods=n))
    label, row = ind.last_snapshot(ind.add_all_indicators(df))
    assert label is not None
    assert row["Close"] == 200.0
    assert "ROC20" in row


def test_smartapi_candle_dates_are_tz_naive():
    # Regression: SmartAPI timestamps arrive tz-aware (+05:30); merging them
    # with the tz-naive CSV cache index crashed sort_index().
    import pandas as pd
    import nse.smartapi as smartapi
    from unittest.mock import patch

    fake = smartapi.SmartAPISession()
    fake._obj = None
    rows = [("2026-08-13T18:30:00+05:30", 100, 102, 99, 101, 5000)]
    with patch.object(fake, "_ensure_login", return_value=None), \
         patch.object(fake, "_equity_contract",
                      return_value={"token": "2885"}), \
         patch.object(fake, "_retry_call",
                      return_value={"data": rows}):
        df = fake.candles("RELIANCE", pd.Timestamp("2026-08-13"),
                          pd.Timestamp("2026-08-13"))
    assert df.index.tz is None
    assert df.index[-1] == pd.Timestamp("2026-08-13")
