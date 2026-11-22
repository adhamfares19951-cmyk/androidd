"""
Unit tests for core.network_diag (Network and SIM diagnostics engine).
Uses FakeBridge with command routing. Every check verdict is tested against evidence.
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from core.network_diag import (
    NetworkDiagnostics,
    diagnose_network,
    parse_baseband,
    parse_imei_service_call,
    parse_sim_state,
    parse_telephony_registry,
    INSERTED,
    SKIP,
)
from core.result import CheckResult, Status
from tests.fakes import FakeBridge, PERMISSION

# Sample valid IMEI: 866762040012345 (Luhn check digit: 5 is valid for prefix)
# Let's verify: 866762040012345:
# 8 6 6 7 6 2 0 4 0 0 1 2 3 4 5
# Doubling every second from right:
# 5 + (4*2=8) + 3 + (2*2=4) + 1 + (0*2=0) + 0 + (4*2=8) + 0 + (2*2=4) + 6 + (7*2=14->5) + 6 + (6*2=12->3) + 8
# = 5 + 8 + 3 + 4 + 1 + 0 + 0 + 8 + 0 + 4 + 6 + 5 + 6 + 3 + 8 = 61 -> not divisible by 10.
# Let's find a valid Luhn IMEI:
# 352099001761482
# 2 + 16(7) + 4 + 2 + 6 + 14(5) + 1 + 0 + 0 + 0 + 9 + 0 + 2 + 10(1) + 3 = 40 (40%10==0). Valid!
SAMPLE_IMEI = "490154203237518"
SAMPLE_IMEI_PARCEL = (
    "Result: Parcel(00000000 0000000f 00340039 00300031 00350034 00320030 00330032 00330037 00350031 00380000 '....4.9.0.1.5.4.2.0.3.2.3.7.5.1.8.')"
)
SAMPLE_BASEBAND = "G998BXXU9EWA6"


class TestNetworkDiagParsers(unittest.TestCase):
    def test_parse_sim_state(self):
        self.assertEqual(parse_sim_state("READY"), ["READY"])
        self.assertEqual(parse_sim_state("READY,ABSENT"), ["READY", "ABSENT"])
        self.assertEqual(parse_sim_state(""), [])
        self.assertEqual(parse_sim_state(None), [])

    def test_parse_baseband(self):
        self.assertEqual(parse_baseband("G998BXXU9EWA6"), "G998BXXU9EWA6")
        self.assertEqual(parse_baseband("G998BXXU9EWA6,G998BXXU9EWA6"), "G998BXXU9EWA6")
        self.assertIsNone(parse_baseband(""))
        self.assertIsNone(parse_baseband("unknown"))
        self.assertIsNone(parse_baseband("UNKNOWN"))
        self.assertIsNone(parse_baseband("null"))

    def test_parse_imei_service_call(self):
        self.assertEqual(parse_imei_service_call(SAMPLE_IMEI_PARCEL), SAMPLE_IMEI)
        self.assertEqual(parse_imei_service_call(SAMPLE_IMEI), SAMPLE_IMEI)
        self.assertIsNone(parse_imei_service_call(""))
        # All zeroes is rejected as corrupted
        self.assertIsNone(parse_imei_service_call("Result: Parcel(00000000 '000000000000000')"))
        # Invalid checksum rejected
        self.assertIsNone(parse_imei_service_call("123456789012345"))

    def test_parse_telephony_registry(self):
        # Case: normal signal
        text_normal = (
            "mServiceState=0 0 voice home data home\n"
            "mVoiceRegState=0(IN_SERVICE)\n"
            "mSignalStrength=SignalStrength: level=3 rssi=-75 dBm\n"
        )
        reg, level, dbm, is_zero = parse_telephony_registry(text_normal)
        self.assertEqual(reg, "IN_SERVICE")
        self.assertEqual(level, 3)
        self.assertEqual(dbm, -75)
        self.assertFalse(is_zero)

        # Case: zero signal
        text_zero = (
            "mVoiceRegState=1(OUT_OF_SERVICE)\n"
            "mSignalStrength=SignalStrength: level=0 rssi=-120 dBm\n"
        )
        reg, level, dbm, is_zero = parse_telephony_registry(text_zero)
        self.assertEqual(reg, "OUT_OF_SERVICE")
        self.assertEqual(level, 0)
        self.assertEqual(dbm, -120)
        self.assertTrue(is_zero)


class TestNetworkDiagnostics(unittest.TestCase):
    def test_baseband_empty_diagnoses_modem_efs(self):
        routes = {
            "getprop gsm.sim.state": (0, "READY", ""),
            "getprop gsm.version.baseband": (0, "", ""),
            "service call iphonesubinfo": (0, SAMPLE_IMEI_PARCEL, ""),
            "dumpsys telephony.registry": (0, "level=3", ""),
        }
        b = FakeBridge(routes)
        res = NetworkDiagnostics(b).diagnose("s1")

        self.assertEqual(res.status, Status.FAIL)
        self.assertEqual(res.fault_area, "modem_efs")
        self.assertEqual(res.confidence, "medium")
        self.assertEqual(res.diagnosis.key, "network.fault.modem_efs")
        self.assertTrue(any(c.key == "network.cause.efs" for c in res.possible_causes))
        self.assertTrue(any("getprop gsm.version.baseband" in e.source for e in res.evidence))

    def test_imei_null_diagnoses_modem_efs(self):
        routes = {
            "getprop gsm.sim.state": (0, "READY", ""),
            "getprop gsm.version.baseband": (0, SAMPLE_BASEBAND, ""),
            "service call iphonesubinfo": (0, "Result: Parcel(00000000 '000000000000000')", ""),
            "dumpsys telephony.registry": (0, "level=3", ""),
        }
        b = FakeBridge(routes)
        res = NetworkDiagnostics(b).diagnose("s1")

        self.assertEqual(res.status, Status.FAIL)
        self.assertEqual(res.fault_area, "modem_efs")
        self.assertEqual(res.diagnosis.key, "network.fault.modem_efs")

    def test_baseband_unknown_string_diagnoses_modem_efs(self):
        routes = {
            "getprop gsm.sim.state": (0, "READY", ""),
            "getprop gsm.version.baseband": (0, "unknown", ""),
            "service call iphonesubinfo": (0, SAMPLE_IMEI_PARCEL, ""),
            "dumpsys telephony.registry": (0, "level=3", ""),
        }
        b = FakeBridge(routes)
        res = NetworkDiagnostics(b).diagnose("s1")

        self.assertEqual(res.status, Status.FAIL)
        self.assertEqual(res.fault_area, "modem_efs")

    def test_sim_absent_without_manual_answer_requests_step(self):
        routes = {
            "getprop gsm.sim.state": (0, "ABSENT", ""),
            "getprop gsm.version.baseband": (0, SAMPLE_BASEBAND, ""),
            "service call iphonesubinfo": (0, SAMPLE_IMEI_PARCEL, ""),
            "dumpsys telephony.registry": (0, "level=0", ""),
        }
        b = FakeBridge(routes)
        res = NetworkDiagnostics(b).diagnose("s1", answers={})

        self.assertEqual(res.status, Status.UNKNOWN)
        self.assertIsNotNone(res.manual_step)
        self.assertEqual(res.manual_step.step_id, "known_good_sim")
        self.assertEqual(res.diagnosis.key, "network.sim_absent_prompt")

    def test_sim_absent_with_known_good_sim_diagnoses_sim_reader(self):
        routes = {
            "getprop gsm.sim.state": (0, "ABSENT", ""),
            "getprop gsm.version.baseband": (0, SAMPLE_BASEBAND, ""),
            "service call iphonesubinfo": (0, SAMPLE_IMEI_PARCEL, ""),
            "dumpsys telephony.registry": (0, "level=0", ""),
        }
        b = FakeBridge(routes)
        res = NetworkDiagnostics(b).diagnose("s1", answers={"known_good_sim": INSERTED})

        self.assertEqual(res.status, Status.FAIL)
        self.assertEqual(res.fault_area, "sim_reader")
        self.assertEqual(res.confidence, "medium")
        self.assertEqual(res.diagnosis.key, "network.fault.sim_reader")
        self.assertTrue(any(c.key == "network.cause.reader_pins" for c in res.possible_causes))
        self.assertTrue(any(c.key == "network.cause.sim_power" for c in res.possible_causes))
        self.assertTrue(any(c.key == "network.cause.modem_hardware" for c in res.possible_causes))

    def test_sim_absent_skipped_returns_unknown(self):
        routes = {
            "getprop gsm.sim.state": (0, "ABSENT", ""),
            "getprop gsm.version.baseband": (0, SAMPLE_BASEBAND, ""),
            "service call iphonesubinfo": (0, SAMPLE_IMEI_PARCEL, ""),
            "dumpsys telephony.registry": (0, "level=0", ""),
        }
        b = FakeBridge(routes)
        res = NetworkDiagnostics(b).diagnose("s1", answers={"known_good_sim": SKIP})

        self.assertEqual(res.status, Status.UNKNOWN)
        self.assertEqual(res.diagnosis.key, "sim.absent")

    def test_sim_io_error_fails(self):
        routes = {
            "getprop gsm.sim.state": (0, "CARD_IO_ERROR", ""),
            "getprop gsm.version.baseband": (0, SAMPLE_BASEBAND, ""),
            "service call iphonesubinfo": (0, SAMPLE_IMEI_PARCEL, ""),
            "dumpsys telephony.registry": (0, "level=0", ""),
        }
        b = FakeBridge(routes)
        res = NetworkDiagnostics(b).diagnose("s1")

        self.assertEqual(res.status, Status.FAIL)
        self.assertEqual(res.fault_area, "sim_card")
        self.assertEqual(res.diagnosis.key, "network.sim_io_error")

    def test_rf_antenna_fault_when_imei_and_sim_ready_but_signal_zero(self):
        routes = {
            "getprop gsm.sim.state": (0, "READY", ""),
            "getprop gsm.version.baseband": (0, SAMPLE_BASEBAND, ""),
            "service call iphonesubinfo": (0, SAMPLE_IMEI_PARCEL, ""),
            "dumpsys telephony.registry": (0, "mVoiceRegState=1(OUT_OF_SERVICE)\nmSignalStrength=level=0", ""),
        }
        b = FakeBridge(routes)
        res = NetworkDiagnostics(b).diagnose("s1")

        self.assertEqual(res.status, Status.FAIL)
        self.assertEqual(res.fault_area, "rf_antenna")
        self.assertEqual(res.confidence, "medium")
        self.assertEqual(res.diagnosis.key, "network.fault.rf_antenna")
        self.assertTrue(any(c.key == "network.cause.antenna_cable" for c in res.possible_causes))
        self.assertTrue(any(c.key == "network.cause.pa_ic" for c in res.possible_causes))

    def test_normal_network_passes(self):
        routes = {
            "getprop gsm.sim.state": (0, "READY", ""),
            "getprop gsm.version.baseband": (0, SAMPLE_BASEBAND, ""),
            "service call iphonesubinfo": (0, SAMPLE_IMEI_PARCEL, ""),
            "dumpsys telephony.registry": (0, "mVoiceRegState=0(IN_SERVICE)\nmSignalStrength=level=4 rssi=-65 dBm", ""),
        }
        b = FakeBridge(routes)
        res = diagnose_network(b, "s1")

        self.assertEqual(res.status, Status.PASS)
        self.assertEqual(res.diagnosis.key, "network.ok")
        self.assertEqual(res.imei, SAMPLE_IMEI)
        self.assertEqual(res.baseband_version, SAMPLE_BASEBAND)
        self.assertEqual(res.signal_level, 4)

    def test_to_check_result_contract(self):
        routes = {
            "getprop gsm.sim.state": (0, "READY", ""),
            "getprop gsm.version.baseband": (0, SAMPLE_BASEBAND, ""),
            "service call iphonesubinfo": (0, SAMPLE_IMEI_PARCEL, ""),
            "dumpsys telephony.registry": (0, "mVoiceRegState=0(IN_SERVICE)\nmSignalStrength=level=4", ""),
        }
        b = FakeBridge(routes)
        res = NetworkDiagnostics(b).diagnose("s1")
        chk = res.to_check_result()

        self.assertIsInstance(chk, CheckResult)
        self.assertEqual(chk.check_id, "network.diagnosis")
        self.assertEqual(chk.status, Status.PASS)
        self.assertTrue(chk.has_real_data)
        self.assertGreaterEqual(len(chk.evidence), 4)

    def test_no_telephony_returns_unknown(self):
        routes = {
            "getprop gsm.sim.state": (0, "", ""),
            "getprop gsm.version.baseband": (0, "", ""),
            "service call iphonesubinfo": (1, "", "service not found"),
            "dumpsys telephony.registry": (1, "", "service not found"),
        }
        b = FakeBridge(routes)
        res = NetworkDiagnostics(b).diagnose("s1")

        self.assertEqual(res.status, Status.UNKNOWN)
        self.assertEqual(res.diagnosis.key, "sim.no_telephony")

    def test_permission_denied_returns_needs_root(self):
        routes = {
            "getprop gsm.sim.state": PERMISSION,
            "getprop gsm.version.baseband": (0, SAMPLE_BASEBAND, ""),
            "service call iphonesubinfo": (0, SAMPLE_IMEI_PARCEL, ""),
            "dumpsys telephony.registry": (0, "level=3", ""),
        }
        b = FakeBridge(routes)
        res = NetworkDiagnostics(b).diagnose("s1")

        self.assertEqual(res.status, Status.NEEDS_ROOT)


if __name__ == "__main__":
    unittest.main()
