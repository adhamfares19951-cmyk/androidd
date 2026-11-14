"""
Status-derivation matrix: every sysfs / command based check is fed the same five kinds of read
(normal output, empty output, permission denied in stderr, missing path, malformed output)
and must return exactly the expected status. Assertions are on status values and check ids only.
"""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.fakes import FakeBridge, EMPTY, PERMISSION, MISSING, MALFORMED
from core.result import Status, Evidence, status_from_read, aggregate_status
from core.readers import run_read, read_node, parse_int
from core import jedec
from core.adb_bridge import parse_shell_v2_stream
from core.board_diag import MotherboardDiagnostics, parse_usb_type
from core.hardware_tester import HardwareTester
from core.storage_benchmark import StorageBenchmark, parse_dd_speed_mb_s
from database.db_manager import DatabaseManager

P, F, U, R = "PASS", "FAIL", "UNKNOWN", "NEEDS_ROOT"

SCENARIOS = {"empty": EMPTY, "permission": PERMISSION, "missing": MISSING, "malformed": MALFORMED}


def v2_packet(pkt_id: int, payload: bytes) -> bytes:
    return bytes([pkt_id]) + len(payload).to_bytes(4, "little") + payload


class _Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.db.close()
        DatabaseManager.reset_instance()
        self.tmp.cleanup()

    def board(self, bridge):
        return MotherboardDiagnostics(bridge, db=self.db)

    def assertEvidence(self, items):
        self.assertTrue(items, "evidence list must not be empty")
        for e in items:
            self.assertIsInstance(e, Evidence)
            self.assertTrue(e.source)

    def run_matrix(self, pattern, normal, expected, check, extra_routes=None):
        """expected: dict scenario -> status. check(bridge) -> (status, evidence)."""
        cases = dict(SCENARIOS)
        cases["normal"] = normal
        for name, reply in cases.items():
            if name not in expected:
                continue
            with self.subTest(scenario=name):
                bridge = FakeBridge(dict(extra_routes or {}))
                bridge.route(pattern, reply)
                status, evidence = check(bridge)
                self.assertEqual(str(status), expected[name])
                self.assertEvidence(evidence)


class TestResultModel(unittest.TestCase):
    def test_status_from_read_never_returns_pass_or_fail(self):
        for out, err, code in [("4123000", "", 0), ("", "", 0), ("x", "boom", 1), (None, None, None)]:
            self.assertIn(status_from_read(out, err, code), (Status.UNKNOWN, Status.NEEDS_ROOT))

    def test_permission_in_stdout_or_stderr(self):
        self.assertEqual(status_from_read("", "cat: x: Permission denied", 1), Status.NEEDS_ROOT)
        self.assertEqual(status_from_read("Operation not permitted", "", None), Status.NEEDS_ROOT)
        self.assertEqual(status_from_read("", "avc: denied { read }", 1), Status.NEEDS_ROOT)

    def test_log_content_avc_lines_are_not_a_permission_error(self):
        log = "\n".join(f"[{i}.0] audit: avc: denied {{ read }} for comm=\"x\"" for i in range(10))
        self.assertEqual(status_from_read(log, "", 0, log_content=True), Status.UNKNOWN)
        self.assertEqual(status_from_read(log, "", 0), Status.NEEDS_ROOT)
        self.assertEqual(status_from_read("", "dmesg: klogctl: Permission denied", 1, log_content=True), Status.NEEDS_ROOT)

    def test_aggregate_status(self):
        self.assertEqual(aggregate_status([]), Status.UNKNOWN)
        self.assertEqual(aggregate_status([P, P]), Status.PASS)
        self.assertEqual(aggregate_status([P, U]), Status.UNKNOWN)
        self.assertEqual(aggregate_status([P, R]), Status.UNKNOWN)
        self.assertEqual(aggregate_status([P, R, F]), Status.FAIL)

    def test_evidence_exit_code_default_none(self):
        self.assertIsNone(Evidence(source="x", raw_excerpt="y").exit_code)


class TestReaders(unittest.TestCase):
    def test_read_node_prefers_permission_error_over_missing(self):
        b = FakeBridge({"/a": MISSING, "/b": PERMISSION})
        res = read_node(b, ["/a", "/b"])
        self.assertEqual(res.status, Status.NEEDS_ROOT)
        self.assertEqual(res.source, "/b")

    def test_glob_paths_are_not_quoted(self):
        b = FakeBridge({"cat /sys/x/*": (0, "1", "")})
        self.assertTrue(read_node(b, ["/sys/x/*"]).ok)
        self.assertEqual(b.commands[-1], "cat /sys/x/*")
        read_node(b, ["/sys/y"])
        self.assertEqual(b.commands[-1], "cat '/sys/y'")

    def test_legacy_shell_errors_in_stdout_are_not_data(self):
        # Legacy shell (exit code None) merges stderr into stdout
        b = FakeBridge({"cat": (None, "cat: /x: No such file or directory", "")})
        res = run_read(b, "cat /x")
        self.assertFalse(res.ok)
        self.assertFalse(res.path_exists)
        self.assertEqual(res.status, Status.UNKNOWN)

    def test_parse_int_is_strict(self):
        self.assertEqual(parse_int("4123000\n"), 4123000)
        self.assertEqual(parse_int("-450"), -450)
        self.assertIsNone(parse_int("abc12"))
        self.assertIsNone(parse_int(""))


class TestShellV2(unittest.TestCase):
    def test_stdout_stderr_and_exit_code_are_separated(self):
        data = v2_packet(1, b"hello\n") + v2_packet(2, b"oops\n") + v2_packet(1, b"world") + v2_packet(3, bytes([2]))
        out, err, code = parse_shell_v2_stream(data)
        self.assertEqual(out, b"hello\nworld")
        self.assertEqual(err, b"oops\n")
        self.assertEqual(code, 2)

    def test_missing_exit_packet_gives_none(self):
        out, err, code = parse_shell_v2_stream(v2_packet(1, b"partial"))
        self.assertEqual(out, b"partial")
        self.assertIsNone(code)


class TestParsers(unittest.TestCase):
    def test_jedec(self):
        self.assertEqual(jedec.parse_life_time("0x01 0x02"), [1, 2])
        self.assertEqual(jedec.parse_life_time("0x00 0x00"), [])
        self.assertEqual(jedec.parse_life_time("garbage"), [])
        self.assertEqual(jedec.evaluate([1, 2], 1), Status.PASS)
        self.assertEqual(jedec.evaluate([9], 1), Status.FAIL)
        self.assertEqual(jedec.evaluate([1], 2), Status.FAIL)
        self.assertEqual(jedec.evaluate([], None), Status.UNKNOWN)

    def test_dd_speed(self):
        self.assertEqual(parse_dd_speed_mb_s("67108864 bytes (64 M) copied, 0.5 s, 128 M/s"), 128.0)
        self.assertEqual(parse_dd_speed_mb_s("41943040 bytes (42 MB, 40 MiB) copied, 0.4 s, 105 MB/s"), 100.0)
        self.assertIsNone(parse_dd_speed_mb_s("dd: /data/local/tmp/x: Permission denied"))

    def test_usb_type(self):
        self.assertEqual(parse_usb_type("Unknown [SDP] DCP CDP"), "SDP")
        self.assertEqual(parse_usb_type("USB_PD"), "USB_PD")
        self.assertIsNone(parse_usb_type("garbage@@value"))
        self.assertIsNone(parse_usb_type("Unknown"))


class TestBoardMatrix(_Base):
    def test_battery_voltage(self):
        def check(b):
            p = self.board(b).analyze_pmic("serial")
            if p.voltage_status == P:
                self.assertEqual(p.voltage_mv, 4123.0)
                self.assertEqual(p.voltage_source, "/sys/class/power_supply/battery/voltage_now")
            else:
                self.assertIsNone(p.voltage_mv)
            return p.voltage_status, p.evidence
        self.run_matrix("battery/voltage_now", (0, "4123000", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check)

    def test_battery_current(self):
        def check(b):
            p = self.board(b).analyze_pmic("serial")
            if p.current_status == P:
                self.assertEqual(p.current_ma, -450.0)
            else:
                self.assertIsNone(p.current_ma)
            return p.current_status, p.evidence
        self.run_matrix("battery/current_now", (0, "-450000", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check)

    def test_battery_temperature(self):
        def check(b):
            p = self.board(b).analyze_pmic("serial")
            if p.temp_status == P:
                self.assertEqual(p.temp_c, 31.5)
            else:
                self.assertIsNone(p.temp_c)
            return p.temp_status, p.evidence
        self.run_matrix("battery/temp", (0, "315", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check)

    def test_battery_capacity_vs_design(self):
        def check(b):
            p = self.board(b).analyze_pmic("serial")
            return p.health_verdict, p.evidence
        normal = {"battery/charge_full_design": (0, "5000000", "")}
        self.run_matrix("battery/charge_full", (0, "4500000", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check,
                        extra_routes=normal)
        b = FakeBridge({"battery/charge_full_design": (0, "5000000", ""), "battery/charge_full": (0, "3000000", "")})
        self.assertEqual(self.board(b).analyze_pmic("s").health_verdict, F)

    def test_thermal(self):
        def check(b):
            t = self.board(b).read_thermal("serial")
            return t.status, t.evidence
        normal = (0, "Temperature{mValue=36.5, mType=0, mName=cpu0, mStatus=0}", "")
        # same reply for dumpsys thermalservice and the sysfs fallback
        for name, reply in [("normal", normal)] + list(SCENARIOS.items()):
            with self.subTest(scenario=name):
                b = FakeBridge({"thermalservice": reply, "thermal_zone": reply})
                t = self.board(b).read_thermal("s")
                self.assertEqual(t.status, {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}[name])
                self.assertEvidence(t.evidence)

    def test_ramoops(self):
        def check(b):
            bb = self.board(b).run_blackbox_forensics("serial")
            return bb.kernel_ramoops_status, bb.evidence
        self.run_matrix("pstore", (0, "[ 1.0] boot ok\n[ 2.0] init done", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U}, check)
        b = FakeBridge({"pstore": (0, "[ 9.0] Kernel panic - not syncing: Fatal exception", "")})
        self.assertEqual(self.board(b).run_blackbox_forensics("s").kernel_ramoops_status, F)

    def test_boot_reason_requires_a_reading(self):
        bb = self.board(FakeBridge()).run_blackbox_forensics("s")
        self.assertEqual(bb.status, U)
        self.assertEqual(bb.shutdown_category, "UNKNOWN")
        bb = self.board(FakeBridge(props={"sys.boot.reason": "reboot,userrequested"})).run_blackbox_forensics("s")
        self.assertEqual(bb.status, P)
        bb = self.board(FakeBridge(props={"sys.boot.reason": "kernel_panic"})).run_blackbox_forensics("s")
        self.assertEqual(bb.status, F)
        self.assertTrue(bb.possible_causes)

    def test_dmesg(self):
        def check(b):
            pa = self.board(b).inspect_panic_and_emmc("serial")
            return pa.dmesg_status, pa.evidence
        self.run_matrix("dmesg", (0, "[ 1.0] Booting Linux\n[ 2.0] init: started", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U}, check)
        b = FakeBridge({"dmesg": (0, "[ 3.0] mmc0: error -110 whilst initialising", "")})
        self.assertEqual(self.board(b).inspect_panic_and_emmc("s").status, F)

    def test_storage_life_registers(self):
        def check(b):
            r = self.board(b).read_emmc_ufs_lifespan("serial")
            self.assertEqual(r.health_verdict, r.status)
            return r.status, r.evidence
        self.run_matrix("mmcblk0/device/life_time", (0, "0x01 0x02", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check)
        b = FakeBridge({"mmcblk0/device/life_time": (0, "0x0a 0x0a", "")})
        self.assertEqual(self.board(b).read_emmc_ufs_lifespan("s").status, F)

    def test_fast_charge(self):
        def check(b):
            r = self.board(b).diagnose_fast_charge("serial")
            if r.status != P:
                self.assertIsNone(r.voltage_mv)
                self.assertIsNone(r.wattage_w)
                self.assertEqual(r.charger_type, "UNKNOWN")
            return r.status, r.evidence
        self.run_matrix("power_supply/usb/", (0, "Unknown [SDP] DCP", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check)

    def test_fast_charge_power_only_from_two_measurements(self):
        b = FakeBridge({"usb/voltage_now": (0, "5000000", ""), "usb/current_now": (0, "1500000", "")})
        r = self.board(b).diagnose_fast_charge("s")
        self.assertEqual(r.wattage_w, 7.5)
        b = FakeBridge({"usb/voltage_now": (0, "5000000", "")})
        self.assertIsNone(self.board(b).diagnose_fast_charge("s").wattage_w)

    def test_full_board_overall(self):
        r = self.board(FakeBridge()).run_full_diagnostics("s")
        self.assertEqual(r.overall_health, U)
        self.assertEqual(set(r.check_statuses), {"battery.fuel_gauge", "thermal.zones", "board.boot_reason", "board.kernel_log"})

    def test_stress_test_without_readings_does_not_crash(self):
        with patch("core.board_diag.time.sleep"):
            r = self.board(FakeBridge()).run_cpu_pmic_stress_test("s", duration_sec=4)
        self.assertEqual(r.status, U)
        self.assertIsNone(r.voltage_sag_mv)
        self.assertTrue(all(s.voltage_mv is None for s in r.samples))

    def test_storage_benchmark_unparsed_is_not_a_verdict(self):
        w, rd, status, _ = self.board(FakeBridge()).benchmark_storage_speed("s", 1)
        self.assertIsNone(w)
        self.assertEqual(status, U)
        b = FakeBridge({"of=/data": (0, "", "1048576 bytes (1 M) copied, 0.1 s, 10 M/s")})
        self.assertEqual(self.board(b).benchmark_storage_speed("s", 1)[2], F)

    def test_reset_battery_statistics_reports_real_exit_code(self):
        ok, _ = self.board(FakeBridge({"batterystats --reset": (0, "Battery stats reset.", "")})).reset_battery_fuel_gauge_stats("s")
        self.assertTrue(ok)
        ok, _ = self.board(FakeBridge()).reset_battery_fuel_gauge_stats("s")
        self.assertFalse(ok)


class TestStorageBenchmarkMatrix(_Base):
    def test_wear_registers(self):
        def check(b):
            r = StorageBenchmark(b).diagnose_storage_health("serial")
            self.assertEqual(r.has_real_data, r.status in (P, F))
            return r.status, r.evidence
        self.run_matrix("device/life_time", (0, "0x01 0x01", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check,
                        extra_routes={"pre_eol_info": (0, "0x01", "")})

    def test_full_storage_test_exists_for_ui(self):
        r = StorageBenchmark(FakeBridge()).run_full_storage_test("s")
        self.assertEqual(r.status, U)
        self.assertEqual(r.benchmark_status, U)


class TestHardwareMatrix(_Base):
    FEATURES = {"pm has-feature": (0, "true", "")}

    def test_fingerprint(self):
        def check(b):
            r = HardwareTester(b).diagnose_fingerprint("serial")
            self.assertEqual(r.check_id, "fingerprint.sensor")
            return r.status, r.evidence
        self.run_matrix("dumpsys fingerprint", (0, '{"prints":[{"id":0,"count":1}],"sensorId":0}', ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check,
                        extra_routes=self.FEATURES)
        b = FakeBridge(dict(self.FEATURES)).route("dumpsys fingerprint", (0, "Can't find service: fingerprint", ""))
        self.assertEqual(HardwareTester(b).diagnose_fingerprint("s").status, F)

    def test_keys(self):
        events = "\n".join(f"/dev/input/event0: EV_KEY {k} {s}" for k in ("KEY_VOLUMEUP", "KEY_VOLUMEDOWN", "KEY_POWER")
                           for s in ("DOWN", "UP"))

        def check(b):
            r = HardwareTester(b).diagnose_physical_buttons("serial")
            return r.status, r.evidence
        self.run_matrix("getevent", (124, events, ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check)

    def test_sim(self):
        tele = {"telephony": (0, "true", "")}
        cases = [
            ({"gsm.sim.state": (0, "READY,ABSENT", ""), "gsm.version.baseband": (0, "MPSS.HI.1", "")}, P),
            ({"gsm.sim.state": (0, "ABSENT", ""), "gsm.version.baseband": (0, "MPSS.HI.1", "")}, U),
            ({"gsm.sim.state": (0, "CARD_IO_ERROR", ""), "gsm.version.baseband": (0, "MPSS.HI.1", "")}, F),
            ({"gsm.sim.state": (0, "ABSENT", "")}, F),  # radio up, no baseband reported
            ({}, U),                                    # nothing readable
        ]
        for routes, expected in cases:
            with self.subTest(routes=routes):
                r = HardwareTester(FakeBridge({**tele, **routes})).diagnose_sim_and_baseband("s")
                self.assertEqual(r.status, expected)
                self.assertEvidence(r.evidence)
        r = HardwareTester(FakeBridge({"telephony": (0, "false", "")})).diagnose_sim_and_baseband("s")
        self.assertEqual(r.status, U)

    def test_wifi(self):
        def check(b):
            r = HardwareTester(b).diagnose_wifi("serial")
            return r.status, r.evidence
        self.run_matrix("cmd wifi status", (0, "Wifi is enabled\nWifi scanning is always available", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check)
        r = HardwareTester(FakeBridge({"cmd wifi status": (0, "Wifi is disabled", "")})).diagnose_wifi("s")
        self.assertEqual(r.status, U)

    def test_wifi_privacy_mac_is_not_a_fault(self):
        b = FakeBridge({"cmd wifi status": (0, "Wifi is enabled", ""), "wlan0/address": (0, "02:00:00:00:00:00", "")})
        self.assertEqual(HardwareTester(b).diagnose_wifi("s").status, P)

    def test_bluetooth(self):
        def check(b):
            r = HardwareTester(b).diagnose_bluetooth("serial")
            return r.status, r.evidence
        self.run_matrix("bluetooth_manager", (0, "  enabled: true\n  state: ON\n  address: AA:BB:CC:DD:EE:FF", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check,
                        extra_routes=self.FEATURES)

    def test_declared_features_are_never_pass(self):
        r = HardwareTester(FakeBridge(dict(self.FEATURES))).diagnose_peripherals_chips("s")
        self.assertEqual(r.status, U)
        self.assertEqual(r.check_id, "system.declared_features")

    def test_backlight(self):
        def check(b):
            r = HardwareTester(b).diagnose_display_backlight("serial")
            if r.status != P:
                self.assertIsNone(r.panel_detected)
            return r.status, r.evidence
        self.run_matrix("brightness", (0, "200", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check)

    def test_audio(self):
        def check(b):
            r = HardwareTester(b).diagnose_audio_codec("serial")
            return r.status, r.evidence
        self.run_matrix("asound/cards", (0, " 0 [sm8450mtp     ]: sm8450 - sm8450-mtp-snd-card", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check)
        r = HardwareTester(FakeBridge({"asound/cards": (0, "--- no soundcards ---", "")})).diagnose_audio_codec("s")
        self.assertEqual(r.status, F)

    def test_panel_info(self):
        def check(b):
            r = HardwareTester(b).inspect_display_panel_info("serial")
            self.assertEqual(r.check_id, "display.panel_info")
            return r.status, r.evidence
        self.run_matrix("dmesg", (0, "[ 1.2] dsi_panel_init: nt36672a video mode panel", ""),
                        {"normal": P, "empty": U, "permission": R, "missing": U, "malformed": U}, check)

    def test_full_audit_without_device_output_is_unknown_not_fail(self):
        r = HardwareTester(FakeBridge()).run_full_audit("s")
        self.assertEqual(r.status, U)
        self.assertEqual(r.cameras, [])
        self.assertEqual(r.missing_critical_sensors, [])
        self.assertIsNone(r.vibrator_functional)
        self.assertNotIn("buttons.keys", r.check_statuses)

    def test_sensors_and_cameras_are_parsed_not_invented(self):
        b = FakeBridge({
            "sensorservice": (0, "0x0000000b) BMI160 Accelerometer | Bosch | ver: 1 | type: android.sensor.accelerometer(1) | perm: n/a", ""),
            "media.camera": (0, "== Camera HAL device device@3.4/internal/0 (v3.4) static information: ==\n  android.lens.facing: BACK", ""),
        })
        t = HardwareTester(b)
        sensors, missing = t.audit_sensors("s")
        self.assertEqual(len(sensors), 1)
        self.assertIsNone(sensors[0].power_ma)
        self.assertIn("Gyroscope", missing)
        cams = t.audit_cameras("s")
        self.assertEqual([c.camera_id for c in cams], ["0"])
        self.assertEqual(cams[0].facing, "Back")


if __name__ == "__main__":
    unittest.main()
