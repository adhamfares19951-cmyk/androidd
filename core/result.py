"""
Android Doctor Pro - Core Result & Verification System
Provides the unified 4-State Verdict System, Evidence tracking, and CheckResult contracts.
"""

from enum import Enum
from dataclasses import dataclass, field, asdict
from typing import Optional, List, Any, Dict

MAX_EXCERPT_CHARS = 2048


class Status(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NEEDS_ROOT = "NEEDS_ROOT"

    def __str__(self) -> str:
        return self.value


@dataclass
class Evidence:
    """
    Carries the exact evidentiary audit trail for any hardware, kernel or sysfs check.
    Max excerpt length capped at ~2 KB to prevent report bloat.
    """
    source: str = ""
    raw_excerpt: str = ""
    exit_code: Optional[int] = 0

    def __post_init__(self):
        if self.raw_excerpt and len(self.raw_excerpt) > MAX_EXCERPT_CHARS:
            self.raw_excerpt = self.raw_excerpt[:MAX_EXCERPT_CHARS] + "\n... [truncated]"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "source": self.source,
            "raw_excerpt": self.raw_excerpt,
            "exit_code": self.exit_code
        }


@dataclass
class CheckResult:
    """
    Standardized result contract for all system, hardware and board checks.
    """
    check_id: str
    status: Status = Status.UNKNOWN
    value: Any = None
    unit: str = ""
    message_key: str = ""
    possible_causes: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    @property
    def has_real_data(self) -> bool:
        return self.status in (Status.PASS, Status.FAIL)

    def add_evidence(self, source: str, raw_excerpt: str, exit_code: Optional[int] = 0) -> "CheckResult":
        self.evidence.append(Evidence(source=source, raw_excerpt=raw_excerpt, exit_code=exit_code))
        return self

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "status": str(self.status),
            "value": self.value,
            "unit": self.unit,
            "message_key": self.message_key,
            "possible_causes": list(self.possible_causes),
            "evidence": [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence],
            "has_real_data": self.has_real_data
        }


def status_from_read(
    output: Optional[str],
    exit_code: Optional[int] = 0,
    path_exists: bool = True
) -> Status:
    """
    Derives initial status from raw output and exit code:
    - "Permission denied" / "avc: denied" / "Operation not permitted" -> NEEDS_ROOT
    - Path not existing, empty output, or exit_code != 0 without valid output -> UNKNOWN
    - Otherwise returns UNKNOWN (a caller MUST parse a real verified value to yield PASS or FAIL).
    """
    if not path_exists or output is None:
        return Status.UNKNOWN

    out_lower = output.lower()
    if (
        "permission denied" in out_lower
        or "avc: denied" in out_lower
        or "operation not permitted" in out_lower
        or "requires root" in out_lower
    ):
        return Status.NEEDS_ROOT

    if not output.strip() or (exit_code is not None and exit_code != 0 and not output.strip()):
        return Status.UNKNOWN

    return Status.UNKNOWN
