"""Behaviour tests for the issues confirmed in the external audit (each failed before its fix)."""
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.result import M
from database.db_manager import DatabaseManager
from tests.fakes import FakeBridge

BY_NAME = "/dev/block/by-name"


class PhoneWithPartitions(FakeBridge):
    """Partition table + dd that works, pull that writes `content` (or raises)."""

    def __init__(self, parts, content=b"IMG", pull_raises=False, **kw):
        routes = {"ls -d /dev/block/bootdevice/by-name": (0, BY_NAME, ""),
                  f"ls -l {BY_NAME}": (0, "\n".join(f"lrwxrwxrwx 1 root root 16 2026-01-01 00:00 {n} -> /dev/block/sda{i}"
                                                    for i, (n, _) in enumerate(parts)), "")}
        for i, (_, size) in enumerate(parts):
            routes[f"/sys/class/block/sda{i}/size"] = (0, str(size // 512), "")
        routes["dd if="] = (0, "1+0 records in", "")
        super().__init__(routes, **kw)
        self.content, self.pull_raises = content, pull_raises
        self.shell_calls = []

    def shell(self, command, serial=None, timeout=30, root=False, use_socket=True):
        self.shell_calls.append((command, root))
        return super().shell(command, serial, timeout, root, use_socket)

    def pull(self, remote_path, local_path, serial=None, timeout=60):
        self.commands.append(f"pull {remote_path}")
        if self.pull_raises:
            raise RuntimeError("cable unplugged")
        with open(local_path, "wb") as f:
            f.write(self.content)
        return True, "1 file pulled"


class PartitionBackupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        p = patch.dict(os.environ, {"USERPROFILE": self.tmp.name})
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def manager(self, bridge):
        from core.partition_backup import PartitionBackupManager
        return PartitionBackupManager(bridge)

    def test_critical_set_into_one_folder_with_wireless_serial(self):
        phone = PhoneWithPartitions([("efs", 4 << 20), ("modemst1", 2 << 20), ("vendor", 8 << 20)])
        res = self.manager(phone).backup_critical_partitions("192.168.1.100:5555")
        self.assertTrue(res.success)
        self.assertEqual(sorted(res.dumped_partitions), ["efs", "modemst1"])
        self.assertNotIn(":", os.path.basename(res.backup_directory))
        self.assertEqual(len(os.listdir(res.backup_directory)), 2)
        self.assertEqual(res.diagnosis.key, "part.set_summary")

    def test_phone_copy_removed_even_when_pull_fails(self):
        phone = PhoneWithPartitions([("efs", 1 << 20)], pull_raises=True)
        with self.assertRaises(RuntimeError):
            self.manager(phone).backup_partition("S1", "efs")
        self.assertTrue(any(c.startswith("rm -f /data/local/tmp/adp_efs_") for c, _ in phone.shell_calls))

    def test_empty_file_is_not_a_backup(self):
        phone = PhoneWithPartitions([("efs", 1 << 20)], content=b"")
        r = self.manager(phone).backup_partition("S1", "efs")
        self.assertFalse(r.success)
        self.assertEqual(r.error_message.key, "part.pull_failed")

    def test_root_copy_is_made_readable_for_pull(self):
        phone = PhoneWithPartitions([("efs", 1 << 20)])
        phone.routes.insert(0, ("dd if=/dev/block/sda0 of=/data/local/tmp/adp_efs_", (1, "", "dd: Permission denied")))
        self.manager(phone).backup_partition("S1", "efs")
        root_calls = [c for c, root in phone.shell_calls if root]
        self.assertTrue(any("chmod 644" in c for c in root_calls))

    def test_all_partitions_skips_user_data_and_huge_images(self):
        phone = PhoneWithPartitions([("boot", 64 << 20), ("userdata", 1 << 20), ("system", 3 << 30)])
        res = self.manager(phone).backup_all_partitions("S1")
        self.assertEqual(res.dumped_partitions, ["boot"])
        self.assertEqual(sorted(res.skipped_partitions), ["system", "userdata"])

    def test_backup_base_dir_on_desktop(self):
        self.assertEqual(self.manager(FakeBridge()).backup_base_dir,
                         os.path.join(self.tmp.name, "Desktop", "Android_Doctor_Backups"))


class ApkTests(unittest.TestCase):
    def test_extract_all_user_apks(self):
        from core.batch_apk_manager import BatchAPKManager
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"USERPROFILE": d}):
            phone = PhoneWithPartitions([], routes_extra=None) if False else PhoneWithPartitions([])
            phone.routes.insert(0, ("pm list packages -3 -f", (0, "package:/data/app/a/base.apk=com.a\npackage:/data/app/b/base.apk=com.b", "")))
            phone.routes.insert(0, ("pm path com.", (0, "package:/data/app/x/base.apk", "")))
            seen = []
            res = BatchAPKManager(phone).extract_all_user_apks("10.0.0.2:5555", progress_callback=lambda *a: seen.append(a))
        self.assertEqual([r.package_name for r in res], ["com.a", "com.b"])
        self.assertTrue(all(r.success for r in res))
        self.assertEqual(seen, [(1, 2, "com.a"), (2, 2, "com.b")])
        self.assertNotIn(":", res[0].local_path.split(os.sep)[-2])


class DatabaseTests(unittest.TestCase):
    def test_report_with_message_objects_is_saved(self):
        with tempfile.TemporaryDirectory() as d:
            db = DatabaseManager(os.path.join(d, "t.db"))
            rid = db.save_diagnostic_report(
                device_serial="S1", report_title="x", overall_status="PASS", battery_health_pct=90.0,
                voltage_mv=4000.0, current_ma=-300.0, temp_c=30.0, thermal_summary="ok", storage_summary="ok",
                ram_summary="ok", sensor_summary="ok", security_summary=M("sec.summary", apps=1, total=2, ca="PASS"),
                raw_dict={"summary": M("sec.summary", apps=1, total=2, ca="PASS")})
            self.assertGreater(rid, 0)
            db.close()
            DatabaseManager.reset_instance()


class WirelessTests(unittest.TestCase):
    def test_enable_message_is_translatable(self):
        from core.adb_bridge import ADBBridge
        with patch("core.adb_bridge.get_adb_path", return_value="adb"), \
             patch("core.adb_bridge.get_fastboot_path", return_value="fastboot"), \
             patch("core.adb_bridge.get_scrcpy_path", return_value="scrcpy"):
            ADBBridge._instance = None
            b = ADBBridge()
        try:
            replies = {"tcpip": (0, "restarting in TCP mode port: 5555", ""),
                       "connect": (0, "connected to 10.0.0.2:5555", "")}
            with patch.object(b, "get_device_ip_address", return_value="10.0.0.2"), \
                 patch("core.adb_bridge.time.sleep"), \
                 patch.object(b, "run_adb", side_effect=lambda args, **kw: replies[args[0]]):
                ok, addr, msg = b.enable_wireless_adb("S1")
        finally:
            ADBBridge._instance = None
        self.assertTrue(ok)
        self.assertEqual(addr, "10.0.0.2:5555")
        self.assertEqual(msg.key, "wireless.enabled")


if __name__ == "__main__":
    unittest.main()


class VerdictTests(unittest.TestCase):
    def test_battery_check_fails_on_low_capacity_or_failed_health(self):
        from core.board_diag import PMICStatus
        self.assertEqual(PMICStatus(voltage_status="PASS").check_status, "PASS")
        self.assertEqual(PMICStatus(voltage_status="PASS", health_verdict="FAIL").check_status, "FAIL")
        for health in ("Dead", "Overheat", "Over voltage"):
            self.assertEqual(PMICStatus(voltage_status="PASS", health=health).check_status, "FAIL", health)
        self.assertEqual(PMICStatus(voltage_status="PASS", health="Good").check_status, "PASS")

    def test_high_severity_kernel_signature_is_a_finding(self):
        from core.board_diag import MotherboardDiagnostics
        with tempfile.TemporaryDirectory() as d:
            b = FakeBridge({"dmesg": (0, "\n".join(["[1.0] boot ok"] * 5 + ["[9.9] pmic: uvlo_detected, vph_pwr collapse"]), "")},
                           root=True)
            rep = MotherboardDiagnostics(b, DatabaseManager(os.path.join(d, "t.db"))).run_root_forensics_pro("S1")
            DatabaseManager.reset_instance()
        self.assertEqual(rep.status, "FAIL")
        self.assertIn("UVLO_BROWNOUT", str([f.args for f in rep.findings]))

    def test_report_overall_counts_failed_extra_checks_but_not_ca_inventory(self):
        import re as _re
        from ui.report_exporter import DiagnosticReportExporter
        from ui.i18n import t
        board = {"checks": [{"check_id": "battery.fuel_gauge", "status": "PASS"}]}

        def overall(**kw):
            with tempfile.TemporaryDirectory() as d:
                path = DiagnosticReportExporter.generate_html_report(device_profile={"serial": "S"}, board_diag=board,
                                                                     output_dir=d, lang="en", **kw)
                html = Path(path).read_text(encoding="utf-8")
            return _re.search(_re.escape(t("report.overall", "en")) + r': <span class="badge"[^>]*>([A-Z_]+)<', html).group(1)
        self.assertEqual(overall(), "PASS")
        self.assertEqual(overall(leakage_result={"status": "FAIL"}), "FAIL")      # was PASS before the fix
        self.assertEqual(overall(sec_scan={"user_ca_status": "FAIL"}), "PASS")    # security inventory: shown, not counted

    def test_camera_flash_reads_the_value_not_the_type(self):
        from core.hardware_tester import _flash_value
        self.assertIs(_flash_value("android.flash.info.available (50000): byte[1] [ 0 ]"), False)
        self.assertIs(_flash_value("android.flash.info.available (50000): byte[1] [ 1 ]"), True)
        self.assertIsNone(_flash_value("android.flash.info.available (50000): byte[1]"))

    def test_profiler_does_not_invent_values(self):
        from core.profiler import DeviceProfiler
        p = DeviceProfiler(FakeBridge()).profile_device("S1")   # nothing readable
        self.assertIsNone(p.cpu_cores)
        self.assertEqual(p.display_refresh_rate, "Unknown")
        self.assertEqual(p.encryption_status, "Unknown")
        for name in ("battery_voltage_mv", "battery_temp_c", "battery_level_pct", "ram_total_mb", "storage_free_gb"):
            self.assertIsNone(getattr(p, name), name)

    def test_negative_battery_temperature_keeps_its_sign(self):
        from core.profiler import DeviceProfiler
        p = DeviceProfiler(FakeBridge({"dumpsys battery": (0, "  temperature: -50\n  level: 40", "")})).profile_device("S1")
        self.assertEqual(p.battery_temp_c, -5.0)

    def test_storage_wear_retried_with_root(self):
        from core.storage_benchmark import StorageBenchmark

        class RootPhone(FakeBridge):
            def shell(self, command, serial=None, timeout=30, root=False, use_socket=True):
                if "life_time" in command or "pre_eol_info" in command:
                    return (0, "0x02 0x03" if "life_time" in command else "0x01", "") if root else (1, "", "Permission denied")
                return super().shell(command, serial, timeout, root, use_socket)
        rep = StorageBenchmark(RootPhone(root=True)).diagnose_storage_health("S1")
        self.assertEqual(rep.status, "PASS")
        self.assertTrue(any("(su)" in e.source for e in rep.evidence))


class QuotingAndPathTests(unittest.TestCase):
    def test_send_text_is_quoted_for_the_device_shell(self):
        import shlex
        from core.rescue_center import RescueCenter
        b = FakeBridge()
        RescueCenter(b).send_text('12"; reboot; echo $HOME')
        cmd = b.commands[-1]
        self.assertTrue(cmd.startswith("input text "))
        # single quotes: $HOME, backticks and ; stay literal on the phone (inside double quotes $ still expands)
        self.assertEqual(cmd, "input text " + shlex.quote('12";%sreboot;%secho%s$HOME'))

    def test_browser_url_is_quoted(self):
        import shlex
        from core.provisioning_manager import ProvisioningManager
        b = FakeBridge()
        ProvisioningManager(b).launch_browser_url("example.com/a'b;reboot", "S1")
        self.assertEqual(shlex.split(b.commands[-1])[-1], "https://example.com/a'b;reboot")

    def test_efs_backup_folder_safe_for_wireless_serial(self):
        from core.board_diag import MotherboardDiagnostics
        with tempfile.TemporaryDirectory() as d, patch.dict(os.environ, {"USERPROFILE": d}):
            diag = MotherboardDiagnostics(FakeBridge(root=True), DatabaseManager(os.path.join(d, "t.db")))
            res = diag.backup_efs_nvram_partitions("192.168.1.5:5555")
            DatabaseManager.reset_instance()
        self.assertNotIn(":", os.path.basename(res.backup_folder))


class SigningDbEvidenceTests(unittest.TestCase):
    def _key(self, d, passphrase=None):
        from core import report_signing as rs
        env = {rs.PASSPHRASE_ENV: passphrase} if passphrase else {}
        with patch.dict(os.environ, env, clear=False):
            if not passphrase:
                os.environ.pop(rs.PASSPHRASE_ENV, None)
            priv, pub, _ = rs.generate_keypair(os.path.join(d, "keys"), project_root=os.path.join(d, "proj"))
        return priv, pub

    def test_passphrase_errors_say_what_is_wrong(self):
        from core import report_signing as rs
        with tempfile.TemporaryDirectory() as d:
            plain, _ = self._key(d)
            with patch.dict(os.environ, {rs.PASSPHRASE_ENV: "secret"}):
                with self.assertRaises(rs.SigningError) as cm:
                    rs.load_private_key(plain, project_root=os.path.join(d, "proj"))
            self.assertEqual(cm.exception.msg.key, "sign.err.passphrase_not_needed")
        with tempfile.TemporaryDirectory() as d:
            enc, _ = self._key(d, passphrase="right")
            with patch.dict(os.environ, {rs.PASSPHRASE_ENV: "wrong"}):
                with self.assertRaises(rs.SigningError) as cm:
                    rs.load_private_key(enc, project_root=os.path.join(d, "proj"))
            self.assertEqual(cm.exception.msg.key, "sign.err.passphrase_wrong")

    def test_null_signature_is_rejected_not_a_crash(self):
        import json as _json
        from core import report_signing as rs
        with tempfile.TemporaryDirectory() as d:
            priv, pub = self._key(d)
            report = os.path.join(d, "r.html")
            with open(report, "w", encoding="utf-8") as f:
                f.write("x")
            with patch.dict(os.environ, {}, clear=False):
                os.environ.pop(rs.PASSPHRASE_ENV, None)
                sig = rs.sign_file(report, rs.load_private_key(priv, project_root=os.path.join(d, "proj")))
            with open(sig, encoding="utf-8") as f:
                rec = _json.load(f)
            rec["signature"] = None
            with open(sig, "w", encoding="utf-8") as f:
                _json.dump(rec, f)
            ok, msg = rs.verify_file(report, pub)
        self.assertFalse(ok)
        self.assertEqual(msg.key, "sign.verify.bad_format")

    def test_ticket_failure_and_notes_on_null(self):
        with tempfile.TemporaryDirectory() as d:
            db = DatabaseManager(os.path.join(d, "t.db"))
            self.assertEqual(db.create_repair_ticket("S1", ticket_id="T1"), "T1")
            self.assertIsNone(db.create_repair_ticket("S1", ticket_id="T1"))   # duplicate id: not saved
            conn = db._get_connection()
            conn.execute("UPDATE repair_tickets SET notes = NULL WHERE ticket_id = 'T1'")
            conn.commit()
            conn.close()
            self.assertTrue(db.update_repair_ticket_status("T1", "DONE", notes="screen replaced"))
            self.assertEqual(db.get_repair_ticket("T1")["notes"], "screen replaced")
            db.close()
            DatabaseManager.reset_instance()

    def test_evidence_shows_exit_zero(self):
        from core.result import Evidence
        from ui.ui_helpers import format_evidence_for_display
        txt = format_evidence_for_display([Evidence(source="cat x", raw_excerpt="1", exit_code=0)], lang="en")
        self.assertIn("[exit 0]", txt)
        self.assertNotIn("[exit", format_evidence_for_display([{"source": "s", "raw_excerpt": "1"}], lang="en"))


class _EFSPhone(FakeBridge):
    """Rooted phone for the (older) board_diag EFS backup path: dd + run_adb pull,
    with a real /sys partition size so a short dump can be caught."""

    def __init__(self, dd_ok=True, pull_ok=True, pull_bytes=1024, sectors=2):
        routes = {
            "magisk -v": (0, "Magisk v26", ""),
            "ls -d /dev/block/bootdevice/by-name": (0, "/dev/block/bootdevice/by-name", ""),
            "ls -la /dev/block/bootdevice/by-name": (
                0,
                "lrwxrwxrwx 1 root root 16 2026 efs -> /dev/block/mmcblk0p1\n"
                "lrwxrwxrwx 1 root root 16 2026 modemst1 -> /dev/block/mmcblk0p2", ""),
            "readlink -f /dev/block/bootdevice/by-name/efs": (0, "/dev/block/mmcblk0p1", ""),
            "readlink -f /dev/block/bootdevice/by-name/modemst1": (0, "/dev/block/mmcblk0p2", ""),
            "cat /sys/class/block/mmcblk0p1/size": (0, str(sectors), ""),
            "cat /sys/class/block/mmcblk0p2/size": (0, str(sectors), ""),
            "dd if=": (0 if dd_ok else 1, "", ""),
        }
        super().__init__(routes, default=(0, "", ""), root=True)
        self.pull_ok, self.pull_bytes = pull_ok, pull_bytes

    def run_adb(self, args, serial=None, timeout=30):
        if args and args[0] == "pull":
            if self.pull_ok:
                with open(args[2], "wb") as f:
                    f.write(b"\x00" * self.pull_bytes)
                return (0, "", "")
            return (1, "", "error")
        return (1, "", "")


class EFSBackupVerificationTests(unittest.TestCase):
    """The board_diag EFS backup must never report success for a short/failed dump."""

    def _run(self, phone):
        from core.board_diag import MotherboardDiagnostics
        with tempfile.TemporaryDirectory() as d:
            res = MotherboardDiagnostics(phone, db=None).backup_efs_nvram_partitions("S1", dest_dir=d)
            files = sorted(f for f in os.listdir(d) if f.endswith(".img"))
            return res, files

    def test_complete_dump_succeeds_and_is_verified(self):
        res, files = self._run(_EFSPhone())
        self.assertTrue(res.success)
        self.assertEqual(sorted(res.backed_up_partitions), ["efs", "modemst1"])
        self.assertEqual(res.failed_partitions, [])
        self.assertEqual(files, ["efs.img", "modemst1.img"])

    def test_short_dump_is_rejected_and_file_removed(self):
        res, files = self._run(_EFSPhone(pull_bytes=512))   # 512 != real 1024
        self.assertFalse(res.success)
        self.assertEqual(res.backed_up_partitions, [])
        self.assertEqual(sorted(res.failed_partitions), ["efs", "modemst1"])
        self.assertEqual(files, [])   # the truncated copies were deleted, not left to be trusted

    def test_failed_dd_is_not_counted(self):
        res, _ = self._run(_EFSPhone(dd_ok=False))
        self.assertFalse(res.success)
        self.assertEqual(res.backed_up_partitions, [])
        self.assertEqual(sorted(res.failed_partitions), ["efs", "modemst1"])

    def test_failed_pull_is_not_counted(self):
        res, _ = self._run(_EFSPhone(pull_ok=False))
        self.assertFalse(res.success)
        self.assertEqual(res.backed_up_partitions, [])
        self.assertEqual(sorted(res.failed_partitions), ["efs", "modemst1"])


class CliDoesNotLoadGuiTests(unittest.TestCase):
    """--cli must not drag in tkinter/customtkinter via the ui package initializer."""

    def test_cli_imports_stay_gui_free(self):
        import subprocess, sys
        code = (
            "import sys\n"
            "from ui.i18n import t, set_current_language\n"
            "from ui import ui_helpers, report_exporter, verifier_page\n"
            "assert 'customtkinter' not in sys.modules, 'customtkinter loaded'\n"
            "assert 'ui.main_window' not in sys.modules, 'main_window loaded'\n"
            "import ui\n"
            "assert ui.DiagnosticReportExporter is not None\n"
        )
        # A fresh interpreter, so modules imported by the test runner don't mask the result.
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                           cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        self.assertEqual(r.returncode, 0, r.stderr)
