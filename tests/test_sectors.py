"""Tests for nse/sectors.py: real NSE sector/industry classification."""

import json

import pytest

from nse import sectors
from nse.nse_api import NSEUnavailable


class _FakeSession:
    def __init__(self, responses):
        self.responses = responses  # {symbol: equityResponse-shaped dict or Exception}
        self.calls = []

    def get_json(self, path, params=None, use_cache=True):
        symbol = params["symbol"]
        self.calls.append(symbol)
        resp = self.responses.get(symbol)
        if isinstance(resp, Exception):
            raise resp
        return resp

    def close(self):
        pass


def _quote_payload(macro="Energy", sector="Oil Gas & Consumable Fuels",
                    industry="Petroleum Products", basic_industry="Refineries & Marketing"):
    return {"equityResponse": [{"secInfo": {
        "macro": macro, "sector": sector, "industryInfo": industry, "basicIndustry": basic_industry,
    }}]}


# --------------------------------------------------------- fetch_symbol_sector
def test_fetch_symbol_sector_parses_real_shape():
    session = _FakeSession({"RELIANCE": _quote_payload()})
    info = sectors.fetch_symbol_sector(session, "RELIANCE")
    assert info == {"macro": "Energy", "sector": "Oil Gas & Consumable Fuels",
                    "industry": "Petroleum Products", "basic_industry": "Refineries & Marketing"}


def test_fetch_symbol_sector_none_on_unavailable():
    session = _FakeSession({"X": NSEUnavailable("blocked")})
    assert sectors.fetch_symbol_sector(session, "X") is None


def test_fetch_symbol_sector_none_on_empty_response():
    session = _FakeSession({"X": {"equityResponse": []}})
    assert sectors.fetch_symbol_sector(session, "X") is None


def test_fetch_symbol_sector_none_when_secinfo_has_no_sector_data():
    session = _FakeSession({"X": {"equityResponse": [{"secInfo": {"macro": None, "sector": None}}]}})
    assert sectors.fetch_symbol_sector(session, "X") is None


def test_fetch_symbol_sector_none_on_missing_secinfo_key():
    session = _FakeSession({"X": {"equityResponse": [{}]}})
    assert sectors.fetch_symbol_sector(session, "X") is None


# ------------------------------------------------------------- fetch_sector_map
def test_fetch_sector_map_skips_symbols_with_no_data():
    session = _FakeSession({
        "RELIANCE": _quote_payload(),
        "DELISTED": {"equityResponse": []},
    })
    result = sectors.fetch_sector_map(["RELIANCE", "DELISTED"], session=session, delay=0)
    assert set(result) == {"RELIANCE"}


def test_fetch_sector_map_does_not_close_an_injected_session():
    session = _FakeSession({"A": _quote_payload()})
    closed = []
    session.close = lambda: closed.append(True)
    sectors.fetch_sector_map(["A"], session=session, delay=0)
    assert closed == []  # caller-owned session -- fetch_sector_map must not close it


def test_fetch_sector_map_calls_every_symbol():
    session = _FakeSession({"A": _quote_payload(), "B": _quote_payload(sector="IT")})
    sectors.fetch_sector_map(["A", "B"], session=session, delay=0)
    assert session.calls == ["A", "B"]


# --------------------------------------------------------- save/load round trip
def test_save_and_load_sector_map_round_trip(tmp_path):
    path = str(tmp_path / "sector_map.json")
    data = {"RELIANCE": {"macro": "Energy", "sector": "Oil Gas & Consumable Fuels",
                         "industry": "Petroleum Products", "basic_industry": "Refineries & Marketing"}}
    sectors.save_sector_map(data, path=path)
    loaded = sectors.load_sector_map(path=path)
    assert loaded == data


def test_load_sector_map_empty_when_file_missing(tmp_path):
    assert sectors.load_sector_map(path=str(tmp_path / "nope.json")) == {}


def test_load_sector_map_empty_on_malformed_file(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json")
    assert sectors.load_sector_map(path=str(path)) == {}


def test_save_sector_map_creates_parent_dir(tmp_path):
    path = str(tmp_path / "nested" / "dir" / "sector_map.json")
    sectors.save_sector_map({"A": {"sector": "IT"}}, path=path)
    assert json.loads(open(path).read())["data"] == {"A": {"sector": "IT"}}


# ------------------------------------------------------------------- sector_only_map
def test_sector_only_map_flattens_to_symbol_sector():
    rich = {
        "RELIANCE": {"macro": "Energy", "sector": "Oil Gas & Consumable Fuels"},
        "TCS": {"macro": "Information Technology", "sector": "Information Technology"},
    }
    assert sectors.sector_only_map(rich) == {
        "RELIANCE": "Oil Gas & Consumable Fuels", "TCS": "Information Technology",
    }


def test_sector_only_map_skips_entries_without_a_sector():
    rich = {"A": {"macro": "Energy", "sector": None}, "B": {"macro": "IT", "sector": "IT Services"}}
    assert sectors.sector_only_map(rich) == {"B": "IT Services"}
