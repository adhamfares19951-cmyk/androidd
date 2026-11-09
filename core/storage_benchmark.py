import re
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List, Tuple
from core.adb_bridge import ADBBridge
from core.result import Evidence, Status, CheckResult, status_from_read

logger = logging.getLogger("StorageBenchmark")


@dataclass
class StorageHealthReport:
    storage_type: str = "Unknown"  # eMMC, UFS, NVMe
    storage_name: str = "Unknown"
    storage_capacity_gb: float = 0.0
    
    # Wear Level & Life Time
    life_time_type_a: str = "Unknown"  # SLC wear (e.g. 0-10%, 10-20%)
    life_time_type_b: str = "Unknown"  # MLC/TLC wear
    pre_eol_status: str = "Normal"     # Normal, Warning (80%+ consumed), Urgent (90%+)
    estimated_wear_pct: int = 0
    is_failing_or_degraded: bool = False
    status: str = "UNKNOWN"            # "PASS" | "FAIL" | "UNKNOWN" | "NEEDS_ROOT"
    
    # Speed Benchmark (MB/s)
    read_speed_mbps: float = 0.0
    write_speed_mbps: float = 0.0
    benchmark_duration_sec: float = 0.0
    benchmark_status: str = "Not Run"
    
    diagnosis: str = "Storage memory health not evaluated."
    raw_logs: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    @property
    def write_speed_mb_s(self) -> float:
        return self.write_speed_mbps

    @property
    def read_speed_mb_s(self) -> float:
        return self.read_speed_mbps

    @property
    def pre_eol_info(self) -> str:
        return self.pre_eol_status

    @property
    def health_verdict(self) -> str:
        return self.status

    @property
    def has_real_data(self) -> bool:
        return self.status in ("PASS", "FAIL")

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["write_speed_mb_s"] = self.write_speed_mbps
        d["read_speed_mb_s"] = self.read_speed_mbps
        d["pre_eol_info"] = self.pre_eol_status
        d["health_verdict"] = self.status
        d["has_real_data"] = self.has_real_data
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


class StorageBenchmark:
    """
    Evaluates eMMC and UFS NAND flash health, life-time wear indicators,
    and runs sequential Read/Write I/O benchmarks on Android devices.
    """
    def __init__(self, bridge: Optional[ADBBridge] = None):
        self.bridge = bridge or ADBBridge()

    def _parse_life_time(self, raw_hex: str) -> Tuple[str, int]:
        """
        Parses JEDEC eMMC/UFS life_time indicators:
        0x01: 0% - 10% device life time used
        0x02: 10% - 20% device life time used
        ...
        0x0A: 90% - 100% device life time used
        0x0B: Exceeded its maximum estimated device life time
        """
        val_str = raw_hex.strip().lower()
        mapping = {
            "0x01": ("0% - 10% (Excellent)", 5),
            "0x02": ("10% - 20% (Normal)", 15),
            "0x03": ("20% - 30% (Normal)", 25),
            "0x04": ("30% - 40% (Good)", 35),
            "0x05": ("40% - 50% (Good)", 45),
            "0x06": ("50% - 60% (Moderate Wear)", 55),
            "0x07": ("60% - 70% (Significant Wear)", 65),
            "0x08": ("70% - 80% (High Wear)", 75),
            "0x09": ("80% - 90% (CRITICAL: Near EOL)", 85),
            "0x0a": ("90% - 100% (URGENT: Replacement Needed)", 95),
            "0x0b": ("EXCEEDED MAX LIFE TIME (Failing)", 100),
        }
        for k, v in mapping.items():
            if k in val_str or val_str.endswith(k.replace("0x", "")):
                return v
        return (f"Raw {val_str}", 0)

    def _parse_pre_eol(self, raw_hex: str) -> Tuple[str, bool]:
        """
        JEDEC Pre-EOL Info:
        0x01: Normal
        0x02: Warning (consumed 80% of reserved blocks)
        0x03: Urgent (consumed 90%+ of reserved blocks)
        """
        val = raw_hex.strip().lower()
        if "0x01" in val or val.endswith("1"):
            return ("Normal (Healthy reserved blocks)", False)
        elif "0x02" in val or val.endswith("2"):
            return ("WARNING: 80% of reserved blocks consumed", True)
        elif "0x03" in val or val.endswith("3"):
            return ("CRITICAL URGENT: 90%+ reserved blocks consumed (Immediate Failure Imminent)", True)
        return ("Unknown / Not Reported", False)

    def diagnose_storage_health(self, serial: str) -> StorageHealthReport:
        logger.info(f"Auditing NAND flash memory health for {serial}...")
        report = StorageHealthReport()
        logs = []

        # 1. Detect Storage Type (UFS vs eMMC)
        code_ufs, out_ufs, _ = self.bridge.shell("ls -d /sys/bus/scsi/devices/host* /sys/class/scsi_host/host* 2>/dev/null", serial=serial, timeout=4)
        code_emmc, out_emmc, _ = self.bridge.shell("ls -d /sys/block/mmcblk0 2>/dev/null", serial=serial, timeout=4)

        if code_ufs == 0 and out_ufs.strip():
            report.storage_type = "UFS (Universal Flash Storage)"
        elif code_emmc == 0 and out_emmc.strip():
            report.storage_type = "eMMC (Embedded MultiMediaCard)"
        else:
            report.storage_type = "NAND Flash / UFS"

        # 2. Storage Chip Name & Capacity
        code_name, out_name, _ = self.bridge.shell(
            "cat /sys/block/mmcblk0/device/name /sys/block/sda/device/model 2>/dev/null",
            serial=serial,
            timeout=4
        )
        if code_name == 0 and out_name.strip():
            report.storage_name = out_name.strip().splitlines()[0]

        code_size, out_size, _ = self.bridge.shell(
            "cat /sys/block/mmcblk0/size /sys/block/sda/size 2>/dev/null",
            serial=serial,
            timeout=4
        )
        if code_size == 0 and out_size.strip():
            try:
                sectors = int(out_size.strip().splitlines()[0])
                report.storage_capacity_gb = round((sectors * 512) / (1024**3), 2)
            except ValueError:
                pass

        # 3. Read eMMC Wear Out indicators (life_time & pre_eol_info)
        code_life, out_life, err_life = self.bridge.shell(
            "cat /sys/block/mmcblk0/device/life_time",
            serial=serial,
            timeout=4
        )
        life_st = status_from_read(out_life, err_life, code_life)
        if code_life == 0 and out_life.strip():
            parts = out_life.strip().split()
            if len(parts) >= 2:
                desc_a, pct_a = self._parse_life_time(parts[0])
                desc_b, pct_b = self._parse_life_time(parts[1])
                report.life_time_type_a = desc_a
                report.life_time_type_b = desc_b
                report.estimated_wear_pct = max(pct_a, pct_b)
                logs.append(f"eMMC Life Time Raw: {out_life.strip()} -> TypeA: {desc_a}, TypeB: {desc_b}")
            elif len(parts) == 1:
                desc_a, pct_a = self._parse_life_time(parts[0])
                report.life_time_type_a = desc_a
                report.estimated_wear_pct = pct_a

        code_eol, out_eol, err_eol = self.bridge.shell(
            "cat /sys/block/mmcblk0/device/pre_eol_info",
            serial=serial,
            timeout=4
        )
        eol_st = status_from_read(out_eol, err_eol, code_eol)
        if code_eol == 0 and out_eol.strip():
            eol_desc, is_failing = self._parse_pre_eol(out_eol)
            report.pre_eol_status = eol_desc
            if is_failing:
                report.is_failing_or_degraded = True
            logs.append(f"Pre-EOL Info: {out_eol.strip()} -> {eol_desc}")

        # 4. Read UFS Health Descriptors if available
        code_ufs_h, out_ufs_h, err_ufs = self.bridge.shell(
            "cat /sys/kernel/debug/ufshcd*/dump_device_desc /sys/class/scsi_device/*/device/dump_device_desc",
            serial=serial,
            timeout=4
        )
        ufs_st = status_from_read(out_ufs_h, err_ufs, code_ufs_h)
        if code_ufs_h == 0 and out_ufs_h.strip():
            logs.append("UFS Device Descriptor retrieved.")
            # Search for bDeviceLifeTimeEstA and B in UFS
            m_a = re.search(r"bDeviceLifeTimeEstA\s*=\s*(0x[0-9a-fA-F]+)", out_ufs_h)
            m_b = re.search(r"bDeviceLifeTimeEstB\s*=\s*(0x[0-9a-fA-F]+)", out_ufs_h)
            if m_a and m_b:
                desc_a, pct_a = self._parse_life_time(m_a.group(1))
                desc_b, pct_b = self._parse_life_time(m_b.group(1))
                report.life_time_type_a = desc_a
                report.life_time_type_b = desc_b
                report.estimated_wear_pct = max(pct_a, pct_b)

        # 5. Formulate Diagnostic Verdict based on actual reads
        if report.estimated_wear_pct >= 85 or report.is_failing_or_degraded:
            report.is_failing_or_degraded = True
            report.status = "FAIL"
            report.diagnosis = f"FAIL: Flash memory heavily degraded ({report.estimated_wear_pct}% wear, EOL: {report.pre_eol_status}). High risk of unrecoverable bootloop or storage I/O collapse!"
        elif report.estimated_wear_pct >= 60:
            report.status = "PASS"
            report.diagnosis = f"PASS (Warning): Flash memory exhibits moderate wear ({report.estimated_wear_pct}% consumed). Normal aging, but avoid aggressive flashing."
        elif report.estimated_wear_pct > 0:
            report.status = "PASS"
            report.diagnosis = f"PASS: Flash memory is healthy and in good operating condition ({report.estimated_wear_pct}% wear level, EOL: {report.pre_eol_status})."
        else:
            if life_st == Status.NEEDS_ROOT or eol_st == Status.NEEDS_ROOT or ufs_st == Status.NEEDS_ROOT:
                report.status = "NEEDS_ROOT"
                report.diagnosis = f"NEEDS_ROOT: JEDEC wear descriptors restricted by SELinux on {report.storage_type}. Root required to read raw block descriptor."
            else:
                report.status = "UNKNOWN"
                report.diagnosis = f"UNKNOWN: JEDEC wear descriptors unexposed by OEM driver for {report.storage_type} ({report.storage_name})."

        report.raw_logs = logs
        report.evidence.append(Evidence(
            source="NAND Storage Controller",
            raw_excerpt=f"type={report.storage_type}, name={report.storage_name}, capacity={report.storage_capacity_gb}GB, wear_pct={report.estimated_wear_pct}, EOL={report.pre_eol_status}",
            exit_code=0
        ))
        for log in logs:
            report.evidence.append(Evidence(source="JEDEC Life Time Log", raw_excerpt=log, exit_code=0))
        if life_st == Status.NEEDS_ROOT:
            report.evidence.append(Evidence(source="/sys/block/mmcblk0/device/life_time", raw_excerpt=(err_life or out_life or "Permission denied").strip(), exit_code=code_life))
        return report
        return report

    def run_speed_benchmark(self, serial: str, test_size_mb: int = 40) -> StorageHealthReport:
        """
        Executes sequential Read & Write speed benchmark via ADB shell using 'dd'.
        Creates a temporary test file in /data/local/tmp/ and measures true throughput in MB/s.
        """
        logger.info(f"Running true I/O speed benchmark on {serial} (Size: {test_size_mb}MB)...")
        report = self.diagnose_storage_health(serial)
        start_time = time.time()
        
        tmp_file = "/data/local/tmp/speed_benchmark.dat"

        # Step 1: Write Speed Test
        # Write test_size_mb from /dev/zero to tmp_file with fdatasync
        write_cmd = f"dd if=/dev/zero of={tmp_file} bs=1M count={test_size_mb} conv=fdatasync 2>&1"
        code_w, out_w, _ = self.bridge.shell(write_cmd, serial=serial, timeout=25)
        
        write_speed = 0.0
        if code_w == 0 and out_w:
            # Match e.g. "41943040 bytes (40 MB, 38 MiB) copied, 0.432 s, 97.1 MB/s" or "97.1 MB/s"
            m = re.search(r"([\d\.]+)\s*(MB/s|GB/s|kB/s|bytes/sec)", out_w)
            if m:
                val = float(m.group(1))
                unit = m.group(2)
                if unit == "GB/s":
                    write_speed = val * 1024
                elif unit == "MB/s":
                    write_speed = val
                elif unit in ["kB/s", "KB/s"]:
                    write_speed = val / 1024
                else:
                    write_speed = val / (1024 * 1024)

        # Step 2: Read Speed Test
        # Read from tmp_file to /dev/null
        read_cmd = f"dd if={tmp_file} of=/dev/null bs=1M count={test_size_mb} 2>&1"
        code_r, out_r, _ = self.bridge.shell(read_cmd, serial=serial, timeout=25)

        read_speed = 0.0
        if code_r == 0 and out_r:
            m = re.search(r"([\d\.]+)\s*(MB/s|GB/s|kB/s|bytes/sec)", out_r)
            if m:
                val = float(m.group(1))
                unit = m.group(2)
                if unit == "GB/s":
                    read_speed = val * 1024
                elif unit == "MB/s":
                    read_speed = val
                elif unit in ["kB/s", "KB/s"]:
                    read_speed = val / 1024
                else:
                    read_speed = val / (1024 * 1024)

        # Step 3: Clean up temporary benchmark file
        self.bridge.shell(f"rm -f {tmp_file}", serial=serial, timeout=5)

        total_duration = round(time.time() - start_time, 2)
        report.read_speed_mbps = round(read_speed, 2)
        report.write_speed_mbps = round(write_speed, 2)
        report.benchmark_duration_sec = total_duration
        report.benchmark_status = "Completed"

        # Evaluate performance tiers
        if read_speed > 350:
            tier = "Ultra Fast (High-End UFS 3.0 / 3.1)"
        elif read_speed > 180:
            tier = "Fast (Standard UFS 2.1 / 2.2)"
        elif read_speed > 80:
            tier = "Normal (Standard eMMC 5.1)"
        elif read_speed > 0:
            tier = "Slow / Degraded I/O (Bottleneck or Worn Flash)"
        else:
            tier = "Benchmark Inconclusive"

        report.diagnosis += f" | Benchmark: Read {report.read_speed_mbps} MB/s, Write {report.write_speed_mbps} MB/s [{tier}]"
        return report
