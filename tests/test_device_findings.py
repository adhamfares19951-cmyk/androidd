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
