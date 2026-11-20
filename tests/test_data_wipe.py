"""Data Erasure Certificate: encryption is only ever claimed when the device confirms it,
post-wipe state is read not assumed, and the certificate verifies against its signing key."""
import os
import tempfile
import unittest

from core.result import Status
from core import data_wipe
from core.data_wipe import (assess_wipe_readiness, build_certificate, write_certificate,
                            verify_certificate, verify_post_wipe, initiate_factory_reset,
                            VERDICT_CRYPTO, VERDICT_UNVERIFIED, CRYPTO_FBE, CRYPTO_NONE)


class _Phone:
    """Minimal bridge: canned getprop values and one settings/shell route."""
    def __init__(self, props=None, setup_complete="1", rooted=False):
        self.props = props or {}
        self.setup_complete = setup_complete
        self.rooted = rooted
        self.shell_calls = []
    def get_prop(self, name, serial=None):
        return self.props.get(name, "")
    def is_root_available(self, serial=None):
        return self.rooted
    def shell(self, cmd, serial=None, timeout=10, root=False, use_socket=True):
        self.shell_calls.append(cmd)
        if "user_setup_complete" in cmd:
            return (0, self.setup_complete, "")
        if "MASTER_CLEAR" in cmd:
            return (0, "", "")
        return (0, "", "")


ENCRYPTED = {"ro.crypto.state": "encrypted", "ro.crypto.type": "file",
             "ro.product.model": "M2003J15SC", "ro.product.brand": "Xiaomi",
             "ro.build.version.release": "11", "ro.serialno": "abc123"}


class WipeAssessmentTests(unittest.TestCase):
    def test_encrypted_device_is_pass_and_fbe(self):
        a = assess_wipe_readiness(_Phone(ENCRYPTED), "abc123")
        self.assertEqual(a.status, Status.PASS)
        self.assertIs(a.encrypted, True)
        self.assertEqual(a.crypto_type, CRYPTO_FBE)

    def test_unencrypted_device_is_fail(self):
        a = assess_wipe_readiness(_Phone({"ro.crypto.state": "unencrypted"}), "s")
        self.assertEqual(a.status, Status.FAIL)
        self.assertIs(a.encrypted, False)
        self.assertEqual(a.crypto_type, CRYPTO_NONE)

    def test_silent_device_is_unknown_never_assumed_safe(self):
        a = assess_wipe_readiness(_Phone({}), "s")      # no crypto props at all
        self.assertEqual(a.status, Status.UNKNOWN)
        self.assertIsNone(a.encrypted)


class CertificateVerdictTests(unittest.TestCase):
    def test_crypto_erasure_only_when_encryption_confirmed(self):
        enc = build_certificate(assess_wipe_readiness(_Phone(ENCRYPTED), "abc123"))
        self.assertEqual(enc.verdict, VERDICT_CRYPTO)

        plain = build_certificate(assess_wipe_readiness(_Phone({"ro.crypto.state": "unencrypted"}), "s"))
        self.assertEqual(plain.verdict, VERDICT_UNVERIFIED)

        unknown = build_certificate(assess_wipe_readiness(_Phone({}), "s"))
        self.assertEqual(unknown.verdict, VERDICT_UNVERIFIED)


class PostWipeTests(unittest.TestCase):
    def test_fresh_device_confirms_wipe(self):
        self.assertTrue(verify_post_wipe(_Phone(setup_complete="0"), "s"))

    def test_set_up_device_does_not_confirm_wipe(self):
        self.assertFalse(verify_post_wipe(_Phone(setup_complete="1"), "s"))


class ResetTriggerTests(unittest.TestCase):
    def test_nothing_happens_without_confirmation(self):
        ph = _Phone(ENCRYPTED)
        r = initiate_factory_reset(ph, "s", confirm=False)
        self.assertFalse(r["started"])
        self.assertEqual(ph.shell_calls, [])        # no command issued at all

    def test_non_root_opens_on_device_confirm(self):
        ph = _Phone(ENCRYPTED, rooted=False)
        r = initiate_factory_reset(ph, "s", confirm=True)
        self.assertFalse(r["started"])
        self.assertTrue(r["needs_on_device_confirm"])

    def test_root_sends_master_clear(self):
        ph = _Phone(ENCRYPTED, rooted=True)
        r = initiate_factory_reset(ph, "s", confirm=True)
        self.assertTrue(r["started"])
        self.assertTrue(any("MASTER_CLEAR" in c for c in ph.shell_calls))


class SignAndVerifyTests(unittest.TestCase):
    def test_signed_certificate_verifies_and_tamper_is_caught(self):
        from core.report_signing import generate_keypair, load_private_key
        with tempfile.TemporaryDirectory() as d:
            proj = os.path.join(d, "proj"); os.makedirs(proj)
            priv, pub, _ = generate_keypair(os.path.join(d, "keys"), project_root=proj)  # keys OUTSIDE project
            key = load_private_key(priv, project_root=proj)
            cert = build_certificate(assess_wipe_readiness(_Phone(ENCRYPTED), "abc123"),
                                     post_wipe_verified=True, shop="Fix-It")
            out = write_certificate(cert, os.path.join(d, "certs"), signing_key=key)
            self.assertTrue(out["signed"])
            self.assertTrue(os.path.isfile(out["json_path"]))
            ok, _ = verify_certificate(out["json_path"], pub)
            self.assertTrue(ok)
            # tamper with the issued certificate -> verification must fail
            with open(out["json_path"], "a", encoding="utf-8") as f:
                f.write(" ")
            ok2, _ = verify_certificate(out["json_path"], pub)
            self.assertFalse(ok2)

    def test_unsigned_certificate_is_written_but_marked_unsigned(self):
        with tempfile.TemporaryDirectory() as d:
            cert = build_certificate(assess_wipe_readiness(_Phone(ENCRYPTED), "abc123"))
            out = write_certificate(cert, d, signing_key=None)
            self.assertFalse(out["signed"])
            self.assertIsNone(out["qr_text"])
            self.assertTrue(os.path.isfile(out["json_path"]))


if __name__ == "__main__":
    unittest.main()
