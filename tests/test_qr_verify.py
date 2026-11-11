"""Signed QR summary on reports: format, verification (Python and the offline page), CLI."""
import io
import os
import pathlib
import re
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch
from urllib.parse import quote

import main as app_main
from core import report_signing as rs
from ui import pdf_export
from ui import report_exporter
from ui.report_exporter import DiagnosticReportExporter
from ui.verifier_page import PAGE_NAME, _STRINGS, build_verifier_html, export_verifier

FIELDS = {"ref": "ADP-1-AB", "serial": "R58M1234", "model": "Samsung SM-A525F; نسخة",
          "status": "PASS", "date": "2026-11-11 12:00:00"}


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        for k in (rs.PASSPHRASE_ENV, rs.VERIFY_URL_ENV, rs.KEY_PATH_ENV):
            os.environ.pop(k, None)
        self.priv, self.pub, self.kid = rs.generate_keypair(os.path.join(self.tmp.name, "keys"))
        self.key = rs.load_private_key(self.priv)

    def tearDown(self):
        self.tmp.cleanup()


class SummaryTests(_Case):
    def test_roundtrip_keeps_every_field(self):
        text = rs.make_signed_summary(self.key, FIELDS)
        self.assertTrue(text.startswith("ADP1;"))
        self.assertEqual(text.count(";"), 7)  # the ';' inside the model is encoded
        ok, msg, fields = rs.verify_signed_summary(text, self.pub)
        self.assertTrue(ok, msg)
        self.assertEqual(msg.key, "qr.verify.ok")
        self.assertEqual(fields, FIELDS)

    def test_any_changed_field_fails(self):
        text = rs.make_signed_summary(self.key, FIELDS)
        for old, new in (("PASS", "FAIL"), ("R58M1234", "R58M1235"), ("2026-11-11", "2026-11-12")):
            with self.subTest(change=new):
                ok, msg, _ = rs.verify_signed_summary(text.replace(old, new, 1), self.pub)
                self.assertFalse(ok)
                self.assertEqual(msg.key, "qr.verify.bad_signature")

    def test_other_key_and_garbage(self):
        other_priv, _, _ = rs.generate_keypair(os.path.join(self.tmp.name, "other"))
        text = rs.make_signed_summary(rs.load_private_key(other_priv), FIELDS)
        self.assertEqual(rs.verify_signed_summary(text, self.pub)[1].key, "sign.verify.other_key")
        for junk in ("", "hello", "ADP1;a;b", '{"ref": "x"}', "ADP1;" + ";" * 6 + "!!!"):
            with self.subTest(junk=junk):
                ok, msg, _ = rs.verify_signed_summary(junk, self.pub)
                self.assertFalse(ok)
                self.assertIn(msg.key, ("qr.verify.bad_format", "qr.verify.bad_signature", "sign.verify.other_key"))

    def test_link_and_encoded_forms(self):
        text = rs.make_signed_summary(self.key, FIELDS)
        for form in ("https://shop.example/verify.html#" + text, quote(text, safe=""),
                     "https://shop.example/verify.html#" + quote(text, safe="")):
            with self.subTest(form=form[:40]):
                self.assertTrue(rs.verify_signed_summary(form, self.pub)[0])


class ReportQrTests(_Case):
    def qr_text(self, **kw):
        seen = []
        real = report_exporter._qr_svg

        def capture(data, size_px=90):
            seen.append(data)
            return real(data, size_px)
        with patch.object(report_exporter, "_qr_svg", side_effect=capture):
            DiagnosticReportExporter.generate_html_report(
                device_profile={"serial": "R58M1234", "brand": "samsung", "model": "SM-A525F"},
                output_dir=self.tmp.name, lang="en", **kw)
        return seen[0]

    def test_signed_report_carries_verifiable_summary(self):
        text = self.qr_text(signing_key_path=self.priv)
        ok, _, fields = rs.verify_signed_summary(text, self.pub)
        self.assertTrue(ok)
        self.assertEqual((fields["serial"], fields["model"], fields["status"]), ("R58M1234", "samsung SM-A525F", "UNKNOWN"))

    def test_verify_url_makes_a_link_only_for_https(self):
        with patch.dict(os.environ, {rs.VERIFY_URL_ENV: "https://shop.example/v.html"}):
            text = self.qr_text(signing_key_path=self.priv)
        self.assertTrue(text.startswith("https://shop.example/v.html#ADP1;"))
        self.assertTrue(rs.verify_signed_summary(text, self.pub)[0])
        with patch.dict(os.environ, {rs.VERIFY_URL_ENV: "http://insecure.example/v.html"}):
            self.assertTrue(self.qr_text(signing_key_path=self.priv).startswith("ADP1;"))

    def test_unsigned_report_keeps_plain_qr(self):
        data = self.qr_text()
        self.assertIsInstance(data, dict)
        self.assertEqual(data["sn"], "R58M1234")


class VerifierPageTests(_Case):
    def test_page_contents(self):
        for lang, direction in (("ar", "rtl"), ("en", "ltr")):
            page = build_verifier_html(self.pub, lang)
            self.assertIn(f'dir="{direction}"', page)
            self.assertIn(self.kid, page)
            self.assertIn(rs.public_key_raw_b64(self.pub), page)
            self.assertNotIn("__", re.sub(r"__proto__", "", page.split("<script>")[0]))  # every token replaced
            self.assertEqual(page.lower().count("</script"), 1)
            self.assertNotIn("http://", page.replace('http://www.w3.org', ''))  # no outside requests
            self.assertNotIn("https://", page)

    def test_export_writes_file(self):
        path, kid = export_verifier(os.path.join(self.tmp.name, "site"), self.pub, "en")
        self.assertEqual(os.path.basename(path), PAGE_NAME)
        self.assertEqual(kid, self.kid)


@unittest.skipUnless(pdf_export.find_browser(), "needs Microsoft Edge or Google Chrome")
class VerifierPageInBrowserTests(_Case):
    def result(self, page, fragment):
        url = pathlib.Path(page).resolve().as_uri() + "#" + fragment
        with tempfile.TemporaryDirectory() as prof:
            out = subprocess.run([pdf_export.find_browser(), "--headless=new", "--disable-gpu", f"--user-data-dir={prof}",
                                  "--virtual-time-budget=5000", "--dump-dom", url],
                                 capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
                                 creationflags=pdf_export.CREATE_NO_WINDOW).stdout
        m = re.search(r'data-result="([a-z_]+)"', out)
        return m.group(1) if m else None

    def test_browser_checks_signature_itself(self):
        page, _ = export_verifier(os.path.join(self.tmp.name, "site"), self.pub, "en")
        good = rs.make_signed_summary(self.key, FIELDS)
        self.assertEqual(self.result(page, good), "ok")
        self.assertEqual(self.result(page, good.replace("PASS", "FAIL", 1)), "bad_signature")


class CliQrTests(_Case):
    def cmd(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = app_main.run_signing_command(app_main.build_parser().parse_args(["--lang", "en", *argv]))
        return code, out.getvalue()

    def test_verify_qr_exit_codes(self):
        good = rs.make_signed_summary(self.key, FIELDS)
        code, out = self.cmd("--verify-qr", good, "--public-key", self.pub)
        self.assertEqual(code, app_main.EXIT_OK)
        self.assertIn("R58M1234", out)
        self.assertEqual(self.cmd("--verify-qr", good.replace("PASS", "FAIL", 1), "--public-key", self.pub)[0],
                         app_main.EXIT_BAD_SIGNATURE)
        self.assertEqual(self.cmd("--verify-qr", good)[0], app_main.EXIT_INTERNAL)  # no public key

    def test_export_verifier(self):
        site = os.path.join(self.tmp.name, "site")
        code, out = self.cmd("--export-verifier", site, "--public-key", self.pub)
        self.assertEqual(code, app_main.EXIT_OK)
        self.assertTrue(os.path.isfile(os.path.join(site, PAGE_NAME)))
        self.assertIn(self.kid, out)

    def test_all_verifier_strings_exist(self):
        from ui.i18n import STRINGS
        for lang in ("ar", "en"):
            for k in _STRINGS:
                self.assertTrue(STRINGS[lang].get(f"verifier.{k}"), (lang, k))


if __name__ == "__main__":
    unittest.main()
