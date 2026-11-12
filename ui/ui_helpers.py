"""Small formatting helpers and status-pill rules shared by the main window and its tabs."""

from typing import List, Any
from core.result import Status, aggregate_status
from ui.i18n import t


def _tri_key(value: Any) -> str:
    """Translation key for a yes / no / unknown value."""
    return "common.unknown" if value is None else ("common.yes" if value else "common.no")


def _feature_key(value: Any) -> str:
    return "feature.UNKNOWN" if value is None else ("feature.DECLARED" if value else "feature.NOT_DECLARED")


def _flinger_key(value: Any) -> str:
    return "audio.flinger.UNKNOWN" if value is None else ("audio.flinger.RUNNING" if value else "audio.flinger.NOT_RUNNING")


def maintenance_pill_status(actions: List[Any]) -> str:
    """Overall maintenance status: the same rule as every other aggregate (no actions -> UNKNOWN)."""
    return str(aggregate_status([getattr(a, "status", Status.UNKNOWN) for a in actions]))


def bootrom_pill_status(diag: Any) -> str:
    """The port scan reports which USB modes were seen, not a pass/fail reading. Fastboot or MTP
    are normal modes and even EDL/BROM can be intentional, so the pill never claims FAIL or PASS."""
    return str(Status.UNKNOWN)


def _fmt(value: Any, unit: str = "") -> str:
    """Format a reading; None is shown as UNKNOWN (never as 0 or a guess)."""
    if value is None:
        return "UNKNOWN"
    return f"{value} {unit}".strip()


def format_evidence_for_display(evidence_items: Any, is_ar: bool = True, lang: Any = None) -> str:
    """Renders a list of Evidence objects with source and excerpt formatted separately for UI display."""
    if not evidence_items:
        return ""
    if isinstance(evidence_items, str):
        evidence_list = [{"source": "Raw Data", "raw_excerpt": evidence_items.strip(), "exit_code": None}]
    elif isinstance(evidence_items, list):
        evidence_list = evidence_items
    else:
        evidence_list = [evidence_items]

    if not evidence_list:
        return ""

    lang = lang or ("ar" if is_ar else "en")
    header = t("ui.technical_evidence", lang)
    lines = [header]
    for ev in evidence_list:
        src = getattr(ev, "source", None) or (ev.get("source") if isinstance(ev, dict) else "")
        exc = getattr(ev, "raw_excerpt", None) or (ev.get("raw_excerpt") if isinstance(ev, dict) else "")
        code = getattr(ev, "exit_code", 0) if hasattr(ev, "exit_code") else (ev.get("exit_code") if isinstance(ev, dict) else 0)
        code_str = f" [exit {code}]" if code is not None and code != 0 else ""
        lines.append(t("ui.source", lang, src=src, code_str=code_str))
        if exc:
            for eline in str(exc).strip().splitlines():
                lines.append(f"    {eline}")
    return "\n".join(lines)
