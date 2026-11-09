import os
import sys
import argparse
import logging
import threading

# Configure clean logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger("AndroidDoctorPro")


def enable_windows_high_dpi():
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def run_cli_diagnostics(serial: str = ""):
    print("\n=======================================================")
    print("      ANDROID DOCTOR PRO — CLI HARDWARE SUITE          ")
    print("=======================================================\n")
    
    # Lazy imports to guarantee fast and fail-safe execution
    from core.adb_bridge import ADBBridge
    from database.db_manager import DatabaseManager
    from core.profiler import DeviceProfiler
    from core.board_diag import MotherboardDiagnostics
    from core.hardware_tester import HardwareTester
    from core.malware_hunter import MalwareHunter
    from ui.report_exporter import DiagnosticReportExporter

    bridge = ADBBridge()
    devices = bridge.list_devices()
    if not devices:
        print("[!] No connected Android devices detected in ADB or Fastboot mode.")
        print("[i] Please connect a device via USB with USB Debugging enabled.")
        return

    # Check device states
    target = None
    if serial:
        for d in devices:
            if d["serial"] == serial:
                target = d
                break
        if not target:
            print(f"[!] Specified serial {serial} not found in connected devices.")
            return
    else:
        # Default to first authorized device, or first device if none authorized
        authorized = [d for d in devices if d.get("state") == "device"]
        target = authorized[0] if authorized else devices[0]

    target_serial = target["serial"]
    target_state = target.get("state", "unknown")
    print(f"[*] Target device: {target_serial} (State: {target_state.upper()})")

    if target_state == "unauthorized":
        print("[!] Device is UNAUTHORIZED: Please unlock your phone screen and allow USB Debugging prompt.")
        return
    elif target_state == "offline":
        print("[!] Device is OFFLINE: Replug the USB cable or check USB port/cable stability.")
        return
    elif target_state == "fastboot":
        print("[i] Device is in FASTBOOT Bootloader Mode. Querying bootloader variables...")
        from core.rescue_center import RescueCenter
        rescue = RescueCenter(bridge)
        fb_vars = rescue.get_fastboot_variables(target_serial)
        print(f"Product: {fb_vars.product} | Bootloader: {fb_vars.bootloader_version} | Unlocked: {fb_vars.is_unlocked}")
        return
    elif target_state != "device":
        print(f"[!] Device in unsupported state: {target_state}")
        return

    db = DatabaseManager()
    try:
        profiler = DeviceProfiler(bridge)
        board_diag = MotherboardDiagnostics(bridge, db)
        hw_tester = HardwareTester(bridge)
        malware_hunter = MalwareHunter(bridge, db)

        print("\n--- 1. PROFILING DEVICE ---")
        profile = profiler.profile_device(target_serial)
        print(f"Device: {profile.brand} {profile.model} (Codename: {profile.device_codename})")
        print(f"OS: Android {profile.android_version} (API {profile.sdk_version}, Security Patch: {profile.security_patch})")
        print(f"SoC: {profile.soc_chipset} ({profile.cpu_cores} Cores, {profile.cpu_architecture})")
        print(f"RAM: {profile.ram_free_mb}MB free / {profile.ram_total_mb}MB total")
        print(f"Storage: {profile.storage_free_gb}GB free / {profile.storage_total_gb}GB total")

        print("\n--- 2. MOTHERBOARD & PMIC DIAGNOSTICS ---")
        bdiag = board_diag.run_full_diagnostics(target_serial)
        print(f"PMIC Voltage: {bdiag.pmic.voltage_mv} mV | Standby Current: {bdiag.pmic.current_ma} mA")
        print(f"Battery Health: {bdiag.pmic.health} | True Capacity Pct: {bdiag.pmic.true_health_pct}% | Temp: {bdiag.pmic.temp_c}°C")
        print(f"Monitored Thermal Zones: {len(bdiag.thermal_zones)}")
        if bdiag.critical_findings:
            print("Hardware Warnings / Faults:")
            for f in bdiag.critical_findings:
                print(f"  [ALERT] {f}")
        else:
            print("Motherboard Health: PASS (All voltages and thermal zones normal)")

        print("\n--- 3. HARDWARE COMPONENTS & SENSORS ---")
        hw = hw_tester.run_full_audit(target_serial)
        print(f"Active I2C Sensors: {len(hw.sensors)}")
        print(f"Camera Sensors: {len(hw.cameras)}")
        if hw.missing_critical_sensors:
            print(f"Missing Sensors: {', '.join(hw.missing_critical_sensors)}")

        print("\n--- 4. SECURITY & MALWARE HUNTER ---")
        sec = malware_hunter.audit_security(target_serial)
        print(f"Scanned: {sec.total_packages_scanned} packages (3rd Party: {sec.third_party_count})")
        print(f"Threat Assessment: {sec.overall_risk}")
        print(f"Critical Threats: {len(sec.critical_threats)} | Ghost Apps: {len(sec.ghost_apps)}")

        print("\n--- 5. EXPORTING OFFICIAL CERTIFICATE ---")
        report_file = DiagnosticReportExporter.generate_html_report(
            device_profile=profile.to_dict(),
            board_diag=bdiag.to_dict(),
            hw_audit=hw.to_dict(),
            sec_scan=sec.to_dict()
        )
        print(f"[✓] Printable Diagnostic Certificate generated at:\n    {report_file}")
        print("\nScan Completed Successfully.")
    finally:
        if hasattr(db, "close"):
            db.close()


def main():
    parser = argparse.ArgumentParser(description="Android Doctor Pro — Diagnostic & Repair Desktop Suite")
    parser.add_argument("--cli", action="store_true", help="Run diagnostics in CLI mode without GUI")
    parser.add_argument("--serial", type=str, default="", help="Target device serial number")
    parser.add_argument("--lang", type=str, default="ar", choices=["ar", "en"], help="Interface language (ar / en)")
    parser.add_argument("--check-bins", action="store_true", help="Verify and download ADB/Fastboot/scrcpy binaries")
    args = parser.parse_args()

    enable_windows_high_dpi()

    from setup_bins import verify_binaries, ensure_all_binaries, get_adb_path, get_fastboot_path, get_scrcpy_path

    # Check local binaries without blocking
    local_adb = get_adb_path()
    local_fastboot = get_fastboot_path()
    local_scrcpy = get_scrcpy_path()
    all_present = os.path.exists(local_adb) and os.path.exists(local_fastboot) and os.path.exists(local_scrcpy)

    if args.check_bins or not all_present:
        logger.info("Ensuring bundled local binaries in bin/...")
        bin_status = ensure_all_binaries()
        logger.info(f"Binaries verified: ADB={bin_status['adb']['available']}, Fastboot={bin_status['fastboot']['available']}, scrcpy={bin_status['scrcpy']['available']}")
        if args.check_bins:
            print("Binary verification complete.")
            return
    else:
        # Instant local check
        bin_status = verify_binaries()
        logger.info(f"Local binaries verified in bin/: ADB={bin_status['adb']['available']}, Fastboot={bin_status['fastboot']['available']}, scrcpy={bin_status['scrcpy']['available']}")

    if args.cli:
        run_cli_diagnostics(args.serial)
        return

    # Launch GUI immediately without network delay
    logger.info(f"Launching Android Doctor Pro GUI (Language: {args.lang})...")
    from ui.main_window import AndroidDoctorApp
    app = AndroidDoctorApp(lang=args.lang)
    app.mainloop()


if __name__ == "__main__":
    main()
