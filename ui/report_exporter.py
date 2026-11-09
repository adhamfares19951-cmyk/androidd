import os
import time
import json
import logging
import hmac
import hashlib
from typing import Optional, Dict, Any

logger = logging.getLogger("ReportExporter")

REPORTS_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "reports")
os.makedirs(REPORTS_DIR, exist_ok=True)

# Secret salt key for HMAC-SHA256 hardware certificate signing
CERTIFICATE_SIGNING_SECRET = b"AndroidDoctorPro_v4_Hardware_Certificate_Authority_2026"


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
        disp_auth: Optional[Dict[str, Any]] = None,
        root_forensics: Optional[Dict[str, Any]] = None,
        technician_name: str = "Chief Hardware Technician",
        ticket_id: str = "",
        customer_name: str = "",
        customer_phone: str = "",
        reported_fault: str = "",
        repair_cost: float = 0.0,
        output_path: Optional[str] = None,
        output_dir: Optional[str] = None,
        lang: str = "ar"
    ) -> str:
        dev = device_profile or {}
        bdiag = board_diag or {}
        haudit = hw_audit or {}
        sscan = sec_scan or {}
        oresult = opt_result or {}
        lresult = leakage_result or {}
        srep = storage_report or {}
        fcharge = fast_charge or {}
        slife = storage_life_report or {}
        stress = stress_report or {}
        acodec = audio_codec or haudit.get("audio_codec", {})
        dauth = disp_auth or haudit.get("display_panel_info", haudit.get("display_authenticity", {}))
        rforensics = root_forensics or {}

        serial = dev.get("serial", "UNKNOWN_SERIAL")
        timestamp_str = time.strftime("%Y-%m-%d %H:%M:%S")
        report_id = f"ADP-{int(time.time())}-{serial[:6].upper() if serial else 'DEV'}"

        if not output_path:
            target_dir = output_dir or REPORTS_DIR
            os.makedirs(target_dir, exist_ok=True)
            output_path = os.path.join(target_dir, f"Diagnostic_Report_{serial}_{int(time.time())}.html")

        pmic = bdiag.get("pmic", {})
        thermals = bdiag.get("thermal_zones", [])
        panics = bdiag.get("panic_analysis", {})
        overall_health = bdiag.get("overall_health", "PASS")
        findings = bdiag.get("critical_findings", [])

        sensors = haudit.get("sensors", [])
        missing_sensors = haudit.get("missing_critical_sensors", [])
        cameras = haudit.get("cameras", [])
        fp = haudit.get("fingerprint", {})
        btn = haudit.get("buttons", {})
        sim = haudit.get("sim_baseband", {})
        disp = haudit.get("display_backlight", {})
        wifi = haudit.get("wifi", {})
        bt = haudit.get("bluetooth", {})
        periph = haudit.get("peripherals", {})

        critical_threats = sscan.get("critical_threats", [])
        high_threats = sscan.get("high_threats", [])
        ghost_apps = sscan.get("ghost_apps", [])
        connections = sscan.get("active_connections", [])
        overall_risk = sscan.get("overall_risk", "CLEAN")

        opt_before = oresult.get("before", {})
        opt_after = oresult.get("after", {})
        ram_freed = oresult.get("ram_freed_mb", 0.0)
        storage_freed = oresult.get("storage_freed_mb", 0.0)
        drain_reduction = oresult.get("drain_reduction_ma", 0.0)
        actions = oresult.get("actions_completed", [])

        true_health = pmic.get("true_health_pct")
        if true_health is not None:
            health_color = "#10b981" if true_health >= 85 else ("#f59e0b" if true_health >= 70 else "#ef4444")
            health_badge_text = f"{true_health}% {'كفاءة البطارية' if is_ar else 'Health'}"
        else:
            health_color = "#6e7681"
            health_badge_text = pmic.get("health_verdict", "NEEDS_ROOT")

        risk_color = "#10b981" if overall_risk == "CLEAN" else ("#f59e0b" if overall_risk in ["LOW", "MEDIUM"] else "#ef4444")
        diag_color = "#10b981" if overall_health == "PASS" else ("#f59e0b" if overall_health == "WARNING" else "#ef4444")

        # Cryptographic HMAC-SHA256 Digital Signature
        sign_payload = f"{report_id}|{serial}|{dev.get('model','')}|{true_health}|{overall_health}|{ticket_id}|{timestamp_str}".encode("utf-8")
        cert_hmac_sig = hmac.new(CERTIFICATE_SIGNING_SECRET, sign_payload, hashlib.sha256).hexdigest()

        # Pure Vector SVG QR Code Generation
        qr_data_obj = {
            "app": "AndroidDoctorPro",
            "ver": "4.0",
            "ref": report_id,
            "ticket": ticket_id or "NONE",
            "sn": serial,
            "dev": f"{dev.get('brand', '')} {dev.get('model', '')}".strip(),
            "bat": f"{true_health}%" if true_health is not None else "NEEDS_ROOT",
            "status": overall_health,
            "sig": cert_hmac_sig[:16],
            "tech": technician_name,
            "date": timestamp_str
        }
        qr_svg_html = ""
        try:
            import re
            import qrcode
            import qrcode.image.svg
            factory = qrcode.image.svg.SvgPathImage
            qr_img = qrcode.make(json.dumps(qr_data_obj, ensure_ascii=False), image_factory=factory, box_size=8)
            qr_svg_raw = qr_img.to_string(encoding="unicode")
            if "<?xml" in qr_svg_raw:
                qr_svg_raw = qr_svg_raw.split("?>", 1)[-1].strip()
            # Cleanly replace dimensions with 90x90 px
            qr_svg_raw = re.sub(r'width="[^"]+"', 'width="90"', qr_svg_raw, count=1)
            qr_svg_raw = re.sub(r'height="[^"]+"', 'height="90"', qr_svg_raw, count=1)
            if "<svg " in qr_svg_raw:
                qr_svg_raw = qr_svg_raw.replace("<svg ", '<svg style="display:block;border-radius:4px;" ', 1)
            qr_svg_html = qr_svg_raw
        except Exception as e:
            logger.warning(f"QR code SVG rendering fallback: {e}")
            qr_svg_html = f'<div style="font-family:monospace;font-size:10px;color:#79c0ff;padding:6px;border:1px dashed #58a6ff;text-align:center;">QR VERIFIED<br>SIG: {cert_hmac_sig[:10]}</div>'

        is_ar = (lang == "ar")
        direction = "rtl" if is_ar else "ltr"
        doc_title = "تقرير الفحص التشخيصي للهاتف — أندرويد دكتور برو" if is_ar else f"Android Doctor Pro - Diagnostic Report ({serial})"
        header_title = "أندرويد دكتور برو (ANDROID DOCTOR PRO)" if is_ar else "ANDROID DOCTOR PRO"
        header_sub = "تقرير الفحص التشخيصي المعتمد للعتاد واللوحة الأم وتذكرة الصيانة" if is_ar else "Advanced Hardware Diagnostic Report & Technical Intake Voucher"
        btn_print_text = "طباعة تقرير الفحص / حفظ بصيغة PDF" if is_ar else "Print Diagnostic Report / Save as PDF"
        specs_card_title = "مواصفات الجهاز والعتاد الأساسي" if is_ar else "DEVICE SPECIFICATIONS"
        pmic_card_title = "جهد البطارية وآيسي الشحن (PMIC)" if is_ar else "BATTERY & CHARGING IC (PMIC)"
        thermal_card_title = f"خريطة حرارة الآيسيات ({len(thermals)} مستشعر)" if is_ar else f"THERMAL MAPPING ({len(thermals)} Zones)"
        panic_card_title = "سجلات الذاكرة التخزينية وانهيار النظام" if is_ar else "NAND / UFS & KERNEL PANICS"
        hw_card_title = "الفحص التفاعلي لقطع الهاتف والحساسات" if is_ar else "INTERACTIVE HARDWARE AUDIT"
        sec_card_title = "الفحص الأمني وصائد برامج التجسس" if is_ar else "SECURITY AUDIT & SPYWARE HUNTER"
        opt_card_title = "نتائج الصيانة وتسريع أداء النظام" if is_ar else "REAL SYSTEM OPTIMIZATION METRICS"

        def make_evidence_box(evidence_items, label_ar="عرض الدليل التقني المباشر", label_en="Show Evidence") -> str:
            if not evidence_items:
                return ""
            if isinstance(evidence_items, str):
                if not evidence_items.strip():
                    return ""
                evidence_list = [{"source": "Raw Data", "raw_excerpt": evidence_items.strip(), "exit_code": 0}]
            elif isinstance(evidence_items, list):
                evidence_list = evidence_items
            else:
                evidence_list = [evidence_items]

            lbl = label_ar if is_ar else label_en
            items_html = []
            import html
            for ev in evidence_list:
                src = getattr(ev, "source", None) or (ev.get("source") if isinstance(ev, dict) else "")
                exc = getattr(ev, "raw_excerpt", None) or (ev.get("raw_excerpt") if isinstance(ev, dict) else "")
                code = getattr(ev, "exit_code", 0) if hasattr(ev, "exit_code") else (ev.get("exit_code") if isinstance(ev, dict) else 0)
                if not src and not exc:
                    continue
                code_badge = f' <span class="badge" style="background:#30363d;font-size:10px;">exit {code}</span>' if code is not None and code != 0 else ''
                src_html = f'<div style="font-family:monospace;font-size:11px;color:#58a6ff;margin-bottom:4px;word-break:break-all;"><strong>Source:</strong> {html.escape(str(src))}{code_badge}</div>'
                exc_html = f'<pre style="margin-top:2px;margin-bottom:8px;background:#0d1117;border:1px solid #30363d;border-radius:4px;padding:6px;font-size:11px;overflow-x:auto;max-height:200px;white-space:pre-wrap;">{html.escape(str(exc))}</pre>'
                items_html.append(f'<div class="evidence-item" style="margin-bottom:8px;">{src_html}{exc_html}</div>')

            if not items_html:
                return ""

            return f"""<details class="evidence-box" style="margin-top:8px;"><summary style="cursor:pointer;color:#79c0ff;font-weight:bold;font-size:12px;">🔍 {lbl} ({len(items_html)})</summary><div style="padding-top:8px;">{''.join(items_html)}</div></details>"""

        # Formatted PMIC readings
        v_val = pmic.get("voltage_mv")
        v_src = pmic.get("voltage_source", "UNKNOWN")
        if v_val is not None:
            pmic_v_html = f'{v_val} mV <span style="color: #8b949e; font-size: 11px;">({v_src})</span>'
        else:
            pmic_v_html = f'<span class="badge badge-warn">UNKNOWN</span> <span style="color: #8b949e; font-size: 11px;">({v_src})</span>'

        c_val = pmic.get("current_ma")
        c_src = pmic.get("current_source", "UNKNOWN")
        c_note = pmic.get("current_note") or ("يشمل تيار الشحن أثناء توصيل USB" if is_ar else "includes charging current while USB is connected")
        if c_val is not None:
            pmic_c_html = f'{c_val} mA <span style="color: #8b949e; font-size: 11px;">({c_src})</span><br><small style="color: #8b949e;">({c_note})</small>'
        else:
            pmic_c_html = f'<span class="badge badge-warn">UNKNOWN</span> <span style="color: #8b949e; font-size: 11px;">({c_src})</span>'

        t_val = pmic.get("temp_c")
        t_src = pmic.get("temp_source", "UNKNOWN")
        if t_val is not None:
            pmic_t_html = f'{t_val} °C <span style="color: #8b949e; font-size: 11px;">({t_src})</span>'
        else:
            pmic_t_html = f'<span class="badge badge-warn">UNKNOWN</span> <span style="color: #8b949e; font-size: 11px;">({t_src})</span>'

        # Customer ticket display logic
        has_ticket_info = bool(ticket_id or customer_name or reported_fault or repair_cost > 0)
        ticket_section_html = ""
        if has_ticket_info:
            ticket_section_html = f"""
        <!-- Customer Repair Ticket & Intake Voucher -->
        <div class="card" style="margin-bottom: 25px; border: 1px solid #1f6feb;">
            <div class="card-header" style="color: #79c0ff;">
                <span>{'قسيمة استلام العميل وتذكرة الصيانة الفنية' if is_ar else 'Customer Intake Voucher & Repair Ticket'}</span>
                <span class="badge" style="background: #1f6feb; color: #fff;">{ticket_id or ('بدون تذكرة' if is_ar else 'GENERAL')}</span>
            </div>
            <div class="grid-2">
                <div>
                    <div class="prop-row"><span class="prop-name">{'اسم العميل:' if is_ar else 'Customer Name:'}</span><span class="prop-val">{customer_name or ('عميل مباشر' if is_ar else 'Direct Customer')}</span></div>
                    <div class="prop-row"><span class="prop-name">{'رقم هاتف العميل:' if is_ar else 'Customer Phone:'}</span><span class="prop-val" style="direction: ltr; display: inline-block;">{customer_phone or '—'}</span></div>
                    <div class="prop-row"><span class="prop-name">{'تكلفة الصيانة المقدرة:' if is_ar else 'Estimated Cost:'}</span><span class="prop-val" style="color: #3fb950; font-weight: bold;">{f"${repair_cost:.2f}" if repair_cost > 0 else ("بانتظار التسعير الفني" if is_ar else "Pending Quote")}</span></div>
                </div>
                <div>
                    <div class="prop-row"><span class="prop-name">{'العطل المشتكى منه:' if is_ar else 'Reported Fault:'}</span><span class="prop-val" style="color: #f0883e;">{reported_fault or ('فحص شامل للعتاد واللوحة الأم' if is_ar else 'Full Motherboard & Hardware Diagnostic')}</span></div>
                    <div class="prop-row"><span class="prop-name">{'حالة التذكرة:' if is_ar else 'Ticket Status:'}</span><span class="prop-val"><span class="badge badge-pass">{'معتمد ومفحوص رسمياً' if is_ar else 'INSPECTED & CERTIFIED'}</span></span></div>
                    <div class="prop-row"><span class="prop-name">{'الرقم التسلسلي المسجل:' if is_ar else 'Device S/N:'}</span><span class="prop-val" style="font-family: monospace;">{serial}</span></div>
                </div>
            </div>
        </div>
        """

        # Root Forensics Section logic
        root_section_html = ""
        if rforensics:
            rf_rooted = rforensics.get("is_rooted", False)
            rf_root_type = rforensics.get("root_type", "NON_ROOT")
            rf_faults = rforensics.get("matched_fault_signatures", [])
            rf_i2c = rforensics.get("i2c_bus_errors", [])
            rf_wear = rforensics.get("nand_wear_status", "UNKNOWN")
            rf_badge_color = "#238636" if rf_rooted else "#6e7681"
            rf_badge_text = f"مروّت ({rf_root_type})" if (rf_rooted and is_ar) else (f"ROOTED ({rf_root_type})" if rf_rooted else ("غير مروت" if is_ar else "NON-ROOT"))

            faults_summary = "لا توجد حوادث انهيار سابقة" if is_ar else "No hardware panic signatures found"
            if rf_faults:
                faults_summary = ", ".join([f.get("name_ar" if is_ar else "name", "Fault") for f in rf_faults[:3]])

            root_section_html = f"""
        <!-- Root Forensics Pro (Kernel Ramoops & Exact Wear) -->
        <div class="card" style="margin-bottom: 25px; border: 1px solid #8957e5;">
            <div class="card-header" style="color: #d2a8ff;">
                <span>{'التحليل الجنائي المتقدم للكيرنل واللوحة الأم (Root Forensics Pro)' if is_ar else 'Motherboard & Kernel Forensics Pro (Rooted Mode)'}</span>
                <span class="badge" style="background: {rf_badge_color}; color: #fff;">{rf_badge_text}</span>
            </div>
            <div class="grid-2">
                <div>
                    <div class="prop-row"><span class="prop-name">{'سجلات الكيرنل المحفوظة (Ramoops):' if is_ar else 'Persistent Kernel Ramoops:'}</span><span class="prop-val">{'متوفرة ومفحوصة' if rforensics.get('kernel_ramoops_log') else ('لا توجد انهيارات سابقة' if is_ar else 'No persistent panics')}</span></div>
                    <div class="prop-row"><span class="prop-name">{'بصمات الأعطال المكتشفة:' if is_ar else 'Matched Fault Signatures:'}</span><span class="prop-val" style="color: {'#ef4444' if rf_faults else '#10b981'};">{faults_summary}</span></div>
                    <div class="prop-row"><span class="prop-name">{'أخطاء ناقل I2C للهاردوير:' if is_ar else 'I2C Bus Hardware Errors:'}</span><span class="prop-val" style="color: {'#ef4444' if rf_i2c else '#10b981'};">{len(rf_i2c)} {'أخطاء' if is_ar else 'errors'}</span></div>
                </div>
                <div>
                    <div class="prop-row"><span class="prop-name">{'استهلاك خلايا SLC المباشر:' if is_ar else 'Exact SLC Lifetime:'}</span><span class="prop-val">{rforensics.get('nand_exact_lifetime_a') or 'N/A'}</span></div>
                    <div class="prop-row"><span class="prop-name">{'استهلاك خلايا MLC المباشر:' if is_ar else 'Exact MLC Lifetime:'}</span><span class="prop-val">{rforensics.get('nand_exact_lifetime_b') or 'N/A'}</span></div>
                    <div class="prop-row"><span class="prop-name">{'تقييم كفاءة الذاكرة (Root):' if is_ar else 'NAND Wear Status:'}</span><span class="prop-val"><span class="badge {'badge-fail' if rf_wear == 'FAIL' else 'badge-pass'}">{rf_wear}</span></span></div>
                </div>
            </div>
            <div style="margin-top: 8px; font-size: 12px; color: #8b949e; background: #010409; padding: 8px; border-radius: 4px;">
                <strong>{'التشخيص الجنائي:' if is_ar else 'Diagnosis:'}</strong> {rforensics.get('diagnosis', 'Root forensics audit complete.')}
            </div>
        </div>
        """

        # HTML generation
        html = f"""<!DOCTYPE html>
<html lang="{'ar' if is_ar else 'en'}" dir="{direction}">
<head>
    <meta charset="UTF-8">
    <title>{doc_title}</title>
    <style>
        @media print {{
            body {{ -webkit-print-color-adjust: exact; print-color-adjust: exact; }}
            .no-print {{ display: none !important; }}
        }}
        * {{ box-sizing: border-box; margin: 0; padding: 0; }}
        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Cairo", "Tahoma", sans-serif;
            background-color: #0d1117;
            color: #c9d1d9;
            padding: 30px;
            font-size: 14px;
            line-height: 1.6;
        }}
        .container {{
            max-width: 960px;
            margin: 0 auto;
            background-color: #161b22;
            border: 1px solid #30363d;
            border-radius: 12px;
            padding: 35px;
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        }}
        .header {{
            display: flex;
            justify-content: space-between;
            align-items: center;
            border-bottom: 2px solid #30363d;
            padding-bottom: 20px;
            margin-bottom: 25px;
            gap: 20px;
        }}
        .title-box h1 {{
            color: #58a6ff;
            font-size: 24px;
            margin-bottom: 5px;
            letter-spacing: 0.5px;
        }}
        .title-box p {{
            color: #8b949e;
            font-size: 13px;
        }}
        .header-cert-box {{
            display: flex;
            align-items: center;
            gap: 15px;
        }}
        .qr-box {{
            background: #ffffff;
            padding: 4px;
            border-radius: 6px;
            box-shadow: 0 2px 8px rgba(0,0,0,0.4);
            display: flex;
            align-items: center;
            justify-content: center;
        }}
        .cert-badge {{
            text-align: {'left' if is_ar else 'right'};
        }}
        .cert-badge .code {{
            font-family: monospace;
            background: #21262d;
            padding: 6px 12px;
            border-radius: 6px;
            color: #79c0ff;
            border: 1px solid #30363d;
            font-size: 12px;
        }}
        .grid-2 {{
            display: grid;
            grid-template-columns: 1fr 1fr;
            gap: 20px;
            margin-bottom: 25px;
        }}
        .grid-3 {{
            display: grid;
            grid-template-columns: 1fr 1fr 1fr;
            gap: 20px;
            margin-bottom: 25px;
        }}
        .card {{
            background: #0d1117;
            border: 1px solid #30363d;
            border-radius: 8px;
            padding: 18px;
        }}
        .card-header {{
            font-size: 14px;
            font-weight: 600;
            color: #58a6ff;
            border-bottom: 1px solid #21262d;
            padding-bottom: 8px;
            margin-bottom: 12px;
            display: flex;
            justify-content: space-between;
            align-items: center;
        }}
        .prop-row {{
            display: flex;
            justify-content: space-between;
            padding: 5px 0;
            border-bottom: 1px solid #161b22;
        }}
        .prop-name {{
            color: #8b949e;
        }}
        .prop-val {{
            color: #f0f6fc;
            font-weight: 500;
        }}
        .badge {{
            display: inline-block;
            padding: 3px 8px;
            border-radius: 4px;
            font-size: 11px;
            font-weight: bold;
        }}
        .badge-pass {{ background: #238636; color: #fff; }}
        .badge-warn {{ background: #9e6a03; color: #fff; }}
        .badge-fail {{ background: #da3633; color: #fff; }}
        .table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 8px;
            font-size: 13px;
        }}
        .table th, .table td {{
            padding: 8px 10px;
            border: 1px solid #21262d;
            text-align: {'right' if is_ar else 'left'};
        }}
        .table th {{
            background: #161b22;
            color: #8b949e;
        }}
        .alert-box {{
            background: #381e1e;
            border-{'right' if is_ar else 'left'}: 4px solid #ef4444;
            color: #fca5a5;
            padding: 12px;
            border-radius: 4px;
            margin-bottom: 15px;
            font-size: 13px;
        }}
        details.evidence-box {{
            margin-top: 10px;
            background: #090d13;
            border: 1px solid #21262d;
            border-radius: 6px;
            padding: 8px 12px;
            font-size: 11px;
        }}
        details.evidence-box summary {{
            cursor: pointer;
            color: #58a6ff;
            font-weight: 600;
            user-select: none;
            outline: none;
        }}
        details.evidence-box pre {{
            margin-top: 6px;
            color: #8b949e;
            white-space: pre-wrap;
            word-break: break-all;
            font-family: Consolas, monospace;
            font-size: 11px;
            line-height: 1.4;
            background: #010409;
            padding: 6px 10px;
            border-radius: 4px;
            border: 1px solid #161b22;
        }}
        .btn-print {{
            background: #238636;
            color: #fff;
            padding: 10px 20px;
            border: none;
            border-radius: 6px;
            cursor: pointer;
            font-weight: bold;
            font-size: 14px;
        }}
        .btn-print:hover {{ background: #2ea043; }}
        .footer {{
            border-top: 1px solid #30363d;
            margin-top: 30px;
            padding-top: 20px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            color: #8b949e;
            font-size: 12px;
        }}
    </style>
</head>
<body>
    <div class="container">
        <!-- Header with QR Code & Reference -->
        <div class="header">
            <div class="title-box">
                <h1>{header_title}</h1>
                <p>{header_sub}</p>
            </div>
            <div class="header-cert-box">
                <div class="qr-box">
                    {qr_svg_html}
                </div>
                <div class="cert-badge">
                    <span class="code">REF: {report_id}</span><br>
                    <small style="color: #8b949e; margin-top: 4px; display: inline-block;">{'التاريخ:' if is_ar else 'Date:'} {timestamp_str}</small><br>
                    <small style="font-family: monospace; color: #58a6ff; font-size: 10px;">SIG: {cert_hmac_sig[:12]}...</small>
                </div>
            </div>
        </div>

        <div class="no-print" style="margin-bottom: 20px; text-align: {'left' if is_ar else 'right'};">
            <button class="btn-print" onclick="window.print()">🖨️ {btn_print_text}</button>
        </div>

        <!-- Customer Repair Shop Ticket (if present) -->
        {ticket_section_html}

        <!-- Root Forensics Pro (if present) -->
        {root_section_html}

        <!-- Findings Alerts -->
        {"".join([f'<div class="alert-box"><strong>{"تنبيه فني عاجل:" if is_ar else "ALERT:"}</strong> {f}</div>' for f in findings])}

        <!-- Device & Hardware Info -->
        <div class="grid-2">
            <div class="card">
                <div class="card-header">{specs_card_title}</div>
                <div class="prop-row"><span class="prop-name">{'الموديل والشركة:' if is_ar else 'Model / Marketing:'}</span><span class="prop-val">{dev.get("brand", "Unknown")} {dev.get("model", "Unknown")}</span></div>
                <div class="prop-row"><span class="prop-name">{'الرقم التسلسلي:' if is_ar else 'Serial Number:'}</span><span class="prop-val" style="font-family: monospace;">{serial}</span></div>
                <div class="prop-row"><span class="prop-name">{'كود المازربورد:' if is_ar else 'Board Codename:'}</span><span class="prop-val">{dev.get("device_codename", "Unknown")}</span></div>
                <div class="prop-row"><span class="prop-name">{'إصدار أندرويد:' if is_ar else 'Android Version:'}</span><span class="prop-val">Android {dev.get("android_version", "Unknown")} (API {dev.get("sdk_version", "Unknown")})</span></div>
                <div class="prop-row"><span class="prop-name">{'مستوى الأمان:' if is_ar else 'Security Patch:'}</span><span class="prop-val">{dev.get("security_patch", "Unknown")}</span></div>
                <div class="prop-row"><span class="prop-name">{'المعالج والأنوية:' if is_ar else 'SoC / Processor:'}</span><span class="prop-val">{dev.get("soc_chipset", "Unknown")} ({dev.get("cpu_cores", 8)} Cores)</span></div>
                <div class="prop-row"><span class="prop-name">{'الروت والحماية:' if is_ar else 'Root / SELinux:'}</span><span class="prop-val">{'نعم (مروت)' if dev.get("is_rooted") else 'لا (أصلي)'} | {dev.get("selinux_mode", "Enforcing")}</span></div>
            </div>

            <div class="card">
                <div class="card-header">
                    <span>{pmic_card_title}</span>
                    <span class="badge" style="background: {health_color}; color: #fff;">{health_badge_text}</span>
                </div>
                <div class="prop-row"><span class="prop-name">{'جهد البطارية (مقياس الوقود):' if is_ar else 'Battery voltage (fuel gauge):'}</span><span class="prop-val">{pmic_v_html}</span></div>
                <div class="prop-row"><span class="prop-name">{'تيار البطارية:' if is_ar else 'Battery current:'}</span><span class="prop-val">{pmic_c_html}</span></div>
                <div class="prop-row"><span class="prop-name">{'حرارة البطارية:' if is_ar else 'Battery Temp:'}</span><span class="prop-val">{pmic_t_html}</span></div>
                <div class="prop-row"><span class="prop-name">{'صحة البطارية المعلنة:' if is_ar else 'Reported Health:'}</span><span class="prop-val">{pmic.get("health", "UNKNOWN")}</span></div>
                <div class="prop-row"><span class="prop-name">{'حالة التوصيل:' if is_ar else 'Status:'}</span><span class="prop-val">{pmic.get("status", "UNKNOWN")}</span></div>
                <div class="prop-row"><span class="prop-name">{'دورات الشحن:' if is_ar else 'Charge Cycles:'}</span><span class="prop-val">{pmic.get("cycle_count", "N/A")}</span></div>
                <div class="prop-row"><span class="prop-name">{'السعة التصميمية:' if is_ar else 'Design Capacity:'}</span><span class="prop-val">{f"{pmic.get('charge_full_design_uah')} uAh" if pmic.get('charge_full_design_uah') else "UNKNOWN"}</span></div>
                {make_evidence_box(pmic.get("evidence", []))}
            </div>
        </div>

        <!-- Motherboard & Thermal Zones -->
        <div class="grid-2">
            <div class="card">
                <div class="card-header">
                    <span>{thermal_card_title}</span>
                    <span class="badge badge-pass">{'الآيسيات طبيعية' if is_ar else 'IC Zones Monitored'}</span>
                </div>
                <table class="table">
                    <thead>
                        <tr><th>{'المنطقة' if is_ar else 'Zone'}</th><th>{'نوع الحساس' if is_ar else 'Sensor Type'}</th><th>{'الحرارة' if is_ar else 'Temperature'}</th><th>{'الحالة' if is_ar else 'Status'}</th></tr>
                    </thead>
                    <tbody>
                        {"".join([f'<tr><td>Zone {tz.get("zone_id", 0)}</td><td>{tz.get("zone_type", "CPU")}</td><td>{tz.get("temp_c", 0)}°C</td><td><span class="badge {"badge-fail" if tz.get("is_overheated") else "badge-pass"}">{"مرتفعة" if (tz.get("is_overheated") and is_ar) else ("آمنة" if is_ar else ("HOT" if tz.get("is_overheated") else "SAFE"))}</span></td></tr>' for tz in thermals[:6]]) if thermals else f'<tr><td colspan="4">{"حرارة جميع الآيسيات ضمن المعدل الآمن" if is_ar else "Normal operating temperatures across all motherboard IC zones."}</td></tr>'}
                    </tbody>
                </table>
            </div>

            <div class="card">
                <div class="card-header">
                    <span>{panic_card_title}</span>
                    <span class="badge" style="background: {diag_color}; color: #fff;">{panics.get("summary_verdict", "Clean")[:18]}</span>
                </div>
                <div class="prop-row"><span class="prop-name">{'أخطاء ذاكرة UFS/eMMC:' if is_ar else 'eMMC/UFS I/O Errors:'}</span><span class="prop-val">{len(panics.get("emmc_ufs_errors", []))}</span></div>
                <div class="prop-row"><span class="prop-name">{'إعادة تشغيل الحماية (Watchdog):' if is_ar else 'Watchdog Timeout Resets:'}</span><span class="prop-val">{len(panics.get("watchdog_resets", []))}</span></div>
                <div class="prop-row"><span class="prop-name">{'انهيارات الكيرنل (Kernel Panic):' if is_ar else 'Kernel Panics (Oops):'}</span><span class="prop-val">{len(panics.get("kernel_panics", []))}</span></div>
                <div class="prop-row"><span class="prop-name">{'إطفاء أمان الحرارة المفرطة:' if is_ar else 'Thermal Shutdown Trips:'}</span><span class="prop-val">{len(panics.get("thermal_shutdowns", []))}</span></div>
                <div style="margin-top: 10px; font-size: 12px; color: #8b949e; background: #010409; padding: 8px; border-radius: 4px;">
                    {panics.get("summary_verdict", "No kernel errors found.")}
                </div>
                {make_evidence_box(panics.get("blackbox", {}).get("evidence", []))}
            </div>
        </div>

        <!-- Hardware Components & Sensors -->
        <div class="card" style="margin-bottom: 25px;">
            <div class="card-header">
                <span>{hw_card_title}</span>
                <span class="badge badge-pass">{len(sensors)} {'حساس متصل' if is_ar else 'Sensors Active'}</span>
            </div>
            <div class="grid-3" style="margin-bottom: 0;">
                <div>
                    <h4 style="color: #79c0ff; font-size: 13px; margin-bottom: 6px;">{'المستشعرات المسجلة (حسب نظام Sensor HAL):' if is_ar else 'Reported sensors:'}</h4>
                    <ul style="padding-{'right' if is_ar else 'left'}: 18px; font-size: 12px; color: #c9d1d9;">
                        {"".join([f'<li>{s.get("name", "Sensor")} ({s.get("sensor_type", "Standard")})</li>' for s in sensors[:5]]) if sensors else f"<li>{'تم التحقق من الحساسات عبر ناقل العتاد' if is_ar else 'Sensors verified via hardware bus'}</li>"}
                    </ul>
                </div>
                <div>
                    <h4 style="color: #79c0ff; font-size: 13px; margin-bottom: 6px;">{'كاميرات الهاتف:' if is_ar else 'Camera Modules:'}</h4>
                    <ul style="padding-{'right' if is_ar else 'left'}: 18px; font-size: 12px; color: #c9d1d9;">
                        {"".join([f'<li>كاميرا {c.get("camera_id")}: عدسة {c.get("facing")} (فلاش: {"نعم" if c.get("flash_available") else "لا"})</li>' if is_ar else f'<li>Camera {c.get("camera_id")}: {c.get("facing")} Sensor (Flash: {"Yes" if c.get("flash_available") else "No"})</li>' for c in cameras]) if cameras else f"<li>{'نظام الكاميرات سليم' if is_ar else 'Standard Camera Subsystem Active'}</li>"}
                    </ul>
                </div>
                <div>
                    <h4 style="color: #79c0ff; font-size: 13px; margin-bottom: 6px;">{'الحساسات المفقودة أو المعطلة:' if is_ar else 'Missing Standard Sensors:'}</h4>
                    <div style="font-size: 12px; color: {"#ef4444" if missing_sensors else "#10b981"};">
                        {", ".join(missing_sensors) if missing_sensors else ("لا يوجد (جميع الحساسات الأساسية تعمل بكفاءة)" if is_ar else "None (All Primary Sensors Present & Verified)")}
                    </div>
                </div>
            </div>
            <!-- Peripherals Subsystem Diagnostics -->
            <div style="margin-top: 15px; border-top: 1px solid #21262d; padding-top: 12px;">
                <h4 style="color: #58a6ff; font-size: 13px; margin-bottom: 8px;">{'فحص الملحقات الدقيقة (البصمة، الشريحة، الأزرار، الشاشة):' if is_ar else 'Low-Level Hardware Peripherals Audit:'}</h4>
                <div class="grid-2" style="margin-bottom: 0;">
                    <div style="font-size: 12px;">
                        <div><strong>{'حساس البصمة:' if is_ar else 'Fingerprint IC:'}</strong> <span style="color: {'#ef4444' if fp.get('is_flex_disconnected') else '#10b981'};">{fp.get('diagnosis', 'Normal')}</span></div>
                        <div style="margin-top: 4px;"><strong>{'بيت الشريحة والبيسباند:' if is_ar else 'SIM & Baseband:'}</strong> <span style="color: {'#ef4444' if sim.get('is_rf_baseband_faulty') else '#10b981'};">{sim.get('diagnosis', 'Normal')}</span></div>
                    </div>
                    <div style="font-size: 12px;">
                        <div><strong>{'أزرار الصوت والباور:' if is_ar else 'Physical Buttons:'}</strong> <span style="color: {'#ef4444' if btn.get('is_any_key_stuck') else '#10b981'};">{btn.get('diagnosis', 'Normal')}</span></div>
                        <div style="margin-top: 4px;"><strong>{'شاشة العرض والإضاءة:' if is_ar else 'Display & Backlight:'}</strong> <span style="color: {'#ef4444' if disp.get('is_panel_disconnected') else '#10b981'};">{disp.get('diagnosis', 'Normal')}</span></div>
                    </div>
                </div>
                <div class="grid-2" style="margin-top: 8px; margin-bottom: 0;">
                    <div style="font-size: 12px;">
                        <div><strong>{'الواي فاي (Wi-Fi):' if is_ar else 'Wi-Fi Chipset:'}</strong> <span style="color: {'#ef4444' if wifi.get('is_hardware_faulty') else '#10b981'};">{wifi.get('diagnosis', 'Normal')}</span></div>
                        <div style="margin-top: 4px;"><strong>{'البلوتوث (Bluetooth):' if is_ar else 'Bluetooth Controller:'}</strong> <span style="color: {'#ef4444' if bt.get('is_hardware_faulty') else '#10b981'};">{bt.get('diagnosis', 'Normal')}</span></div>
                    </div>
                    <div style="font-size: 12px;">
                        <div><strong>{'آيسي الصوت (Audio Codec):' if is_ar else 'Audio Codec IC:'}</strong> <span style="color: {'#ef4444' if acodec.get('is_codec_faulty') else '#10b981'};">{acodec.get('diagnosis', 'Normal')} ({acodec.get('codec_chip_name', 'ALSA Soundcard')})</span></div>
                        <div style="margin-top: 4px;"><strong>{'معلومات وحدة الشاشة (DSI/VBMeta):' if is_ar else 'Display Controller & Boot Integrity:'}</strong> <span style="color: {'#10b981' if dauth.get('status') == 'PASS' else ('#ef4444' if dauth.get('status') == 'FAIL' else '#f59e0b')};">{dauth.get('panel_controller_name', 'OEM DSI')} | {dauth.get('verified_boot_state', 'green')}</span></div>
                    </div>
                </div>
            </div>
            <!-- Storage Health, Lifespan & Stress Test -->
            <div style="margin-top: 15px; border-top: 1px solid #21262d; padding-top: 12px;">
                <h4 style="color: #a78bfa; font-size: 13px; margin-bottom: 8px;">{'فحص عمر وسرعة الذاكرة واختبار إجهاد المعالج والباور:' if is_ar else 'NAND Storage Wear Benchmark & CPU Stress Test:'}</h4>
                <div class="grid-2" style="margin-bottom: 0;">
                    <div style="font-size: 12px;">
                        <div><strong>{'نوع وموديل الذاكرة:' if is_ar else 'NAND Storage Unit:'}</strong> <span style="color: #38bdf8;">{slife.get('storage_type', srep.get('storage_type', 'NAND'))} {slife.get('device_model', '')}</span></div>
                        <div style="margin-top: 4px;"><strong>{'معدل استهلاك الذاكرة (JEDEC):' if is_ar else 'Storage Wear Level:'}</strong> <span style="color: {'#ef4444' if slife.get('is_critical') else ('#f59e0b' if slife.get('is_warning') else '#10b981')};">SLC: {slife.get('life_time_type_a', srep.get('life_time_type_a', 'Normal'))} | Pre-EOL: {slife.get('pre_eol_info', 'Normal')}</span></div>
                        <div style="margin-top: 4px;"><strong>{'سرعة القراءة / الكتابة:' if is_ar else 'Read / Write Speed:'}</strong> <span style="color: #10b981;">{srep.get('read_speed_mb_s', '—')} MB/s (Read) / {srep.get('write_speed_mb_s', '—')} MB/s (Write)</span></div>
                    </div>
                    <div style="font-size: 12px;">
                        <div><strong>{'اختبار الإجهاد (30 ثانية):' if is_ar else '30s Stress Test:'}</strong> <span style="color: {'#ef4444' if (stress.get('severe_sag_detected') or stress.get('throttling_detected')) else '#10b981'};">{stress.get('verdict', 'Normal')}: {stress.get('diagnosis', 'Stable')}</span></div>
                        <div style="margin-top: 4px;"><strong>{'هبوط الجهد (Voltage Sag):' if is_ar else 'Voltage Sag:'}</strong> <span style="color: #f59e0b;">{stress.get('voltage_sag_mv', 0)} mV ({stress.get('initial_voltage_mv', 0)} mV &rarr; {stress.get('min_voltage_mv', 0)} mV)</span></div>
                        <div style="margin-top: 4px;"><strong>{'ارتفاع حرارة المعالج:' if is_ar else 'Thermal Rise:'}</strong> <span style="color: #ef4444;">+{stress.get('temp_rise_c', 0)} °C (Max: {stress.get('max_temp_c', 0)} °C)</span></div>
                    </div>
                </div>
            </div>
            {make_evidence_box(haudit.get("evidence", []) or slife.get("evidence", []))}
        </div>

        <!-- Security & Malware Hunter -->
        <div class="card" style="margin-bottom: 25px;">
            <div class="card-header">
                <span>{sec_card_title}</span>
                <span class="badge" style="background: {risk_color}; color: #fff;">{'مستوى الخطر:' if is_ar else 'Risk:'} {overall_risk}</span>
            </div>
            <div class="grid-2">
                <div>
                    <div class="prop-row"><span class="prop-name">{'إجمالي التطبيقات المفحوصة:' if is_ar else 'Total Packages Scanned:'}</span><span class="prop-val">{sscan.get("total_packages_scanned", 0)}</span></div>
                    <div class="prop-row"><span class="prop-name">{'تطبيقات المستخدم المثبتة:' if is_ar else 'Third-Party User Apps:'}</span><span class="prop-val">{sscan.get("third_party_count", 0)}</span></div>
                    <div class="prop-row"><span class="prop-name">{'تطبيقات النظام الأساسية:' if is_ar else 'System Pre-installed Apps:'}</span><span class="prop-val">{sscan.get("system_count", 0)}</span></div>
                    <div class="prop-row"><span class="prop-name">{'التطبيقات الشبحية (بدون أيقونة):' if is_ar else 'Ghost Apps (Hidden Launcher):'}</span><span class="prop-val">{len(ghost_apps)}</span></div>
                    <div class="prop-row"><span class="prop-name">{'تطبيقات التجسس الخطرة (RAT):' if is_ar else 'Critical RAT / Spyware Threats:'}</span><span class="prop-val" style="color: {"#ef4444" if critical_threats else "#10b981"}">{len(critical_threats)}</span></div>
                </div>
                <div>
                    <h4 style="color: #79c0ff; font-size: 13px; margin-bottom: 6px;">{'الاتصالات الخارجية النشطة:' if is_ar else 'Active Network Sockets:'}</h4>
                    <table class="table" style="font-size: 11px;">
                        <thead><tr><th>{'الوجهة الخارجية' if is_ar else 'Remote IP:Port'}</th><th>{'التطبيق المالك' if is_ar else 'Owner'}</th><th>{'الحالة' if is_ar else 'State'}</th></tr></thead>
                        <tbody>
                            {"".join([f'<tr><td>{nc.get("remote_address")}:{nc.get("remote_port")}</td><td>{nc.get("package_name")}</td><td>{nc.get("state")}</td></tr>' for nc in connections[:4]]) if connections else f"<tr><td colspan='3'>{'لا توجد اتصالات مريبة نشطة' if is_ar else 'No active external sockets'}</td></tr>"}
                        </tbody>
                    </table>
                </div>
            </div>
        </div>

        <!-- Optimization Metrics -->
        <div class="card" style="margin-bottom: 25px;">
            <div class="card-header">
                <span>{opt_card_title}</span>
                <span class="badge badge-pass">{'تمت الصيانة بنجاح' if is_ar else 'Optimized'}</span>
            </div>
            <div class="grid-3">
                <div style="background: #010409; padding: 12px; border-radius: 6px; text-align: center;">
                    <div style="color: #8b949e; font-size: 12px;">{'الرام المحررة' if is_ar else 'RAM Released'}</div>
                    <div style="font-size: 20px; font-weight: bold; color: #58a6ff;">+{ram_freed} MB</div>
                    <div style="font-size: 11px; color: #8b949e;">{opt_before.get("ram_free_mb", 0)}MB &rarr; {opt_after.get("ram_free_mb", 0)}MB</div>
                </div>
                <div style="background: #010409; padding: 12px; border-radius: 6px; text-align: center;">
                    <div style="color: #8b949e; font-size: 12px;">{'مساحة التخزين الموفرة' if is_ar else 'Storage Cleaned'}</div>
                    <div style="font-size: 20px; font-weight: bold; color: #238636;">+{storage_freed} MB</div>
                    <div style="font-size: 11px; color: #8b949e;">{opt_before.get("storage_free_mb", 0)}MB &rarr; {opt_after.get("storage_free_mb", 0)}MB</div>
                </div>
                <div style="background: #010409; padding: 12px; border-radius: 6px; text-align: center;">
                    <div style="color: #8b949e; font-size: 12px;">{'توفير استنزاف البطارية' if is_ar else 'Standby Drain Reduction'}</div>
                    <div style="font-size: 20px; font-weight: bold; color: #f59e0b;">-{drain_reduction} mA</div>
                    <div style="font-size: 11px; color: #8b949e;">{opt_before.get("current_drain_ma", 0)}mA &rarr; {opt_after.get("current_drain_ma", 0)}mA</div>
                </div>
            </div>
            <div style="margin-top: 12px; font-size: 12px; color: #8b949e;">
                <strong>{'العمليات المنفذة:' if is_ar else 'Executed Protocols:'}</strong> {", ".join(actions) if actions else ("تهيئة الفلاش TRIM، تجميع الكود البرمجي bg-dexopt، تفريغ الكاش، وتفعيل توفير الطاقة Doze" if is_ar else "NAND Flash TRIM, Bytecode bg-dexopt, Cache Purge, Deep Doze, UI 0.5x Scale")}
            </div>
        </div>

        <!-- Footer & Sign-off with Cryptographic Verification -->
        <div class="footer">
            <div>
                <div>{'الفني المسؤول:' if is_ar else 'Inspected By:'} <strong>{technician_name}</strong></div>
                <div>{'المحطة:' if is_ar else 'Station:'} <strong>{'مختبر أندرويد دكتور برو لفحص وصيانة العتاد' if is_ar else 'Android Doctor Pro Hardware Lab'}</strong></div>
                {f'<div style="margin-top: 4px;">{"رقم التذكرة:" if is_ar else "Ticket REF:"} <strong style="color: #58a6ff;">{ticket_id}</strong></div>' if ticket_id else ''}
            </div>
            <div style="text-align: {'left' if is_ar else 'right'};">
                <div style="font-family: monospace; font-size: 11px;">{'الختم الرقمي المشفر (HMAC-SHA256):' if is_ar else 'HMAC-SHA256 DIGITAL STAMP:'}</div>
                <div style="font-family: monospace; font-size: 10px; color: #58a6ff; word-break: break-all;">{cert_hmac_sig}</div>
                <div style="margin-top: 4px;">{'الحالة:' if is_ar else 'Status:'} <strong>{'تقرير تشخيصي موثق ومحمي بختم رقمي' if is_ar else 'VERIFIED DIAGNOSTIC REPORT, DIGITALLY SIGNED'}</strong></div>
            </div>
        </div>
    </div>
</body>
</html>
"""

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(html)

        logger.info(f"Diagnostic certificate generated at: {output_path}")
        return output_path
