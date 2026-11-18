"""
HTML diagnostic report.

Every visible text is a translation key (locales/*.json); every value from the device is HTML-escaped.
Without a signing key the report says it is NOT signed (a hash or HMAC with a key stored in this
program would prove nothing). With `signing_key_path` (an Ed25519 key kept outside the program,
see core/report_signing.py) the footer names the key id and `<report>.sig` holds a detached
signature of the file's exact bytes.
"""

import html
import json
import logging
import os
import re
import time
from typing import Any, Dict, List, Optional

from core.result import aggregate_status
from ui.i18n import t, render

logger = logging.getLogger("ReportExporter")

from app_paths import default_reports_dir

REPORTS_DIR = default_reports_dir()  # reports/ from source; Documents in a packaged build (created on first write)

STATUS_COLORS = {"PASS": "#238636", "FAIL": "#da3633", "UNKNOWN": "#6e7681", "NEEDS_ROOT": "#9e6a03"}

CSS = """
@media print { body { -webkit-print-color-adjust: exact; print-color-adjust: exact; } .no-print { display: none !important; } }
* { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, "Segoe UI", Roboto, "Cairo", Tahoma, sans-serif; background: #0d1117; color: #c9d1d9; padding: 30px; font-size: 14px; line-height: 1.6; }
.container { max-width: 960px; margin: 0 auto; background: #161b22; border: 1px solid #30363d; border-radius: 12px; padding: 35px; }
.header { display: flex; justify-content: space-between; align-items: center; border-bottom: 2px solid #30363d; padding-bottom: 20px; margin-bottom: 25px; gap: 20px; }
.header h1 { color: #58a6ff; font-size: 24px; }
.header p { color: #8b949e; font-size: 13px; }
.ref { font-family: monospace; background: #21262d; padding: 6px 12px; border-radius: 6px; color: #79c0ff; border: 1px solid #30363d; font-size: 12px; }
.qr-box { background: #fff; padding: 4px; border-radius: 6px; }
.card { background: #0d1117; border: 1px solid #30363d; border-radius: 8px; padding: 18px; margin-bottom: 20px; }
.card-header { font-size: 14px; font-weight: 600; color: #58a6ff; border-bottom: 1px solid #21262d; padding-bottom: 8px; margin-bottom: 12px; display: flex; justify-content: space-between; align-items: center; }
.grid-2 { display: grid; grid-template-columns: 1fr 1fr; gap: 20px; }
.prop-row { display: flex; justify-content: space-between; gap: 12px; padding: 5px 0; border-bottom: 1px solid #161b22; }
.prop-name { color: #8b949e; }
.prop-val { color: #f0f6fc; font-weight: 500; }
.src { color: #8b949e; font-size: 11px; }
.num { white-space: nowrap; }
.badge { display: inline-block; padding: 3px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; color: #fff; }
.table { width: 100%; border-collapse: collapse; margin-top: 8px; font-size: 13px; }
.table th, .table td { padding: 7px 10px; border: 1px solid #21262d; vertical-align: top; }
.table th { background: #161b22; color: #8b949e; }
.note { font-size: 12px; color: #8b949e; margin-top: 6px; }
.causes { margin: 6px 18px 0 18px; font-size: 12px; color: #f0883e; }
details.evidence-box { margin-top: 10px; background: #090d13; border: 1px solid #21262d; border-radius: 6px; padding: 8px 12px; font-size: 11px; }
details.evidence-box summary { cursor: pointer; color: #58a6ff; font-weight: 600; }
details.evidence-box pre { margin-top: 4px; color: #8b949e; white-space: pre-wrap; word-break: break-all; font-family: Consolas, monospace; background: #010409; padding: 6px 10px; border-radius: 4px; max-height: 220px; overflow: auto; }
.btn-print { background: #238636; color: #fff; padding: 10px 20px; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; }
.footer { border-top: 1px solid #30363d; margin-top: 30px; padding-top: 20px; display: flex; justify-content: space-between; color: #8b949e; font-size: 12px; gap: 20px; }
"""


def _qr_svg(data: Any, size_px: int = 90) -> str:
    """QR code as inline SVG: a dict is encoded as JSON, a string as is (signed summary / link)."""
    try:
        import qrcode
        import qrcode.image.svg
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        img = qrcode.make(text, image_factory=qrcode.image.svg.SvgPathImage, box_size=8)
        svg = img.to_string(encoding="unicode")
        if "<?xml" in svg:
            svg = svg.split("?>", 1)[-1].strip()
        svg = re.sub(r'width="[^"]+"', f'width="{size_px}"', svg, count=1)
        svg = re.sub(r'height="[^"]+"', f'height="{size_px}"', svg, count=1)
        return svg
    except Exception as e:
        logger.debug(f"QR code not rendered: {e}")
        return ""


class DiagnosticReportExporter:
    @staticmethod
    def generate_html_report(
        device_profile: Optional[Dict[str, Any]] = None,
        board_diag: Optional[Dict[str, Any]] = None,
        hw_audit: Optional[Dict[str, Any]] = None,
        sec_scan: Optional[Dict[str, Any]] = None,
        opt_result: Optional[Dict[str, Any]] = None,
        leakage_result: Optional[Dict[str, Any]] = None,
        storage_report: Optional[Dict[str, Any]] = None,
        fast_charge: Optional[Dict[str, Any]] = None,
        storage_life_report: Optional[Dict[str, Any]] = None,
        stress_report: Optional[Dict[str, Any]] = None,
        audio_codec: Optional[Dict[str, Any]] = None,
        panel_info: Optional[Dict[str, Any]] = None,
        root_forensics: Optional[Dict[str, Any]] = None,
        extra_checks: Optional[List[Dict[str, Any]]] = None,
        display_check: Optional[Dict[str, Any]] = None,
        parts_check: Optional[Dict[str, Any]] = None,
        layered: Optional[List[Dict[str, Any]]] = None,
        log_analysis: Optional[Dict[str, Any]] = None,
        signing_key_path: Optional[str] = None,
        technician_name: str = "",
        ticket_id: str = "",
        customer_name: str = "",
        customer_phone: str = "",
        reported_fault: str = "",
        repair_cost: float = 0.0,
        output_path: Optional[str] = None,
        output_dir: Optional[str] = None,
        lang: str = "ar"
    ) -> str:
        # Load the key first: a bad key must fail before anything is written.
        signing_key = None
        if signing_key_path:
            from core.report_signing import load_private_key
            signing_key = load_private_key(signing_key_path)

        dev = device_profile or {}
        bdiag = board_diag or {}
        haudit = hw_audit or {}
        sscan = sec_scan or {}
        oresult = opt_result or {}
        leak = leakage_result or {}
        srep = storage_report or {}
        fcharge = fast_charge or {}
        slife = storage_life_report or {}
        stress = stress_report or {}
        acodec = audio_codec or haudit.get("audio_codec", {})
        pinfo = panel_info or haudit.get("display_panel_info", {})
        rforensics = root_forensics or {}

        def tr(key: str, **kw: Any) -> str:
            return html.escape(t(key, lang, **kw))

        def esc(value: Any) -> str:
            return html.escape(render(value, lang))

        def ltr(text: str, cls: str = "") -> str:
            # Numbers, units and command names stay left-to-right inside an Arabic (RTL) report.
            return f'<bdi dir="ltr"{f" class={chr(34)}{cls}{chr(34)}" if cls else ""}>{text}</bdi>'

        def val(value: Any, unit: str = "") -> str:
            # a value and its unit never wrap apart ("55.2" / "°C")
            return "UNKNOWN" if value is None else ltr(html.escape(f"{value} {unit}".strip()), "num")

        def badge(status: Any) -> str:
            s = str(status or "UNKNOWN")
            return f'<span class="badge" style="background:{STATUS_COLORS.get(s, "#6e7681")}">{html.escape(s)}</span>'

        def row(name_key: str, value_html: str, **kw: Any) -> str:
            return f'<div class="prop-row"><span class="prop-name">{tr(name_key, **kw)}</span><span class="prop-val">{value_html}</span></div>'

        def causes(items: Any) -> str:
            if not items:
                return ""
            lis = "".join(f"<li>{tr('common.possible_cause')}: {esc(c)}</li>" for c in items)
            return f'<ul class="causes">{lis}</ul>'

        def evidence(items: Any) -> str:
            if not items:
                return ""
            if not isinstance(items, list):
                items = [items]
            blocks = []
            for ev in items:
                src = getattr(ev, "source", None) if not isinstance(ev, dict) else ev.get("source")
                exc = getattr(ev, "raw_excerpt", None) if not isinstance(ev, dict) else ev.get("raw_excerpt")
                code = getattr(ev, "exit_code", None) if not isinstance(ev, dict) else ev.get("exit_code")
                if not src and not exc:
                    continue
                code_txt = f" [exit {html.escape(str(code))}]" if code is not None else ""
                blocks.append(f'<div><strong>{html.escape(str(src))}</strong>{code_txt}<pre>{html.escape(str(exc or ""))}</pre></div>')
            if not blocks:
                return ""
            return (f'<details class="evidence-box"><summary>{tr("report.show_evidence")} ({len(blocks)})</summary>'
                    f'{"".join(blocks)}</details>')

        def card(title_html: str, body: str, right: str = "") -> str:
            return f'<div class="card"><div class="card-header"><span>{title_html}</span>{right}</div>{body}</div>'

        serial = str(dev.get("serial") or "UNKNOWN")
        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")
        report_id = f"ADP-{int(time.time())}-{serial[:6].upper()}"
        if not output_path:
            target_dir = output_dir or REPORTS_DIR
            os.makedirs(target_dir, exist_ok=True)
            safe_serial = re.sub(r"[^A-Za-z0-9_.-]", "_", serial)
            output_path = os.path.join(target_dir, f"Diagnostic_Report_{safe_serial}_{int(time.time())}.html")

        pmic = bdiag.get("pmic", {})
        panics = bdiag.get("panic_analysis", {})
        blackbox = panics.get("blackbox", {})

        # ---------------- per-check status table ----------------
        # Key checks come from the CheckResult lists ("checks"); dicts saved before that list
        # existed fall back to check_statuses + the known detail fields.
        legacy_detail_by_id = {
            "battery.fuel_gauge": None,
            "thermal.zones": None,
            "board.boot_reason": blackbox.get("diagnosis"),
            "board.kernel_log": panics.get("summary_verdict"),
            "audio.subsystem": acodec.get("diagnosis"),
            "fingerprint.sensor": haudit.get("fingerprint", {}).get("diagnosis"),
            "buttons.keys": haudit.get("buttons", {}).get("diagnosis"),
            "telephony.sim_baseband": haudit.get("sim_baseband", {}).get("diagnosis"),
            "display.backlight": haudit.get("display_backlight", {}).get("diagnosis"),
            "wifi.adapter": haudit.get("wifi", {}).get("diagnosis"),
            "bluetooth.adapter": haudit.get("bluetooth", {}).get("diagnosis"),
        }

        def key_checks(source: Dict[str, Any]) -> List[tuple]:
            if source.get("checks"):
                return [(c.get("check_id"), c.get("status"), c.get("detail")) for c in source["checks"]]
            return [(cid, st, legacy_detail_by_id.get(cid)) for cid, st in source.get("check_statuses", {}).items()]

        # extra_checks: CheckResult dicts from the caller (e.g. CLI stages that failed -> UNKNOWN)
        key_rows = key_checks(bdiag) + key_checks(haudit) + [
            (c.get("check_id"), c.get("status"), c.get("detail")) for c in (extra_checks or [])]
        checks: List[tuple] = list(key_rows)  # (check_id, status, detail)
        extra = [
            ("storage.health", srep.get("status"), srep.get("diagnosis")),
            ("storage.jedec_life", slife.get("status"), slife.get("diagnosis")),
            ("battery.screen_off_current", leak.get("status"), leak.get("diagnosis")),
            ("board.cpu_stress", stress.get("status"), stress.get("diagnosis")),
            ("charging.input", fcharge.get("status"), fcharge.get("diagnosis")),
            ("display.panel_info", pinfo.get("status"), pinfo.get("diagnosis")),
            ("security.user_ca_certificates", sscan.get("user_ca_status"), sscan.get("user_ca_diagnosis")),
            ("board.root_forensics", rforensics.get("status"), rforensics.get("diagnosis")),
        ]
        checks += [c for c in extra if c[1]]
        info_only = {"security.user_ca_certificates"}
        overall = str(aggregate_status([st for cid, st, _ in checks if cid not in info_only]))

        rows = "".join(
            f"<tr><td>{tr(cid)}</td><td>{badge(st)}</td><td>{esc(diag) if diag else ''}</td></tr>"
            for cid, st, diag in checks)
        status_table = card(
            tr("report.checks_title"),
            (f'<table class="table"><thead><tr><th>{tr("report.col_check")}</th><th>{tr("report.col_status")}</th>'
             f'<th>{tr("report.col_details")}</th></tr></thead><tbody>{rows or ""}</tbody></table>'
             f'<div class="note">{tr("report.status_legend")}</div>'),
            right=f'{tr("report.overall")}: {badge(overall)}')

        # ---------------- device ----------------
        device_card = card(tr("report.device_title"), "".join([
            row("report.model", html.escape(f"{dev.get('brand', '')} {dev.get('model', '')}".strip() or "UNKNOWN")),
            row("report.serial", f"<code>{html.escape(serial)}</code>"),
            row("report.android", html.escape(f"Android {dev.get('android_version', 'UNKNOWN')} (API {dev.get('sdk_version', 'UNKNOWN')})")),
            row("report.security_patch", val(dev.get("security_patch"))),
            row("report.soc", val(dev.get("soc_chipset"))),
        ]))

        # ---------------- battery ----------------
        def reading(value, unit, source, status):
            # Not read: show why (NEEDS_ROOT / UNKNOWN); the "UNKNOWN" source placeholder is not a source.
            if value is None:
                return badge(status or "UNKNOWN")
            return f'{val(value, unit)} <span class="src">{ltr(html.escape("(" + str(source) + ")"))}</span>'

        health_pct = pmic.get("true_health_pct")
        cycles = pmic.get("cycle_count")
        battery_card = card(tr("battery.fuel_gauge"), "".join([
            row("report.battery_voltage", reading(pmic.get("voltage_mv"), "mV", pmic.get("voltage_source"), pmic.get("voltage_status"))),
            row("report.battery_current", reading(pmic.get("current_ma"), "mA", pmic.get("current_source"), pmic.get("current_status"))
                + (f'<br><span class="src">{esc(pmic.get("current_note"))}</span>' if pmic.get("current_note") else "")),
            row("report.battery_temp", reading(pmic.get("temp_c"), "°C", pmic.get("temp_source"), pmic.get("temp_status"))),
            row("report.capacity_vs_design", (val(health_pct, "%") + " " if health_pct is not None else "") + badge(pmic.get("health_verdict"))
                + (f'<br><span class="src">{tr("report.design_from_spec", name=pmic.get("model_spec_name") or "-")}</span>'
                   if pmic.get("design_capacity_source") == "model_db" else "")),
            row("report.cycle_count", val(cycles) if cycles is not None else badge(pmic.get("cycle_status") or "UNKNOWN")),
            row("report.reported_health", val(pmic.get("health"))),
            evidence(pmic.get("evidence")),
        ]), right=badge(bdiag.get("check_statuses", {}).get("battery.fuel_gauge",
                                pmic.get("check_status", pmic.get("voltage_status")))))

        findings = bdiag.get("critical_findings", [])
        findings_html = "".join(f'<div class="card" style="border-color:#ef4444">{tr("report.finding")}: {esc(f)}</div>' for f in findings)

        # ---------------- thermal ----------------
        zones = bdiag.get("thermal_zones", [])
        zone_rows = []
        for z in zones:
            limit = z.get("limit_c")
            if z.get("plausible") is False:
                zst = tr("report.zone_implausible")
            elif limit is None:
                zst = tr("report.zone_not_judged")
            elif z.get("is_overheated"):
                zst = tr("report.zone_above", limit=limit)
            else:
                zst = tr("report.zone_within", limit=limit)
            zone_rows.append(f"<tr><td>{html.escape(str(z.get('zone_type')))}</td><td>{html.escape(str(z.get('category', 'other')))}</td>"
                             f"<td>{val(z.get('temp_c'), '°C')}</td><td>{zst}</td></tr>")
        thermal_card = card(tr("thermal.zones"),
                            (f'<table class="table"><thead><tr><th>{tr("report.zone")}</th><th>{tr("report.category")}</th>'
                             f'<th>{tr("report.temperature")}</th><th>{tr("report.col_status")}</th></tr></thead>'
                             f'<tbody>{"".join(zone_rows) or "<tr><td colspan=4>" + tr("report.no_zones") + "</td></tr>"}</tbody></table>'
                             + evidence(bdiag.get("thermal_evidence"))),
                            right=badge(bdiag.get("thermal_status")))

        # ---------------- kernel log / boot reason ----------------
        kernel_card = card(tr("board.kernel_log"), "".join([
            row("report.boot_reason", val(blackbox.get("boot_reason"))),
            row("report.pstore", badge(blackbox.get("kernel_ramoops_status"))),
            row("report.dmesg", badge(panics.get("dmesg_status"))),
            row("report.storage_errors", str(len(panics.get("emmc_ufs_errors", [])))),
            row("report.kernel_panics", str(len(panics.get("kernel_panics", [])))),
            f'<div class="note">{esc(panics.get("summary_verdict"))}</div>' if panics.get("summary_verdict") else "",
            causes(blackbox.get("possible_causes")),
            evidence((panics.get("evidence") or []) + (blackbox.get("evidence") or [])),
        ]), right=badge(panics.get("status")))

        # ---------------- hardware ----------------
        sensors = haudit.get("sensors", [])
        cameras = haudit.get("cameras", [])
        sensor_list = "".join(f"<li>{html.escape(str(s.get('name')))} ({html.escape(str(s.get('sensor_type')))})</li>" for s in sensors[:12])
        camera_list = "".join(
            f"<li>{tr('report.camera_item', id=c.get('camera_id'), facing=t('camera.facing.' + str(c.get('facing', 'UNKNOWN')), lang))}</li>"
            for c in cameras)
        missing = haudit.get("missing_critical_sensors", [])
        hw_parts = [
            f'<div class="grid-2"><div><strong>{tr("sensors.reported")}</strong> {badge(haudit.get("sensors_status"))}'
            f'<ul style="margin:6px 18px">{sensor_list or "<li>" + tr("report.none_reported") + "</li>"}</ul>'
            + (f'<div class="note">{tr("report.sensors_not_reported", names=", ".join(missing))}</div>' if missing else "")
            + f'</div><div><strong>{tr("camera.reported")}</strong> {badge(haudit.get("cameras_status"))}'
            f'<ul style="margin:6px 18px">{camera_list or "<li>" + tr("report.none_reported") + "</li>"}</ul></div></div>'
        ]
        for key, cid in (("fingerprint", "fingerprint.sensor"), ("buttons", "buttons.keys"), ("sim_baseband", "telephony.sim_baseband"),
                         ("display_backlight", "display.backlight"), ("wifi", "wifi.adapter"), ("bluetooth", "bluetooth.adapter"),
                         ("peripherals", "system.declared_features")):
            item = haudit.get(key, {})
            if not item:
                continue
            hw_parts.append(f'<div class="prop-row"><span class="prop-name">{tr(cid)}</span><span class="prop-val">{badge(item.get("status"))}</span></div>'
                            f'<div class="note">{esc(item.get("diagnosis")) if item.get("diagnosis") else ""}</div>{causes(item.get("possible_causes"))}')
        if acodec:
            hw_parts.append(f'<div class="prop-row"><span class="prop-name">{tr("audio.subsystem")}</span><span class="prop-val">{badge(acodec.get("status"))}</span></div>'
                            f'<div class="note">{esc(acodec.get("diagnosis")) if acodec.get("diagnosis") else ""}</div>{causes(acodec.get("possible_causes"))}')
        if pinfo:
            hw_parts.append(f'<div class="prop-row"><span class="prop-name">{tr("display.panel_info")}</span><span class="prop-val">{badge(pinfo.get("status"))}</span></div>'
                            f'<div class="note">{esc(pinfo.get("diagnosis")) if pinfo.get("diagnosis") else ""} '
                            f'| verifiedbootstate: {val(pinfo.get("verified_boot_state"))}</div>')
        hw_parts.append(evidence(haudit.get("evidence")))
        hw_card = card(tr("report.hardware_title"), "".join(hw_parts), right=badge(haudit.get("status")) if haudit else "")

        # ---------------- storage / stress / charging ----------------
        storage_parts = []
        if srep or slife:
            src = srep or slife
            storage_parts += [
                row("report.storage_type", val(src.get("storage_type"))),
                row("report.wear_a", esc(src.get("life_time_type_a"))),
                row("report.pre_eol", esc(src.get("pre_eol_info", src.get("pre_eol_status")))),
                row("report.read_write_speed", f'{val(srep.get("read_speed_mb_s"), "MB/s")} / {val(srep.get("write_speed_mb_s"), "MB/s")}'),
                f'<div class="note">{esc(src.get("diagnosis"))}</div>' if src.get("diagnosis") else "",
                evidence(src.get("evidence")),
            ]
        if stress:
            storage_parts += [
                row("report.stress_voltage", f'{val(stress.get("initial_voltage_mv"), "mV")} -> {val(stress.get("min_voltage_mv"), "mV")} ({val(stress.get("voltage_sag_mv"), "mV")})'),
                row("report.stress_temp", f'{val(stress.get("initial_temp_c"), "°C")} -> {val(stress.get("max_temp_c"), "°C")} <span class="src">({html.escape(str(stress.get("temp_source")))})</span>'),
                f'<div class="note">{badge(stress.get("status"))} {esc(stress.get("diagnosis")) if stress.get("diagnosis") else ""}</div>',
            ]
        if fcharge:
            storage_parts += [
                row("report.charger", f'{val(fcharge.get("charger_type"))} | {esc(fcharge.get("protocol_name"))}'),
                row("report.charger_measured", f'{val(fcharge.get("voltage_v"), "V")} @ {val(fcharge.get("current_a"), "A")} = {val(fcharge.get("wattage_w"), "W")}'),
                evidence(fcharge.get("evidence")),
            ]
        if leak:
            storage_parts += [
                row("report.screen_off_current", f'{badge(leak.get("status"))} {val(leak.get("avg_ma"), "mA")} / {val(leak.get("max_ma"), "mA")}'),
                f'<div class="note">{esc(leak.get("diagnosis")) if leak.get("diagnosis") else ""}</div>{causes(leak.get("possible_causes"))}',
            ]
        storage_card = card(tr("report.storage_title"), "".join(storage_parts)) if storage_parts else ""

        # ---------------- security inventory ----------------
        sec_card = ""
        if sscan:
            apps = sscan.get("sensitive_apps", [])
            app_rows = "".join(
                f"<tr><td><code>{html.escape(str(a.get('package_name')))}</code></td>"
                f"<td>{html.escape(', '.join(render(l, lang) for l in a.get('permission_labels', [])))}</td>"
                f"<td>{html.escape(' '.join(render(n, lang) for n in a.get('review_notes', [])))}</td></tr>"
                for a in apps[:40])
            sec_card = card(tr("security.sensitive_permissions"), (
                row("report.packages", html.escape(f"{sscan.get('third_party_count', 0)} / {sscan.get('total_packages_scanned', 0)}"))
                + row("security.user_ca_certificates", badge(sscan.get("user_ca_status")))
                + f'<div class="note">{esc(sscan.get("summary")) if sscan.get("summary") else ""}</div>'
                + (f'<table class="table"><thead><tr><th>{tr("report.package")}</th><th>{tr("report.permissions")}</th>'
                   f'<th>{tr("report.notes")}</th></tr></thead><tbody>{app_rows}</tbody></table>' if app_rows else "")
                + evidence(sscan.get("evidence"))),
                right=f'<span class="badge" style="background:#6e7681">{tr("report.review_not_verdict")}</span>')

        # ---------------- maintenance ----------------
        maint_card = ""
        actions = oresult.get("actions", [])
        if actions:
            arows = "".join(
                f"<tr><td><code>{html.escape(str(a.get('command')))}</code></td><td>{badge(a.get('status'))}</td>"
                f"<td>{(val(a.get('before'), 'MB') + ' -> ' + val(a.get('after'), 'MB')) if a.get('measured') else tr('report.not_measurable')}</td>"
                f"<td>{esc(a.get('note')) if a.get('note') else ''}</td></tr>"
                for a in actions)
            maint_card = card(tr("report.maintenance_title"),
                              f'<table class="table"><thead><tr><th>{tr("report.command")}</th><th>{tr("report.col_status")}</th>'
                              f'<th>{tr("report.measured")}</th><th>{tr("report.notes")}</th></tr></thead><tbody>{arows}</tbody></table>')

        # ---------------- root forensics ----------------
        root_card = ""
        if rforensics:
            root_card = card(tr("board.root_forensics"), "".join([
                row("report.root_type", val(rforensics.get("root_type"))),
                row("report.fault_signatures", str(len(rforensics.get("matched_fault_signatures", [])))),
                row("report.i2c_errors", str(len(rforensics.get("i2c_bus_errors", [])))),
                f'<div class="note">{esc(rforensics.get("diagnosis")) if rforensics.get("diagnosis") else ""}</div>',
                evidence(rforensics.get("evidence")),
            ]), right=badge(rforensics.get("status")))

        # ---------------- screen originality indicators (never counted in the overall result) ----------------
        display_card = ""
        if display_check:
            effect_colors = {"supports": "#238636", "against": "#d29922", "info": "#6e7681"}
            irows = "".join(
                f'<tr><td><span class="badge" style="background:{effect_colors.get(i.get("effect"), "#6e7681")}">'
                f'{tr("display.effect." + str(i.get("effect", "info")))}</span></td><td>{esc(i.get("text"))}</td></tr>'
                for i in display_check.get("indicators", []))
            display_card = card(tr("display.originality_indicators"), "".join([
                f'<div class="note"><strong>{esc(display_check.get("diagnosis"))}</strong></div>',
                f'<table class="table"><tbody>{irows}</tbody></table>' if irows else "",
                f'<div class="note">{tr("display.not_proof")}</div>',
                evidence(display_check.get("evidence")),
            ]))

        # ---------------- parts originality indicators (never counted in the overall result) ----------------
        parts_card = ""
        if parts_check:
            effect_colors = {"supports": "#238636", "against": "#d29922", "info": "#6e7681"}
            prows = "".join(
                f'<tr><td><span class="badge" style="background:{effect_colors.get(i.get("effect"), "#6e7681")}">'
                f'{tr("display.effect." + str(i.get("effect", "info")))}</span></td><td>{esc(i.get("text"))}</td></tr>'
                for i in parts_check.get("indicators", []))
            parts_card = card(tr("parts.originality_indicators"), "".join([
                f'<div class="note"><strong>{esc(parts_check.get("diagnosis"))}</strong></div>',
                f'<table class="table"><tbody>{prows}</tbody></table>' if prows else "",
                f'<div class="note">{tr("parts.not_proof")}</div>',
                evidence(parts_check.get("evidence")),
            ]))

        # ---------------- guided (layered) diagnosis: narrows a found fault; never counted in the overall -------
        layered_card = ""
        shown = [r for r in (layered or []) if r.get("status") in ("CONCLUDED", "NEEDS_MANUAL", "INSUFFICIENT")]
        if shown:
            blocks = []
            for r in shown:
                trail = "".join(f"<li>{esc(x)}</li>" for x in r.get("trail", []))
                head = f'<div class="note"><strong>{tr("layered.area." + r["area"])}</strong></div>'
                body = f"<ul>{trail}</ul>" if trail else ""
                if r["status"] == "CONCLUDED":
                    body += f'<div class="note">&rArr; {esc(r.get("conclusion"))} ({tr("cli.layered_confidence." + r.get("confidence", "medium"))})</div>'
                elif r["status"] == "NEEDS_MANUAL" and r.get("manual_step"):
                    opts = "".join(f"<li>{esc(o.get("label"))}</li>" for o in r["manual_step"].get("options", []))
                    body += (f'<div class="note">&raquo; {tr("cli.layered_manual")}: {esc(r["manual_step"].get("instruction"))}</div>'
                             f"<ul>{opts}</ul>")
                else:
                    body += f'<div class="note">{esc(r.get("diagnosis"))}</div>'
                blocks.append(head + body)
            layered_card = card(tr("diagnosis.layered"), "".join(blocks))

        # ---------------- offline log analysis (the file may be from another phone: not counted) ----------------
        log_card = ""
        if log_analysis:
            mrows = "".join(
                f'<tr><td>{badge(m.get("severity"))}</td><td><code>{html.escape(str(m.get("rule_id")))}</code></td>'
                f'<td>{ltr(html.escape(str(m.get("count"))))}</td><td>{ltr(html.escape(str(m.get("line_no"))))}</td>'
                f'<td>{html.escape(str((m.get("description_ar") if lang == "ar" else m.get("description_en")) or m.get("description_en", "")))}'
                f'<br><span class="src">{ltr(html.escape(str(m.get("line", ""))))}</span></td></tr>'
                for m in log_analysis.get("matches", []))
            log_parts = [
                row("report.log_source", ltr(html.escape(str(log_analysis.get("source", "-"))))),
                row("report.log_lines", val(log_analysis.get("lines"))),
                row("report.log_soc", val(log_analysis.get("soc"))),
            ]
            if log_analysis.get("fatal_line"):
                log_parts.append(row("report.log_fatal", ltr(html.escape(
                    f'{log_analysis.get("fatal_line_no")}: {log_analysis.get("fatal_line")}'))))
            if mrows:
                log_parts.append(
                    f'<table class="table"><thead><tr><th>{tr("report.log_col_severity")}</th><th>{tr("report.log_col_rule")}</th>'
                    f'<th>{tr("report.log_col_count")}</th><th>{tr("report.log_col_line")}</th><th>{tr("report.log_col_text")}</th>'
                    f'</tr></thead><tbody>{mrows}</tbody></table>')
            areas = log_analysis.get("board_areas") or []
            if areas:
                log_parts.append(f'<div class="note">{html.escape(t("cli.logscan_areas", lang, areas=", ".join(t(f"board_area.{a}", lang) for a in areas)))}</div>')
            log_parts += [
                causes((lang == "ar" and log_analysis.get("possible_causes_ar")) or log_analysis.get("possible_causes")),
                f'<div class="note">{esc(log_analysis.get("diagnosis"))}</div>',
                f'<div class="note">{tr("report.log_not_counted")}</div>',
                evidence(log_analysis.get("evidence")),
            ]
            log_card = card(tr("log.offline_analysis"), "".join(log_parts), right=badge(log_analysis.get("status")))

        # ---------------- ticket ----------------
        ticket_card = ""
        if ticket_id or customer_name or reported_fault or repair_cost > 0:
            ticket_card = card(tr("report.ticket_title"), "".join([
                row("report.ticket_id", val(ticket_id or None)),
                row("report.customer_name", val(customer_name or None)),
                row("report.customer_phone", val(customer_phone or None)),
                row("report.reported_fault", val(reported_fault or None)),
                row("report.estimated_cost", html.escape(f"${repair_cost:.2f}") if repair_cost > 0 else tr("report.cost_pending")),
            ]))

        if signing_key is not None:
            from core.report_signing import key_id_from_public
            signature_note = tr("report.signed", key_id=key_id_from_public(signing_key.public_key()))
        else:
            signature_note = tr("report.not_signed")
        if signing_key is not None:
            # Signed summary: verifiable from the printed QR alone (core/report_signing.py)
            from core.report_signing import VERIFY_URL_ENV, make_signed_summary
            model_text = f"{dev.get('brand', '')} {dev.get('model', '')}".strip()
            qr_text = make_signed_summary(signing_key, {"ref": report_id, "serial": serial, "model": model_text,
                                                        "status": overall, "date": timestamp_str})
            verify_url = os.environ.get(VERIFY_URL_ENV, "").strip()
            if verify_url.startswith("https://"):
                qr_text = f"{verify_url.split('#', 1)[0]}#{qr_text}"
            qr = _qr_svg(qr_text, size_px=128)
        else:
            qr = _qr_svg({"ref": report_id, "sn": serial, "status": overall, "date": timestamp_str, "ticket": ticket_id or None})
        from ui.i18n import is_rtl
        direction = "rtl" if is_rtl(lang) else "ltr"
        page = f"""<!DOCTYPE html>
<html lang="{html.escape(lang)}" dir="{direction}">
<head><meta charset="UTF-8"><title>{tr("report.doc_title", serial=serial)}</title><style>{CSS}</style></head>
<body><div class="container">
<div class="header">
  <div><h1>{tr("report.title")}</h1><p>{tr("report.subtitle")}</p></div>
  <div style="display:flex;gap:15px;align-items:center;">{f'<div class="qr-box">{qr}</div>' if qr else ''}
    <div><span class="ref">REF: {html.escape(report_id)}</span><br><small>{tr("report.date")}: {html.escape(timestamp_str)}</small></div></div>
</div>
<div class="no-print" style="margin-bottom:20px;"><button class="btn-print" onclick="window.print()">{tr("report.print")}</button></div>
{ticket_card}
{status_table}
{findings_html}
<div class="grid-2">{device_card}{battery_card}</div>
<div class="grid-2">{thermal_card}{kernel_card}</div>
{hw_card}
{storage_card}
{sec_card}
{maint_card}
{root_card}
{display_card}
{parts_card}
{layered_card}
{log_card}
<div class="footer">
  <div>{tr("report.technician")}: <strong>{html.escape(technician_name or "-")}</strong></div>
  <div>{signature_note} <strong>{html.escape(report_id)}</strong></div>
</div>
</div></body></html>
"""
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(page)
        logger.info(f"Diagnostic report generated at: {output_path}")
        if signing_key is not None:
            from core.report_signing import sign_file
            logger.info(f"Report signature written to: {sign_file(output_path, signing_key)}")
        return output_path
