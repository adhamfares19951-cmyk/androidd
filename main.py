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
EXIT_BAD_SIGNATURE = 5  # --verify-report: the signature does not verify

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
    for h in list(root.handlers):   # called again (tests, CLI then GUI): replace our handlers, never stack them
        if getattr(h, "_adp_handler", False):
            root.removeHandler(h)
            h.close()
    try:
        os.makedirs(log_dir, exist_ok=True)
        fh = logging.handlers.RotatingFileHandler(os.path.join(log_dir, "android_doctor.log"),
                                                  maxBytes=1_000_000, backupCount=5, encoding="utf-8")
        fh.setFormatter(fmt)
        fh._adp_handler = True
        root.addHandler(fh)
    except OSError:
        log_dir = ""
    if sys.stderr is not None:
        ch = logging.StreamHandler(sys.stderr)
        ch.setLevel(console_level)
        ch.setFormatter(fmt)
        ch._adp_handler = True
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


from diagnostics_runner import print_status_table  # noqa: E402,F401  (kept importable from main)


def _db_factory():
    from database.db_manager import DatabaseManager
    return DatabaseManager()


def run_cli_diagnostics(serial: str = "", lang: str = "ar", output_dir: Optional[str] = None,
                        signing_key: Optional[str] = None, pdf: bool = False, companion: bool = False) -> int:
    from core.adb_bridge import ADBBridge
    from diagnostics_runner import diagnose_device
    from ui.i18n import t, set_current_language

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

    opened: List[Any] = []

    def get_db():
        if not opened:
            opened.append(_db_factory())
        return opened[0]

    try:
        return diagnose_device(bridge, target, lang, get_db, output_dir, signing_key, pdf,
                               companion=companion).exit_code
    finally:
        if opened and hasattr(opened[0], "close"):
            opened[0].close()


def run_cli_all_devices(lang: str = "ar", output_dir: Optional[str] = None, signing_key: Optional[str] = None,
                        pdf: bool = False, max_workers: int = 4, companion: bool = False) -> int:
    """Every connected device in parallel, one report each. Exit code: the worst device result."""
    from core.adb_bridge import ADBBridge
    from diagnostics_runner import combined_exit_code, diagnose_all
    from ui.i18n import t, set_current_language

    set_current_language(lang)
    print(f"\n=== {t('cli.title', lang)} ===\n")
    bridge = ADBBridge()
    try:
        devices = bridge.list_devices()
    except Exception:
        logger.exception("Listing devices failed")
        print(t("cli.internal_error", lang, error="list_devices"))
        return EXIT_INTERNAL
    if not devices:
        print(t("cli.no_device", lang))
        return EXIT_NO_DEVICE
    print(t("cli.batch_start", lang, count=len(devices), workers=min(max_workers, len(devices))))

    def done(run):
        print(t("cli.batch_device_done", lang, serial=run.serial, status=run.overall, code=run.exit_code), flush=True)

    runs = diagnose_all(bridge, devices, lang, _db_factory, output_dir, signing_key, pdf,
                        max_workers=max_workers, on_done=done, companion=companion)
    for run in runs:
        print(f"\n{'=' * 60}\n{run.serial}  {run.model}\n{'=' * 60}")
        for line in run.output:
            print(line)

    print(f"\n{t('cli.batch_summary', lang)}")
    for run in runs:
        print(f"  {run.serial:<24} {run.state:<13} {run.overall:<10} exit {run.exit_code}  {run.report_path or '-'}")
    return combined_exit_code(runs)


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
    parser.add_argument("--all-devices", action="store_true",
                        help="with --cli: diagnose every connected device in parallel (one report each)")
    parser.add_argument("--workers", type=int, default=4, help="parallel devices for --all-devices (default 4)")
    parser.add_argument("--companion", action="store_true",
                        help="with --cli: also install and run the companion app on the phone (screen on, unlocked)")
    parser.add_argument("--pdf", action="store_true",
                        help="also write a PDF copy of the CLI report (printed by the installed Edge/Chrome)")
    parser.add_argument("--sign-key", type=str, default=None,
                        help="Ed25519 private key (outside this folder) to sign the report; default: $ADP_SIGNING_KEY")
    parser.add_argument("--gen-signing-key", type=str, metavar="DIR", default=None,
                        help="create a new Ed25519 key pair in DIR (outside this folder)")
    parser.add_argument("--verify-report", type=str, metavar="REPORT", default=None,
                        help="verify REPORT against REPORT.sig with --public-key")
    parser.add_argument("--public-key", type=str, default=None, help="public key for --verify-report")
    parser.add_argument("--signature", type=str, default=None, help="signature file (default: REPORT.sig)")
    parser.add_argument("--verify-qr", type=str, metavar="TEXT", default=None,
                        help="verify the text (or link) of a signed report QR code with --public-key")
    parser.add_argument("--export-verifier", type=str, metavar="DIR", default=None,
                        help="write DIR/adp_verify.html, an offline QR verification page for --public-key")
    parser.add_argument("--analyze-log", type=str, metavar="FILE", default=None,
                        help="check a saved UART / dmesg / last_kmsg / logcat file against the fault signatures "
                             "(no phone, no root)")
    parser.add_argument("--screen-check", action="store_true",
                        help="screen and parts originality indicators (read only; indicators, never proof)")
    parser.add_argument("--uart-list", action="store_true", help="list serial ports (USB-UART adapters)")
    parser.add_argument("--uart-capture", type=str, metavar="PORT", default=None,
                        help="capture a phone's boot log from PORT (read only), save it, then analyse it")
    parser.add_argument("--companion-wifi", action="store_true",
                        help="companion app without USB debugging: show a QR code, wait for the results over Wi-Fi "
                             "(--seconds, default 600 here)")
    parser.add_argument("--companion-file", type=str, metavar="FILE", default=None,
                        help="judge a result file the companion app saved in Download/AndroidDoctor")
    parser.add_argument("--baud", type=int, default=115200, help="baud rate for --uart-capture (default 115200)")
    parser.add_argument("--seconds", type=int, default=None,
                        help="how long --uart-capture records (default 60) or --companion-wifi waits (default 600)")
    return parser


def run_screen_check(serial: str, lang: str) -> int:
    """--screen-check. Exit: 0 checked, 1 no device, 2 unauthorized / offline, 4 device list failed."""
    from core.adb_bridge import ADBBridge
    from core.display_check import DisplayOriginalityCheck
    from ui.i18n import t, set_current_language
    from ui.ui_helpers import format_display_check, format_parts_check
    from core.parts_check import PartsCheck
    set_current_language(lang)
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
    state = target.get("state", "unknown")
    if state != "device":
        print(t(f"cli.state.{state}", lang) if state in ("unauthorized", "offline")
              else t("cli.unsupported_state", lang, state=state))
        return EXIT_UNAUTHORIZED
    result = DisplayOriginalityCheck(bridge).run(target["serial"])
    print("\n".join(format_display_check(result, lang)))
    print()
    print("\n".join(format_parts_check(PartsCheck(bridge).run(target["serial"]), lang)))
    return EXIT_OK


def run_uart_command(args) -> int:
    """--uart-list / --uart-capture. Exit: 0 done (whatever was received), 4 the port could not be used."""
    import time as _time
    from core import log_analyzer, uart_capture as uc
    from ui.i18n import t, render, set_current_language
    from ui.ui_helpers import format_log_analysis
    lang = args.lang
    set_current_language(lang)
    if args.uart_list:
        ports = uc.list_ports()
        if not ports:
            print(t("cli.uart_no_ports", lang))
            return EXIT_OK
        print(t("cli.uart_ports_header", lang))
        for p in ports:
            print(t("cli.uart_port_line", lang, device=p.device, description=p.description, adapter=p.adapter or "-"))
        return EXIT_OK

    if args.seconds is None:
        args.seconds = 60
    print(t("cli.uart_wiring", lang))
    cap = uc.UartCapture(args.uart_capture, baud=args.baud, on_text=lambda s: print(s, end="", flush=True))
    try:
        cap.start()
    except uc.UartError as e:
        print(render(e.msg, lang))
        return EXIT_INTERNAL
    print(t("cli.uart_capturing", lang, port=args.uart_capture, baud=args.baud, seconds=args.seconds, path=cap.out_path))
    try:
        end = _time.monotonic() + max(1, args.seconds)
        while _time.monotonic() < end and cap.running:
            _time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    result = cap.stop()
    print()
    if result.error is not None:
        print(render(result.error, lang))
    print(t("cli.uart_saved", lang, bytes=result.bytes_received, seconds=result.seconds, path=result.path))
    if result.bytes_received == 0:
        print(t("cli.uart_nothing", lang))
        return EXIT_OK
    print("\n".join(format_log_analysis(log_analyzer.analyze_file(result.path), lang)))
    return EXIT_OK


def _print_companion(run, lang: str) -> None:
    from core import companion
    from core.companion_lan import device_label, reported_device, reported_model
    from core.model_db import default_db
    from ui.i18n import t, render
    spec = default_db().lookup(reported_model(run), reported_device(run)) if (reported_model(run) or reported_device(run)) else None
    print(t("cli.companion_phone", lang, device=device_label(run) or "-"))
    for c in companion.judge(run, spec):
        print(f"[{c.status}] {t(c.check_id, lang)}")
        if c.detail is not None:
            print(f"    {render(c.detail, lang)}")
        for cause in c.possible_causes:
            print(f"    - {t('common.possible_cause', lang)}: {render(cause, lang)}")


def run_companion_without_adb(args) -> int:
    """--companion-wifi / --companion-file FILE. Exit: 0 results judged, 1 nothing arrived in time,
    4 the server could not start or the file could not be read."""
    from core.companion_lan import CompanionWifiSession, load_result_file, qr_text
    from ui.i18n import t, render, set_current_language
    lang = args.lang
    set_current_language(lang)
    if args.companion_file:
        run = load_result_file(args.companion_file)
        if not run.results and run.error is not None:
            print(render(run.error, lang))
            return EXIT_INTERNAL
        _print_companion(run, lang)
        return EXIT_OK
    session = CompanionWifiSession(lang=lang)
    try:
        session.start()
    except Exception as e:
        print(t("ui.companion_wifi_failed", lang, error=str(e)))
        return EXIT_INTERNAL
    seconds = args.seconds or 600
    try:
        qr = qr_text(session.url())
        if qr:
            print(qr)
        print(t("cli.companion_wifi_open", lang, url=session.url(), seconds=seconds))
        run = session.wait(seconds)
    finally:
        session.stop()
    if not session.received.is_set():
        print(render(run.error, lang))
        return EXIT_NO_DEVICE
    _print_companion(run, lang)
    return EXIT_OK


def run_log_analysis(path: str, lang: str) -> int:
    """--analyze-log FILE. Exit: 0 analyzed (whatever it found), 4 file could not be read."""
    from core import log_analyzer as la
    from ui.i18n import t, set_current_language
    from ui.ui_helpers import format_log_analysis
    set_current_language(lang)
    try:
        r = la.analyze_file(path)
    except OSError as e:
        print(t("cli.logscan_unreadable_file", lang, error=str(e)))
        return EXIT_INTERNAL
    print("\n".join(format_log_analysis(r, lang)))
    return EXIT_OK


def run_signing_command(args) -> int:
    """--gen-signing-key / --verify-report / --verify-qr / --export-verifier.
    Exit: 0 ok, 5 signature does not verify, 4 error."""
    from core.report_signing import SigningError, generate_keypair, verify_file, verify_signed_summary
    from ui.i18n import t, render, set_current_language
    set_current_language(args.lang)
    try:
        if args.gen_signing_key:
            priv, pub, key_id = generate_keypair(args.gen_signing_key)
            print(t("cli.signing_key_written", args.lang, private=priv, public=pub, key_id=key_id))
            return EXIT_OK
        if not args.public_key:
            print(t("cli.public_key_needed", args.lang))
            return EXIT_INTERNAL
        if args.export_verifier:
            from ui.verifier_page import export_verifier
            path, key_id = export_verifier(args.export_verifier, args.public_key, args.lang)
            print(t("cli.verifier_written", args.lang, path=path, key_id=key_id))
            return EXIT_OK
        if args.verify_qr is not None:
            ok, msg, _ = verify_signed_summary(args.verify_qr, args.public_key)
            print(render(msg, args.lang))
            return EXIT_OK if ok else EXIT_BAD_SIGNATURE
        ok, msg = verify_file(args.verify_report, args.public_key, args.signature)
        print(render(msg, args.lang))
        return EXIT_OK if ok else EXIT_BAD_SIGNATURE
    except SigningError as e:
        print(t("cli.signing_failed", args.lang, error=render(e.msg, args.lang)))
        return EXIT_INTERNAL


def main() -> int:
    """Runs one command or the GUI; our private adb server is stopped at the end if this run started using it."""
    from core import adb_bridge
    adb_bridge.begin_run()
    try:
        return _main()
    finally:
        adb_bridge.stop_private_server_if_used()


def _main() -> int:
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

    if args.gen_signing_key or args.verify_report or args.verify_qr is not None or args.export_verifier:
        return run_signing_command(args)

    if args.analyze_log:
        return run_log_analysis(args.analyze_log, args.lang)

    if args.companion_wifi or args.companion_file:
        return run_companion_without_adb(args)

    if args.uart_list or args.uart_capture:
        return run_uart_command(args)

    if args.screen_check:
        return run_screen_check(args.serial, args.lang)

    if args.cli:
        try:
            key = args.sign_key or os.environ.get("ADP_SIGNING_KEY") or None
            if args.all_devices:
                return run_cli_all_devices(args.lang, args.output_dir, key, pdf=args.pdf,
                                           max_workers=max(1, args.workers), companion=args.companion)
            return run_cli_diagnostics(args.serial, args.lang, args.output_dir, key, pdf=args.pdf,
                                       companion=args.companion)
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
