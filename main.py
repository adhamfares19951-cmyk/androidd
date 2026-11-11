"""
Android Doctor Pro - entry point (GUI by default, --cli for a console run).

Exit codes (CLI):
  0  run completed (results may still contain FAIL / UNKNOWN / NEEDS_ROOT)
  1  no device (or the --serial given is not connected)
  2  device unauthorized or offline
  3  partial results: at least one stage failed and is reported as UNKNOWN
  4  internal error (e.g. the report could not be written)
"""

import argparse
import logging
import logging.handlers
import os
import sys
import traceback
from typing import Any, Dict, List, Optional, Tuple

EXIT_OK, EXIT_NO_DEVICE, EXIT_UNAUTHORIZED, EXIT_PARTIAL, EXIT_INTERNAL = 0, 1, 2, 3, 4

logger = logging.getLogger("AndroidDoctorPro")


def configure_console_encoding() -> None:
    """UTF-8 console output (Arabic text, °C, etc.). pythonw has no stdout at all."""
    for stream in (sys.stdout, sys.stderr):
        if stream is not None and hasattr(stream, "reconfigure"):
            try:
                stream.reconfigure(encoding="utf-8", errors="replace")
            except (ValueError, OSError):
                pass


def log_directory() -> str:
    base = os.environ.get("LOCALAPPDATA")
    if base:
        return os.path.join(base, "AndroidDoctorPro", "logs")
    return os.path.join(os.path.expanduser("~"), ".local", "state", "AndroidDoctorPro", "logs")


def configure_logging(console_level: int = logging.WARNING) -> str:
    """Rotating log file (1 MB x 5) in %LOCALAPPDATA%\\AndroidDoctorPro\\logs\\; console shows warnings only."""
    log_dir = log_directory()
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    try:
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(os.path.join(log_dir, "android_doctor.log"),
                                                  maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        root.addHandler(fh)
    except OSError:
        log_dir = ""
    if sys.stderr is not None:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(console_level)
        ch.setFormatter(fmt)
        root.addHandler(ch)
    return log_dir


def enable_windows_high_dpi() -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                import ctypes
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


# ------------------------------------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------------------------------------
def choose_device(devices: List[Dict[str, Any]], serial: str, lang: str) -> Tuple[Optional[Dict[str, Any]], int]:
    from ui.i18n import t
    if not devices:
        print(t("cli.no_device", lang))
        return None, EXIT_NO_DEVICE
    if serial:
        for d in devices:
            if d.get("serial") == serial:
                return d, EXIT_OK
        print(t("cli.serial_not_found", lang, serial=serial))
        return None, EXIT_NO_DEVICE
    if len(devices) > 1:
        print(t("cli.several_devices", lang, count=len(devices)))
        for d in devices:
            print(f"  - {d.get('serial')}  [{d.get('state')}]  {d.get('model') or ''}")
    authorized = [d for d in devices if d.get("state") == "device"]
    chosen = authorized[0] if authorized else devices[0]
    if len(devices) > 1:
        print(t("cli.chosen_device", lang, serial=chosen.get("serial")))
    return chosen, EXIT_OK


def print_status_table(rows: List[Tuple[str, str, Any]], lang: str) -> None:
    from ui.i18n import t, render
    name_w = max([len(t(cid, lang)) for cid, _, _ in rows] + [10])
    print(f"\n{t('cli.table_title', lang)}")
    print("-" * (name_w + 14))
    for cid, status, detail in rows:
        line = f"{t(cid, lang):<{name_w}}  {status:<10}"
        if detail is not None:
            line += f"  {render(detail, lang)}"
        print(line)


def run_cli_diagnostics(serial: str = "", lang: str = "ar", output_dir: Optional[str] = None) -> int:
    from core.adb_bridge import ADBBridge
    from core.result import aggregate_status, CheckResult, Status, M
    from ui.i18n import t, render, set_current_language

    set_current_language(lang)
    print(f"\n=== {t('cli.title', lang)} ===\n")

    bridge = ADBBridge()
    try:
        devices = bridge.list_devices()
    except Exception:
        logger.exception("Listing devices failed")
        print(t("cli.internal_error", lang, error="list_devices"))
        return EXIT_INTERNAL

    target, code = choose_device(devices, serial, lang)
    if target is None:
        return code
    target_serial = target["serial"]
    state = target.get("state", "unknown")
    print(t("cli.target", lang, serial=target_serial, state=state))

    if state in ("unauthorized", "offline"):
        print(t(f"cli.state.{state}", lang))
        return EXIT_UNAUTHORIZED
    if state == "fastboot":
        from core.rescue_center import RescueCenter
        fb = RescueCenter(bridge).get_fastboot_variables(target_serial)
        print(t("cli.fastboot_vars", lang, product=fb.product, bootloader=fb.bootloader_version, unlocked=fb.is_unlocked))
        return EXIT_OK
    if state != "device":
        print(t("cli.unsupported_state", lang, state=state))
        return EXIT_UNAUTHORIZED

    from database.db_manager import DatabaseManager
    db = DatabaseManager()
    results: Dict[str, Any] = {}
    failed_stages: List[str] = []

    def stage(name: str, fn):
        print(f"\n--- {t('cli.stage.' + name, lang)} ---")
        try:
            results[name] = fn()
            return results[name]
        except Exception as e:
            logger.error(f"Stage {name} failed: {e}\n{traceback.format_exc()}")
            failed_stages.append(name)
            print(t("cli.stage_failed", lang, error=f"{type(e).__name__}: {e}"))
            return None

    try:
        from core.profiler import DeviceProfiler
        from core.board_diag import MotherboardDiagnostics
        from core.hardware_tester import HardwareTester
        from core.malware_hunter import MalwareHunter

        profile = stage("profile", lambda: DeviceProfiler(bridge).profile_device(target_serial))
        if profile is not None:
            print(t("cli.device_line", lang, brand=profile.brand, model=profile.model,
                    android=profile.android_version, sdk=profile.sdk_version, patch=profile.security_patch))

        bdiag = stage("board", lambda: MotherboardDiagnostics(bridge, db).run_full_diagnostics(target_serial))
        if bdiag is not None:
            p = bdiag.pmic
            print(t("cli.battery_voltage", lang, value=p.voltage_mv, source=p.voltage_source))
            print(t("cli.battery_current", lang, value=p.current_ma, source=p.current_source, note=p.current_note))
            print(t("cli.battery_capacity", lang, value=p.true_health_pct, status=p.health_verdict))
            print(t("cli.battery_temp", lang, value=p.temp_c, source=p.temp_source))
            for f in bdiag.critical_findings:
                print(f"  [!] {render(f, lang)}")

        hw = stage("hardware", lambda: HardwareTester(bridge).run_full_audit(target_serial))
        if hw is not None:
            print(t("cli.reported_sensors", lang, count=len(hw.sensors), status=hw.sensors_status))
            print(t("cli.reported_cameras", lang, count=len(hw.cameras), status=hw.cameras_status))

        sec = stage("security", lambda: MalwareHunter(bridge, db).audit_security(target_serial))
        if sec is not None:
            print(render(sec.summary, lang))

        # ---------------- per-check status table (from CheckResult lists) ----------------
        stage_checks = [CheckResult(check_id=f"cli.stage.{name}", status=Status.UNKNOWN,
                                    detail=M("cli.stage_failed_short")) for name in failed_stages]
        device_checks: List[CheckResult] = ((bdiag.checks if bdiag is not None else [])
                                            + (hw.checks if hw is not None else []))
        info_checks: List[CheckResult] = sec.check_results() if sec is not None else []  # shown, not counted
        rows: List[Tuple[str, str, Any]] = [(c.check_id, str(c.status), c.detail)
                                            for c in device_checks + info_checks + stage_checks]
        overall = str(aggregate_status([c.status for c in device_checks + stage_checks]))
        print_status_table(rows, lang)
        print(f"\n{t('cli.overall', lang, status=overall)}")

        # ---------------- report (always written, also for partial results) ----------------
        try:
            from ui.report_exporter import DiagnosticReportExporter
            report_file = DiagnosticReportExporter.generate_html_report(
                device_profile=profile.to_dict() if profile is not None else {"serial": target_serial},
                board_diag=bdiag.to_dict() if bdiag is not None else {},
                hw_audit=hw.to_dict() if hw is not None else {},
                sec_scan=sec.to_dict() if sec is not None else {},
                extra_checks=[c.to_dict() for c in stage_checks],
                lang=lang,
                output_dir=output_dir,
            )
            print(f"\n{t('cli.report_written', lang, path=report_file)}")
        except Exception as e:
            logger.error(f"Report generation failed: {e}\n{traceback.format_exc()}")
            print(t("cli.internal_error", lang, error=f"report: {e}"))
            return EXIT_INTERNAL
    finally:
        if hasattr(db, "close"):
            db.close()

    return EXIT_PARTIAL if failed_stages else EXIT_OK


# ------------------------------------------------------------------------------------------------
def build_parser():
    from ui.i18n import available_languages
    parser = argparse.ArgumentParser(description="Android Doctor Pro - diagnostics over ADB / fastboot")
    parser.add_argument("--cli", action="store_true", help="run the diagnostics in the console (no GUI)")
    parser.add_argument("--serial", type=str, default="", help="target device serial")
    parser.add_argument("--lang", type=str, default="ar", choices=available_languages(), help="interface language")
    parser.add_argument("--check-bins", action="store_true", help="verify bin/ against bin/manifest.json (no download)")
    parser.add_argument("--repair-tools", action="store_true", help="download and re-install broken tools (hash-checked)")
    parser.add_argument("--output-dir", type=str, default=None, help="folder for the HTML report (CLI)")
    return parser


def main() -> int:
    configure_console_encoding()
    configure_logging()
    args = build_parser().parse_args()

    if args.check_bins or args.repair_tools:
        import json
        from setup_bins import verify_binaries, repair_tools
        if args.repair_tools:
            print(json.dumps(repair_tools(), indent=2))
        status = verify_binaries()
        print(json.dumps(status, indent=2))
        return EXIT_OK if status["ok"] else EXIT_INTERNAL

    if args.cli:
        try:
            return run_cli_diagnostics(args.serial, args.lang, args.output_dir)
        except Exception:
            logger.critical(f"Internal error\n{traceback.format_exc()}")
            return EXIT_INTERNAL

    # GUI: nothing is downloaded or repaired before the window opens; a banner offers "Repair tools".
    enable_windows_high_dpi()
    from ui.main_window import AndroidDoctorApp
    app = AndroidDoctorApp(lang=args.lang)
    app.mainloop()
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
