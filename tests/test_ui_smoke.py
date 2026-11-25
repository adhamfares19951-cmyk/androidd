"""Headless smoke, RTL/LTR switch, and responsive fit tests for the desktop UI."""
import gc

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from database.db_manager import DatabaseManager
from ui.theme import rtl_wrap, update_status_badge, BG_CANVAS


class TestThemeHelpers(unittest.TestCase):
    def test_rtl_wrap_preserves_letter_codepoints_and_wraps_arabic_lines(self):
        sample = "الشركة والموديل:\n"
        out_ar = rtl_wrap(sample, "ar")
        self.assertTrue(out_ar.startswith("\u202b"))
        self.assertTrue(out_ar.endswith("\u202c\n"))
        # Must not transform standard Arabic letters into presentation forms
        self.assertIn("الشركة والموديل:", out_ar)

        out_en = rtl_wrap("Model & Brand:", "en")
        self.assertEqual(out_en, "Model & Brand:")

    def test_rtl_wrap_is_idempotent(self):
        first = rtl_wrap("Test line", "ar")
        second = rtl_wrap(first, "ar")
        self.assertEqual(first, second)


class TestMainWindowSmoke(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db_path = os.path.join(self._tmpdir.name, "ui_smoke.db")

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_all_tabs_build_and_fit_1100px_width_in_ar_and_en(self):
        from ui import main_window

        all_tabs = (
            "dashboard",
            "board_diag",
            "hardware_tester",
            "malware_hunter",
            "optimizer",
            "rescue_center",
            "reports",
        )
        expected_pills = {
            "pmic", "fast_charge", "nand", "stress", "bootrom", "leakage", "logscan",
            "thermal", "panic", "blackbox", "root_forensics",
            "sensors", "cameras", "fp", "keys", "sim", "backlight",
            "wifi", "bt", "chips", "storage", "audio_codec", "panel_info",
            "user_ca", "maint", "network",
        }

        db_factory = lambda *a, **kw: DatabaseManager(db_path=self._db_path)
        with (
            patch.object(main_window, "DatabaseManager", side_effect=db_factory),
            patch.object(main_window.AndroidDoctorApp, "_start_device_monitor", return_value=None),
            patch.object(main_window.AndroidDoctorApp, "refresh_devices_async", return_value=None),
            patch.object(main_window.AndroidDoctorApp, "_auto_poll_devices", return_value=None),
            patch.object(main_window, "verify_binaries", return_value={"ok": True, "packages": {}}),
        ):
            app = main_window.AndroidDoctorApp(lang="ar")
            app.withdraw()
            app.geometry("1100x750+0+0")
            app.update_idletasks()
            try:
                self.assertEqual(app.cget("fg_color"), BG_CANVAS)
                max_content_budget = 790
                for tab_key in all_tabs:
                    app.select_tab(tab_key)
                    app.update_idletasks()
                    frame = app.tab_frames[tab_key]
                    for w in frame.winfo_children():
                        self.assertLessEqual(
                            w.winfo_reqwidth(),
                            max_content_budget,
                            f"Horizontal overflow in AR tab '{tab_key}': req_w={w.winfo_reqwidth()} > {max_content_budget}",
                        )

                self.assertEqual(set(app.status_pills.keys()), expected_pills)
                self.assertEqual(
                    set(app.kpi_labels.keys()),
                    {"model", "android", "root", "selinux"},
                )

                for st in ("PASS", "FAIL", "UNKNOWN", "NEEDS_ROOT"):
                    app._set_pill("pmic", st)
                    self.assertEqual(app.status_pills["pmic"].cget("text"), st)
                update_status_badge(None, "PASS")

                app.select_tab("reports")
                app.switch_language("en")
                self.assertEqual(app.current_lang, "en")
                self.assertEqual(app._current_tab, "reports")

                for tab_key in all_tabs:
                    app.select_tab(tab_key)
                    app.update_idletasks()
                    frame = app.tab_frames[tab_key]
                    for w in frame.winfo_children():
                        self.assertLessEqual(
                            w.winfo_reqwidth(),
                            max_content_budget,
                            f"Horizontal overflow in EN tab '{tab_key}': req_w={w.winfo_reqwidth()} > {max_content_budget}",
                        )

                app.switch_language("ar")
                self.assertEqual(app.current_lang, "ar")
                self.assertEqual(app.nav_buttons["dashboard"].cget("anchor"), "e")
            finally:
                app.destroy()
                # free Tk fonts/images here, on the main thread: if a later worker thread's
                # garbage collection finalizes them, it waits for a Tk main loop that is not running
                gc.collect()


class TestPillStatusRules(unittest.TestCase):
    """Pills must follow the core status rules, never invent a verdict."""

    def _actions(self, *statuses):
        from types import SimpleNamespace
        return [SimpleNamespace(status=s) for s in statuses]

    def test_maintenance_unknown_measured_step_is_not_pass(self):
        from ui.main_window import maintenance_pill_status
        self.assertEqual(maintenance_pill_status(self._actions("UNKNOWN", "PASS", "PASS")), "UNKNOWN")

    def test_maintenance_rules(self):
        from ui.main_window import maintenance_pill_status
        self.assertEqual(maintenance_pill_status(self._actions("PASS", "PASS", "PASS")), "PASS")
        self.assertEqual(maintenance_pill_status(self._actions("PASS", "FAIL", "UNKNOWN")), "FAIL")
        self.assertEqual(maintenance_pill_status(self._actions("PASS", "NEEDS_ROOT", "PASS")), "UNKNOWN")
        self.assertEqual(maintenance_pill_status([]), "UNKNOWN")

    def test_bootrom_scan_never_claims_fail_for_detected_ports(self):
        from types import SimpleNamespace
        from ui.main_window import bootrom_pill_status
        fastboot = SimpleNamespace(detected_ports=[{"mode": "Android Fastboot"}])
        none = SimpleNamespace(detected_ports=[])
        self.assertEqual(bootrom_pill_status(fastboot), "UNKNOWN")
        self.assertEqual(bootrom_pill_status(none), "UNKNOWN")


if __name__ == "__main__":
    unittest.main()
