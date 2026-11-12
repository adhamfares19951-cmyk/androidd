import os
import re
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List, Tuple
from core.adb_bridge import ADBBridge

from core.result import Msg, M
from app_paths import desktop_dir, safe_name

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
    error_message: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class PartitionSetResult:
    """Result of backing up several partitions (critical set or all)."""
    success: bool = False                       # every attempted partition was copied, and at least one
    dumped_partitions: List[str] = field(default_factory=list)
    failed_partitions: List[str] = field(default_factory=list)
    skipped_partitions: List[str] = field(default_factory=list)   # too large or user data
    backup_directory: str = ""
    diagnosis: Any = None
    results: List[BackupResult] = field(default_factory=list)

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

    # Never copied by "all partitions": user data, and anything larger than this, because the image is
    # first written to /data/local/tmp on the phone itself and could fill its storage.
    SKIP_ALWAYS = {"userdata", "super", "cache", "metadata"}
    MAX_IMAGE_BYTES = 1024 * 1024 * 1024

    def __init__(self, bridge: Optional[ADBBridge] = None):
        self.bridge = bridge or ADBBridge()

    @property
    def backup_base_dir(self) -> str:
        return os.path.join(desktop_dir(), "Android_Doctor_Backups")

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

    def backup_partition(self, serial: str, partition_name: str, output_dir: Optional[str] = None,
                         target: Optional[PartitionInfo] = None) -> BackupResult:
        """
        Copies one partition image to the PC: dd on the phone to /data/local/tmp, chmod so adb can read
        a root-written file, pull, and always remove the phone-side copy (also when something fails).
        A copy is accepted only if a non-empty file arrived on the PC.
        """
        start_time = time.time()
        res = BackupResult(serial=serial, partition_name=partition_name, local_file_path="")

        if not output_dir:
            output_dir = os.path.join(self.backup_base_dir, safe_name(serial))
        os.makedirs(output_dir, exist_ok=True)
        timestamp = int(time.time())
        local_dest = os.path.join(output_dir, f"{safe_name(partition_name)}_{safe_name(serial)}_{timestamp}.img")
        res.local_file_path = local_dest

        if target is None:
            target = next((p for p in self.scan_partition_table(serial) if p.name.lower() == partition_name.lower()), None)
        if not target:
            res.error_message = M("part.not_found", name=partition_name)
            return res

        remote_tmp = f"/data/local/tmp/adp_{safe_name(partition_name)}_{timestamp}.img"
        timeout = max(45, int((target.size_bytes or 0) / (8 * 1024 * 1024)) + 30)  # ~8 MB/s worst case
        try:
            dd_cmd = f"dd if={target.block_path} of={remote_tmp} bs=4096 conv=sync,noerror 2>&1"
            code, out, err = self.bridge.shell(dd_cmd, serial=serial, timeout=timeout)
            if code != 0 or "permission denied" in (out + err).lower():
                code, out, err = self.bridge.shell(f"{dd_cmd} && chmod 644 {remote_tmp}", serial=serial,
                                                   timeout=timeout, root=True)
            if code != 0 and "permission denied" in (out + err).lower():
                res.error_message = M("part.needs_root")
                return res

            pull_success, pull_msg = self.bridge.pull(remote_tmp, local_dest, serial=serial, timeout=max(60, timeout))
            if pull_success and os.path.exists(local_dest) and os.path.getsize(local_dest) > 0:
                res.size_bytes = os.path.getsize(local_dest)
                res.duration_sec = round(time.time() - start_time, 2)
                res.success = True
                logger.info(f"Partition {partition_name} backed up ({res.size_bytes} bytes) -> {local_dest}")
            else:
                res.error_message = M("part.pull_failed", output=pull_msg or "empty file")
                if os.path.exists(local_dest) and os.path.getsize(local_dest) == 0:
                    os.remove(local_dest)
        finally:
            # never leave a raw image of a sensitive partition on the phone
            self.bridge.shell(f"rm -f {remote_tmp}", serial=serial, timeout=10, root=True)
        return res

    def backup_all_critical(self, serial: str) -> List[BackupResult]:
        """Dumps all detected critical partitions (EFS, Persist, Modem) sequentially."""
        return self.backup_critical_partitions(serial).results

    def _backup_set(self, serial: str, partitions: List[PartitionInfo], skipped: List[str],
                    progress_callback=None) -> PartitionSetResult:
        out = PartitionSetResult(skipped_partitions=list(skipped))
        out.backup_directory = os.path.join(self.backup_base_dir, f"{safe_name(serial)}_{int(time.time())}")
        if not partitions:
            out.diagnosis = M("part.none_found")
            return out
        for i, p in enumerate(partitions, start=1):
            if progress_callback:
                progress_callback(f"[{i}/{len(partitions)}] {p.name}")
            r = self.backup_partition(serial, p.name, output_dir=out.backup_directory, target=p)
            out.results.append(r)
            (out.dumped_partitions if r.success else out.failed_partitions).append(p.name)
        out.success = bool(out.dumped_partitions) and not out.failed_partitions
        out.diagnosis = M("part.set_summary", done=len(out.dumped_partitions), total=len(partitions),
                          failed=", ".join(out.failed_partitions) or "-", skipped=", ".join(out.skipped_partitions) or "-")
        return out

    def backup_critical_partitions(self, serial: str, progress_callback=None) -> PartitionSetResult:
        """EFS / modem / persist / boot... (CRITICAL_PARTITIONS) into one folder."""
        parts = [p for p in self.scan_partition_table(serial) if p.is_critical]
        return self._backup_set(serial, parts, [], progress_callback)

    def backup_all_partitions(self, serial: str, progress_callback=None) -> PartitionSetResult:
        """Every listed partition except user data and images over MAX_IMAGE_BYTES (reported as skipped)."""
        parts, skipped = [], []
        for p in self.scan_partition_table(serial):
            if p.name.lower() in self.SKIP_ALWAYS or (p.size_bytes or 0) > self.MAX_IMAGE_BYTES:
                skipped.append(p.name)
            else:
                parts.append(p)
        return self._backup_set(serial, parts, skipped, progress_callback)
