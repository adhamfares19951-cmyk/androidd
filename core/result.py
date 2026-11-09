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
    exit_code defaults to None (an unknown exit code must never look like success).
    """
    source: str = ""
    raw_excerpt: str = ""
    exit_code: Optional[int] = None

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

    def add_evidence(self, source: str, raw_excerpt: str, exit_code: Optional[int] = None) -> "CheckResult":
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
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    exit_code: Optional[int] = None,
    path_exists: bool = True
) -> Status:
    """
    Derives initial status from raw stdout, stderr, exit code, and path existence:
    - "Permission denied" / "avc: denied" / "Operation not permitted" in stdout OR stderr -> NEEDS_ROOT
    - Path not existing -> UNKNOWN
    - Both stdout and stderr empty, or missing data -> UNKNOWN
    - exit_code != 0 and no valid stdout -> UNKNOWN
    - Otherwise returns UNKNOWN (a caller MUST parse a real verified value to yield PASS or FAIL).
    """
    if not path_exists:
        return Status.UNKNOWN

    combined = f"{stdout or ''}\n{stderr or ''}".lower()
    if (
        "permission denied" in combined
        or "avc: denied" in combined
        or "operation not permitted" in combined
        or "requires root" in combined
    ):
        return Status.NEEDS_ROOT

    clean_out = (stdout or "").strip()
    clean_err = (stderr or "").strip()

    if not clean_out and not clean_err:
        return Status.UNKNOWN

    if not clean_out and clean_err:
        return Status.UNKNOWN

    if exit_code is not None and exit_code != 0 and not clean_out:
        return Status.UNKNOWN

    return Status.UNKNOWN
