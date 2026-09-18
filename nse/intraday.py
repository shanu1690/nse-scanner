"""Intraday monitoring during market hours (PROJECT_BRIEF.md Section 4.3/
4.4 and Rule 8: "Do not generate new calls continuously... New calls only
at defined decision points; between them, only invalidation alerts on
open ideas").

This is the free/cron approximation of Section 4.2's originally-specified
always-on Render backend + SmartWebSocketV2 feed. There is no persistent
connection here: GitHub Actions runs `nse-scan intraday-check` on a cron
every ~15 minutes during market hours. That means:

  - timing is best-effort (Actions cron can drift several minutes -- see
    Section 4.3's own warning about this), not tick-by-tick
  - every check is a fresh, independent REST poll, not a live stream

Two things happen on every run, per Rule 8's split:

  1. INVALIDATION MONITORING (always, every run): re-check every currently
     open journaled pick's status (stop breached, target hit, option
     profit/loss) against a fresh LTP. Only an ACTUAL CHANGE since the
     last run is reported -- "still open, nothing new" produces no alert,
     by design (Section 4.4: these fire as alerts on existing ideas, not
     as noise).

  2. NEW-CALL ADMISSION (only within DECISION_POINT_TOLERANCE_MIN of one
     of nse/calendar/market_calendar.py's three intraday DECISION_POINTS
     -- 9:45/11:30/14:30 IST): re-check candidates that were approved by
     last night's `nse-scan site` run but vetoed ONLY for a capacity
     reason (nse/sitebuilder.py's `vetoed_capacity`, e.g. max_open_ideas)
     -- never a fresh re-scan of the universe, which is both expensive
     and exactly the "generate new calls continuously" Rule 8 forbids.
     A candidate is re-admitted only if (a) a slot has actually freed up
     since this morning and (b) its live price hasn't drifted more than
     MAX_CHASE_PCT from the planned entry/spot -- the same "don't chase"
     threshold already in the daily report's buy checklist.

Regime-conditional STYLE switching (momentum vs fade) is deliberately NOT
done here: nse/regime/compare.py already found switching does not
provably beat the static config.yaml style out-of-sample, and Section
4.4 is explicit -- "if it can't [prove out], ship the static rule and say
so." A live VIX/NIFTY read is surfaced as an informational note only, not
used to pick a strategy.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from typing import Optional

from . import report as report_mod
from . import tracker
from .calendar.market_calendar import (IST, DECISION_POINTS, MARKET_CLOSE,
                                       MARKET_OPEN, MarketCalendar,
                                       MissingCalendarError)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_BUNDLE_DIR = os.path.join(ROOT, "data", "latest_bundle")
DEFAULT_STATE_PATH = os.path.join(ROOT, "data", "intraday_state.json")

# Only the three INTRADAY refresh points (Section 4.3's table). The two
# pre-open points (8:45/9:05) need fresh fundamentals/news/overnight-gap
# data this module does not fetch -- deliberately out of scope here; the
# committed bundle (last night's EOD run) already stands in for "the
# day's plan" at market open.
INTRADAY_DECISION_POINTS = DECISION_POINTS[2:]
DECISION_POINT_TOLERANCE_MIN = 10

# "SKIP any stock that opens more than ~1.5% above ENTRY (don't chase)" --
# the same threshold already in the daily report's buy checklist
# (nse/report.py / nse/cli.py's morning-mode body), applied here to
# whether a capacity-freed candidate is still a fair entry, not a chase.
MAX_CHASE_PCT = 1.5

TERMINAL_DELIVERY_STATUSES = {"STOPPED OUT", "T1 HIT", "TGT2 HIT"}
TERMINAL_OPTION_STATUSES = {"PROFIT", "LOSS"}

# The journal (data/journal.json) accumulates indefinitely -- it's the
# project's "honesty ledger", never pruned. A delivery pick is a "3-5 day
# idea" (see tracker.py); anything saved much longer ago is almost
# certainly already resolved one way or another in real life, not a
# position genuinely still open. Without this filter, a fresh state file
# (first run, or after a long gap) treats EVERY historical journal entry
# as newly relevant and floods the first alert with weeks of stale
# symbols -- found live, running this against the real journal, before
# this filter existed (150+ drop lines, one run).
DELIVERY_RELEVANCE_DAYS = 10

# Options get TWO checks, not one: past the idea's own `expiry` is
# unambiguous, but that alone isn't enough -- found live, against the
# real journal, that a symbol re-picked as a fresh idea on several
# different dates (Aug through Sep, in this project's own testing
# history) can share the SAME nearest-monthly-expiry string across all
# of them if that contract was the nearest liquid series each time. A
# genuinely month-old journal entry then still reads as "not yet
# expired" by expiry alone. A monthly options idea is realistically
# stale well before 30 days regardless of its contract's expiry date.
OPTIONS_SAVED_RELEVANCE_DAYS = 30


def _relevant_picks(picks: list, today: date) -> list:
    out = []
    for p in picks:
        try:
            saved = date.fromisoformat(p["saved"])
        except (KeyError, ValueError):
            continue  # can't place it in time -- not confidently relevant

        if p.get("type") == "delivery":
            if (today - saved).days > DELIVERY_RELEVANCE_DAYS:
                continue
        else:
            if (today - saved).days > OPTIONS_SAVED_RELEVANCE_DAYS:
                continue
            expiry = p.get("expiry")
            if expiry:
                try:
                    if datetime.strptime(expiry, "%d-%b-%Y").date() < today:
                        continue
                except ValueError:
                    pass  # unrecognised format -- don't guess, keep it
        out.append(p)
    return out


@dataclass
class IntradayResult:
    ran: bool = True
    reason: str = ""
    is_decision_point: bool = False
    drops: list = field(default_factory=list)
    adds: list = field(default_factory=list)
    regime_note: Optional[str] = None
    message: Optional[str] = None
    sent: bool = False


def _now_ist() -> datetime:
    return datetime.now(timezone.utc).astimezone(IST)


def is_decision_point(moment: Optional[datetime] = None,
                       tolerance_min: int = DECISION_POINT_TOLERANCE_MIN) -> bool:
    """Within `tolerance_min` minutes of one of the three intraday
    DECISION_POINTS -- Actions cron is best-effort, so an exact-minute
    match would silently miss most decision points."""
    moment = moment or _now_ist()
    t = moment.time()
    for dp in INTRADAY_DECISION_POINTS:
        dp_minutes = dp.hour * 60 + dp.minute
        now_minutes = t.hour * 60 + t.minute
        if abs(now_minutes - dp_minutes) <= tolerance_min:
            return True
    return False


def load_state(path: str = DEFAULT_STATE_PATH) -> dict:
    if not os.path.exists(path):
        return {"date": None, "last_status": {}}
    try:
        with open(path) as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return {"date": None, "last_status": {}}


def save_state(state: dict, path: str = DEFAULT_STATE_PATH) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as fh:
        json.dump(state, fh, indent=2)


def _load_bundle(bundle_dir: str) -> dict:
    def _load(name):
        p = os.path.join(bundle_dir, name)
        if not os.path.exists(p):
            return None
        with open(p) as fh:
            return json.load(fh)
    return {
        "delivery": _load("delivery.json") or {"picks": [], "vetoed_capacity": []},
        "options": _load("options.json") or {"picks": [], "vetoed_capacity": []},
        "manifest": _load("manifest.json") or {},
    }


def check_invalidations(session, state: dict) -> tuple:
    """Fresh LTP for every journaled pick still relevant (see
    _relevant_picks -- delivery within DELIVERY_RELEVANCE_DAYS, options
    not yet expired). Returns (drop_events, surviving_open_symbols_by_type,
    updated_last_status). A "drop event" is a pick whose status is now
    terminal but was NOT terminal (or not yet seen) as of the last run --
    so a position that was already STOPPED OUT an hour ago doesn't
    re-alert every 15 minutes."""
    today = date.today()
    picks = _relevant_picks(tracker.load_picks(), today)
    if not picks:
        return [], {"delivery": [], "options": []}, {}

    symbols = sorted({p["symbol"] for p in picks})
    quotes = session.ltp_batch(symbols)
    last_status = state.get("last_status", {})

    drops = []
    new_status = {}
    surviving = {"delivery": [], "options": []}

    for p in picks:
        pid = tracker._pid(p)
        cur = quotes.get(p["symbol"])
        if cur is None:
            new_status[pid] = last_status.get(pid, "UNKNOWN")
            continue
        if p["type"] == "delivery":
            pnl, status = tracker._status_delivery(p, cur, today)
            terminal = status in TERMINAL_DELIVERY_STATUSES
        else:
            _, status, _ = tracker._status_option(p, cur, today)
            terminal = status in TERMINAL_OPTION_STATUSES

        new_status[pid] = status
        was_terminal = last_status.get(pid) in (
            TERMINAL_DELIVERY_STATUSES | TERMINAL_OPTION_STATUSES)
        if terminal and not was_terminal:
            drops.append({
                "symbol": p["symbol"], "type": p["type"], "status": status,
                "current_price": cur,
            })
        if not terminal:
            surviving[p["type"]].append(p)

    return drops, surviving, new_status


def _price_within_chase_limit(live_price: float, planned_price: float) -> bool:
    if not planned_price:
        return False
    return live_price <= planned_price * (1 + MAX_CHASE_PCT / 100)


def check_admissions(session, bundle: dict, surviving: dict, limits) -> list:
    """Re-check capacity-only-vetoed candidates from last night's bundle.
    Only nse-max_open_ideas vetoes are re-checked (see module docstring --
    sector/heat/correlation re-admission needs a full live portfolio-risk
    reconstruction this module deliberately does not attempt against
    historically heterogeneous journal data)."""
    n_open = len(surviving["delivery"]) + len(surviving["options"])
    if n_open >= limits.max_open_ideas:
        return []  # no slot freed up -- nothing to reconsider

    adds = []
    slots_left = limits.max_open_ideas - n_open

    delivery_candidates = [v for v in bundle["delivery"].get("vetoed_capacity", [])
                           if v.get("veto_rule") == "max_open_ideas"]
    options_candidates = [v for v in bundle["options"].get("vetoed_capacity", [])
                          if v.get("veto_rule") == "max_open_ideas"]

    if delivery_candidates:
        quotes = session.ltp_batch([c["symbol"] for c in delivery_candidates])
        for c in delivery_candidates:
            if slots_left <= 0:
                break
            live = quotes.get(c["symbol"])
            if live is None or not _price_within_chase_limit(live, c["entry"]):
                continue
            adds.append({"symbol": c["symbol"], "type": "delivery",
                        "entry": c["entry"], "live_price": live, "score": c["score"]})
            slots_left -= 1

    if options_candidates:
        quotes = session.ltp_batch([c["symbol"] for c in options_candidates])
        for c in options_candidates:
            if slots_left <= 0:
                break
            live = quotes.get(c["symbol"])
            planned_spot = c.get("spot")
            if live is None or planned_spot is None or not _price_within_chase_limit(live, planned_spot):
                continue
            adds.append({"symbol": c["symbol"], "type": "options",
                        "entry": planned_spot, "live_price": live,
                        "cost": c.get("cost"), "score": c.get("score")})
            slots_left -= 1

    return adds


def _regime_note(session) -> Optional[str]:
    """Best-effort, informational only -- never blocks or fails the run.
    A live NIFTY read via SmartAPI; VIX is skipped here (not wired into
    SmartAPI in this project -- see nse/data.py, VIX is fetched via
    yfinance historically). Absence of this note changes nothing else."""
    try:
        nifty = session.index_ltp("NIFTY")
    except Exception:
        nifty = None
    if nifty is None:
        return None
    return f"NIFTY spot {nifty:,.1f}"


def _format_message(drops, adds, regime_note) -> Optional[str]:
    if not drops and not adds:
        return None
    lines = []
    for d in drops:
        lines.append(f"DROP {d['symbol']} ({d['type']}): {d['status']} "
                     f"@ {d['current_price']:.2f}")
    for a in adds:
        if a["type"] == "delivery":
            lines.append(f"ADD {a['symbol']}: slot freed up, still near entry "
                         f"{a['entry']:.2f} (now {a['live_price']:.2f})")
        else:
            lines.append(f"ADD {a['symbol']} (options): slot freed up, spot "
                         f"still near plan ({a['live_price']:.2f})")
    if regime_note:
        lines.append(regime_note)
    return "\n".join(lines)


def run_intraday_check(*, bundle_dir: str = DEFAULT_BUNDLE_DIR,
                       state_path: str = DEFAULT_STATE_PATH,
                       calendar: Optional[MarketCalendar] = None,
                       session=None, ntfy_cfg: Optional[dict] = None,
                       quiet: bool = False) -> IntradayResult:
    calendar = calendar or MarketCalendar(os.path.join(ROOT, "config", "holidays"))
    now = datetime.now(timezone.utc)
    try:
        market_open = calendar.is_open(now)
    except MissingCalendarError as exc:
        # The holiday list for this year hasn't been populated/verified yet
        # (see market_calendar.py's own refusal-to-guess design). Fall back
        # to a plain weekday+session-window check rather than crashing --
        # worse (a real holiday won't be skipped, wasting a few SmartAPI
        # calls that will just come back with no fresh data) but never
        # silently wrong about a TRADING day, and loud about the gap.
        if not quiet:
            print(f"WARNING: {exc} -- falling back to a plain weekday check, "
                  f"no holiday awareness this run. Populate config/holidays/ "
                  f"for the current year to fix this properly.")
        local = now.astimezone(IST)
        market_open = local.weekday() < 5 and MARKET_OPEN <= local.time() < MARKET_CLOSE
    if not market_open:
        if not quiet:
            print("Market closed (holiday or outside session hours) -- skipping, no API calls made.")
        return IntradayResult(ran=False, reason="market_closed")

    from . import backtest as bt  # for _CONFIG, avoids a separate config load path
    from .risk import RiskLimits

    if session is None:
        from . import smartapi
        session = smartapi.get_shared_session()

    limits = RiskLimits.from_config(bt._CONFIG)
    state = load_state(state_path)
    today_str = date.today().isoformat()
    if state.get("date") != today_str:
        state = {"date": today_str, "last_status": {}}  # fresh day, fresh state

    drops, surviving, new_status = check_invalidations(session, state)

    at_decision_point = is_decision_point(_now_ist())
    adds = []
    if at_decision_point:
        bundle = _load_bundle(bundle_dir)
        adds = check_admissions(session, bundle, surviving, limits)

    regime_note = _regime_note(session) if (drops or adds or at_decision_point) else None
    message = _format_message(drops, adds, regime_note)

    sent = False
    if message and ntfy_cfg and ntfy_cfg.get("topic"):
        report_mod.send_ntfy("nse-scanner intraday update", message, ntfy_cfg)
        sent = True

    state["last_status"] = new_status
    save_state(state, state_path)

    if not quiet:
        print(message or "No change since last check.")
        if sent:
            print("Sent via ntfy.")

    return IntradayResult(ran=True, is_decision_point=at_decision_point,
                          drops=drops, adds=adds, regime_note=regime_note,
                          message=message, sent=sent)
