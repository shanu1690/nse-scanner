"""Tests for BSE XBRL fundamentals ingestion (nse/fundamentals/xbrl_ingest.py).

parse_ixbrl()/extract_filing() are tested against a small synthetic
inline-XBRL snippet built to mirror the real tag structure (verified live
against RELIANCE's actual filing during development) rather than embedding
a 160KB real document. The network-facing functions are tested via
monkeypatched requests.get -- no live calls in the suite.
"""

from datetime import date, datetime, timezone

import pytest

from nse.fundamentals import xbrl_ingest as xi
from nse.pit.store import PointInTimeStore

# A minimal but structurally faithful iXBRL snippet: same tag shape as the
# real filings (verified live), covering both text (ix:nonNumeric) and
# numeric (ix:nonFraction) facts, a repeated tag across two contexts (only
# the first should win), and a comma-formatted Indian-style number.
_SAMPLE_IXBRL = """
<html><body>
<span><ix:nonNumeric name='in-capmkt:Symbol' contextRef='OneD'>RELIANCE</ix:nonNumeric></span>
<span><ix:nonNumeric name='in-capmkt:NatureOfReportStandaloneConsolidated' contextRef='OneD'>Standalone</ix:nonNumeric></span>
<span><ix:nonNumeric name='in-capmkt:WhetherResultsAreAuditedOrUnaudited' contextRef='OneD'>Unaudited</ix:nonNumeric></span>
<span><ix:nonNumeric name='in-capmkt:DateOfEndOfReportingPeriod' contextRef='OneD'>30-06-2026</ix:nonNumeric></span>
<span><ix:nonNumeric name='in-capmkt:AuditorsFirmName' contextRef='OneD'>Deloitte Haskins &amp; Sells LLP</ix:nonNumeric></span>
<td><ix:nonFraction name='in-capmkt:RevenueFromOperations' contextRef='OneD' unitRef='INR' scale='7' decimals='-7'>1,66,013.00</ix:nonFraction></td>
<td><ix:nonFraction name='in-capmkt:ProfitLossForPeriod' contextRef='OneD' unitRef='INR' scale='7' decimals='-7'>13,272.00</ix:nonFraction></td>
<td><ix:nonFraction name='in-capmkt:ProfitLossForPeriod' contextRef='PriorD' unitRef='INR' scale='7' decimals='-7'>11,111.00</ix:nonFraction></td>
<td><ix:nonFraction name='in-capmkt:BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations' contextRef='OneD' unitRef='INR'>9.81</ix:nonFraction></td>
<td><ix:nonFraction name='in-capmkt:UnparsableFact' contextRef='OneD'></ix:nonFraction></td>
</body></html>
"""

# A bank-format snippet (SEBI Regulation 33 "Format B" -- verified live
# against HDFCBANK) using different tag names for the same concepts: no
# top-level RevenueFromOperations/ProfitBeforeTax/ProfitLossForPeriod at
# all, only their bank-taxonomy equivalents.
_SAMPLE_IXBRL_BANK = """
<html><body>
<span><ix:nonNumeric name='in-capmkt:Symbol' contextRef='OneD'>HDFCBANK</ix:nonNumeric></span>
<span><ix:nonNumeric name='in-capmkt:DateOfEndOfReportingPeriod' contextRef='OneD'>30-06-2026</ix:nonNumeric></span>
<td><ix:nonFraction name='in-capmkt:InterestEarned' contextRef='OneD' unitRef='INR'>90,575.33</ix:nonFraction></td>
<td><ix:nonFraction name='in-capmkt:ProfitLossFromOrdinaryActivitiesBeforeTax' contextRef='OneD' unitRef='INR'>27,193.16</ix:nonFraction></td>
<td><ix:nonFraction name='in-capmkt:ProfitLossForThePeriod' contextRef='OneD' unitRef='INR'>20,382.69</ix:nonFraction></td>
<td><ix:nonFraction name='in-capmkt:TaxExpense' contextRef='OneD' unitRef='INR'>6,810.47</ix:nonFraction></td>
<td><ix:nonFraction name='in-capmkt:BasicEarningsPerShareBeforeExtraordinaryItems' contextRef='OneD' unitRef='INR'>12.5</ix:nonFraction></td>
</body></html>
"""


# ------------------------------------------------------------------- parse_ixbrl
def test_parse_ixbrl_extracts_numeric_and_text_facts():
    facts = xi.parse_ixbrl(_SAMPLE_IXBRL)
    assert facts["Symbol"] == "RELIANCE"
    assert facts["DateOfEndOfReportingPeriod"] == "30-06-2026"
    assert facts["RevenueFromOperations"] == 166013.0  # commas stripped


def test_parse_ixbrl_unescapes_text_facts():
    facts = xi.parse_ixbrl(_SAMPLE_IXBRL)
    assert facts["AuditorsFirmName"] == "Deloitte Haskins & Sells LLP"


def test_parse_ixbrl_first_occurrence_wins_on_repeated_tag():
    facts = xi.parse_ixbrl(_SAMPLE_IXBRL)
    assert facts["ProfitLossForPeriod"] == 13272.0  # not the PriorD 11,111.00


def test_parse_ixbrl_empty_numeric_value_is_none_not_a_crash():
    facts = xi.parse_ixbrl(_SAMPLE_IXBRL)
    assert facts["UnparsableFact"] is None


# ----------------------------------------------------------------- extract_filing
def test_extract_filing_builds_period_end_and_values():
    filing = xi.extract_filing(_SAMPLE_IXBRL, "RELIANCE")
    assert filing is not None
    assert filing.symbol == "RELIANCE"
    assert filing.period_end == date(2026, 6, 30)
    assert filing.nature == "Standalone"
    assert filing.audited == "Unaudited"
    assert filing.values["revenue_from_operations"] == 166013.0
    assert filing.values["net_profit"] == 13272.0
    assert filing.values["eps_basic"] == 9.81
    assert filing.values["debt_equity_ratio"] is None  # not present in this snippet


def test_extract_filing_none_when_no_period_end():
    assert xi.extract_filing("<html>no facts here</html>", "X") is None


# ------------------------------------------ period-end-from-quarter fallback
# Real, live-verified shape: some BSE filings (a SEBI taxonomy-version
# transition seen in RELIANCE's Apr/Jul-2025 filings) omit
# DateOfEndOfReportingPeriod entirely and tag only DateOfStartOfFinancialYear
# + a ReportingQuarter ordinal.
_SAMPLE_IXBRL_NO_PERIOD_END = """
<html><body>
<span><ix:nonNumeric name='in-capmkt:Symbol' contextRef='OneD'>RELIANCE</ix:nonNumeric></span>
<span><ix:nonNumeric name='in-capmkt:ReportingQuarter' contextRef='OneD'>First quarter</ix:nonNumeric></span>
<span><ix:nonNumeric name='in-capmkt:DateOfStartOfFinancialYear' contextRef='OneD'>01-04-2025</ix:nonNumeric></span>
<span><ix:nonNumeric name='in-capmkt:DateOfEndOfFinancialYear' contextRef='OneD'>31-03-2026</ix:nonNumeric></span>
<td><ix:nonFraction name='in-capmkt:ProfitLossForPeriod' contextRef='OneD' unitRef='INR'>5000.00</ix:nonFraction></td>
</body></html>
"""


def test_derive_period_end_from_quarter_first():
    facts = {"ReportingQuarter": "First quarter", "DateOfStartOfFinancialYear": "01-04-2025"}
    assert xi._derive_period_end_from_quarter(facts) == date(2025, 6, 30)


def test_derive_period_end_from_quarter_fourth_rolls_into_next_year():
    facts = {"ReportingQuarter": "Fourth quarter", "DateOfStartOfFinancialYear": "01-04-2024"}
    assert xi._derive_period_end_from_quarter(facts) == date(2025, 3, 31)


def test_derive_period_end_from_quarter_all_four_ordinals():
    fy_start = "01-04-2023"
    expected = {"First quarter": date(2023, 6, 30), "Second quarter": date(2023, 9, 30),
                "Third quarter": date(2023, 12, 31), "Fourth quarter": date(2024, 3, 31)}
    for quarter, exp in expected.items():
        facts = {"ReportingQuarter": quarter, "DateOfStartOfFinancialYear": fy_start}
        assert xi._derive_period_end_from_quarter(facts) == exp


def test_derive_period_end_from_quarter_none_without_fy_start():
    assert xi._derive_period_end_from_quarter({"ReportingQuarter": "First quarter"}) is None


def test_derive_period_end_from_quarter_none_with_unrecognized_ordinal():
    facts = {"ReportingQuarter": "Special quarter", "DateOfStartOfFinancialYear": "01-04-2025"}
    assert xi._derive_period_end_from_quarter(facts) is None


def test_extract_filing_falls_back_to_derived_period_end():
    filing = xi.extract_filing(_SAMPLE_IXBRL_NO_PERIOD_END, "RELIANCE")
    assert filing is not None
    assert filing.period_end == date(2025, 6, 30)
    assert filing.values["net_profit"] == 5000.0


# ------------------------------------------------------ bank/NBFC taxonomy fallback
def test_extract_filing_resolves_bank_format_tags():
    """A bank filing has none of the industrial-format tags at all -- each
    factor must resolve via its bank-taxonomy fallback tag instead."""
    filing = xi.extract_filing(_SAMPLE_IXBRL_BANK, "HDFCBANK")
    assert filing is not None
    assert filing.values["revenue_from_operations"] == 90575.33  # via InterestEarned
    assert filing.values["profit_before_tax"] == 27193.16        # via ProfitLossFromOrdinaryActivitiesBeforeTax
    assert filing.values["net_profit"] == 20382.69                # via ProfitLossForThePeriod
    assert filing.values["eps_basic"] == 12.5                     # via BasicEarningsPerShareBeforeExtraordinaryItems
    assert filing.values["tax_expense"] == 6810.47                # same tag name in both formats


def test_extract_filing_bank_format_has_no_debt_equity_or_finance_costs():
    """debt_equity_ratio/finance_costs are deliberately industrial-only --
    a bank filing genuinely has neither tag, and no fallback is defined."""
    filing = xi.extract_filing(_SAMPLE_IXBRL_BANK, "HDFCBANK")
    assert filing.values["debt_equity_ratio"] is None
    assert filing.values["finance_costs"] is None


def test_first_tagged_value_prefers_industrial_tag_when_both_present():
    """When a filing (unusually) tags both formats, the industrial tag --
    listed first -- wins, matching the priority order documented on
    FACTOR_TAGS."""
    facts = {"RevenueFromOperations": 100.0, "InterestEarned": 200.0}
    assert xi._first_tagged_value(facts, xi.FACTOR_TAGS["revenue_from_operations"]) == 100.0


def test_first_tagged_value_none_when_no_candidate_present():
    assert xi._first_tagged_value({}, ("A", "B")) is None


# --------------------------------------------------------------- date/time helpers
def test_parse_ddmmyyyy():
    assert xi._parse_ddmmyyyy("30-06-2026") == date(2026, 6, 30)
    assert xi._parse_ddmmyyyy("garbage") is None
    assert xi._parse_ddmmyyyy("") is None


def test_parse_bse_created_localizes_ist_to_utc():
    dt = xi._parse_bse_created("2026-07-17T19:48:51.41")
    assert dt.tzinfo is not None
    assert dt.astimezone(timezone.utc).hour == 14  # 19:48 IST -> 14:18 UTC... see below
    assert dt.astimezone(timezone.utc).minute == 18


def test_parse_bse_created_invalid_returns_none():
    assert xi._parse_bse_created("not-a-date") is None
    assert xi._parse_bse_created("") is None


# ------------------------------------------------------------------- find_scrip_code
class _FakeResponse:
    def __init__(self, json_data, status_code=200):
        self._json = json_data
        self.status_code = status_code

    def raise_for_status(self):
        if self.status_code != 200:
            import requests
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        return self._json

    @property
    def text(self):
        return str(self._json)


def test_find_scrip_code_matches_exact_id(monkeypatch):
    payload = [
        {"scripcode": "500325", "ID": "RELIANCE"},
        {"scripcode": "500390", "ID": "RELINFRA"},
    ]
    monkeypatch.setattr(xi.requests, "get", lambda *a, **k: _FakeResponse(payload))
    assert xi.find_scrip_code("RELIANCE") == "500325"


def test_find_scrip_code_no_exact_match_returns_none(monkeypatch):
    payload = [{"scripcode": "500390", "ID": "RELINFRA"}]
    monkeypatch.setattr(xi.requests, "get", lambda *a, **k: _FakeResponse(payload))
    assert xi.find_scrip_code("RELIANCE") is None


def test_find_scrip_code_network_error_returns_none(monkeypatch):
    def boom(*a, **k):
        raise xi.requests.RequestException("network down")
    monkeypatch.setattr(xi.requests, "get", boom)
    assert xi.find_scrip_code("RELIANCE") is None


# ---------------------------------------------------------------- fetch_filing_index
def test_fetch_filing_index_sends_ddmmyyyy_dates(monkeypatch):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured.update(params)
        return _FakeResponse({"Table": [{"Scrip_cd": 500325}]})

    monkeypatch.setattr(xi.requests, "get", fake_get)
    rows = xi.fetch_filing_index("500325", date(2025, 1, 1), date(2026, 9, 14))
    assert captured["FROMDT"] == "01/01/2025"
    assert captured["TODT"] == "14/09/2026"
    assert rows == [{"Scrip_cd": 500325}]


def test_fetch_filing_index_returns_empty_list_on_error(monkeypatch):
    monkeypatch.setattr(xi.requests, "get", lambda *a, **k: _FakeResponse({}, status_code=500))
    assert xi.fetch_filing_index("500325", date(2025, 1, 1), date(2026, 1, 1)) == []


# -------------------------------------------------------------------- fetch_document
def test_fetch_document_returns_none_on_404(monkeypatch):
    monkeypatch.setattr(xi.requests, "get", lambda *a, **k: _FakeResponse("", status_code=404))
    assert xi.fetch_document("some/file.html") is None


def test_fetch_document_returns_text_on_success(monkeypatch):
    class R:
        status_code = 200
        text = "<html>ok</html>"
    monkeypatch.setattr(xi.requests, "get", lambda *a, **k: R())
    assert xi.fetch_document("some/file.html") == "<html>ok</html>"


# ------------------------------------------------------------------- ingest_symbol
def test_ingest_symbol_no_scrip_code(monkeypatch):
    monkeypatch.setattr(xi, "find_scrip_code", lambda sym: None)
    with PointInTimeStore() as store:
        written, err = xi.ingest_symbol("NOSUCHSYM", store)
    assert written == 0
    assert "no BSE scrip code" in err


def test_ingest_symbol_no_filings(monkeypatch):
    monkeypatch.setattr(xi, "find_scrip_code", lambda sym: "500325")
    monkeypatch.setattr(xi, "fetch_filing_index", lambda *a, **k: [])
    with PointInTimeStore() as store:
        written, err = xi.ingest_symbol("RELIANCE", store)
    assert written == 0
    assert "no filings" in err


def test_ingest_symbol_writes_records_and_dedupes_documents(monkeypatch):
    monkeypatch.setattr(xi, "find_scrip_code", lambda sym: "500325")
    rows = [
        {"XMLName": "a/filing1.html", "Fld_CreateDate": "2026-07-17T19:48:51.41"},
        {"XMLName": "a/filing1.html", "Fld_CreateDate": "2026-07-17T19:48:51.41"},  # dup
        {"XMLName": "a/filing2.xml", "Fld_CreateDate": "2026-04-24T22:52:53.1"},    # .xml skipped
        {"XMLName": None, "Fld_CreateDate": "2026-01-01T00:00:00"},                 # no name
    ]
    monkeypatch.setattr(xi, "fetch_filing_index", lambda *a, **k: rows)
    monkeypatch.setattr(xi, "fetch_document", lambda name: _SAMPLE_IXBRL if name == "a/filing1.html" else None)
    monkeypatch.setattr(xi.time, "sleep", lambda s: None)

    with PointInTimeStore() as store:
        written, err = xi.ingest_symbol("RELIANCE", store)
        assert err is None
        assert written > 0
        as_of = store.as_of(["RELIANCE"], ["net_profit"], datetime(2026, 8, 1, tzinfo=timezone.utc))
        assert len(as_of) == 1
        assert float(as_of[0]["value"]) == 13272.0
        # published_at strictly before the filing's own board-approval news:
        # querying just before the filing's real published_at hides it (PIT contract)
        hidden = store.as_of(["RELIANCE"], ["net_profit"], datetime(2026, 7, 17, 14, 18, tzinfo=timezone.utc))
        assert hidden == []


def test_ingest_symbol_skips_document_that_fails_to_parse(monkeypatch):
    monkeypatch.setattr(xi, "find_scrip_code", lambda sym: "500325")
    rows = [{"XMLName": "bad.html", "Fld_CreateDate": "2026-07-17T19:48:51.41"}]
    monkeypatch.setattr(xi, "fetch_filing_index", lambda *a, **k: rows)
    monkeypatch.setattr(xi, "fetch_document", lambda name: "<html>no xbrl facts</html>")
    monkeypatch.setattr(xi.time, "sleep", lambda s: None)

    with PointInTimeStore() as store:
        written, err = xi.ingest_symbol("RELIANCE", store)
    assert written == 0
    assert err is None  # not an error condition, just nothing usable in this doc
