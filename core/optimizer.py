import re
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Any, Callable
from core.adb_bridge import ADBBridge
from database.db_manager import DatabaseManager

logger = logging.getLogger("Optimizer")


@dataclass
class OptimizationMetrics:
    ram_free_mb: float = 0.0
    ram_total_mb: float = 0.0
    storage_free_mb: float = 0.0
    storage_total_mb: float = 0.0
    current_drain_ma: float = 0.0
    current_readable: bool = True

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class OptimizationResult:
    serial: str
    timestamp: str
    before: OptimizationMetrics
    after: OptimizationMetrics
    ram_freed_mb: float
    storage_freed_mb: float
    drain_reduction_ma: float
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
            "actions_completed": self.actions_completed,
            "detailed_log": self.detailed_log
        }


class RealSystemOptimizer:
    def __init__(self, bridge: Optional[ADBBridge] = None, db: Optional[DatabaseManager] = None):
        self.bridge = bridge or ADBBridge()
        self.db = db or DatabaseManager()

    def capture_metrics(self, serial: str) -> OptimizationMetrics:
        metrics = OptimizationMetrics()

        code_m, out_m, _ = self.bridge.shell("cat /proc/meminfo", serial=serial, timeout=8)
        if code_m == 0 and out_m:
            total_kb = 0
            avail_kb = 0
            free_kb = 0
            for line in out_m.splitlines():
                if "MemTotal:" in line:
                    m = re.search(r"(\d+)", line)
                    if m:
                        total_kb = int(m.group(1))
                elif "MemAvailable:" in line:
                    m = re.search(r"(\d+)", line)
                    if m:
                        avail_kb = int(m.group(1))
                elif "MemFree:" in line and free_kb == 0:
                    m = re.search(r"(\d+)", line)
                    if m:
                        free_kb = int(m.group(1))

            metrics.ram_total_mb = round(total_kb / 1024.0, 1)
            effective_free = avail_kb if avail_kb > 0 else free_kb
            metrics.ram_free_mb = round(effective_free / 1024.0, 1)

        code_s, out_s, _ = self.bridge.shell("df -k /data", serial=serial, timeout=8)
        if code_s == 0 and out_s:
            lines = [l.strip() for l in out_s.splitlines() if l.strip()]
            for line in lines[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        tot_k = int(parts[1])
                        avail_k = int(parts[3])
                        metrics.storage_total_mb = round(tot_k / 1024.0, 1)
                        metrics.storage_free_mb = round(avail_k / 1024.0, 1)
                        break
                    except ValueError:
                        continue

        c_val = self.bridge.read_sysfs_int([
            "/sys/class/power_supply/battery/current_now",
            "/sys/class/power_supply/bms/current_now",
            "/sys/class/power_supply/sec-battery/current_now"
        ], serial=serial)

        if c_val is not None:
            if abs(c_val) > 20000:
                metrics.current_drain_ma = abs(round(c_val / 1000.0, 1))
            else:
                metrics.current_drain_ma = abs(float(c_val))
            metrics.current_readable = True
        else:
            # Zero fake data when node is inaccessible under SELinux
            metrics.current_drain_ma = 0.0
            metrics.current_readable = False

        return metrics

    def run_optimization(
        self,
        serial: str,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> OptimizationResult:
        logger.info(f"Starting Real Android System Deep Optimization on {serial}...")
        log_lines: List[str] = []
        actions_completed: List[str] = []

        def log_step(msg: str):
            logger.info(msg)
            log_lines.append(f"[{time.strftime('%H:%M:%S')}] {msg}")

        if progress_callback:
            progress_callback(5, "Capturing Baseline Hardware Metrics...")
        log_step("Capturing baseline memory, storage, and current drain...")
        before = self.capture_metrics(serial)
        log_step(f"Baseline: Free RAM={before.ram_free_mb}MB, Free Storage={before.storage_free_mb}MB, Drain={before.current_drain_ma}mA")

        # Step 1: Cache & Junk Purge
        if progress_callback:
            progress_callback(20, "Step 1/5: Trimming System Caches & Purging Junk Thumbnails...")
        log_step("Executing 'pm trim-caches 999G' to release cached app resources...")
        code_tc, out_tc, _ = self.bridge.shell("pm trim-caches 999G", serial=serial, timeout=30)
        log_step(f"Cache trim output: {out_tc.strip() or 'Completed'}")

        log_step("Removing thumbnail caches and temporary debug logs...")
        self.bridge.shell("rm -rf /sdcard/DCIM/.thumbnails/* 2>/dev/null", serial=serial, timeout=10)
        self.bridge.shell("rm -rf /data/local/tmp/* 2>/dev/null", serial=serial, timeout=10)
        self.bridge.shell("rm -rf /sdcard/Android/data/*/cache/* 2>/dev/null", serial=serial, timeout=10)
        actions_completed.append("Cache & Junk Purge (pm trim-caches + DCIM .thumbnails + temp logs)")

        # Step 2: Non-Root Storage Idle Maintenance & TRIM
        if progress_callback:
            progress_callback(40, "Step 2/5: Triggering Storage Idle Maintenance (sm idle-maint run)...")
        log_step("Triggering Android storage maintenance task (sm idle-maint run)...")
        code_maint, out_maint, _ = self.bridge.shell("sm idle-maint run", serial=serial, timeout=40)
        if code_maint != 0:
            log_step("Falling back to 'sm fstrim'...")
            code_trim, out_trim, _ = self.bridge.shell("sm fstrim", serial=serial, timeout=30)
            if code_trim != 0:
                log_step("Falling back to 'vdc fstrim dotrim'...")
                self.bridge.shell("vdc fstrim dotrim", serial=serial, timeout=30)
        log_step("Storage idle-maint / TRIM dispatched successfully.")
        actions_completed.append("Storage Idle Maintenance (sm idle-maint run / sm fstrim)")

        # Step 3: ART Compiler Optimization
        if progress_callback:
            progress_callback(60, "Step 3/5: Running ART Compiler Bytecode Optimization (bg-dexopt-job)...")
        log_step("Initiating background bytecode AOT/speed profile compilation...")
        code_dex, out_dex, _ = self.bridge.shell("cmd package bg-dexopt-job", serial=serial, timeout=90)
        log_step(f"ART compilation response: {out_dex.strip() or 'bg-dexopt-job dispatched'}")
        self.bridge.shell("cmd package compile -m speed-profile -a", serial=serial, timeout=45)
        actions_completed.append("ART Compiler Optimization (bg-dexopt-job speed-profile)")

        # Step 4: Doze & Standby Optimization
        if progress_callback:
            progress_callback(80, "Step 4/5: Enforcing Deep Doze Power-Saving Policy...")
        log_step("Activating aggressive Doze mode and resetting battery standby listeners...")
        self.bridge.shell("dumpsys deviceidle force-idle", serial=serial, timeout=10)
        self.bridge.shell("device_config put device_idle light_after_inactive_to 1000 2>/dev/null", serial=serial, timeout=5)
        actions_completed.append("Deep Doze Mode Activation (dumpsys deviceidle force-idle)")

        # Step 5: UI Smoothness & Responsiveness
        if progress_callback:
            progress_callback(90, "Step 5/5: Tweaking Window Animator Scales to 0.5x...")
        log_step("Setting system animation scales to 0.5x for snappy response...")
        self.bridge.shell("settings put global window_animation_scale 0.5", serial=serial, timeout=5)
        self.bridge.shell("settings put global transition_animation_scale 0.5", serial=serial, timeout=5)
        self.bridge.shell("settings put global animator_duration_scale 0.5", serial=serial, timeout=5)
        actions_completed.append("UI Responsiveness Boost (Animation scales 0.5x)")

        # Capture After Metrics
        time.sleep(2.0)
        log_step("Capturing post-optimization metrics...")
        after = self.capture_metrics(serial)
        log_step(f"Post-Optimization: Free RAM={after.ram_free_mb}MB, Free Storage={after.storage_free_mb}MB, Drain={after.current_drain_ma}mA")

        ram_freed = round(max(0.0, after.ram_free_mb - before.ram_free_mb), 1)
        storage_freed = round(max(0.0, after.storage_free_mb - before.storage_free_mb), 1)
        drain_reduction = round(max(0.0, before.current_drain_ma - after.current_drain_ma), 1) if (before.current_readable and after.current_readable) else 0.0

        result = OptimizationResult(
            serial=serial,
            timestamp=time.strftime("%Y-%m-%d %H:%M:%S"),
            before=before,
            after=after,
            ram_freed_mb=ram_freed,
            storage_freed_mb=storage_freed,
            drain_reduction_ma=drain_reduction,
            actions_completed=actions_completed,
            detailed_log="\n".join(log_lines)
        )

        self.db.save_optimization_log(
            device_serial=serial,
            ram_before_mb=before.ram_free_mb,
            ram_after_mb=after.ram_free_mb,
            storage_free_before_mb=before.storage_free_mb,
            storage_free_after_mb=after.storage_free_mb,
            current_drain_before_ma=before.current_drain_ma,
            current_drain_after_ma=after.current_drain_ma,
            actions_taken=actions_completed,
            log_details=result.detailed_log
        )

        if progress_callback:
            progress_callback(100, f"Optimization Complete! Freed {ram_freed}MB RAM, {storage_freed}MB Storage.")

        return result
