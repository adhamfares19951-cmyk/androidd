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


def value_or_status(value: Any, unit: str = "", status: Any = None) -> str:
    """A reading with its unit, or, when it was not read, why (NEEDS_ROOT / UNKNOWN) without the unit."""
    if value is None:
        return str(status or "UNKNOWN")
    return f"{value}{unit}" if unit == "%" else f"{value} {unit}".strip()


def format_log_analysis(r: Any, lang: str) -> List[str]:
    """Lines shown for an offline log analysis (core.log_analyzer.LogAnalysis), in the CLI and the GUI alike."""
    from core import log_analyzer as la
    from ui.i18n import t, render
    out = [t("cli.logscan_header", lang, source=r.source)]
    truncated = t("cli.logscan_truncated", lang, mb=la.MAX_BYTES // (1024 * 1024)) if r.truncated else ""
    out.append(t("cli.logscan_summary", lang, lines=r.lines, status=r.status, truncated=truncated))
    if r.soc:
        out.append(t("cli.logscan_soc", lang, soc=r.soc, evidence=r.soc_evidence))
    elif r.soc_candidates:
        out.append(t("cli.logscan_soc_unclear", lang,
                     candidates=", ".join(f"{name} ({n})" for name, n in r.soc_candidates)))
    if r.fatal_line is not None:
        out.append(t("cli.logscan_fatal", lang, line=r.fatal_line_no, text=r.fatal_line))
    for m in r.matches:
        out.append(t("cli.logscan_match", lang, severity=m.severity, rule=m.rule_id, count=m.count,
                     line=m.line_no, text=m.line))
        out.append(f"      {la.description(m, lang)}")
        out.append(f"      {la.action(m, lang)}")
    if r.possible_causes:
        names = r.possible_causes_ar if lang == "ar" and getattr(r, "possible_causes_ar", None) else r.possible_causes
        out.append(t("cli.logscan_causes", lang, causes=", ".join(names)))
    if getattr(r, "board_areas", None):
        out.append(t("cli.logscan_areas", lang, areas=", ".join(t(f"board_area.{a}", lang) for a in r.board_areas)))
    out.append(render(r.diagnosis, lang))
    return out


def format_display_check(r: Any, lang: str) -> List[str]:
    """Lines shown for the screen originality indicators (core.display_check), in the CLI and the GUI alike."""
    from ui.i18n import t, render
    out = [t("cli.display_header", lang, model=r.model or "-"), render(r.diagnosis, lang)]
    for i in r.indicators:
        out.append(t("cli.display_indicator", lang, effect=t(f"display.effect.{i.effect}", lang), text=render(i.text, lang)))
    out.append(t("display.not_proof", lang))
    return out


def format_parts_check(r: Any, lang: str) -> List[str]:
    """Lines shown for the parts indicators (core.parts_check), in the CLI and the GUI alike."""
    from ui.i18n import t, render
    out = [t("cli.parts_header", lang, model=r.reference or r.model or "-"), render(r.diagnosis, lang)]
    for i in r.indicators:
        out.append(t("cli.display_indicator", lang, effect=t(f"display.effect.{i.effect}", lang), text=render(i.text, lang)))
    out.append(t("parts.not_proof", lang))
    return out


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
        code = ev.get("exit_code") if isinstance(ev, dict) else getattr(ev, "exit_code", None)
        code_str = f" [exit {code}]" if code is not None else ""
        lines.append(t("ui.source", lang, src=src, code_str=code_str))
        if exc:
            for eline in str(exc).strip().splitlines():
                lines.append(f"    {eline}")
    return "\n".join(lines)
