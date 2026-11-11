"""PDF copy of a report: printed by the installed Edge/Chrome, verified as a real PDF, optionally signed."""
import io
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main as app_main
from core import report_signing as rs
from database.db_manager import DatabaseManager
from tests.fakes import FakeBridge
from ui import pdf_export as pe
from ui.report_exporter import DiagnosticReportExporter


def _write(path, data):
    with open(path, "wb") as f:
        f.write(data)


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.html = os.path.join(self.tmp.name, "r.html")
        _write(self.html, "<html><body dir='rtl'>تقرير</body></html>".encode("utf-8"))
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(rs.PASSPHRASE_ENV, None)

    def tearDown(self):
        DatabaseManager.reset_instance()
        self.tmp.cleanup()

    def expect(self, key, fn, *a, **kw):
        with self.assertRaises(pe.PdfExportError) as ctx:
            fn(*a, **kw)
        self.assertEqual(ctx.exception.msg.key, key)


class ErrorPathTests(_Case):
    def test_finds_first_installed_browser(self):
        cands = pe.browser_candidates()
        with patch.object(pe.os.path, "isfile", side_effect=lambda p: p == cands[2]):
            self.assertEqual(pe.find_browser(), cands[2])
        with patch.object(pe.os.path, "isfile", return_value=False):
            self.assertIsNone(pe.find_browser())

    def test_no_browser_and_no_report(self):
        with patch.object(pe, "find_browser", return_value=None):
            self.expect("pdf.err.no_browser", pe.html_to_pdf, self.html)
        self.expect("pdf.err.no_report", pe.html_to_pdf, os.path.join(self.tmp.name, "missing.html"), browser="x")

    def test_stale_pdf_is_never_taken_as_result(self):
        stale = pe.default_pdf_path(self.html)
        _write(stale, b"%PDF-1.4 old file")
        done = subprocess.CompletedProcess([], 0, "", "")
        with patch.object(pe.subprocess, "run", return_value=done):  # browser "succeeds" but writes nothing
            self.expect("pdf.err.failed", pe.html_to_pdf, self.html, browser="browser.exe")
        self.assertFalse(os.path.exists(stale))

    def test_non_pdf_output_is_rejected(self):
        def fake_run(cmd, **kw):
            out = next(a.split("=", 1)[1] for a in cmd if a.startswith("--print-to-pdf="))
            _write(out, b"<html>error page</html>")
            return subprocess.CompletedProcess(cmd, 0, "", "renderer crashed")
        with patch.object(pe.subprocess, "run", side_effect=fake_run):
            self.expect("pdf.err.failed", pe.html_to_pdf, self.html, browser="browser.exe")

    def test_timeout(self):
        with patch.object(pe.subprocess, "run", side_effect=subprocess.TimeoutExpired("b", 5)):
            self.expect("pdf.err.timeout", pe.html_to_pdf, self.html, browser="browser.exe", timeout=5)

    def test_command_uses_private_profile_and_file_uri(self):
        seen = {}

        def fake_run(cmd, **kw):
            seen["cmd"] = cmd
            out = next(a.split("=", 1)[1] for a in cmd if a.startswith("--print-to-pdf="))
            _write(out, b"%PDF-1.4\n%%EOF")
            return subprocess.CompletedProcess(cmd, 0, "", "")
        with patch.object(pe.subprocess, "run", side_effect=fake_run):
            pdf = pe.html_to_pdf(self.html, browser="browser.exe")
        cmd = seen["cmd"]
        self.assertEqual(cmd[0], "browser.exe")
        self.assertIn("--headless=new", cmd)
        profile = next(a.split("=", 1)[1] for a in cmd if a.startswith("--user-data-dir="))
        self.assertIn("adp_pdf_", profile)
        self.assertFalse(os.path.exists(profile))  # temporary profile removed afterwards
        self.assertTrue(cmd[-1].startswith("file:///"))
        self.assertEqual(pdf, os.path.abspath(pe.default_pdf_path(self.html)))

    def test_bad_signing_key_fails_before_printing(self):
        with patch.object(pe.subprocess, "run") as run:
            with self.assertRaises(rs.SigningError):
                pe.export_pdf(self.html, os.path.join(self.tmp.name, "missing.pem"))
        run.assert_not_called()


@unittest.skipUnless(pe.find_browser(), "needs Microsoft Edge or Google Chrome")
class RealBrowserTests(_Case):
    def test_real_arabic_report_to_signed_pdf(self):
        html = DiagnosticReportExporter.generate_html_report(
            device_profile={"serial": "PDFTEST", "model": "SM-A525F"}, output_dir=self.tmp.name, lang="ar")
        priv, pub, _ = rs.generate_keypair(os.path.join(self.tmp.name, "keys"))
        pdf, sig = pe.export_pdf(html, priv)
        with open(pdf, "rb") as f:
            data = f.read()
        self.assertTrue(data.startswith(b"%PDF-"))
        self.assertIn(b"/Type /Page", data.replace(b"/Type/Page", b"/Type /Page"))
        ok, msg = rs.verify_file(pdf, pub, sig)
        self.assertTrue(ok, msg)


class CliPdfTests(_Case):
    def run_cli(self, export):
        b = FakeBridge()
        b.devices = [{"serial": "A1", "state": "device"}]
        db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))
        out = io.StringIO()
        with patch("core.adb_bridge.ADBBridge", lambda: b), \
                patch("database.db_manager.DatabaseManager", lambda *a, **k: db), \
                patch.object(pe, "export_pdf", side_effect=export), redirect_stdout(out):
            code = app_main.run_cli_diagnostics("", "en", output_dir=self.tmp.name, pdf=True)
        return code, out.getvalue()

    def test_cli_pdf_written(self):
        code, out = self.run_cli(lambda html, key: (html[:-5] + ".pdf", None))
        self.assertEqual(code, app_main.EXIT_OK)
        self.assertIn(".pdf", out)

    def test_cli_pdf_failure_is_internal_error(self):
        def fail(html, key):
            raise pe.PdfExportError(pe.M("pdf.err.no_browser"))
        code, out = self.run_cli(fail)
        self.assertEqual(code, app_main.EXIT_INTERNAL)
        self.assertIn("Edge", out)


if __name__ == "__main__":
    unittest.main()
