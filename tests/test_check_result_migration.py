"""Every judged check surfaces as a CheckResult; CLI and report read the same lists."""
import gc
import os
import re
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from core.result import CheckResult, Evidence, Status, M, aggregate_status, coerce_status
from core.board_diag import (PMICStatus, LeakageTestResult, ThermalReport, BlackboxReport, PanicAnalysis,
                             FastChargeDiagnostics, StorageLifeReport, StressTestReport, RootForensicsReport)
from core.hardware_tester import (FingerprintDiagnostics, PhysicalButtonsDiagnostics, SIMBasebandDiagnostics,
                                  WiFiDiagnostics, BluetoothDiagnostics, PeripheralChipsDiagnostics,
                                  DisplayBacklightDiagnostics, AudioCodecReport, DisplayPanelInfo)
from core.storage_benchmark import StorageHealthReport
from core.malware_hunter import SecurityAuditScan
from ui.report_exporter import DiagnosticReportExporter

PER_CHECK_CLASSES = [
    LeakageTestResult, ThermalReport, BlackboxReport, PanicAnalysis, FastChargeDiagnostics,
    StorageLifeReport, StressTestReport, RootForensicsReport, FingerprintDiagnostics,
    PhysicalButtonsDiagnostics, SIMBasebandDiagnostics, WiFiDiagnostics, BluetoothDiagnostics,
    PeripheralChipsDiagnostics, DisplayBacklightDiagnostics, AudioCodecReport, DisplayPanelInfo,
    StorageHealthReport,
]


class ContractTests(unittest.TestCase):
    def test_coerce_status_never_invents_pass(self):
        self.assertEqual(coerce_status("PASS"), Status.PASS)
        self.assertEqual(coerce_status(Status.NEEDS_ROOT), Status.NEEDS_ROOT)
        for junk in ("--", "", None, "Reported", "pass"):
            self.assertEqual(coerce_status(junk), Status.UNKNOWN, junk)
        self.assertEqual(aggregate_status(["PASS", "--"]), Status.UNKNOWN)

    def test_every_check_class_maps_status_evidence_and_id(self):
        ev = Evidence(source="cat /x", raw_excerpt="42", exit_code=0)
        for cls in PER_CHECK_CLASSES:
            with self.subTest(cls=cls.__name__):
                default = cls().to_check_result()
                self.assertEqual(default.status, Status.UNKNOWN)
                self.assertEqual(default.check_id, cls().check_id)
                self.assertEqual(default.evidence, [])

                obj = cls()
                obj.status = "FAIL"
                obj.evidence = [ev]
                cr = obj.to_check_result()
                self.assertIsInstance(cr, CheckResult)
                self.assertEqual(cr.status, Status.FAIL)
                self.assertEqual(cr.evidence, [ev])
                cr.to_dict()  # serialisable

    def test_detail_and_causes_are_carried(self):
        leak = LeakageTestResult(status="FAIL", avg_ma=312.5, diagnosis=M("leak.high"),
                                 possible_causes=[M("cause.a")])
        cr = leak.to_check_result()
        self.assertEqual((cr.value, cr.unit), (312.5, "mA"))
        self.assertEqual(cr.detail, M("leak.high"))
        d = cr.to_dict()
        self.assertEqual(d["detail"], {"key": "leak.high", "args": {}})
        self.assertEqual(d["possible_causes"], [{"key": "cause.a", "args": {}}])

    def test_pmic_uses_check_status_not_charging_text(self):
        pmic = PMICStatus(status="Charging", voltage_status="PASS", voltage_mv=4012.0)
        cr = pmic.to_check_result()
        self.assertEqual(cr.status, Status.PASS)
        self.assertEqual((cr.value, cr.unit), (4012.0, "mV"))
        pmic.fault_flags = [M("pmic.flag")]
        self.assertEqual(pmic.to_check_result().status, Status.FAIL)

    def test_panic_detail_is_summary_verdict(self):
        p = PanicAnalysis(status="NEEDS_ROOT", summary_verdict=M("kernellog.needs_root"))
        self.assertEqual(p.to_check_result().detail, M("kernellog.needs_root"))

    def test_security_scan_check_result(self):
        ev = Evidence(source="ls cacerts-added", raw_excerpt="Permission denied", exit_code=1)
        scan = SecurityAuditScan(serial="S", user_ca_status="NEEDS_ROOT", user_ca_evidence=[ev])
        [cr] = scan.check_results()
        self.assertEqual(cr.check_id, "security.user_ca_certificates")
        self.assertEqual(cr.status, Status.NEEDS_ROOT)
        self.assertIsNone(cr.value)  # no count without a real read
        self.assertEqual(cr.evidence, [ev])
        self.assertEqual(scan.to_dict()["checks"][0]["status"], "NEEDS_ROOT")


class AggregateMirrorsChecksTests(unittest.TestCase):
    def test_board_and_hardware_check_statuses_mirror_checks(self):
        from tests.fakes import FakeBridge
        from core.board_diag import MotherboardDiagnostics
        from core.hardware_tester import HardwareTester
        bridge = FakeBridge()
        board = MotherboardDiagnostics(bridge).run_full_diagnostics("SERIAL")
        hw = HardwareTester(bridge).run_full_audit("SERIAL")
        for agg in (board, hw):
            with self.subTest(agg=type(agg).__name__):
                self.assertTrue(agg.checks)
                self.assertEqual(agg.check_statuses, {c.check_id: str(c.status) for c in agg.checks})
                self.assertEqual(agg.to_dict()["checks"], [c.to_dict() for c in agg.checks])
        self.assertEqual([c.check_id for c in board.checks],
                         ["battery.fuel_gauge", "thermal.zones", "board.boot_reason", "board.kernel_log"])
        self.assertNotIn("buttons.keys", hw.check_statuses)  # not run without capture_buttons


def _overall(html_text: str) -> str:
    import html as _html
    from ui.i18n import t
    label = re.escape(_html.escape(t("report.overall", "en")))
    m = re.search(label + r': <span class="badge"[^>]*>([A-Z_]+)</span>', html_text)
    assert m, "overall badge not found"
    return m.group(1)


class ReportTests(unittest.TestCase):
    def _report(self, **kw):
        with tempfile.TemporaryDirectory() as d:
            path = DiagnosticReportExporter.generate_html_report(device_profile={"serial": "S"}, output_dir=d,
                                                                 lang="en", **kw)
            with open(path, encoding="utf-8") as f:
                return f.read()

    def _board(self, status):
        return {"checks": [CheckResult(check_id="battery.fuel_gauge", status=status).to_dict(),
                           CheckResult(check_id="thermal.zones", status=status).to_dict()]}

    def test_failed_stage_makes_report_overall_unknown(self):
        # Before: the report ignored failed CLI stages and said PASS while the CLI said UNKNOWN.
        self.assertEqual(_overall(self._report(board_diag=self._board(Status.PASS))), "PASS")
        stage = CheckResult(check_id="cli.stage.hardware", status=Status.UNKNOWN,
                            detail=M("cli.stage_failed_short")).to_dict()
        html_text = self._report(board_diag=self._board(Status.PASS), extra_checks=[stage])
        self.assertEqual(_overall(html_text), "UNKNOWN")

    def test_no_checks_is_unknown(self):
        self.assertEqual(_overall(self._report()), "UNKNOWN")

    def test_legacy_dict_without_checks_still_rendered(self):
        html_text = self._report(board_diag={"check_statuses": {"battery.fuel_gauge": "FAIL"}})
        self.assertEqual(_overall(html_text), "FAIL")


class _InlineThread:
    """threading.Thread stand-in that runs its target on start() (keeps the GUI test deterministic)."""

    def __init__(self, target=None, daemon=None, **kw):
        self._target = target

    def start(self):
        self._target()


class GuiReportIncludesLeakageTests(unittest.TestCase):
    def test_gui_certificate_passes_screen_off_current(self):
        from ui import main_window
        from database.db_manager import DatabaseManager
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
                    app.last_leakage = LeakageTestResult(status="FAIL", avg_ma=300.0)
                    with patch.object(main_window.DiagnosticReportExporter, "generate_html_report",
                                      return_value=os.path.join(d, "r.html")) as gen, \
                         patch.object(main_window.webbrowser, "open"), \
                         patch.object(app, "_refresh_past_reports"), \
                         patch("ui.tabs.reports.threading.Thread", _InlineThread):
                        app._generate_and_open_certificate()   # the worker runs inline
                        app.update()                           # run the after(0) UI callback
                    leak = gen.call_args.kwargs["leakage_result"]
                    self.assertEqual(leak["status"], "FAIL")
                    self.assertEqual(leak["avg_ma"], 300.0)
                finally:
                    app.destroy()
                    # free Tk fonts/images here, on the main thread: if a later worker thread's
                    # garbage collection finalizes them, it waits for a Tk main loop that is not running
                    gc.collect()


if __name__ == "__main__":
    unittest.main()
