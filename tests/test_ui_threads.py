"""Worker threads talking to the window.

Tkinter raises "RuntimeError: main thread is not in main loop" in a worker thread that calls after()
while the main thread has not been dispatching events for about one second. CustomTkinter's first
mainloop() runs update() (title bar colour) before Tkinter's real loop, so callbacks scheduled earlier can
start workers before the loop dispatches; on a slow PC the worker died (seen as a failing batch-scan test
under load, and at startup it could drop the first device scan or the tools check)."""
import gc
import os
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

import customtkinter

from database.db_manager import DatabaseManager
from tests.fakes import FakeBridge
from tests.gui_loop import run_until


class _AppCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.deaths = []
        old_hook = threading.excepthook
        threading.excepthook = lambda a: self.deaths.append(f"{a.exc_type.__name__}: {a.exc_value}")
        self.addCleanup(setattr, threading, "excepthook", old_hook)

    def tearDown(self):
        self.tmp.cleanup()

    def make_app(self, stack):
        from ui import main_window
        db_path = os.path.join(self.tmp.name, "ui.db")
        for p in (patch.object(main_window, "DatabaseManager", side_effect=lambda *a, **k: DatabaseManager(db_path=db_path)),
                  patch.object(main_window.AndroidDoctorApp, "_start_device_monitor", return_value=None),
                  patch.object(main_window.AndroidDoctorApp, "refresh_devices_async", return_value=None),
                  patch.object(main_window.AndroidDoctorApp, "_auto_poll_devices", return_value=None),
                  patch.object(main_window, "verify_binaries", return_value={"ok": True, "packages": {}})):
            stack.enter_context(p)
        app = main_window.AndroidDoctorApp(lang="en")
        app.withdraw()
        return app

    def close(self, app):
        app.destroy()
        del app
        gc.collect()
        DatabaseManager.reset_instance()


class TestAfterFromWorkerThreads(_AppCase):
    def test_worker_after_before_mainloop_does_not_kill_the_worker(self):
        from contextlib import ExitStack
        with ExitStack() as stack:
            app = self.make_app(stack)
            ran = []
            try:
                worker = threading.Thread(target=lambda: app.after(0, lambda: ran.append("ui")))
                worker.start()
                worker.join(10)                      # the main thread is NOT in mainloop here
                self.assertEqual(self.deaths, [])
                run_until(app, lambda: bool(ran), timeout=15, first_check_ms=50)
                self.assertEqual(ran, ["ui"])        # delivered once the loop runs
                self.assertIsNotNone(app.after(10, lambda: None))   # main-thread calls still return an id
            finally:
                self.close(app)

    def test_batch_scan_when_customtkinter_prepares_slowly(self):
        """The original failure, made deterministic: CustomTkinter's pre-loop update() takes 1.5 s."""
        from contextlib import ExitStack
        from ui.tabs import reports as reports_tab
        original = customtkinter.CTk._windows_set_titlebar_color

        def slow_titlebar(self, mode):
            original(self, mode)
            time.sleep(1.5)

        with ExitStack() as stack:
            stack.enter_context(patch.object(customtkinter.CTk, "_windows_set_titlebar_color", slow_titlebar))
            stack.enter_context(patch.object(reports_tab, "REPORTS_DIR", self.tmp.name))
            app = self.make_app(stack)
            try:
                app._ensure_tab("reports")
                fake = FakeBridge()
                fake.devices = [{"serial": "G1", "state": "device"}, {"serial": "G2", "state": "device"}]
                app.bridge = fake
                app.after(0, app._run_batch_scan_async)
                run_until(app, lambda: app.btn_batch_scan.cget("state") == "normal", timeout=30)
                text = app.batch_text.get("1.0", "end")
            finally:
                self.close(app)
        self.assertEqual(self.deaths, [])
        self.assertIn("G1", text)
        self.assertIn("G2", text)

    def test_quit_made_before_the_real_loop_is_not_lost(self):
        """CustomTkinter's pre-loop step handles events; a condition met in there used to end in a single
        quit() that Tkinter's mainloop() then cleared, so the test ran forever (seen as a hung suite)."""
        import _tkinter
        from contextlib import ExitStack
        original = customtkinter.CTk._windows_set_titlebar_color
        seen = {"in_pre_loop": False}

        def busy_titlebar(self, mode):
            seen["in_pre_loop"] = True                # CustomTkinter's own step already runs update()
            original(self, mode)
            end = time.monotonic() + 1.5
            while time.monotonic() < end:            # keeps handling events before Tkinter's own loop
                self.tk.dooneevent(_tkinter.ALL_EVENTS | _tkinter.DONT_WAIT)
                time.sleep(0.01)
            seen["in_pre_loop"] = False

        with ExitStack() as stack:
            stack.enter_context(patch.object(customtkinter.CTk, "_windows_set_titlebar_color", busy_titlebar))
            app = self.make_app(stack)
            met = {"in_pre_loop": None}
            try:
                def condition():
                    if met["in_pre_loop"] is None:
                        met["in_pre_loop"] = seen["in_pre_loop"]
                    return True
                t0 = time.monotonic()
                self.assertTrue(run_until(app, condition, timeout=20, first_check_ms=50))
                self.assertLess(time.monotonic() - t0, 15)
                self.assertTrue(met["in_pre_loop"])         # the condition really was met before the real loop
            finally:
                self.close(app)


if __name__ == "__main__":
    unittest.main()
