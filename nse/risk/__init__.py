"""Risk management (Phase 8): position sizing, sector/correlation caps,
portfolio heat, and options-budget defense-in-depth. See enforce.py for
the veto engine and PROJECT_BRIEF.md Section 8 / .claude/agents/
risk-manager.md for the rules this implements.

sector_map is an OPTIONAL input supplied by the caller (nse/sitebuilder.py
loads it from nse/sectors.py's cache), not something this package
fabricates or looks up on its own -- nse/risk/ itself stays data-source-
agnostic. Real, live NSE sector classification IS available (see
nse/sectors.py's docstring for how it was found, after NSE's older
per-symbol/sectoral-index endpoints both returned a genuine "Resource not
found"); `nse-scan refresh-sectors` populates config/sector_map.json.
Symbols missing from that cache (never refreshed, or genuinely new/
delisted) still skip the sector cap check (reported via
RiskGateResult.sector_unknown) rather than being silently assumed
compliant -- this remains true even with a populated cache, it just
affects fewer symbols now.
"""

from .enforce import RiskGateResult, RiskVeto, enforce_delivery_picks, enforce_option_ideas  # noqa: F401
from .limits import RiskLimits  # noqa: F401
from .sizing import position_size, reward_risk_ratio  # noqa: F401
