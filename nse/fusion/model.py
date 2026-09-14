"""Logistic-regression fusion model + calibration (Phase 6).

Model choice follows PROJECT_BRIEF.md Section 6's explicit guidance:
"start with logistic regression ... interpretable, hard to overfit" -- with
at most a few thousand point-in-time observations, anything deeper mostly
memorises noise, and every pick has to explain itself in plain English,
which a linear model's coefficients make direct.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.preprocessing import StandardScaler

try:
    # sklearn >=1.6: cv="prefit" is deprecated (removal slated for 1.8) in
    # favour of wrapping the already-fitted estimator explicitly.
    from sklearn.frozen import FrozenEstimator
except ImportError:  # sklearn <1.6: FrozenEstimator doesn't exist yet
    FrozenEstimator = None

__all__ = ["FusionModel", "REASON_TEMPLATES"]

# Plain-English labels for the top-contributor report. Falls back to the raw
# feature name for anything not listed (e.g. a `<name>_missing` flag, or a
# fundamental factor added later that this table hasn't caught up with yet).
REASON_TEMPLATES = {
    "trend": "Trend strength (price vs EMA structure)",
    "breakout": "Proximity to a breakout / 52-week high",
    "momentum": "Price momentum (rate of change)",
    "volume": "Volume confirmation (above-average activity)",
    "relative_strength": "Relative strength vs NIFTY",
    "vix_level": "India VIX level",
    "vix_percentile": "India VIX vs its own trailing history",
    "vix_trend_5d": "5-day VIX trend",
    "pct_above_50dma": "Market breadth (% of universe above 50DMA)",
    "advance_decline_ratio": "Market breadth (advance/decline ratio)",
    "nifty_trend_flag": "NIFTY trend regime",
    "net_margin": "Net profit margin",
    "effective_tax_rate": "Effective tax rate",
    "interest_coverage": "Interest coverage",
    "revenue_growth_yoy": "Revenue growth (YoY)",
    "net_profit_growth_yoy": "Net profit growth (YoY)",
    "eps_growth_yoy": "EPS growth (YoY)",
    "debt_equity_ratio": "Debt/equity ratio",
    "pe_ttm": "Trailing P/E",
}


class FusionModel:
    """StandardScaler + LogisticRegression, fit on a TRAIN split and then
    separately calibrated (Platt/sigmoid or isotonic) on a disjoint
    CALIBRATION split -- never the same rows for both, mirroring
    nse/backtest.py's train/select/held-out discipline.

    Reason/contribution reporting always uses the BASE (uncalibrated)
    logistic model's coefficients: calibration is a monotonic reweighting
    of the base model's scalar output applied after the fact, and doesn't
    carry its own per-feature attribution.
    """

    def __init__(self, C: float = 1.0, max_iter: int = 2000):
        self.C = C
        self.max_iter = max_iter
        self.scaler = StandardScaler()
        self.base_model = LogisticRegression(C=C, max_iter=max_iter)
        self.calibrated = None
        self.feature_names_: "list[str] | None" = None

    def fit(self, X_train: pd.DataFrame, y_train) -> "FusionModel":
        self.feature_names_ = list(X_train.columns)
        Xs = self.scaler.fit_transform(X_train[self.feature_names_])
        self.base_model.fit(Xs, np.asarray(y_train))
        return self

    def calibrate(self, X_calib: pd.DataFrame, y_calib, method: str = "sigmoid") -> "FusionModel":
        if self.feature_names_ is None:
            raise RuntimeError("call fit() before calibrate()")
        Xs = self.scaler.transform(X_calib[self.feature_names_])
        if FrozenEstimator is not None:
            self.calibrated = CalibratedClassifierCV(FrozenEstimator(self.base_model), method=method)
        else:
            self.calibrated = CalibratedClassifierCV(estimator=self.base_model,
                                                      method=method, cv="prefit")
        self.calibrated.fit(Xs, np.asarray(y_calib))
        return self

    def predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Calibrated probability if calibrate() has run, else the base
        model's raw sigmoid output."""
        Xs = self.scaler.transform(X[self.feature_names_])
        model = self.calibrated if self.calibrated is not None else self.base_model
        return model.predict_proba(Xs)[:, 1]

    def base_predict_proba(self, X: pd.DataFrame) -> np.ndarray:
        """Uncalibrated probability -- for measuring what calibration
        itself changed (e.g. the Brier-score before/after comparison)."""
        Xs = self.scaler.transform(X[self.feature_names_])
        return self.base_model.predict_proba(Xs)[:, 1]

    def top_contributions(self, x_row: pd.Series, n: int = 5) -> list:
        """Top-n |coefficient * standardized value| feature contributions
        for one row, from the BASE model (see class docstring)."""
        if self.feature_names_ is None:
            raise RuntimeError("call fit() before top_contributions()")
        Xs = self.scaler.transform(pd.DataFrame([x_row[self.feature_names_]]))[0]
        coefs = self.base_model.coef_[0]
        contributions = coefs * Xs
        order = np.argsort(-np.abs(contributions))[:n]
        out = []
        for idx in order:
            name = self.feature_names_[idx]
            base_name = name[:-len("_missing")] if name.endswith("_missing") else name
            out.append({
                "feature": name,
                "label": REASON_TEMPLATES.get(base_name, base_name),
                "contribution": float(contributions[idx]),
                "direction": "up" if contributions[idx] > 0 else "down",
            })
        return out
