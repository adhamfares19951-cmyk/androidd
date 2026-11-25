"""
UI tests for DataWipeMixin (Data Erasure Certificate card in Reports tab).
"""

import os
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.result import Status
from ui.i18n import t
from ui.tabs.data_wipe import DataWipeMixin

ENCRYPTED_PROPS = {
    "ro.crypto.state": "encrypted",
    "ro.crypto.type": "file",
    "ro.product.model": "Pixel 6",
    "ro.product.brand": "Google",
    "ro.build.version.release": "13",
    "ro.build.id": "TP1A.220624.014",
    "ro.serialno": "TEST123456",
}


class _FakeBridge:
    def __init__(self, props=None, setup_complete="0", rooted=False):
        self.props = props or {}
        self.setup_complete = setup_complete
        self.rooted = rooted
        self.commands = []

    def get_prop(self, name, serial=None):
        return self.props.get(name, "")

    def is_root_available(self, serial=None):
        return self.rooted

    def shell(self, cmd, serial=None, timeout=10, root=False, use_socket=True):
        self.commands.append(cmd)
        if "user_setup_complete" in cmd:
            return (0, self.setup_complete, "")
        if "MASTER_CLEAR" in cmd:
            return (0, "", "")
        return (0, "", "")


class MockReportsTab(DataWipeMixin):
    current_lang = "en"

    def __init__(self, bridge):
        self.current_serial = SimpleNamespace(get=lambda: "TEST123456")
        self.bridge = bridge
        self.wipe_text = object()
        self.boxes = []
        self.pills = {}
        self.wipe_operator_entry = SimpleNamespace(get=lambda: "Alice Tech")
        self.wipe_shop_entry = SimpleNamespace(get=lambda: "Apex Mobile Lab")
        self.wipe_notes_entry = SimpleNamespace(get=lambda: "Clean erasure confirmed")
        self.last_wipe_assessment = None
        self.last_wipe_certificate = None
        self.last_wipe_cert_files = None
        self.last_wipe_verified = False

    def _tr(self, key, **kw):
        return t(key, self.current_lang, **kw)

    def _m(self, msg):
        from ui.i18n import render
        return render(msg, self.current_lang)

    def _layout(self):
        return {"lead": "left", "trail": "right", "is_rtl": False, "anchor": "w"}

    def _set_box(self, box, text):
        self.boxes.append((box, text))

    def _set_pill(self, key, status):
        self.pills[key] = status

    def after(self, ms, fn):
        fn()


class TestDataWipePanel(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        self.tmp.cleanup()

    def test_run_wipe_assess_async_encrypted(self):
        b = _FakeBridge(ENCRYPTED_PROPS)
        tab = MockReportsTab(b)

        with patch("ui.tabs.data_wipe.threading",
                   SimpleNamespace(Thread=lambda target=None, daemon=None, **kw: SimpleNamespace(start=target))):
            tab.run_wipe_assess_async()

        self.assertIsNotNone(tab.last_wipe_assessment)
        self.assertEqual(tab.last_wipe_assessment.status, Status.PASS)
        self.assertEqual(tab.pills.get("wipe"), str(Status.PASS))
        last_text = [t for box, t in tab.boxes if box is tab.wipe_text][-1]
        self.assertIn("Pixel 6", last_text)
        self.assertIn("FBE", last_text)

    def test_initiate_wipe_reset_declined_does_nothing(self):
        b = _FakeBridge(ENCRYPTED_PROPS)
        tab = MockReportsTab(b)

        with patch("ui.tabs.data_wipe.messagebox.askyesno", return_value=False):
            tab.initiate_wipe_reset_async()

        self.assertEqual(b.commands, [])

    def test_initiate_wipe_reset_confirmed(self):
        b = _FakeBridge(ENCRYPTED_PROPS, rooted=True)
        tab = MockReportsTab(b)

        with patch("ui.tabs.data_wipe.messagebox.askyesno", return_value=True), \
             patch("ui.tabs.data_wipe.threading",
                   SimpleNamespace(Thread=lambda target=None, daemon=None, **kw: SimpleNamespace(start=target))):
            tab.initiate_wipe_reset_async()

        self.assertTrue(any("MASTER_CLEAR" in c for c in b.commands))

    def test_run_wipe_verify_async_success(self):
        b = _FakeBridge(ENCRYPTED_PROPS, setup_complete="0")
        tab = MockReportsTab(b)

        with patch("ui.tabs.data_wipe.threading",
                   SimpleNamespace(Thread=lambda target=None, daemon=None, **kw: SimpleNamespace(start=target))):
            tab.run_wipe_verify_async()

        self.assertTrue(tab.last_wipe_verified)
        self.assertEqual(tab.pills.get("wipe"), "PASS")
        last_text = [t for box, t in tab.boxes if box is tab.wipe_text][-1]
        self.assertIn("SUCCEEDED", last_text)

    def test_issue_wipe_certificate_writes_json_and_html(self):
        b = _FakeBridge(ENCRYPTED_PROPS, setup_complete="0")
        tab = MockReportsTab(b)

        # 1. Assess
        with patch("ui.tabs.data_wipe.threading",
                   SimpleNamespace(Thread=lambda target=None, daemon=None, **kw: SimpleNamespace(start=target))):
            tab.run_wipe_assess_async()
            tab.last_wipe_verified = True

        # 2. Issue cert into temp dir
        with patch("ui.tabs.data_wipe.REPORTS_DIR", self.tmp.name), \
             patch("ui.tabs.data_wipe.threading",
                   SimpleNamespace(Thread=lambda target=None, daemon=None, **kw: SimpleNamespace(start=target))):
            tab.issue_wipe_certificate_async()

        self.assertIsNotNone(tab.last_wipe_certificate)
        self.assertIsNotNone(tab.last_wipe_cert_files)
        json_path = tab.last_wipe_cert_files["json_path"]
        html_path = tab.last_wipe_cert_files["html_path"]
        self.assertTrue(os.path.isfile(json_path))
        self.assertTrue(os.path.isfile(html_path))
        with open(html_path, "r", encoding="utf-8") as f:
            content = f.read()
        self.assertIn("CERTIFICATE OF SECURE DATA ERASURE", content)
        self.assertIn("Pixel 6", content)
        self.assertIn("Alice Tech", content)


if __name__ == "__main__":
    unittest.main()
