"""
Unit tests for legitimate official firmware flashing (core.flashing).
Uses temporary files/dirs and injected subprocess/bridge transport — no real
devices or external flashing binaries required.
"""

import os
import tempfile
import unittest
import zipfile

from core.flashing import OfficialFlasher
from core.result import Status


class FakeFastbootBridge:
    def __init__(self, getvar_out: str, flash_code: int = 0):
        self.getvar_out = getvar_out
        self.flash_code = flash_code
        self.calls = []

    def run_fastboot(self, args, serial=None, timeout=120):
        self.calls.append((list(args), serial))
        if args[:2] == ["getvar", "all"]:
            return 0, "", self.getvar_out
        if args and args[0] == "flash":
            return self.flash_code, f"Sending '{args[1]}' OKAY", ""
        return 0, "", ""


class TestOfficialFlasher(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bin_dir = os.path.join(self.tmp.name, "bin")
        os.makedirs(self.bin_dir, exist_ok=True)

    def tearDown(self):
        self.tmp.cleanup()

    def _touch(self, rel_path: str, content: bytes = b"dummy") -> str:
        full = os.path.join(self.tmp.name, rel_path)
        os.makedirs(os.path.dirname(full), exist_ok=True)
        with open(full, "wb") as f:
            f.write(content)
        return full

    def test_adb_sideload_valid_zip_runs_and_passes(self):
        zip_path = os.path.join(self.tmp.name, "ota.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("META-INF/com/android/metadata", "pre-device=raphael\npost-build=test\n")

        seen_cmds = []

        def fake_runner(cmd, timeout):
            seen_cmds.append(cmd)
            return 0, "Total xfer: 1.00x", ""

        flasher = OfficialFlasher(runner=fake_runner, bin_dir=self.bin_dir)
        res = flasher.adb_sideload(zip_path, serial="SER1", expected_device="raphael")
        self.assertEqual(res.status, Status.PASS)
        self.assertEqual(len(seen_cmds), 1)
        self.assertIn("sideload", seen_cmds[0])
        self.assertIn(zip_path, seen_cmds[0])

    def test_adb_sideload_rejects_device_mismatch(self):
        zip_path = os.path.join(self.tmp.name, "ota.zip")
        with zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("META-INF/com/android/metadata", "pre-device=raphael\n")

        called = []
        flasher = OfficialFlasher(runner=lambda c, t: (called.append(c), (0, "", ""))[1], bin_dir=self.bin_dir)
        res = flasher.adb_sideload(zip_path, expected_device="cepheus")
        self.assertEqual(res.status, Status.FAIL)
        self.assertEqual(called, [])

    def test_fastboot_refuses_locked_bootloader(self):
        boot_img = self._touch("boot.img")
        bridge = FakeFastbootBridge("product: raphael\nunlocked: no\nanti: 1\n")
        flasher = OfficialFlasher(bridge=bridge, bin_dir=self.bin_dir)
        res = flasher.fastboot_flash({"boot": boot_img}, serial="FB1", expected_product="raphael")
        self.assertEqual(res.status, Status.FAIL)
        self.assertEqual(res.flashed_partitions, [])
        # Ensure flash command was never issued
        self.assertFalse(any(c[0][0] == "flash" for c in bridge.calls))

    def test_fastboot_refuses_model_mismatch(self):
        boot_img = self._touch("boot.img")
        bridge = FakeFastbootBridge("product: cepheus\nunlocked: yes\nanti: 1\n")
        flasher = OfficialFlasher(bridge=bridge, bin_dir=self.bin_dir)
        res = flasher.fastboot_flash({"boot": boot_img}, serial="FB1", expected_product="raphael")
        self.assertEqual(res.status, Status.FAIL)
        self.assertFalse(any(c[0][0] == "flash" for c in bridge.calls))

    def test_fastboot_refuses_arb_downgrade(self):
        boot_img = self._touch("boot.img")
        bridge = FakeFastbootBridge("product: raphael\nunlocked: yes\nanti: 4\n")
        flasher = OfficialFlasher(bridge=bridge, bin_dir=self.bin_dir)
        res = flasher.fastboot_flash({"boot": boot_img}, serial="FB1", expected_product="raphael", package_arb=2)
        self.assertEqual(res.status, Status.FAIL)
        self.assertFalse(any(c[0][0] == "flash" for c in bridge.calls))

    def test_fastboot_refuses_forbidden_frp_partition(self):
        frp_img = self._touch("frp.img")
        bridge = FakeFastbootBridge("product: raphael\nunlocked: yes\nanti: 1\n")
        flasher = OfficialFlasher(bridge=bridge, bin_dir=self.bin_dir)
        res = flasher.fastboot_flash({"frp": frp_img}, serial="FB1")
        self.assertEqual(res.status, Status.FAIL)
        self.assertEqual(bridge.calls, [])

    def test_fastboot_passes_when_unlocked_matched_and_safe_arb(self):
        boot_img = self._touch("boot.img")
        dtbo_img = self._touch("dtbo.img")
        bridge = FakeFastbootBridge("product: raphael\nunlocked: yes\nanti: 2\n")
        flasher = OfficialFlasher(bridge=bridge, bin_dir=self.bin_dir)
        res = flasher.fastboot_flash(
            {"boot": boot_img, "dtbo": dtbo_img},
            serial="FB1",
            expected_product="raphael",
            package_arb=2,
        )
        self.assertEqual(res.status, Status.PASS)
        self.assertEqual(res.flashed_partitions, ["boot", "dtbo"])

    def test_mtk_brom_refuses_auth_bypass_and_frp(self):
        scatter = self._touch("MT6765_Android_scatter.txt")
        flasher = OfficialFlasher(runner=lambda c, t: (0, "", ""), bin_dir=self.bin_dir)

        res_frp = flasher.mtk_brom_flash(scatter, partitions=["frp"])
        self.assertEqual(res_frp.status, Status.FAIL)

        res_bypass = flasher.mtk_brom_flash(scatter, extra_args=["--auth-bypass"])
        self.assertEqual(res_bypass.status, Status.FAIL)

    def test_mtk_brom_missing_binary_returns_unknown_and_present_runs(self):
        scatter = self._touch("MT6765_Android_scatter.txt")
        flasher = OfficialFlasher(runner=lambda c, t: (0, "Download Ok", ""), bin_dir=self.bin_dir)

        res_missing = flasher.mtk_brom_flash(scatter, tool="spflashtool")
        self.assertEqual(res_missing.status, Status.UNKNOWN)

        # Create dummy standalone binary in bin/
        self._touch(os.path.join("bin", "flash_tool.exe" if os.name == "nt" else "flash_tool"))
        seen = []
        flasher2 = OfficialFlasher(
            runner=lambda c, t: (seen.append(c), (0, "Download Ok", ""))[1],
            bin_dir=self.bin_dir,
        )
        res_ok = flasher2.mtk_brom_flash(scatter, tool="spflashtool")
        self.assertEqual(res_ok.status, Status.PASS)
        self.assertEqual(len(seen), 1)
        self.assertIn("-s", seen[0])

    def test_odin_flash_validates_slots_extensions_and_runs(self):
        ap = self._touch("AP_file.tar.md5")
        bl = self._touch("BL_file.tar")
        bad_ext = self._touch("bad.zip")

        flasher = OfficialFlasher(runner=lambda c, t: (0, "PASS!", ""), bin_dir=self.bin_dir)
        self.assertEqual(flasher.odin_flash({"INVALID": ap}).status, Status.FAIL)
        self.assertEqual(flasher.odin_flash({"AP": bad_ext}).status, Status.FAIL)
        self.assertEqual(flasher.odin_flash({"AP": ap}).status, Status.UNKNOWN)  # missing odin4

        self._touch(os.path.join("bin", "odin4.exe" if os.name == "nt" else "odin4"))
        seen = []
        flasher2 = OfficialFlasher(
            runner=lambda c, t: (seen.append(c), (0, "PASS!", ""))[1],
            bin_dir=self.bin_dir,
        )
        res = flasher2.odin_flash({"BL": bl, "AP": ap})
        self.assertEqual(res.status, Status.PASS)
        self.assertEqual(set(res.flashed_partitions), {"BL", "AP"})
        self.assertIn("-b", seen[0])
        self.assertIn("-a", seen[0])


if __name__ == "__main__":
    unittest.main()
