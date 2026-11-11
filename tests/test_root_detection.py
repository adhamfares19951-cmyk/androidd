"""Root detection and `su` wrapping in ADBBridge.

The transport is replaced at `_shell_once`, so these tests never touch adb or a device.
"""
import unittest
from unittest.mock import patch

from core.adb_bridge import ADBBridge, _SU_RC_MARKER


def _bridge():
    with patch("core.adb_bridge.get_adb_path", return_value="adb"), \
         patch("core.adb_bridge.get_fastboot_path", return_value="fastboot"), \
         patch("core.adb_bridge.get_scrcpy_path", return_value="scrcpy"):
        ADBBridge._instance = None
        return ADBBridge()


class RootDetectionTests(unittest.TestCase):
    def tearDown(self):
        ADBBridge._instance = None

    def _run(self, responder):
        bridge = _bridge()
        calls = []

        def fake_shell_once(command, serial, timeout, use_socket):
            calls.append(command)
            return responder(command)

        with patch.object(bridge, "_shell_once", side_effect=fake_shell_once):
            return bridge.is_root_available("SERIAL"), calls

    def test_granted_su_shell_v2_is_root(self):
        ok, _ = self._run(lambda c: (0, f"uid=0(root) gid=0(root) context=u:r:magisk:s0\n{_SU_RC_MARKER}0\n", ""))
        self.assertTrue(ok)

    def test_denied_su_is_not_root_even_if_su_binary_exists(self):
        # Magisk "Deny": su exists on PATH but refuses; the old `which su` fallback said True here.
        def responder(command):
            if "which su" in command:
                return 0, "/system/bin/su\n", ""
            return 0, f"{_SU_RC_MARKER}1\n", "Permission denied\n"
        ok, calls = self._run(responder)
        self.assertFalse(ok)
        self.assertFalse(any("which su" in c for c in calls))

    def test_legacy_shell_without_exit_code_uses_marker(self):
        # No shell_v2: transport exit code is None, the marker carries su's real status.
        ok, _ = self._run(lambda c: (None, f"uid=0(root) gid=0(root)\n{_SU_RC_MARKER}0\n", ""))
        self.assertTrue(ok)

    def test_legacy_shell_denied_su_is_not_root(self):
        ok, _ = self._run(lambda c: (None, f"{_SU_RC_MARKER}1\n", "Permission denied\n"))
        self.assertFalse(ok)

    def test_no_su_binary_is_not_root(self):
        ok, _ = self._run(lambda c: (0, f"{_SU_RC_MARKER}127\n", "/system/bin/sh: su: inaccessible or not found\n"))
        self.assertFalse(ok)

    def test_uid_text_from_non_root_user_is_not_root(self):
        ok, _ = self._run(lambda c: (0, f"uid=2000(shell) gid=2000(shell)\n{_SU_RC_MARKER}0\n", ""))
        self.assertFalse(ok)


class RootShellWrapTests(unittest.TestCase):
    def tearDown(self):
        ADBBridge._instance = None

    def test_root_shell_on_legacy_transport_returns_su_output_without_marker(self):
        bridge = _bridge()
        responses = {True: (None, f"line1\nline2\n{_SU_RC_MARKER}0\n", "")}

        def fake(command, serial, timeout, use_socket):
            return responses[command.startswith("su -c")]

        with patch.object(bridge, "_shell_once", side_effect=fake):
            code, out, err = bridge.shell("dmesg", serial="S", root=True)
        self.assertEqual(code, 0)
        self.assertEqual(out, "line1\nline2")
        self.assertNotIn(_SU_RC_MARKER, out)

    def test_root_shell_falls_back_without_root_when_su_fails(self):
        bridge = _bridge()
        calls = []

        def fake(command, serial, timeout, use_socket):
            calls.append(command)
            if command.startswith("su -c"):
                return None, f"{_SU_RC_MARKER}1\n", "Permission denied"
            return 1, "", "dmesg: klogctl: Permission denied"

        with patch.object(bridge, "_shell_once", side_effect=fake):
            code, out, err = bridge.shell("dmesg", serial="S", root=True)
        self.assertEqual(calls, ['su -c "dmesg"; echo "' + _SU_RC_MARKER + '$?"', "dmesg"])
        self.assertEqual(code, 1)
        self.assertIn("Permission denied", err)

    def test_nonzero_su_status_overrides_zero_transport_code(self):
        # `cmd; echo marker` always exits 0 on shell_v2, so the marker must decide.
        bridge = _bridge()

        def fake(command, serial, timeout, use_socket):
            if command.startswith("su -c"):
                return 0, f"{_SU_RC_MARKER}1\n", "Permission denied"
            return 1, "", "denied"

        with patch.object(bridge, "_shell_once", side_effect=fake):
            code, _, _ = bridge.shell("cat /x", serial="S", root=True)
        self.assertEqual(code, 1)


if __name__ == "__main__":
    unittest.main()
