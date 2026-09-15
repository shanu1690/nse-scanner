"""BSE XBRL fundamentals ingestion (Phase 5).

Source: individual company financial-result filings BSE publishes in
inline-XBRL (iXBRL) format -- a regulatory disclosure, not the paid bulk
"Corporate Data Information Products" BSE also sells (see
PHASE2-INTEGRATION-EXAMPLES.md / the Phase 4 ToS notes for the distinction).
No bot-blocking observed on any of these endpoints, unlike NSE's
Akamai-protected corporate-filings pages -- plain HTTP works, no session or
cookie priming needed.

Endpoints (found via one live browser session on bseindia.com's Financial
Results page, then verified standalone -- see PROJECT_BRIEF.md Phase 5
notes for the discovery trail):

  scrip lookup:    api.bseindia.com/MSource/1D/SmartSearchPeer.aspx
                   ?Type=EQ&searchString={ticker}
  filing index:    api.bseindia.com/BseIndiaAPI/api/Corp_FinanceResult_ng_new/w
                   ?SCRIP_CD=...&FlagDur=&HFQ=&ISUBGROUP_CODE=&segment=C
                   &FROMDT=DD/MM/YYYY&TODT=DD/MM/YYYY
                   -- omitting FROMDT/TODT silently returns an empty table;
                   this cost real time to discover, don't drop it again.
  filing document: www.bseindia.com/XBRLFILES/{XMLName}
                   -- only the *.html (IFIndas...) documents are reliably
                   served; the sibling *.xml (FourOneUploadDocument) rows
                   the index also returns 404 in every case checked, so
                   rows without an .html name are skipped.

Point-in-time fields come from BSE's own labels, not inferred:
  period_end    <- in-capmkt:DateOfEndOfReportingPeriod (the QUARTER's own
                   end date -- in-capmkt:DateOfEndOfFinancialYear is the
                   full fiscal year and would be wrong here)
  published_at  <- the filing index row's own Fld_CreateDate (when BSE's
                   system recorded the filing) rather than the document's
                   date-only board-meeting field, which has no time
                   component and would be a strictly worse timestamp.

Every numeric/text fact in the filing is inline-XBRL tagged
(<ix:nonFraction name='in-capmkt:...'>/<ix:nonNumeric name='in-capmkt:...'>)
with a stable SEBI-taxonomy name -- parsed directly rather than guessing at
visual table layout, which varies enough between filers/periods that
position-based parsing would be fragile.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

from nse.pit.store import PITRecord, PointInTimeStore

try:
    from zoneinfo import ZoneInfo
    IST = ZoneInfo("Asia/Kolkata")
except ImportError:  # pragma: no cover
    IST = timezone(timedelta(hours=5, minutes=30))

__all__ = [
    "find_scrip_code", "fetch_filing_index", "fetch_document",
    "parse_ixbrl", "extract_filing", "ingest_symbol", "FACTOR_TAGS",
]

API_BASE = "https://api.bseindia.com"
DOC_BASE = "https://www.bseindia.com"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/html, */*",
    "Referer": "https://www.bseindia.com/corporates/Comp_ResultsNew",
}
REQUEST_GAP = 0.5  # seconds between calls -- polite, not load-tested further

# A curated subset of SEBI in-capmkt taxonomy tags, mapped to the quality/
# growth/valuation/balance-sheet factor names Phase 5 asks for. Every filing
# tags ~50-80 facts; this is deliberately not all of them -- add more here
# once run_factor_analysis-style auditing says a specific one is worth it.
#
# Banks/NBFCs file under a DIFFERENT SEBI taxonomy layout (Regulation 33
# "Format B" -- verified live against HDFCBANK/ICICIBANK/SBIN/KOTAKBANK/
# AXISBANK filings) that uses different tag names for the same concept:
# e.g. "ProfitLossForThePeriod" instead of "ProfitLossForPeriod",
# "ProfitLossFromOrdinaryActivitiesBeforeTax" instead of "ProfitBeforeTax",
# "BasicEarningsPerShareBeforeExtraordinaryItems" instead of the
# "...FromContinuingAndDiscontinuedOperations" variant, and no top-level
# "RevenueFromOperations" tag at all -- a bank's core-business revenue is
# tagged "InterestEarned" instead. Each entry below is a tuple of candidate
# tags tried in order (industrial-format first, bank/NBFC fallback second);
# the first one actually present in a given filing wins. "TaxExpense",
# "Income" and "PaidUpValueOfEquityShareCapital" use the same tag name in
# both formats, so those stay single-candidate.
#
# Two factors are intentionally left industrial-only, not "missing"
# fallbacks: "debt_equity_ratio" and "finance_costs" don't have an honest
# bank equivalent. A bank's "InterestExpended" is its core operating cost
# (funding deposits/borrowings to re-lend), not debt-servicing cost on top
# of operations the way "finance costs" means for a non-financial company --
# mapping it in would make debt_equity_ratio/interest_coverage silently
# compare an apples factor to an oranges one. Better to genuinely have no
# data for banks here than to fabricate a number that looks comparable but
# isn't.
#
# NBFC filings (e.g. BAJFINANCE) are a separate, harder gap: BSE's filing
# index for them returns ONLY .xml-suffixed document names (no ".html"
# duplicate the way industrial/bank filings get) and the raw .xml URL
# genuinely 404s on BSE's own server (verified live) -- there is currently
# no known way to fetch NBFC filing content at all, so ingest_symbol()
# correctly returns 0 records for them. That's a document-access problem,
# not a taxonomy-mapping one, and isn't fixed by this tag table.
FACTOR_TAGS = {
    "revenue_from_operations": ("RevenueFromOperations", "InterestEarned"),
    "total_income": ("Income",),
    "total_expenses": ("Expenses",),
    "profit_before_tax": ("ProfitBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"),
    "tax_expense": ("TaxExpense",),
    "net_profit": ("ProfitLossForPeriod", "ProfitLossForThePeriod"),
    "comprehensive_income": ("ComprehensiveIncomeForThePeriod",),
    "eps_basic": ("BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                  "BasicEarningsPerShareBeforeExtraordinaryItems"),
    "eps_diluted": ("DilutedEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
                    "DilutedEarningsPerShareBeforeExtraordinaryItems"),
    "debt_equity_ratio": ("DebtEquityRatio",),
    "paid_up_equity_capital": ("PaidUpValueOfEquityShareCapital",),
    "finance_costs": ("FinanceCosts",),
}

_NUM_FACT_RE = re.compile(
    r"<ix:nonFraction\b[^>]*?\bname=['\"]([^'\"]+)['\"][^>]*>([^<]*)</ix:nonFraction>"
)
_TXT_FACT_RE = re.compile(
    r"<ix:nonNumeric\b[^>]*?\bname=['\"]([^'\"]+)['\"][^>]*>([^<]*)</ix:nonNumeric>"
)


def _unescape(s: str) -> str:
    return (s.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
             .replace("&quot;", '"').replace("&#39;", "'"))


def find_scrip_code(ticker: str, *, timeout: int = 15) -> Optional[str]:
    """BSE scrip code for an NSE ticker, via exact match on the search
    API's own `ID` field (the search is fuzzy and returns several
    companies for a short ticker; only an exact match is trustworthy)."""
    try:
        resp = requests.get(
            f"{API_BASE}/MSource/1D/SmartSearchPeer.aspx",
            params={"Type": "EQ", "searchString": ticker},
            headers=HEADERS, timeout=timeout,
        )
        resp.raise_for_status()
        matches = resp.json()
    except (requests.RequestException, ValueError):
        return None
    for m in matches or []:
        if str(m.get("ID", "")).upper() == ticker.upper():
            return str(m.get("scripcode"))
    return None


def fetch_filing_index(scrip_code: str, from_date: date, to_date: date,
                        *, timeout: int = 20) -> list[dict]:
    """Raw filing-index rows for one BSE scrip code. FROMDT/TODT are
    required -- omitting them returns an empty table, not "all time"."""
    try:
        resp = requests.get(
            f"{API_BASE}/BseIndiaAPI/api/Corp_FinanceResult_ng_new/w",
            params={
                "SCRIP_CD": scrip_code, "FlagDur": "", "HFQ": "",
                "ISUBGROUP_CODE": "", "segment": "C",
                "FROMDT": from_date.strftime("%d/%m/%Y"),
                "TODT": to_date.strftime("%d/%m/%Y"),
            },
            headers=HEADERS, timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("Table") or []
    except (requests.RequestException, ValueError):
        return []


def fetch_document(xml_name: str, *, timeout: int = 20) -> Optional[str]:
    try:
        resp = requests.get(f"{DOC_BASE}/XBRLFILES/{xml_name}",
                            headers=HEADERS, timeout=timeout)
        if resp.status_code != 200:
            return None
        return resp.text
    except requests.RequestException:
        return None


def parse_ixbrl(html: str) -> dict:
    """Every inline-XBRL tagged fact in the document, keyed by tag name
    with the taxonomy prefix stripped ("in-capmkt:Income" -> "Income").
    Numeric facts (ix:nonFraction) are parsed as float with the
    Indian-format thousands commas stripped; text facts (ix:nonNumeric)
    are unescaped strings. Where a tag repeats across contexts
    (comparative periods, segments), the first occurrence wins -- checked
    against real filings, the current-period context reliably comes first.
    """
    facts: dict = {}
    for name, val in _NUM_FACT_RE.findall(html):
        tag = name.rsplit(":", 1)[-1]
        if tag in facts:
            continue
        val = val.strip()
        if not val:
            facts[tag] = None
            continue
        try:
            facts[tag] = float(val.replace(",", ""))
        except ValueError:
            facts[tag] = None
    for name, val in _TXT_FACT_RE.findall(html):
        tag = name.rsplit(":", 1)[-1]
        if tag in facts:
            continue
        facts[tag] = _unescape(val.strip())
    return facts


def _parse_ddmmyyyy(s: str) -> Optional[date]:
    try:
        return datetime.strptime(s.strip(), "%d-%m-%Y").date()
    except (ValueError, AttributeError):
        return None


# Indian fiscal quarters are fixed calendar ranges (FY runs Apr-Mar):
# First=Apr-Jun, Second=Jul-Sep, Third=Oct-Dec, Fourth=Jan-Mar. Keyed by the
# ReportingQuarter tag's leading ordinal word, lowercased.
_FISCAL_QUARTER_END = {
    "first": (6, 30), "second": (9, 30), "third": (12, 31), "fourth": (3, 31),
}


def _derive_period_end_from_quarter(facts: dict) -> Optional[date]:
    """Fallback period_end for filings that omit DateOfEndOfReportingPeriod
    entirely -- seen live in a handful of real BSE filings from a SEBI
    taxonomy-version transition around April-July 2025 (e.g. RELIANCE's Q4
    FY25 and Q1 FY26 results) that tag only DateOfStartOfFinancialYear plus
    a ReportingQuarter ordinal instead. Since Indian fiscal quarters are
    fixed calendar ranges, which ordinal quarter of which fiscal year fully
    determines the period end -- reconstructing it here means these
    filings' data doesn't just vanish from the point-in-time store (Phase 6
    caught this: with these two quarters silently dropped, no symbol had a
    valid year-ago comparison and every growth-YoY factor came back empty
    across the entire universe, not because growth was noise but because
    the data pipeline missed a duplicate/backfilled record).
    """
    fy_start = _parse_ddmmyyyy(facts.get("DateOfStartOfFinancialYear", ""))
    if fy_start is None:
        return None
    ordinal = (facts.get("ReportingQuarter") or "").strip().lower().split(" ")[0]
    month_day = _FISCAL_QUARTER_END.get(ordinal)
    if month_day is None:
        return None
    month, day = month_day
    year = fy_start.year + 1 if ordinal == "fourth" else fy_start.year
    return date(year, month, day)


def _parse_bse_created(s: str) -> Optional[datetime]:
    """Fld_CreateDate looks like '2026-07-17T19:48:51.41', naive, in IST
    (BSE's own timezone) -- localize then convert to UTC.

    The fractional-seconds part is a variable number of digits (".41",
    ".5", ...), which datetime.fromisoformat() only accepts as exactly 3
    or 6 digits on Python < 3.11 -- pad it explicitly rather than depend
    on the interpreter version.
    """
    if not s:
        return None
    whole, _, frac = s.partition(".")
    if frac:
        s = f"{whole}.{frac.ljust(6, '0')[:6]}"
    try:
        dt = datetime.fromisoformat(s)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=IST)
    return dt.astimezone(timezone.utc)


@dataclass
class ParsedFiling:
    symbol: str
    period_end: date
    nature: Optional[str]          # "Standalone" / "Consolidated"
    audited: Optional[str]         # "Audited" / "Unaudited"
    values: dict                   # factor_name -> float | None
    raw_facts: dict                # every tagged fact, for anything not in FACTOR_TAGS


def _first_tagged_value(facts: dict, candidate_tags: tuple) -> Optional[float]:
    """First candidate tag actually present (with a non-None value) in this
    filing's facts, in priority order -- lets one factor name resolve to
    either the industrial or the bank/NBFC taxonomy tag without needing to
    know upfront which format a given filer uses."""
    for tag in candidate_tags:
        val = facts.get(tag)
        if val is not None:
            return val
    return None


def extract_filing(html: str, symbol: str) -> Optional[ParsedFiling]:
    facts = parse_ixbrl(html)
    period_end = _parse_ddmmyyyy(facts.get("DateOfEndOfReportingPeriod", ""))
    if period_end is None:
        period_end = _derive_period_end_from_quarter(facts)
    if period_end is None:
        return None
    values = {name: _first_tagged_value(facts, tags) for name, tags in FACTOR_TAGS.items()}
    return ParsedFiling(
        symbol=symbol, period_end=period_end,
        nature=facts.get("NatureOfReportStandaloneConsolidated"),
        audited=facts.get("WhetherResultsAreAuditedOrUnaudited"),
        values=values, raw_facts=facts,
    )


def ingest_symbol(symbol: str, store: PointInTimeStore, *,
                   from_date: Optional[date] = None,
                   to_date: Optional[date] = None,
                   scrip_code: Optional[str] = None) -> tuple[int, Optional[str]]:
    """Fetch, parse and store fundamentals for one symbol.

    Returns (records_written, error_message). error_message is set (and
    records_written is 0) when no BSE scrip code can be found or the
    filing index has nothing in range -- both are normal, expected outcomes
    for a name that isn't BSE-listed or has no recent filings, not
    exceptions to propagate.
    """
    to_date = to_date or date.today()
    from_date = from_date or (to_date - timedelta(days=3 * 365))
    scrip = scrip_code or find_scrip_code(symbol)
    if scrip is None:
        return 0, f"no BSE scrip code found for {symbol}"

    rows = fetch_filing_index(scrip, from_date, to_date)
    if not rows:
        return 0, f"no filings for {symbol} (scrip {scrip}) in range"

    written = 0
    seen_docs = set()
    for row in rows:
        xml_name = row.get("Consol_XMLName") or row.get("XMLName")
        if not xml_name or not xml_name.lower().endswith(".html"):
            xml_name = row.get("XMLName")
        if not xml_name or not xml_name.lower().endswith(".html"):
            continue  # the sibling .xml documents 404 in every case checked
        if xml_name in seen_docs:
            continue
        seen_docs.add(xml_name)

        published_at = _parse_bse_created(row.get("Fld_CreateDate", ""))
        if published_at is None:
            continue

        time.sleep(REQUEST_GAP)
        html = fetch_document(xml_name)
        if html is None:
            continue
        filing = extract_filing(html, symbol)
        if filing is None:
            continue

        period_end_dt = datetime.combine(filing.period_end, datetime.min.time(),
                                         tzinfo=timezone.utc)
        records = [
            PITRecord(symbol=symbol, field=field, value=value,
                      period_end=period_end_dt, published_at=published_at,
                      source="bse_xbrl", fetched_at=datetime.now(timezone.utc))
            for field, value in filing.values.items() if value is not None
        ]
        if records:
            written += store.put(records)

    return written, None
