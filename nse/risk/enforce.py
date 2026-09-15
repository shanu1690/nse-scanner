"""The risk gate: mechanical, veto-only enforcement of PROJECT_BRIEF.md
Section 8 / risk-manager.md's rules.

  "You are not an advisor. You enforce mechanical limits the user set. Do
  not offer opinions on whether a trade is a good idea."
  "Flag, do not silently drop. When you veto, the pipeline logs the
  symbol, the rule breached and the value that breached it."

Every veto below carries exactly that: symbol, rule, the value that
breached the limit, and the limit itself -- see RiskVeto and
RiskGateResult.render(), which is what a run report prints.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import sizing as sz
from .correlation import correlation_matrix
from .limits import RiskLimits

__all__ = ["RiskVeto", "RiskGateResult", "enforce_delivery_picks", "enforce_option_ideas"]

REQUIRED_DELIVERY_FIELDS = ("symbol", "entry", "stop")


@dataclass
class RiskVeto:
    symbol: str
    rule: str
    value: object
    limit: object
    detail: str = ""


@dataclass
class RiskGateResult:
    approved: list = field(default_factory=list)
    vetoes: list = field(default_factory=list)
    # Symbols the sector cap could not be checked for -- no verified sector
    # classification available. Reported, never silently treated as "pass".
    sector_unknown: set = field(default_factory=set)

    def render(self) -> str:
        lines = [f"Risk gate: {len(self.approved)} approved, {len(self.vetoes)} vetoed"]
        by_rule: dict = {}
        for v in self.vetoes:
            by_rule.setdefault(v.rule, []).append(v)
        for rule, vs in sorted(by_rule.items()):
            lines.append(f"  {rule}: {len(vs)}")
            for v in vs[:5]:
                lines.append(f"    - {v.symbol}: {v.detail}")
            if len(vs) > 5:
                lines.append(f"    ... +{len(vs) - 5} more")
        if self.sector_unknown:
            shown = sorted(self.sector_unknown)[:10]
            more = f" ... +{len(self.sector_unknown) - 10} more" if len(self.sector_unknown) > 10 else ""
            lines.append(f"  sector cap NOT enforced for {len(self.sector_unknown)} symbol(s) "
                         f"(no verified sector classification supplied): {shown}{more}")
        return "\n".join(lines)


def enforce_delivery_picks(picks: list, limits: RiskLimits, *,
                            price_frames: Optional[dict] = None,
                            sector_map: Optional[dict] = None,
                            open_ideas: Optional[list] = None) -> RiskGateResult:
    """`picks`: dicts with at least symbol/entry/stop (target1/target2
    optional, used for reward:risk), in the PRIORITY ORDER limits should be
    applied in -- this function does not re-sort; a caller that wants
    "strongest picks win when a cap binds" must sort by score descending
    before calling this.

    `open_ideas`: already-published/open picks (same shape, already
    risk-sized -- carrying "rupee_risk" and "sector") whose risk counts
    against the sector/heat/max-open-ideas limits from the start, since
    they're real existing exposure, not candidates. None means a cold
    start with nothing else open.

    `sector_map`/`price_frames` are optional: omitting either narrows what
    can be enforced (sector cap / correlation cap respectively) rather
    than raising -- reported via `sector_unknown` and by simply not
    vetoing on correlation, never by guessing.
    """
    result = RiskGateResult()
    open_ideas = open_ideas or []
    sector_map = sector_map or {}

    sector_risk: dict = {}
    for oi in open_ideas:
        sec = sector_map.get(oi.get("symbol"))
        if sec:
            sector_risk[sec] = sector_risk.get(sec, 0.0) + (oi.get("rupee_risk") or 0.0)
    portfolio_risk = sum(oi.get("rupee_risk") or 0.0 for oi in open_ideas)
    n_open = len(open_ideas)

    # One correlation matrix over every symbol that could possibly need
    # checking (all candidates + all already-open symbols), computed once
    # rather than rebuilt on every loop iteration.
    all_symbols = [p.get("symbol") for p in picks] + [oi.get("symbol") for oi in open_ideas]
    corr = (correlation_matrix(all_symbols, price_frames, limits.correlation_lookback_days)
            if price_frames else None)

    approved_syms = [oi.get("symbol") for oi in open_ideas]

    for pick in picks:
        symbol = pick.get("symbol")

        # Rule: a pick without a stop is not a pick.
        missing = [f for f in REQUIRED_DELIVERY_FIELDS if pick.get(f) is None]
        if missing:
            result.vetoes.append(RiskVeto(symbol, "missing_required_field", missing, None,
                                          f"missing {missing}"))
            continue

        if n_open >= limits.max_open_ideas:
            result.vetoes.append(RiskVeto(
                symbol, "max_open_ideas", n_open, limits.max_open_ideas,
                f"{n_open} open ideas already at the {limits.max_open_ideas} cap"))
            continue

        sized = sz.position_size(pick["entry"], pick["stop"], limits.capital, limits.per_trade_risk_pct)
        if sized is None:
            raw_risk = (pick["entry"] - pick["stop"]) if pick.get("stop") is not None else None
            result.vetoes.append(RiskVeto(
                symbol, "invalid_stop_distance", raw_risk, 0,
                "stop does not define a positive, sizeable risk at this capital/risk%"))
            continue
        rupee_risk = sized["rupee_risk"]

        prospective_heat_pct = (portfolio_risk + rupee_risk) / limits.capital * 100
        if prospective_heat_pct > limits.max_portfolio_heat_pct:
            result.vetoes.append(RiskVeto(
                symbol, "portfolio_heat", round(prospective_heat_pct, 2), limits.max_portfolio_heat_pct,
                f"adding this pick would take portfolio heat to {prospective_heat_pct:.2f}%"))
            continue

        sector = sector_map.get(symbol)
        if sector is None:
            result.sector_unknown.add(symbol)
        else:
            prospective_sector_risk = sector_risk.get(sector, 0.0) + rupee_risk
            prospective_sector_pct = prospective_sector_risk / limits.capital * 100
            if prospective_sector_pct > limits.max_sector_pct:
                result.vetoes.append(RiskVeto(
                    symbol, "sector_cap", round(prospective_sector_pct, 2), limits.max_sector_pct,
                    f"sector '{sector}' would reach {prospective_sector_pct:.2f}% of capital's risk budget"))
                continue

        if corr is not None and symbol in corr.columns and approved_syms:
            available = [s for s in approved_syms if s in corr.columns]
            if available:
                other_corrs = corr.loc[symbol, available]
                worst_sym = other_corrs.abs().idxmax()
                worst_val = float(other_corrs[worst_sym])
                if abs(worst_val) > limits.max_correlation:
                    result.vetoes.append(RiskVeto(
                        symbol, "correlation_cap", round(worst_val, 2), limits.max_correlation,
                        f"correlated {worst_val:+.2f} with already-approved {worst_sym} "
                        f"over the trailing {limits.correlation_lookback_days}d"))
                    continue

        approved = dict(pick)
        approved.update({
            "position_size": sized["shares"],
            "rupee_risk": rupee_risk,
            "risk_pct_of_capital": sized["risk_pct_of_capital"],
            "reward_risk": sz.reward_risk_ratio(
                pick["entry"], pick["stop"], pick.get("target2") or pick.get("target1")),
            "sector": sector,
        })
        result.approved.append(approved)
        approved_syms.append(symbol)
        portfolio_risk += rupee_risk
        if sector:
            sector_risk[sector] = sector_risk.get(sector, 0.0) + rupee_risk
        n_open += 1

    return result


def enforce_option_ideas(idea_results: list, limits: RiskLimits, *,
                          n_open_start: int = 0) -> RiskGateResult:
    """Independent re-verification of the Rs 10,000 budget cap --
    "defence in depth" per risk-manager.md: this must never simply trust
    nse/options.py's own enforcement. Recomputes cost from the idea's own
    legs + lot_size (net premium x lot size) rather than re-reading
    idea['cost'] blindly, and vetoes on a MISMATCH between the two as its
    own integrity issue, distinct from a genuine over-budget idea.

    `idea_results`: nse.options.select_option_idea() results (already
    including refused ones with idea=None -- those pass through untouched,
    since options.py already refused them and there's nothing to re-check).
    `n_open_start`: already-open idea count (delivery + options combined)
    this call's max_open_ideas check should count from.
    """
    result = RiskGateResult()
    n_open = n_open_start
    for r in idea_results:
        symbol = r.get("symbol")
        idea = r.get("idea")
        if idea is None:
            continue

        if n_open >= limits.max_open_ideas:
            result.vetoes.append(RiskVeto(
                symbol, "max_open_ideas", n_open, limits.max_open_ideas,
                f"{n_open} open ideas already at the {limits.max_open_ideas} cap"))
            continue

        lot_size = idea.get("lot_size")
        recomputed_cost = None
        if lot_size:
            net_premium = sum(
                (leg["premium"] if leg["action"] == "BUY" else -leg["premium"])
                for leg in idea.get("legs", [])
            )
            recomputed_cost = round(net_premium * lot_size, 2)

        reported_cost = idea.get("cost")
        if recomputed_cost is not None and reported_cost is not None and \
                abs(recomputed_cost - reported_cost) > 1.0:
            result.vetoes.append(RiskVeto(
                symbol, "cost_mismatch", recomputed_cost, reported_cost,
                "independently recomputed cost disagrees with options.py's own figure -- "
                "refusing to trust either"))
            continue

        cost_to_check = recomputed_cost if recomputed_cost is not None else reported_cost
        if cost_to_check is None or cost_to_check > limits.options_budget_cap:
            result.vetoes.append(RiskVeto(
                symbol, "options_budget_cap", cost_to_check, limits.options_budget_cap,
                f"cost Rs {cost_to_check} > Rs {limits.options_budget_cap:,.0f} cap"))
            continue

        result.approved.append(r)
        n_open += 1

    return result
