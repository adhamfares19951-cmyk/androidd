"""Adding a language is a locale file: name and direction come from meta.name / meta.dir."""
import gc
import os
import tempfile
import unittest
from unittest.mock import patch

from database.db_manager import DatabaseManager
from ui import i18n
from ui.theme import get_layout, rtl_wrap


def _with_languages(*extra):
    """Exactly Arabic + English plus fake languages: ('fa', 'rtl') copies Arabic, ('xx', 'ltr') copies English.
    Independent of which real locale files exist, so the switch tests stay meaningful."""
    strings = {code: i18n.STRINGS[code] for code in ("ar", "en")}
    for code, direction in extra:
        base = dict(i18n.STRINGS["ar" if direction == "rtl" else "en"])
        base["meta.name"] = f"Lang-{code}"
        base["meta.dir"] = direction
        strings[code] = base
    return patch.object(i18n, "STRINGS", strings)


class MetadataTests(unittest.TestCase):
    def test_every_locale_declares_name_and_direction(self):
        for code in i18n.available_languages():
            with self.subTest(lang=code):
                self.assertTrue(i18n.STRINGS[code].get("meta.name"))
                self.assertIn(i18n.STRINGS[code].get("meta.dir"), ("rtl", "ltr"))
        self.assertTrue(i18n.is_rtl("ar"))
        self.assertFalse(i18n.is_rtl("en"))

    def test_direction_follows_metadata_not_language_code(self):
        with _with_languages(("fa", "rtl"), ("xx", "ltr")):
            self.assertTrue(get_layout("fa")["is_rtl"])
            self.assertFalse(get_layout("xx")["is_rtl"])
            self.assertTrue(rtl_wrap("x", "fa").startswith("‫"))
            self.assertEqual(rtl_wrap("x", "xx"), "x")
            self.assertEqual(i18n.language_name("fa"), "Lang-fa")
        self.assertFalse(get_layout("unknown-language")["is_rtl"])  # missing language: left to right

    def test_report_and_verifier_page_direction(self):
        from ui.report_exporter import DiagnosticReportExporter
        with tempfile.TemporaryDirectory() as d, _with_languages(("fa", "rtl"), ("xx", "ltr")):
            for code, direction in (("fa", "rtl"), ("xx", "ltr")):
                path = DiagnosticReportExporter.generate_html_report(device_profile={"serial": "S"}, output_dir=d, lang=code)
                with open(path, encoding="utf-8") as f:
                    self.assertIn(f'dir="{direction}"', f.read())
            from core import report_signing as rs
            from ui.verifier_page import build_verifier_html
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(rs.PASSPHRASE_ENV, None)
                _, pub, _ = rs.generate_keypair(os.path.join(d, "keys"))
            self.assertIn('dir="rtl"', build_verifier_html(pub, "fa"))

    def test_evidence_text_in_the_current_language(self):
        from core.result import Evidence
        from ui.ui_helpers import format_evidence_for_display
        with _with_languages(("xx", "ltr")):
            i18n.STRINGS["xx"]["ui.technical_evidence"] = "PREUVES"
            text = format_evidence_for_display([Evidence(source="cat x", raw_excerpt="1")], lang="xx")
        self.assertIn("PREUVES", text)


class SwitcherTests(unittest.TestCase):
    def build(self, *extra):
        from ui import main_window
        tmp = tempfile.mkdtemp()
        with _with_languages(*extra), \
             patch.object(main_window, "DatabaseManager", side_effect=lambda *a, **k: DatabaseManager(db_path=os.path.join(tmp, "l.db"))), \
             patch.object(main_window.AndroidDoctorApp, "_start_device_monitor", return_value=None), \
             patch.object(main_window.AndroidDoctorApp, "refresh_devices_async", return_value=None), \
             patch.object(main_window.AndroidDoctorApp, "_auto_poll_devices", return_value=None), \
             patch.object(main_window, "verify_binaries", return_value={"ok": True, "packages": {}}):
            app = main_window.AndroidDoctorApp(lang="en")
            app.withdraw()
            try:
                result = {"buttons": sorted(app.lang_buttons), "menu": app.lang_menu is not None}
                if extra:
                    code = extra[0][0]
                    app.switch_language(code)
                    app.update_idletasks()
                    result["rtl_after_switch"] = app._layout()["is_rtl"]
                    result["sidebar_col"] = int(app.sidebar_frame.grid_info()["column"])
            finally:
                app.destroy()
                # free Tk fonts/images here, on the main thread: if a later worker thread's
                # garbage collection finalizes them, it waits for a Tk main loop that is not running
                gc.collect()
                DatabaseManager.reset_instance()
        return result

    def test_two_languages_two_buttons(self):
        self.assertEqual(self.build(), {"buttons": ["ar", "en"], "menu": False})

    def test_third_rtl_language_gets_a_button_and_right_sidebar(self):
        r = self.build(("fa", "rtl"))
        self.assertEqual(r["buttons"], ["ar", "en", "fa"])
        self.assertTrue(r["rtl_after_switch"])
        self.assertEqual(r["sidebar_col"], 2)  # right side

    def test_many_languages_use_a_menu(self):
        r = self.build(("xx", "ltr"), ("fa", "rtl"))
        self.assertTrue(r["menu"])
        self.assertEqual(r["buttons"], [])
        self.assertFalse(r["rtl_after_switch"])
        self.assertEqual(r["sidebar_col"], 0)


if __name__ == "__main__":
    unittest.main()
