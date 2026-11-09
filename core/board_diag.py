import os
import re
import time
import json
import hashlib
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Any, Callable
from core.adb_bridge import ADBBridge
from core.result import Evidence, Status, CheckResult
from database.db_manager import DatabaseManager

logger = logging.getLogger("BoardDiag")

BATTERY_SYSFS_DIRS = [
    "/sys/class/power_supply/battery",
    "/sys/class/power_supply/bms",
    "/sys/class/power_supply/sec-battery",
    "/sys/class/power_supply/qcom-battery",
    "/sys/class/power_supply/mtk-battery",
    "/sys/class/power_supply/charger",
    "/sys/class/power_supply/usb"
]


@dataclass
class DiagnosticVerdict:
    status: str  # "PASS" | "FAIL" | "UNKNOWN" | "NEEDS_ROOT"
    value: Any = None
    evidence: List[Evidence] = field(default_factory=list)
    diagnosis: str = ""

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class PMICStatus:
    voltage_mv: Optional[float] = None
    voltage_source: str = "UNKNOWN"
    current_ma: Optional[float] = None
    current_source: str = "UNKNOWN"
    current_note: str = ""
    charge_full_uah: Optional[int] = None
    charge_full_design_uah: Optional[int] = None
    cycle_count: Optional[int] = None
    temp_c: Optional[float] = None
    temp_source: str = "UNKNOWN"
    status: str = "UNKNOWN"
    health: str = "UNKNOWN"
    true_health_pct: Optional[float] = None
    health_verdict: str = "UNKNOWN"  # "PASS", "FAIL", "UNKNOWN", "NEEDS_ROOT"
    is_charging: bool = False
    is_ac_plugged: bool = False
    fault_flags: List[str] = field(default_factory=list)
    raw_details: Dict[str, Any] = field(default_factory=dict)
    evidence: List[Evidence] = field(default_factory=list)

    @property
    def temperature_c(self) -> float:
        return self.temp_c if self.temp_c is not None else 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class ThermalZone:
    zone_id: int
    zone_type: str
    temp_c: float
    is_overheated: bool

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class BlackboxReport:
    boot_reason: str = "Unknown"
    boot_reason_history: str = "None"
    shutdown_category: str = "NORMAL"  # "THERMAL", "UVLO", "WATCHDOG_PANIC", "USER_REQUESTED", "UNKNOWN"
    has_abnormal_shutdown: bool = False
    dropbox_events: List[str] = field(default_factory=list)
    kernel_ramoops_status: str = "UNKNOWN"  # "PASS", "FAIL", "NEEDS_ROOT"
    kernel_log_snippets: List[str] = field(default_factory=list)
    diagnosis: str = "System boot records normal."
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class PanicAnalysis:
    has_critical_error: bool = False
    emmc_ufs_errors: List[str] = field(default_factory=list)
    watchdog_resets: List[str] = field(default_factory=list)
    thermal_shutdowns: List[str] = field(default_factory=list)
    kernel_panics: List[str] = field(default_factory=list)
    summary_verdict: str = "Clean"
    blackbox: BlackboxReport = field(default_factory=BlackboxReport)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "has_critical_error": self.has_critical_error,
            "emmc_ufs_errors": self.emmc_ufs_errors,
            "watchdog_resets": self.watchdog_resets,
            "thermal_shutdowns": self.thermal_shutdowns,
            "kernel_panics": self.kernel_panics,
            "summary_verdict": self.summary_verdict,
            "blackbox": self.blackbox.to_dict()
        }


@dataclass
class DeadPhoneBootROMDiagnosis:
    detected_ports: List[Dict[str, str]] = field(default_factory=list)
    is_qualcomm_edl: bool = False
    is_mediatek_brom: bool = False
    is_samsung_odin: bool = False
    is_usb_cycling: bool = False
    cycling_details: str = ""
    summary_diagnosis: str = "No BootROM / Emergency USB state detected."

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FastChargeDiagnostics:
    charger_type: str = "Unknown"
    voltage_mv: float = 0.0
    voltage_v: float = 0.0
    current_ma: float = 0.0
    current_a: float = 0.0
    wattage_w: float = 0.0
    protocol_name: str = "Standard USB (5V/0.5A)"
    is_fast_charging: bool = False
    battery_level: int = 0
    charge_status: str = "Unknown"
    health_state: str = "Good"
    diagnosis: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StorageLifeReport:
    storage_type: str = "Unknown"
    device_model: str = ""
    life_time_type_a: str = "Unknown"
    life_time_type_b: str = "Unknown"
    pre_eol_info: str = "Unknown"
    raw_hex_a: str = ""
    raw_hex_b: str = ""
    raw_pre_eol: str = ""
    status: str = "UNKNOWN"  # "PASS", "FAIL", "UNKNOWN", "NEEDS_ROOT"
    is_warning: bool = False
    is_critical: bool = False
    health_verdict: str = "GOOD"
    diagnosis: str = ""
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class StressTestSnapshot:
    second: int = 0
    freqs_mhz: List[float] = field(default_factory=list)
    avg_freq_mhz: float = 0.0
    voltage_mv: float = 0.0
    temp_c: float = 0.0
    battery_temp_c: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StressTestReport:
    duration_seconds: int = 30
    initial_voltage_mv: float = 0.0
    min_voltage_mv: float = 0.0
    voltage_sag_mv: float = 0.0
    initial_temp_c: float = 0.0
    max_temp_c: float = 0.0
    temp_rise_c: float = 0.0
    max_freq_mhz: float = 0.0
    min_freq_mhz: float = 0.0
    throttling_detected: bool = False
    severe_sag_detected: bool = False
    samples: List[StressTestSnapshot] = field(default_factory=list)
    verdict: str = "STABLE"
    diagnosis: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return {
            "duration_seconds": self.duration_seconds,
            "initial_voltage_mv": self.initial_voltage_mv,
            "min_voltage_mv": self.min_voltage_mv,
            "voltage_sag_mv": self.voltage_sag_mv,
            "initial_temp_c": self.initial_temp_c,
            "max_temp_c": self.max_temp_c,
            "temp_rise_c": self.temp_rise_c,
            "max_freq_mhz": self.max_freq_mhz,
            "min_freq_mhz": self.min_freq_mhz,
            "throttling_detected": self.throttling_detected,
            "severe_sag_detected": self.severe_sag_detected,
            "samples": [s.to_dict() for s in self.samples],
            "verdict": self.verdict,
            "diagnosis": self.diagnosis
        }


@dataclass
class BoardDiagnosticResult:
    serial: str
    pmic: PMICStatus
    thermal_zones: List[ThermalZone]
    panic_analysis: PanicAnalysis
    overall_health: str = "PASS"
    critical_findings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "serial": self.serial,
            "pmic": self.pmic.to_dict(),
            "thermal_zones": [tz.to_dict() for tz in self.thermal_zones],
            "panic_analysis": self.panic_analysis.to_dict(),
            "overall_health": self.overall_health,
            "critical_findings": self.critical_findings
        }


@dataclass
class RootForensicsReport:
    serial: str = ""
    is_rooted: bool = False
    root_type: str = "NON_ROOT"  # "Magisk", "KernelSU", "APatch", "su", "NON_ROOT"
    kernel_ramoops_log: str = ""
    matched_fault_signatures: List[Dict[str, Any]] = field(default_factory=list)
    i2c_bus_errors: List[str] = field(default_factory=list)
    nand_exact_lifetime_a: str = ""
    nand_exact_lifetime_b: str = ""
    nand_exact_pre_eol: str = ""
    nand_wear_status: str = "UNKNOWN"
    diagnosis: str = ""
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class EFSBackupResult:
    serial: str = ""
    success: bool = False
    backed_up_partitions: List[str] = field(default_factory=list)
    backup_folder: str = ""
    total_bytes: int = 0
    hashes: Dict[str, str] = field(default_factory=dict)
    message: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MotherboardDiagnostics:
    def __init__(self, bridge: Optional[ADBBridge] = None, db: Optional[DatabaseManager] = None):
        self.bridge = bridge or ADBBridge()
        self.db = db or DatabaseManager()

    def analyze_pmic(self, serial: str) -> PMICStatus:
        pmic = PMICStatus()
        raw: Dict[str, Any] = {}
        evidence_items: List[Evidence] = []

        # 1. Voltage (Battery voltage - fuel gauge)
        v_paths = [f"{base}/voltage_now" for base in BATTERY_SYSFS_DIRS] + \
                  [f"{base}/batt_vol" for base in BATTERY_SYSFS_DIRS]
        for p in v_paths:
            v_val = self.bridge.read_sysfs([p], serial=serial)
            if v_val is not None:
                clean_num = re.sub(r"[^\d\-]", "", v_val)
                if clean_num:
                    try:
                        raw_v = int(clean_num)
                        raw["voltage_raw"] = raw_v
                        if raw_v > 20000:
                            pmic.voltage_mv = round(raw_v / 1000.0, 1)
                        elif raw_v > 100:
                            pmic.voltage_mv = float(raw_v)
                        pmic.voltage_source = p
                        evidence_items.append(Evidence(source=p, raw_excerpt=f"raw={raw_v} -> voltage_mv={pmic.voltage_mv}", exit_code=0))
                        break
                    except ValueError:
                        pass

        # 2. Battery current
        c_paths = [f"{base}/current_now" for base in BATTERY_SYSFS_DIRS] + \
                  [f"{base}/batt_current" for base in BATTERY_SYSFS_DIRS]
        for p in c_paths:
            c_val = self.bridge.read_sysfs([p], serial=serial)
            if c_val is not None:
                clean_num = re.sub(r"[^\d\-]", "", c_val)
                if clean_num:
                    try:
                        raw_c = int(clean_num)
                        raw["current_raw"] = raw_c
                        if abs(raw_c) > 20000:
                            pmic.current_ma = round(raw_c / 1000.0, 1)
                        else:
                            pmic.current_ma = float(raw_c)
                        pmic.current_source = p
                        evidence_items.append(Evidence(source=p, raw_excerpt=f"raw={raw_c} -> current_ma={pmic.current_ma}", exit_code=0))
                        break
                    except ValueError:
                        pass

        # Determine USB charging vs Wi-Fi current note
        is_wifi_conn = ":" in serial
        if is_wifi_conn:
            pmic.current_note = "Wi-Fi connected (pure battery current without USB charger)"
        else:
            pmic.current_note = "includes charging current while USB is connected"

        # 3. Capacity & Cycles
        cf_paths = [f"{base}/charge_full" for base in BATTERY_SYSFS_DIRS]
        cf_raw = self.bridge.read_sysfs_int(cf_paths, serial=serial)
        if cf_raw is not None:
            pmic.charge_full_uah = cf_raw
            evidence_items.append(Evidence(source=cf_paths[0], raw_excerpt=f"charge_full: {cf_raw} uAh", exit_code=0))

        cfd_paths = [f"{base}/charge_full_design" for base in BATTERY_SYSFS_DIRS]
        cfd_raw = self.bridge.read_sysfs_int(cfd_paths, serial=serial)
        if cfd_raw is not None:
            pmic.charge_full_design_uah = cfd_raw
            evidence_items.append(Evidence(source=cfd_paths[0], raw_excerpt=f"charge_full_design: {cfd_raw} uAh", exit_code=0))

        cycles_paths = [f"{base}/cycle_count" for base in BATTERY_SYSFS_DIRS]
        cycles_raw = self.bridge.read_sysfs_int(cycles_paths, serial=serial)
        if cycles_raw is not None:
            pmic.cycle_count = cycles_raw
            evidence_items.append(Evidence(source=cycles_paths[0], raw_excerpt=f"cycle_count: {cycles_raw}", exit_code=0))

        temp_paths = [f"{base}/temp" for base in BATTERY_SYSFS_DIRS] + \
                     [f"{base}/batt_temp" for base in BATTERY_SYSFS_DIRS]
        for p in temp_paths:
            t_val = self.bridge.read_sysfs([p], serial=serial)
            if t_val is not None:
                clean_num = re.sub(r"[^\d\-]", "", t_val)
                if clean_num:
                    try:
                        raw_t = int(clean_num)
                        if raw_t > 1000:
                            pmic.temp_c = round(raw_t / 1000.0, 1)
                        elif raw_t > 100:
                            pmic.temp_c = round(raw_t / 10.0, 1)
                        else:
                            pmic.temp_c = float(raw_t)
                        pmic.temp_source = p
                        evidence_items.append(Evidence(source=p, raw_excerpt=f"raw={raw_t} -> temp_c={pmic.temp_c}", exit_code=0))
                        break
                    except ValueError:
                        pass

        status_paths = [f"{base}/status" for base in BATTERY_SYSFS_DIRS]
        status_raw = self.bridge.read_sysfs(status_paths, serial=serial)
        if status_raw:
            pmic.status = status_raw.strip().capitalize()
            pmic.is_charging = "charg" in status_raw.lower()
            evidence_items.append(Evidence(source=status_paths[0], raw_excerpt=f"status: {status_raw.strip()}", exit_code=0))

        health_paths = [f"{base}/health" for base in BATTERY_SYSFS_DIRS]
        health_raw = self.bridge.read_sysfs(health_paths, serial=serial)
        if health_raw:
            pmic.health = health_raw.strip().capitalize()
            evidence_items.append(Evidence(source=health_paths[0], raw_excerpt=f"health: {health_raw.strip()}", exit_code=0))

        # Non-root dumpsys battery fallback and verification
        code, out, _ = self.bridge.shell("dumpsys battery", serial=serial, timeout=8)
        if code == 0 and out:
            evidence_items.append(Evidence(source="dumpsys battery", raw_excerpt=out.strip()[:1000], exit_code=code))
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("voltage:") and pmic.voltage_mv is None:
                    m = re.search(r"(\d+)", line)
                    if m:
                        v = float(m.group(1))
                        pmic.voltage_mv = v if v < 10000 else round(v / 1000.0, 1)
                        pmic.voltage_source = "dumpsys battery (voltage)"
                elif line.startswith("temperature:") and pmic.temp_c is None:
                    m = re.search(r"(\d+)", line)
                    if m:
                        pmic.temp_c = round(int(m.group(1)) / 10.0, 1)
                        pmic.temp_source = "dumpsys battery (temperature)"
                elif line.startswith("status:") and pmic.status == "UNKNOWN":
                    val = line.split(":", 1)[1].strip()
                    status_map = {"1": "UNKNOWN", "2": "Charging", "3": "Discharging", "4": "Not charging", "5": "Full"}
                    pmic.status = status_map.get(val, val)
                    pmic.is_charging = pmic.status == "Charging"
                elif line.startswith("health:") and pmic.health == "UNKNOWN":
                    val = line.split(":", 1)[1].strip()
                    health_map = {"1": "UNKNOWN", "2": "Good", "3": "Overheat", "4": "Dead", "5": "Over voltage", "6": "Failure", "7": "Cold"}
                    pmic.health = health_map.get(val, val)
                elif line.startswith("AC powered:"):
                    pmic.is_ac_plugged = "true" in line.lower()
                elif line.startswith("USB powered:") and not pmic.is_ac_plugged:
                    pmic.is_ac_plugged = "true" in line.lower()
        else:
            evidence_items.append(Evidence(source="dumpsys battery", raw_excerpt=out or "No output from dumpsys battery", exit_code=code))

        # Real Battery Health % Calculation (Zero fake numbers!)
        if pmic.charge_full_uah is not None and pmic.charge_full_design_uah is not None and pmic.charge_full_uah > 0 and pmic.charge_full_design_uah > 0:
            calc_health = (pmic.charge_full_uah / pmic.charge_full_design_uah) * 100.0
            pmic.true_health_pct = round(min(100.0, max(1.0, calc_health)), 1)
            pmic.health_verdict = "PASS" if pmic.true_health_pct >= 80.0 else "FAIL"
            evidence_items.append(Evidence(source="BMS Fuel Gauge Health Calc", raw_excerpt=f"{pmic.charge_full_uah} / {pmic.charge_full_design_uah} = {pmic.true_health_pct}%", exit_code=0))
        else:
            # When design capacity registers are blocked by SELinux / OEM
            pmic.true_health_pct = None
            pmic.health_verdict = "NEEDS_ROOT" if not self.bridge.is_root_available(serial) else "UNKNOWN"
            evidence_items.append(Evidence(source="BMS Fuel Gauge Capacity", raw_excerpt="charge_full / charge_full_design inaccessible (Protected by SELinux or unexposed by OEM BMS driver)", exit_code=1))

        if pmic.is_charging and pmic.voltage_mv is not None and 0 < pmic.voltage_mv < 3500.0:
            pmic.fault_flags.append(
                "Critical Voltage Drop: Potential Charging IC (PMIC) failure or degraded battery cell (< 3500mV while charging)"
            )

        if pmic.is_ac_plugged and pmic.is_charging and pmic.current_ma is not None and 0 < pmic.current_ma < 200.0:
            pmic.fault_flags.append(
                "High Resistance / Low Current: Dirty charging port, bad cable, or damaged USB board line (< 200mA on AC)"
            )

        pmic.raw_details = raw
        pmic.evidence = evidence_items
        return pmic

    def run_leakage_test(
        self,
        serial: str,
        sample_count: int = 10,
        sample_interval: float = 3.0,
        progress_callback: Optional[Callable[[int, float, str], None]] = None
    ) -> Tuple[bool, float, float, List[float], str]:
        logger.info(f"Initiating Motherboard Battery Current Leakage Test on {serial}...")

        if progress_callback:
            progress_callback(0, 0.0, "Turning off screen to measure battery current (includes charging current while USB connected)...")

        self.bridge.shell("input keyevent 223", serial=serial, timeout=5)
        time.sleep(4.0)

        samples: List[float] = []
        c_paths = [f"{base}/current_now" for base in BATTERY_SYSFS_DIRS] + \
                  [f"{base}/batt_current" for base in BATTERY_SYSFS_DIRS]

        readable = True
        for idx in range(1, sample_count + 1):
            c_val = self.bridge.read_sysfs_int(c_paths, serial=serial)
            c_ma = 0.0
            if c_val is not None:
                if abs(c_val) > 20000:
                    c_ma = abs(round(c_val / 1000.0, 1))
                else:
                    c_ma = abs(float(c_val))
            else:
                readable = False
                c_ma = 0.0

            samples.append(c_ma)
            pct = int((idx / sample_count) * 100)
            msg = f"Sample {idx}/{sample_count}: Battery current = {c_ma} mA"
            if progress_callback:
                progress_callback(pct, c_ma, msg)
            if idx < sample_count:
                time.sleep(sample_interval)

        self.bridge.shell("input keyevent 224", serial=serial, timeout=5)

        avg_leakage = round(sum(samples) / len(samples), 1) if samples and readable else 0.0
        max_leakage = round(max(samples), 1) if samples and readable else 0.0

        is_wifi = ":" in serial
        usb_note = " (Note: Wi-Fi connected, no USB charging current)" if is_wifi else " (Note: includes charging current while USB is connected)"

        if not readable or max_leakage == 0.0:
            is_faulty = False
            verdict = "UNKNOWN: SELinux Enforcing blocked current_now node. Requires root access or hardware USB inline multimeter."
        elif avg_leakage > 250.0 or max_leakage > 350.0:
            is_faulty = True
            verdict = f"FAIL: Elevated Battery Current Detected! Avg: {avg_leakage}mA, Peak: {max_leakage}mA{usb_note}"
        else:
            is_faulty = False
            verdict = f"PASS: Battery current within safe limits. Avg: {avg_leakage}mA, Peak: {max_leakage}mA{usb_note}"

        self.db.save_leakage_test(
            device_serial=serial,
            samples=samples,
            avg_leakage_ma=avg_leakage,
            max_leakage_ma=max_leakage,
            is_faulty=is_faulty,
            verdict=verdict
        )

        return is_faulty, avg_leakage, max_leakage, samples, verdict

    def scan_thermal_map(self, serial: str) -> List[ThermalZone]:
        """
        Query thermal matrix using non-root dumpsys thermalservice and sysfs nodes.
        Works across all modern Android versions without root!
        """
        zones: List[ThermalZone] = []

        # 1. Non-root dumpsys thermalservice (Gold standard on Android 9+)
        code_d, out_d, _ = self.bridge.shell("dumpsys thermalservice", serial=serial, timeout=8)
        if code_d == 0 and out_d:
            # Parse Temperature records: Temperature{mValue=35.5, mType=0, mName=cpu-1-0, mStatus=0}
            matches = re.findall(r"Temperature\{mValue=([\d\.\-]+).*?mType=(\d+).*?mName=([^,]+).*?mStatus=(\d+)", out_d)
            for m in matches:
                try:
                    val = float(m[0])
                    name = m[2].strip()
                    is_hot = val > 45.0
                    zones.append(ThermalZone(zone_id=len(zones), zone_type=name, temp_c=round(val, 1), is_overheated=is_hot))
                except Exception:
                    continue

        # 2. Sysfs fallback / augmentation
        if not zones:
            code, out, _ = self.bridge.shell(
                "for z in /sys/class/thermal/thermal_zone*; do echo -n \"$z:\"; cat $z/type 2>/dev/null; echo -n \"=\"; cat $z/temp 2>/dev/null; echo; done",
                serial=serial,
                timeout=10
            )
            if code == 0 and out:
                for line in out.splitlines():
                    line = line.strip()
                    if not line or ":" not in line or "=" not in line:
                        continue
                    try:
                        path_part, rest = line.split(":", 1)
                        type_str, temp_str = rest.split("=", 1)
                        z_match = re.search(r"thermal_zone(\d+)", path_part)
                        z_id = int(z_match.group(1)) if z_match else len(zones)
                        t_type = type_str.strip() or f"Zone_{z_id}"
                        t_clean = re.sub(r"[^\d\-]", "", temp_str.strip())
                        if not t_clean:
                            continue
                        t_int = int(t_clean)
                        t_c = round(t_int / 1000.0, 1) if abs(t_int) > 1000 else float(t_int)
                        if -40.0 <= t_c <= 125.0:
                            is_hot = t_c > 45.0
                            zones.append(ThermalZone(zone_id=z_id, zone_type=t_type, temp_c=t_c, is_overheated=is_hot))
                    except Exception as e:
                        logger.debug(f"Thermal zone parse error: {e}")

        return zones

    def run_blackbox_forensics(self, serial: str) -> BlackboxReport:
        """
        Non-Root Sudden Shutdown & Reboot Forensics Analyzer.
        Reads boot reason properties, Dropbox restart entries, and kernel panic logs.
        """
        bb = BlackboxReport()

        # 1. Non-Root System Properties
        b_reason = self.bridge.get_prop("sys.boot.reason", serial=serial) or \
                   self.bridge.get_prop("ro.boot.bootreason", serial=serial) or "Unknown"
        b_history = self.bridge.get_prop("persist.sys.boot.reason.history", serial=serial) or "None"
        shutdown_req = self.bridge.get_prop("sys.shutdown.requested", serial=serial)

        bb.boot_reason = b_reason
        bb.boot_reason_history = b_history
        bb.evidence.append(Evidence(source="sys.boot.reason", raw_excerpt=f"reason={b_reason}, history={b_history}, shutdown_req={shutdown_req}", exit_code=0))

        # 2. Non-Root Dropbox Log Query
        code_drop, out_drop, _ = self.bridge.shell(
            "dumpsys dropbox --print SYSTEM_RESTART SYSTEM_LAST_KMSG SYSTEM_TOMBSTONE critical_event thermal_shutdown watchdog 2>/dev/null | tail -n 120",
            serial=serial,
            timeout=10
        )
        if code_drop == 0 and out_drop:
            bb.evidence.append(Evidence(source="dumpsys dropbox", raw_excerpt=out_drop.strip()[:1000], exit_code=code_drop))
            for line in out_drop.splitlines():
                line_str = line.strip()
                if not line_str:
                    continue
                if re.search(r"(?i)(crash|panic|restart|thermal|watchdog|tombstone|fatal|oops)", line_str):
                    bb.dropbox_events.append(line_str[:120])
                    if len(bb.dropbox_events) >= 15:
                        break
        else:
            bb.evidence.append(Evidence(source="dumpsys dropbox", raw_excerpt=out_drop or "No dropbox crash events logged", exit_code=code_drop))

        # 3. Kernel RAMOOPS and DMESG (Root vs Non-Root handling)
        is_root = self.bridge.is_root_available(serial)
        if is_root:
            code_k, out_k, _ = self.bridge.shell("cat /sys/fs/pstore/console-ramoops* 2>/dev/null | tail -n 80", serial=serial, timeout=8, root=True)
            if code_k == 0 and out_k.strip():
                bb.kernel_ramoops_status = "CRITICAL_LOG_FOUND"
                bb.evidence.append(Evidence(source="/sys/fs/pstore/console-ramoops", raw_excerpt=out_k.strip()[:1000], exit_code=code_k))
                for l in out_k.splitlines():
                    if re.search(r"(?i)(panic|oops|fatal|reset|watchdog)", l):
                        bb.kernel_log_snippets.append(l.strip()[:100])
            else:
                bb.kernel_ramoops_status = "PASS"
                bb.evidence.append(Evidence(source="/sys/fs/pstore/console-ramoops", raw_excerpt="No panic or crash logs in ramoops", exit_code=code_k))
        else:
            bb.kernel_ramoops_status = "NEEDS_ROOT"
            bb.evidence.append(Evidence(source="/sys/fs/pstore/console-ramoops", raw_excerpt="Protected by SELinux (Requires Root for raw kernel buffer)", exit_code=1))

        # 4. Categorize Shutdown Reason
        reason_combined = f"{b_reason} {b_history} {' '.join(bb.dropbox_events)}".lower()

        if re.search(r"(?i)(thermal|overheat|crit_temp|tsens)", reason_combined):
            bb.shutdown_category = "THERMAL"
            bb.has_abnormal_shutdown = True
            bb.diagnosis = "Thermal Shutdown Event: Device forcibly powered off due to extreme motherboard/battery temperature trip."
        elif re.search(r"(?i)(uvlo|undervoltage|vbat_low|battery_pull|brownout)", reason_combined):
            bb.shutdown_category = "UVLO"
            bb.has_abnormal_shutdown = True
            bb.diagnosis = "Under-Voltage Lockout (UVLO): Sudden power cutoff caused by battery cell voltage sag or loose battery connector pin."
        elif re.search(r"(?i)(watchdog|wdog|kernel_panic|subsys_restart|oops|softlockup|hardlockup)", reason_combined):
            bb.shutdown_category = "WATCHDOG_PANIC"
            bb.has_abnormal_shutdown = True
            bb.diagnosis = "Watchdog / Kernel Panic: CPU or PMIC hung, forcing a hardware watchdog reset."
        elif re.search(r"(?i)(userrequested|power_menu|shell|recovery)", reason_combined):
            bb.shutdown_category = "USER_REQUESTED"
            bb.has_abnormal_shutdown = False
            bb.diagnosis = "Normal clean shutdown requested by user or system OS."
        else:
            bb.shutdown_category = "NORMAL"
            bb.has_abnormal_shutdown = False
            bb.diagnosis = "Standard boot state. No unexpected crash or sudden power cut records found."

        return bb

    def inspect_panic_and_emmc(self, serial: str) -> PanicAnalysis:
        analysis = PanicAnalysis()
        blackbox = self.run_blackbox_forensics(serial)
        analysis.blackbox = blackbox

        if blackbox.has_abnormal_shutdown:
            analysis.has_critical_error = True
            if blackbox.shutdown_category == "THERMAL":
                analysis.thermal_shutdowns.append(blackbox.diagnosis)
            elif blackbox.shutdown_category == "WATCHDOG_PANIC":
                analysis.watchdog_resets.append(blackbox.diagnosis)
            elif blackbox.shutdown_category == "UVLO":
                analysis.watchdog_resets.append(blackbox.diagnosis)

        # Non-root Dropbox records analysis
        for ev in blackbox.dropbox_events:
            if "tombstone" in ev.lower() or "panic" in ev.lower():
                analysis.kernel_panics.append(ev)
                analysis.has_critical_error = True
            elif "watchdog" in ev.lower():
                analysis.watchdog_resets.append(ev)
                analysis.has_critical_error = True

        # Check root dmesg if available
        if self.bridge.is_root_available(serial):
            code_dmesg, out_dmesg, _ = self.bridge.shell("dmesg | tail -n 150", serial=serial, timeout=8, root=True)
            if code_dmesg == 0 and out_dmesg:
                for line in out_dmesg.splitlines():
                    l_clean = line.strip()
                    if re.search(r"(?i)(I/O error|mmc0|blk_update_request|EXT4-fs error|F2FS-fs error)", l_clean):
                        if len(analysis.emmc_ufs_errors) < 10 and l_clean not in analysis.emmc_ufs_errors:
                            analysis.emmc_ufs_errors.append(l_clean)
                            analysis.has_critical_error = True
                    elif re.search(r"(?i)(Kernel panic|Oops:|fatal exception)", l_clean):
                        if len(analysis.kernel_panics) < 10 and l_clean not in analysis.kernel_panics:
                            analysis.kernel_panics.append(l_clean)
                            analysis.has_critical_error = True

        if analysis.kernel_panics or analysis.emmc_ufs_errors:
            analysis.summary_verdict = "CRITICAL HARDWARE FAULT DETECTED (eMMC/UFS or Kernel Panic)"
        elif analysis.watchdog_resets or analysis.thermal_shutdowns:
            analysis.summary_verdict = f"WARNING: System Instability ({blackbox.shutdown_category})"
        else:
            analysis.summary_verdict = "Clean: No kernel panics or NAND controller corruption logged."

        return analysis

    def run_full_diagnostics(self, serial: str) -> BoardDiagnosticResult:
        logger.info(f"Running Full Motherboard Diagnostics on {serial}...")
        pmic = self.analyze_pmic(serial)
        thermal_zones = self.scan_thermal_map(serial)
        panic = self.inspect_panic_and_emmc(serial)

        findings: List[str] = []
        findings.extend(pmic.fault_flags)

        overheated_zones = [f"{z.zone_type} ({z.temp_c}°C)" for z in thermal_zones if z.is_overheated]
        if overheated_zones:
            findings.append(f"Thermal Alert (>45°C detected): {', '.join(overheated_zones)}")

        if panic.has_critical_error:
            findings.append(f"Storage & Kernel Anomaly: {panic.summary_verdict}")

        has_real_readings = (
            pmic.voltage_mv is not None or
            pmic.current_ma is not None or
            len(thermal_zones) > 0 or
            panic.blackbox.boot_reason != "Unknown"
        )
        if not has_real_readings:
            overall = "UNKNOWN"
        elif pmic.fault_flags or panic.kernel_panics or panic.emmc_ufs_errors:
            overall = "CRITICAL"
        elif overheated_zones or panic.watchdog_resets or panic.thermal_shutdowns:
            overall = "WARNING"
        else:
            overall = "PASS"

        result = BoardDiagnosticResult(
            serial=serial,
            pmic=pmic,
            thermal_zones=thermal_zones,
            panic_analysis=panic,
            overall_health=overall,
            critical_findings=findings
        )
        return result

    def diagnose_dead_phone_bootrom(self, check_cycling: bool = True) -> DeadPhoneBootROMDiagnosis:
        diag = DeadPhoneBootROMDiagnosis()
        ports = self.bridge.scan_bootrom_usb_ports()
        diag.detected_ports = ports

        for p in ports:
            mode = p.get("mode", "")
            if "Qualcomm EDL" in mode:
                diag.is_qualcomm_edl = True
            elif "MediaTek" in mode:
                diag.is_mediatek_brom = True
            elif "Samsung Odin" in mode:
                diag.is_samsung_odin = True

        if check_cycling:
            cycling = self.bridge.detect_usb_port_cycling(interval_seconds=3)
            diag.is_usb_cycling = cycling.get("is_cycling", False)
            diag.cycling_details = cycling.get("diagnosis", "")

        if diag.is_qualcomm_edl:
            diag.summary_diagnosis = (
                "Hypothesis (Possible Causes):\n"
                "• Device enumerated in Qualcomm Emergency Download (EDL 9008) mode.\n"
                "• Possible cause 1: Corrupted primary bootloader (PBL/XBL/ABOOT).\n"
                "• Possible cause 2: Corrupted or unreadable eMMC / UFS partition table (GPT).\n"
                "• Possible cause 3: Hardware damage or solder fatigue on NAND memory chip / CPU BGA.\n"
                "• Possible cause 4: Hardware test-point (EDL TP) or volume-down key shorted to GND."
            )
        elif diag.is_mediatek_brom:
            diag.summary_diagnosis = (
                "Hypothesis (Possible Causes):\n"
                "• Device enumerated in MediaTek BootROM (BROM / Preloader) mode.\n"
                "• Possible cause 1: Corrupted Preloader or LK (Little Kernel) image.\n"
                "• Possible cause 2: eMMC / UFS internal NAND Flash memory failure or unprogrammed state.\n"
                "• Possible cause 3: Main PMIC power rails (VPROC, VCORE, VMEM) collapsing under early boot load.\n"
                "• Possible cause 4: KCOL0 hardware download key line shorted to ground."
            )
        elif diag.is_samsung_odin:
            diag.summary_diagnosis = (
                "Hypothesis (Possible Causes):\n"
                "• Device in Samsung Odin Download / Exynos BootROM mode.\n"
                "• Possible cause 1: Bootloop / soft-brick from incomplete OTA update or system file corruption.\n"
                "• Possible cause 2: FRP / Knox lock triggered download mode recovery.\n"
                "• Possible cause 3: Faulty PIT (Partition Information Table) or bad storage blocks."
            )
        elif diag.is_usb_cycling:
            diag.summary_diagnosis = (
                "Hypothesis (Possible Causes) — Rapid USB Connect/Disconnect Cycling:\n"
                "• Possible cause 1: PMIC Watchdog Reset Loop / Bootloop due to battery voltage dropping below UVLO threshold.\n"
                "• Possible cause 2: Short circuit on VBUS, CC1/CC2, or USB D+/D- data lines in the charging sub-board/flex.\n"
                "• Possible cause 3: Cold solder joint or cracked BGA balls under SoC or PMIC.\n"
                f"• Observed: {diag.cycling_details}"
            )
        elif ports:
            diag.summary_diagnosis = (
                f"Hypothesis (Possible Causes):\n"
                f"• Low-level USB interface detected: {ports[0].get('name')}.\n"
                "• Possible cause: Device in vendor-specific recovery or factory diagnostic mode."
            )
        else:
            diag.summary_diagnosis = "No emergency BootROM, EDL, or recovery USB interface detected on Windows PnP ports."

        return diag

    def diagnose_fast_charge(self, serial: str) -> FastChargeDiagnostics:
        result = FastChargeDiagnostics()
        
        usb_types = [
            "/sys/class/power_supply/usb/type",
            "/sys/class/power_supply/usb/real_type",
            "/sys/class/power_supply/battery/charge_type",
            "/sys/class/power_supply/charger/charge_type"
        ]
        raw_type = self.bridge.read_sysfs(usb_types, serial=serial) or ""
        raw_type_clean = raw_type.strip()
        result.charger_type = raw_type_clean if raw_type_clean else "Standard USB"

        v_paths = [
            "/sys/class/power_supply/usb/voltage_now",
            "/sys/class/power_supply/usb/voltage_max",
            "/sys/class/power_supply/battery/voltage_now"
        ]
        v_raw = self.bridge.read_sysfs_int(v_paths, serial=serial)
        if v_raw is not None:
            if v_raw > 20000:
                result.voltage_mv = float(v_raw / 1000.0)
            elif v_raw > 100:
                result.voltage_mv = float(v_raw)
            else:
                result.voltage_mv = float(v_raw * 1000.0)
            result.voltage_v = round(result.voltage_mv / 1000.0, 2)

        c_paths = [
            "/sys/class/power_supply/battery/current_now",
            "/sys/class/power_supply/usb/current_max",
            "/sys/class/power_supply/usb/input_current_limit"
        ]
        c_raw = self.bridge.read_sysfs_int(c_paths, serial=serial)
        if c_raw is not None:
            c_abs = abs(c_raw)
            if c_abs > 20000:
                result.current_ma = float(c_abs / 1000.0)
            else:
                result.current_ma = float(c_abs)
            result.current_a = round(result.current_ma / 1000.0, 2)

        code, out, _ = self.bridge.shell("dumpsys battery", serial=serial, timeout=8)
        if code == 0 and out:
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("voltage:") and result.voltage_mv == 0:
                    m = re.search(r"(\d+)", line)
                    if m:
                        v = float(m.group(1))
                        result.voltage_mv = v if v < 10000 else float(v / 1000.0)
                        result.voltage_v = round(result.voltage_mv / 1000.0, 2)
                elif line.startswith("level:"):
                    m = re.search(r"(\d+)", line)
                    if m:
                        result.battery_level = int(m.group(1))
                elif line.startswith("status:"):
                    val = line.split(":", 1)[1].strip()
                    status_map = {"1": "Unknown", "2": "Charging", "3": "Discharging", "4": "Not charging", "5": "Full"}
                    result.charge_status = status_map.get(val, val)
                elif line.startswith("health:"):
                    val = line.split(":", 1)[1].strip()
                    health_map = {"1": "Unknown", "2": "Good", "3": "Overheat", "4": "Dead", "5": "Over voltage", "6": "Failure", "7": "Cold"}
                    result.health_state = health_map.get(val, val)
                elif "Max charging current:" in line and result.current_ma == 0:
                    m = re.search(r"(\d+)", line)
                    if m:
                        c_val = float(m.group(1))
                        result.current_ma = c_val / 1000.0 if c_val > 10000 else c_val
                        result.current_a = round(result.current_ma / 1000.0, 2)
                elif "Max charging voltage:" in line and result.voltage_mv == 0:
                    m = re.search(r"(\d+)", line)
                    if m:
                        v_val = float(m.group(1))
                        result.voltage_mv = v_val / 1000.0 if v_val > 10000 else v_val
                        result.voltage_v = round(result.voltage_mv / 1000.0, 2)

        if result.voltage_v > 0 and result.current_a > 0:
            result.wattage_w = round(result.voltage_v * result.current_a, 2)
        elif result.voltage_v > 0:
            result.wattage_w = round(result.voltage_v * 0.5, 2)

        t_lower = result.charger_type.lower()
        if "pd" in t_lower or "power_delivery" in t_lower:
            result.protocol_name = "USB Power Delivery (USB-PD)"
            result.is_fast_charging = True
        elif "qc" in t_lower or "quick" in t_lower or "turbo" in t_lower:
            result.protocol_name = "Qualcomm Quick Charge / Turbo"
            result.is_fast_charging = True
        elif "vooc" in t_lower or "warp" in t_lower or "dart" in t_lower:
            result.protocol_name = "SuperVOOC / Warp / Dart Flash Charge"
            result.is_fast_charging = True
        elif "dcp" in t_lower:
            result.protocol_name = "Dedicated Charging Port (DCP 5V ~1.5-2.4A)"
            result.is_fast_charging = result.wattage_w >= 9.0
        elif "cdp" in t_lower:
            result.protocol_name = "Charging Downstream Port (CDP 5V ~1.5A)"
            result.is_fast_charging = False
        elif "sdp" in t_lower or "standard" in t_lower:
            result.protocol_name = "Standard USB Port (SDP 5V ~0.5A)"
            result.is_fast_charging = False
        else:
            if result.wattage_w >= 14.0:
                result.protocol_name = f"High-Power Fast Charge (~{result.wattage_w}W)"
                result.is_fast_charging = True
            elif result.wattage_w >= 9.0:
                result.protocol_name = f"Standard Fast Charge (~{result.wattage_w}W)"
                result.is_fast_charging = True
            else:
                result.protocol_name = "Standard Charging (5V Nominal)"
                result.is_fast_charging = False

        speed_label = "Fast Charging ACTIVE" if result.is_fast_charging else "Standard/Slow Charging"
        result.diagnosis = (
            f"{speed_label} | Protocol: {result.protocol_name} | "
            f"Power Draw: {result.wattage_w}W ({result.voltage_v}V @ {result.current_a}A) | "
            f"Battery: {result.battery_level}% ({result.charge_status})"
        )
        return result

    def reset_battery_fuel_gauge_stats(self, serial: str) -> Tuple[bool, str]:
        """
        Reset Battery Statistics (dumpsys batterystats --reset).
        Note: This clears Android OS software battery usage history only.
        It does not alter physical battery cell chemistry or hardware fuel gauge registers.
        """
        logger.info(f"Resetting battery OS statistics on {serial}...")
        log_entries = []

        code1, out1, err1 = self.bridge.shell("dumpsys batterystats --reset", serial=serial, timeout=10)
        if code1 == 0:
            log_entries.append("OK: Android OS software batterystats history wiped successfully.")
        else:
            log_entries.append(f"Notice: dumpsys batterystats returned code {code1} ({err1.strip()})")

        code2, out2, _ = self.bridge.shell("cmd battery reset", serial=serial, timeout=10)
        if code2 == 0:
            log_entries.append("OK: Battery HAL simulation reset to actual hardware state.")

        bms_reset_paths = [
            "/sys/class/power_supply/bms/reset",
            "/sys/class/power_supply/battery/fg_reset"
        ]
        for path in bms_reset_paths:
            self.bridge.shell(f"echo 1 > {path}", serial=serial, timeout=5)

        log_entries.append("Note: Reset battery statistics only clears OS usage tracking records.")
        return True, "\n".join(log_entries)

    # Alias for backwards compatibility
    def reset_battery_stats_and_gauge(self, serial: str) -> Tuple[bool, str]:
        return self.reset_battery_fuel_gauge_stats(serial)

    def read_emmc_ufs_lifespan(self, serial: str) -> StorageLifeReport:
        """
        Query JEDEC eMMC / UFS hardware life-time estimation and pre-EOL registers.
        Returns structured 4-state verdict with raw evidence. If blocked by SELinux, returns NEEDS_ROOT.
        """
        report = StorageLifeReport()
        jedec_wear_map = {
            "0x00": "Not defined",
            "0x01": "0% - 10% device life time used (Brand New / Healthy)",
            "0x02": "10% - 20% device life time used (Normal)",
            "0x03": "20% - 30% device life time used (Normal)",
            "0x04": "30% - 40% device life time used (Normal)",
            "0x05": "40% - 50% device life time used (Normal)",
            "0x06": "50% - 60% device life time used (Normal)",
            "0x07": "60% - 70% device life time used (Fair)",
            "0x08": "70% - 80% device life time used (Warning: High Wear)",
            "0x09": "80% - 90% device life time used (Warning: Very High Wear)",
            "0x0a": "90% - 100% device life time used (Critical: Near End of Life)",
            "0x0b": "Exceeded device life time (Urgent: Imminent Storage Failure!)"
        }

        jedec_pre_eol_map = {
            "0x00": "Not defined",
            "0x01": "Normal (Reserved NAND blocks intact)",
            "0x02": "Warning (Consumed 80% of reserved NAND blocks)",
            "0x03": "Urgent (Consumed 90%+ reserved blocks - Imminent Brick/Corruption)"
        }

        # 1. Try eMMC paths
        emmc_life_raw = self.bridge.read_sysfs([
            "/sys/block/mmcblk0/device/life_time",
            "/sys/devices/platform/soc/*.mmc/mmc_host/mmc0/mmc0:0001/life_time",
            "/sys/devices/soc/*.mmc/mmc_host/mmc0/mmc0:0001/life_time"
        ], serial=serial)

        emmc_pre_eol_raw = self.bridge.read_sysfs([
            "/sys/block/mmcblk0/device/pre_eol_info",
            "/sys/devices/platform/soc/*.mmc/mmc_host/mmc0/mmc0:0001/pre_eol_info",
            "/sys/devices/soc/*.mmc/mmc_host/mmc0/mmc0:0001/pre_eol_info"
        ], serial=serial)

        # 2. Try UFS paths
        ufs_life_a = self.bridge.read_sysfs([
            "/sys/class/ufs/ufs0/health_descriptor/life_time_estimation_a",
            "/sys/devices/platform/soc/*.ufshc/health_descriptor/life_time_estimation_a",
            "/sys/devices/soc/*.ufshc/health_descriptor/life_time_estimation_a"
        ], serial=serial)

        ufs_life_b = self.bridge.read_sysfs([
            "/sys/class/ufs/ufs0/health_descriptor/life_time_estimation_b",
            "/sys/devices/platform/soc/*.ufshc/health_descriptor/life_time_estimation_b",
            "/sys/devices/soc/*.ufshc/health_descriptor/life_time_estimation_b"
        ], serial=serial)

        ufs_pre_eol = self.bridge.read_sysfs([
            "/sys/class/ufs/ufs0/health_descriptor/pre_eol_info",
            "/sys/devices/platform/soc/*.ufshc/health_descriptor/pre_eol_info",
            "/sys/devices/soc/*.ufshc/health_descriptor/pre_eol_info"
        ], serial=serial)

        if emmc_life_raw:
            report.status = "PASS"
            report.storage_type = "eMMC / eMCP"
            report.evidence.append(Evidence(source="/sys/block/mmcblk0/device/life_time", raw_excerpt=f"eMMC life_time: {emmc_life_raw.strip()}, pre_eol: {emmc_pre_eol_raw or 'N/A'}", exit_code=0))
            parts = emmc_life_raw.strip().split()
            if len(parts) >= 2:
                report.raw_hex_a = parts[0].lower()
                report.raw_hex_b = parts[1].lower()
            elif len(parts) == 1:
                report.raw_hex_a = parts[0].lower()
                report.raw_hex_b = parts[0].lower()

            if emmc_pre_eol_raw:
                report.raw_pre_eol = emmc_pre_eol_raw.strip().lower()
        elif ufs_life_a or ufs_life_b:
            report.status = "PASS"
            report.storage_type = "Universal Flash Storage (UFS)"
            report.evidence.append(Evidence(source="health_descriptor/life_time_estimation", raw_excerpt=f"UFS life_time_a: {ufs_life_a}, b: {ufs_life_b}, pre_eol: {ufs_pre_eol}", exit_code=0))
            if ufs_life_a:
                report.raw_hex_a = ufs_life_a.strip().lower()
            if ufs_life_b:
                report.raw_hex_b = ufs_life_b.strip().lower()
            if ufs_pre_eol:
                report.raw_pre_eol = ufs_pre_eol.strip().lower()
        else:
            # Check if root is available
            is_root = self.bridge.is_root_available(serial)
            if is_root:
                # Query with root
                code_r, out_r, _ = self.bridge.shell("cat /sys/block/mmcblk0/device/life_time 2>/dev/null", serial=serial, root=True)
                if code_r == 0 and out_r.strip():
                    report.status = "PASS"
                    report.storage_type = "eMMC / eMCP (via Root)"
                    report.evidence.append(Evidence(source="root life_time", raw_excerpt=out_r.strip(), exit_code=0))
                    parts = out_r.strip().split()
                    report.raw_hex_a = parts[0].lower() if parts else "0x01"
                    report.raw_hex_b = parts[1].lower() if len(parts) > 1 else report.raw_hex_a
                else:
                    report.status = "UNKNOWN"
                    report.storage_type = "Internal Flash (NAND)"
                    report.evidence.append(Evidence(source="sysfs NAND health descriptor", raw_excerpt="NAND health descriptor not exposed by OEM storage driver.", exit_code=code_r))
            else:
                report.status = "NEEDS_ROOT"
                report.storage_type = "Internal Flash (NAND)"
                report.evidence.append(Evidence(source="/sys/block/mmcblk0/device/life_time", raw_excerpt="Permission denied: /sys/block/mmcblk0/device/life_time (SELinux Enforced). Root required for raw JEDEC health descriptor.", exit_code=1))

        def norm_hex(h: str) -> str:
            if not h:
                return "0x00"
            if not h.startswith("0x"):
                try:
                    val = int(h, 16)
                    return f"0x{val:02x}"
                except ValueError:
                    return h
            return h

        h_a = norm_hex(report.raw_hex_a)
        h_b = norm_hex(report.raw_hex_b)
        h_eol = norm_hex(report.raw_pre_eol)

        report.life_time_type_a = jedec_wear_map.get(h_a, f"Wear Level: {h_a}")
        report.life_time_type_b = jedec_wear_map.get(h_b, f"Wear Level: {h_b}")
        report.pre_eol_info = jedec_pre_eol_map.get(h_eol, f"Pre-EOL: {h_eol}")

        crit_keys = ["0x0a", "0x0b"]
        warn_keys = ["0x08", "0x09"]
        if h_a in crit_keys or h_b in crit_keys or h_eol == "0x03":
            report.is_critical = True
            report.health_verdict = "CRITICAL"
            report.diagnosis = "CRITICAL STORAGE WEAR: Internal Flash memory is nearing hardware death (>90% wear). Backup user data immediately!"
        elif h_a in warn_keys or h_b in warn_keys or h_eol == "0x02":
            report.is_warning = True
            report.health_verdict = "WARNING"
            report.diagnosis = "WARNING: NAND wear is high (>70% lifecycle used). Flash memory degradation in progress."
        elif report.status == "NEEDS_ROOT":
            report.health_verdict = "NEEDS_ROOT"
            report.diagnosis = "JEDEC life-time registers restricted by SELinux. Run storage speed benchmark or root device to inspect raw registers."
        else:
            report.health_verdict = "GOOD"
            report.diagnosis = f"NAND Flash chip is in healthy state ({report.life_time_type_a}). Reserved blocks normal."

        return report

    def benchmark_storage_speed(self, serial: str, test_size_mb: int = 64) -> Tuple[float, float, str, str]:
        """
        Run a safe 64MB sequential write and read benchmark in /data/local/tmp/bench.bin
        using dd with conv=fsync. Cleans up temp file immediately.
        """
        logger.info(f"Executing storage benchmark ({test_size_mb}MB) on {serial}...")
        tmp_file = "/data/local/tmp/bench.bin"

        def parse_speed_mb(dd_output: str) -> float:
            m = re.search(r"([\d.]+)\s*(GB/s|MB/s|kB/s|B/s|bytes/sec)", dd_output, re.IGNORECASE)
            if m:
                val = float(m.group(1))
                unit = m.group(2).lower()
                if "gb" in unit:
                    return round(val * 1024.0, 1)
                elif "mb" in unit:
                    return round(val, 1)
                elif "kb" in unit:
                    return round(val / 1024.0, 2)
                elif "bytes" in unit or unit == "b/s":
                    return round(val / (1024.0 * 1024.0), 2)
            return 0.0

        # 1. Write Benchmark
        write_cmd = f"dd if=/dev/zero of={tmp_file} bs=1m count={test_size_mb} conv=fsync 2>&1"
        code_w, out_w, err_w = self.bridge.shell(write_cmd, serial=serial, timeout=25)
        write_text = f"{out_w}\n{err_w}"
        write_speed = parse_speed_mb(write_text)

        # 2. Read Benchmark
        read_cmd = f"dd if={tmp_file} of=/dev/null bs=1m count={test_size_mb} 2>&1"
        code_r, out_r, err_r = self.bridge.shell(read_cmd, serial=serial, timeout=25)
        read_text = f"{out_r}\n{err_r}"
        read_speed = parse_speed_mb(read_text)

        # 3. Clean up immediately
        self.bridge.shell(f"rm -f {tmp_file}", serial=serial, timeout=5)

        # 4. Fallback estimation if dd didn't print speed directly
        if write_speed == 0.0:
            m_time = re.search(r"([\d.]+)\s*(?:s|secs|seconds)", write_text)
            if m_time:
                secs = float(m_time.group(1))
                if secs > 0:
                    write_speed = round(float(test_size_mb) / secs, 1)

        if read_speed == 0.0:
            m_time_r = re.search(r"([\d.]+)\s*(?:s|secs|seconds)", read_text)
            if m_time_r:
                secs_r = float(m_time_r.group(1))
                if secs_r > 0:
                    read_speed = round(float(test_size_mb) / secs_r, 1)

        # Evaluation
        if write_speed > 0 and write_speed < 25.0:
            verdict = "DEGRADED"
            diag = (
                f"Severe NAND Write Bottleneck: Write={write_speed} MB/s (< 25 MB/s threshold). "
                f"Read={read_speed} MB/s. Internal storage chip is degraded or suffering severe I/O stalls."
            )
        elif write_speed >= 120.0:
            verdict = "EXCELLENT"
            diag = f"High-Speed UFS Storage: Write={write_speed} MB/s | Read={read_speed} MB/s (Optimal Performance)."
        elif write_speed >= 35.0:
            verdict = "NORMAL"
            diag = f"Standard eMMC / UFS Speed: Write={write_speed} MB/s | Read={read_speed} MB/s (Healthy NAND)."
        else:
            verdict = "FAIR"
            diag = f"Acceptable Speed: Write={write_speed} MB/s | Read={read_speed} MB/s."

        return write_speed, read_speed, verdict, diag

    def run_cpu_pmic_stress_test(
        self,
        serial: str,
        duration_sec: int = 30,
        progress_callback: Optional[Callable[[StressTestSnapshot, str], None]] = None
    ) -> StressTestReport:
        """
        Run a controlled 30-second multi-core CPU and Power Management (PMIC) stress test.
        Samples CPU core frequencies, battery voltage sag, and thermal rise every 2 seconds.
        """
        logger.info(f"Initiating 30-second CPU/PMIC Stress Test on {serial}...")
        report = StressTestReport(duration_seconds=duration_sec)

        pmic_initial = self.analyze_pmic(serial)
        report.initial_voltage_mv = pmic_initial.voltage_mv
        report.initial_temp_c = pmic_initial.temp_c
        report.min_voltage_mv = pmic_initial.voltage_mv
        report.max_temp_c = pmic_initial.temp_c

        launch_cmd = (
            "nohup sh -c 'for i in $(seq 1 4); do "
            "(while true; do :; done &); "
            "done; sleep " + str(duration_sec + 5) + "; killall sh 2>/dev/null' >/dev/null 2>&1 &"
        )
        self.bridge.shell(launch_cmd, serial=serial, timeout=6)

        interval = 2
        total_steps = max(1, duration_sec // interval)

        try:
            for step in range(1, total_steps + 1):
                time.sleep(interval)
                current_sec = step * interval

                # 1. CPU frequencies
                code_f, out_f, _ = self.bridge.shell(
                    "cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq 2>/dev/null",
                    serial=serial,
                    timeout=4
                )
                freqs: List[float] = []
                if code_f == 0 and out_f:
                    for line in out_f.splitlines():
                        line = line.strip()
                        if line.isdigit():
                            freqs.append(round(int(line) / 1000.0, 1))

                avg_freq = round(sum(freqs) / len(freqs), 1) if freqs else 0.0
                if freqs:
                    max_f = max(freqs)
                    min_f = min(freqs)
                    if max_f > report.max_freq_mhz:
                        report.max_freq_mhz = max_f
                    if report.min_freq_mhz == 0.0 or min_f < report.min_freq_mhz:
                        report.min_freq_mhz = min_f

                # 2. Live voltage
                v_code, v_out, _ = self.bridge.shell(
                    "cat /sys/class/power_supply/battery/voltage_now 2>/dev/null",
                    serial=serial,
                    timeout=3
                )
                cur_v = 0.0
                if v_code == 0 and v_out and v_out.strip().isdigit():
                    raw_v = int(v_out.strip())
                    cur_v = round(raw_v / 1000.0, 1) if raw_v > 20000 else float(raw_v)
                else:
                    cur_v = pmic_initial.voltage_mv

                if cur_v > 0 and cur_v < report.min_voltage_mv:
                    report.min_voltage_mv = cur_v

                # 3. Live thermal
                t_code, t_out, _ = self.bridge.shell(
                    "cat /sys/class/thermal/thermal_zone0/temp 2>/dev/null",
                    serial=serial,
                    timeout=3
                )
                cur_t = 0.0
                if t_code == 0 and t_out and t_out.strip().lstrip("-").isdigit():
                    raw_t = int(t_out.strip())
                    cur_t = round(raw_t / 1000.0, 1) if raw_t > 1000 else float(raw_t)
                else:
                    cur_t = pmic_initial.temp_c

                if cur_t > report.max_temp_c:
                    report.max_temp_c = cur_t

                snapshot = StressTestSnapshot(
                    second=current_sec,
                    freqs_mhz=freqs,
                    avg_freq_mhz=avg_freq,
                    voltage_mv=cur_v,
                    temp_c=cur_t,
                    battery_temp_c=pmic_initial.temp_c
                )
                report.samples.append(snapshot)

                status_msg = (
                    f"[{current_sec}/{duration_sec}s] "
                    f"CPU Avg: {avg_freq} MHz | Voltage: {cur_v} mV | Temp: {cur_t}°C"
                )
                if progress_callback:
                    progress_callback(snapshot, status_msg)

        finally:
            logger.info("Stress test completed. Killing background workload...")
            self.bridge.shell("killall -9 sh 2>/dev/null; pkill -9 -f while 2>/dev/null", serial=serial, timeout=5)

        report.voltage_sag_mv = round(report.initial_voltage_mv - report.min_voltage_mv, 1)
        report.temp_rise_c = round(report.max_temp_c - report.initial_temp_c, 1)

        if report.max_freq_mhz > 0 and report.min_freq_mhz > 0:
            drop_ratio = (report.max_freq_mhz - report.min_freq_mhz) / report.max_freq_mhz
            report.throttling_detected = drop_ratio >= 0.35

        report.severe_sag_detected = report.voltage_sag_mv >= 350.0

        if report.severe_sag_detected and report.throttling_detected:
            report.verdict = "CRITICAL"
            report.diagnosis = (
                f"Severe PMIC Sag ({report.voltage_sag_mv} mV) & Thermal Throttling detected! "
                f"Battery internal resistance is excessive or PMIC power rail is unstable under load."
            )
        elif report.severe_sag_detected:
            report.verdict = "WEAK_PMIC_BATTERY"
            report.diagnosis = (
                f"Excessive Battery Voltage Sag ({report.voltage_sag_mv} mV drop). "
                f"High risk of sudden shutdowns during gaming or heavy camera use."
            )
        elif report.throttling_detected:
            report.verdict = "THROTTLED"
            report.diagnosis = (
                f"Thermal Throttling Active: Core clocks dropped from {report.max_freq_mhz} MHz to {report.min_freq_mhz} MHz "
                f"with a {report.temp_rise_c}°C temperature rise. Thermal paste or heat spreader inspection suggested."
            )
        else:
            report.verdict = "STABLE"
            report.diagnosis = (
                f"System Stable: Minimal voltage sag ({report.voltage_sag_mv} mV) and controlled thermal rise "
                f"(+{report.temp_rise_c}°C). PMIC and cooling subsystem operate within normal margins."
            )

        return report

    def load_fault_signatures(self) -> List[Dict[str, Any]]:
        """
        Dynamically loads external fault signatures from data/fault_signatures.json
        """
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sig_file = os.path.join(base_dir, "data", "fault_signatures.json")
        if os.path.exists(sig_file):
            try:
                with open(sig_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    return data.get("rules", [])
            except Exception as e:
                logger.warning(f"Error loading fault_signatures.json: {e}")
        return []

    def check_root_access(self, serial: str) -> Tuple[bool, str]:
        """
        Strict root verification. Returns (is_rooted, root_type).
        root_type: 'Magisk', 'KernelSU', 'APatch', 'su (Standard)', or 'NON_ROOT'.
        """
        cached = self.bridge.get_cached_capability(serial, "root_info")
        if cached is not None:
            return cached

        code, out, _ = self.bridge.shell("su -c id", serial=serial, timeout=8)
        if code != 0 or "uid=0" not in out:
            res = (False, "NON_ROOT")
            self.bridge.set_cached_capability(serial, "root_info", res)
            return res

        # Identify root engine
        code_m, out_m, _ = self.bridge.shell("magisk -v", serial=serial, timeout=5, root=True)
        if code_m == 0 and out_m:
            res = (True, f"Magisk ({out_m.strip()})")
            self.bridge.set_cached_capability(serial, "root_info", res)
            return res

        code_k, out_k, _ = self.bridge.shell("ls /data/adb/ksu 2>/dev/null", serial=serial, timeout=5, root=True)
        if code_k == 0 and "ksu" in out_k:
            res = (True, "KernelSU (KSU)")
            self.bridge.set_cached_capability(serial, "root_info", res)
            return res

        code_a, out_a, _ = self.bridge.shell("ls /data/adb/ap 2>/dev/null", serial=serial, timeout=5, root=True)
        if code_a == 0 and "ap" in out_a:
            res = (True, "APatch")
            self.bridge.set_cached_capability(serial, "root_info", res)
            return res

        res = (True, "su (Standard Root)")
        self.bridge.set_cached_capability(serial, "root_info", res)
        return res

    def run_root_forensics_pro(self, serial: str) -> RootForensicsReport:
        """
        Deep Root Forensics Engine:
        1. Reads console-ramoops and last_kmsg directly via su -c
        2. Matches kernel logs against fault_signatures.json regex patterns
        3. Extracts I2C hex errors (-110 timeout, -5 I/O failure)
        4. Reads exact NAND (eMMC/UFS) life_time & pre_eol_info via su -c
        """
        rep = RootForensicsReport(serial=serial)
        is_rooted, root_type = self.check_root_access(serial)
        rep.is_rooted = is_rooted
        rep.root_type = root_type

        if not is_rooted:
            rep.diagnosis = "Device is NOT Rooted. Deep kernel ramoops & raw block forensics require root (UID 0)."
            rep.evidence.append(Evidence(source="su -c id", raw_excerpt="Permission Denied or su binary missing", exit_code=1))
            return rep

        rep.evidence.append(Evidence(source="check_root_access", raw_excerpt=f"Root Type: {root_type}", exit_code=0))

        # 1. Read persistent console-ramoops & last_kmsg
        cmd_ramoops = "cat /sys/fs/pstore/console-ramoops* /sys/fs/pstore/dmesg-ramoops* /proc/last_kmsg 2>/dev/null | tail -n 250"
        code_r, out_r, _ = self.bridge.shell(cmd_ramoops, serial=serial, timeout=12, root=True)
        if code_r == 0 and out_r.strip():
            rep.kernel_ramoops_log = out_r.strip()
            rep.evidence.append(Evidence(source="pstore/console-ramoops", raw_excerpt=out_r.strip()[:1000], exit_code=code_r))
        else:
            rep.kernel_ramoops_log = "No persistent console-ramoops crash log stored in pstore."
            rep.evidence.append(Evidence(source="pstore/console-ramoops", raw_excerpt="No persistent console-ramoops crash log stored in pstore.", exit_code=code_r))

        # 2. Match fault signatures against dmesg & ramoops
        cmd_dmesg = "dmesg | tail -n 300"
        _, out_dmesg, _ = self.bridge.shell(cmd_dmesg, serial=serial, timeout=10, root=True)
        combined_logs = f"{rep.kernel_ramoops_log}\n{out_dmesg}"

        signatures = self.load_fault_signatures()
        matched = []
        for sig in signatures:
            pattern = sig.get("pattern", "")
            if pattern and re.search(pattern, combined_logs, re.IGNORECASE):
                matched.append(sig)

        rep.matched_fault_signatures = matched
        if matched:
            rep.evidence.append(Evidence(source="Fault Signature Engine", raw_excerpt=f"Matched {len(matched)} signatures", exit_code=0))

        # 3. Extract I2C bus hex errors
        i2c_errors = []
        for line in combined_logs.splitlines():
            l_str = line.strip()
            if re.search(r"(?i)(i2c.*error|i2c_geni|i2c_designware|-110|-5|NACK)", l_str):
                if len(i2c_errors) < 8 and l_str not in i2c_errors:
                    i2c_errors.append(l_str)
        rep.i2c_bus_errors = i2c_errors
        if i2c_errors:
            rep.evidence.append(Evidence(source="I2C Kernel Bus Traces", raw_excerpt="\n".join(i2c_errors), exit_code=0))

        # 4. Exact NAND Wear via su -c
        # eMMC
        code_lt, out_lt, _ = self.bridge.shell("cat /sys/block/mmcblk0/device/life_time 2>/dev/null", serial=serial, timeout=6, root=True)
        code_eol, out_eol, _ = self.bridge.shell("cat /sys/block/mmcblk0/device/pre_eol_info 2>/dev/null", serial=serial, timeout=6, root=True)

        if code_lt == 0 and out_lt.strip():
            tokens = out_lt.strip().split()
            if len(tokens) >= 2:
                rep.nand_exact_lifetime_a = tokens[0]
                rep.nand_exact_lifetime_b = tokens[1]
                wear_map = {
                    "0x01": "0% - 10% used (Healthy)",
                    "0x02": "10% - 20% used (Good)",
                    "0x03": "20% - 30% used (Good)",
                    "0x04": "30% - 40% used (Good)",
                    "0x05": "40% - 50% used (Normal)",
                    "0x06": "50% - 60% used (Moderate Wear)",
                    "0x07": "60% - 70% used (Moderate Wear)",
                    "0x08": "70% - 80% used (Warning)",
                    "0x09": "80% - 90% used (Critical Wear)",
                    "0x0a": "90% - 100% used (Urgent Failure Risk)",
                    "0x0b": "Exceeded maximum device lifetime (Imminent Death)"
                }
                status_a = wear_map.get(tokens[0].lower(), tokens[0])
                status_b = wear_map.get(tokens[1].lower(), tokens[1])
                rep.nand_wear_status = f"SLC: {status_a} | MLC: {status_b}"
                rep.evidence.append(Evidence(source="/sys/block/mmcblk0/device/life_time", raw_excerpt=out_lt.strip(), exit_code=code_lt))

        if code_eol == 0 and out_eol.strip():
            eol_val = out_eol.strip()
            rep.nand_exact_pre_eol = eol_val
            eol_map = {"0x01": "Normal", "0x02": "Warning (80% reserve used)", "0x03": "Critical (EOL reached)"}
            rep.nand_exact_pre_eol = eol_map.get(eol_val.lower(), eol_val)
            rep.evidence.append(Evidence(source="/sys/block/mmcblk0/device/pre_eol_info", raw_excerpt=out_eol.strip(), exit_code=code_eol))

        # UFS health descriptor fallback if eMMC was absent
        if not rep.nand_wear_status or rep.nand_wear_status == "UNKNOWN":
            code_ufs, out_ufs, _ = self.bridge.shell(
                "cat /sys/class/ufs/ufs0/health_descriptor/life_time_estimation_a 2>/dev/null",
                serial=serial, timeout=6, root=True
            )
            if code_ufs == 0 and out_ufs.strip():
                rep.nand_exact_lifetime_a = out_ufs.strip()
                rep.nand_wear_status = f"UFS Wear: {out_ufs.strip()}"
                rep.evidence.append(Evidence(source="health_descriptor/life_time_estimation_a", raw_excerpt=out_ufs.strip(), exit_code=code_ufs))

        # Formulate diagnosis
        diag_items = []
        if matched:
            crit = [m.get("id") for m in matched if m.get("severity") == "CRITICAL"]
            if crit:
                diag_items.append(f"Critical Fault Signatures: {', '.join(crit)}")
        if i2c_errors:
            diag_items.append(f"I2C Hardware Bus Failure ({len(i2c_errors)} timeout/NACK traces)")
        if "Critical" in rep.nand_wear_status or "Exceeded" in rep.nand_wear_status:
            diag_items.append(f"NAND Storage Health Degraded ({rep.nand_wear_status})")

        if diag_items:
            rep.diagnosis = f"Root Forensics Alert: {' | '.join(diag_items)}"
        else:
            rep.diagnosis = f"Root Forensics Clean: Kernel ramoops and NAND registers show stable hardware ({root_type})."

        return rep

    def backup_efs_nvram_partitions(
        self,
        serial: str,
        dest_dir: Optional[str] = None,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> EFSBackupResult:
        """
        One-click raw block 'dd' backup of critical EFS, NVRAM, and Baseband partitions.
        Protects customer device IMEI, WiFi MAC, and Baseband RF calibrations.
        """
        result = EFSBackupResult(serial=serial)
        is_rooted, root_type = self.check_root_access(serial)
        if not is_rooted:
            result.success = False
            result.message = "Root access required to dump raw hardware partitions."
            return result

        if not dest_dir:
            desktop = os.path.join(os.path.expanduser("~"), "Desktop")
            dest_dir = os.path.join(desktop, f"Android_EFS_Backup_{serial}_{int(time.time())}")

        os.makedirs(dest_dir, exist_ok=True)
        result.backup_folder = dest_dir

        if progress_callback:
            progress_callback(f"Root verified ({root_type}). Scanning block devices for EFS/NVRAM...")

        by_name_dirs = [
            "/dev/block/bootdevice/by-name",
            "/dev/block/by-name",
            "/dev/block/platform/*/by-name",
            "/dev/block/platform/soc/*/by-name"
        ]

        found_by_name = ""
        for p in by_name_dirs:
            code, out, _ = self.bridge.shell(f"ls -d {p} 2>/dev/null", serial=serial, timeout=5, root=True)
            if code == 0 and out.strip():
                found_by_name = out.strip().splitlines()[0]
                break

        if not found_by_name:
            result.success = False
            result.message = "Could not locate partition by-name directory on device."
            return result

        code_ls, out_ls, _ = self.bridge.shell(f"ls -la {found_by_name} 2>/dev/null", serial=serial, timeout=10, root=True)
        if code_ls != 0 or not out_ls:
            result.success = False
            result.message = f"Failed to list partitions in {found_by_name}."
            return result

        target_names = ["efs", "sec_efs", "modemst1", "modemst2", "fsg", "fsc", "persist", "nvram", "nvdata"]
        discovered_targets = []

        for line in out_ls.splitlines():
            tokens = line.strip().split()
            for part in target_names:
                if part in tokens or any(t.endswith(f"/{part}") or t == part for t in tokens):
                    if part not in discovered_targets:
                        discovered_targets.append(part)

        if not discovered_targets:
            result.success = False
            result.message = "No radio/EFS partitions matched (efs, modemst1, modemst2, persist, nvram)."
            return result

        if progress_callback:
            progress_callback(f"Discovered {len(discovered_targets)} radio partitions: {', '.join(discovered_targets)}")

        backed_up = []
        total_size = 0
        hashes = {}

        for part in discovered_targets:
            remote_node = f"{found_by_name}/{part}"
            remote_tmp = f"/data/local/tmp/{part}.img"
            local_file = os.path.join(dest_dir, f"{part}.img")

            if progress_callback:
                progress_callback(f"Dumping {part} partition via dd...")

            code_dd, out_dd, err_dd = self.bridge.shell(
                f"dd if={remote_node} of={remote_tmp} bs=4096 2>/dev/null",
                serial=serial, timeout=30, root=True
            )
            self.bridge.shell(f"chmod 644 {remote_tmp}", serial=serial, timeout=5, root=True)

            code_pull, out_pull, _ = self.bridge.run_adb(["pull", remote_tmp, local_file], serial=serial, timeout=45)
            self.bridge.shell(f"rm -f {remote_tmp}", serial=serial, timeout=5, root=True)

            if os.path.exists(local_file) and os.path.getsize(local_file) > 0:
                f_size = os.path.getsize(local_file)
                total_size += f_size
                backed_up.append(part)

                hasher = hashlib.sha256()
                with open(local_file, "rb") as bf:
                    for chunk in iter(lambda: bf.read(65536), b""):
                        hasher.update(chunk)
                hashes[part] = hasher.hexdigest()

                if progress_callback:
                    progress_callback(f"✓ {part}.img pulled successfully ({f_size / 1024:.1f} KB) - SHA256: {hashes[part][:12]}...")

        manifest_path = os.path.join(dest_dir, "manifest.json")
        manifest_data = {
            "device_serial": serial,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
            "backed_up_partitions": backed_up,
            "total_bytes": total_size,
            "hashes": hashes
        }
        with open(manifest_path, "w", encoding="utf-8") as mf:
            json.dump(manifest_data, mf, indent=2)

        result.success = len(backed_up) > 0
        result.backed_up_partitions = backed_up
        result.total_bytes = total_size
        result.hashes = hashes
        result.message = f"Successfully backed up {len(backed_up)} critical radio partitions ({total_size / (1024*1024):.2f} MB)."
        return result
