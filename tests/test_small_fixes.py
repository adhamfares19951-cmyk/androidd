"""Three small issues: the device list text after a language switch, the charger type that Android
reports without root, and the speaker test leaving its file and maximum volume on the phone."""
import gc
import os
import tempfile
import unittest
from unittest.mock import patch

from core.board_diag import MotherboardDiagnostics
from core.hardware_tester import HardwareTester
from database.db_manager import DatabaseManager
from tests.fakes import FakeBridge, PERMISSION
from ui.i18n import t

# dumpsys battery on the Redmi Note 9 while on a USB cable (the power_supply nodes are SELinux-blocked)
DUMPSYS_USB = (0, "Current Battery Service state:\n  AC powered: false\n  USB powered: true\n  Wireless powered: false\n"
                  "  Max charging current: 500000\n  Max charging voltage: 5000000\n  status: 2\n  health: 2\n  level: 38\n", "")


class TestChargerTypeFromAndroid(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.db.close()
        DatabaseManager.reset_instance()
        self.tmp.cleanup()

    def charge(self, dumpsys):
        b = FakeBridge({"/sys/class/power_supply/": PERMISSION, "dumpsys battery": dumpsys})
        return MotherboardDiagnostics(b, self.db).diagnose_fast_charge("s")

    def test_blocked_nodes_use_androids_power_source(self):
        r = self.charge(DUMPSYS_USB)
        self.assertEqual(r.charger_type, "USB")
        self.assertEqual(r.charger_type_source, "dumpsys battery")
        self.assertEqual(r.status, "NEEDS_ROOT")              # nothing was measured: the verdict does not change
        self.assertIsNone(r.is_fast_charging)
        self.assertEqual(r.protocol_name, "UNKNOWN")          # "USB powered" says nothing about the protocol
        self.assertTrue(any("USB powered=true" in e.raw_excerpt for e in r.evidence))

    def test_no_source_reported_stays_unknown(self):
        r = self.charge((0, DUMPSYS_USB[1].replace("USB powered: true", "USB powered: false"), ""))
        self.assertEqual(r.charger_type, "UNKNOWN")

    def test_kernel_type_wins(self):
        b = FakeBridge({"/sys/class/power_supply/usb/real_type": (0, "USB_PD", ""),
                        "/sys/class/power_supply/": PERMISSION, "dumpsys battery": DUMPSYS_USB})
        r = MotherboardDiagnostics(b, self.db).diagnose_fast_charge("s")
        self.assertEqual((r.charger_type, r.charger_type_source), ("USB_PD", "/sys/class/power_supply/usb/real_type"))


class PhoneWithAdb(FakeBridge):
    def run_adb(self, args, serial=None, timeout=30):
        self.commands.append("adb " + " ".join(args))
        return 0, "1 file pushed", ""


class TestSpeakerToneLeavesNothing(unittest.TestCase):
    def tone(self, volume_reply):
        b = PhoneWithAdb({"--stream 3 --get": volume_reply})
        ok, _ = HardwareTester(b).play_speaker_test_tone("s", duration_sec=0.05)
        self.assertTrue(ok)
        return b.commands

    def test_file_removed_and_volume_restored_after_playing(self):
        cmds = self.tone((0, "volume is 6 in range [0..15]", ""))
        cleanup = [c for c in cmds if "sleep" in c and "rm -f /data/local/tmp/doctor_speaker_tone.wav" in c]
        self.assertEqual(len(cleanup), 1)
        self.assertIn("--stream 3 --set 6", cleanup[0])
        self.assertIn("nohup", cleanup[0])                    # survives the end of the adb shell session

    def test_unknown_previous_volume_is_not_restored(self):
        cmds = self.tone((1, "", "cmd: Failure calling service media_session"))
        cleanup = [c for c in cmds if "rm -f /data/local/tmp/doctor_speaker_tone.wav" in c]
        self.assertEqual(len(cleanup), 1)
        self.assertNotIn("--set", cleanup[0])                 # no guessed volume


class TestDeviceListFollowsLanguage(unittest.TestCase):
    def test_no_device_text_is_retranslated(self):
        from ui import main_window
        tmp = tempfile.TemporaryDirectory()
        empty = {"all_adb": [], "authorized": [], "unauthorized": [], "offline": [], "fastboot": [], "pnp_phones": []}
        with patch.object(main_window, "DatabaseManager", side_effect=lambda *a, **k: DatabaseManager(os.path.join(tmp.name, "u.db"))), \
                patch.object(main_window.AndroidDoctorApp, "_start_device_monitor", return_value=None), \
                patch.object(main_window.AndroidDoctorApp, "_auto_poll_devices", return_value=None), \
                patch.object(main_window.AndroidDoctorApp, "refresh_devices_async", return_value=None), \
                patch.object(main_window, "verify_binaries", return_value={"ok": True, "packages": {}}):
            app = main_window.AndroidDoctorApp(lang="ar")
            app.withdraw()
            try:
                with patch.object(app.bridge, "get_device_connection_diagnostics", return_value=empty):
                    app.refresh_devices_sync()
                    app.update()
                    self.assertEqual(app.device_combo.get(), t("no_devices", "ar"))
                    app.switch_language("en")
                    app.refresh_devices_sync()
                    app.update()
                    self.assertEqual(app.device_combo.get(), t("no_devices", "en"))
            finally:
                app.destroy()
                DatabaseManager.reset_instance()
                gc.collect()
                tmp.cleanup()


if __name__ == "__main__":
    unittest.main()
