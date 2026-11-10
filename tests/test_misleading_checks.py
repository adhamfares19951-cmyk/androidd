"""
Step 3: the checks that used to give misleading results.
Assertions are on statuses, check ids, issued commands and structured fields, not on sentences.
"""

import os
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from tests.fakes import FakeBridge, PERMISSION, MISSING
from core.board_diag import MotherboardDiagnostics
from core.optimizer import RealSystemOptimizer
from core.rescue_center import RescueCenter, WHATSAPP_MEDIA_PATHS, WHATSAPP_NOTE
from core.malware_hunter import MalwareHunter, parse_granted_permissions
from database.db_manager import DatabaseManager
from ui.report_exporter import DiagnosticReportExporter
from ui.i18n import STRINGS, render

NO_CHARGER = (0, "Current Battery Service state:\n  AC powered: false\n  USB powered: false\n  Wireless powered: false\n  Dock powered: false\n  level: 80", "")
WITH_CHARGER = (0, "  AC powered: false\n  USB powered: true\n  Wireless powered: false\n  level: 80", "")
WIFI_SERIAL = "192.168.1.20:5555"


class _DbCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.db.close()
        DatabaseManager.reset_instance()
        self.tmp.cleanup()


class TestScreenOffCurrent(_DbCase):
    def run_test(self, bridge, serial=WIFI_SERIAL):
        with patch("core.board_diag.time.sleep"):
            return MotherboardDiagnostics(bridge, db=self.db).run_leakage_test(serial, sample_count=3, sample_interval=0)

    def test_usb_connection_is_not_measured(self):
        b = FakeBridge({"dumpsys battery": NO_CHARGER, "battery/current_now": (0, "-120000", "")})
        r = self.run_test(b, serial="R58N12345")
        self.assertEqual(r.status, "UNKNOWN")
        self.assertEqual(r.precondition, "usb_connection")
        self.assertIsNone(r.avg_ma)
        self.assertFalse(any("input keyevent" in c for c in b.commands), "screen must not be touched when the test cannot run")

    def test_charger_connected_is_not_measured(self):
        r = self.run_test(FakeBridge({"dumpsys battery": WITH_CHARGER, "battery/current_now": (0, "-120000", "")}))
        self.assertEqual(r.status, "UNKNOWN")
        self.assertEqual(r.precondition, "charger_connected")

    def test_power_state_unreadable_is_not_measured(self):
        r = self.run_test(FakeBridge({"battery/current_now": (0, "-120000", "")}))
        self.assertEqual(r.status, "UNKNOWN")
        self.assertEqual(r.precondition, "power_state_unknown")

    def test_normal_current_passes(self):
        r = self.run_test(FakeBridge({"dumpsys battery": NO_CHARGER, "battery/current_now": (0, "-120000", "")}))
        self.assertEqual(r.status, "PASS")
        self.assertEqual(r.avg_ma, 120.0)
        self.assertEqual(r.samples, [120.0, 120.0, 120.0])
        self.assertEqual(r.check_id, "battery.screen_off_current")

    def test_high_current_is_possible_causes_not_a_short(self):
        r = self.run_test(FakeBridge({"dumpsys battery": NO_CHARGER, "battery/current_now": (0, "-400000", "")}))
        self.assertEqual(r.status, "FAIL")
        self.assertGreaterEqual(len(r.possible_causes), 2)
        self.assertEqual(r.diagnosis.key, "leak.high")
        for lang in ("en", "ar"):
            self.assertNotRegex(render(r.diagnosis, lang).lower(), r"short|leak|شورت|تسريب")

    def test_unreadable_current_is_never_averaged(self):
        r = self.run_test(FakeBridge({"dumpsys battery": NO_CHARGER, "battery/current_now": PERMISSION}))
        self.assertEqual(r.status, "NEEDS_ROOT")
        self.assertIsNone(r.avg_ma)
        self.assertEqual(r.samples, [])


class TestThermalThresholds(_DbCase):
    def read(self, routes):
        return MotherboardDiagnostics(FakeBridge(routes), db=self.db).read_thermal("s")

    def test_cpu_under_load_is_normal(self):
        out = ("Temperature{mValue=80.0, mType=0, mName=cpu-0-0, mStatus=0}\n"
               "Temperature{mValue=48.0, mType=2, mName=battery, mStatus=0}\n"
               "Temperature{mValue=3.9, mType=6, mName=vbat, mStatus=0}")
        r = self.read({"thermalservice": (0, out, "")})
        self.assertEqual(r.status, "PASS")
        by_name = {z.zone_type: z for z in r.zones}
        self.assertNotIn("vbat", by_name, "BCL voltage readings are not temperatures")
        self.assertEqual(by_name["cpu-0-0"].category, "cpu")
        self.assertFalse(by_name["cpu-0-0"].is_overheated)
        self.assertEqual(by_name["battery"].limit_c, 50.0)

    def test_hot_skin_fails(self):
        r = self.read({"thermalservice": (0, "Temperature{mValue=47.0, mType=3, mName=skin, mStatus=0}", "")})
        self.assertEqual(r.status, "FAIL")
        self.assertTrue(r.zones[0].is_overheated)

    def test_unclassified_zone_is_not_judged(self):
        r = self.read({"thermal_zone": (0, "/sys/class/thermal/thermal_zone0|weird_sensor|60000", "")})
        self.assertEqual(r.status, "UNKNOWN")
        self.assertIsNone(r.zones[0].limit_c)
        self.assertFalse(r.zones[0].is_overheated)

    def test_thresholds_come_from_data_file(self):
        rules = MotherboardDiagnostics.load_thermal_rules()
        for cat in ("battery", "skin", "cpu", "gpu"):
            self.assertIn(cat, rules["categories"])
        self.assertGreater(rules["categories"]["cpu"]["limit_c"], rules["categories"]["skin"]["limit_c"])


class _DfBridge(FakeBridge):
    """Returns a different `df -k /data` reading on each call."""
    def __init__(self, df_available_kb, **kw):
        super().__init__(**kw)
        self._df = list(df_available_kb)

    def shell(self, command, serial=None, timeout=30, root=False, use_socket=True):
        if command.startswith("df -k /data"):
            self.commands.append(command)
            avail = self._df.pop(0) if len(self._df) > 1 else self._df[0]
            return 0, f"Filesystem 1K-blocks Used Available Use% Mounted on\n/dev/block/dm-5 100000000 50000000 {avail} 50% /data", ""
        return super().shell(command, serial, timeout, root, use_socket)


class TestMaintenance(_DbCase):
    FORBIDDEN = ("rm -rf", "settings put", "device_config", "sm fstrim", "vdc", "deviceidle", "compile -m")

    def test_only_measured_or_labelled_actions(self):
        b = _DfBridge([50000000, 50102400, 50102400], routes={
            "pm trim-caches": (0, "", ""), "sm idle-maint": (0, "", ""), "bg-dexopt-job": (0, "Success", ""),
            "meminfo": (0, "MemTotal: 8000000 kB\nMemAvailable: 3000000 kB", ""),
        })
        r = RealSystemOptimizer(b, db=self.db).run_optimization("s")
        self.assertEqual(r.storage_freed_mb, 100.0)
        self.assertIsNone(r.ram_freed_mb)
        self.assertIsNone(r.drain_reduction_ma)
        by_id = {a.action_id: a for a in r.actions}
        self.assertTrue(by_id["cache_trim"].measured)
        self.assertEqual(by_id["cache_trim"].status, "PASS")
        self.assertFalse(by_id["idle_maintenance"].measured)
        self.assertFalse(by_id["bg_dexopt"].measured)
        self.assertIsNone(by_id["idle_maintenance"].delta)
        for cmd in b.commands:
            for bad in self.FORBIDDEN:
                self.assertNotIn(bad, cmd)

    def test_unreadable_storage_gives_no_number(self):
        r = RealSystemOptimizer(FakeBridge({"pm trim-caches": (0, "", "")}), db=self.db).run_optimization("s")
        self.assertIsNone(r.storage_freed_mb)
        self.assertEqual(r.actions[0].status, "UNKNOWN")

    def test_failed_commands_are_not_pass(self):
        r = RealSystemOptimizer(FakeBridge(), db=self.db).run_optimization("s")
        self.assertEqual({a.status for a in r.actions}, {"FAIL"})


class TestCachePartition(unittest.TestCase):
    def rescue(self, fastboot_routes):
        b = FakeBridge()
        b.fastboot_routes = fastboot_routes
        return RescueCenter(b), b

    def test_erase_only_when_cache_exists(self):
        rc, b = self.rescue({"getvar partition-type:cache": (0, "", "partition-type:cache: ext4\nFinished. Total time: 0.001s"),
                             "erase cache": (0, "", "Erasing 'cache' OKAY")})
        self.assertIs(rc.has_cache_partition("s")[0], True)
        ok, _ = rc.fix_cache_bootloop("s")
        self.assertTrue(ok)
        self.assertIn("fastboot erase cache", b.commands)
        self.assertFalse(any("format" in c for c in b.commands))

    def test_no_cache_partition_erases_nothing(self):
        rc, b = self.rescue({"getvar": (1, "", "getvar:partition-type:cache FAILED (remote: 'GetVar Variable Not found')")})
        self.assertIs(rc.has_cache_partition("s")[0], False)
        ok, _ = rc.fix_cache_bootloop("s")
        self.assertFalse(ok)
        self.assertNotIn("fastboot erase cache", b.commands)

    def test_unreadable_answer_erases_nothing(self):
        rc, b = self.rescue({"getvar": (1, "", "< waiting for any device >")})
        self.assertIsNone(rc.has_cache_partition("s")[0])
        self.assertFalse(rc.fix_cache_bootloop("s")[0])
        self.assertNotIn("fastboot erase cache", b.commands)


class TestDataRescue(unittest.TestCase):
    def pulled(self, category):
        b = FakeBridge()
        with tempfile.TemporaryDirectory() as d:
            _, summary = RescueCenter(b).emergency_data_pull("s", category, d)
        return [c[len("pull "):] for c in b.commands if c.startswith("pull ")], summary

    def test_whatsapp_media_only(self):
        paths, summary = self.pulled("whatsapp")
        self.assertEqual(paths, list(WHATSAPP_MEDIA_PATHS))
        self.assertIn(WHATSAPP_NOTE, summary)

    def test_full_storage_never_pulls_whatsapp_databases(self):
        paths, _ = self.pulled("full_storage")
        for p in paths:
            if "whatsapp" in p.lower():
                self.assertTrue(p.endswith("/Media"), p)


class TestReportHasNoIntegrityClaim(unittest.TestCase):
    def test_no_signature_claims(self):
        with tempfile.TemporaryDirectory() as d:
            path = DiagnosticReportExporter.generate_html_report(
                device_profile={"model": "Pixel", "serial": "S1"}, board_diag={}, hw_audit={}, sec_scan={},
                technician_name="T", lang="en", output_dir=d)
            with open(path, encoding="utf-8") as f:
                content = f.read()
        for bad in ("HMAC", "SIG:", "DIGITALLY SIGNED", "QR VERIFIED", "VERIFIED DIAGNOSTIC", "Certificate"):
            self.assertNotIn(bad, content)
        import ui.report_exporter as rex
        self.assertFalse(hasattr(rex, "CERTIFICATE_SIGNING_SECRET"))


GRANTED_DUMP = """Packages:
  Package [com.x] (abc):
    requested permissions:
      android.permission.READ_SMS
      android.permission.CAMERA
    install permissions:
      android.permission.INTERNET: granted=true
    User 0: ceDataInode=1 installed=true
      runtime permissions:
        android.permission.CAMERA: granted=true, flags=[ USER_SET ]
        android.permission.READ_SMS: granted=false, flags=[ ]
"""


class TestSensitivePermissions(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = DatabaseManager(os.path.join(self.tmp.name, "t.db"))

    def tearDown(self):
        self.db.close()
        DatabaseManager.reset_instance()
        self.tmp.cleanup()

    def bridge(self, ca_reply):
        return FakeBridge({
            "pm list packages -f -3": (0, "package:/data/app/x/base.apk=com.x", ""),
            "pm list packages -f -s": (0, "", ""),
            "query-activities": (0, "packageName=com.other", ""),
            "dumpsys package com.x": (0, GRANTED_DUMP, ""),
            "enabled_accessibility_services": (0, "com.x/com.x.AccService", ""),
            "device_policy": (0, "", ""),
            "cacerts-added": ca_reply,
        })

    def test_only_granted_permissions_count(self):
        granted, lines = parse_granted_permissions(GRANTED_DUMP)
        self.assertEqual(granted, ["android.permission.CAMERA"])
        self.assertEqual(len(lines), 1)

    def test_inventory_without_verdicts(self):
        scan = MalwareHunter(self.bridge(PERMISSION), self.db).audit_security("s")
        self.assertEqual(scan.check_id, "security.sensitive_permissions")
        self.assertEqual(len(scan.sensitive_apps), 1)
        app = scan.sensitive_apps[0]
        self.assertEqual(app.sensitive_permissions, ["android.permission.CAMERA"])
        self.assertTrue(app.accessibility_enabled)
        self.assertIs(app.has_launcher_icon, False)
        self.assertTrue(app.evidence)
        self.assertFalse(hasattr(app, "threat_level"))
        d = scan.to_dict()
        for key in ("critical_threats", "high_threats", "ghost_apps", "overall_risk"):
            self.assertNotIn(key, d)

    def test_user_ca_store_status(self):
        cases = [(PERMISSION, "NEEDS_ROOT"), ((0, "", ""), "PASS"), ((0, "a1b2c3d4.0", ""), "FAIL"), (MISSING, "UNKNOWN")]
        for reply, expected in cases:
            with self.subTest(reply=reply):
                scan = MalwareHunter(self.bridge(reply), self.db).audit_security("s")
                self.assertEqual(scan.user_ca_status, expected)


class TestNoMisleadingWording(unittest.TestCase):
    EN = re.compile(r"\bRAT\b|Spyware|Malware Hunter|Signed|DIGITALLY|HMAC|Official|Ghost|Optimizer|Leakage", re.I)
    AR = re.compile(r"التجسس|تجسس|موثق|المعتمد|شبحي|تسريب|شورت|الفيروسات|التسريع")

    def test_i18n_values(self):
        # The honest disclaimer "not (digitally) signed" / "غير موقّع" is allowed; any claim of signing is not.
        for key, value in STRINGS["en"].items():
            claim = re.sub(r"not (digitally )?signed", "", value, flags=re.I)
            self.assertIsNone(self.EN.search(claim), f"en.{key}")
        for key, value in STRINGS["ar"].items():
            claim = value.replace("غير موقّع", "")
            self.assertIsNone(self.AR.search(claim), f"ar.{key}")
            self.assertNotIn("موقّع", claim, f"ar.{key}")


if __name__ == "__main__":
    unittest.main()
