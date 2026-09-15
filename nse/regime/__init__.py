"""Market regime classification (Phase 6). See classify.py for the rule and
compare.py for the out-of-sample regime-vs-static-style comparison."""

from .classify import REGIME_LABELS, build_regime_history, classify_row  # noqa: F401
from .compare import run_regime_switch_audit  # noqa: F401
