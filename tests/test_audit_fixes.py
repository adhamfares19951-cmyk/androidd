"""Behaviour tests for the issues confirmed in the external audit (each failed before its fix)."""
import os
import tempfile
import unittest
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
