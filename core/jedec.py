"""
JEDEC eMMC / UFS health register parsing (life_time estimation and pre-EOL info).

One parser shared by every storage-health check so the thresholds are identical everywhere.
"""

import re
from typing import List, Optional, Tuple

from core.result import Status

# DEVICE_LIFE_TIME_EST_TYP_A/B: value -> (lower %, upper %) of device life time used
LIFE_TIME_RANGES = {
    0x01: (0, 10), 0x02: (10, 20), 0x03: (20, 30), 0x04: (30, 40), 0x05: (40, 50),
    0x06: (50, 60), 0x07: (60, 70), 0x08: (70, 80), 0x09: (80, 90), 0x0A: (90, 100),
    0x0B: (100, None),
}

PRE_EOL_LABELS = {
    0x01: "Normal",
    0x02: "Warning: 80% of reserved blocks consumed",
    0x03: "Urgent: 90% of reserved blocks consumed",
}

# FAIL thresholds: >= 80-90% of life time used, or pre-EOL warning or worse.
LIFE_TIME_FAIL_AT = 0x09
PRE_EOL_FAIL_AT = 0x02

_HEX_RE = re.compile(r"^(0x)?[0-9a-fA-F]{1,2}$")


def parse_register(token: Optional[str]) -> Optional[int]:
    """Parse one register token such as '0x01' or '01'. Returns None if malformed."""
    if token is None:
        return None
    t = token.strip()
    if not _HEX_RE.match(t):
        return None
    return int(t, 16)


def parse_life_time(text: Optional[str]) -> List[int]:
    """
    Parse eMMC `life_time` ("0x01 0x02") or a single UFS life_time_estimation value.
    Returns only valid defined values (0x01..0x0B). 0x00 means "not defined" and is dropped.
    """
    values: List[int] = []
    for token in (text or "").split():
        v = parse_register(token)
        if v is not None and v in LIFE_TIME_RANGES:
            values.append(v)
    return values


def parse_pre_eol(text: Optional[str]) -> Optional[int]:
    tokens = (text or "").split()
    if not tokens:
        return None
    v = parse_register(tokens[0])
    return v if v in PRE_EOL_LABELS else None


def life_time_label(value: int) -> str:
    low, high = LIFE_TIME_RANGES[value]
    if high is None:
        return f"0x{value:02x}: exceeded estimated device life time"
    return f"0x{value:02x}: {low}% - {high}% of device life time used"


def pre_eol_label(value: int) -> str:
    return f"0x{value:02x}: {PRE_EOL_LABELS[value]}"


def evaluate(life_values: List[int], pre_eol: Optional[int]) -> Status:
    """PASS/FAIL only when at least one register was parsed; otherwise UNKNOWN (caller decides NEEDS_ROOT)."""
    if not life_values and pre_eol is None:
        return Status.UNKNOWN
    if any(v >= LIFE_TIME_FAIL_AT for v in life_values) or (pre_eol is not None and pre_eol >= PRE_EOL_FAIL_AT):
        return Status.FAIL
    return Status.PASS


def wear_upper_bound_pct(life_values: List[int]) -> Optional[int]:
    """Upper bound of the worst reported life-time range (e.g. 0x02 -> 20). None if nothing parsed."""
    if not life_values:
        return None
    low, high = LIFE_TIME_RANGES[max(life_values)]
    return 100 if high is None else high
