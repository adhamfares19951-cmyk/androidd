"""The schematics panel in the Motherboard tab: matched on the connected phone's own model / codename / maker,
only real files can be opened, a fetch is checked against its checksum, and the technician can add files.
Every test uses its own temporary folders: nothing is read from or written to the real user folders."""
import gc
import hashlib
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.result import Status
from database.db_manager import DatabaseManager
from tests.fakes import FakeBridge
from ui.i18n import t

RUN_NOW = SimpleNamespace(Thread=lambda target=None, daemon=None, **kw: SimpleNamespace(start=target))
A52 = {"ro.product.model": "SM-A525F", "ro.product.device": "a52q", "ro.product.manufacturer": "samsung"}


class PanelTests(unittest.TestCase):
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
        base = self.d.name
        self.index = os.path.join(base, "user_index.json")
        self.local = os.path.join(base, "schematics")
        self.cache = os.path.join(base, "cache")
        os.makedirs(self.local)
        app = self.app
        self.saved = (app.bridge, app._schematics_index_path, app._schematics_local_dir, app._schematics_cache_dir)
        app._schematics_index_path = lambda: self.index
        app._schematics_local_dir = lambda: self.local
        app._schematics_cache_dir = lambda: self.cache
        app.bridge = FakeBridge(props=dict(A52))
        app.current_serial.set("S1")
        self.threads = patch("ui.tabs.schematics.threading", RUN_NOW)
        self.threads.start()

    def tearDown(self):
        self.threads.stop()
        app = self.app
        app.bridge, app._schematics_index_path, app._schematics_local_dir, app._schematics_cache_dir = self.saved
        app.current_serial.set("")
        self.d.cleanup()

    def write_index(self, docs):
        with open(self.index, "w", encoding="utf-8") as f:
            json.dump({"schema": 1, "documents": docs}, f)

    def put(self, rel, data=b"%PDF-1.4"):
        path = os.path.join(self.local, rel)
        with open(path, "wb") as f:
            f.write(data)
        return path

    def text(self):
        self.app.update()
        return self.app.schem_text.get("1.0", "end")

    def test_found_for_the_connected_phone_by_its_own_model(self):
        self.write_index([{"id": "a52", "doc_type": "schematic", "component": "A52 main board",
                           "models": ["SM-A525F"], "file": "a52.pdf"},
                          {"id": "other", "doc_type": "schematic", "component": "A12 board",
                           "models": ["SM-A125F"], "file": "a12.pdf"}])
        self.put("a52.pdf")
        self.app._schematics_find_for_phone()
        text = self.text()
        self.assertIn(t("ui.schem_device", "en", model="SM-A525F", codename="a52q", maker="samsung"), text)
        self.assertIn(t("ui.schem_count", "en", count=1), text)
        self.assertEqual([(r.doc.id, r.status) for r in self.app.last_schematics], [("a52", Status.PASS)])
        self.assertEqual(self.app.schem_model_entry.get(), "SM-A525F")       # the field shows what was matched

    def test_codename_of_another_maker_is_not_matched(self):
        self.write_index([{"id": "moto", "doc_type": "schematic", "component": "Moto board",
                           "codenames": ["a52q"], "manufacturer": "motorola", "file": "m.pdf"}])
        self.put("m.pdf")
        self.app._schematics_find_for_phone()
        self.assertIn(t("ui.schem_none", "en", query="SM-A525F / a52q"), self.text())
        self.assertEqual(self.app.last_schematics, [])

    def test_no_phone_says_so_and_nothing_is_invented(self):
        self.app.current_serial.set("")
        self.app._schematics_find_for_phone()
        self.assertIn(t("ui.schem_no_phone", "en"), self.text())
        self.write_index([])
        self.app.schem_model_entry.delete(0, "end")
        self.app.schem_model_entry.insert(0, "SM-A525F")
        self.app._schematics_search()
        self.assertIn(t("ui.schem_none", "en", query="SM-A525F"), self.text())   # the bundled index is empty

    def test_only_a_real_file_can_be_opened_and_a_source_can_be_fetched(self):
        data = b"%PDF-1.4 fetched"
        self.write_index([{"id": "on", "doc_type": "boardview", "component": "A52 board", "models": ["SM-A525F"],
                           "file": "a52.bvr", "source": "https://example.invalid/a52.bvr",
                           "sha256": hashlib.sha256(data).hexdigest()}])
        self.app._schematics_find_for_phone()
        self.app.update()
        (res,) = self.app.last_schematics
        self.assertEqual((res.status, res.origin), (Status.UNKNOWN, "on_demand"))
        with patch("core.schematics_db.SchematicsDatabase._download", return_value=data) as dl:
            self.app._schematics_fetch(res)
            self.app.update()
        dl.assert_called_once()
        self.assertIn(t("schematic.fetched", "en", component="A52 board", type="boardview"), self.text())
        (res,) = self.app.last_schematics                                      # refreshed after the fetch
        self.assertEqual((res.status, res.origin), (Status.PASS, "cache"))
        with open(res.path, "rb") as f:
            self.assertEqual(f.read(), data)

    def test_a_tampered_download_is_a_fail_and_not_kept(self):
        self.write_index([{"id": "on", "doc_type": "schematic", "component": "A52 board", "models": ["SM-A525F"],
                           "file": "a52.pdf", "source": "https://example.invalid/a52.pdf", "sha256": "0" * 64}])
        self.app._schematics_find_for_phone()
        self.app.update()
        (res,) = self.app.last_schematics
        with patch("core.schematics_db.SchematicsDatabase._download", return_value=b"something else"):
            self.app._schematics_fetch(res)
            self.app.update()
        self.assertIn("[FAIL]", self.text())
        self.assertFalse(os.path.exists(os.path.join(self.cache, "a52.pdf")))

    def test_adding_a_file_makes_it_found_for_that_model(self):
        src = os.path.join(self.d.name, "board.pdf")
        with open(src, "wb") as f:
            f.write(b"%PDF-1.4 mine")
        doc = self.app._schematics_add_from(src, "schematic", "A52 main board", model="SM-A525F")
        self.assertIsNotNone(doc)
        self.app.update()
        self.assertEqual([(r.doc.component, r.status) for r in self.app.last_schematics],
                         [("A52 main board", Status.PASS)])
        self.assertTrue(os.path.isfile(os.path.join(self.local, "board.pdf")))
        bad = self.app._schematics_add_from(src, "schematic", "", model="SM-A525F")
        self.assertIsNone(bad)
        self.assertIn(t("schematic.add_no_component", "en"), self.text())

    def test_pdf_opens_in_the_system_viewer_and_an_image_inside_the_program(self):
        pdf = self.put("a52.pdf")
        with patch("ui.tabs.schematics.os.startfile", create=True) as start:
            self.app._schematics_open(pdf)
        start.assert_called_once_with(pdf)
        from PIL import Image
        png = os.path.join(self.local, "points.png")
        Image.new("RGB", (40, 30), "white").save(png)
        with patch("ui.tabs.schematics.os.startfile", create=True) as start:
            self.app._schematics_open(png)
            self.app.update()
        start.assert_not_called()
        self.assertEqual(self.app._schem_viewer.title(), "points.png")
        self.app._schem_viewer.destroy()


if __name__ == "__main__":
    unittest.main()
