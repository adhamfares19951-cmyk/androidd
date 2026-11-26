"""
Unit tests for the IMEI Validation & Blacklist Lookup panel (ui.tabs.imei_check).
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from core.imei_check import BlacklistConfig
from database.db_manager import DatabaseManager
from ui import main_window


class TestImeiCheckPanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp.close()
        self.db_path = self.tmp.name

    def tearDown(self):
        DatabaseManager.reset_instance()
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
            app._ensure_tab("hardware_tester")
            return app

    def test_panel_components_exist(self):
        app = self._build_app("en")
        try:
            self.assertTrue(hasattr(app, "card_imei_check"))
            self.assertIn("imei", app.status_pills)
            self.assertTrue(hasattr(app, "entry_imei"))
            self.assertTrue(hasattr(app, "btn_imei_read"))
            self.assertTrue(hasattr(app, "btn_imei_validate"))
            self.assertTrue(hasattr(app, "btn_imei_blacklist"))
            self.assertTrue(hasattr(app, "btn_imei_byok"))
            self.assertTrue(hasattr(app, "lbl_imei_byok_status"))
            self.assertTrue(hasattr(app, "imei_text"))
        finally:
            app.destroy()

    def test_validate_imei_valid_and_invalid(self):
        app = self._build_app("en")
        try:
            # Valid GSMA sample IMEI
            app.entry_imei.delete(0, "end")
            app.entry_imei.insert(0, "490154203237518")
            app.validate_imei_action()
            out = app.imei_text.get("1.0", "end")
            self.assertIn("[PASS]", out)
            self.assertIn("490154203237518", out)

            # Invalid checksum
            app.entry_imei.delete(0, "end")
            app.entry_imei.insert(0, "490154203237519")
            app.validate_imei_action()
            out_bad = app.imei_text.get("1.0", "end")
            self.assertIn("[FAIL]", out_bad)
        finally:
            app.destroy()

    def test_byok_config_persistence_in_db(self):
        app = self._build_app("en")
        try:
            cfg = BlacklistConfig(
                endpoint="https://api.example.com/imei",
                api_key="secret-key-123",
                service_id="42",
                provider="sickw",
            )
            app._save_byok_config(cfg)
            loaded = app._load_byok_config()
            self.assertEqual(loaded.endpoint, "https://api.example.com/imei")
            self.assertEqual(loaded.api_key, "secret-key-123")
            self.assertEqual(loaded.service_id, "42")
            self.assertEqual(loaded.provider, "sickw")
            self.assertIn("api.example.com", app.lbl_imei_byok_status.cget("text"))
        finally:
            app.destroy()

    def test_blacklist_lookup_without_byok_is_unknown(self):
        app = self._build_app("en")
        try:
            app.entry_imei.delete(0, "end")
            app.entry_imei.insert(0, "490154203237518")

            # Run synchronously by patching Thread or calling check_blacklist directly via UI helper
            with patch("ui.tabs.imei_check.threading.Thread") as mock_thread:
                def run_sync(target, daemon=True):
                    m = type("T", (), {"start": lambda self: target()})()
                    return m
                mock_thread.side_effect = run_sync
                app.check_imei_blacklist_async()
                app.update()

            out = app.imei_text.get("1.0", "end")
            self.assertIn("[UNKNOWN]", out)
            self.assertIn("490154203237518", out)
        finally:
            app.destroy()


if __name__ == "__main__":
    unittest.main()
