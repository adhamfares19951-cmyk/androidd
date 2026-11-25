"""
UI tests for the Network & SIM diagnosis screen in HardwareTabMixin.
"""

import os
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.fakes import FakeBridge
from ui.i18n import t
from ui.tabs.hardware import HardwareTabMixin
from core.result import Status

SAMPLE_IMEI = "490154203237518"
SAMPLE_IMEI_PARCEL = (
    "Result: Parcel(00000000 0000000f 00340039 00300031 00350034 00320030 00330032 00330037 00350031 00380000 '....4.9.0.1.5.4.2.0.3.2.3.7.5.1.8.')"
)
SAMPLE_BASEBAND = "G998BXXU9EWA6"


class MockTab(HardwareTabMixin):
    current_lang = "en"

    def __init__(self, bridge):
        self.current_serial = SimpleNamespace(get=lambda: "s1")
        self.bridge = bridge
        self.network_diag_text = object()
        self.sim_text = object()
        self.boxes = []
        self.pills = {}
        self.net_manual_frame = MagicMock()

    def _tr(self, key, **kw):
        return t(key, self.current_lang, **kw)

    def _ensure_tab(self, key):
        pass

    def _layout(self):
        return {"lead": "left", "trail": "right", "is_rtl": False}

    def _set_box(self, box, text):
        self.boxes.append((box, text))

    def _set_pill(self, key, status):
        self.pills[key] = status

    def after(self, ms, fn):
        fn()


class TestNetworkDiagPanel(unittest.TestCase):
    def test_run_network_diag_passes_and_updates_ui(self):
        routes = {
            "getprop gsm.sim.state": (0, "READY", ""),
            "getprop gsm.version.baseband": (0, SAMPLE_BASEBAND, ""),
            "service call iphonesubinfo": (0, SAMPLE_IMEI_PARCEL, ""),
            "dumpsys telephony.registry": (0, "mVoiceRegState=0(IN_SERVICE)\nmSignalStrength=level=4 rssi=-65 dBm", ""),
        }
        b = FakeBridge(routes)
        tab = MockTab(b)

        with patch("ui.tabs.hardware.threading",
                   SimpleNamespace(Thread=lambda target=None, daemon=None, **kw: SimpleNamespace(start=target))):
            tab.run_network_diag_async()

        self.assertIsNotNone(tab.last_network_diag)
        self.assertEqual(tab.last_network_diag.status, Status.PASS)
        self.assertEqual(tab.pills.get("network"), str(Status.PASS))
        self.assertEqual(tab.pills.get("sim"), str(Status.PASS))

        last_text = [text for box, text in tab.boxes if box is tab.network_diag_text][-1]
        self.assertIn("READY", last_text)
        self.assertIn(SAMPLE_BASEBAND, last_text)
        self.assertIn(SAMPLE_IMEI, last_text)
        self.assertIn("Level 4", last_text)
        tab.net_manual_frame.pack_forget.assert_called()

    def test_run_network_diag_sim_absent_shows_manual_step(self):
        routes = {
            "getprop gsm.sim.state": (0, "ABSENT", ""),
            "getprop gsm.version.baseband": (0, SAMPLE_BASEBAND, ""),
            "service call iphonesubinfo": (0, SAMPLE_IMEI_PARCEL, ""),
            "dumpsys telephony.registry": (0, "level=0", ""),
        }
        b = FakeBridge(routes)
        tab = MockTab(b)

        with patch("ui.tabs.hardware.threading",
                   SimpleNamespace(Thread=lambda target=None, daemon=None, **kw: SimpleNamespace(start=target))):
            tab.run_network_diag_async()

        self.assertEqual(tab.last_network_diag.status, Status.UNKNOWN)
        self.assertIsNotNone(tab.last_network_diag.manual_step)
        tab.net_manual_frame.pack.assert_called()

    def test_run_network_diag_with_manual_answer_concludes_fail(self):
        routes = {
            "getprop gsm.sim.state": (0, "ABSENT", ""),
            "getprop gsm.version.baseband": (0, SAMPLE_BASEBAND, ""),
            "service call iphonesubinfo": (0, SAMPLE_IMEI_PARCEL, ""),
            "dumpsys telephony.registry": (0, "level=0", ""),
        }
        b = FakeBridge(routes)
        tab = MockTab(b)

        with patch("ui.tabs.hardware.threading",
                   SimpleNamespace(Thread=lambda target=None, daemon=None, **kw: SimpleNamespace(start=target))):
            tab.run_network_diag_async(answers={"known_good_sim": "inserted"})

        self.assertEqual(tab.last_network_diag.status, Status.FAIL)
        self.assertEqual(tab.last_network_diag.fault_area, "sim_reader")
        self.assertEqual(tab.pills.get("network"), str(Status.FAIL))
        tab.net_manual_frame.pack_forget.assert_called()

    def test_run_network_diag_no_serial_shows_warning(self):
        tab = MockTab(FakeBridge())
        tab.current_serial = SimpleNamespace(get=lambda: "")
        with patch("ui.tabs.hardware.messagebox.showwarning") as warn_mock:
            tab.run_network_diag_async()
            warn_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
