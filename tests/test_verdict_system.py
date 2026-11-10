"""
Unit and Integration Tests for Android Doctor Pro - Section 0 & Step 3
Verifying:
1. Strict 4-State Verdict System (PASS, FAIL, UNKNOWN, NEEDS_ROOT)
2. Evidence Tracking with Evidence dataclass in all modules (exit_code default None)
3. status_from_read(stdout, stderr, exit_code, path_exists) checking both stdout and stderr
4. Getevent shell user access without root (general failures return UNKNOWN with evidence, not NEEDS_ROOT)
5. DisplayPanelInfo (check_id: display.panel_info, dmesg permission denied -> NEEDS_ROOT)
6. Full matrix tests covering: normal output, empty output, permission denied (stderr), missing path, malformed output
7. Accurate Renaming (Fuel gauge, Battery current + note, Reported sensors, Reset battery statistics, Diagnostic Report)
8. Hypotheses explicitly marked as hypotheses for EDL/BROM/Odin/Cycling
9. Isolated DB and Report directories (tests never touch real data/ or reports/)
"""

import os
import sys
import unittest
import tempfile
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.result import Status, Evidence, CheckResult, status_from_read, MAX_EXCERPT_CHARS
from core.board_diag import MotherboardDiagnostics, PMICStatus, DeadPhoneBootROMDiagnosis
from core.hardware_tester import (
    HardwareTester,
    FingerprintDiagnostics,
    PhysicalButtonsDiagnostics,
    SIMBasebandDiagnostics,
    WiFiDiagnostics,
    BluetoothDiagnostics,
    PeripheralChipsDiagnostics,
    DisplayBacklightDiagnostics,
    AudioCodecReport,
    DisplayPanelInfo
)
from core.storage_benchmark import StorageBenchmark, StorageHealthReport
from database.db_manager import DatabaseManager
from ui.report_exporter import DiagnosticReportExporter
from ui.i18n import STRINGS, render
from tests.fakes import FakeBridge


class TestVerdictSystemAndNoFakeResults(unittest.TestCase):

    def setUp(self):
        # Isolated temporary directory for tests - real data/ and reports/ are never touched
        self.temp_dir = tempfile.TemporaryDirectory()
        self.test_db_path = os.path.join(self.temp_dir.name, "test_android_doctor.db")
        self.db_manager = DatabaseManager(self.test_db_path)

        self.mock_bridge = MagicMock()
        # Default shell return value is 3-tuple (returncode, stdout, stderr)
        self.mock_bridge.shell.return_value = (0, "", "")
        self.mock_bridge.read_sysfs.return_value = None
        self.mock_bridge.read_sysfs_int.return_value = None
        self.mock_bridge.read_sysfs_str.return_value = None
        self.mock_bridge.getprop.return_value = ""
        self.mock_bridge.get_prop.return_value = ""
        self.mock_bridge.is_root_available.return_value = False
        self.mock_bridge.scan_bootrom_usb_ports.return_value = []
        self.mock_bridge.detect_usb_port_cycling.return_value = {"is_cycling": False, "diagnosis": "Stable"}

        self.board_diag = MotherboardDiagnostics(self.mock_bridge, db=self.db_manager)
        self.hw_tester = HardwareTester(self.mock_bridge)
        self.storage_bench = StorageBenchmark(self.mock_bridge)

    def tearDown(self):
        self.db_manager.close()
        DatabaseManager.reset_instance()
        self.temp_dir.cleanup()

    def test_core_result_contracts(self):
        """Verify the contracts in core/result.py: Status, Evidence, CheckResult, and status_from_read."""
        # Status enum values
        self.assertEqual(Status.PASS.value, "PASS")
        self.assertEqual(Status.FAIL.value, "FAIL")
        self.assertEqual(Status.UNKNOWN.value, "UNKNOWN")
        self.assertEqual(Status.NEEDS_ROOT.value, "NEEDS_ROOT")

        # 1. Evidence.exit_code default must be None, not 0
        ev_def = Evidence(source="test_source", raw_excerpt="output text")
        self.assertIsNone(ev_def.exit_code, "Evidence.exit_code default must be None so unknown exit code never looks like success")

        # Evidence truncation at MAX_EXCERPT_CHARS
        huge_text = "A" * (MAX_EXCERPT_CHARS + 500)
        ev = Evidence(source="test_source", raw_excerpt=huge_text, exit_code=0)
        self.assertLessEqual(len(ev.raw_excerpt), MAX_EXCERPT_CHARS + 30)
        self.assertTrue(ev.raw_excerpt.endswith("... [truncated]"))
        self.assertEqual(ev.source, "test_source")
        self.assertEqual(ev.exit_code, 0)
        ev_dict = ev.to_dict()
        self.assertIn("source", ev_dict)
        self.assertIn("raw_excerpt", ev_dict)
        self.assertIn("exit_code", ev_dict)

        # CheckResult has_real_data contract: only PASS or FAIL
        cr_pass = CheckResult(check_id="chk_pass", status=Status.PASS, value=4.2)
        self.assertTrue(cr_pass.has_real_data)

        cr_fail = CheckResult(check_id="chk_fail", status=Status.FAIL, value=0.0)
        self.assertTrue(cr_fail.has_real_data)

        cr_unknown = CheckResult(check_id="chk_unknown", status=Status.UNKNOWN)
        self.assertFalse(cr_unknown.has_real_data)

        cr_root = CheckResult(check_id="chk_root", status=Status.NEEDS_ROOT)
        self.assertFalse(cr_root.has_real_data)

        # CheckResult add_evidence and serialization
        cr_pass.add_evidence(source="sysfs", raw_excerpt="ok", exit_code=0)
        self.assertEqual(len(cr_pass.evidence), 1)
        self.assertIsInstance(cr_pass.evidence[0], Evidence)
        serialized = cr_pass.to_dict()
        self.assertEqual(serialized["status"], "PASS")
        self.assertTrue(serialized["has_real_data"])
        self.assertEqual(len(serialized["evidence"]), 1)
        self.assertEqual(serialized["evidence"][0]["source"], "sysfs")

    def test_status_from_read_stdout_and_stderr(self):
        """2. status_from_read receives stdout AND stderr; permission errors in stderr must return NEEDS_ROOT."""
        # Permission denied in stderr alone
        self.assertEqual(
            status_from_read(stdout="", stderr="cat: /sys/block/mmcblk0/device/life_time: Permission denied", exit_code=1),
            Status.NEEDS_ROOT,
            "Permission denied in stderr must yield NEEDS_ROOT"
        )

        # avc: denied in stderr
        self.assertEqual(
            status_from_read(stdout="", stderr="type=1400 audit: avc: denied { read } for comm=\"cat\"", exit_code=1),
            Status.NEEDS_ROOT,
            "SELinux avc: denied in stderr must yield NEEDS_ROOT"
        )

        # Operation not permitted in stderr
        self.assertEqual(
            status_from_read(stdout="", stderr="Operation not permitted", exit_code=1),
            Status.NEEDS_ROOT
        )

        # Permission denied in stdout
        self.assertEqual(
            status_from_read(stdout="Permission denied", stderr="", exit_code=1),
            Status.NEEDS_ROOT
        )

        # Path not existing
        self.assertEqual(status_from_read(path_exists=False), Status.UNKNOWN)

        # Empty stdout and empty stderr
        self.assertEqual(status_from_read(stdout="", stderr="", exit_code=0), Status.UNKNOWN)

        # None values
        self.assertEqual(status_from_read(stdout=None, stderr=None), Status.UNKNOWN)

        # Normal valid output returns UNKNOWN until parsed by caller
        self.assertEqual(status_from_read(stdout="4120000", stderr="", exit_code=0), Status.UNKNOWN)

        # Non-zero exit code without permission error (e.g. file not found)
        self.assertEqual(
            status_from_read(stdout="", stderr="cat: /sys/fake/node: No such file or directory", exit_code=1),
            Status.UNKNOWN
        )

    def test_pmic_missing_data_never_fakes_pass_or_values(self):
        """Verify that when sysfs is unreadable, physical values are None/UNKNOWN and health is UNKNOWN or NEEDS_ROOT, never PASS."""
        self.mock_bridge.read_sysfs.return_value = None
        self.mock_bridge.read_sysfs_int.return_value = None
        self.mock_bridge.read_sysfs_str.return_value = None
        self.mock_bridge.shell.return_value = (0, "", "")

        pmic = self.board_diag.analyze_pmic("test_serial_123")

        self.assertIsNone(pmic.voltage_mv, "Voltage must be None when unreadable, not 0 or fake mV")
        self.assertEqual(pmic.voltage_source, "UNKNOWN")
        self.assertIsNone(pmic.current_ma, "Current must be None when unreadable")
        self.assertEqual(pmic.current_source, "UNKNOWN")
        self.assertIsNone(pmic.temp_c, "Temperature must be None when unreadable")
        self.assertEqual(pmic.temp_source, "UNKNOWN")
        self.assertIsNone(pmic.true_health_pct, "Health pct must be None when capacity nodes are missing")
        self.assertIn(pmic.health_verdict, ["UNKNOWN", "NEEDS_ROOT", Status.UNKNOWN, Status.NEEDS_ROOT], "Health verdict cannot be PASS without data")
        self.assertTrue(len(pmic.evidence) > 0, "evidence list must be populated")
        self.assertTrue(all(isinstance(e, Evidence) for e in pmic.evidence), "Every item in evidence must be an Evidence instance")

    def test_battery_current_note_usb_vs_wifi(self):
        """Verify that USB connections include charging note and Wi-Fi connections indicate pure battery current."""
        self.mock_bridge.read_sysfs.return_value = "4100000"
        self.mock_bridge.read_sysfs_int.return_value = 500000
        self.mock_bridge.read_sysfs_str.return_value = "Good"
        self.mock_bridge.shell.return_value = (0, "level: 80\nvoltage: 4100\ntemperature: 300\n", "")

        # USB Serial (no colon)
        pmic_usb = self.board_diag.analyze_pmic("usb_device_001")
        self.assertEqual(pmic_usb.current_note.key, "battery.current_note.usb")
        self.assertIn("charging current while USB is connected", render(pmic_usb.current_note, "en"))

        # Wi-Fi Serial (contains colon)
        pmic_wifi = self.board_diag.analyze_pmic("192.168.1.100:5555")
        self.assertEqual(pmic_wifi.current_note.key, "battery.current_note.wifi")
        self.assertIn("no USB charging current", render(pmic_wifi.current_note, "en"))

    def test_bootrom_hypotheses_labeled_as_hypotheses(self):
        """Verify that dead phone diagnostic verdicts are structured hypotheses with possible causes."""
        self.mock_bridge.scan_bootrom_usb_ports.return_value = [{
            "mode": "Qualcomm EDL 9008",
            "name": "Qualcomm HS-USB QDLoader 9008 (COM5)",
            "status": "Emergency Download",
            "diagnosis": "Possible causes: Bootloader or NAND corruption"
        }]
        report = self.board_diag.diagnose_dead_phone_bootrom(check_cycling=False)
        self.assertTrue(report.is_qualcomm_edl)
        self.assertEqual(report.summary_diagnosis.key, "bootrom.qualcomm_edl")
        self.assertTrue(render(report.summary_diagnosis, "en").startswith("Hypothesis"))
        self.assertGreaterEqual(len(report.possible_causes), 3, "Possible causes must be listed")
        for port in report.detected_ports:
            self.assertIn("Possible causes", port["diagnosis"])

    def test_hardware_tester_4_state_verdicts_and_evidence(self):
        """Verify all hardware tester modules return valid 4-state verdicts and non-empty evidence list."""
        VALID_STATUSES = {"PASS", "FAIL", "UNKNOWN", "NEEDS_ROOT"}

        # Empty/failing bridge responses to test non-root / missing data behavior
        self.mock_bridge.shell.return_value = (0, "", "")
        self.mock_bridge.read_sysfs_str.return_value = None
        self.mock_bridge.read_sysfs_int.return_value = None
        self.mock_bridge.getprop.return_value = ""

        # 1. Fingerprint
        fp = self.hw_tester.diagnose_fingerprint("test_serial")
        self.assertIn(fp.status, VALID_STATUSES)
        self.assertTrue(len(fp.evidence) > 0, "Fingerprint evidence must not be empty")
        self.assertIsInstance(fp.evidence[0], Evidence)
        self.assertNotEqual(fp.status, "PASS", "Missing biometric service must not return PASS")

        # 2. Buttons
        btn = self.hw_tester.diagnose_physical_buttons("test_serial")
        self.assertIn(btn.status, VALID_STATUSES)
        self.assertTrue(len(btn.evidence) > 0, "Buttons evidence must not be empty")
        self.assertIsInstance(btn.evidence[0], Evidence)

        # 3. SIM & Baseband
        sim = self.hw_tester.diagnose_sim_and_baseband("test_serial")
        self.assertIn(sim.status, VALID_STATUSES)
        self.assertTrue(len(sim.evidence) > 0, "SIM evidence must not be empty")
        self.assertIsInstance(sim.evidence[0], Evidence)

        # 4. Display Backlight
        disp = self.hw_tester.diagnose_display_backlight("test_serial")
        self.assertIn(disp.status, VALID_STATUSES)
        self.assertTrue(len(disp.evidence) > 0, "Display backlight evidence must not be empty")
        self.assertIsInstance(disp.evidence[0], Evidence)

        # 5. Wi-Fi
        wifi = self.hw_tester.diagnose_wifi("test_serial")
        self.assertIn(wifi.status, VALID_STATUSES)
        self.assertTrue(len(wifi.evidence) > 0, "Wi-Fi evidence must not be empty")
        self.assertIsInstance(wifi.evidence[0], Evidence)

        # 6. Bluetooth
        bt = self.hw_tester.diagnose_bluetooth("test_serial")
        self.assertIn(bt.status, VALID_STATUSES)
        self.assertTrue(len(bt.evidence) > 0, "Bluetooth evidence must not be empty")
        self.assertIsInstance(bt.evidence[0], Evidence)

        # 7. Peripherals
        peri = self.hw_tester.diagnose_peripherals_chips("test_serial")
        self.assertIn(peri.status, VALID_STATUSES)
        self.assertTrue(len(peri.evidence) > 0, "Peripherals evidence must not be empty")
        self.assertIsInstance(peri.evidence[0], Evidence)

        # 8. Audio Codec
        codec = self.hw_tester.diagnose_audio_codec("test_serial")
        self.assertIn(codec.status, VALID_STATUSES)
        self.assertTrue(len(codec.evidence) > 0, "Audio codec evidence must not be empty")
        self.assertIsInstance(codec.evidence[0], Evidence)

        # 9. Display Panel Info (renamed from DisplayPanelAuthenticity)
        auth = self.hw_tester.inspect_display_panel_info("test_serial")
        self.assertEqual(auth.check_id, "display.panel_info")
        self.assertIn(auth.status, VALID_STATUSES)
        self.assertTrue(len(auth.evidence) > 0, "Display panel info evidence must not be empty")
        self.assertIsInstance(auth.evidence[0], Evidence)

    def test_getevent_shell_access_and_errors(self):
        """3. getevent works without root. General failure -> UNKNOWN with evidence. Stderr permission denied -> NEEDS_ROOT."""
        # Case A: all three keys pressed and released during capture, without root -> PASS
        all_keys = "\n".join(
            f"/dev/input/event1: EV_KEY {k} {s}"
            for k in ("KEY_VOLUMEUP", "KEY_VOLUMEDOWN", "KEY_POWER") for s in ("DOWN", "UP")
        )
        btn_pass = HardwareTester(FakeBridge({"getevent": (124, all_keys, "")})).diagnose_physical_buttons("serial_1")
        self.assertEqual(btn_pass.status, "PASS")

        # Case A2: only Power pressed -> Power key PASS, the untested volume keys keep the overall result UNKNOWN
        only_power = "/dev/input/event1: EV_KEY KEY_POWER DOWN\n/dev/input/event1: EV_KEY KEY_POWER UP\n"
        btn_power = HardwareTester(FakeBridge({"getevent": (0, only_power, "")})).diagnose_physical_buttons("serial_1")
        self.assertEqual(btn_power.power_status, "PASS")
        self.assertEqual(btn_power.status, "UNKNOWN")

        # Case B: getevent timeout / empty without permission error -> UNKNOWN (NOT NEEDS_ROOT!)
        btn_unknown = HardwareTester(FakeBridge({"getevent": (124, "", "")})).diagnose_physical_buttons("serial_1")
        self.assertEqual(btn_unknown.status, "UNKNOWN", "Non-permission getevent failure must return UNKNOWN, not NEEDS_ROOT")
        self.assertTrue(len(btn_unknown.evidence) > 0)

        # Case C: Actual permission error in stderr -> NEEDS_ROOT
        btn_root = HardwareTester(FakeBridge({
            "getevent": (1, "", "getevent: could not open /dev/input/event0: Permission denied")
        })).diagnose_physical_buttons("serial_1")
        self.assertEqual(btn_root.status, "NEEDS_ROOT", "Permission error in stderr must return NEEDS_ROOT")

    def test_display_panel_info_status_derivation(self):
        """4. DisplayPanelInfo: no fake original claim. If dmesg is blocked by permissions -> NEEDS_ROOT."""
        props = {"verifiedbootstate": (0, "green", ""), "vbmeta.device_state": (0, "locked", "")}

        # Case A: dmesg blocked by SELinux / permission denied in stderr -> NEEDS_ROOT
        panel_root = HardwareTester(FakeBridge({
            **props,
            "dmesg": (1, "", "dmesg: klogctl: Permission denied"),
            "msm_fb_panel_info": (1, "", "No such file or directory"),
        })).inspect_display_panel_info("serial_1")
        self.assertEqual(panel_root.check_id, "display.panel_info")
        self.assertEqual(panel_root.status, "NEEDS_ROOT", "Permission denied on dmesg must return NEEDS_ROOT")
        self.assertNotIn("OEM Verified", render(panel_root.diagnosis, "en"), "Must not claim OEM authenticity")

        # Case B: Panel controller identified from dmesg -> PASS
        panel_pass = HardwareTester(FakeBridge({
            **props,
            "dmesg": (0, "[ 1.234] dsi_panel: probe novatek nt36672 active", ""),
        })).inspect_display_panel_info("serial_1")
        self.assertEqual(panel_pass.status, "PASS")
        self.assertEqual(panel_pass.panel_controller_name, "nt36672")

        # Case C: Unexposed panel controller without permission error -> UNKNOWN
        panel_unk = HardwareTester(FakeBridge({
            **props,
            "dmesg": (0, "normal boot logs without panel tags", ""),
            "msm_fb_panel_info": (1, "", "No such file"),
        })).inspect_display_panel_info("serial_1")
        self.assertEqual(panel_unk.status, "UNKNOWN", "Unexposed panel info must return UNKNOWN")

    def test_storage_health_unreadable_is_not_pass(self):
        """Verify storage health test without root or missing wear nodes returns NEEDS_ROOT or UNKNOWN, never PASS."""
        self.mock_bridge.read_sysfs_str.return_value = None
        self.mock_bridge.shell.return_value = (0, "", "")

        report = self.storage_bench.diagnose_storage_health("test_serial")
        self.assertIn(report.status, ["NEEDS_ROOT", "UNKNOWN", Status.NEEDS_ROOT, Status.UNKNOWN])
        self.assertNotEqual(report.status, "PASS", "Storage wear cannot PASS when nodes are unreadable")
        self.assertFalse(report.has_real_data, "has_real_data must be False when storage wear is unreadable")
        self.assertTrue(len(report.evidence) > 0, "Storage evidence must not be empty")
        self.assertIsInstance(report.evidence[0], Evidence)

    def test_status_from_read_coverage_matrix(self):
        """Step 3: Test checks covering normal output, empty output, permission denied (stderr), missing path, malformed."""
        base = {
            "ls -d /sys/block/mmcblk0": (0, "/sys/block/mmcblk0", ""),
            "mmcblk0/device/name": (0, "KLMBG4GE", ""),
            "mmcblk0/size": (0, "61071360", ""),
        }

        def storage(life, eol, ufs):
            bridge = FakeBridge({**base, "device/life_time": life, "pre_eol_info": eol, "health_descriptor": ufs})
            return StorageBenchmark(bridge).diagnose_storage_health("serial_x")

        # 1. Storage health with Permission Denied in stderr -> NEEDS_ROOT
        rep_root = storage((1, "", "cat: /sys/block/mmcblk0/device/life_time: Permission denied"),
                           (1, "", "cat: /sys/block/mmcblk0/device/pre_eol_info: Permission denied"),
                           (1, "", "dump_device_desc: Permission denied"))
        self.assertEqual(rep_root.status, "NEEDS_ROOT", "Permission denied in stderr must yield NEEDS_ROOT")

        # 2. Storage health with normal wear output -> PASS
        rep_pass = storage((0, "0x01 0x01", ""), (0, "0x01", ""), (1, "", "No such file"))
        self.assertEqual(rep_pass.status, "PASS", "Valid parsed wear data must yield PASS")
        self.assertTrue(rep_pass.has_real_data)

        # 3. Storage health with missing nodes without permission error -> UNKNOWN
        rep_unk = storage((1, "", "cat: /sys/block/mmcblk0/device/life_time: No such file or directory"),
                          (1, "", "cat: /sys/block/mmcblk0/device/pre_eol_info: No such file or directory"),
                          (1, "", "dump_device_desc: No such file or directory"))
        self.assertEqual(rep_unk.status, "UNKNOWN", "Missing wear nodes without permission error must yield UNKNOWN")
        self.assertFalse(rep_unk.has_real_data)

        # 4. Storage health with malformed wear output -> UNKNOWN
        rep_mal = storage((0, "invalid_gibberish_hex", ""), (0, "corrupt_data", ""), (1, "", "No such file"))
        self.assertEqual(rep_mal.status, "UNKNOWN", "Malformed wear data must yield UNKNOWN")

    def test_report_exporter_renamed_terms_and_evidence(self):
        """Verify HTML report includes renamed terms and expandable evidence drawers, and does not touch real reports/ directory."""
        report_html_path = DiagnosticReportExporter.generate_html_report(
            device_profile={"model": "Pixel 7", "serial": "TEST1234"},
            board_diag={
                "pmic": {
                    "voltage_mv": 4120.0,
                    "voltage_source": "/sys/class/power_supply/battery/voltage_now",
                    "current_ma": -450.0,
                    "current_source": "/sys/class/power_supply/battery/current_now",
                    "current_note": "includes charging current while USB is connected",
                    "temp_c": 31.5,
                    "temp_source": "/sys/class/power_supply/battery/temp",
                    "status": "Charging",
                    "health": "Good",
                    "true_health_pct": None,
                    "health_verdict": "NEEDS_ROOT",
                    "charge_full_design_uah": 4500000,
                    "cycle_count": None,
                    "fault_flags": [],
                    "evidence": [Evidence(source="dumpsys battery", raw_excerpt="level=80 voltage=4120", exit_code=0)]
                },
                "leakage_test": {
                    "is_faulty": False,
                    "avg_leakage_ma": 450.0,
                    "max_leakage_ma": 460.0,
                    "samples": [450.0],
                    "verdict": "Battery current: Average: 450.0mA"
                },
                "thermal_zones": [],
                "panic_analysis": {
                    "panic_count": 0,
                    "panics": [],
                    "summary_verdict": "Clean",
                    "blackbox": {"evidence": [Evidence(source="/sys/fs/pstore", raw_excerpt="console-ramoops-0: empty", exit_code=0)]}
                },
                "overall_health": "UNKNOWN",
                "summary": "Diagnostics run."
            },
            hw_audit={
                "sensors": [{"name": "LSM6DSO Accelerometer", "vendor": "STMicroelectronics", "power_ma": 0.5, "sensor_type": "android.sensor.accelerometer"}],
                "fingerprint": {"status": "UNKNOWN", "evidence": [Evidence(source="dumpsys fingerprint", raw_excerpt="service not available", exit_code=1)]},
                "buttons": {"status": "UNKNOWN", "evidence": [Evidence(source="getevent -p", raw_excerpt="permission denied", exit_code=1)]},
                "wifi": {"status": "PASS", "evidence": [Evidence(source="dumpsys wifi", raw_excerpt="Wi-Fi is enabled", exit_code=0)]},
                "bluetooth": {"status": "PASS", "evidence": [Evidence(source="dumpsys bluetooth_manager", raw_excerpt="state ON", exit_code=0)]},
                "display_panel_info": {"status": "PASS", "panel_controller_name": "nt36672", "driver_source": "dmesg", "verified_boot_state": "green", "evidence": [Evidence(source="dmesg", raw_excerpt="novatek", exit_code=0)]},
                "evidence": [
                    Evidence(source="dumpsys wifi", raw_excerpt="Wi-Fi is enabled", exit_code=0),
                    Evidence(source="dumpsys bluetooth_manager", raw_excerpt="state ON", exit_code=0)
                ]
            },
            sec_scan={},
            opt_result={},
            storage_report={"status": "NEEDS_ROOT", "evidence": [Evidence(source="/sys/block/mmcblk0/device/life_time", raw_excerpt="Permission denied", exit_code=1)]},
            fast_charge={},
            storage_life_report={},
            stress_report={},
            audio_codec={"status": "PASS", "evidence": [Evidence(source="/proc/asound/cards", raw_excerpt="0 [sm8450]", exit_code=0)]},
            panel_info={"status": "PASS", "evidence": [Evidence(source="getprop ro.boot.verifiedbootstate", raw_excerpt="green", exit_code=0)]},
            root_forensics={},
            technician_name="Senior Engineer",
            ticket_id="TCK-999888",
            lang="en",
            output_dir=self.temp_dir.name
        )

        # Verify output was created inside the isolated temp directory
        self.assertTrue(report_html_path.startswith(self.temp_dir.name), "Report must be saved inside temporary test directory")
        self.assertTrue(os.path.exists(report_html_path), "Generated HTML report must exist on disk")

        with open(report_html_path, "r", encoding="utf-8") as f:
            content = f.read()

        # Check required new terms
        self.assertIn("Diagnostic Report", content)
        self.assertIn("Battery voltage (fuel gauge)", content)
        self.assertIn("Battery current", content)
        self.assertIn("Reported sensors", content)
        self.assertIn("Show Evidence", content)
        self.assertIn("evidence-box", content)

        # Check obsolete terms are NOT present
        self.assertNotIn("Official Certificate", content)
        self.assertNotIn("PMIC Voltage", content)
        self.assertNotIn("Standby Current", content)
        self.assertNotIn("Active I2C Sensors", content)

    def test_i18n_does_not_contain_obsolete_terms(self):
        """Verify i18n dictionaries in Arabic and English use the required new terms."""
        en = STRINGS["en"]
        ar = STRINGS["ar"]

        # English checks
        self.assertNotIn("Official Certificate", en.values())
        self.assertNotIn("Active I2C Sensors", en.values())
        self.assertEqual(en.get("btn_reset_gauge"), "Reset Battery Statistics")
        self.assertIn("Battery voltage (fuel gauge)", en.get("board_sub", "") + en.get("card_pmic", ""))
        self.assertIn("Reported sensors", en.get("card_sensors", ""))

        # Arabic checks
        self.assertEqual(ar.get("btn_reset_gauge"), "إعادة ضبط إحصائيات استهلاك البطارية")
        self.assertIn("مقياس الوقود", ar.get("board_title", "") + ar.get("card_pmic", "") + ar.get("board_sub", ""))
        self.assertIn("المستشعرات المسجلة", ar.get("card_sensors", "") + ar.get("hw_sub", ""))


if __name__ == "__main__":
    unittest.main()
