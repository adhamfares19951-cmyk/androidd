"""Ed25519 report signing: key outside the program, detached signature over the exact file bytes."""
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from unittest.mock import patch

import main as app_main
from core import report_signing as rs
from database.db_manager import DatabaseManager
from tests.fakes import FakeBridge
from ui.i18n import t
from ui.report_exporter import DiagnosticReportExporter


def _rb(path):
    with open(path, "rb") as f:
        return f.read()


def _rt(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


def _wb(path, data, mode="wb"):
    with open(path, mode) as f:
        f.write(data)


def _json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _expect_error(test, key, fn, *args, **kw):
    with test.assertRaises(rs.SigningError) as ctx:
        fn(*args, **kw)
    test.assertEqual(ctx.exception.msg.key, key)


class SigningCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.keys = os.path.join(self.tmp.name, "keys")
        self.out = os.path.join(self.tmp.name, "reports")
        os.makedirs(self.out)
        env = patch.dict(os.environ, {}, clear=False)
        env.start()
        self.addCleanup(env.stop)
        os.environ.pop(rs.PASSPHRASE_ENV, None)
        os.environ.pop(rs.KEY_PATH_ENV, None)
        self.priv, self.pub, self.key_id = rs.generate_keypair(self.keys)

    def tearDown(self):
        DatabaseManager.reset_instance()
        self.tmp.cleanup()

    def report(self, signing_key_path=None, lang="en"):
        return DiagnosticReportExporter.generate_html_report(
            device_profile={"serial": "A1"}, output_dir=self.out, lang=lang, signing_key_path=signing_key_path)


class KeyHandlingTests(SigningCase):
    def test_keypair_files_and_key_id(self):
        self.assertTrue(os.path.isfile(self.priv) and os.path.isfile(self.pub))
        self.assertRegex(self.key_id, r"^[0-9a-f]{16}$")
        self.assertEqual(rs.key_id_from_public(rs.load_public_key(self.pub)), self.key_id)

    def test_never_overwrites_an_existing_key(self):
        before = _rb(self.priv)
        _expect_error(self, "sign.err.key_exists", rs.generate_keypair, self.keys)
        self.assertEqual(_rb(self.priv), before)

    def test_key_inside_program_folder_is_refused(self):
        project = os.path.join(self.tmp.name, "project")
        os.makedirs(project)
        _expect_error(self, "sign.err.key_inside_project", rs.generate_keypair,
                      os.path.join(project, "keys"), project_root=project)
        self.assertFalse(os.path.exists(os.path.join(project, "keys")))
        # the real program folder: refused before the file is even opened
        _expect_error(self, "sign.err.key_inside_project", rs.load_private_key,
                      os.path.join(rs.PROJECT_ROOT, "data", "no_such_key.pem"))

    def test_encrypted_key_needs_passphrase(self):
        enc_dir = os.path.join(self.tmp.name, "enc")
        with patch.dict(os.environ, {rs.PASSPHRASE_ENV: "correct horse"}):
            priv, _, _ = rs.generate_keypair(enc_dir)
            rs.load_private_key(priv)
        self.assertIn(b"ENCRYPTED", _rb(priv))
        _expect_error(self, "sign.err.passphrase_needed", rs.load_private_key, priv)
        with patch.dict(os.environ, {rs.PASSPHRASE_ENV: "wrong"}):
            _expect_error(self, "sign.err.passphrase_wrong", rs.load_private_key, priv)

    def test_public_key_is_not_a_signing_key(self):
        _expect_error(self, "sign.err.key_invalid", rs.load_private_key, self.pub)


class ReportSignatureTests(SigningCase):
    def test_unsigned_report_says_so_and_has_no_signature(self):
        path = self.report()
        text = _rt(path)
        self.assertIn(t("report.not_signed", "en").split(".")[0], text)
        self.assertNotIn(self.key_id, text)
        self.assertFalse(os.path.exists(rs.signature_path_for(path)))

    def test_signed_report_verifies_in_both_languages(self):
        for lang in ("en", "ar"):
            with self.subTest(lang=lang):
                path = self.report(self.priv, lang=lang)
                text = _rt(path)
                self.assertIn(self.key_id, text)
                self.assertNotIn(t("report.not_signed", lang).split("،")[0].split(".")[0], text)
                ok, msg = rs.verify_file(path, self.pub)
                self.assertTrue(ok, msg)
                self.assertEqual(msg.key, "sign.verify.ok")
                rec = _json(rs.signature_path_for(path))
                self.assertEqual((rec["algorithm"], rec["key_id"], rec["file"]),
                                 ("Ed25519", self.key_id, os.path.basename(path)))
                os.remove(path)
                os.remove(rs.signature_path_for(path))

    def test_one_changed_byte_is_detected(self):
        path = self.report(self.priv)
        data = bytearray(_rb(path))
        i = data.index(b"A1")
        data[i:i + 2] = b"B2"
        _wb(path, bytes(data))
        ok, msg = rs.verify_file(path, self.pub)
        self.assertFalse(ok)
        self.assertEqual(msg.key, "sign.verify.modified")

    def test_forged_hash_still_fails_on_signature(self):
        path = self.report(self.priv)
        _wb(path, b"<!-- edited -->", "ab")
        sig = rs.signature_path_for(path)
        rec = _json(sig)
        import hashlib
        rec["sha256"] = hashlib.sha256(_rb(path)).hexdigest()
        _wb(sig, json.dumps(rec).encode("utf-8"))
        ok, msg = rs.verify_file(path, self.pub)
        self.assertFalse(ok)
        self.assertEqual(msg.key, "sign.verify.bad_signature")

    def test_other_public_key_is_rejected(self):
        path = self.report(self.priv)
        _, other_pub, _ = rs.generate_keypair(os.path.join(self.tmp.name, "other"))
        ok, msg = rs.verify_file(path, other_pub)
        self.assertFalse(ok)
        self.assertEqual(msg.key, "sign.verify.other_key")

    def test_garbage_signature_file(self):
        path = self.report(self.priv)
        _wb(rs.signature_path_for(path), b"not json")
        ok, msg = rs.verify_file(path, self.pub)
        self.assertFalse(ok)
        self.assertEqual(msg.key, "sign.verify.bad_format")

    def test_bad_key_writes_no_report(self):
        _expect_error(self, "sign.err.key_unreadable", self.report, os.path.join(self.tmp.name, "missing.pem"))
        self.assertEqual(os.listdir(self.out), [])


class CliSigningTests(SigningCase):
    def cmd(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = app_main.run_signing_command(app_main.build_parser().parse_args(["--lang", "en", *argv]))
        return code, out.getvalue()

    def test_gen_and_verify_exit_codes(self):
        code, out = self.cmd("--gen-signing-key", os.path.join(self.tmp.name, "cli_keys"))
        self.assertEqual(code, app_main.EXIT_OK)
        self.assertIn(rs.PUBLIC_KEY_NAME, out)

        path = self.report(self.priv)
        self.assertEqual(self.cmd("--verify-report", path, "--public-key", self.pub)[0], app_main.EXIT_OK)
        _wb(path, b" ", "ab")
        code, out = self.cmd("--verify-report", path, "--public-key", self.pub)
        self.assertEqual(code, app_main.EXIT_BAD_SIGNATURE)
        self.assertIn("INVALID", out)
        self.assertEqual(self.cmd("--verify-report", path)[0], app_main.EXIT_INTERNAL)  # no public key
        self.assertEqual(self.cmd("--verify-report", path, "--public-key", os.path.join(self.tmp.name, "x.pem"))[0],
                         app_main.EXIT_INTERNAL)

    def _diagnostics(self, signing_key):
        b = FakeBridge()
        b.devices = [{"serial": "A1", "state": "device"}]
        db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))
        out = io.StringIO()
        with patch("core.adb_bridge.ADBBridge", lambda: b), \
                patch("database.db_manager.DatabaseManager", lambda *a, **k: db), redirect_stdout(out):
            code = app_main.run_cli_diagnostics("", "en", output_dir=self.out, signing_key=signing_key)
        return code, out.getvalue()

    def test_cli_diagnostics_signs_report(self):
        code, out = self._diagnostics(self.priv)
        self.assertEqual(code, app_main.EXIT_OK)
        [html_name] = [f for f in os.listdir(self.out) if f.endswith(".html")]
        ok, _ = rs.verify_file(os.path.join(self.out, html_name), self.pub)
        self.assertTrue(ok)
        self.assertIn(".sig", out)

    def test_cli_diagnostics_with_bad_key_fails_without_report(self):
        code, out = self._diagnostics(os.path.join(self.tmp.name, "missing.pem"))
        self.assertEqual(code, app_main.EXIT_INTERNAL)
        self.assertEqual([f for f in os.listdir(self.out) if f.endswith(".html")], [])


if __name__ == "__main__":
    unittest.main()
