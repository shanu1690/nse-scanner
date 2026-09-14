"""Regression test for nse/data.py's Yahoo ticker mapping.

Caught live: Phase 6's fusion/regime audit tried to fetch India VIX for the
first time via update_index_history("^INDIAVIX", ...), and _yahoo_ticker()
only special-cased "^NSEI" -- every other index ticker fell through to
_symbol_ns() and got ".NS" appended, producing "^INDIAVIX.NS", which
doesn't exist on Yahoo Finance (only equity/derivative underlyings need
the .NS suffix; index tickers are used as-is).
"""

from nse import data as data_mod


def test_yahoo_ticker_leaves_index_tickers_unmodified():
    assert data_mod._yahoo_ticker("^NSEI") == "^NSEI"
    assert data_mod._yahoo_ticker("^INDIAVIX") == "^INDIAVIX"


def test_yahoo_ticker_appends_ns_for_equity_symbols():
    assert data_mod._yahoo_ticker("RELIANCE") == "RELIANCE.NS"
    assert data_mod._yahoo_ticker("TCS.NS") == "TCS.NS"  # already suffixed, not doubled
