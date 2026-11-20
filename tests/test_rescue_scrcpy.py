"""scrcpy mirror / OTG launch behaviour (no real scrcpy process is ever spawned)."""
import unittest
from unittest.mock import patch, MagicMock

from core.rescue_center import RescueCenter
from tests.fakes import FakeBridge


class _FakeProc:
    def __init__(self, alive=True):
        self._alive = alive
        self.terminated = False
    def poll(self):
        return None if self._alive else 0
    def terminate(self):
        self.terminated = True
        self._alive = False
    def wait(self, timeout=None):
        return 0
    def kill(self):
        self._alive = False


class ScrcpyLaunchTests(unittest.TestCase):
    def _center(self):
        rc = RescueCenter(FakeBridge())
        rc.scrcpy_path = "/fake/scrcpy"
        return rc

    def test_mirror_uses_uhid_so_miui_input_works(self):
        rc = self._center()
        with patch("core.rescue_center.os.path.exists", return_value=True), \
             patch("core.rescue_center.subprocess.Popen", return_value=_FakeProc()) as popen:
            ok, _ = rc.launch_screen_mirror("S1")
        self.assertTrue(ok)
        cmd = popen.call_args[0][0]
        self.assertIn("--keyboard=uhid", cmd)
        self.assertIn("--mouse=uhid", cmd)

    def test_otg_tears_down_a_running_mirror_first(self):
        rc = self._center()
        old = _FakeProc(alive=True)
        rc._scrcpy_process = old
        with patch("core.rescue_center.os.path.exists", return_value=True), \
             patch("core.rescue_center.subprocess.Popen", return_value=_FakeProc()) as popen:
            ok, _ = rc.launch_otg_mode("S1")
        self.assertTrue(ok)
        self.assertTrue(old.terminated)          # the previous session was stopped
        cmd = popen.call_args[0][0]
        self.assertIn("--otg", cmd)

    def test_mirror_reports_missing_scrcpy_instead_of_silent_failure(self):
        rc = self._center()
        with patch("core.rescue_center.os.path.exists", return_value=False):
            ok, msg = rc.launch_screen_mirror("S1")
        self.assertFalse(ok)
        self.assertEqual(msg.key, "scrcpy.not_found")


if __name__ == "__main__":
    unittest.main()
