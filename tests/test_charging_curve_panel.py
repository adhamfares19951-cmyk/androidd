"""
Unit tests for the Charging Curve panel (ui.tabs.charging_curve).
"""

import csv
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from core.charging_curve import ChargingCurve, ChargingSample
from database.db_manager import DatabaseManager
from core.result import Status
from ui import main_window


class TestChargingCurvePanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        if os.path.exists(self.db_path):
            try:
                os.unlink(self.db_path)
            except OSError:
                pass

    def _build_app(self, lang="en"):
        db_factory = lambda *a, **kw: DatabaseManager(db_path=self.db_path)
        with (
            patch.object(main_window, "DatabaseManager", side_effect=db_factory),
            patch.object(main_window.AndroidDoctorApp, "_start_device_monitor", return_value=None),
            patch.object(main_window.AndroidDoctorApp, "refresh_devices_async", return_value=None),
            patch.object(main_window.AndroidDoctorApp, "_auto_poll_devices", return_value=None),
            patch.object(main_window, "verify_binaries", return_value={"ok": True, "packages": {}}),
        ):
            app = main_window.AndroidDoctorApp(lang=lang)
            app.withdraw()
            app._ensure_tab("board_diag")
            return app

    def test_panel_components_exist(self):
        app = self._build_app("en")
        try:
            self.assertTrue(hasattr(app, "charging_curve_card"))
            self.assertIn("charge_curve", app.status_pills)
            self.assertTrue(hasattr(app, "btn_cc_start"))
            self.assertTrue(hasattr(app, "btn_cc_stop"))
            self.assertTrue(hasattr(app, "btn_cc_export"))
            self.assertTrue(hasattr(app, "cc_canvas"))
            self.assertTrue(hasattr(app, "cc_console"))
            self.assertTrue(hasattr(app, "combo_cc_dur"))
            self.assertEqual(app.combo_cc_dur.get(), "60s")
        finally:
            app.destroy()

    def test_canvas_draw_empty_and_with_samples(self):
        app = self._build_app("en")
        try:
            # Empty state rendering
            app._draw_cc_canvas()

            # Add sample and render
            sample1 = ChargingSample(t_seconds=1.0, voltage_mv=4000.0, current_ma=1500.0, temp_c=31.0, is_charging=True)
            sample2 = ChargingSample(t_seconds=2.0, voltage_mv=4050.0, current_ma=1800.0, temp_c=32.0, is_charging=True)
            app.cc_samples = [sample1, sample2]
            app._cc_requested_duration = 60.0
            app._draw_cc_canvas()

            # Canvas items should be created
            items = app.cc_canvas.find_all()
            self.assertGreater(len(items), 5)
        finally:
            app.destroy()

    def test_sample_progress_and_completion_callbacks(self):
        app = self._build_app("en")
        try:
            sample = ChargingSample(t_seconds=5.0, voltage_mv=4100.0, current_ma=2000.0, temp_c=33.5, is_charging=True)
            app._on_cc_sample(sample, total_s=60.0)

            self.assertEqual(len(app.cc_samples), 1)
            self.assertIn("5s / 60s", app.lbl_cc_elapsed.cget("text"))
            self.assertIn("4.10 V", app.lbl_cc_voltage.cget("text"))
            self.assertIn("2000 mA", app.lbl_cc_current.cget("text"))
            self.assertIn("8.20 W", app.lbl_cc_power.cget("text"))
            self.assertIn("33.5 °C", app.lbl_cc_temp.cget("text"))

            # Simulate completion
            curve = ChargingCurve(app.cc_samples, requested_s=60.0)
            app._on_cc_done(curve)

            self.assertFalse(app.cc_capturing)
            self.assertEqual(app.btn_cc_start.cget("state"), "normal")
            self.assertEqual(app.btn_cc_stop.cget("state"), "disabled")
            self.assertEqual(app.btn_cc_export.cget("state"), "normal")
            text_content = app.cc_console.get("1.0", "end")
            self.assertIn("8.20", text_content)
        finally:
            app.destroy()

    def test_csv_export(self):
        app = self._build_app("en")
        try:
            samples = [
                ChargingSample(t_seconds=0.0, voltage_mv=3900.0, current_ma=1000.0, temp_c=29.0, is_charging=True),
                ChargingSample(t_seconds=1.0, voltage_mv=3950.0, current_ma=1500.0, temp_c=29.5, is_charging=True),
            ]
            curve = ChargingCurve(samples, requested_s=60.0)
            app.last_charge_curve = curve

            out_csv = os.path.join(tempfile.gettempdir(), "test_charge_curve_out.csv")
            with patch("tkinter.filedialog.asksaveasfilename", return_value=out_csv), \
                 patch("tkinter.messagebox.showinfo") as mock_info:
                app.export_charge_curve_csv()
                mock_info.assert_called()

            self.assertTrue(os.path.exists(out_csv))
            with open(out_csv, "r", encoding="utf-8") as f:
                reader = csv.DictReader(f)
                rows = list(reader)
                self.assertEqual(len(rows), 2)
                self.assertEqual(rows[0]["voltage_mv"], "3900.0")
                self.assertEqual(rows[1]["current_ma"], "1500.0")
            os.unlink(out_csv)
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
