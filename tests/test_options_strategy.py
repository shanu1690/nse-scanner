"""Tests for the Phase 7 strategy-selection layer in nse/options.py:
prob_finish_itm, the quality bar, cross_source_d_oi, spread/naked-long
construction, select_option_idea's budget/rule enforcement, and rank_ideas.

select_option_idea()'s integration tests monkeypatch analyze_option_chain
itself rather than trying to reverse-engineer its scoring thresholds --
this file's job is to test the Phase 7 rule enforcement built ON TOP of
that analysis, not re-derive the scoring formula.
"""

import numpy as np
import pandas as pd
import pytest

from nse import options as opt


# ------------------------------------------------------------- prob_finish_itm
def test_prob_finish_itm_deep_itm_call_near_one():
    assert opt.prob_finish_itm(spot=200, strike=100, iv_pct=25, dte_days=20, side="CE") > 0.95


def test_prob_finish_itm_deep_otm_call_near_zero():
    assert opt.prob_finish_itm(spot=100, strike=200, iv_pct=25, dte_days=20, side="CE") < 0.05


def test_prob_finish_itm_atm_call_is_middling():
    p = opt.prob_finish_itm(spot=100, strike=100, iv_pct=25, dte_days=20, side="CE")
    assert 0.3 < p < 0.7


def test_prob_finish_itm_put_is_complement_of_call_direction():
    call_p = opt.prob_finish_itm(spot=100, strike=90, iv_pct=25, dte_days=20, side="CE")
    put_p = opt.prob_finish_itm(spot=100, strike=90, iv_pct=25, dte_days=20, side="PE")
    assert call_p > put_p  # spot already above this strike -> call favoured


@pytest.mark.parametrize("kwargs", [
    dict(spot=0, strike=100, iv_pct=25, dte_days=20, side="CE"),
    dict(spot=100, strike=0, iv_pct=25, dte_days=20, side="CE"),
    dict(spot=100, strike=100, iv_pct=25, dte_days=0, side="CE"),
    dict(spot=100, strike=100, iv_pct=0, dte_days=20, side="CE"),
    dict(spot=100, strike=100, iv_pct=None, dte_days=20, side="CE"),
])
def test_prob_finish_itm_none_on_invalid_input(kwargs):
    assert opt.prob_finish_itm(**kwargs) is None


def test_prob_finish_itm_unknown_side_returns_none():
    assert opt.prob_finish_itm(spot=100, strike=100, iv_pct=25, dte_days=20, side="XX") is None


# ------------------------------------------------------------------ _leg_quality
def test_leg_quality_rejects_thin_oi():
    ok, why = opt._leg_quality({"oi": 10, "bid": 9, "ask": 10, "premium": 10})
    assert not ok and "OI" in why


def test_leg_quality_rejects_wide_spread():
    ok, why = opt._leg_quality({"oi": 10000, "bid": 5, "ask": 8, "premium": 10})
    assert not ok and "spread" in why


def test_leg_quality_passes_good_leg():
    ok, why = opt._leg_quality({"oi": 10000, "bid": 9.7, "ask": 10.1, "premium": 10})
    assert ok and why is None


def test_leg_quality_unknown_bid_ask_not_auto_rejected():
    ok, why = opt._leg_quality({"oi": 10000, "bid": 0, "ask": 0, "premium": 10})
    assert ok and why is None


# --------------------------------------------------------------- cross_source_d_oi
def _chain(strikes_and_doi, expiry="25-Aug-2026", spot=1000.0):
    data = []
    for strike, doi in strikes_and_doi:
        data.append({
            "strikePrice": strike, "expiryDate": expiry,
            "CE": {"openInterest": 5000, "changeinOpenInterest": doi, "totalTradedVolume": 100,
                   "impliedVolatility": 25.0, "lastPrice": 10.0, "buyPrice1": 9.5, "sellPrice1": 10.5},
            "PE": {"openInterest": 5000, "changeinOpenInterest": doi, "totalTradedVolume": 100,
                   "impliedVolatility": 25.0, "lastPrice": 10.0, "buyPrice1": 9.5, "sellPrice1": 10.5},
        })
    return {"records": {"data": data, "underlyingValue": spot,
                        "expiryDates": [expiry], "strikePrices": [s for s, _ in strikes_and_doi]},
            "filtered": {"data": []}}


def test_cross_source_d_oi_skips_fetch_when_doi_already_nonzero():
    raw = _chain([(980, 100), (1000, -50), (1020, 200)])
    called = []
    opt.cross_source_d_oi("TEST", raw, session_factory=lambda: called.append(1) or object())
    assert called == []  # never invoked -- nothing to cross-source


def test_cross_source_d_oi_merges_real_values_when_all_zero(tmp_path, monkeypatch):
    raw = _chain([(980, 0), (1000, 0), (1020, 0)])
    nse_raw = _chain([(980, 150), (1000, -75), (1020, 300)])

    class FakeSession:
        def option_chain_equity(self, symbol, expiry=None):
            return nse_raw
        def close(self):
            pass

    opt.cross_source_d_oi("TEST", raw, session_factory=lambda: FakeSession())
    legs = [leg for item in raw["records"]["data"] for leg in (item["CE"], item["PE"])]
    assert all(leg["changeinOpenInterest"] != 0 for leg in legs)
    assert raw["records"]["data"][0]["CE"]["changeinOpenInterest"] == 150


def test_cross_source_d_oi_failure_leaves_doi_at_zero_and_logs(tmp_path, monkeypatch):
    from nse.quality import events as qe
    events_path = tmp_path / "_events.jsonl"
    monkeypatch.setattr(qe, "DEFAULT_EVENTS_PATH", events_path)

    raw = _chain([(980, 0), (1000, 0)])

    def boom():
        raise RuntimeError("NSE blocked")

    opt.cross_source_d_oi("TEST", raw, session_factory=boom)
    legs = [leg for item in raw["records"]["data"] for leg in (item["CE"], item["PE"])]
    assert all(leg["changeinOpenInterest"] == 0 for leg in legs)
    logged = qe.read_events(path=events_path)
    assert len(logged) == 1
    assert logged[0].kind == "d_oi_cross_source_unavailable"


# --------------------------------------------------------- spread/naked builders
def _frame(rows):
    return pd.DataFrame(rows)


def _row(strike, side, premium, oi=10000, iv=25.0, bid=None, ask=None):
    bid = premium * 0.97 if bid is None else bid
    ask = premium * 1.03 if ask is None else ask
    return {"strike": strike, "side": side, "oi": oi, "d_oi": 0, "volume": 100,
            "iv": iv, "premium": premium, "bid": bid, "ask": ask}


def test_build_naked_long_picks_atm_within_budget():
    frame = _frame([
        _row(1000, "CE", 20.0), _row(1020, "CE", 12.0), _row(1040, "CE", 7.0),
    ])
    rejected = []
    idea = opt._build_naked_long(frame, "CE", 1000, 20, spot=1000, dte=10,
                                  lot_size=100, budget=10000, rejected=rejected)
    assert idea is not None
    assert idea["cost"] == pytest.approx(2000.0)
    assert idea["max_loss"] == pytest.approx(2000.0)
    assert idea["breakeven"] == pytest.approx(1020.0)
    assert not rejected


def test_build_naked_long_over_budget_returns_none_and_logs_rejection():
    frame = _frame([_row(1000, "CE", 500.0)])  # cost = 500*100 = 50,000
    rejected = []
    idea = opt._build_naked_long(frame, "CE", 1000, 20, spot=1000, dte=10,
                                  lot_size=100, budget=10000, rejected=rejected)
    assert idea is None
    assert any(r["kind"] == "over_budget" for r in rejected)


def test_build_naked_long_skips_thin_oi_leg():
    frame = _frame([
        _row(1000, "CE", 20.0, oi=10),   # thin -- rejected
        _row(1020, "CE", 12.0, oi=10000),  # passes
    ])
    rejected = []
    idea = opt._build_naked_long(frame, "CE", 1000, 20, spot=1000, dte=10,
                                  lot_size=100, budget=10000, rejected=rejected)
    assert idea is not None
    assert idea["legs"][0]["strike"] == 1020
    assert any(r["kind"] == "quality" for r in rejected)


def test_build_debit_spread_within_budget():
    frame = _frame([
        _row(1000, "CE", 20.0), _row(1020, "CE", 12.0), _row(1040, "CE", 7.0),
    ])
    rejected = []
    idea = opt._build_debit_spread(frame, "CE", 1000, 20, spot=1000, dte=10,
                                    lot_size=100, budget=10000, rejected=rejected)
    assert idea is not None
    net_debit = 20.0 - 12.0
    assert idea["cost"] == pytest.approx(net_debit * 100)
    assert idea["max_profit"] == pytest.approx((20 - net_debit) * 100)
    assert idea["breakeven"] == pytest.approx(1000 + net_debit)
    assert len(idea["legs"]) == 2
    assert idea["legs"][0]["action"] == "BUY" and idea["legs"][1]["action"] == "SELL"


def test_build_debit_spread_put_side_direction():
    frame = _frame([
        _row(1000, "PE", 20.0), _row(980, "PE", 12.0),
    ])
    idea = opt._build_debit_spread(frame, "PE", 1000, 20, spot=1000, dte=10,
                                    lot_size=100, budget=10000, rejected=[])
    assert idea is not None
    assert idea["legs"][1]["strike"] == 980
    assert idea["breakeven"] < 1000


def test_build_debit_spread_none_when_no_long_leg():
    frame = _frame([_row(1020, "CE", 12.0)])  # no strike at atm_strike=1000
    assert opt._build_debit_spread(frame, "CE", 1000, 20, spot=1000, dte=10,
                                    lot_size=100, budget=10000, rejected=[]) is None


def test_build_debit_spread_skips_inverted_quotes():
    # Short leg priced HIGHER than the long leg (bad/stale quote) -- net
    # debit would be negative; must not be trusted as a real spread.
    frame = _frame([_row(1000, "CE", 10.0), _row(1020, "CE", 15.0)])
    assert opt._build_debit_spread(frame, "CE", 1000, 20, spot=1000, dte=10,
                                    lot_size=100, budget=10000, rejected=[]) is None


# --------------------------------------------------------------- payoff diagram
def test_payoff_diagram_naked_long_call_shape():
    idea = {"legs": [{"action": "BUY", "side": "CE", "strike": 1000, "premium": 20.0}]}
    points = opt._payoff_diagram(idea, spot=1000, lot_size=100)
    assert len(points) == opt.PAYOFF_POINTS
    lo_price = points[0]["price"]
    hi_price = points[-1]["price"]
    assert lo_price < 1000 < hi_price
    # far below strike: pure loss of premium
    assert points[0]["pnl"] == pytest.approx(-20.0 * 100)
    # far above strike: solidly profitable
    assert points[-1]["pnl"] > 0


def test_payoff_diagram_debit_spread_caps_profit_and_loss():
    idea = {"legs": [
        {"action": "BUY", "side": "CE", "strike": 1000, "premium": 20.0},
        {"action": "SELL", "side": "CE", "strike": 1020, "premium": 12.0},
    ]}
    points = opt._payoff_diagram(idea, spot=1000, lot_size=100)
    pnls = [p["pnl"] for p in points]
    assert min(pnls) == pytest.approx(-8.0 * 100)   # net debit
    assert max(pnls) == pytest.approx((20 - 8) * 100)  # width - net debit


# ------------------------------------------------------------- select_option_idea
def _fake_analysis(**overrides):
    base = {
        "symbol": "TEST", "spot": 1000.0, "expiry": "25-Aug-2026", "dte": 10,
        "score": 70.0, "direction": "CE", "lot_size": 100, "atm_strike": 1000,
        "_step": 20,
        "_frame": _frame([_row(1000, "CE", 20.0), _row(1020, "CE", 12.0), _row(1040, "CE", 7.0)]),
    }
    base.update(overrides)
    return base


def test_select_option_idea_neutral_gets_no_idea(monkeypatch):
    monkeypatch.setattr(opt, "analyze_option_chain", lambda *a, **k: _fake_analysis(direction="NEUTRAL"))
    result = opt.select_option_idea("TEST", {})
    assert result["idea"] is None
    assert result["reason"] == "no directional edge"


def test_select_option_idea_near_expiry_blocked(monkeypatch):
    monkeypatch.setattr(opt, "analyze_option_chain", lambda *a, **k: _fake_analysis(dte=1))
    result = opt.select_option_idea("TEST", {})
    assert result["idea"] is None
    assert result["reason"] == "near-expiry blocked"
    assert result["rejected"][0]["kind"] == "near_expiry"


def test_select_option_idea_no_lot_size_blocked(monkeypatch):
    monkeypatch.setattr(opt, "analyze_option_chain", lambda *a, **k: _fake_analysis(lot_size=None))
    result = opt.select_option_idea("TEST", {})
    assert result["idea"] is None
    assert result["reason"] == "lot size unavailable"


def test_select_option_idea_prefers_spread_over_naked_long(monkeypatch):
    monkeypatch.setattr(opt, "analyze_option_chain", lambda *a, **k: _fake_analysis())
    result = opt.select_option_idea("TEST", {})
    assert result["idea"]["strategy"] == "debit_spread"
    assert "thesis" in result["idea"] and "TEST" in result["idea"]["thesis"]
    assert len(result["idea"]["payoff"]) == opt.PAYOFF_POINTS


def test_select_option_idea_falls_back_to_naked_long_when_spread_over_budget(monkeypatch):
    # Only ATM leg is cheap enough for a naked long; the spread's net debit
    # (using far pricier premiums) blows the budget every width.
    frame = _frame([_row(1000, "CE", 20.0, oi=10000)])  # no other strikes -> no spread possible
    monkeypatch.setattr(opt, "analyze_option_chain",
                        lambda *a, **k: _fake_analysis(_frame=frame))
    result = opt.select_option_idea("TEST", {}, budget=10000)
    assert result["idea"]["strategy"] == "long"


def test_select_option_idea_nothing_qualifies(monkeypatch):
    frame = _frame([_row(1000, "CE", 500.0, oi=10000)])  # far over any reasonable budget
    monkeypatch.setattr(opt, "analyze_option_chain",
                        lambda *a, **k: _fake_analysis(_frame=frame))
    result = opt.select_option_idea("TEST", {}, budget=10000)
    assert result["idea"] is None
    assert result["reason"] == "no qualifying option trade today"
    assert len(result["rejected"]) > 0


def test_select_option_idea_propagates_analysis_error(monkeypatch):
    monkeypatch.setattr(opt, "analyze_option_chain", lambda *a, **k: {"symbol": "TEST", "error": "chain empty"})
    result = opt.select_option_idea("TEST", {})
    assert result["idea"] is None
    assert result["reason"] == "chain empty"


# ------------------------------------------------------------------- rank_ideas
def test_rank_ideas_sorts_by_probability_descending():
    ideas = [
        {"symbol": "A", "idea": {"probability": 0.3}},
        {"symbol": "B", "idea": {"probability": 0.7}},
        {"symbol": "C", "idea": None},
        {"symbol": "D", "idea": {"probability": 0.5}},
    ]
    ranked = opt.rank_ideas(ideas)
    assert [r["symbol"] for r in ranked] == ["B", "D", "A", "C"]


def test_rank_ideas_none_probability_sorts_above_no_idea():
    ideas = [
        {"symbol": "A", "idea": None},
        {"symbol": "B", "idea": {"probability": None}},
    ]
    ranked = opt.rank_ideas(ideas)
    assert ranked[0]["symbol"] == "B"
