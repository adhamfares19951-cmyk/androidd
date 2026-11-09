"""
Unit and Integration Tests for Android Doctor Pro - Section 0
Verifying:
1. Strict 4-State Verdict System (PASS, FAIL, UNKNOWN, NEEDS_ROOT)
2. Raw Evidence Presence in all modules
3. Accurate Renaming (Fuel gauge, Battery current + note, Reported sensors, Reset battery statistics, Diagnostic Report)
4. Hypotheses explicitly marked as hypotheses for EDL/BROM/Odin/Cycling
5. No fake defaults (no turning missing data into PASS or 100%)
"""

import os
import sys
import unittest
from unittest.mock import MagicMock

# Add project root to path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

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
    DisplayPanelAuthenticity
)
from core.storage_benchmark import StorageBenchmark, StorageHealthReport
from ui.report_exporter import DiagnosticReportExporter
from ui.i18n import STRINGS


class TestVerdictSystemAndNoFakeResults(unittest.TestCase):

    def setUp(self):
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

        self.board_diag = MotherboardDiagnostics(self.mock_bridge)
        self.hw_tester = HardwareTester(self.mock_bridge)
        self.storage_bench = StorageBenchmark(self.mock_bridge)

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
        self.assertIn(pmic.health_verdict, ["UNKNOWN", "NEEDS_ROOT"], "Health verdict cannot be PASS without data")
        self.assertTrue(len(pmic.raw_evidence) > 0, "raw_evidence must be recorded")

    def test_battery_current_note_usb_vs_wifi(self):
        """Verify that USB connections include charging note and Wi-Fi connections indicate pure battery current."""
        self.mock_bridge.read_sysfs.return_value = "4100000"
        self.mock_bridge.read_sysfs_int.return_value = 500000
        self.mock_bridge.read_sysfs_str.return_value = "Good"
        self.mock_bridge.shell.return_value = (0, "level: 80\nvoltage: 4100\ntemperature: 300\n", "")

        # USB Serial (no colon)
        pmic_usb = self.board_diag.analyze_pmic("usb_device_001")
        self.assertIn("includes charging current while USB is connected", pmic_usb.current_note)

        # Wi-Fi Serial (contains colon)
        pmic_wifi = self.board_diag.analyze_pmic("192.168.1.100:5555")
        self.assertIn("Wi-Fi connected (pure battery current without USB charger)", pmic_wifi.current_note)

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
        self.assertIn("Hypothesis (Possible Causes):", report.summary_diagnosis)
        self.assertIn("Possible cause", report.summary_diagnosis)
        for port in report.detected_ports:
            self.assertIn("Possible causes", port["diagnosis"])

    def test_hardware_tester_4_state_verdicts_and_evidence(self):
        """Verify all hardware tester modules return valid 4-state verdicts and non-empty raw_evidence."""
        VALID_STATUSES = {"PASS", "FAIL", "UNKNOWN", "NEEDS_ROOT"}

        # Empty/failing bridge responses to test non-root / missing data behavior
        self.mock_bridge.shell.return_value = (0, "", "")
        self.mock_bridge.read_sysfs_str.return_value = None
        self.mock_bridge.read_sysfs_int.return_value = None
        self.mock_bridge.getprop.return_value = ""

        # 1. Fingerprint
        fp = self.hw_tester.diagnose_fingerprint("test_serial")
        self.assertIn(fp.status, VALID_STATUSES)
        self.assertTrue(len(fp.raw_evidence) > 0, "Fingerprint raw_evidence must not be empty")
        self.assertNotEqual(fp.status, "PASS", "Missing biometric service must not return PASS")

        # 2. Buttons
        btn = self.hw_tester.diagnose_physical_buttons("test_serial")
        self.assertIn(btn.status, VALID_STATUSES)
        self.assertTrue(len(btn.raw_evidence) > 0, "Buttons raw_evidence must not be empty")

        # 3. SIM & Baseband
        sim = self.hw_tester.diagnose_sim_and_baseband("test_serial")
        self.assertIn(sim.status, VALID_STATUSES)
        self.assertTrue(len(sim.raw_evidence) > 0, "SIM raw_evidence must not be empty")

        # 4. Display Backlight
        disp = self.hw_tester.diagnose_display_backlight("test_serial")
        self.assertIn(disp.status, VALID_STATUSES)
        self.assertTrue(len(disp.raw_evidence) > 0, "Display backlight raw_evidence must not be empty")

        # 5. Wi-Fi
        wifi = self.hw_tester.diagnose_wifi("test_serial")
        self.assertIn(wifi.status, VALID_STATUSES)
        self.assertTrue(len(wifi.raw_evidence) > 0, "Wi-Fi raw_evidence must not be empty")

        # 6. Bluetooth
        bt = self.hw_tester.diagnose_bluetooth("test_serial")
        self.assertIn(bt.status, VALID_STATUSES)
        self.assertTrue(len(bt.raw_evidence) > 0, "Bluetooth raw_evidence must not be empty")

        # 7. Peripherals
        peri = self.hw_tester.diagnose_peripherals_chips("test_serial")
        self.assertIn(peri.status, VALID_STATUSES)
        self.assertTrue(len(peri.raw_evidence) > 0, "Peripherals raw_evidence must not be empty")

        # 8. Audio Codec
        codec = self.hw_tester.diagnose_audio_codec("test_serial")
        self.assertIn(codec.status, VALID_STATUSES)
        self.assertTrue(len(codec.raw_evidence) > 0, "Audio codec raw_evidence must not be empty")

        # 9. Display Authenticity
        auth = self.hw_tester.inspect_display_authenticity("test_serial")
        self.assertIn(auth.status, VALID_STATUSES)
        self.assertTrue(len(auth.raw_evidence) > 0, "Display authenticity raw_evidence must not be empty")

    def test_storage_health_unreadable_is_not_pass(self):
        """Verify storage health test without root or missing wear nodes returns NEEDS_ROOT or UNKNOWN, never PASS."""
        self.mock_bridge.read_sysfs_str.return_value = None
        self.mock_bridge.shell.return_value = (0, "", "")

        report = self.storage_bench.diagnose_storage_health("test_serial")
        self.assertIn(report.status, ["NEEDS_ROOT", "UNKNOWN"])
        self.assertNotEqual(report.status, "PASS", "Storage wear cannot PASS when nodes are unreadable")
        self.assertFalse(report.has_real_data, "has_real_data must be False when storage wear is unreadable")
        self.assertTrue(len(report.raw_evidence) > 0)

    def test_report_exporter_renamed_terms_and_evidence(self):
        """Verify HTML report includes renamed terms and expandable evidence drawers, and does not contain obsolete terms."""
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
                    "raw_evidence": "dumpsys battery: level=80 voltage=4120"
                },
                "leakage_test": {
                    "is_faulty": False,
                    "avg_leakage_ma": 450.0,
                    "max_leakage_ma": 460.0,
                    "samples": [450.0],
                    "verdict": "Battery current: Average: 450.0mA"
                },
                "thermal_zones": [],
                "kernel_panics": {"panic_count": 0, "panics": []},
                "overall_health": "UNKNOWN",
                "summary": "Diagnostics run."
            },
            hw_audit={
                "sensors": [{"name": "LSM6DSO Accelerometer", "vendor": "STMicroelectronics", "power_ma": 0.5, "sensor_type": "android.sensor.accelerometer"}],
                "fingerprint": {"status": "UNKNOWN", "raw_evidence": "dumpsys fingerprint: service not available"},
                "buttons": {"status": "UNKNOWN", "raw_evidence": "getevent -p: permission denied"},
                "wifi": {"status": "PASS", "raw_evidence": "dumpsys wifi: Wi-Fi is enabled"},
                "bluetooth": {"status": "PASS", "raw_evidence": "dumpsys bluetooth_manager: state ON"}
            },
            sec_scan={},
            opt_result={},
            storage_report={"status": "NEEDS_ROOT", "raw_evidence": "Permission denied reading /sys/block/mmcblk0/device/life_time"},
            fast_charge={},
            storage_life_report={},
            stress_report={},
            audio_codec={"status": "PASS", "raw_evidence": "/proc/asound/cards: 0 [sm8450]"},
            disp_auth={"status": "PASS", "raw_evidence": "getprop ro.boot.verifiedbootstate: green"},
            root_forensics={},
            technician_name="Senior Engineer",
            ticket_id="TCK-999888",
            lang="en"
        )

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
