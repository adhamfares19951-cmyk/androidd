"""Our private adb server (port 5038) kept running after the GUI was closed (seen after a build check): it
holds the phone's USB connection, so another tool's adb (port 5037) has to fight it for the phone.
When the program ends, it now stops that server, but only if this run really used the adb bridge."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main as app_main
from core import adb_bridge
from tests.fakes import FakeBridge


def kill_calls(run_mock):
    return [c.args[0] for c in run_mock.call_args_list if "kill-server" in c.args[0]]


class FakeApp:
    """Stands in for the GUI window: it creates the real (singleton) bridge, as the window does."""
    def __init__(self, lang="ar"):
        adb_bridge.ADBBridge()

    def mainloop(self):
        pass


class TestServerStoppedOnExit(unittest.TestCase):
    def test_closing_the_gui_stops_our_server_on_the_private_port(self):
        with patch("ui.main_window.AndroidDoctorApp", FakeApp), patch.object(app_main, "enable_windows_high_dpi"), \
                patch("sys.argv", ["main.py"]), patch.object(adb_bridge.subprocess, "run") as run:
            self.assertEqual(app_main.main(), app_main.EXIT_OK)
        calls = kill_calls(run)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][1:], ["-P", str(adb_bridge.ADB_SERVER_PORT), "kill-server"])
        self.assertNotEqual(adb_bridge.ADB_SERVER_PORT, 5037)          # never another tool's default server

    def test_a_command_without_adb_does_not_touch_the_server(self):
        out = io.StringIO()
        with patch("sys.argv", ["main.py", "--uart-list"]), redirect_stdout(out), \
                patch.object(adb_bridge.subprocess, "run") as run:
            app_main.main()
        self.assertEqual(kill_calls(run), [])

    def test_a_fake_bridge_in_tests_never_stops_a_real_server(self):
        b = FakeBridge()
        with patch("core.adb_bridge.ADBBridge", lambda: b), redirect_stdout(io.StringIO()), \
                patch("sys.argv", ["main.py", "--screen-check", "--lang", "en"]), \
                patch.object(adb_bridge.subprocess, "run") as run:
            app_main.main()
        self.assertEqual(kill_calls(run), [])

    def test_a_failing_stop_does_not_change_the_exit_code(self):
        with patch("ui.main_window.AndroidDoctorApp", FakeApp), patch.object(app_main, "enable_windows_high_dpi"), \
                patch("sys.argv", ["main.py"]), patch.object(adb_bridge.subprocess, "run", side_effect=OSError("gone")):
            self.assertEqual(app_main.main(), app_main.EXIT_OK)


if __name__ == "__main__":
    unittest.main()
