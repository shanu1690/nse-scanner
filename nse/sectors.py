"""Real NSE sector/industry classification.

Closes the gap nse/risk/__init__.py flagged: no verified sector data
source existed as of Phase 8/9 -- NSE's older per-symbol metadata endpoint
(`/api/equity-meta-info`) and its sectoral-index constituent endpoint
(`/api/equity-stockIndices`) both returned a genuine "Resource not found"
when checked live (not a bot-block). NSE has since restructured its own
equity quote page onto a different API surface entirely
(`/api/NextApi/apiClient/GetQuoteApi?functionName=...`), discovered live
by loading nseindia.com's real equity quote page in the interactive
Browser tool and reading its own network requests -- the same technique
that worked when direct endpoint guesses didn't. That page's own
`getSymbolData` call carries a real, official 4-level classification per
symbol:

    macro > sector > industryInfo > basicIndustry

e.g. RELIANCE: Energy > Oil Gas & Consumable Fuels > Petroleum Products >
Refineries & Marketing. Verified live (not fabricated from memory) for
RELIANCE / TCS / HDFCBANK before building this module, and reachable
through the existing nse_api.NSESession (requests backend, no Playwright
needed) once primed against the right landing page -- the same "a session
primed on the wrong page doesn't unlock a different page's endpoints"
quirk this project already found for /companies-listing/*.

`sector` (the second level) is what nse/risk/enforce.py's sector cap uses:
coarse enough that a ~200-symbol universe doesn't fragment into near-
singleton buckets, granular enough to actually distinguish, e.g., "Oil Gas
& Consumable Fuels" from a bare "Energy". All four levels are cached
though, in case a coarser/finer cap is wanted later.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from typing import Optional

from .nse_api import NSESession, NSEUnavailable

__all__ = [
    "fetch_symbol_sector", "fetch_sector_map", "load_sector_map", "save_sector_map",
    "sector_only_map", "DEFAULT_SECTOR_MAP_PATH",
]

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_SECTOR_MAP_PATH = os.path.join(ROOT, "config", "sector_map.json")
# Any real, currently-listed symbol works to prime the session -- the
# landing page itself doesn't need to match the symbol being queried, only
# to be the right PAGE (Akamai ties bot-check validity to page context,
# not to the specific query string).
LANDING_PATH = "/get-quotes/equity?symbol=RELIANCE"
REQUEST_GAP = 1.0  # seconds between symbols -- courtesy delay, on top of NSESession's own


def fetch_symbol_sector(session: NSESession, symbol: str) -> Optional[dict]:
    """{"macro", "sector", "industry", "basic_industry"} for one symbol,
    or None when NSE has nothing usable for it (newly listed, delisted,
    or a transient API hiccup) -- callers must treat that as "unknown",
    never guess a value to fill the gap.
    """
    try:
        data = session.get_json(
            "/api/NextApi/apiClient/GetQuoteApi",
            params={"functionName": "getSymbolData", "marketType": "N", "series": "EQ", "symbol": symbol},
        )
    except NSEUnavailable:
        return None
    responses = (data or {}).get("equityResponse") or []
    if not responses:
        return None
    sec = responses[0].get("secInfo") or {}
    macro, sector = sec.get("macro") or None, sec.get("sector") or None
    if not macro and not sector:
        return None
    return {
        "macro": macro,
        "sector": sector,
        "industry": sec.get("industryInfo") or None,
        "basic_industry": sec.get("basicIndustry") or None,
    }


def fetch_sector_map(universe: list, *, session: Optional[NSESession] = None,
                      delay: float = REQUEST_GAP, quiet: bool = True) -> dict:
    """{symbol: {"macro", "sector", "industry", "basic_industry"}} for
    every symbol NSE has data for. A symbol NSE has nothing for is simply
    absent from the result -- see fetch_symbol_sector's contract.
    """
    owns_session = session is None
    session = session or NSESession(landing_path=LANDING_PATH)
    out: dict = {}
    try:
        for i, symbol in enumerate(universe):
            info = fetch_symbol_sector(session, symbol)
            if info is not None:
                out[symbol] = info
            if not quiet:
                status = info if info is not None else "no sector data"
                print(f"  [{i + 1}/{len(universe)}] {symbol}: {status}")
            if i < len(universe) - 1:
                time.sleep(delay)
    finally:
        if owns_session:
            session.close()
    return out


def save_sector_map(sector_map: dict, path: str = DEFAULT_SECTOR_MAP_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {"_fetched_at": datetime.now(timezone.utc).isoformat(), "data": sector_map}
    with open(path, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)


def load_sector_map(path: str = DEFAULT_SECTOR_MAP_PATH) -> dict:
    """{} if nothing has been fetched yet, or the file is unreadable --
    nse/risk/ already treats an empty/missing sector_map as "the cap can't
    be enforced yet", not an error, so this refuses to guess rather than
    raising."""
    try:
        with open(path) as fh:
            payload = json.load(fh)
        return payload.get("data") or {}
    except (OSError, ValueError):
        return {}


def sector_only_map(sector_map: dict) -> dict:
    """{symbol: sector} -- the flat mapping nse/risk/enforce.py's
    `sector_map` parameter actually wants, derived from the richer
    4-level data this module stores."""
    return {sym: info["sector"] for sym, info in sector_map.items() if info.get("sector")}
