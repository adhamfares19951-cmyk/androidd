"""Several devices at once: parallel runs, isolated failures, one report each, shared DB closed once."""
import io
import os
import subprocess
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import diagnostics_runner as dr
import main as app_main
from core.adb_bridge import ADBBridge
from database.db_manager import DatabaseManager
from tests.fakes import FakeBridge


def _bridge():
    with patch("core.adb_bridge.get_adb_path", return_value="adb"), \
         patch("core.adb_bridge.get_fastboot_path", return_value="fastboot"), \
         patch("core.adb_bridge.get_scrcpy_path", return_value="scrcpy"):
        ADBBridge._instance = None
        return ADBBridge()


class _Counter:
    def __init__(self):
        self.lock = threading.Lock()
        self.now = 0
        self.peak = 0

    def __enter__(self):
        with self.lock:
            self.now += 1
            self.peak = max(self.peak, self.now)

    def __exit__(self, *a):
        with self.lock:
            self.now -= 1


class RunnerTests(unittest.TestCase):
    DEVICES = [{"serial": f"S{i}", "state": "device"} for i in range(3)]

    def fake_diagnose(self, counter, delay=0.3, crash=None):
        def run(bridge, target, lang, get_db, output_dir=None, signing_key=None, pdf=False, emit=print, **kw):
            with counter:
                get_db()
                if target["serial"] == crash:
                    raise RuntimeError("boom")
                time.sleep(delay)
                emit(f"line from {target['serial']}")
                return dr.DeviceRun(serial=target["serial"], state="device", exit_code=dr.EXIT_OK, overall="PASS")
        return run

    def test_devices_run_in_parallel_and_keep_order(self):
        c = _Counter()
        with patch.object(dr, "diagnose_device", side_effect=self.fake_diagnose(c)):
            t0 = time.monotonic()
            runs = dr.diagnose_all(None, self.DEVICES, "en", lambda: None, max_workers=3)
            elapsed = time.monotonic() - t0
        self.assertEqual(c.peak, 3)
        self.assertLess(elapsed, 0.75)  # sequential would take >= 0.9 s
        self.assertEqual([r.serial for r in runs], ["S0", "S1", "S2"])
        self.assertEqual(runs[1].output, ["line from S1"])  # each device's text kept together

    def test_worker_limit_is_respected(self):
        c = _Counter()
        with patch.object(dr, "diagnose_device", side_effect=self.fake_diagnose(c, delay=0.05)):
            dr.diagnose_all(None, self.DEVICES, "en", lambda: None, max_workers=1)
        self.assertEqual(c.peak, 1)

    def test_one_crash_does_not_stop_the_others(self):
        c = _Counter()
        done = []
        with patch.object(dr, "diagnose_device", side_effect=self.fake_diagnose(c, delay=0.05, crash="S1")):
            runs = dr.diagnose_all(None, self.DEVICES, "en", lambda: None, on_done=done.append)
        self.assertEqual([r.exit_code for r in runs], [dr.EXIT_OK, dr.EXIT_INTERNAL, dr.EXIT_OK])
        self.assertIn("boom", runs[1].output[-1])
        self.assertEqual(sorted(r.serial for r in done), ["S0", "S1", "S2"])
        self.assertEqual(dr.combined_exit_code(runs), dr.EXIT_INTERNAL)

    def test_shared_db_opened_once_and_closed_after_all_workers(self):
        events = []

        class DB:
            def close(self):
                events.append("close")

        opened = []

        def factory():
            opened.append(1)
            return DB()

        def run(bridge, target, lang, get_db, output_dir=None, signing_key=None, pdf=False, emit=print, **kw):
            get_db()
            time.sleep(0.05)
            events.append(f"end {target['serial']}")
            return dr.DeviceRun(serial=target["serial"], state="device", exit_code=0)
        with patch.object(dr, "diagnose_device", side_effect=run):
            dr.diagnose_all(None, self.DEVICES, "en", factory, max_workers=3)
        self.assertEqual(len(opened), 1)
        self.assertEqual(events[-1], "close")
        self.assertEqual(events.count("close"), 1)

    def test_combined_exit_code(self):
        mk = lambda c: dr.DeviceRun(serial="x", state="device", exit_code=c)
        self.assertEqual(dr.combined_exit_code([mk(0), mk(0)]), 0)
        self.assertEqual(dr.combined_exit_code([mk(0), mk(2)]), 2)
        self.assertEqual(dr.combined_exit_code([mk(2), mk(3)]), 3)
        self.assertEqual(dr.combined_exit_code([]), dr.EXIT_NO_DEVICE)


class CliAllDevicesTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        DatabaseManager.reset_instance()
        self.tmp.cleanup()

    def run_cli(self, devices):
        b = FakeBridge()
        b.devices = devices
        db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))
        out = io.StringIO()
        with patch("core.adb_bridge.ADBBridge", lambda: b), \
                patch("database.db_manager.DatabaseManager", lambda *a, **k: db), redirect_stdout(out):
            code = app_main.run_cli_all_devices("en", output_dir=self.tmp.name, max_workers=3)
        return code, out.getvalue()

    def reports(self):
        return sorted(f for f in os.listdir(self.tmp.name) if f.endswith(".html"))

    def test_no_devices(self):
        self.assertEqual(self.run_cli([])[0], app_main.EXIT_NO_DEVICE)

    def test_one_report_per_authorized_device(self):
        code, out = self.run_cli([{"serial": "A1", "state": "device"}, {"serial": "B2", "state": "device"},
                                  {"serial": "C3", "state": "unauthorized"}])
        self.assertEqual(code, app_main.EXIT_UNAUTHORIZED)  # worst of 0, 0, 2
        reports = self.reports()
        self.assertEqual(len(reports), 2)
        self.assertTrue(any("A1" in r for r in reports) and any("B2" in r for r in reports))
        for serial in ("A1", "B2", "C3"):
            self.assertIn(serial, out.split("Summary")[-1])

    def test_all_devices_flag_is_parsed(self):
        args = app_main.build_parser().parse_args(["--cli", "--all-devices", "--workers", "2"])
        self.assertTrue(args.all_devices)
        self.assertEqual(args.workers, 2)


class GuiBatchScanTests(unittest.TestCase):
    def test_button_scans_every_device_and_lists_reports(self):
        from ui import main_window
        from ui.tabs import reports as reports_tab
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "gui.db")
            fake = FakeBridge()
            fake.devices = [{"serial": "G1", "state": "device"}, {"serial": "G2", "state": "device"}]
            with patch.object(main_window, "DatabaseManager", side_effect=lambda *a, **k: DatabaseManager(db_path=db_path)), \
                 patch.object(main_window.AndroidDoctorApp, "_start_device_monitor", return_value=None), \
                 patch.object(main_window.AndroidDoctorApp, "refresh_devices_async", return_value=None), \
                 patch.object(main_window.AndroidDoctorApp, "_auto_poll_devices", return_value=None), \
                 patch.object(main_window, "verify_binaries", return_value={"ok": True, "packages": {}}), \
                 patch.object(reports_tab, "REPORTS_DIR", d):
                app = main_window.AndroidDoctorApp(lang="en")
                app.withdraw()
                try:
                    app._ensure_tab("reports")
                    app.bridge = fake
                    # Worker threads talk to the window through after(), which needs a running mainloop.
                    deadline = time.monotonic() + 30

                    def poll():
                        if app.btn_batch_scan.cget("state") == "normal" or time.monotonic() > deadline:
                            app.quit()
                        else:
                            app.after(50, poll)
                    app.after(0, app._run_batch_scan_async)
                    app.after(200, poll)
                    app.mainloop()
                    text = app.batch_text.get("1.0", "end")
                finally:
                    app.destroy()
                    DatabaseManager.reset_instance()
            self.assertIn("G1", text)
            self.assertIn("G2", text)
            self.assertEqual(len([f for f in os.listdir(d) if f.endswith(".html")]), 2)


class PerDeviceLockTests(unittest.TestCase):
    def tearDown(self):
        ADBBridge._instance = None

    def test_lock_identity(self):
        b = _bridge()
        self.assertIs(b._lock_for(None), b._cmd_lock)
        self.assertIs(b._lock_for("A"), b._lock_for("A"))
        self.assertIsNot(b._lock_for("A"), b._lock_for("B"))

    def _timed(self, serials):
        b = _bridge()

        def slow_run(cmd, **kw):
            time.sleep(0.3)
            return subprocess.CompletedProcess(cmd, 0, "ok", "")
        with patch("core.adb_bridge.subprocess.run", side_effect=slow_run):
            threads = [threading.Thread(target=b.run_adb, args=(["get-state"], s)) for s in serials]
            t0 = time.monotonic()
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            return time.monotonic() - t0

    def test_different_devices_do_not_wait_for_each_other(self):
        self.assertLess(self._timed(["A", "B"]), 0.5)

    def test_same_device_stays_in_order(self):
        self.assertGreaterEqual(self._timed(["A", "A"]), 0.55)


if __name__ == "__main__":
    unittest.main()
