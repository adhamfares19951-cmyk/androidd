"""Setup-wizard / provisioning: informational FRP and OEM-unlock properties, opening a URL on the phone,
and the Rescue tab handlers that show them. Nothing is invented: an empty property stays "UNKNOWN"."""
import unittest
from unittest.mock import patch

from core.provisioning_manager import ProvisioningManager
from tests.fakes import FakeBridge
from ui.i18n import render, t

PROPS = {"getprop ro.frp.pst": (0, "/dev/block/by-name/frp", ""),
         "getprop sys.oem_unlock_allowed": (0, "0", ""),
         "getprop ro.oem_unlock_supported": (0, "1", ""),
         "getprop ro.boot.cid": (0, "XIAOMI_GLOBAL", ""),
         "settings get global device_provisioned": (0, "1", ""),
         "settings get secure user_setup_complete": (0, "1", "")}


class TestProvisioningStatus(unittest.TestCase):
    def test_reads_frp_and_oem_unlock_properties(self):
        st = ProvisioningManager(FakeBridge(dict(PROPS))).get_provisioning_status("s")
        self.assertEqual(st.pst_block_path, "/dev/block/by-name/frp")
        self.assertEqual(st.oem_unlock_allowed, "0")
        self.assertEqual(st.oem_unlock_supported, "1")
        self.assertTrue(st.is_device_provisioned)
        self.assertTrue(st.is_user_setup_complete)
        d = st.to_dict()
        for k in ("pst_block_path", "oem_unlock_allowed", "oem_unlock_supported"):
            self.assertIn(k, d)

    def test_empty_properties_stay_unknown(self):
        routes = dict(PROPS)
        routes.update({"getprop ro.frp.pst": (0, "", ""), "getprop sys.oem_unlock_allowed": (0, "\n", ""),
                       "getprop ro.oem_unlock_supported": (1, "", "getprop: error")})
        st = ProvisioningManager(FakeBridge(routes)).get_provisioning_status("s")
        self.assertEqual((st.pst_block_path, st.oem_unlock_allowed, st.oem_unlock_supported),
                         ("UNKNOWN", "UNKNOWN", "UNKNOWN"))

    def test_carrier_id_is_never_used_as_the_frp_path(self):
        routes = dict(PROPS)
        routes["getprop ro.frp.pst"] = (0, "", "")
        b = FakeBridge(routes)
        st = ProvisioningManager(b).get_provisioning_status("s")
        self.assertEqual(st.pst_block_path, "UNKNOWN")
        self.assertFalse([c for c in b.commands if "ro.boot.cid" in c])

    def test_supported_is_not_reported_as_allowed(self):
        routes = dict(PROPS)
        routes["getprop sys.oem_unlock_allowed"] = (0, "", "")
        st = ProvisioningManager(FakeBridge(routes)).get_provisioning_status("s")
        self.assertEqual(st.oem_unlock_allowed, "UNKNOWN")
        self.assertEqual(st.oem_unlock_supported, "1")

    def test_flag_logic_unchanged(self):
        routes = dict(PROPS)
        routes["settings get secure user_setup_complete"] = (0, "0", "")
        st = ProvisioningManager(FakeBridge(routes)).get_provisioning_status("s")
        self.assertTrue(st.is_device_provisioned)
        self.assertFalse(st.is_user_setup_complete)
        self.assertEqual(st.status_summary.key, "prov.user_pending")


class TestLaunchBrowserUrl(unittest.TestCase):
    def test_opened(self):
        ok, msg = ProvisioningManager(FakeBridge({"am start": (0, "Starting: Intent { act=android.intent.action.VIEW }", "")})) \
            .launch_browser_url("example.com", "s")
        self.assertTrue(ok)
        self.assertEqual(msg.key, "prov.url_opened")

    def test_am_start_error_with_exit_zero_is_not_opened(self):
        reply = (0, "Starting: Intent { act=android.intent.action.VIEW dat=https://example.com/... }\n"
                    "Error: Activity not started, unable to resolve Intent", "")
        ok, msg = ProvisioningManager(FakeBridge({"am start": reply})).launch_browser_url("example.com", "s")
        self.assertFalse(ok)
        self.assertEqual(msg.key, "prov.url_failed")
        self.assertIn("unable to resolve Intent", render(msg, "en"))


class _Var:
    def __init__(self, v):
        self.v = v

    def get(self):
        return self.v


class _Entry(_Var):
    pass


class _RunNow:
    """threading.Thread stand-in that runs the target at start()."""

    def __init__(self, target=None, daemon=None, **kw):
        self.target = target

    def start(self):
        self.target()


class FakeRescueTab:
    """Just what the provisioning handlers of RescueTabMixin use."""

    def __init__(self, serial, url="", bridge=None):
        from ui.tabs.rescue import RescueTabMixin
        self.current_serial = _Var(serial)
        self.prov_url_entry = _Entry(url)
        self.prov_log_text = object()
        self.current_lang = "en"
        self.provisioning = ProvisioningManager(bridge or FakeBridge(dict(PROPS)))
        self.boxes = []
        self.handlers = RescueTabMixin

    def _tr(self, key, **kw):
        return t(key, self.current_lang, **kw)

    def _m(self, value):
        return render(value, self.current_lang)

    def _set_box(self, box, text):
        self.boxes.append(text)

    def after(self, ms, fn):
        fn()


class TestRescueTabProvisioning(unittest.TestCase):
    def run_handler(self, tab, name):
        with patch("ui.tabs.rescue.threading.Thread", _RunNow), \
                patch("ui.tabs.rescue.messagebox") as mb:
            getattr(tab.handlers, name)(tab)
        return mb

    def test_inspect_shows_frp_and_oem_unlock(self):
        tab = FakeRescueTab("s")
        self.run_handler(tab, "_check_provisioning")
        text = tab.boxes[-1]
        self.assertIn("/dev/block/by-name/frp", text)
        self.assertIn(t("ui.oem_unlock_allowed", "en", value="0"), text)
        self.assertIn(t("ui.oem_unlock_supported", "en", value="1"), text)

    def test_open_url_needs_a_phone_and_a_url(self):
        tab = FakeRescueTab("", "https://example.com")
        mb = self.run_handler(tab, "_launch_prov_url")
        self.assertTrue(mb.showwarning.called)
        self.assertEqual(tab.boxes, [])
        tab = FakeRescueTab("s", "   ")
        mb = self.run_handler(tab, "_launch_prov_url")
        self.assertTrue(mb.showwarning.called)
        self.assertEqual(tab.boxes, [])

    def test_open_url_shows_the_result(self):
        b = FakeBridge({"am start": (0, "Starting: Intent { act=android.intent.action.VIEW }", "")})
        tab = FakeRescueTab("s", " example.com/help ", bridge=b)
        self.run_handler(tab, "_launch_prov_url")
        self.assertEqual(tab.boxes, [t("prov.url_opened", "en", url="https://example.com/help")])
        self.assertTrue(any(c.startswith("am start -a android.intent.action.VIEW") for c in b.commands))


class TestBatteryBadgeWithoutCheckStatuses(unittest.TestCase):
    def test_badge_uses_the_battery_check_status(self):
        import os
        import re
        import tempfile
        from core.board_diag import PMICStatus
        from ui.report_exporter import DiagnosticReportExporter
        pmic = PMICStatus(voltage_mv=3800.0, voltage_status="PASS", health="Dead")   # device reports a dead battery
        d = pmic.to_dict()
        self.assertEqual(d["check_status"], "FAIL")
        with tempfile.TemporaryDirectory() as tmp:
            path = DiagnosticReportExporter.generate_html_report(device_profile={"serial": "S"}, board_diag={"pmic": d},
                                                                 output_dir=tmp, lang="en")
            with open(path, encoding="utf-8") as f:
                html = f.read()
        header = re.search(re.escape(t("battery.fuel_gauge", "en")) + r"</span>(<span class=\"badge\"[^>]*>[A-Z_]+</span>)", html)
        self.assertIsNotNone(header)
        self.assertIn(">FAIL<", header.group(1))


if __name__ == "__main__":
    unittest.main()
