"""The diode-mode notebook card: readings typed by the technician are saved for the model and compared with the
reference board; large gaps are marked, nothing becomes a PASS/FAIL. Uses a temporary notebook file only."""
import gc
import os
import tempfile
import unittest
from unittest.mock import patch

from core.diode_notes import DiodeNotebook
from database.db_manager import DatabaseManager
from ui.i18n import t


class DiodeCardTests(unittest.TestCase):
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
        cls.app._ensure_tab("board_diag")

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
        self.path = os.path.join(self.d.name, "notes.json")
        self.app._diode_notebook = lambda: DiodeNotebook(self.path)
        for e in list(self.app.diode_entries.values()) + [self.app.schem_model_entry]:
            e.delete(0, "end")

    def tearDown(self):
        del self.app._diode_notebook
        self.d.cleanup()

    def enter(self, point, reading, board):
        app = self.app
        for key, value in (("ui.diode_point", point), ("ui.diode_reading", reading)):
            app.diode_entries[key].delete(0, "end")
            app.diode_entries[key].insert(0, value)
        app.diode_board.set(t(f"diode.board.{board}", "en"))
        app._diode_save()

    def text(self):
        self.app.update()
        return self.app.diode_text.get("1.0", "end")

    def test_readings_are_saved_and_compared_with_the_reference_board(self):
        self.app.diode_entries["ui.diode_model"].insert(0, "SM-A525F")
        self.enter("VBAT", "0.412", "reference")
        self.enter("VBAT", "405", "test")
        self.enter("VDD_CPU", "380", "reference")
        self.enter("VDD_CPU", "300", "test")
        text = self.text()
        self.assertIn(t("ui.diode_header", "en", model="SM-A525F", count=2), text)
        rows = {r.point: r.kind for r in self.app.last_diode_rows}
        self.assertEqual(rows, {"VBAT": "same", "VDD_CPU": "differs"})
        self.assertIn("[!] VDD_CPU", text)
        self.assertIn("[ ] VBAT", text)
        self.assertNotIn("PASS", text)
        self.assertNotIn("FAIL", text)
        self.assertEqual(DiodeNotebook(self.path).points("SM-A525F")["VBAT"]["reference"]["mv"], 412.0)
        self.assertEqual(self.app.diode_entries["ui.diode_reading"].get(), "")     # ready for the next point

    def test_the_model_comes_from_the_schematics_card(self):
        self.app.schem_model_entry.insert(0, "SM-A125F")
        self.enter("VBAT", "OL", "reference")
        self.assertEqual(self.app.diode_entries["ui.diode_model"].get(), "SM-A125F")
        self.assertIn("SM-A125F", DiodeNotebook(self.path).models())

    def test_a_bad_reading_is_refused_and_not_saved(self):
        self.app.diode_entries["ui.diode_model"].insert(0, "SM-A525F")
        self.enter("VBAT", "abc", "test")
        self.assertIn(t("diode.bad_reading", "en", text="abc"), self.text())
        self.assertFalse(os.path.exists(self.path))

    def test_no_model_says_so(self):
        self.enter("VBAT", "400", "test")
        self.assertIn(t("diode.no_model", "en"), self.text())


if __name__ == "__main__":
    unittest.main()
