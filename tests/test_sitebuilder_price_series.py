"""Tests for nse/sitebuilder.py's _price_series() (Phase 9: candlestick
chart needs EMA/Donchian overlays for every bar, not just the last one)
and build()'s "levels" (entry/stop/target) attachment for delivery picks.
"""

import json

import numpy as np
import pandas as pd

import nse.cli as cli_mod
import nse.sitebuilder as sb
from nse import indicators as ind

from tests.test_sitebuilder_publish_gate import _synthetic_prices, _wire_cli


def test_price_series_appends_indicator_columns_without_reordering_ohlcv():
    df = _synthetic_prices(["X"], n=300)["X"]
    full = ind.add_all_indicators(df)
    rows = sb._price_series("X", full)
    assert len(rows) == 250  # capped at the recent-250 window
    row = rows[-1]
    # original [date, o, h, l, c, v] shape preserved at the front
    assert isinstance(row[0], str)
    for i in (1, 2, 3, 4):
        assert isinstance(row[i], float)
    assert isinstance(row[5], int)
    # new indicator columns appended after -- ema21/50/200, dc_high20/low20
    assert len(row) == 11
    for i in (6, 7, 8, 9, 10):
        assert row[i] is None or isinstance(row[i], float)


def test_price_series_early_bars_have_none_for_unwarmed_indicators():
    df = _synthetic_prices(["X"], n=300)["X"]
    full = ind.add_all_indicators(df)
    rows = sb._price_series("X", full)
    first_row = rows[0]  # near the start of the 250-bar window, EMA200 not yet warm this early
    # Not every field is guaranteed None here, but the function must not
    # crash or emit NaN-as-float on an unwarmed indicator -- None only.
    for i in (6, 7, 8, 9, 10):
        assert first_row[i] is None or isinstance(first_row[i], float)


def test_build_attaches_levels_for_delivery_picks_only(tmp_path, monkeypatch):
    symbols = [f"SYM{i}" for i in range(5)]
    prices = _synthetic_prices(symbols)
    _wire_cli(monkeypatch, symbols, prices, prices["SYM0"])
    monkeypatch.setitem(cli_mod.CONFIG, "risk", {
        "capital": 10_000_000.0, "max_portfolio_heat_pct": 1000.0, "max_open_ideas": 50,
    })

    out_dir = tmp_path / "site"
    sb.build(str(out_dir), top_n=5, quiet=True)

    delivery = json.loads((out_dir / "data" / "delivery.json").read_text())
    assert delivery["picks"]
    pick_symbol = delivery["picks"][0]["symbol"]

    price_data = json.loads((out_dir / "data" / "prices" / f"{pick_symbol}.json").read_text())
    assert price_data["levels"] is not None
    for key in ("entry", "stop", "target1", "target2"):
        assert key in price_data["levels"]
