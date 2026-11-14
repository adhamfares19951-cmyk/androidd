"""
Step 5: CLI exit codes / stage isolation / status table, and the bundled-tools manifest, verification,
repair (hash checked before extraction) and private adb port.
"""

import hashlib
import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

import main as app_main  # noqa: E402
import setup_bins  # noqa: E402
from tests.fakes import FakeBridge  # noqa: E402
from database.db_manager import DatabaseManager  # noqa: E402


class _CliCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "t.db")

    def tearDown(self):
        DatabaseManager.reset_instance()
        self.tmp.cleanup()

    def run_cli(self, bridge, serial="", lang="en"):
        out = io.StringIO()
        db = DatabaseManager(self.db_path)  # temp DB: the real data/android_doctor.db is never opened
        with patch("core.adb_bridge.ADBBridge", lambda: bridge), \
                patch("database.db_manager.DatabaseManager", lambda *a, **k: db), \
                redirect_stdout(out):
            code = app_main.run_cli_diagnostics(serial, lang, output_dir=self.tmp.name)
        return code, out.getvalue()

    def reports(self):
        return [f for f in os.listdir(self.tmp.name) if f.endswith(".html")]


class TestCliExitCodes(_CliCase):
    def test_no_device(self):
        code, _ = self.run_cli(FakeBridge())
        self.assertEqual(code, app_main.EXIT_NO_DEVICE)

    def test_unknown_serial(self):
        b = FakeBridge()
        b.devices = [{"serial": "A1", "state": "device"}]
        self.assertEqual(self.run_cli(b, serial="ZZ")[0], app_main.EXIT_NO_DEVICE)

    def test_unauthorized_and_offline(self):
        for state in ("unauthorized", "offline"):
            b = FakeBridge()
            b.devices = [{"serial": "A1", "state": state}]
            self.assertEqual(self.run_cli(b)[0], app_main.EXIT_UNAUTHORIZED, state)

    def test_full_run_writes_report_and_status_table(self):
        b = FakeBridge()
        b.devices = [{"serial": "A1", "state": "device"}]
        code, out = self.run_cli(b)
        self.assertEqual(code, app_main.EXIT_OK)
        self.assertEqual(len(self.reports()), 1)
        self.assertIn("UNKNOWN", out)
        self.assertNotIn("Motherboard Health: PASS", out)
        for old in ("PMIC Voltage", "Standby Current", "True Capacity Pct", "Active I2C Sensors", "OFFICIAL CERTIFICATE"):
            self.assertNotIn(old, out)

    def test_failing_stage_is_unknown_and_report_still_written(self):
        b = FakeBridge()
        b.devices = [{"serial": "A1", "state": "device"}]
        with patch("core.hardware_tester.HardwareTester.run_full_audit", side_effect=RuntimeError("boom")):
            code, out = self.run_cli(b)
        self.assertEqual(code, app_main.EXIT_PARTIAL)
        self.assertEqual(len(self.reports()), 1)
        self.assertIn("boom", out)

    def test_report_failure_is_internal_error(self):
        b = FakeBridge()
        b.devices = [{"serial": "A1", "state": "device"}]
        with patch("ui.report_exporter.DiagnosticReportExporter.generate_html_report", side_effect=OSError("disk full")):
            self.assertEqual(self.run_cli(b)[0], app_main.EXIT_INTERNAL)

    def test_several_devices_are_listed_and_choice_is_stated(self):
        b = FakeBridge()
        b.devices = [{"serial": "OFF1", "state": "offline"}, {"serial": "GOOD2", "state": "device"}]
        code, out = self.run_cli(b)
        self.assertEqual(code, app_main.EXIT_OK)
        self.assertIn("OFF1", out)
        self.assertIn("GOOD2", out)
        self.assertIn("--serial", out)


class TestMainEntry(unittest.TestCase):
    def test_lang_choices_come_from_locales(self):
        from ui.i18n import available_languages
        parser = app_main.build_parser()
        action = next(a for a in parser._actions if a.dest == "lang")
        self.assertEqual(sorted(action.choices), sorted(available_languages()))

    def test_no_download_before_gui_and_no_unused_threading(self):
        src = Path(ROOT, "main.py").read_text(encoding="utf-8")
        self.assertNotIn("ensure_all_binaries", src)
        self.assertNotIn("import threading", src)

    def test_log_directory_under_localappdata(self):
        with patch.dict(os.environ, {"LOCALAPPDATA": r"C:\X"}):
            self.assertEqual(app_main.log_directory(), os.path.join(r"C:\X", "AndroidDoctorPro", "logs"))


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class TestBundledTools(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.bin = os.path.join(self.tmp.name, "bin")
        self.arch = os.path.join(self.tmp.name, "archives")
        os.makedirs(self.bin)
        os.makedirs(self.arch)
        self.key = setup_bins.platform_key()
        self.files = {"adb.exe": b"ADB-BYTES", "fastboot.exe": b"FASTBOOT"}

    def tearDown(self):
        self.tmp.cleanup()

    def make_zip(self, name, members):
        path = os.path.join(self.arch, name)
        with zipfile.ZipFile(path, "w") as z:
            for n, data in members.items():
                z.writestr(n, data)
        return path

    def make_manifest(self, archive_sha):
        m = {"packages": {"platform-tools": {
            "version": "1",
            "downloads": {self.key: {"url": "https://example.invalid/pt.zip", "sha256": archive_sha,
                                     "type": "zip", "strip_prefix": "platform-tools/"}},
            "files": {self.key: {k: _sha(v) for k, v in self.files.items()}}}}}
        path = os.path.join(self.tmp.name, "manifest.json")
        Path(path).write_text(json.dumps(m))
        return path

    def test_repo_manifest_pins_versions_and_hashes(self):
        m = setup_bins.load_manifest()
        self.assertEqual(m["packages"]["scrcpy"]["version"], "5.0.1")
        for pkg in m["packages"].values():
            self.assertTrue(pkg["version"])
            for dl in pkg["downloads"].values():
                self.assertRegex(dl["sha256"], r"^[0-9a-f]{64}$")
                self.assertTrue(dl["url"].startswith("https://"))
            self.assertTrue(pkg["files"]["windows-x86_64"])

    def test_verify_reports_missing_and_mismatched(self):
        manifest = self.make_manifest("0" * 64)
        Path(self.bin, "adb.exe").write_bytes(b"TAMPERED")
        res = setup_bins.verify_binaries(manifest_path=manifest, bin_dir=self.bin)
        info = res["packages"]["platform-tools"]
        self.assertFalse(res["ok"])
        self.assertEqual(info["mismatched"], ["adb.exe"])
        self.assertEqual(info["missing"], ["fastboot.exe"])

    def test_archive_hash_mismatch_extracts_nothing(self):
        self.make_zip("pt.zip", {f"platform-tools/{k}": v for k, v in self.files.items()})
        manifest = self.make_manifest("0" * 64)
        res = setup_bins.repair_tools(archive_dir=self.arch, manifest_path=manifest, bin_dir=self.bin)
        self.assertFalse(res["platform-tools"]["ok"])
        self.assertEqual(os.listdir(self.bin), [])

    def test_verified_archive_is_installed(self):
        path = self.make_zip("pt.zip", {f"platform-tools/{k}": v for k, v in self.files.items()})
        manifest = self.make_manifest(_sha(Path(path).read_bytes()))
        with patch("setup_bins.stop_adb_server") as stop:
            res = setup_bins.repair_tools(archive_dir=self.arch, manifest_path=manifest, bin_dir=self.bin)
        self.assertTrue(res["platform-tools"]["ok"], res)
        stop.assert_called_once()
        self.assertTrue(setup_bins.verify_binaries(manifest_path=manifest, bin_dir=self.bin)["ok"])

    def test_unsafe_archive_path_is_rejected(self):
        path = self.make_zip("pt.zip", {"platform-tools/../evil.exe": b"x", **{f"platform-tools/{k}": v for k, v in self.files.items()}})
        manifest = self.make_manifest(_sha(Path(path).read_bytes()))
        res = setup_bins.repair_tools(archive_dir=self.arch, manifest_path=manifest, bin_dir=self.bin)
        self.assertFalse(res["platform-tools"]["ok"])
        self.assertFalse(os.path.exists(os.path.join(self.tmp.name, "evil.exe")))

    def test_private_adb_port_and_scrcpy_env(self):
        env = setup_bins.tool_env()
        self.assertEqual(setup_bins.ADB_SERVER_PORT, 5038)
        self.assertEqual(env["ANDROID_ADB_SERVER_PORT"], "5038")
        self.assertEqual(env["ADB"], setup_bins.ADB_PATH)

    def test_stop_only_our_adb_server(self):
        with patch("setup_bins.os.path.isfile", return_value=True), patch("setup_bins.subprocess.run") as run:
            setup_bins.stop_adb_server()
        args = run.call_args[0][0]
        self.assertEqual(args[1:], ["-P", "5038", "kill-server"])
        src = Path(ROOT, "setup_bins.py").read_text(encoding="utf-8")
        self.assertNotIn("taskkill", src)
        self.assertNotIn("killall", src)


if __name__ == "__main__":
    unittest.main()
