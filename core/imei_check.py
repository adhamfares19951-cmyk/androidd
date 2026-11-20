"""IMEI validation and blacklist (lost/stolen) lookup - the lawful, dealer-facing side of
IMEI: checking a used phone before buying it, never changing it.

Two layers, both honest:

1. Local, offline, free: validate the IMEI's format and Luhn check digit. A typo or an
   invalid IMEI is caught here with no network and no cost.
2. Blacklist lookup, BYOK (Bring Your Own Key): the technician supplies their own API
   endpoint and key (e.g. a DHRU Fusion / SickW account). Only a real response from that
   service yields a blacklist verdict; with no service configured, or an ambiguous/failed
   response, the result is UNKNOWN - a "clean" verdict is never invented, and the raw
   response is always kept as evidence so the technician can read it themselves.
"""
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from core.result import Evidence, M, ProducesCheckResult, Status


@dataclass
class BlacklistConfig:
    """Technician-owned lookup account. Nothing here ships with the program."""
    endpoint: str = ""
    api_key: str = ""
    service_id: str = ""
    provider: str = "generic"      # generic / dhru / sickw - affects request shape only


def luhn_ok(digits: str) -> bool:
    """Standard Luhn checksum over the 15 IMEI digits."""
    if not digits.isdigit():
        return False
    total = 0
    # Rightmost digit is the check digit; double every second digit moving left.
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 1:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return total % 10 == 0


class ImeiCheckResult(ProducesCheckResult):
    check_id = "imei.check"
    _check_detail_attr = "detail"

    def __init__(self, imei: str, status: Status, valid_format: bool, blacklist: str,
                 detail: Any = None, evidence: Optional[List[Evidence]] = None):
        self.imei = imei
        self.status = status
        self.valid_format = valid_format
        self.blacklist = blacklist          # "clean" / "blacklisted" / "unknown"
        self.detail = detail
        self.evidence = evidence or []
        self.possible_causes: List[Any] = []

    def _check_value(self):
        return self.imei, ""


def normalize_imei(imei: str) -> str:
    """Keep digits only (drop spaces, dashes); a 15-digit IMEISV is trimmed to its IMEI 14+1."""
    digits = "".join(c for c in (imei or "") if c.isdigit())
    return digits


def validate_imei(imei: str) -> ImeiCheckResult:
    """Format + Luhn only. 15 digits that pass Luhn are a well-formed IMEI (not a blacklist
    verdict - that needs the lookup below)."""
    digits = normalize_imei(imei)
    if len(digits) != 15:
        return ImeiCheckResult(digits, Status.FAIL, False, "unknown",
                               detail=M("imei.bad_length", n=len(digits)))
    if not luhn_ok(digits):
        return ImeiCheckResult(digits, Status.FAIL, False, "unknown",
                               detail=M("imei.bad_checksum"))
    return ImeiCheckResult(digits, Status.PASS, True, "unknown",
                           detail=M("imei.format_ok"))


def _interpret_blacklist(text: str) -> str:
    """Conservative reading of a lookup response: 'clean', 'blacklisted', or 'unknown'.
    Never guesses clean - anything unclear stays unknown, and the raw text is the record."""
    t = (text or "").lower()
    clean_markers = ("not blacklisted", "no blacklist", "blacklist status: clean",
                     "blacklist: clean", "status: clean", "not reported", "no record",
                     "not found in", "is clean", "gsma: clean")
    bad_markers = ("blacklisted", "reported lost", "reported stolen", "lost or stolen",
                   "lost/stolen", "is barred", "blocked by carrier")
    negated = any(m in t for m in clean_markers)
    # A positive hit counts only when it is not the negated "not blacklisted" form.
    positive = any(m in t for m in bad_markers) and "not blacklisted" not in t and "no blacklist" not in t
    if positive:
        return "blacklisted"
    if negated:
        return "clean"
    return "unknown"


def _default_transport(endpoint: str, params: dict, timeout: int = 20) -> str:
    import urllib.parse
    import urllib.request
    data = urllib.parse.urlencode(params).encode("utf-8")
    with urllib.request.urlopen(endpoint, data=data, timeout=timeout) as resp:  # nosec - tech URL
        return resp.read().decode("utf-8", "replace")


def check_blacklist(imei: str, config: Optional[BlacklistConfig] = None,
                    transport: Optional[Callable[[str, dict], str]] = None) -> ImeiCheckResult:
    """Validate the IMEI, then (only if a BYOK service is configured) look up its blacklist
    status. The verdict comes solely from the service response."""
    base = validate_imei(imei)
    if not base.valid_format:
        return base      # a malformed IMEI is not worth a paid lookup

    if config is None or not config.endpoint or not config.api_key:
        return ImeiCheckResult(base.imei, Status.UNKNOWN, True, "unknown",
                               detail=M("imei.no_service"))

    params = {"imei": base.imei, "key": config.api_key}
    if config.service_id:
        params["service"] = config.service_id
    send = transport or _default_transport
    try:
        raw = send(config.endpoint, params)
    except Exception as e:
        return ImeiCheckResult(base.imei, Status.UNKNOWN, True, "unknown",
                               detail=M("imei.lookup_failed", error=str(e)),
                               evidence=[Evidence(source="imei:lookup", raw_excerpt=str(e))])

    verdict = _interpret_blacklist(raw)
    ev = [Evidence(source="imei:lookup", raw_excerpt=(raw or "")[:1500])]
    if verdict == "blacklisted":
        return ImeiCheckResult(base.imei, Status.FAIL, True, "blacklisted",
                               detail=M("imei.blacklisted"), evidence=ev)
    if verdict == "clean":
        return ImeiCheckResult(base.imei, Status.PASS, True, "clean",
                               detail=M("imei.clean"), evidence=ev)
    return ImeiCheckResult(base.imei, Status.UNKNOWN, True, "unknown",
                           detail=M("imei.lookup_unclear"), evidence=ev)


def read_imei(bridge, serial: str) -> Optional[str]:
    """Best-effort device read. Modern Android restricts this without privilege, so a miss
    is normal and returns None (the technician enters the IMEI by hand) - never a fake value."""
    try:
        code, out, _ = bridge.shell("service call iphonesubinfo 1", serial=serial, timeout=6)
    except Exception:
        return None
    if code != 0 or not out:
        return None
    # Parcel dump interleaves the digits as UTF-16 chars between dots; pull digits out.
    digits = "".join(c for c in out if c.isdigit())
    # The parcel contains length/able words too; a clean IMEI read ends with 15 trailing digits.
    tail = digits[-15:] if len(digits) >= 15 else ""
    return tail if len(tail) == 15 and luhn_ok(tail) else None
