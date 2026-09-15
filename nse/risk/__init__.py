"""Risk management (Phase 8): position sizing, sector/correlation caps,
portfolio heat, and options-budget defense-in-depth. See enforce.py for
the veto engine and PROJECT_BRIEF.md Section 8 / .claude/agents/
risk-manager.md for the rules this implements.

No verified NSE sector classification data source was found (checked
live; NSE's sectoral-index constituent endpoint returns "Resource not
found" as of this writing) -- sector_map is therefore an OPTIONAL input
supplied by the caller, not something this package fabricates or looks up
on its own. Symbols with no entry in sector_map simply skip the sector
cap check (reported via RiskGateResult.sector_unknown), rather than being
silently assumed compliant.
"""

from .enforce import RiskGateResult, RiskVeto, enforce_delivery_picks, enforce_option_ideas  # noqa: F401
from .limits import RiskLimits  # noqa: F401
from .sizing import position_size, reward_risk_ratio  # noqa: F401
