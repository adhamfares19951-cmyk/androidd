"""
Maintenance actions with honest results.

Only one action has a measurable effect without root: `pm trim-caches` (free space on /data
before and after, read with `df`). Android's own maintenance jobs (`sm idle-maint run`,
`cmd package bg-dexopt-job`) are started on request, but their effect cannot be measured here,
so they are reported as "started, not measured". Nothing deletes user files or changes settings.
"""

import re
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Any, Callable

from core.adb_bridge import ADBBridge
from core.result import Evidence, Status, Msg, M
from core.readers import run_read
from database.db_manager import DatabaseManager

logger = logging.getLogger("Optimizer")


@dataclass
class OptimizationMetrics:
    """Readings taken before / after. None = not read."""
    ram_free_mb: Optional[float] = None       # MemAvailable; shown for information, not attributed to any action
    ram_total_mb: Optional[float] = None
    storage_free_mb: Optional[float] = None   # df -k /data, available column
    storage_total_mb: Optional[float] = None
    current_drain_ma: Optional[float] = None  # kept for DB compatibility; not measured
    current_readable: bool = False
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() for e in self.evidence]
        return d


@dataclass
class MaintenanceAction:
    action_id: str
    command: str
    exit_code: Optional[int] = None
    measured: bool = False
    unit: str = ""
    before: Optional[float] = None
    after: Optional[float] = None
    delta: Optional[float] = None
    status: str = "UNKNOWN"   # PASS = command succeeded (and measured if measurable); FAIL = command failed
    note: Any = None
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() for e in self.evidence]
        return d


@dataclass
class OptimizationResult:
    serial: str
    timestamp: str
    before: OptimizationMetrics
    after: OptimizationMetrics
    ram_freed_mb: Optional[float] = None        # never attributed: no action here targets RAM
    storage_freed_mb: Optional[float] = None    # measured delta of free /data space around pm trim-caches
    drain_reduction_ma: Optional[float] = None  # not measured
    actions: List[MaintenanceAction] = field(default_factory=list)
    actions_completed: List[str] = field(default_factory=list)
    detailed_log: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "serial": self.serial,
            "timestamp": self.timestamp,
            "before": self.before.to_dict(),
            "after": self.after.to_dict(),
            "ram_freed_mb": self.ram_freed_mb,
            "storage_freed_mb": self.storage_freed_mb,
            "drain_reduction_ma": self.drain_reduction_ma,
            "actions": [a.to_dict() for a in self.actions],
            "actions_completed": self.actions_completed,
            "detailed_log": self.detailed_log
        }


def parse_meminfo(text: str) -> Dict[str, Optional[float]]:
    vals: Dict[str, Optional[float]] = {"total": None, "available": None}
    for line in (text or "").splitlines():
        m = re.match(r"^(MemTotal|MemAvailable):\s+(\d+)\s+kB", line.strip())
        if m:
            key = "total" if m.group(1) == "MemTotal" else "available"
            vals[key] = round(int(m.group(2)) / 1024.0, 1)
    return vals


def parse_df_data(text: str) -> Dict[str, Optional[float]]:
    """Parse `df -k /data`: second line, columns size/used/available in KiB."""
    lines = [l for l in (text or "").splitlines() if l.strip()]
    for line in lines[1:]:
        parts = line.split()
        if len(parts) >= 4 and parts[1].isdigit() and parts[3].isdigit():
            return {"total": round(int(parts[1]) / 1024.0, 1), "available": round(int(parts[3]) / 1024.0, 1)}
    return {"total": None, "available": None}


def _command_status(res) -> str:
    """Status of an unmeasured command: PASS only for a confirmed exit code 0."""
    if res.status == Status.NEEDS_ROOT:
        return str(Status.NEEDS_ROOT)
    if res.exit_code == 0:
        return str(Status.PASS)
    if res.exit_code is None or res.exit_code == -1:  # unknown exit code or timeout
        return str(Status.UNKNOWN)
    return str(Status.FAIL)


class RealSystemOptimizer:
    def __init__(self, bridge: Optional[ADBBridge] = None, db: Optional[DatabaseManager] = None):
        self.bridge = bridge or ADBBridge()
        self.db = db or DatabaseManager()

    def capture_metrics(self, serial: str) -> OptimizationMetrics:
        metrics = OptimizationMetrics()
        mem = run_read(self.bridge, "cat /proc/meminfo", serial=serial, timeout=8, source="/proc/meminfo")
        if mem.ok:
            vals = parse_meminfo(mem.text)
            metrics.ram_total_mb, metrics.ram_free_mb = vals["total"], vals["available"]
        metrics.evidence.append(mem.evidence(f"MemTotal={metrics.ram_total_mb} MB, MemAvailable={metrics.ram_free_mb} MB"))

        df = run_read(self.bridge, "df -k /data", serial=serial, timeout=8)
        if df.ok:
            vals = parse_df_data(df.text)
            metrics.storage_total_mb, metrics.storage_free_mb = vals["total"], vals["available"]
        metrics.evidence.append(df.evidence(f"/data free={metrics.storage_free_mb} MB"))
        return metrics

    def run_optimization(
        self,
        serial: str,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> OptimizationResult:
        logger.info(f"Starting maintenance actions on {serial}...")
        log_lines: List[str] = []

        def log_step(msg: str):
            logger.info(msg)
            log_lines.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

        if progress_callback:
            progress_callback(5, M("maint.progress.before"))
        before = self.capture_metrics(serial)
        actions: List[MaintenanceAction] = []

        # 1. App cache trim: measured by free space on /data before/after
        if progress_callback:
            progress_callback(20, M("maint.progress.step1"))
        trim = run_read(self.bridge, "pm trim-caches 999G", serial=serial, timeout=60)
        mid = self.capture_metrics(serial)
        a1 = MaintenanceAction(action_id="cache_trim", command="pm trim-caches 999G", exit_code=trim.exit_code,
                               measured=True, unit="MB", before=before.storage_free_mb, after=mid.storage_free_mb)
        a1.evidence = [trim.evidence()] + before.evidence[1:] + mid.evidence[1:]
        if trim.exit_code not in (0, None) or trim.status == Status.NEEDS_ROOT:
            a1.status = str(Status.FAIL if trim.status != Status.NEEDS_ROOT else Status.NEEDS_ROOT)
            a1.note = M("maint.note.trim_failed")
        elif a1.before is not None and a1.after is not None:
            a1.delta = round(a1.after - a1.before, 1)
            a1.status = str(Status.PASS)
            a1.note = M("maint.note.trim_measured")
        else:
            a1.status = str(Status.UNKNOWN)
            a1.note = M("maint.note.trim_unmeasured")
        actions.append(a1)
        log_step(f"pm trim-caches: exit={trim.exit_code}, free /data {a1.before} -> {a1.after} MB (delta {a1.delta})")

        # 2. Android storage maintenance (includes fstrim on the system's own schedule) - not measurable
        if progress_callback:
            progress_callback(50, M("maint.progress.step2"))
        maint = run_read(self.bridge, "sm idle-maint run", serial=serial, timeout=60)
        a2 = MaintenanceAction(action_id="idle_maintenance", command="sm idle-maint run", exit_code=maint.exit_code,
                               measured=False, evidence=[maint.evidence()])
        a2.status = _command_status(maint)
        a2.note = M("maint.note.idle_maint")
        actions.append(a2)
        log_step(f"sm idle-maint run: exit={maint.exit_code}")

        # 3. Background app compilation job - not measurable
        if progress_callback:
            progress_callback(70, M("maint.progress.step3"))
        dex = run_read(self.bridge, "cmd package bg-dexopt-job", serial=serial, timeout=300)
        a3 = MaintenanceAction(action_id="bg_dexopt", command="cmd package bg-dexopt-job", exit_code=dex.exit_code,
                               measured=False, evidence=[dex.evidence()])
        a3.status = _command_status(dex)
        a3.note = M("maint.note.dexopt")
        actions.append(a3)
        log_step(f"bg-dexopt-job: exit={dex.exit_code}")

        if progress_callback:
            progress_callback(90, M("maint.progress.after"))
        after = self.capture_metrics(serial)

        result = OptimizationResult(
            serial=serial,
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            before=before,
            after=after,
            storage_freed_mb=a1.delta,
            actions=actions,
            actions_completed=[f"{a.command} [{a.status}{'' if a.measured else ', not measured'}]" for a in actions],
            detailed_log="\n".join(log_lines)
        )

        try:
            self.db.save_optimization_log(
                device_serial=serial,
                ram_before_mb=before.ram_free_mb,
                ram_after_mb=after.ram_free_mb,
                storage_free_before_mb=a1.before,
                storage_free_after_mb=a1.after,
                current_drain_before_ma=None,
                current_drain_after_ma=None,
                actions_taken=result.actions_completed,
                log_details=result.detailed_log
            )
        except Exception as e:
            logger.warning(f"Could not store maintenance log: {e}")

        if progress_callback:
            progress_callback(100, M("maint.progress.done", delta=a1.delta))
        return result
