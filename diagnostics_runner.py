"""
Full diagnostics of one device, and of every connected device in parallel (CLI --all-devices and the
GUI batch panel).

`diagnose_device` is the single-device pipeline: profile -> board -> hardware -> security -> status
table -> HTML report (optionally signed, optionally PDF). Output goes through `emit`, so parallel runs
can keep each device's text together instead of interleaving lines.

`diagnose_all` runs `diagnose_device` for each device on worker threads. Each device gets its own
report file. Devices that are unauthorized/offline are reported, not diagnosed. The shared SQLite
database is closed once, after every worker has finished.
"""

import logging
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

logger = logging.getLogger("AndroidDoctorPro")

EXIT_OK, EXIT_NO_DEVICE, EXIT_UNAUTHORIZED, EXIT_PARTIAL, EXIT_INTERNAL = 0, 1, 2, 3, 4

Emit = Callable[[str], None]


@dataclass
class DeviceRun:
    serial: str
    state: str
    exit_code: int
    overall: str = "UNKNOWN"
    model: str = ""
    report_path: Optional[str] = None
    pdf_path: Optional[str] = None
    failed_stages: List[str] = field(default_factory=list)
    output: List[str] = field(default_factory=list)


def print_status_table(rows: List[Tuple[str, str, Any]], lang: str, emit: Emit = print) -> None:
    from ui.i18n import t, render
    name_w = max([len(t(cid, lang)) for cid, _, _ in rows] + [10])
    emit(f"\n{t('cli.table_title', lang)}")
    emit("-" * (name_w + 14))
    for cid, status, detail in rows:
        line = f"{t(cid, lang):<{name_w}}  {status:<10}"
        if detail is not None:
            line += f"  {render(detail, lang)}"
        emit(line)


def diagnose_device(bridge, target: Dict[str, Any], lang: str, get_db: Callable[[], Any],
                    output_dir: Optional[str] = None, signing_key: Optional[str] = None,
                    pdf: bool = False, emit: Emit = print, companion: bool = False) -> DeviceRun:
    """Diagnose one listed device. Never closes the database (the caller owns it)."""
    from core.result import aggregate_status, CheckResult, Status, M
    from core.report_signing import SigningError
    from ui.i18n import t, render

    target_serial = target["serial"]
    state = target.get("state", "unknown")
    run = DeviceRun(serial=target_serial, state=state, exit_code=EXIT_OK, model=target.get("model") or "")
    emit(t("cli.target", lang, serial=target_serial, state=state))

    if state in ("unauthorized", "offline"):
        emit(t(f"cli.state.{state}", lang))
        run.exit_code = EXIT_UNAUTHORIZED
        return run
    if state == "fastboot":
        from core.rescue_center import RescueCenter
        fb = RescueCenter(bridge).get_fastboot_variables(target_serial)
        emit(t("cli.fastboot_vars", lang, product=fb.product, bootloader=fb.bootloader_version, unlocked=fb.is_unlocked))
        return run
    if state != "device":
        emit(t("cli.unsupported_state", lang, state=state))
        run.exit_code = EXIT_UNAUTHORIZED
        return run

    db = get_db()
    failed_stages = run.failed_stages

    def stage(name: str, fn):
        emit(f"\n--- {t('cli.stage.' + name, lang)} ---")
        try:
            return fn()
        except Exception as e:
            logger.error(f"[{target_serial}] Stage {name} failed: {e}\n{traceback.format_exc()}")
            failed_stages.append(name)
            emit(t("cli.stage_failed", lang, error=f"{type(e).__name__}: {e}"))
            return None

    from core.profiler import DeviceProfiler
    from core.board_diag import MotherboardDiagnostics
    from core.hardware_tester import HardwareTester
    from core.malware_hunter import MalwareHunter

    profile = stage("profile", lambda: DeviceProfiler(bridge).profile_device(target_serial))
    if profile is not None:
        run.model = f"{profile.brand} {profile.model}".strip() or run.model
        emit(t("cli.device_line", lang, brand=profile.brand, model=profile.model,
               android=profile.android_version, sdk=profile.sdk_version, patch=profile.security_patch))

    bdiag = stage("board", lambda: MotherboardDiagnostics(bridge, db).run_full_diagnostics(target_serial))
    if bdiag is not None:
        p = bdiag.pmic
        # A value that was not read shows why (NEEDS_ROOT / UNKNOWN), never the "UNKNOWN" source placeholder.
        def reading(key, value, source, status, **kw):
            if value is None:
                return t(key + "_unread", lang, status=status, **kw)
            return t(key, lang, value=value, source=source, **kw)

        emit(reading("cli.battery_voltage", p.voltage_mv, p.voltage_source, p.voltage_status))
        emit(reading("cli.battery_current", p.current_ma, p.current_source, p.current_status, note=p.current_note))
        emit(t("cli.battery_capacity", lang, value=p.true_health_pct, status=p.health_verdict))
        emit(reading("cli.battery_temp", p.temp_c, p.temp_source, p.temp_status))
        for f in bdiag.critical_findings:
            emit(f"  [!] {render(f, lang)}")

    hw = stage("hardware", lambda: HardwareTester(bridge).run_full_audit(target_serial))
    if hw is not None:
        emit(t("cli.reported_sensors", lang, count=len(hw.sensors), status=hw.sensors_status))
        emit(t("cli.reported_cameras", lang, count=len(hw.cameras), status=hw.cameras_status))

    companion_checks: List[Any] = []
    if companion:
        def run_companion_stage():
            from core import companion as comp
            from core.model_db import default_db
            run = comp.run_companion(bridge, target_serial)
            spec = default_db().lookup(bridge.get_prop("ro.product.model", serial=target_serial) or "")
            return comp.judge(run, spec)
        companion_checks = stage("companion", run_companion_stage) or []

    sec = stage("security", lambda: MalwareHunter(bridge, db).audit_security(target_serial))
    if sec is not None:
        emit(render(sec.summary, lang))

    # ---------------- per-check status table (from CheckResult lists) ----------------
    stage_checks = [CheckResult(check_id=f"cli.stage.{name}", status=Status.UNKNOWN,
                                detail=M("cli.stage_failed_short")) for name in failed_stages]
    device_checks: List[CheckResult] = ((bdiag.checks if bdiag is not None else [])
                                        + (hw.checks if hw is not None else []) + companion_checks)
    info_checks: List[CheckResult] = sec.check_results() if sec is not None else []  # shown, not counted
    rows: List[Tuple[str, str, Any]] = [(c.check_id, str(c.status), c.detail)
                                        for c in device_checks + info_checks + stage_checks]
    run.overall = str(aggregate_status([c.status for c in device_checks + stage_checks]))
    print_status_table(rows, lang, emit)
    emit(f"\n{t('cli.overall', lang, status=run.overall)}")

    # ---------------- report (always written, also for partial results) ----------------
    try:
        from ui.report_exporter import DiagnosticReportExporter
        run.report_path = DiagnosticReportExporter.generate_html_report(
            device_profile=profile.to_dict() if profile is not None else {"serial": target_serial},
            board_diag=bdiag.to_dict() if bdiag is not None else {},
            hw_audit=hw.to_dict() if hw is not None else {},
            sec_scan=sec.to_dict() if sec is not None else {},
            extra_checks=[c.to_dict() for c in stage_checks + companion_checks],
            signing_key_path=signing_key,
            lang=lang,
            output_dir=output_dir,
        )
        emit(f"\n{t('cli.report_written', lang, path=run.report_path)}")
        if signing_key:
            from core.report_signing import signature_path_for
            emit(t("cli.signature_written", lang, path=signature_path_for(run.report_path)))
        if pdf:
            from ui.pdf_export import PdfExportError, export_pdf
            try:
                run.pdf_path, pdf_sig = export_pdf(run.report_path, signing_key)
            except PdfExportError as e:
                emit(t("cli.pdf_failed", lang, error=render(e.msg, lang)))
                run.exit_code = EXIT_INTERNAL
                return run
            emit(t("cli.pdf_written", lang, path=run.pdf_path))
            if pdf_sig:
                emit(t("cli.signature_written", lang, path=pdf_sig))
    except SigningError as e:
        emit(t("cli.signing_failed", lang, error=render(e.msg, lang)))
        run.exit_code = EXIT_INTERNAL
        return run
    except Exception as e:
        logger.error(f"[{target_serial}] Report generation failed: {e}\n{traceback.format_exc()}")
        emit(t("cli.internal_error", lang, error=f"report: {e}"))
        run.exit_code = EXIT_INTERNAL
        return run

    run.exit_code = EXIT_PARTIAL if failed_stages else EXIT_OK
    return run


def diagnose_all(bridge, devices: List[Dict[str, Any]], lang: str, get_db: Callable[[], Any],
                 output_dir: Optional[str] = None, signing_key: Optional[str] = None, pdf: bool = False,
                 max_workers: int = 4, on_done: Optional[Callable[[DeviceRun], None]] = None,
                 companion: bool = False) -> List[DeviceRun]:
    """Diagnose every device in parallel; results in the order of `devices`.

    `on_done` is called (from a worker thread) as each device finishes. A crash inside one device's
    run becomes that device's EXIT_INTERNAL result; the others continue.
    """
    import threading
    db_lock = threading.Lock()
    db_holder: List[Any] = []

    def shared_db():
        with db_lock:
            if not db_holder:
                db_holder.append(get_db())
            return db_holder[0]

    def one(target: Dict[str, Any]) -> DeviceRun:
        out: List[str] = []
        try:
            run = diagnose_device(bridge, target, lang, shared_db, output_dir, signing_key, pdf, emit=out.append,
                                  companion=companion)
        except Exception as e:
            logger.error(f"[{target.get('serial')}] Diagnostics crashed: {e}\n{traceback.format_exc()}")
            run = DeviceRun(serial=target.get("serial", "?"), state=target.get("state", "unknown"),
                            exit_code=EXIT_INTERNAL)
            out.append(f"{type(e).__name__}: {e}")
        run.output = out
        if on_done is not None:
            try:
                on_done(run)
            except Exception:
                logger.exception("on_done callback failed")
        return run

    results: Dict[int, DeviceRun] = {}
    try:
        with ThreadPoolExecutor(max_workers=max(1, min(max_workers, len(devices) or 1)),
                                thread_name_prefix="adp-device") as pool:
            futures = {pool.submit(one, d): i for i, d in enumerate(devices)}
            for fut in as_completed(futures):
                results[futures[fut]] = fut.result()
    finally:
        if db_holder and hasattr(db_holder[0], "close"):
            db_holder[0].close()
    return [results[i] for i in range(len(devices))]


def combined_exit_code(runs: List[DeviceRun]) -> int:
    """Worst result wins: internal error (4) > partial (3) > unauthorized/offline (2) > ok (0)."""
    return max((r.exit_code for r in runs), default=EXIT_NO_DEVICE)
