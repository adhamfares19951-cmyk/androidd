"""
Layered diagnosis (core/layered_diag.py): walk each decision tree, check that a manual step is asked when
software cannot separate the part from its flex, and that the right component is concluded from the
technician's answers. Pure logic, no phone.
"""
import unittest

import core.layered_diag as L
from core.result import Status
from ui.i18n import render, set_current_language


class _FP:
    def __init__(self, error_status="OK", is_functional=None):
        self.error_status = error_status
        self.is_functional = is_functional
        self.evidence = []


class _BAT:
    def __init__(self, temp_c=None, health_verdict="UNKNOWN", true_health_pct=None):
        self.temp_c = temp_c
        self.health_verdict = health_verdict
        self.true_health_pct = true_health_pct
        self.evidence = []


class _CHG:
    def __init__(self, charger_type="UNKNOWN", is_ac_plugged=False, voltage_mv=None, current_ma=None):
        self.charger_type = charger_type
        self.is_ac_plugged = is_ac_plugged
        self.voltage_mv = voltage_mv
        self.current_ma = current_ma
        self.evidence = []


class FingerprintTree(unittest.TestCase):
    def test_working_sensor_is_not_applicable(self):
        self.assertEqual(L.diagnose_fingerprint(_FP("OK", True), {}).status, L.NOT_APPLICABLE)

    def test_flex_then_sensor_then_board(self):
        fp = _FP("HAL_CRASH_LOOP", False)
        r = L.diagnose_fingerprint(fp, {})
        self.assertEqual(r.status, L.NEEDS_MANUAL)
        self.assertEqual(r.manual_step.step_id, "fp_flex")
        self.assertEqual([k for k, _ in r.manual_step.options], [L.FIXED, L.SAME, L.SKIP])
        # a known-good flex fixes it -> the flex, high confidence
        r = L.diagnose_fingerprint(fp, {"fp_flex": L.FIXED})
        self.assertEqual((r.status, r.confidence), (L.CONCLUDED, "high"))
        # flex did not help -> ask for the sensor next
        r = L.diagnose_fingerprint(fp, {"fp_flex": L.SAME})
        self.assertEqual(r.manual_step.step_id, "fp_sensor")
        # sensor fixes it -> the sensor
        r = L.diagnose_fingerprint(fp, {"fp_flex": L.SAME, "fp_sensor": L.FIXED})
        self.assertEqual((r.status, r.confidence), (L.CONCLUDED, "high"))
        # neither helped -> the board line, medium confidence
        r = L.diagnose_fingerprint(fp, {"fp_flex": L.SAME, "fp_sensor": L.SAME})
        self.assertEqual((r.status, r.confidence), (L.CONCLUDED, "medium"))

    def test_skip_is_insufficient_not_a_guess(self):
        r = L.diagnose_fingerprint(_FP("HW_UNAVAILABLE", False), {"fp_flex": L.SKIP})
        self.assertEqual(r.status, L.INSUFFICIENT)
        self.assertIsNone(r.conclusion)


class BatteryTrees(unittest.TestCase):
    def test_impossible_temperature_only(self):
        self.assertEqual(L.diagnose_battery_temp(_BAT(temp_c=31.0), {}).status, L.NOT_APPLICABLE)
        self.assertEqual(L.diagnose_battery_temp(_BAT(temp_c=78.0), {}).status, L.NEEDS_MANUAL)
        self.assertEqual(L.diagnose_battery_temp(_BAT(temp_c=-20.0), {}).manual_step.step_id, "bat_temp_swap")
        r = L.diagnose_battery_temp(_BAT(temp_c=-20.0), {"bat_temp_swap": L.FIXED})
        self.assertEqual((r.status, r.confidence), (L.CONCLUDED, "high"))
        r = L.diagnose_battery_temp(_BAT(temp_c=-20.0), {"bat_temp_swap": L.SAME})
        self.assertEqual(r.confidence, "medium")      # the board NTC line

    def test_low_health_runs_only_on_a_fail_verdict(self):
        self.assertEqual(L.diagnose_battery_health(_BAT(health_verdict="PASS"), {}).status, L.NOT_APPLICABLE)
        bat = _BAT(health_verdict=str(Status.FAIL), true_health_pct=61.0)
        self.assertEqual(L.diagnose_battery_health(bat, {}).manual_step.step_id, "bat_health_swap")
        self.assertEqual(L.diagnose_battery_health(bat, {"bat_health_swap": L.FIXED}).confidence, "high")
        self.assertEqual(L.diagnose_battery_health(bat, {"bat_health_swap": L.SAME}).confidence, "medium")


class ChargingTree(unittest.TestCase):
    def test_no_symptom_is_not_applicable(self):
        self.assertEqual(L.diagnose_charging(_CHG("PD", False, 5000, 1500), (), {}).status, L.NOT_APPLICABLE)

    def test_short_from_the_log_wins(self):
        r = L.diagnose_charging(_CHG(), ("USB_CC_VBUS_SHORT",), {})
        self.assertEqual(r.manual_step.step_id, "chg_clean")
        self.assertEqual(L.diagnose_charging(_CHG(), ("USB_CC_VBUS_SHORT",), {"chg_clean": L.FIXED}).confidence, "high")
        self.assertEqual(L.diagnose_charging(_CHG(), ("USB_CC_VBUS_SHORT",), {"chg_clean": L.SAME}).confidence, "medium")

    def test_dead_port_cable_then_port_then_ic(self):
        chg = _CHG(charger_type="SDP", is_ac_plugged=True)   # plugged, no voltage/current measured
        self.assertEqual(L.diagnose_charging(chg, (), {}).manual_step.step_id, "chg_cable")
        self.assertEqual(L.diagnose_charging(chg, (), {"chg_cable": L.FIXED}).confidence, "high")
        self.assertEqual(L.diagnose_charging(chg, (), {"chg_cable": L.SAME}).manual_step.step_id, "chg_port")
        self.assertEqual(L.diagnose_charging(chg, (), {"chg_cable": L.SAME, "chg_port": L.SAME}).confidence, "medium")


class TouchTree(unittest.TestCase):
    def test_controller_dead(self):
        self.assertEqual(L.diagnose_touch(False, [], {}).status, L.NOT_APPLICABLE)
        self.assertEqual(L.diagnose_touch(True, [], {}).manual_step.step_id, "touch_screen")
        self.assertEqual(L.diagnose_touch(True, [], {"touch_screen": L.FIXED}).confidence, "high")
        self.assertEqual(L.diagnose_touch(True, [], {"touch_screen": L.SAME}).confidence, "medium")


class Dispatcher(unittest.TestCase):
    def test_run_layered_drops_not_applicable_and_keeps_faults(self):
        res = L.run_layered(fingerprint=_FP("OK", True), battery=_BAT(temp_c=80.0, health_verdict=str(Status.FAIL)),
                            charging=_CHG("PD", True, 5000, 1000), touch_not_responding=True, answers={})
        areas = {r.area for r in res}
        self.assertEqual(areas, {"touch", "battery_temp", "battery_health"})
        self.assertTrue(all(r.status != L.NOT_APPLICABLE for r in res))

    def test_every_message_renders_in_all_languages(self):
        fp = _FP("HAL_CRASH_LOOP", False)
        bat = _BAT(temp_c=80.0, health_verdict=str(Status.FAIL), true_health_pct=55.0)
        chg = _CHG("SDP", True)
        combos = [{}, {"fp_flex": L.SAME}, {"fp_flex": L.SAME, "fp_sensor": L.SAME},
                  {"bat_temp_swap": L.SAME}, {"bat_health_swap": L.SAME},
                  {"chg_cable": L.SAME, "chg_port": L.SAME}]
        for lang in ("ar", "en", "fr"):
            set_current_language(lang)
            for answers in combos:
                for r in L.run_layered(fingerprint=fp, battery=bat, charging=chg, touch_not_responding=True, answers=answers):
                    render(r.diagnosis, lang)
                    for step in r.trail:
                        self.assertTrue(render(step, lang))
                    if r.manual_step:
                        for _, label in r.manual_step.options:
                            self.assertTrue(render(label, lang))
        set_current_language("ar")


class GuiWalk(unittest.TestCase):
    """The board tab's _layered_run walks manual steps through an injected ask(), so it is testable without Tk."""

    def test_scripted_walk_reaches_a_conclusion(self):
        from ui.tabs.board_diag import BoardDiagTabMixin
        sources = {"fingerprint": _FP("HAL_CRASH_LOOP", False), "battery": _BAT(), "charging": _CHG("PD", True, 5000, 1000)}
        answers_script = {"fp_flex": L.FIXED}
        asked = []

        def ask(step):
            asked.append(step.step_id)
            return answers_script.get(step.step_id)

        results = BoardDiagTabMixin._layered_run(object.__new__(BoardDiagTabMixin), sources, [], ask)
        self.assertEqual(asked, ["fp_flex"])
        fp = next(r for r in results if r.area == "fingerprint")
        self.assertEqual((fp.status, fp.confidence), (L.CONCLUDED, "high"))

    def test_walk_stops_when_the_dialog_is_cancelled(self):
        from ui.tabs.board_diag import BoardDiagTabMixin
        sources = {"fingerprint": _FP("HAL_CRASH_LOOP", False), "battery": _BAT(), "charging": _CHG()}
        results = BoardDiagTabMixin._layered_run(object.__new__(BoardDiagTabMixin), sources, [], lambda step: None)
        fp = next(r for r in results if r.area == "fingerprint")
        self.assertEqual(fp.status, L.NEEDS_MANUAL)   # left pending, not guessed


class ReportSection(unittest.TestCase):
    def test_report_shows_conclusion_and_manual_step(self):
        import tempfile
        from pathlib import Path
        from ui.report_exporter import DiagnosticReportExporter
        from tests.test_report_sections import BOARD_PASS
        layered = [L.diagnose_fingerprint(_FP("HAL_CRASH_LOOP", False), {"fp_flex": L.FIXED}).to_dict(),
                   L.diagnose_charging(_CHG(), ("USB_CC_VBUS_SHORT",), {}).to_dict()]
        with tempfile.TemporaryDirectory() as d:
            path = DiagnosticReportExporter.generate_html_report(device_profile={"serial": "S"}, board_diag=BOARD_PASS,
                                                                 output_dir=d, lang="ar", layered=layered)
            html = Path(path).read_text(encoding="utf-8")
        self.assertIn("التشخيص على طبقات", html)
        self.assertIn("فلاتة البصمة", html)        # the concluded component
        self.assertIn("نظّف مدخل الشحن", html)       # the pending manual step


if __name__ == "__main__":
    unittest.main()
