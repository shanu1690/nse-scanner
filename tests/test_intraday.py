"""Tests for nse/intraday.py -- PROJECT_BRIEF.md Section 4.3/4.4 and Rule
8's cron approximation: cheap invalidation monitoring every run, new-call
admission only near a decision point, and "not wasteful" holds only if
market-hours gating genuinely short-circuits before any API call."""

import json
import os
from datetime import date, datetime, time, timedelta, timezone
from types import SimpleNamespace

import pytest

from nse import intraday
from nse import tracker
from nse.calendar.market_calendar import IST
from nse.risk import RiskLimits


class _FakeSession:
    """Records every call so tests can assert on exactly what was fetched
    -- and, for the market-closed test, that nothing was fetched at all."""

    def __init__(self, ltp_map=None, index_map=None):
        self.ltp_map = ltp_map or {}
        self.index_map = index_map or {}
        self.ltp_calls = []
        self.index_calls = []

    def ltp_batch(self, symbols):
        self.ltp_calls.append(list(symbols))
        return {s: self.ltp_map[s] for s in symbols if s in self.ltp_map}

    def index_ltp(self, name="NIFTY"):
        self.index_calls.append(name)
        return self.index_map.get(name)


class _FakeCalendar:
    def __init__(self, open_):
        self._open = open_

    def is_open(self, moment):
        return self._open


@pytest.fixture
def isolated_journal(tmp_path, monkeypatch):
    journal_path = tmp_path / "journal.json"
    monkeypatch.setattr(tracker, "JOURNAL", str(journal_path))
    return journal_path


def _pick(symbol, ptype, saved="2026-09-18", **kw):
    base = {"symbol": symbol, "type": ptype, "saved": saved}
    base.update(kw)
    return base


# --------------------------------------------------------------- decision points

def test_is_decision_point_true_within_tolerance():
    moment = datetime(2026, 9, 18, 11, 33, tzinfo=IST)  # 3 min after 11:30
    assert intraday.is_decision_point(moment) is True


def test_is_decision_point_false_between_points():
    moment = datetime(2026, 9, 18, 10, 30, tzinfo=IST)  # nowhere near 9:45/11:30/14:30
    assert intraday.is_decision_point(moment) is False


def test_is_decision_point_excludes_pre_open_points():
    # 9:05 and 8:45 are pre-open points (DECISION_POINTS[0:2]), deliberately
    # NOT in INTRADAY_DECISION_POINTS -- this module doesn't fetch the
    # fresh fundamentals/news those need.
    moment = datetime(2026, 9, 18, 9, 5, tzinfo=IST)
    assert intraday.is_decision_point(moment) is False


# ------------------------------------------------------------- market-hours gate

def test_run_intraday_check_short_circuits_outside_market_hours():
    """The whole point of the cron-based design is 'not wasteful' -- that
    claim is only true if a closed market genuinely makes zero API calls."""
    session = _FakeSession()
    result = intraday.run_intraday_check(
        calendar=_FakeCalendar(open_=False), session=session, quiet=True)
    assert result.ran is False
    assert session.ltp_calls == [] and session.index_calls == []


# ------------------------------------------------------------- invalidations

def test_check_invalidations_detects_a_new_stop_out(isolated_journal):
    tracker.save_picks([_pick("SYM0", "delivery", entry=100.0, stop=95.0,
                              target1=110.0, target2=120.0)])
    session = _FakeSession(ltp_map={"SYM0": 94.0})  # below stop
    drops, surviving, new_status = intraday.check_invalidations(session, {"last_status": {}})
    assert len(drops) == 1
    assert drops[0]["symbol"] == "SYM0" and drops[0]["status"] == "STOPPED OUT"
    assert surviving["delivery"] == []  # no longer open


def test_check_invalidations_no_repeat_alert_for_already_known_status(isolated_journal):
    tracker.save_picks([_pick("SYM0", "delivery", entry=100.0, stop=95.0,
                              target1=110.0, target2=120.0)])
    session = _FakeSession(ltp_map={"SYM0": 94.0})
    pid = tracker._pid({**tracker.load_picks()[0]})
    state = {"last_status": {pid: "STOPPED OUT"}}  # already alerted last run
    drops, surviving, new_status = intraday.check_invalidations(session, state)
    assert drops == []
    assert new_status[pid] == "STOPPED OUT"


def test_check_invalidations_still_open_produces_no_event(isolated_journal):
    tracker.save_picks([_pick("SYM0", "delivery", entry=100.0, stop=95.0,
                              target1=110.0, target2=120.0)])
    session = _FakeSession(ltp_map={"SYM0": 101.0})  # between entry and target1
    drops, surviving, new_status = intraday.check_invalidations(session, {"last_status": {}})
    assert drops == []
    assert len(surviving["delivery"]) == 1


def test_check_invalidations_ignores_stale_delivery_picks(isolated_journal):
    """Real bug, found live: the journal (data/journal.json) accumulates
    indefinitely and is never pruned. Without a recency filter, a fresh
    state file treats a delivery pick saved weeks ago -- almost certainly
    already resolved in real life, a '3-5 day idea' -- as newly relevant,
    flooding the first alert of the day with old noise. Confirmed against
    the real journal before this filter existed: 150+ drop lines on one
    run, heavy duplication of the same handful of symbols."""
    old_saved = (date.today() - timedelta(days=intraday.DELIVERY_RELEVANCE_DAYS + 5)).isoformat()
    tracker.save_picks([_pick("OLD", "delivery", entry=100.0,
                              stop=95.0, target1=110.0, target2=120.0)],
                       date=old_saved)
    session = _FakeSession(ltp_map={"OLD": 94.0})  # would be a stop-out if it counted
    drops, surviving, new_status = intraday.check_invalidations(session, {"last_status": {}})
    assert drops == []
    assert session.ltp_calls == []  # never even fetched -- filtered before the batch call


def test_check_invalidations_ignores_expired_options(isolated_journal):
    old_expiry = "01-Jan-2020"
    tracker.save_picks([_pick("OLDOPT", "options", entry=None, direction="CE",
                              strike=100.0, premium=5.0, breakeven=105.0,
                              spot=100.0, expiry=old_expiry, lot_size=100)])
    session = _FakeSession(ltp_map={"OLDOPT": 200.0})
    drops, surviving, new_status = intraday.check_invalidations(session, {"last_status": {}})
    assert drops == []
    assert session.ltp_calls == []


def test_check_invalidations_keeps_recent_delivery_picks(isolated_journal):
    recent_saved = (date.today() - timedelta(days=intraday.DELIVERY_RELEVANCE_DAYS - 1)).isoformat()
    tracker.save_picks([_pick("RECENT", "delivery", entry=100.0,
                              stop=95.0, target1=110.0, target2=120.0)],
                       date=recent_saved)
    session = _FakeSession(ltp_map={"RECENT": 94.0})
    drops, surviving, new_status = intraday.check_invalidations(session, {"last_status": {}})
    assert len(drops) == 1 and drops[0]["symbol"] == "RECENT"


def test_check_invalidations_missing_quote_keeps_prior_status(isolated_journal):
    tracker.save_picks([_pick("SYM0", "delivery", entry=100.0, stop=95.0,
                              target1=110.0, target2=120.0)])
    session = _FakeSession(ltp_map={})  # SmartAPI had nothing for it this cycle
    pid = tracker._pid({**tracker.load_picks()[0]})
    drops, surviving, new_status = intraday.check_invalidations(
        session, {"last_status": {pid: "OPEN"}})
    assert drops == []
    assert new_status[pid] == "OPEN"


# ------------------------------------------------------------- admissions

def _bundle(delivery_vetoed=None, options_vetoed=None):
    return {
        "delivery": {"picks": [], "vetoed_capacity": delivery_vetoed or []},
        "options": {"picks": [], "vetoed_capacity": options_vetoed or []},
        "manifest": {},
    }


def test_check_admissions_readmits_when_slot_frees_and_price_holds():
    bundle = _bundle(delivery_vetoed=[
        {"symbol": "SYM1", "entry": 100.0, "stop": 95.0, "score": 80.0,
         "veto_rule": "max_open_ideas", "veto_detail": "..."},
    ])
    session = _FakeSession(ltp_map={"SYM1": 100.5})  # 0.5% above entry -- fine
    limits = RiskLimits(max_open_ideas=5)
    adds = intraday.check_admissions(session, bundle, {"delivery": [], "options": []}, limits)
    assert len(adds) == 1 and adds[0]["symbol"] == "SYM1"


def test_check_admissions_rejects_a_chased_price():
    bundle = _bundle(delivery_vetoed=[
        {"symbol": "SYM1", "entry": 100.0, "stop": 95.0, "score": 80.0,
         "veto_rule": "max_open_ideas", "veto_detail": "..."},
    ])
    session = _FakeSession(ltp_map={"SYM1": 103.0})  # 3% above entry -- chasing
    limits = RiskLimits(max_open_ideas=5)
    adds = intraday.check_admissions(session, bundle, {"delivery": [], "options": []}, limits)
    assert adds == []


def test_check_admissions_no_op_when_no_slot_freed():
    bundle = _bundle(delivery_vetoed=[
        {"symbol": "SYM1", "entry": 100.0, "stop": 95.0, "score": 80.0,
         "veto_rule": "max_open_ideas", "veto_detail": "..."},
    ])
    session = _FakeSession(ltp_map={"SYM1": 100.0})
    limits = RiskLimits(max_open_ideas=2)
    still_open = {"delivery": [{"symbol": "A"}, {"symbol": "B"}], "options": []}
    adds = intraday.check_admissions(session, bundle, still_open, limits)
    assert adds == []
    assert session.ltp_calls == []  # didn't even bother fetching quotes


def test_check_admissions_ignores_non_capacity_vetoes():
    """A candidate vetoed for something other than max_open_ideas (e.g. a
    sector cap) is not something this module re-admits -- that needs a
    full live portfolio-risk reconstruction it deliberately doesn't do."""
    bundle = _bundle(delivery_vetoed=[
        {"symbol": "SYM1", "entry": 100.0, "stop": 95.0, "score": 80.0,
         "veto_rule": "sector_cap", "veto_detail": "..."},
    ])
    session = _FakeSession(ltp_map={"SYM1": 100.0})
    limits = RiskLimits(max_open_ideas=5)
    adds = intraday.check_admissions(session, bundle, {"delivery": [], "options": []}, limits)
    assert adds == []


def test_check_admissions_options_candidate_uses_spot_not_entry():
    bundle = _bundle(options_vetoed=[
        {"symbol": "OPT1", "spot": 500.0, "cost": 4000.0, "score": 70.0,
         "veto_rule": "max_open_ideas", "veto_detail": "..."},
    ])
    session = _FakeSession(ltp_map={"OPT1": 500.5})
    limits = RiskLimits(max_open_ideas=5)
    adds = intraday.check_admissions(session, bundle, {"delivery": [], "options": []}, limits)
    assert len(adds) == 1 and adds[0]["type"] == "options"


# ------------------------------------------------------------- message formatting

def test_format_message_none_when_nothing_to_report():
    assert intraday._format_message([], [], None) is None


def test_format_message_includes_drops_and_adds():
    drops = [{"symbol": "A", "type": "delivery", "status": "STOPPED OUT", "current_price": 90.0}]
    adds = [{"symbol": "B", "type": "delivery", "entry": 100.0, "live_price": 100.5}]
    msg = intraday._format_message(drops, adds, "NIFTY spot 25,000.0")
    assert "DROP A" in msg and "STOPPED OUT" in msg
    assert "ADD B" in msg
    assert "NIFTY spot" in msg


# ------------------------------------------------------------- end-to-end

def test_run_intraday_check_end_to_end_sends_ntfy_on_a_drop(isolated_journal, tmp_path, monkeypatch):
    tracker.save_picks([_pick("SYM0", "delivery", entry=100.0, stop=95.0,
                              target1=110.0, target2=120.0)])
    state_path = str(tmp_path / "state.json")
    bundle_dir = str(tmp_path / "bundle")
    os.makedirs(bundle_dir, exist_ok=True)

    from nse import backtest as bt
    monkeypatch.setitem(bt._CONFIG, "risk", {"max_open_ideas": 5})

    sent = {}
    monkeypatch.setattr("nse.report.send_ntfy",
                        lambda subject, body, cfg: sent.setdefault("body", body) or 200)

    session = _FakeSession(ltp_map={"SYM0": 94.0})
    result = intraday.run_intraday_check(
        bundle_dir=bundle_dir, state_path=state_path,
        calendar=_FakeCalendar(open_=True), session=session,
        ntfy_cfg={"topic": "test-topic"}, quiet=True)

    assert result.ran is True
    assert len(result.drops) == 1
    assert result.sent is True
    assert "DROP SYM0" in sent["body"]

    # Second run, same stopped-out position: no repeat alert.
    sent.clear()
    result2 = intraday.run_intraday_check(
        bundle_dir=bundle_dir, state_path=state_path,
        calendar=_FakeCalendar(open_=True), session=session,
        ntfy_cfg={"topic": "test-topic"}, quiet=True)
    assert result2.drops == []
    assert result2.sent is False
    assert "body" not in sent


def test_run_intraday_check_no_ntfy_topic_does_not_send(isolated_journal, tmp_path, monkeypatch):
    tracker.save_picks([_pick("SYM0", "delivery", entry=100.0, stop=95.0,
                              target1=110.0, target2=120.0)])
    from nse import backtest as bt
    monkeypatch.setitem(bt._CONFIG, "risk", {"max_open_ideas": 5})

    called = {"n": 0}
    monkeypatch.setattr("nse.report.send_ntfy",
                        lambda *a, **kw: called.__setitem__("n", called["n"] + 1))

    session = _FakeSession(ltp_map={"SYM0": 94.0})
    result = intraday.run_intraday_check(
        bundle_dir=str(tmp_path / "bundle"), state_path=str(tmp_path / "state.json"),
        calendar=_FakeCalendar(open_=True), session=session,
        ntfy_cfg={"topic": ""}, quiet=True)
    assert result.drops and result.sent is False
    assert called["n"] == 0
