"""Result data structures and scoring for the AI Visibility Audit."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Status vocabulary
PASS = "pass"
WARN = "warn"
FAIL = "fail"
ERROR = "error"   # the check could not be evaluated (network, tool missing…)
INFO = "info"

STATUS_LABEL = {
    PASS: "Pass",
    WARN: "Warning",
    FAIL: "Fail",
    ERROR: "Not determined",
    INFO: "Info",
}
STATUS_COLOR = {
    PASS: "#16a34a",   # green
    WARN: "#d97706",   # amber
    FAIL: "#dc2626",   # red
    ERROR: "#6b7280",  # grey
    INFO: "#317fd2",   # brand blue
}
STATUS_ICON = {
    PASS: "✓",
    WARN: "!",
    FAIL: "✕",
    ERROR: "?",
    INFO: "i",
}


@dataclass
class CheckResult:
    id: str
    title: str
    status: str
    summary: str
    weight: float
    # 0..1 contribution toward this check's weight. None => excluded from the
    # overall score (e.g. the check errored), shown but not counted.
    fraction: Optional[float] = None
    details: List[str] = field(default_factory=list)
    remediation: str = ""
    evidence: Dict[str, Any] = field(default_factory=dict)

    @property
    def points(self) -> float:
        return 0.0 if self.fraction is None else round(self.fraction * self.weight, 2)

    @property
    def counted(self) -> bool:
        return self.fraction is not None


def grade_for(score: int) -> Dict[str, str]:
    if score >= 90:
        return {"grade": "A", "label": "Excellent", "color": "#16a34a"}
    if score >= 75:
        return {"grade": "B", "label": "Good", "color": "#65a30d"}
    if score >= 60:
        return {"grade": "C", "label": "Fair", "color": "#d97706"}
    if score >= 40:
        return {"grade": "D", "label": "Poor", "color": "#ea580c"}
    return {"grade": "F", "label": "Critical", "color": "#dc2626"}


@dataclass
class AuditReport:
    domain: str
    checks: List[CheckResult]
    ran_at: str = field(default_factory=lambda: datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"))

    @property
    def score(self) -> int:
        counted = [c for c in self.checks if c.counted]
        denom = sum(c.weight for c in counted)
        if denom <= 0:
            return 0
        return round(100 * sum(c.points for c in counted) / denom)

    @property
    def grade(self) -> Dict[str, str]:
        return grade_for(self.score)

    @property
    def n_pass(self) -> int:
        return sum(1 for c in self.checks if c.status == PASS)

    @property
    def n_fail(self) -> int:
        return sum(1 for c in self.checks if c.status == FAIL)

    @property
    def n_warn(self) -> int:
        return sum(1 for c in self.checks if c.status == WARN)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "domain": self.domain,
            "ran_at": self.ran_at,
            "score": self.score,
            "grade": self.grade,
            "checks": [
                {
                    "id": c.id, "title": c.title, "status": c.status,
                    "summary": c.summary, "weight": c.weight,
                    "fraction": c.fraction, "points": c.points,
                    "details": c.details, "remediation": c.remediation,
                    "evidence": c.evidence,
                }
                for c in self.checks
            ],
        }
