"""The HTML report's screen-originality and log-analysis sections. Neither counts in the phone's overall
result: the screen check is indicators, never proof, and the analysed log file may come from another phone."""
import gc
from html import escape
import io
import os
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import main as app_main
from core import log_analyzer
from core.display_check import DisplayOriginalityCheck
from database.db_manager import DatabaseManager
from tests.fakes import FakeBridge
from tests.test_display_check import S23U
from ui.i18n import t
from ui.report_exporter import DiagnosticReportExporter

UART = ("[PRELOADER] boot\n[ 3.2] ufshcd-qcom 1d84000.ufshc: ufs_init fail, link startup error -110\n"
        "[ 4.5] Kernel panic - not syncing: VFS: Unable to mount root fs\n")
BOARD_PASS = {"checks": [{"check_id": "battery.fuel_gauge", "status": "PASS"}]}


def report(lang="en", **kw):
    with tempfile.TemporaryDirectory() as d:
        path = DiagnosticReportExporter.generate_html_report(device_profile={"serial": "S"}, board_diag=BOARD_PASS,
                                                             output_dir=d, lang=lang, **kw)
        return Path(path).read_text(encoding="utf-8")


def overall(html, lang="en"):
    return re.search(re.escape(t("report.overall", lang)) + r': <span class="badge"[^>]*>([A-Z_]+)<', html).group(1)


class TestDisplaySection(unittest.TestCase):
    def setUp(self):
        self.result = DisplayOriginalityCheck(FakeBridge(dict(S23U))).run("s").to_dict()

    def test_section_lists_indicators_and_the_limit(self):
        html = report(display_check=self.result)
        self.assertIn(t("display.originality_indicators", "en"), html)
        self.assertIn("SDC_812596", html)
        self.assertIn(t("display.effect.supports", "en"), html)
        self.assertIn("cannot prove", html)
        self.assertIn("Consistent with an original screen", html)

    def test_not_counted_in_the_overall_result(self):
        self.assertEqual(overall(report()), "PASS")
        self.assertEqual(overall(report(display_check=self.result)), "PASS")

    def test_arabic(self):
        html = report(lang="ar", display_check=self.result)
        self.assertIn(t("display.originality_indicators", "ar"), html)
        self.assertIn(t("display.not_proof", "ar")[:20], html)
        # dates and numbers inside Arabic sentences are isolated left-to-right (2022-02-08 showed as 08-02-2022)
        self.assertIn("⁦2022-02-08⁩", html)
        self.assertIn("⁦1440x3088⁩", html)


class TestLogSection(unittest.TestCase):
    def setUp(self):
        self.result = log_analyzer.analyze_text(UART, source="dead_phone_uart.log").to_dict()

    def test_section_shows_file_fatal_line_and_matches(self):
        html = report(log_analysis=self.result)
        self.assertIn("dead_phone_uart.log", html)
        self.assertIn("Kernel panic - not syncing", html)          # isolated fatal line
        self.assertIn("UFS_LINK_STARTUP_FAIL", html)
        self.assertIn(escape(t("report.log_not_counted", "en")), html)       # HTML-escaped like every text

    def test_a_failing_log_does_not_fail_the_phone(self):
        self.assertEqual(self.result["status"], "FAIL")
        self.assertEqual(overall(report(log_analysis=self.result)), "PASS")

    def test_arabic_descriptions(self):
        html = report(lang="ar", log_analysis=self.result)
        self.assertIn("لم يعمل اتصال ذاكرة التخزين UFS", html)       # the rule's Arabic description
        self.assertIn(escape(t("board_area.storage", "ar")), html)  # suspected board area, in Arabic
        self.assertIn(escape(self.result["possible_causes_ar"][0]), html)   # parts named in Arabic
        self.assertNotIn(escape(self.result["possible_causes"][0]), html)

    def test_board_areas_most_likely_first(self):
        html = report(log_analysis=self.result)
        self.assertEqual(self.result["board_areas"][0], "firmware")        # the panic line is the fatal one
        line = t("cli.logscan_areas", "en", areas=", ".join(t(f"board_area.{a}", "en") for a in self.result["board_areas"]))
        self.assertIn(escape(line), html)                                   # escaped once, in this order


class TestGuiReportPassesTheResults(unittest.TestCase):
    def test_certificate_includes_last_display_check_and_log_analysis(self):
        from ui import main_window
        with tempfile.TemporaryDirectory() as d:
            db_path = os.path.join(d, "gui.db")
            with patch.object(main_window, "DatabaseManager", side_effect=lambda *a, **k: DatabaseManager(db_path=db_path)), \
                    patch.object(main_window.AndroidDoctorApp, "_start_device_monitor", return_value=None), \
                    patch.object(main_window.AndroidDoctorApp, "refresh_devices_async", return_value=None), \
                    patch.object(main_window.AndroidDoctorApp, "_auto_poll_devices", return_value=None), \
                    patch.object(main_window, "verify_binaries", return_value={"ok": True, "packages": {}}):
                app = main_window.AndroidDoctorApp(lang="en")
                app.withdraw()
                try:
                    app._ensure_tab("reports")
                    app.current_serial.set("S")
                    stub = SimpleNamespace(to_dict=lambda: {})
                    app.current_profile = app.last_board_diag = app.last_hw_audit = app.last_sec_scan = stub
                    app.last_display_check = DisplayOriginalityCheck(FakeBridge(dict(S23U))).run("s")
                    app.last_log_analysis = log_analyzer.analyze_text(UART, source="dead_phone_uart.log")
                    run_now = SimpleNamespace(Thread=lambda target=None, daemon=None, **kw: SimpleNamespace(start=target))
                    with patch.object(main_window.DiagnosticReportExporter, "generate_html_report",
                                      return_value=os.path.join(d, "r.html")) as gen, \
                            patch.object(main_window.webbrowser, "open"), \
                            patch.object(app, "_refresh_past_reports"), \
                            patch("ui.tabs.reports.threading", run_now):
                        app._generate_and_open_certificate()
                        app.update()
                    kw = gen.call_args.kwargs
                    self.assertEqual(kw["display_check"]["assessment"], "consistent")
                    self.assertEqual(kw["log_analysis"]["source"], "dead_phone_uart.log")
                finally:
                    app.destroy()
                    gc.collect()
                    DatabaseManager.reset_instance()


class TestCliFullScanIncludesTheScreenCheck(unittest.TestCase):
    def test_cli_report_has_the_display_section(self):
        with tempfile.TemporaryDirectory() as d:
            b = FakeBridge(dict(S23U))
            b.devices = [{"serial": "R5CW523A5EW", "state": "device", "model": "SM_S918B"}]
            db = DatabaseManager(os.path.join(d, "t.db"))
            out = io.StringIO()
            try:
                with patch("core.adb_bridge.ADBBridge", lambda: b), \
                        patch("database.db_manager.DatabaseManager", lambda *a, **k: db), redirect_stdout(out):
                    code = app_main.run_cli_diagnostics("", "en", output_dir=d)
                html = next(Path(d, f).read_text(encoding="utf-8") for f in os.listdir(d) if f.endswith(".html"))
            finally:
                db.close()
                DatabaseManager.reset_instance()
        self.assertEqual(code, app_main.EXIT_OK)
        self.assertIn(t("display.originality_indicators", "en"), html)
        self.assertIn("SDC_812596", html)
        self.assertIn("Consistent with an original screen", out.getvalue())


if __name__ == "__main__":
    unittest.main()
