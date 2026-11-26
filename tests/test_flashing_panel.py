"""The official-firmware flashing card in the Rescue tab. The non-negotiable test: nothing is ever flashed
without the technician's confirmation, and the card only ever drives the safety-valved OfficialFlasher.
Each test uses a recording fake flasher and runs the worker synchronously - no real device, no real binaries."""
import gc
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.result import Status
from database.db_manager import DatabaseManager
from tests.fakes import FakeBridge
from ui.i18n import t

RUN_NOW = SimpleNamespace(Thread=lambda target=None, daemon=None, **kw: SimpleNamespace(start=target))


class FakeFlasher:
    """Records which engine method was called; returns a canned PASS result."""
    def __init__(self):
        self.calls = []

    def _result(self, mode, parts=None):
        return SimpleNamespace(mode=mode, status=Status.PASS, detail="ok",
                               flashed_partitions=parts or [], safety_checks={}, evidence=[])

    def adb_sideload(self, path, serial=None, expected_device=None):
        self.calls.append(("adb_sideload", path, expected_device))
        return self._result("adb_sideload")

    def fastboot_flash(self, images, serial=None, expected_product=None):
        self.calls.append(("fastboot_flash", dict(images), expected_product))
        return self._result("fastboot", sorted(images))

    def mtk_brom_flash(self, scatter):
        self.calls.append(("mtk_brom_flash", scatter))
        return self._result("mtk_brom")

    def odin_flash(self, packages):
        self.calls.append(("odin_flash", dict(packages)))
        return self._result("odin", sorted(packages))

    def check_fastboot_safety(self, serial=None, expected_product=None, package_arb=None):
        self.calls.append(("check", expected_product))
        return True, {"product": "lancelot", "unlocked": "yes", "device_arb": 1}, None, []


class FlashingPanelTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from ui import main_window
        cls.tmp = tempfile.TemporaryDirectory()
        db_path = os.path.join(cls.tmp.name, "gui.db")
        cls.patches = [
            patch.object(main_window, "DatabaseManager", side_effect=lambda *a, **k: DatabaseManager(db_path=db_path)),
            patch.object(main_window.AndroidDoctorApp, "_start_device_monitor", return_value=None),
            patch.object(main_window.AndroidDoctorApp, "refresh_devices_async", return_value=None),
            patch.object(main_window.AndroidDoctorApp, "_auto_poll_devices", return_value=None),
            patch.object(main_window, "verify_binaries", return_value={"ok": True, "packages": {}}),
        ]
        for p in cls.patches:
            p.start()
        cls.app = main_window.AndroidDoctorApp(lang="en")
        cls.app.withdraw()
        cls.app._ensure_tab("rescue_center")

    @classmethod
    def tearDownClass(cls):
        cls.app.destroy()
        gc.collect()
        DatabaseManager.reset_instance()
        for p in cls.patches:
            p.stop()
        cls.tmp.cleanup()

    def setUp(self):
        self.d = tempfile.TemporaryDirectory()
        self.fake = FakeFlasher()
        self.app.bridge = FakeBridge()
        self.app.current_serial.set("S1")
        self.app._flasher = lambda: self.fake
        self.app.flash_codename_entry.delete(0, "end")
        self.app.flash_path_entry.delete(0, "end")
        self.threads = patch("ui.tabs.flashing.threading", RUN_NOW)
        self.threads.start()
        # default: confirmation granted; individual tests override
        self.confirm = patch.object(self.app, "_flash_confirm", return_value=True)
        self.confirm.start()

    def tearDown(self):
        self.threads.stop()
        self.confirm.stop()
        del self.app._flasher
        self.app.current_serial.set("")
        self.d.cleanup()

    def _set(self, mode, path):
        self.app.flash_mode.set(t(f"flash.mode.{mode}", "en"))
        self.app._flash_on_mode_change()
        self.app.flash_path_entry.delete(0, "end")
        self.app.flash_path_entry.insert(0, path)

    def text(self):
        self.app.update()
        return self.app.flash_text.get("1.0", "end")

    def _zip(self, name="ota.zip"):
        p = os.path.join(self.d.name, name)
        open(p, "wb").close()
        return p

    def test_sideload_flashes_only_after_confirmation(self):
        self._set("sideload", self._zip())
        self.app.flash_codename_entry.insert(0, "lancelot")
        self.app._flash_run()
        self.app.update()
        self.assertEqual([c[0] for c in self.fake.calls], ["adb_sideload"])
        self.assertEqual(self.fake.calls[0][2], "lancelot")        # codename passed as safety match

    def test_no_confirmation_means_no_flash(self):
        self.confirm.stop()
        with patch.object(self.app, "_flash_confirm", return_value=False):
            self._set("sideload", self._zip())
            self.app._flash_run()
            self.app.update()
        self.assertEqual(self.fake.calls, [])                       # engine never touched
        self.assertIn(t("ui.flash_cancelled", "en"), self.text())
        self.confirm.start()                                        # restore for tearDown symmetry

    def test_fastboot_maps_only_safe_partition_images(self):
        folder = os.path.join(self.d.name, "fw")
        os.makedirs(folder)
        for n in ("boot.img", "dtbo.img", "frp.img", "notes.txt", "vbmeta.img"):
            open(os.path.join(folder, n), "wb").close()
        self._set("fastboot", folder)
        self.app._flash_run()
        self.app.update()
        self.assertEqual([c[0] for c in self.fake.calls], ["fastboot_flash"])
        images = self.fake.calls[0][1]
        self.assertEqual(sorted(images), ["boot", "dtbo", "vbmeta"])   # frp and the .txt are excluded
        self.assertNotIn("frp", images)

    def test_fastboot_empty_folder_does_not_flash(self):
        folder = os.path.join(self.d.name, "empty")
        os.makedirs(folder)
        self._set("fastboot", folder)
        self.app._flash_run()
        self.app.update()
        self.assertEqual(self.fake.calls, [])
        self.assertIn(t("ui.flash_no_images_found", "en"), self.text())

    def test_missing_path_does_not_flash(self):
        self._set("sideload", os.path.join(self.d.name, "nope.zip"))
        self.app._flash_run()
        self.app.update()
        self.assertEqual(self.fake.calls, [])
        self.assertIn(t("ui.flash_no_path", "en"), self.text())

    def test_check_safety_is_read_only_and_never_flashes(self):
        self._set("fastboot", self.d.name)
        self.app.flash_codename_entry.insert(0, "lancelot")
        self.app._flash_check_safety()
        self.app.update()
        self.assertEqual([c[0] for c in self.fake.calls], ["check"])   # only the read-only check ran
        self.assertIn(t("ui.flash_safety_ok", "en"), self.text())

    def test_odin_guesses_slot_from_filename(self):
        ap = os.path.join(self.d.name, "AP_G998.tar.md5")
        open(ap, "wb").close()
        self._set("odin", ap)
        self.app._flash_run()
        self.app.update()
        self.assertEqual(self.fake.calls[0][0], "odin_flash")
        self.assertIn("AP", self.fake.calls[0][1])

    def test_result_shows_flashed_partitions(self):
        folder = os.path.join(self.d.name, "fw2")
        os.makedirs(folder)
        open(os.path.join(folder, "boot.img"), "wb").close()
        self._set("fastboot", folder)
        self.app._flash_run()
        self.app.update()
        self.assertIn(t("ui.flash_done", "en"), self.text())
        self.assertIn("boot", self.text())


if __name__ == "__main__":
    unittest.main()
