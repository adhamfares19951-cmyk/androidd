import re
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List

from core.adb_bridge import ADBBridge
from core.result import Evidence, Status
from core.readers import ReadResult, run_read, read_node, failure_status, parse_int
from core import jedec

logger = logging.getLogger("StorageBenchmark")


def parse_dd_speed_mb_s(dd_output: str) -> Optional[float]:
    """
    Parse dd's summary line. Prefers "<bytes> bytes ... copied, <secs> s" (toybox and GNU),
    falls back to a printed rate ("97.1 MB/s", "128 M/s"). Returns MiB/s or None.
    """
    text = dd_output or ""
    m = re.search(r"(\d+)\s+bytes.*?copied,\s*([\d.]+)\s*s", text)
    if m:
        nbytes, secs = int(m.group(1)), float(m.group(2))
        if nbytes > 0 and secs > 0:
            return round(nbytes / secs / (1024 * 1024), 1)
    m = re.search(r"([\d.]+)\s*([kKMG])i?B?/s", text)
    if m:
        val = float(m.group(1))
        factor = {"k": 1 / 1024, "K": 1 / 1024, "M": 1.0, "G": 1024.0}[m.group(2)]
        return round(val * factor, 1)
    return None


@dataclass
class StorageHealthReport:
    check_id: str = "storage.health"
    storage_type: str = "UNKNOWN"
    storage_name: str = "UNKNOWN"
    storage_capacity_gb: Optional[float] = None

    # JEDEC wear registers
    life_time_type_a: str = "UNKNOWN"
    life_time_type_b: str = "UNKNOWN"
    pre_eol_status: str = "UNKNOWN"
    estimated_wear_pct: Optional[int] = None  # upper bound of the worst JEDEC range, None if not read
    is_failing_or_degraded: bool = False
    status: str = "UNKNOWN"            # "PASS" | "FAIL" | "UNKNOWN" | "NEEDS_ROOT"

    # Speed benchmark (MB/s); None = not measured
    read_speed_mbps: Optional[float] = None
    write_speed_mbps: Optional[float] = None
    benchmark_duration_sec: Optional[float] = None
    benchmark_status: str = "Not run"

    diagnosis: str = ""
    raw_logs: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    @property
    def write_speed_mb_s(self) -> Optional[float]:
        return self.write_speed_mbps

    @property
    def read_speed_mb_s(self) -> Optional[float]:
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
    """eMMC / UFS JEDEC wear registers and a sequential dd read/write benchmark."""

    UFS_DESC_DIRS = [
        "/sys/devices/platform/soc/*.ufshc/health_descriptor",
        "/sys/devices/soc/*.ufshc/health_descriptor",
        "/sys/bus/platform/devices/*.ufshc/health_descriptor",
        "/sys/devices/platform/*.ufs/health_descriptor",
    ]

    def __init__(self, bridge: Optional[ADBBridge] = None):
        self.bridge = bridge or ADBBridge()

    def diagnose_storage_health(self, serial: str) -> StorageHealthReport:
        logger.info(f"Reading storage health registers for {serial}...")
        report = StorageHealthReport()

        # 1. Storage type / name / size (informational)
        emmc_dir = run_read(self.bridge, "ls -d /sys/block/mmcblk0", serial=serial, timeout=4)
        ufs_dir = run_read(self.bridge, "ls -d /sys/class/ufs_device* /sys/bus/platform/drivers/ufshcd*", serial=serial, timeout=4)
        if ufs_dir.ok:
            report.storage_type = "UFS"
        elif emmc_dir.ok:
            report.storage_type = "eMMC"

        name = read_node(self.bridge, ["/sys/block/mmcblk0/device/name", "/sys/block/sda/device/model"], serial=serial, timeout=4)
        if name.ok:
            report.storage_name = name.text.splitlines()[0].strip()
            report.evidence.append(name.evidence(f"name={report.storage_name}"))
        size = read_node(self.bridge, ["/sys/block/mmcblk0/size", "/sys/block/sda/size"], serial=serial, timeout=4)
        sectors = parse_int(size.text) if size.ok else None
        if sectors is not None and sectors > 0:
            report.storage_capacity_gb = round(sectors * 512 / (1024 ** 3), 2)
            report.evidence.append(size.evidence(f"{sectors} sectors x 512 B = {report.storage_capacity_gb} GiB"))

        # 2. JEDEC wear registers (eMMC first, then UFS health descriptor)
        life = read_node(self.bridge, ["/sys/block/mmcblk0/device/life_time"], serial=serial, timeout=4)
        eol = read_node(self.bridge, ["/sys/block/mmcblk0/device/pre_eol_info"], serial=serial, timeout=4)
        ufs_a = read_node(self.bridge, [f"{d}/life_time_estimation_a" for d in self.UFS_DESC_DIRS], serial=serial, timeout=4)
        ufs_b = read_node(self.bridge, [f"{d}/life_time_estimation_b" for d in self.UFS_DESC_DIRS], serial=serial, timeout=4)
        ufs_eol = read_node(self.bridge, [f"{d}/eol_info" for d in self.UFS_DESC_DIRS], serial=serial, timeout=4)
        reads: List[ReadResult] = [life, eol, ufs_a, ufs_b, ufs_eol]

        life_values: List[int] = []
        pre_eol: Optional[int] = None
        a_vals: List[int] = []
        b_vals: List[int] = []
        if life.ok and jedec.parse_life_time(life.text):
            tokens = life.text.split()
            a_vals = jedec.parse_life_time(tokens[0]) if tokens else []
            b_vals = jedec.parse_life_time(tokens[1]) if len(tokens) > 1 else []
            life_values = a_vals + b_vals
            pre_eol = jedec.parse_pre_eol(eol.text) if eol.ok else None
        else:
            a_vals = jedec.parse_life_time(ufs_a.text) if ufs_a.ok else []
            b_vals = jedec.parse_life_time(ufs_b.text) if ufs_b.ok else []
            life_values = a_vals + b_vals
            pre_eol = jedec.parse_pre_eol(ufs_eol.text) if ufs_eol.ok else None

        if a_vals:
            report.life_time_type_a = jedec.life_time_label(a_vals[0])
        if b_vals:
            report.life_time_type_b = jedec.life_time_label(b_vals[0])
        if pre_eol is not None:
            report.pre_eol_status = jedec.pre_eol_label(pre_eol)
        report.estimated_wear_pct = jedec.wear_upper_bound_pct(life_values)

        for r in reads:
            if r.ok or r.status == Status.NEEDS_ROOT or r.path_exists:
                note = ""
                if r.ok and not (jedec.parse_life_time(r.text) or jedec.parse_pre_eol(r.text)):
                    note = "malformed register value"
                report.evidence.append(r.evidence(note))
        if not report.evidence:
            report.evidence.append(life.evidence("no JEDEC health node found"))

        status = jedec.evaluate(life_values, pre_eol)
        if status == Status.UNKNOWN:
            status = failure_status(reads)
        report.status = str(status)

        if status == Status.FAIL:
            report.is_failing_or_degraded = True
            report.diagnosis = (f"Wear registers report high wear (up to {report.estimated_wear_pct}% of rated life, "
                                f"pre-EOL: {report.pre_eol_status}). Possible cause: flash near end of endurance; back up data.")
        elif status == Status.PASS:
            report.diagnosis = f"Wear registers read: up to {report.estimated_wear_pct}% of rated life used, pre-EOL: {report.pre_eol_status}."
        elif status == Status.NEEDS_ROOT:
            report.diagnosis = f"Wear registers for {report.storage_type} are blocked without root."
        else:
            report.diagnosis = f"Wear registers are not exposed (or unreadable) on this {report.storage_type} device."
        return report

    def run_speed_benchmark(self, serial: str, test_size_mb: int = 40) -> StorageHealthReport:
        """Sequential write (fsync) then read of a temp file in /data/local/tmp, measured by dd."""
        logger.info(f"Running storage speed benchmark on {serial} ({test_size_mb} MB)...")
        report = self.diagnose_storage_health(serial)
        start_time = time.time()
        tmp_file = "/data/local/tmp/ad_speed_benchmark.dat"

        w = run_read(self.bridge, f"dd if=/dev/zero of={tmp_file} bs=1048576 count={test_size_mb} conv=fsync",
                     serial=serial, timeout=60)
        r = run_read(self.bridge, f"dd if={tmp_file} of=/dev/null bs=1048576", serial=serial, timeout=60)
        self.bridge.shell(f"rm -f {tmp_file}", serial=serial, timeout=5)

        report.write_speed_mbps = parse_dd_speed_mb_s(f"{w.stdout}\n{w.stderr}") if w.exit_code in (0, None) else None
        report.read_speed_mbps = parse_dd_speed_mb_s(f"{r.stdout}\n{r.stderr}") if r.exit_code in (0, None) else None
        report.benchmark_duration_sec = round(time.time() - start_time, 2)
        report.evidence.append(w.evidence(f"write={report.write_speed_mbps} MB/s"))
        report.evidence.append(r.evidence(f"read={report.read_speed_mbps} MB/s (may be served from page cache)"))

        if report.write_speed_mbps is None and report.read_speed_mbps is None:
            report.benchmark_status = "UNKNOWN"
            report.diagnosis += " | Benchmark: dd output could not be parsed."
        else:
            report.benchmark_status = "Completed"
            report.diagnosis += (f" | Benchmark: write {report.write_speed_mbps} MB/s, read {report.read_speed_mbps} MB/s "
                                 f"(read may be served from page cache).")
        return report

    # Name used by the UI
    def run_full_storage_test(self, serial: str) -> StorageHealthReport:
        return self.run_speed_benchmark(serial)
