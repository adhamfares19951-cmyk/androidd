"""IMEI format/Luhn validation and BYOK blacklist lookup. A 'clean' verdict is never
invented: only a real service response that clearly says so yields PASS."""
import unittest

from core.result import Status
from core.imei_check import (luhn_ok, validate_imei, check_blacklist, BlacklistConfig,
                             _interpret_blacklist, normalize_imei)


# 490154203237518 is the classic GSMA valid-IMEI example (passes Luhn).
VALID = "490154203237518"


class ValidationTests(unittest.TestCase):
    def test_luhn_accepts_valid_imei(self):
        self.assertTrue(luhn_ok(VALID))

    def test_luhn_rejects_mistyped_imei(self):
        self.assertFalse(luhn_ok("490154203237519"))   # last digit off by one

    def test_wrong_length_is_fail(self):
        r = validate_imei("12345")
        self.assertEqual(r.status, Status.FAIL)
        self.assertFalse(r.valid_format)

    def test_spaces_and_dashes_are_stripped(self):
        self.assertEqual(normalize_imei("49-01 5420 3237518"), VALID)
        self.assertTrue(validate_imei("49-01 5420 3237518").valid_format)

    def test_valid_format_is_pass_but_blacklist_unknown(self):
        r = validate_imei(VALID)
        self.assertEqual(r.status, Status.PASS)
        self.assertEqual(r.blacklist, "unknown")


class InterpretTests(unittest.TestCase):
    def test_clean_and_blacklisted_and_ambiguous(self):
        self.assertEqual(_interpret_blacklist("GSMA Blacklist Status: Clean"), "clean")
        self.assertEqual(_interpret_blacklist("Result: Blacklisted (reported stolen)"), "blacklisted")
        self.assertEqual(_interpret_blacklist("Not Blacklisted"), "clean")
        self.assertEqual(_interpret_blacklist("some unrelated text"), "unknown")


class BlacklistLookupTests(unittest.TestCase):
    def _cfg(self):
        return BlacklistConfig(endpoint="https://api.example/imei", api_key="K", service_id="12")

    def test_no_service_is_unknown_not_clean(self):
        r = check_blacklist(VALID, config=None)
        self.assertEqual(r.status, Status.UNKNOWN)
        self.assertEqual(r.blacklist, "unknown")

    def test_malformed_imei_skips_paid_lookup(self):
        calls = []
        check_blacklist("123", config=self._cfg(), transport=lambda u, p: calls.append(u) or "clean")
        self.assertEqual(calls, [])      # never spent a lookup on an invalid IMEI

    def test_clean_response_passes(self):
        r = check_blacklist(VALID, config=self._cfg(),
                            transport=lambda u, p: "Blacklist Status: Clean")
        self.assertEqual(r.status, Status.PASS)
        self.assertEqual(r.blacklist, "clean")
        self.assertTrue(r.evidence)      # raw response kept as evidence

    def test_blacklisted_response_fails(self):
        r = check_blacklist(VALID, config=self._cfg(),
                            transport=lambda u, p: "Status: Blacklisted - reported lost")
        self.assertEqual(r.status, Status.FAIL)
        self.assertEqual(r.blacklist, "blacklisted")

    def test_unclear_response_is_unknown_never_assumed_clean(self):
        r = check_blacklist(VALID, config=self._cfg(),
                            transport=lambda u, p: "{\"balance\": 4.20}")
        self.assertEqual(r.status, Status.UNKNOWN)
        self.assertEqual(r.blacklist, "unknown")

    def test_transport_error_is_unknown(self):
        def boom(u, p):
            raise OSError("timeout")
        r = check_blacklist(VALID, config=self._cfg(), transport=boom)
        self.assertEqual(r.status, Status.UNKNOWN)
        self.assertIn("timeout", r.evidence[0].raw_excerpt)


if __name__ == "__main__":
    unittest.main()
