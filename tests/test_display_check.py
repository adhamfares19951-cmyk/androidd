"""Screen originality indicators (read only). Software cannot prove a screen is original: the check never
gives PASS or FAIL. It lists indicators (supports / against / information) with evidence and an assessment:
consistent with original, indicators against, or not enough data. Built from a Samsung S23 Ultra's readings."""
import io
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main as app_main
from core.display_check import DisplayOriginalityCheck
from tests.fakes import FakeBridge, PERMISSION
from ui.i18n import render

DISPLAY = "\n".join(
    [f"  DisplayModeRecord{{mMode={{id={i}, width={w}, height={h}, fps={f}, vsync={f}}}}}"
     for i, (w, h, f) in enumerate([(1440, 3088, "120.00001"), (1440, 3088, "60.000004"), (1440, 3088, "10.0"),
                                     (1440, 3088, "24.000002"), (1080, 2316, "120.00001")])]
    + ["    supportedHdrTypes=[2, 3, 4]"])
FP_DUMP = ('Dumping for sensorId: 0, provider: FingerprintProvider\n{"service":"FingerprintProvider/default",'
           '"prints":[{"id":0,"count":3,"accept":56,"reject":11,"quality":6,"acquire":436}]}')
S23U = {
    "getprop ro.product.manufacturer": (0, "samsung", ""),
    "getprop ro.product.model": (0, "SM-S918B", ""),
    "lcd/panel/lcd_type": (0, "SDC_812596", ""),
    "lcd/panel/manufacture_date": (0, "manufacture date : 20220208", ""),
    "lcd/panel/cell_id": (0, "b208043b3921140bf10c91", ""),
    "lcd/panel/octa_id": (0, "40101ccA3AZ1S1CMWJAJ046", ""),
    "sec/tsp/fod_info": (0, "18,19,43,18,39", ""),
    "dumpsys display": (0, DISPLAY, ""),
    "pm has-feature android.hardware.fingerprint": (0, "true", ""),
    "dumpsys fingerprint": (0, FP_DUMP, ""),
    "logcat -b crash": (0, "", ""),
}


def crash(pid):
    path = "/vendor/bin/hw/vendor.samsung.hardware.biometrics.fingerprint@3.0-service"
    return (f"F DEBUG   : pid: {pid}, tid: {pid}, name: fingerprint  >>> {path} <<<\n"
            f"F DEBUG   : signal 11 (SIGSEGV), code 1 (SEGV_MAPERR), fault addr 0x0")


def run(routes):
    return DisplayOriginalityCheck(FakeBridge(routes)).run("s")


def effects(r):
    return {i.key: i.effect for i in r.indicators}


class TestAssessment(unittest.TestCase):
    def test_s23_ultra_is_consistent_with_original(self):
        r = run(dict(S23U))
        self.assertEqual(r.assessment, "consistent")
        e = effects(r)
        self.assertEqual(e["panel_maker"], "supports")           # Samsung Display panel on a Samsung phone
        self.assertEqual(e["panel_ids"], "supports")             # panel serial numbers read from the panel
        self.assertEqual(e["fp_under_display"], "supports")      # under-display fingerprint has worked
        self.assertEqual(e["display_modes"], "info")             # specs are reported, not judged
        self.assertEqual(r.status, "UNKNOWN")                    # never PASS: software cannot prove it
        self.assertEqual(r.native_resolution, "1440x3088")
        self.assertEqual(r.max_refresh_hz, 120)
        self.assertTrue(all(i.evidence for i in r.indicators))

    def test_crashing_under_display_fingerprint_is_against(self):
        routes = dict(S23U)
        routes["logcat -b crash"] = (0, "\n".join(crash(p) for p in (101, 102, 103)), "")
        r = run(routes)
        self.assertEqual(r.assessment, "against")
        self.assertEqual(effects(r)["fp_under_display"], "against")
        self.assertEqual(r.status, "UNKNOWN")                    # still not FAIL: a damaged flex does this too

    def test_panel_without_serial_numbers_is_against(self):
        routes = dict(S23U)
        routes["lcd/panel/cell_id"] = (0, "0000000000000000000000", "")
        routes["lcd/panel/octa_id"] = (0, "00000000000000000000000", "")
        self.assertEqual(effects(run(routes))["panel_ids"], "against")

    def test_other_panel_maker_on_a_samsung_is_only_information(self):
        # mid-range Samsung models ship with BOE / CSOT panels from the factory
        routes = dict(S23U)
        routes["lcd/panel/lcd_type"] = (0, "BOE_0a1b2c", "")
        r = run(routes)
        self.assertEqual(effects(r)["panel_maker"], "info")
        self.assertNotEqual(r.assessment, "against")

    def test_blocked_panel_files_are_not_against(self):
        routes = dict(S23U)
        for f in ("lcd_type", "manufacture_date", "cell_id", "octa_id"):
            routes[f"lcd/panel/{f}"] = PERMISSION
        r = run(routes)
        self.assertEqual(effects(r)["panel_maker"], "info")
        self.assertNotIn("against", effects(r).values())

    def test_phone_without_readable_data_is_insufficient(self):
        r = run({"getprop ro.product.manufacturer": (0, "xiaomi", ""), "dumpsys display": (0, DISPLAY, "")})
        self.assertEqual(r.assessment, "insufficient")
        self.assertEqual(r.status, "UNKNOWN")

    def test_messages_render(self):
        r = run(dict(S23U))
        for lang in ("ar", "en", "fr"):
            self.assertTrue(render(r.diagnosis, lang))
            for i in r.indicators:
                self.assertTrue(render(i.text, lang))


class TestHardwareTabButton(unittest.TestCase):
    def test_button_shows_assessment_and_evidence(self):
        from types import SimpleNamespace
        from ui.i18n import t
        from ui.tabs.hardware import HardwareTabMixin

        class Tab(HardwareTabMixin):
            current_lang = "en"

            def __init__(self):
                self.current_serial = SimpleNamespace(get=lambda: "s")
                self.bridge = FakeBridge(dict(S23U))
                self.display_check_text = object()
                self.boxes = []

            def _tr(self, key, **kw):
                return t(key, "en", **kw)

            def _ensure_tab(self, key):
                pass

            def _set_box(self, box, text):
                self.boxes.append(text)

            def after(self, ms, fn):
                fn()

        tab = Tab()
        with patch("ui.tabs.hardware.threading",
                   SimpleNamespace(Thread=lambda target=None, daemon=None, **kw: SimpleNamespace(start=target))):
            tab.run_display_check_async()
        text = tab.boxes[-1]
        self.assertIn("Consistent with an original screen", text)
        self.assertIn("SDC_812596", text)
        self.assertIn("cannot prove", text)
        self.assertEqual(tab.last_display_check.assessment, "consistent")


class TestCli(unittest.TestCase):
    def test_screen_check_prints_assessment_and_indicators(self):
        b = FakeBridge(dict(S23U))
        b.devices = [{"serial": "R5CW523A5EW", "state": "device", "model": "SM_S918B"}]
        out = io.StringIO()
        with patch("core.adb_bridge.ADBBridge", lambda: b), redirect_stdout(out), \
                patch("sys.argv", ["main.py", "--screen-check", "--lang", "en"]):
            code = app_main.main()
        self.assertEqual(code, app_main.EXIT_OK)
        text = out.getvalue()
        self.assertIn("SDC_812596", text)
        self.assertIn("1440x3088", text)
        self.assertIn("cannot prove", text)

    def test_no_device_is_exit_1(self):
        b = FakeBridge()
        out = io.StringIO()
        with patch("core.adb_bridge.ADBBridge", lambda: b), redirect_stdout(out), \
                patch("sys.argv", ["main.py", "--screen-check", "--lang", "en"]):
            self.assertEqual(app_main.main(), app_main.EXIT_NO_DEVICE)


if __name__ == "__main__":
    unittest.main()
