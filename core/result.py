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


PERMISSION_PATTERNS = ("permission denied", "avc: denied", "operation not permitted")


def status_from_read(
    stdout: Optional[str] = None,
    stderr: Optional[str] = None,
    exit_code: Optional[int] = None,
    path_exists: bool = True,
    log_content: bool = False
) -> Status:
    """
    Derive the status of a raw read BEFORE parsing:
    - "Permission denied" / "avc: denied" / "Operation not permitted" in stderr or stdout -> NEEDS_ROOT
    - Path missing -> UNKNOWN
    - Anything else -> UNKNOWN (the caller must parse a real value to return PASS or FAIL)

    log_content=True is for commands whose stdout is itself a log (dmesg, logcat, ramoops).
    Such logs legitimately contain "avc: denied" audit lines about other processes, so for
    stdout only the plain permission errors are checked; stderr is always fully checked.
    """
    err = (stderr or "").lower()
    out = (stdout or "").lower()
    if any(p in err for p in PERMISSION_PATTERNS):
        return Status.NEEDS_ROOT
    if log_content:
        # A real log has many lines; only a short output can be the command's own error message.
        if len(out.strip().splitlines()) <= 2 and any(p in out for p in ("permission denied", "operation not permitted")):
            return Status.NEEDS_ROOT
        return Status.UNKNOWN
    if any(p in out for p in PERMISSION_PATTERNS):
        return Status.NEEDS_ROOT
    return Status.UNKNOWN


def aggregate_status(statuses: List[Any]) -> Status:
    """
    Overall status of several key checks:
    FAIL if any FAIL, else UNKNOWN if any check is UNKNOWN/NEEDS_ROOT (or there are none), else PASS.
    """
    values = [Status(str(s)) for s in statuses]
    if not values:
        return Status.UNKNOWN
    if Status.FAIL in values:
        return Status.FAIL
    if any(v in (Status.UNKNOWN, Status.NEEDS_ROOT) for v in values):
        return Status.UNKNOWN
    return Status.PASS
