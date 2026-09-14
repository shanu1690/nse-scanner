"""Phase 2 wiring: nse/quality/validators.py's DataValidator gates the
sitebuilder.build() publish step, per PHASE2-INTEGRATION-EXAMPLES.md section 4.

The contract: a FAIL blocks publishing and the existing bundle stays as-is.
In this repo's CI-driven, no-persistent-disk deployment, "stays as-is" means
build() raises before any file in <out_dir>/data is written, so the CI step
fails and the later upload/deploy steps in the same job never run.
"""

import numpy as np
import pandas as pd
import pytest

import nse.cli as cli_mod
import nse.sitebuilder as sb
import nse.tracker as tracker_mod
from nse.quality.validators import DataValidator


def _synthetic_prices(symbols, n=300, seed=0):
    """Enough bars (n >= 252) that every indicator (incl. HIGH_52W) is warm."""
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    out = {}
    for i, sym in enumerate(symbols):
        close = 100 + i * 10 + rng.normal(0, 1, n).cumsum()
        close = np.maximum(close, 1.0)
        out[sym] = pd.DataFrame({
            "Open": close + rng.uniform(-0.5, 0.5, n),
            "High": close + rng.uniform(0.1, 1.0, n),
            "Low": close - rng.uniform(0.1, 1.0, n),
            "Close": close,
            "Volume": rng.integers(100_000, 200_000, n),
        }, index=idx)
    return out


def _wire_cli(monkeypatch, symbols, prices, bench):
    monkeypatch.setattr(cli_mod, "SYMBOLS", symbols)
    monkeypatch.setattr(cli_mod, "MOM_CFG", {"min_score": 0.0, "top_n": len(symbols),
                                              "style": "momentum"})
    monkeypatch.setattr(cli_mod, "OPT_CFG", {"top_n": 5, "use_oi_prescreen": True})
    monkeypatch.setattr(cli_mod, "DATA_CFG", {"lookback_days": 300,
                                               "index_benchmark": "^TEST",
                                               "provider": "test"})
    monkeypatch.setattr(cli_mod, "_prefer_fresh_prices", lambda *a, **k: (prices, bench))
    monkeypatch.setattr(cli_mod, "scan_options", lambda *a, **k: [])
    monkeypatch.setattr(tracker_mod, "scorecard_data", lambda *a, **k: None)


def test_healthy_run_publishes_and_records_coverage(tmp_path, monkeypatch):
    symbols = [f"SYM{i}" for i in range(10)]
    prices = _synthetic_prices(symbols)
    _wire_cli(monkeypatch, symbols, prices, prices["SYM0"])

    out_dir = tmp_path / "site"
    files = sb.build(str(out_dir), top_n=5, quiet=True)

    assert files  # delivery/options/scorecard/manifest all written
    manifest = pd.read_json(out_dir / "data" / "manifest.json", typ="series")
    assert manifest["scored_rows"] == 10
    assert manifest["coverage_pct"] == 1.0


def test_poor_coverage_blocks_publish_and_writes_nothing(tmp_path, monkeypatch):
    """Only 2 of 10 universe symbols come back with price history -- well
    under DataValidator's default 95% coverage floor."""
    symbols = [f"SYM{i}" for i in range(10)]
    all_prices = _synthetic_prices(symbols)
    thin_prices = {s: all_prices[s] for s in symbols[:2]}
    _wire_cli(monkeypatch, symbols, thin_prices, all_prices["SYM0"])

    out_dir = tmp_path / "site"
    with pytest.raises(sb.PublishBlocked, match="data validation failed"):
        sb.build(str(out_dir), top_n=5, quiet=True)

    assert not (out_dir / "data").exists()  # nothing partially written


def test_validation_frame_pulls_raw_ohlcv_not_the_score_payload():
    prices = _synthetic_prices(["A", "B"])
    scored = [{"symbol": "A", "score": 61.2}, {"symbol": "B", "score": 40.0}]
    frame = sb._validation_frame(scored, prices, pd.Timestamp.now(tz="UTC"))
    assert set(frame["symbol"]) == {"A", "B"}
    assert frame.loc[frame["symbol"] == "A", "close"].iloc[0] == \
        pytest.approx(float(prices["A"]["Close"].iloc[-1]))


def test_dump_refuses_a_credential_shaped_payload(tmp_path):
    with pytest.raises(sb.PublishBlocked, match="credential-shaped"):
        sb._dump(str(tmp_path / "leaky.json"), {"api_key": "sk-should-not-be-here"})
    assert not (tmp_path / "leaky.json").exists()


def test_dump_writes_normally_otherwise(tmp_path):
    path = sb._dump(str(tmp_path / "ok.json"), {"symbol": "X", "score": 1})
    assert (tmp_path / "ok.json").exists()
    assert path == str(tmp_path / "ok.json")
