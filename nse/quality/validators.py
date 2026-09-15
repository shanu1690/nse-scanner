"""Data integrity checks with publish veto.

The contract: a FAIL blocks publishing. It is never downgraded to a warning, and
the previous good bundle stays live instead. A dashboard that publishes confident
numbers on broken data is the failure mode this project is most exposed to.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Sequence

import numpy as np
import pandas as pd

__all__ = ["Severity", "CheckResult", "ValidationReport", "DataValidator"]


class Severity(str, Enum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass
class CheckResult:
    name: str
    severity: Severity
    message: str
    offenders: list[str] = field(default_factory=list)
    detail: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        head = f"[{self.severity.value}] {self.name}: {self.message}"
        if self.offenders:
            shown = ", ".join(self.offenders[:10])
            more = f" (+{len(self.offenders) - 10} more)" if len(self.offenders) > 10 else ""
            head += f"\n    offenders: {shown}{more}"
        return head


@dataclass
class ValidationReport:
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, result: CheckResult) -> None:
        self.checks.append(result)

    @property
    def failures(self) -> list[CheckResult]:
        return [c for c in self.checks if c.severity is Severity.FAIL]

    @property
    def warnings(self) -> list[CheckResult]:
        return [c for c in self.checks if c.severity is Severity.WARN]

    @property
    def may_publish(self) -> bool:
        """The veto. Any FAIL blocks the publish step."""
        return not self.failures

    def render(self) -> str:
        lines = [str(c) for c in self.checks]
        verdict = "PUBLISH ALLOWED" if self.may_publish else "PUBLISH BLOCKED"
        lines.append(
            f"--- {verdict}: {len(self.failures)} failed, "
            f"{len(self.warnings)} warned, {len(self.checks)} checks run"
        )
        return "\n".join(lines)


class DataValidator:
    """Runs the standard suite over a scored-picks frame before publishing."""

    def __init__(
        self,
        *,
        required_columns: Sequence[str] = ("symbol", "close", "volume", "score"),
        min_coverage: float = 0.95,
        max_staleness_minutes: int = 20,
        continuity_tolerance: float = 0.30,
    ) -> None:
        self.required_columns = list(required_columns)
        self.min_coverage = min_coverage
        self.max_staleness_minutes = max_staleness_minutes
        self.continuity_tolerance = continuity_tolerance

    # ------------------------------------------------------------------ suite
    def validate(
        self,
        df: pd.DataFrame,
        *,
        universe: Sequence[str],
        as_of: datetime,
        previous_row_count: int | None = None,
    ) -> ValidationReport:
        report = ValidationReport()
        report.add(self._schema(df))
        if report.failures:
            return report  # everything downstream assumes the schema holds
        report.add(self._duplicates(df))
        report.add(self._coverage(df, universe))
        report.add(self._staleness(df, as_of))
        report.add(self._sanity(df))
        report.add(self._continuity(df, previous_row_count))
        return report

    # ----------------------------------------------------------------- checks
    def _schema(self, df: pd.DataFrame) -> CheckResult:
        missing = [c for c in self.required_columns if c not in df.columns]
        if missing:
            return CheckResult(
                "schema", Severity.FAIL, f"missing required columns: {missing}"
            )
        if df.empty:
            return CheckResult("schema", Severity.FAIL, "frame is empty")
        null_required = [
            c for c in self.required_columns if df[c].isna().any()
        ]
        if null_required:
            return CheckResult(
                "schema",
                Severity.FAIL,
                f"nulls in required columns: {null_required}",
                offenders=df.loc[df[null_required].isna().any(axis=1), "symbol"]
                .astype(str)
                .tolist(),
            )
        return CheckResult("schema", Severity.PASS, f"{len(df)} rows, columns present")

    def _duplicates(self, df: pd.DataFrame) -> CheckResult:
        dupes = df.loc[df["symbol"].duplicated(keep=False), "symbol"].unique().tolist()
        if dupes:
            return CheckResult(
                "duplicates",
                Severity.FAIL,
                f"{len(dupes)} symbols appear more than once",
                offenders=[str(d) for d in dupes],
            )
        return CheckResult("duplicates", Severity.PASS, "no duplicate symbols")

    def _coverage(self, df: pd.DataFrame, universe: Sequence[str]) -> CheckResult:
        if not universe:
            return CheckResult("coverage", Severity.WARN, "empty universe supplied")
        present = set(df["symbol"].astype(str))
        missing = [s for s in universe if s not in present]
        ratio = 1 - len(missing) / len(universe)
        detail = {"coverage": round(ratio, 4), "universe": len(universe)}
        if ratio < self.min_coverage:
            return CheckResult(
                "coverage",
                Severity.FAIL,
                f"{ratio:.1%} of universe scored, below {self.min_coverage:.0%} floor",
                offenders=[str(s) for s in missing],
                detail=detail,
            )
        return CheckResult(
            "coverage", Severity.PASS, f"{ratio:.1%} of universe scored", detail=detail
        )

    def _staleness(self, df: pd.DataFrame, as_of: datetime) -> CheckResult:
        if "fetched_at" not in df.columns:
            return CheckResult(
                "staleness",
                Severity.FAIL,
                "no fetched_at column - staleness cannot be established, so the "
                "data cannot be trusted as live",
            )
        if as_of.tzinfo is None:
            raise ValueError("as_of must be timezone-aware")
        ts = pd.to_datetime(df["fetched_at"], utc=True, errors="coerce")
        if ts.isna().any():
            return CheckResult(
                "staleness",
                Severity.FAIL,
                "unparseable fetched_at values",
                offenders=df.loc[ts.isna(), "symbol"].astype(str).tolist(),
            )
        age_min = (pd.Timestamp(as_of).tz_convert("UTC") - ts).dt.total_seconds() / 60
        stale = df.loc[age_min > self.max_staleness_minutes, "symbol"].astype(str).tolist()
        future = df.loc[age_min < -1, "symbol"].astype(str).tolist()
        if future:
            return CheckResult(
                "staleness",
                Severity.FAIL,
                "fetched_at in the future - clock skew or a fabricated timestamp",
                offenders=future,
            )
        if stale:
            return CheckResult(
                "staleness",
                Severity.FAIL,
                f"{len(stale)} rows older than {self.max_staleness_minutes} min",
                offenders=stale,
                detail={"max_age_min": round(float(age_min.max()), 1)},
            )
        return CheckResult(
            "staleness",
            Severity.PASS,
            f"oldest row {age_min.max():.1f} min",
            detail={"max_age_min": round(float(age_min.max()), 1)},
        )

    def _sanity(self, df: pd.DataFrame) -> CheckResult:
        problems: list[str] = []
        offenders: list[str] = []

        def flag(mask: pd.Series, label: str) -> None:
            if mask.any():
                problems.append(f"{label} ({int(mask.sum())})")
                offenders.extend(df.loc[mask, "symbol"].astype(str).tolist())

        flag(df["close"] <= 0, "non-positive close")
        flag(~np.isfinite(df["close"]), "non-finite close")
        flag(df["volume"] < 0, "negative volume")
        if "high" in df.columns and "low" in df.columns:
            flag(df["high"] < df["low"], "high below low")
            flag(df["close"] > df["high"], "close above high")
            flag(df["close"] < df["low"], "close below low")
        if "probability" in df.columns:
            flag(
                (df["probability"] < 0) | (df["probability"] > 1),
                "probability outside [0,1]",
            )
        if problems:
            return CheckResult(
                "sanity",
                Severity.FAIL,
                "; ".join(problems),
                offenders=sorted(set(offenders)),
            )
        return CheckResult("sanity", Severity.PASS, "value ranges plausible")

    def _continuity(self, df: pd.DataFrame, previous: int | None) -> CheckResult:
        if previous is None:
            return CheckResult(
                "continuity", Severity.PASS, "no previous run to compare against"
            )
        if previous == 0:
            return CheckResult("continuity", Severity.WARN, "previous run had 0 rows")
        change = abs(len(df) - previous) / previous
        detail = {"previous": previous, "current": len(df), "change": round(change, 3)}
        if change > self.continuity_tolerance:
            return CheckResult(
                "continuity",
                Severity.FAIL,
                f"row count moved {change:.0%} vs previous run "
                f"({previous} -> {len(df)}); something upstream likely broke",
                detail=detail,
            )
        return CheckResult(
            "continuity", Severity.PASS, f"row count stable ({change:.0%})", detail=detail
        )


def scan_bundle_for_credentials(payload: str) -> list[str]:
    """Last line of defence before a world-readable bundle is written.

    Returns the credential-shaped keys found. Non-empty means do not publish.
    """
    import re

    pattern = re.compile(
        r"(api_?key|apikey|client_?code|client_?id|mpin|totp|password|"
        r"secret|access_?token|refresh_?token|feed_?token|jwt)",
        re.IGNORECASE,
    )
    return sorted(set(m.group(0) for m in pattern.finditer(payload)))
