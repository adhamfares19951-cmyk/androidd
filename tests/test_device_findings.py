"""
Issues found on a real phone (Redmi Note 9 M2003J15SC, MT6768, Android 12, not rooted, USB).
The command replies below are the phone's own output. Each test failed on the code before its fix.
"""
import io
import logging
import os
import re
from html import escape as html_escape
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main as app_main
from core.board_diag import MotherboardDiagnostics
from core.hardware_tester import HardwareTester
from database.db_manager import DatabaseManager
from tests.fakes import FakeBridge, PERMISSION
from ui.i18n import render, t

UNKNOWN_CMD = (255, "", "Unknown command: has-system-feature")

# dumpsys thermalservice on the Redmi: the power-amplifier sensor reports -26.0 while the phone is at 31-55 °C.
REDMI_THERMAL = (
    "HAL Ready: true\nCurrent temperatures from HAL:\n"
    "\tTemperature{mValue=47.7, mType=0, mName=CPU, mStatus=0}\n"
    "\tTemperature{mValue=36.0, mType=2, mName=BATTERY, mStatus=0}\n"
    "\tTemperature{mValue=41.0, mType=3, mName=SKIN, mStatus=0}\n"
    "\tTemperature{mValue=-26.0, mType=5, mName=POWER_AMPLIFIER, mStatus=0}\n"
)


class TestDeclaredFeatures(unittest.TestCase):
    """`pm has-system-feature` does not exist (exit 255). The command is `pm has-feature`: true -> exit 0, false -> exit 1."""

    def phone(self):
        return FakeBridge({
            "has-system-feature": UNKNOWN_CMD,
            "pm has-feature android.hardware.location.gps": (0, "true", ""),
            "pm has-feature android.hardware.nfc": (1, "false", ""),
            "pm has-feature android.hardware.usb.host": (0, "true", ""),
            "pm has-feature android.hardware.telephony": (0, "true", ""),
            "pm has-feature android.hardware.fingerprint": (0, "true", ""),
            "pm has-feature android.hardware.bluetooth": (0, "true", ""),
        })

    def test_declared_features_are_read_including_false(self):
        d = HardwareTester(self.phone()).diagnose_peripherals_chips("s")
        self.assertIs(d.gps_present, True)
        self.assertIs(d.nfc_present, False)          # "false" with exit 1 is an answer, not an unreadable node
        self.assertIs(d.usb_otg_supported, True)
        self.assertEqual(d.nfc_status, "NOT_DECLARED")
        self.assertEqual(d.status, "UNKNOWN")        # declared features are never PASS/FAIL

    def test_telephony_feature_false_is_false(self):
        b = self.phone().route("pm has-feature android.hardware.telephony", (1, "false", ""))
        self.assertIs(HardwareTester(b).diagnose_sim_and_baseband("s").has_telephony_feature, False)

    def test_no_check_uses_the_nonexistent_command(self):
        b = self.phone()
        tester = HardwareTester(b)
        tester.diagnose_peripherals_chips("s")
        tester.diagnose_sim_and_baseband("s")
        tester.diagnose_fingerprint("s")
        tester.diagnose_bluetooth("s")
        self.assertFalse([c for c in b.commands if "has-system-feature" in c])
        for feature in ("location.gps", "telephony", "fingerprint", "bluetooth"):
            self.assertTrue(any(c.startswith(f"pm has-feature android.hardware.{feature}") for c in b.commands), feature)

    def test_unknown_command_is_no_value(self):
        b = FakeBridge({"pm has-feature": (255, "", "Unknown command: has-feature")})
        d = HardwareTester(b).diagnose_peripherals_chips("s")
        self.assertIsNone(d.gps_present)
        self.assertIsNone(d.nfc_present)
        self.assertEqual(d.status, "UNKNOWN")


class _Db(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.db.close()
        DatabaseManager.reset_instance()
        self.tmp.cleanup()

    def report(self, lang="en", **kw):
        from ui.report_exporter import DiagnosticReportExporter
        path = DiagnosticReportExporter.generate_html_report(device_profile={"serial": "S"}, output_dir=self.tmp.name,
                                                             lang=lang, **kw)
        with open(path, encoding="utf-8") as f:
            return f.read()


DENIED_BATTERY = FakeBridge({"/sys/class/power_supply/": PERMISSION,
                             "dumpsys battery": (0, "  voltage: 3874\n  temperature: 361\n  level: 32", "")})


class TestBlockedBatteryReadings(_Db):
    """Every node under /sys/class/power_supply/battery is 'Permission denied' on the phone."""

    def test_cycle_count_is_needs_root(self):
        p = MotherboardDiagnostics(DENIED_BATTERY, self.db).analyze_pmic("s")
        self.assertIsNone(p.cycle_count)
        self.assertEqual(p.cycle_status, "NEEDS_ROOT")

    def test_report_shows_needs_root_for_current_and_cycles(self):
        pmic = MotherboardDiagnostics(DENIED_BATTERY, self.db).analyze_pmic("s").to_dict()
        html = self.report(board_diag={"pmic": pmic})
        for key in ("report.battery_current", "report.cycle_count"):
            row = re.search(re.escape(t(key, "en")) + r"</span><span class=\"prop-val\">(.*?)</span></div>", html)
            self.assertIsNotNone(row, key)
            self.assertIn(">NEEDS_ROOT<", row.group(1), key)
            self.assertNotIn("(UNKNOWN)", row.group(1), key)   # the internal source placeholder is not shown

    def test_cli_battery_current_line_shows_needs_root(self):
        b = FakeBridge({"/sys/class/power_supply/": PERMISSION})
        b.devices = [{"serial": "A1", "state": "device"}]
        out = io.StringIO()
        with patch("core.adb_bridge.ADBBridge", lambda: b), \
                patch("database.db_manager.DatabaseManager", lambda *a, **k: self.db), redirect_stdout(out):
            app_main.run_cli_diagnostics("", "en", output_dir=self.tmp.name)
        line = next(l for l in out.getvalue().splitlines() if l.startswith("Battery current:"))
        self.assertIn("NEEDS_ROOT", line)
        self.assertNotIn("[UNKNOWN]", line)


class TestImplausibleThermalReading(_Db):
    def test_minus_26_is_listed_but_not_judged(self):
        rep = MotherboardDiagnostics(FakeBridge({"dumpsys thermalservice": (0, REDMI_THERMAL, "")}), self.db).read_thermal("s")
        pa = next(z for z in rep.zones if z.zone_type == "POWER_AMPLIFIER")
        self.assertEqual(pa.temp_c, -26.0)
        self.assertIsNone(pa.limit_c)
        self.assertFalse(pa.plausible)
        self.assertEqual(rep.status, "PASS")       # CPU, battery and skin are real readings within their limits

    def test_only_an_implausible_zone_is_not_a_pass(self):
        text = "Temperature{mValue=-26.0, mType=5, mName=POWER_AMPLIFIER, mStatus=0}"
        rep = MotherboardDiagnostics(FakeBridge({"dumpsys thermalservice": (0, text, "")}), self.db).read_thermal("s")
        self.assertEqual(rep.status, "UNKNOWN")

    def test_report_says_implausible(self):
        rep = MotherboardDiagnostics(FakeBridge({"dumpsys thermalservice": (0, REDMI_THERMAL, "")}), self.db).read_thermal("s")
        html = self.report(board_diag={"thermal_zones": [z.to_dict() for z in rep.zones]})
        row = re.search(r"<tr><td>POWER_AMPLIFIER</td>.*?</tr>", html).group(0)
        self.assertIn(t("report.zone_implausible", "en"), row)


class TestAudioCardsUnreadable(unittest.TestCase):
    def test_denied_card_list_is_not_reported_as_zero(self):
        af = "Output thread 0x1 type 0\nOutput thread 0x2 type 0\nOutput thread 0x3 type 0\nOutput thread 0x4 type 0\n"
        b = FakeBridge({"dumpsys media.audio_flinger": (0, af, ""),
                        "/proc/asound/cards": (1, "", "cat: /proc/asound/cards: Permission denied"),
                        "/proc/asound/pcm": (1, "", "cat: /proc/asound/pcm: Permission denied")})
        r = HardwareTester(b).diagnose_audio_codec("s")
        self.assertEqual(r.status, "PASS")                   # the audio service reports 4 open outputs
        text = render(r.diagnosis, "en")
        self.assertNotIn("0 sound card", text)
        self.assertIn("NEEDS_ROOT", text)


def fp_crash(pid, path="/vendor/bin/hw/android.hardware.biometrics.fingerprint@2.1-service"):
    """One crash record as the phone's `logcat -b crash` prints it."""
    return "\n".join(
        f"11-14 11:10:44.187  {pid + 5}  {pid + 5} F DEBUG   : {line}" for line in (
            "*** *** *** *** *** *** *** *** *** *** *** *** *** *** *** ***",
            "Process uptime: 0s",
            f"Cmdline: {path}",
            f"pid: {pid}, tid: {pid}, name: android.hardwar  >>> {path} <<<",
            "signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x0",
            "Cause: null pointer dereference",
        ))


class TestFingerprintHalCrashLoop(unittest.TestCase):
    """The phone's fingerprint flex cable is cut: the HAL crashes at start every 5 s (SIGSEGV), yet
    `dumpsys fingerprint` still lists the sensor and the saved prints, which used to give PASS."""
    DUMPSYS = (0, 'Fingerprint21 {"service":"[FingerprintService]Fingerprint21","prints":[{"id":0,"count":2}]} sensorId: 0', "")

    def check(self, crash_log):
        b = FakeBridge({"pm has-feature android.hardware.fingerprint": (0, "true", ""),
                        "dumpsys fingerprint": self.DUMPSYS,
                        "logcat -b crash": (0, crash_log, "")})
        return HardwareTester(b).diagnose_fingerprint("s")

    def test_repeated_hal_crashes_are_a_fail(self):
        fp = self.check("\n".join(fp_crash(p) for p in (8749, 8771, 8790)))
        self.assertEqual(fp.status, "FAIL")
        self.assertFalse(fp.is_functional)
        self.assertEqual(fp.error_status, "HAL_CRASH_LOOP")
        self.assertTrue(fp.possible_causes)
        self.assertIn("3", render(fp.diagnosis, "en"))
        self.assertTrue(any("SIGSEGV" in e.raw_excerpt and "fingerprint@2.1-service" in e.raw_excerpt for e in fp.evidence))

    def test_one_crash_is_not_a_fail(self):
        self.assertEqual(self.check(fp_crash(8749)).status, "PASS")

    def test_other_process_crashing_is_not_a_fingerprint_fail(self):
        log = "\n".join(fp_crash(p, "/system/bin/cameraserver") for p in (1, 2, 3))
        self.assertEqual(self.check(log).status, "PASS")

    def test_unreadable_crash_log_keeps_the_old_verdict(self):
        b = FakeBridge({"pm has-feature android.hardware.fingerprint": (0, "true", ""),
                        "dumpsys fingerprint": self.DUMPSYS,
                        "logcat -b crash": (1, "", "logcat: permission denied")})
        self.assertEqual(HardwareTester(b).diagnose_fingerprint("s").status, "PASS")


class TestStressLoopsAlwaysEnd(_Db):
    """On the phone the 4 busy loops kept running at 100% CPU after the test: `$!` was the `timeout`
    process, and killing it at the end orphaned the loop inside it with no time limit left."""

    def run_stress(self, bridge):
        with patch("core.board_diag.time.sleep"):
            return MotherboardDiagnostics(bridge, self.db).run_cpu_pmic_stress_test("s", duration_sec=2)

    def test_loops_end_by_themselves_and_are_killed_by_name(self):
        b = FakeBridge({"[a]dp_stress_loop": (0, "0", "")})
        self.run_stress(b)
        launch = next(c for c in b.commands if "while" in c)
        self.assertNotIn("timeout ", launch)                      # no wrapper whose death orphans the loop
        self.assertIn("date +%s", launch)                         # each loop checks its own deadline
        self.assertIn("adp_stress_loop", launch)
        cleanup = [c for c in b.commands if "pkill" in c]
        self.assertTrue(cleanup)
        self.assertIn("[a]dp_stress_loop", cleanup[0])            # the pattern cannot match the cleanup shell itself

    def test_loops_left_running_are_reported(self):
        b = FakeBridge({"[a]dp_stress_loop": (0, "2", "")})
        rep = self.run_stress(b)
        self.assertTrue(any("2 stress loop" in e.raw_excerpt for e in rep.evidence))


class TestDeepScanDialogShowsWhyNotRead(unittest.TestCase):
    """The deep-scan dialog said "Battery capacity vs design: UNKNOWN%" on the phone (blocked: NEEDS_ROOT)."""

    def test_value_or_status(self):
        from ui.ui_helpers import value_or_status
        self.assertEqual(value_or_status(None, "%", "NEEDS_ROOT"), "NEEDS_ROOT")
        self.assertEqual(value_or_status(87.5, "%", "PASS"), "87.5%")
        self.assertEqual(value_or_status(None, "%", None), "UNKNOWN")

    def test_dialog_text_has_no_unit_after_a_status(self):
        for lang in ("ar", "en", "fr"):
            text = t("ui.full_scan_finished_board_checks_apps", lang, overall_health="UNKNOWN", sensitive_apps=20,
                     true_health_pct="NEEDS_ROOT")
            self.assertIn("NEEDS_ROOT", text, lang)
            self.assertNotRegex(text, r"NEEDS_ROOT\s*%", lang)


class TestPartitionSizeNotInvented(unittest.TestCase):
    def test_denied_size_is_unknown_not_zero(self):
        from core.partition_backup import PartitionBackupManager
        b = FakeBridge({"ls -d /dev/block/bootdevice/by-name": (1, "", "No such file or directory"),
                        "ls -d /dev/block/by-name": (0, "/dev/block/by-name", ""),
                        "ls -l /dev/block/by-name": (0, "lrwxrwxrwx 1 root root 21 2026-11-12 10:27 boot -> /dev/block/mmcblk0p34", ""),
                        "/size": (1, "", "cat: /sys/class/block/mmcblk0p34/size: Permission denied"),
                        "/proc/partitions": (1, "", "cat: /proc/partitions: Permission denied")})
        parts = PartitionBackupManager(bridge=b).scan_partition_table("s")
        boot = next(p for p in parts if p.name == "boot")
        self.assertIsNone(boot.size_bytes)
        self.assertIsNone(boot.size_mb)


class TestCameraSectionsAfterDeviceList(unittest.TestCase):
    """The Redmi lists every camera ("Device N is closed") before the per-camera static sections."""

    def test_each_camera_gets_its_own_facing(self):
        text = "\n".join(
            [f"  Device {i} is closed, no client instance" for i in (0, 1, 21)]
            + ["== Camera HAL device device@3.6/internal/0 (v3.6) static information: ==", "    Facing: Back",
               "== Camera HAL device device@3.6/internal/1 (v3.6) static information: ==", "    Facing: Front",
               "== Camera HAL device device@3.6/internal/21 (v3.6) static information: ==", "    Facing: Back",
               "== Camera HAL device device@3.6/internal/0 (v3.6) dumpState: =="])
        cams = HardwareTester(FakeBridge({"dumpsys media.camera": (0, text, "")})).audit_cameras("s")
        self.assertEqual({c.camera_id: c.facing for c in cams}, {"0": "Back", "1": "Front", "21": "Back"})


class TestArabicReportKeepsNumbersLtr(_Db):
    def test_values_are_isolated_ltr(self):
        pmic = {"voltage_mv": 3874.0, "voltage_source": "dumpsys battery (voltage, mV)", "voltage_status": "PASS"}
        zones = [{"zone_id": 0, "zone_type": "POWER_AMPLIFIER", "temp_c": -26.0, "is_overheated": False,
                  "category": "other", "limit_c": None},
                 {"zone_id": 1, "zone_type": "CPU", "temp_c": 55.2, "is_overheated": False,
                  "category": "cpu", "limit_c": 95.0}]
        html = self.report(lang="ar", board_diag={"pmic": pmic, "thermal_zones": zones})
        self.assertIn('<bdi dir="ltr" class="num">3874.0 mV</bdi>', html)
        self.assertIn('<bdi dir="ltr">(dumpsys battery (voltage, mV))</bdi>', html)
        self.assertIn('<bdi dir="ltr" class="num">-26.0 °C</bdi>', html)
        self.assertIn(".num { white-space: nowrap; }", html)
        # the limit inside the Arabic sentence is an isolated LTR run (was shown as "C°95")
        self.assertIn("⁦95°C⁩", t("report.zone_within", "ar", limit=95.0))
        self.assertIn(html_escape(t("report.zone_within", "ar", limit=95.0)), html)


class TestPairingCodeNotLogged(unittest.TestCase):
    def test_code_is_not_in_the_log(self):
        from core.adb_bridge import ADBBridge
        with patch.object(ADBBridge, "run_adb", return_value=(0, "Successfully paired to 192.168.1.50:37123", "")), \
                self.assertLogs("ADBBridge", level=logging.DEBUG) as logs:
            ok, _ = ADBBridge().pair_wireless_adb("192.168.1.50:37123", "482913")
        self.assertTrue(ok)
        self.assertNotIn("482913", "\n".join(logs.output))


if __name__ == "__main__":
    unittest.main()
