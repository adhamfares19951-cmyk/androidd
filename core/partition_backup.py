import os
import re
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List, Tuple
from core.adb_bridge import ADBBridge

logger = logging.getLogger("PartitionBackup")


@dataclass
class PartitionInfo:
    name: str
    block_path: str
    size_bytes: int = 0
    size_mb: float = 0.0
    category: str = "Standard"  # Network_IMEI, Calibration, Bootloader, Firmware, UserData
    is_critical: bool = False

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BackupResult:
    serial: str
    partition_name: str
    local_file_path: str
    size_bytes: int = 0
    duration_sec: float = 0.0
    success: bool = False
    error_message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class PartitionBackupManager:
    """
    Manages partition table discovery, critical NVRAM/EFS/Persist detection,
    and safe sector-by-sector extraction to the technician's workstation.
    """
    CRITICAL_PARTITIONS = {
        # Radio & IMEI (NVRAM / EFS)
        "efs": "Network_IMEI",
        "modem": "Network_IMEI",
        "modemst1": "Network_IMEI",
        "modemst2": "Network_IMEI",
        "fsg": "Network_IMEI",
        "fsc": "Network_IMEI",
        "nvram": "Network_IMEI",
        "nvdata": "Network_IMEI",
        "protect1": "Network_IMEI",
        "protect2": "Network_IMEI",
        "sec": "Network_IMEI",
        
        # Sensor & Hardware Calibration
        "persist": "Calibration",
        "devinfo": "Calibration",
        "param": "Calibration",
        "proinfo": "Calibration",
        
        # Boot & System
        "boot": "Bootloader",
        "recovery": "Bootloader",
        "dtbo": "Firmware",
        "vbmeta": "Firmware",
        "misc": "Firmware"
    }

    def __init__(self, bridge: Optional[ADBBridge] = None):
        self.bridge = bridge or ADBBridge()

    def scan_partition_table(self, serial: str) -> List[PartitionInfo]:
        """
        Discovers all storage block partitions on the device.
        Checks multi-vendor sysfs/devfs paths.
        """
        logger.info(f"Scanning partition table for {serial}...")
        partitions: List[PartitionInfo] = []

        # Find by-name directory
        paths = [
            "/dev/block/bootdevice/by-name",
            "/dev/block/by-name",
            "/dev/block/platform/*/by-name",
            "/dev/block/platform/*/*/by-name"
        ]

        found_dir = ""
        for p in paths:
            code, out, _ = self.bridge.shell(f"ls -d {p} 2>/dev/null", serial=serial, timeout=4)
            if code == 0 and out.strip():
                found_dir = out.strip().splitlines()[0]
                break

        if not found_dir:
            # Fallback to /proc/partitions
            return self._scan_from_proc_partitions(serial)

        # List all symlinks in by-name dir
        code_ls, out_ls, _ = self.bridge.shell(f"ls -l {found_dir} 2>/dev/null", serial=serial, timeout=8)
        if code_ls != 0 or not out_ls:
            return self._scan_from_proc_partitions(serial)

        for line in out_ls.splitlines():
            # Example format: lrwxrwxrwx 1 root root 16 1970-01-01 00:00 boot -> /dev/block/mmcblk0p21
            if "->" in line:
                parts = line.split("->")
                left = parts[0].strip().split()
                if not left:
                    continue
                part_name = left[-1]
                target_block = parts[1].strip()

                if not target_block.startswith("/dev/"):
                    target_block = os.path.normpath(os.path.join(found_dir, target_block)).replace("\\", "/")

                # Categorize partition
                p_lower = part_name.lower()
                cat = self.CRITICAL_PARTITIONS.get(p_lower, "Standard")
                is_crit = (p_lower in self.CRITICAL_PARTITIONS)

                # Query partition size
                size_b = self._get_block_size(serial, target_block)

                partitions.append(PartitionInfo(
                    name=part_name,
                    block_path=target_block,
                    size_bytes=size_b,
                    size_mb=round(size_b / (1024 * 1024), 2) if size_b else 0.0,
                    category=cat,
                    is_critical=is_crit
                ))

        # Sort so critical partitions appear first
        partitions.sort(key=lambda x: (not x.is_critical, x.category, x.name))
        return partitions

    def _get_block_size(self, serial: str, block_path: str) -> int:
        block_basename = os.path.basename(block_path)
        # Check /sys/class/block/<name>/size (in 512-byte sectors)
        code, out, _ = self.bridge.shell(f"cat /sys/class/block/{block_basename}/size 2>/dev/null", serial=serial, timeout=2)
        if code == 0 and out.strip().isdigit():
            return int(out.strip()) * 512
        return 0

    def _scan_from_proc_partitions(self, serial: str) -> List[PartitionInfo]:
        partitions = []
        code, out, _ = self.bridge.shell("cat /proc/partitions", serial=serial, timeout=4)
        if code == 0 and out:
            lines = out.strip().splitlines()
            for line in lines[2:]:
                parts = line.split()
                if len(parts) >= 4:
                    name = parts[3]
                    blocks = int(parts[2]) if parts[2].isdigit() else 0
                    size_b = blocks * 1024
                    cat = self.CRITICAL_PARTITIONS.get(name.lower(), "Standard")
                    is_crit = (name.lower() in self.CRITICAL_PARTITIONS)
                    partitions.append(PartitionInfo(
                        name=name,
                        block_path=f"/dev/block/{name}",
                        size_bytes=size_b,
                        size_mb=round(size_b / (1024 * 1024), 2),
                        category=cat,
                        is_critical=is_crit
                    ))
        return partitions

    def backup_partition(self, serial: str, partition_name: str, output_dir: Optional[str] = None) -> BackupResult:
        """
        Dumps a partition image file to the technician's PC desktop backups directory.
        Uses dd on device to /data/local/tmp/<part>.img, pulls to PC, then removes the temp file.
        """
        start_time = time.time()
        res = BackupResult(serial=serial, partition_name=partition_name, local_file_path="")

        if not output_dir:
            user_profile = os.environ.get("USERPROFILE", "C:\\Users\\Default")
            output_dir = os.path.join(user_profile, "Desktop", "Android_Doctor_Backups", serial)

        os.makedirs(output_dir, exist_ok=True)
        timestamp = int(time.time())
        local_filename = f"{partition_name}_{serial}_{timestamp}.img"
        local_dest = os.path.join(output_dir, local_filename)
        res.local_file_path = local_dest

        # 1. Locate partition block path
        partitions = self.scan_partition_table(serial)
        target = next((p for p in partitions if p.name.lower() == partition_name.lower()), None)
        if not target:
            res.success = False
            res.error_message = f"Partition '{partition_name}' not found on device."
            return res

        remote_tmp = f"/data/local/tmp/{partition_name}_{timestamp}.img"

        # 2. Dump partition via dd (trying with root/su fallback)
        dd_cmd = f"dd if={target.block_path} of={remote_tmp} bs=4096 conv=sync,noerror 2>&1"
        code, out, err = self.bridge.shell(dd_cmd, serial=serial, timeout=45)

        if code != 0 or "permission denied" in (out + err).lower():
            # Try su -c if rooted
            code, out, err = self.bridge.shell(f"su -c '{dd_cmd}'", serial=serial, timeout=45)

        if code != 0 and "permission denied" in (out + err).lower():
            res.success = False
            res.error_message = "Permission Denied: Reading raw block partitions requires Root or Recovery Shell access."
            return res

        # 3. Pull image file from device to PC
        pull_success, pull_msg = self.bridge.pull(remote_tmp, local_dest, serial=serial, timeout=60)

        # 4. Remove remote temp file
        self.bridge.shell(f"rm -f {remote_tmp}", serial=serial, timeout=5)

        if pull_success and os.path.exists(local_dest):
            file_size = os.path.getsize(local_dest)
            res.size_bytes = file_size
            res.duration_sec = round(time.time() - start_time, 2)
            res.success = True
            logger.info(f"Partition {partition_name} backed up successfully ({file_size} bytes) -> {local_dest}")
        else:
            res.success = False
            res.error_message = f"Failed to pull image to PC: {pull_msg}"

        return res

    def backup_all_critical(self, serial: str) -> List[BackupResult]:
        """
        Dumps all detected critical partitions (EFS, Persist, Modem) sequentially.
        """
        partitions = self.scan_partition_table(serial)
        critical_parts = [p for p in partitions if p.is_critical]
        results = []
        for p in critical_parts:
            r = self.backup_partition(serial, p.name)
            results.append(r)
        return results
