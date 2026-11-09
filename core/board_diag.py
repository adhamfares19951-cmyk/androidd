import os
import re
import time
import json
import hashlib
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Any, Callable
from core.adb_bridge import ADBBridge
from core.result import Evidence, Status, CheckResult, status_from_read, aggregate_status
from core.readers import ReadResult, run_read, read_node, failure_status, parse_int
from core import jedec
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


USB_TYPES = {
    "unknown", "sdp", "dcp", "cdp", "aca", "c", "pd", "pd_drp", "pd_pps", "apple_brick_id", "brickid",
    "hvdcp", "hvdcp_3", "hvdcp_3p5", "usb_float", "float", "qc", "usb", "usb_dcp", "usb_cdp", "usb_aca",
    "usb_c", "usb_pd", "usb_pd_drp", "usb_hvdcp", "usb_hvdcp_3", "wireless", "mains",
}


def parse_usb_type(text: Optional[str]) -> Optional[str]:
    """
    Parse power_supply usb `type` / `real_type`. Newer kernels list all types with the active one in
    brackets ("Unknown [SDP] DCP CDP"). Returns the active type, or None if the text is not a known type.
    """
    t = (text or "").strip()
    if not t:
        return None
    m = re.search(r"\[([A-Za-z0-9_]+)\]", t)
    token = m.group(1) if m else t.splitlines()[0].strip()
    if token.lower() in USB_TYPES and token.lower() != "unknown":
        return token
    return None


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
    """Battery readings from the fuel gauge / power_supply class. Every value is None when not read."""
    check_id: str = "battery.fuel_gauge"
    voltage_mv: Optional[float] = None
    voltage_source: str = "UNKNOWN"
    voltage_status: str = "UNKNOWN"
    current_ma: Optional[float] = None
    current_source: str = "UNKNOWN"
    current_status: str = "UNKNOWN"
    current_note: str = ""
    charge_full_uah: Optional[int] = None
    charge_full_design_uah: Optional[int] = None
    cycle_count: Optional[int] = None
    temp_c: Optional[float] = None
    temp_source: str = "UNKNOWN"
    temp_status: str = "UNKNOWN"
    status: str = "UNKNOWN"          # charging status text reported by the device
    health: str = "UNKNOWN"          # health text reported by the device
    true_health_pct: Optional[float] = None
    health_verdict: str = "UNKNOWN"  # PASS / FAIL / UNKNOWN / NEEDS_ROOT for capacity vs design
    is_charging: bool = False
    is_ac_plugged: bool = False
    fault_flags: List[str] = field(default_factory=list)
    raw_details: Dict[str, Any] = field(default_factory=dict)
    evidence: List[Evidence] = field(default_factory=list)

    @property
    def temperature_c(self) -> Optional[float]:
        return self.temp_c

    @property
    def check_status(self) -> str:
        """Key-check status of the battery readings: FAIL on a fault flag, else the voltage read status."""
        if self.fault_flags:
            return str(Status.FAIL)
        return self.voltage_status

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
    source: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ThermalReport:
    check_id: str = "thermal.zones"
    zones: List[ThermalZone] = field(default_factory=list)
    status: str = "UNKNOWN"
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "zones": [z.to_dict() for z in self.zones],
            "status": self.status,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass
class BlackboxReport:
    check_id: str = "board.boot_reason"
    boot_reason: str = "UNKNOWN"
    boot_reason_history: str = "UNKNOWN"
    shutdown_category: str = "UNKNOWN"  # "THERMAL", "UVLO", "WATCHDOG_PANIC", "USER_REQUESTED", "NORMAL", "UNKNOWN"
    has_abnormal_shutdown: bool = False
    dropbox_events: List[str] = field(default_factory=list)
    kernel_ramoops_status: str = "UNKNOWN"  # PASS / FAIL / UNKNOWN / NEEDS_ROOT
    kernel_log_snippets: List[str] = field(default_factory=list)
    status: str = "UNKNOWN"
    diagnosis: str = ""
    possible_causes: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class PanicAnalysis:
    check_id: str = "board.kernel_log"
    has_critical_error: bool = False
    emmc_ufs_errors: List[str] = field(default_factory=list)
    watchdog_resets: List[str] = field(default_factory=list)
    thermal_shutdowns: List[str] = field(default_factory=list)
    kernel_panics: List[str] = field(default_factory=list)
    dmesg_status: str = "UNKNOWN"
    status: str = "UNKNOWN"
    summary_verdict: str = ""
    blackbox: BlackboxReport = field(default_factory=BlackboxReport)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "has_critical_error": self.has_critical_error,
            "emmc_ufs_errors": self.emmc_ufs_errors,
            "watchdog_resets": self.watchdog_resets,
            "thermal_shutdowns": self.thermal_shutdowns,
            "kernel_panics": self.kernel_panics,
            "dmesg_status": self.dmesg_status,
            "status": self.status,
            "summary_verdict": self.summary_verdict,
            "blackbox": self.blackbox.to_dict(),
            "evidence": [e.to_dict() for e in self.evidence],
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
    """Charger readings. Measured values and reported maximums are kept apart; None = not read."""
    check_id: str = "charging.input"
    charger_type: str = "UNKNOWN"
    charge_type: str = "UNKNOWN"
    voltage_mv: Optional[float] = None       # measured USB input voltage
    voltage_v: Optional[float] = None
    current_ma: Optional[float] = None       # measured USB input current
    current_a: Optional[float] = None
    wattage_w: Optional[float] = None        # only when both voltage and current were measured
    max_charging_current_ma: Optional[float] = None  # reported maximum, not a measurement
    max_charging_voltage_mv: Optional[float] = None  # reported maximum, not a measurement
    protocol_name: str = "UNKNOWN"
    is_fast_charging: Optional[bool] = None
    battery_level: Optional[int] = None
    charge_status: str = "UNKNOWN"
    health_state: str = "UNKNOWN"
    status: str = "UNKNOWN"
    diagnosis: str = ""
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class StorageLifeReport:
    check_id: str = "storage.jedec_life"
    storage_type: str = "UNKNOWN"
    device_model: str = ""
    life_time_type_a: str = "UNKNOWN"
    life_time_type_b: str = "UNKNOWN"
    pre_eol_info: str = "UNKNOWN"
    raw_hex_a: str = ""
    raw_hex_b: str = ""
    raw_pre_eol: str = ""
    status: str = "UNKNOWN"  # "PASS", "FAIL", "UNKNOWN", "NEEDS_ROOT"
    is_warning: bool = False
    is_critical: bool = False
    health_verdict: str = "UNKNOWN"
    diagnosis: str = ""
    possible_causes: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class StressTestSnapshot:
    second: int = 0
    freqs_mhz: List[float] = field(default_factory=list)
    avg_freq_mhz: Optional[float] = None
    voltage_mv: Optional[float] = None
    temp_c: Optional[float] = None
    battery_temp_c: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class StressTestReport:
    check_id: str = "board.cpu_stress"
    duration_seconds: int = 30
    initial_voltage_mv: Optional[float] = None
    min_voltage_mv: Optional[float] = None
    voltage_sag_mv: Optional[float] = None
    initial_temp_c: Optional[float] = None
    max_temp_c: Optional[float] = None
    temp_rise_c: Optional[float] = None
    temp_source: str = "UNKNOWN"
    max_freq_mhz: Optional[float] = None
    min_freq_mhz: Optional[float] = None
    throttling_detected: bool = False
    severe_sag_detected: bool = False
    samples: List[StressTestSnapshot] = field(default_factory=list)
    status: str = "UNKNOWN"
    verdict: str = "UNKNOWN"
    diagnosis: str = ""
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "duration_seconds": self.duration_seconds,
            "initial_voltage_mv": self.initial_voltage_mv,
            "min_voltage_mv": self.min_voltage_mv,
            "voltage_sag_mv": self.voltage_sag_mv,
            "initial_temp_c": self.initial_temp_c,
            "max_temp_c": self.max_temp_c,
            "temp_rise_c": self.temp_rise_c,
            "temp_source": self.temp_source,
            "max_freq_mhz": self.max_freq_mhz,
            "min_freq_mhz": self.min_freq_mhz,
            "throttling_detected": self.throttling_detected,
            "severe_sag_detected": self.severe_sag_detected,
            "samples": [s.to_dict() for s in self.samples],
            "status": self.status,
            "verdict": self.verdict,
            "diagnosis": self.diagnosis,
            "evidence": [e.to_dict() for e in self.evidence],
        }


@dataclass
class BoardDiagnosticResult:
    serial: str
    pmic: PMICStatus
    thermal_zones: List[ThermalZone]
    panic_analysis: PanicAnalysis
    thermal_status: str = "UNKNOWN"
    thermal_evidence: List[Evidence] = field(default_factory=list)
    check_statuses: Dict[str, str] = field(default_factory=dict)
    overall_health: str = "UNKNOWN"
    critical_findings: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "serial": self.serial,
            "pmic": self.pmic.to_dict(),
            "thermal_zones": [tz.to_dict() for tz in self.thermal_zones],
            "thermal_status": self.thermal_status,
            "thermal_evidence": [e.to_dict() for e in self.thermal_evidence],
            "panic_analysis": self.panic_analysis.to_dict(),
            "check_statuses": dict(self.check_statuses),
            "overall_health": self.overall_health,
            "critical_findings": self.critical_findings
        }


@dataclass
class RootForensicsReport:
    check_id: str = "board.root_forensics"
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
    status: str = "UNKNOWN"
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

    # ------------------------------------------------------------------
    # Battery (fuel gauge) readings
    # ------------------------------------------------------------------
    def _read_scaled(self, serial: str, specs: List[Tuple[str, Optional[float]]], low: float, high: float, unit: str):
        """
        Read the first node from `specs` [(path, divisor)] that yields a plausible value.
        Divisors follow the kernel power_supply ABI (e.g. voltage_now is in uV -> /1000 = mV).
        Returns (value, source, status, evidence_list).
        """
        reads: List[ReadResult] = []
        evidence: List[Evidence] = []
        for path, divisor in specs:
            res = read_node(self.bridge, [path], serial=serial)
            reads.append(res)
            if not res.ok:
                continue
            raw = parse_int(res.text)
            if raw is None:
                evidence.append(res.evidence(f"malformed value, expected an integer ({unit})"))
                continue
            if divisor is None:
                # Vendors use uA or mA here in practice: infer from magnitude and say so in the evidence.
                div, how = (1000.0, "uA inferred (|raw| > 20000)") if abs(raw) > 20000 else (1.0, "mA inferred (|raw| <= 20000)")
            else:
                div, how = divisor, f"/{divisor:g}"
            value = round(raw / div, 1)
            if not (low <= value <= high):
                evidence.append(res.evidence(f"raw={raw} -> {value} {unit} is outside the plausible range {low}..{high}; ignored"))
                continue
            evidence.append(res.evidence(f"raw={raw} {how} -> {value} {unit}"))
            return value, path, str(Status.PASS), evidence
        status = failure_status(reads)
        # keep one evidence item explaining why nothing was read
        if not evidence and reads:
            best = max(reads, key=lambda r: (r.status == Status.NEEDS_ROOT, r.path_exists))
            evidence.append(best.evidence())
        return None, "UNKNOWN", str(status), evidence

    def analyze_pmic(self, serial: str) -> PMICStatus:
        pmic = PMICStatus()
        raw: Dict[str, Any] = {}
        ev: List[Evidence] = []

        # 1. Battery voltage (fuel gauge): voltage_now is uV, Samsung batt_vol is mV
        v_specs = [(f"{b}/voltage_now", 1000.0) for b in BATTERY_SYSFS_DIRS[:5]] + \
                  [(f"{b}/batt_vol", 1.0) for b in BATTERY_SYSFS_DIRS[:5]]
        pmic.voltage_mv, pmic.voltage_source, pmic.voltage_status, e = self._read_scaled(serial, v_specs, 2500.0, 5000.0, "mV")
        ev.extend(e)

        # 2. Battery current: current_now is uA, Samsung batt_current is mA
        c_specs = [(f"{b}/current_now", None) for b in BATTERY_SYSFS_DIRS[:5]] + \
                  [(f"{b}/batt_current", 1.0) for b in BATTERY_SYSFS_DIRS[:5]]
        pmic.current_ma, pmic.current_source, pmic.current_status, e = self._read_scaled(serial, c_specs, -20000.0, 20000.0, "mA")
        ev.extend(e)

        if ":" in serial:
            pmic.current_note = "Wi-Fi connected (pure battery current without USB charger)"
        else:
            pmic.current_note = "includes charging current while USB is connected"

        # 3. Battery temperature: power_supply temp is in tenths of a degree C
        t_specs = [(f"{b}/temp", 10.0) for b in BATTERY_SYSFS_DIRS[:5]] + \
                  [(f"{b}/batt_temp", 10.0) for b in BATTERY_SYSFS_DIRS[:5]]
        pmic.temp_c, pmic.temp_source, pmic.temp_status, e = self._read_scaled(serial, t_specs, -20.0, 80.0, "°C")
        ev.extend(e)

        # 4. Capacity and cycles (uAh)
        cf_read = read_node(self.bridge, [f"{b}/charge_full" for b in BATTERY_SYSFS_DIRS[:5]], serial=serial)
        cfd_read = read_node(self.bridge, [f"{b}/charge_full_design" for b in BATTERY_SYSFS_DIRS[:5]], serial=serial)
        cyc_read = read_node(self.bridge, [f"{b}/cycle_count" for b in BATTERY_SYSFS_DIRS[:5]], serial=serial)
        cf = parse_int(cf_read.text) if cf_read.ok else None
        cfd = parse_int(cfd_read.text) if cfd_read.ok else None
        cyc = parse_int(cyc_read.text) if cyc_read.ok else None
        pmic.charge_full_uah = cf if (cf is not None and cf > 0) else None
        pmic.charge_full_design_uah = cfd if (cfd is not None and cfd > 0) else None
        pmic.cycle_count = cyc if (cyc is not None and cyc >= 0) else None
        ev.append(cf_read.evidence(f"charge_full={pmic.charge_full_uah} uAh" if pmic.charge_full_uah else "charge_full not parsed"))
        ev.append(cfd_read.evidence(f"charge_full_design={pmic.charge_full_design_uah} uAh" if pmic.charge_full_design_uah else "charge_full_design not parsed"))
        if cyc_read.ok or cyc_read.status == Status.NEEDS_ROOT:
            ev.append(cyc_read.evidence(f"cycle_count={pmic.cycle_count}"))

        # 5. Reported status / health text
        st_read = read_node(self.bridge, [f"{b}/status" for b in BATTERY_SYSFS_DIRS[:5]], serial=serial)
        if st_read.ok:
            pmic.status = st_read.text.splitlines()[0].strip().capitalize()
            pmic.is_charging = pmic.status.lower() == "charging"
            ev.append(st_read.evidence(f"status={pmic.status}"))
        h_read = read_node(self.bridge, [f"{b}/health" for b in BATTERY_SYSFS_DIRS[:5]], serial=serial)
        if h_read.ok:
            pmic.health = h_read.text.splitlines()[0].strip().capitalize()
            ev.append(h_read.evidence(f"health={pmic.health}"))

        # 6. dumpsys battery (non-root) fills gaps; its units are mV and tenths of degree C
        db = run_read(self.bridge, "dumpsys battery", serial=serial, timeout=8)
        if db.ok:
            parsed = []
            for line in db.text.splitlines():
                line = line.strip()
                key, _, val = line.partition(":")
                val = val.strip()
                if key == "voltage" and pmic.voltage_mv is None:
                    v = parse_int(val)
                    if v is not None and 2500 <= v <= 5000:
                        pmic.voltage_mv = float(v)
                        pmic.voltage_source = "dumpsys battery (voltage, mV)"
                        pmic.voltage_status = str(Status.PASS)
                        parsed.append(f"voltage={v} mV")
                elif key == "temperature" and pmic.temp_c is None:
                    t = parse_int(val)
                    if t is not None and -200 <= t <= 800:
                        pmic.temp_c = round(t / 10.0, 1)
                        pmic.temp_source = "dumpsys battery (temperature, 0.1 °C)"
                        pmic.temp_status = str(Status.PASS)
                        parsed.append(f"temperature={pmic.temp_c} °C")
                elif key == "status" and pmic.status == "UNKNOWN":
                    status_map = {"2": "Charging", "3": "Discharging", "4": "Not charging", "5": "Full"}
                    if val in status_map:
                        pmic.status = status_map[val]
                        pmic.is_charging = pmic.status == "Charging"
                        parsed.append(f"status={pmic.status}")
                elif key == "health" and pmic.health == "UNKNOWN":
                    health_map = {"2": "Good", "3": "Overheat", "4": "Dead", "5": "Over voltage", "6": "Failure", "7": "Cold"}
                    if val in health_map:
                        pmic.health = health_map[val]
                        parsed.append(f"health={pmic.health}")
                elif key in ("AC powered", "USB powered", "Wireless powered"):
                    if val.lower() == "true":
                        pmic.is_ac_plugged = True
                        parsed.append(f"{key}=true")
            ev.append(db.evidence(", ".join(parsed) if parsed else "no usable fields"))
        else:
            ev.append(db.evidence())

        # 7. Capacity vs design: only from two real readings
        if pmic.charge_full_uah and pmic.charge_full_design_uah:
            pct = round(pmic.charge_full_uah / pmic.charge_full_design_uah * 100.0, 1)
            if 1.0 <= pct <= 150.0:
                pmic.true_health_pct = pct
                pmic.health_verdict = str(Status.PASS if pct >= 80.0 else Status.FAIL)
                ev.append(Evidence(source="charge_full / charge_full_design",
                                   raw_excerpt=f"{pmic.charge_full_uah} / {pmic.charge_full_design_uah} = {pct}% (FAIL below 80%)",
                                   exit_code=None))
            else:
                pmic.health_verdict = str(Status.UNKNOWN)
                ev.append(Evidence(source="charge_full / charge_full_design",
                                   raw_excerpt=f"ratio {pct}% is implausible; not used", exit_code=None))
        else:
            pmic.health_verdict = str(failure_status([cf_read, cfd_read]))

        # 8. Fault flags only from real readings
        if pmic.is_charging and pmic.voltage_mv is not None and pmic.voltage_mv < 3500.0:
            pmic.fault_flags.append(
                "Low battery voltage while charging (< 3500 mV). Possible causes: degraded battery cell, charging circuit fault."
            )
        if pmic.is_ac_plugged and pmic.is_charging and pmic.current_ma is not None and 0 < abs(pmic.current_ma) < 200.0:
            pmic.fault_flags.append(
                "Low charging current (< 200 mA) on external power. Possible causes: dirty port, bad cable, weak charger, USB board damage."
            )

        raw["voltage_source"] = pmic.voltage_source
        raw["current_source"] = pmic.current_source
        pmic.raw_details = raw
        pmic.evidence = ev
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

    # ------------------------------------------------------------------
    # Thermal zones
    # ------------------------------------------------------------------
    THERMAL_ALERT_C = 45.0  # single threshold kept until per-zone thresholds land (Step 3)

    def read_thermal(self, serial: str) -> ThermalReport:
        """Read thermal zones from dumpsys thermalservice (non-root), falling back to sysfs."""
        report = ThermalReport()
        zones: Dict[str, ThermalZone] = {}

        res = run_read(self.bridge, "dumpsys thermalservice", serial=serial, timeout=8)
        reads = [res]
        if res.ok:
            for m in re.finditer(r"Temperature\{mValue=(-?[\d.]+),\s*mType=(-?\d+),\s*mName=([^,}]+)", res.text):
                name = m.group(3).strip()
                try:
                    val = float(m.group(1))
                except ValueError:
                    continue
                if name in zones or not (-40.0 <= val <= 150.0):
                    continue
                zones[name] = ThermalZone(zone_id=len(zones), zone_type=name, temp_c=round(val, 1),
                                          is_overheated=val > self.THERMAL_ALERT_C, source="dumpsys thermalservice")
            report.evidence.append(res.evidence(f"{len(zones)} temperatures parsed"))
        else:
            report.evidence.append(res.evidence())

        if not zones:
            cmd = ('for z in /sys/class/thermal/thermal_zone*; do '
                   'echo "$z|$(cat $z/type)|$(cat $z/temp)"; done')
            sys_res = run_read(self.bridge, cmd, serial=serial, timeout=10, source="/sys/class/thermal/thermal_zone*/{type,temp}")
            reads.append(sys_res)
            if sys_res.ok:
                for line in sys_res.text.splitlines():
                    parts = line.strip().split("|")
                    if len(parts) != 3:
                        continue
                    path, t_type, t_raw = parts
                    t_int = parse_int(t_raw)
                    if t_int is None:
                        continue
                    # thermal_zone temp is millidegree C by ABI; a few drivers report whole degrees
                    t_c = round(t_int / 1000.0, 1) if abs(t_int) > 1000 else float(t_int)
                    if not (-40.0 <= t_c <= 150.0):
                        continue
                    z_match = re.search(r"thermal_zone(\d+)", path)
                    z_id = int(z_match.group(1)) if z_match else len(zones)
                    name = t_type.strip() or f"thermal_zone{z_id}"
                    zones[f"{name}#{z_id}"] = ThermalZone(zone_id=z_id, zone_type=name, temp_c=t_c,
                                                          is_overheated=t_c > self.THERMAL_ALERT_C, source=path)
                report.evidence.append(sys_res.evidence(f"{len(zones)} zones parsed"))
            else:
                report.evidence.append(sys_res.evidence())

        report.zones = list(zones.values())
        if report.zones:
            report.status = str(Status.FAIL if any(z.is_overheated for z in report.zones) else Status.PASS)
        else:
            report.status = str(failure_status(reads))
        return report

    def scan_thermal_map(self, serial: str) -> List[ThermalZone]:
        return self.read_thermal(serial).zones

    # ------------------------------------------------------------------
    # Boot reason, dropbox and pstore (ramoops)
    # ------------------------------------------------------------------
    _ABNORMAL_RULES = [
        ("THERMAL", r"(thermal|overheat|crit_temp|tsens)",
         ["Thermal shutdown recorded. Possible causes: blocked cooling, failing battery, heavy load, faulty thermal sensor."]),
        ("UVLO", r"(uvlo|undervoltage|vbat_low|battery_pull|brownout)",
         ["Under-voltage power loss recorded. Possible causes: worn battery, loose battery connector, power circuit fault."]),
        ("WATCHDOG_PANIC", r"(watchdog|wdog|kernel_panic|subsys_restart|oops|softlockup|hardlockup)",
         ["Watchdog / kernel panic reset recorded. Possible causes: software/kernel bug, unstable firmware, memory or SoC fault."]),
    ]

    def run_blackbox_forensics(self, serial: str) -> BlackboxReport:
        bb = BlackboxReport()

        b_reason = self.bridge.get_prop("sys.boot.reason", serial=serial) or \
                   self.bridge.get_prop("ro.boot.bootreason", serial=serial) or ""
        b_history = self.bridge.get_prop("persist.sys.boot.reason.history", serial=serial) or ""
        bb.boot_reason = b_reason or "UNKNOWN"
        bb.boot_reason_history = b_history or "UNKNOWN"
        bb.evidence.append(Evidence(source="getprop sys.boot.reason / ro.boot.bootreason / persist.sys.boot.reason.history",
                                    raw_excerpt=f"reason={b_reason or '(empty)'}\nhistory={b_history or '(empty)'}",
                                    exit_code=None))

        # Dropbox entry list (non-root). Tags only, no payload.
        drop = run_read(self.bridge, "dumpsys dropbox", serial=serial, timeout=10)
        if drop.ok:
            for line in drop.text.splitlines():
                line_str = line.strip()
                if re.search(r"(?i)(kernel_panic|panic|watchdog|thermal_shutdown|SYSTEM_RESTART)", line_str):
                    bb.dropbox_events.append(line_str[:160])
                    if len(bb.dropbox_events) >= 15:
                        break
            bb.evidence.append(drop.evidence(f"{len(bb.dropbox_events)} relevant entries"))
        else:
            bb.evidence.append(drop.evidence())

        # pstore console-ramoops (last kernel log of the previous boot)
        rres = run_read(self.bridge, "cat /sys/fs/pstore/console-ramoops*", serial=serial, timeout=8,
                        source="/sys/fs/pstore/console-ramoops*", log_content=True)
        if rres.status == Status.NEEDS_ROOT and self.bridge.is_root_available(serial):
            rres = run_read(self.bridge, "cat /sys/fs/pstore/console-ramoops*", serial=serial, timeout=8, root=True,
                            source="/sys/fs/pstore/console-ramoops* (su)", log_content=True)
        if rres.ok:
            for l in rres.text.splitlines()[-400:]:
                if re.search(r"(?i)(kernel panic|internal error: oops|watchdog bark|watchdog bite|fatal exception)", l):
                    bb.kernel_log_snippets.append(l.strip()[:160])
            bb.kernel_ramoops_status = str(Status.FAIL if bb.kernel_log_snippets else Status.PASS)
            tail = "\n".join(rres.text.splitlines()[-40:])
            bb.evidence.append(Evidence(source=rres.source, raw_excerpt=tail, exit_code=rres.exit_code))
        else:
            bb.kernel_ramoops_status = str(rres.status)
            bb.evidence.append(rres.evidence("no readable pstore log" if rres.status == Status.UNKNOWN else ""))

        # Categorise only from what was actually read
        combined = f"{b_reason} {b_history} {' '.join(bb.dropbox_events)} {' '.join(bb.kernel_log_snippets)}".lower()
        for category, pattern, causes in self._ABNORMAL_RULES:
            if re.search(pattern, combined):
                bb.shutdown_category = category
                bb.has_abnormal_shutdown = True
                bb.possible_causes = causes
                bb.status = str(Status.FAIL)
                bb.diagnosis = causes[0]
                return bb

        if b_reason:
            bb.shutdown_category = "USER_REQUESTED" if re.search(r"(userrequested|power_menu|shell|recovery|reboot)", combined) else "NORMAL"
            bb.status = str(Status.PASS)
            bb.diagnosis = f"Last boot reason '{b_reason}' does not indicate an abnormal shutdown."
        else:
            bb.shutdown_category = "UNKNOWN"
            bb.status = str(Status.UNKNOWN)
            bb.diagnosis = "Boot reason is not reported by this device."
        return bb

    def inspect_panic_and_emmc(self, serial: str) -> PanicAnalysis:
        analysis = PanicAnalysis()
        blackbox = self.run_blackbox_forensics(serial)
        analysis.blackbox = blackbox

        if blackbox.has_abnormal_shutdown:
            analysis.has_critical_error = True
            if blackbox.shutdown_category == "THERMAL":
                analysis.thermal_shutdowns.append(blackbox.diagnosis)
            else:
                analysis.watchdog_resets.append(blackbox.diagnosis)
        for ev in blackbox.dropbox_events:
            if "panic" in ev.lower():
                analysis.kernel_panics.append(ev)
                analysis.has_critical_error = True
        analysis.kernel_panics.extend(blackbox.kernel_log_snippets)

        # Kernel log: try without root first; dmesg is usually blocked for the shell user.
        dres = run_read(self.bridge, "dmesg", serial=serial, timeout=8, log_content=True)
        if dres.status == Status.NEEDS_ROOT and self.bridge.is_root_available(serial):
            dres = run_read(self.bridge, "dmesg", serial=serial, timeout=8, root=True, source="dmesg (su)", log_content=True)
        if dres.ok:
            for line in dres.text.splitlines()[-600:]:
                l_clean = line.strip()
                if re.search(r"(?i)(mmc\d+:.*error|blk_update_request: I/O error|EXT4-fs error|F2FS-fs error|ufshcd.*(error|abort))", l_clean):
                    if len(analysis.emmc_ufs_errors) < 10 and l_clean not in analysis.emmc_ufs_errors:
                        analysis.emmc_ufs_errors.append(l_clean)
                elif re.search(r"(?i)(Kernel panic|Internal error: Oops|Unable to handle kernel)", l_clean):
                    if len(analysis.kernel_panics) < 10 and l_clean not in analysis.kernel_panics:
                        analysis.kernel_panics.append(l_clean)
            analysis.dmesg_status = str(Status.FAIL if (analysis.emmc_ufs_errors or analysis.kernel_panics) else Status.PASS)
            analysis.evidence.append(Evidence(source=dres.source, raw_excerpt="\n".join(dres.text.splitlines()[-40:]), exit_code=dres.exit_code))
        else:
            analysis.dmesg_status = str(dres.status)
            analysis.evidence.append(dres.evidence())

        if analysis.kernel_panics or analysis.emmc_ufs_errors:
            analysis.has_critical_error = True

        if analysis.has_critical_error:
            analysis.status = str(Status.FAIL)
            analysis.summary_verdict = "Kernel/storage errors or abnormal resets were found in the logs (see evidence)."
        else:
            analysis.status = analysis.dmesg_status
            if analysis.dmesg_status == str(Status.PASS):
                analysis.summary_verdict = "Kernel log was read: no panic or storage I/O error lines found."
            elif analysis.dmesg_status == str(Status.NEEDS_ROOT):
                analysis.summary_verdict = "Kernel log (dmesg) is blocked without root."
            else:
                analysis.summary_verdict = "Kernel log could not be read."
        return analysis

    def run_full_diagnostics(self, serial: str) -> BoardDiagnosticResult:
        logger.info(f"Running board diagnostics on {serial}...")
        pmic = self.analyze_pmic(serial)
        thermal = self.read_thermal(serial)
        panic = self.inspect_panic_and_emmc(serial)

        findings: List[str] = list(pmic.fault_flags)
        hot = [f"{z.zone_type} ({z.temp_c}°C)" for z in thermal.zones if z.is_overheated]
        if hot:
            findings.append(f"Thermal alert (> {self.THERMAL_ALERT_C:g}°C): {', '.join(hot)}")
        if panic.status == str(Status.FAIL):
            findings.append(panic.summary_verdict)

        check_statuses = {
            "battery.fuel_gauge": pmic.check_status,
            "thermal.zones": thermal.status,
            "board.boot_reason": panic.blackbox.status,
            "board.kernel_log": panic.status,
        }
        overall = aggregate_status(list(check_statuses.values()))

        return BoardDiagnosticResult(
            serial=serial,
            pmic=pmic,
            thermal_zones=thermal.zones,
            panic_analysis=panic,
            thermal_status=thermal.status,
            thermal_evidence=thermal.evidence,
            check_statuses=check_statuses,
            overall_health=str(overall),
            critical_findings=findings
        )

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
        """
        Charger input readings. Measured USB input voltage/current are reported only when read;
        reported maximums (current_max, dumpsys "Max charging ...") are kept separate and never used as measurements.
        """
        result = FastChargeDiagnostics()
        reads: List[ReadResult] = []

        type_read = read_node(self.bridge, ["/sys/class/power_supply/usb/real_type", "/sys/class/power_supply/usb/type"], serial=serial)
        reads.append(type_read)
        parsed_type = parse_usb_type(type_read.text) if type_read.ok else None
        if parsed_type:
            result.charger_type = parsed_type
            result.evidence.append(type_read.evidence(f"charger_type={result.charger_type}"))
        else:
            result.evidence.append(type_read.evidence("malformed usb type" if type_read.ok else ""))

        ct_read = read_node(self.bridge, ["/sys/class/power_supply/battery/charge_type"], serial=serial)
        if ct_read.ok:
            result.charge_type = ct_read.text.splitlines()[0].strip()
            result.evidence.append(ct_read.evidence(f"charge_type={result.charge_type}"))

        v_read = read_node(self.bridge, ["/sys/class/power_supply/usb/voltage_now"], serial=serial)
        reads.append(v_read)
        v_raw = parse_int(v_read.text) if v_read.ok else None
        if v_raw is not None and 0 < v_raw / 1000.0 <= 25000.0:  # uV -> mV
            result.voltage_mv = round(v_raw / 1000.0, 1)
            result.voltage_v = round(result.voltage_mv / 1000.0, 2)
            result.evidence.append(v_read.evidence(f"raw={v_raw} uV -> {result.voltage_mv} mV"))
        else:
            result.evidence.append(v_read.evidence("no measured USB voltage"))

        i_read = read_node(self.bridge, ["/sys/class/power_supply/usb/current_now", "/sys/class/power_supply/usb/input_current_now"], serial=serial)
        reads.append(i_read)
        i_raw = parse_int(i_read.text) if i_read.ok else None
        if i_raw is not None and 0 < abs(i_raw) / 1000.0 <= 15000.0:  # uA -> mA
            result.current_ma = round(abs(i_raw) / 1000.0, 1)
            result.current_a = round(result.current_ma / 1000.0, 2)
            result.evidence.append(i_read.evidence(f"raw={i_raw} uA -> {result.current_ma} mA"))
        else:
            result.evidence.append(i_read.evidence("no measured USB input current"))

        lim_read = read_node(self.bridge, ["/sys/class/power_supply/usb/current_max", "/sys/class/power_supply/usb/input_current_limit"], serial=serial)
        lim_raw = parse_int(lim_read.text) if lim_read.ok else None
        if lim_raw is not None and lim_raw > 0:
            result.max_charging_current_ma = round(lim_raw / 1000.0, 1)
            result.evidence.append(lim_read.evidence(f"reported limit {result.max_charging_current_ma} mA (not a measurement)"))

        db = run_read(self.bridge, "dumpsys battery", serial=serial, timeout=8)
        if db.ok:
            for line in db.text.splitlines():
                key, _, val = line.strip().partition(":")
                val = val.strip()
                if key == "level":
                    lvl = parse_int(val)
                    if lvl is not None and 0 <= lvl <= 100:
                        result.battery_level = lvl
                elif key == "status":
                    result.charge_status = {"2": "Charging", "3": "Discharging", "4": "Not charging", "5": "Full"}.get(val, result.charge_status)
                elif key == "health":
                    result.health_state = {"2": "Good", "3": "Overheat", "4": "Dead", "5": "Over voltage", "6": "Failure", "7": "Cold"}.get(val, result.health_state)
                elif key == "Max charging current" and result.max_charging_current_ma is None:
                    m = parse_int(val)
                    if m is not None and m > 0:
                        result.max_charging_current_ma = round(m / 1000.0, 1)
                elif key == "Max charging voltage":
                    m = parse_int(val)
                    if m is not None and m > 0:
                        result.max_charging_voltage_mv = round(m / 1000.0, 1)
            result.evidence.append(db.evidence())
        else:
            result.evidence.append(db.evidence())

        if result.voltage_v is not None and result.current_a is not None:
            result.wattage_w = round(result.voltage_v * result.current_a, 2)

        t_lower = parsed_type.lower() if parsed_type else ""
        if "pd" in t_lower:
            result.protocol_name, result.is_fast_charging = "USB Power Delivery (USB-PD)", True
        elif "hvdcp" in t_lower or "qc" in t_lower:
            result.protocol_name, result.is_fast_charging = "Qualcomm Quick Charge (HVDCP)", True
        elif "dcp" in t_lower:
            result.protocol_name, result.is_fast_charging = "Dedicated Charging Port (DCP)", None
        elif "cdp" in t_lower:
            result.protocol_name, result.is_fast_charging = "Charging Downstream Port (CDP)", False
        elif "sdp" in t_lower:
            result.protocol_name, result.is_fast_charging = "Standard Downstream Port (SDP)", False
        elif t_lower:
            result.protocol_name = f"Reported type: {result.charger_type}"
        if result.is_fast_charging is None and result.wattage_w is not None:
            result.is_fast_charging = result.wattage_w >= 15.0

        if parsed_type or result.voltage_mv is not None or result.current_ma is not None:
            result.status = str(Status.PASS)
        else:
            result.status = str(failure_status(reads))

        def fmt(v, unit):
            return f"{v} {unit}" if v is not None else "UNKNOWN"

        result.diagnosis = (
            f"Type: {result.charger_type} | Protocol: {result.protocol_name} | "
            f"Measured input: {fmt(result.voltage_v, 'V')} @ {fmt(result.current_a, 'A')} = {fmt(result.wattage_w, 'W')} | "
            f"Battery: {fmt(result.battery_level, '%')} ({result.charge_status})"
        )
        return result

    def reset_battery_fuel_gauge_stats(self, serial: str) -> Tuple[bool, str]:
        """
        Reset battery statistics (dumpsys batterystats --reset).
        Clears Android's software battery usage history only; it does not touch the fuel gauge chip.
        """
        logger.info(f"Resetting battery statistics on {serial}...")
        res = run_read(self.bridge, "dumpsys batterystats --reset", serial=serial, timeout=10)
        ok = res.exit_code == 0 and res.status != Status.NEEDS_ROOT
        lines = [f"dumpsys batterystats --reset -> exit={res.exit_code}: {(res.text or res.stderr or '').strip()[:300]}"]
        lines.append("Note: this only clears Android's battery usage history.")
        return ok, "\n".join(lines)

    # Alias for backwards compatibility
    def reset_battery_stats_and_gauge(self, serial: str) -> Tuple[bool, str]:
        return self.reset_battery_fuel_gauge_stats(serial)

    EMMC_LIFE_PATHS = [
        "/sys/block/mmcblk0/device/life_time",
        "/sys/class/mmc_host/mmc0/mmc0:*/life_time",
    ]
    EMMC_EOL_PATHS = [
        "/sys/block/mmcblk0/device/pre_eol_info",
        "/sys/class/mmc_host/mmc0/mmc0:*/pre_eol_info",
    ]
    UFS_DESC_DIRS = [
        "/sys/devices/platform/soc/*.ufshc/health_descriptor",
        "/sys/devices/soc/*.ufshc/health_descriptor",
        "/sys/bus/platform/devices/*.ufshc/health_descriptor",
        "/sys/devices/platform/*.ufs/health_descriptor",
    ]

    def _read_with_root_retry(self, serial: str, paths: List[str]) -> ReadResult:
        res = read_node(self.bridge, paths, serial=serial)
        if res.status == Status.NEEDS_ROOT and self.bridge.is_root_available(serial):
            for path in paths:
                target = path if "*" in path else f"'{path}'"
                r2 = run_read(self.bridge, f"cat {target}", serial=serial, timeout=5, root=True, source=f"{path} (su)")
                if r2.ok:
                    return r2
        return res

    def read_emmc_ufs_lifespan(self, serial: str) -> StorageLifeReport:
        """JEDEC eMMC / UFS life-time estimation and pre-EOL registers."""
        report = StorageLifeReport()

        emmc_life = self._read_with_root_retry(serial, self.EMMC_LIFE_PATHS)
        emmc_eol = self._read_with_root_retry(serial, self.EMMC_EOL_PATHS)
        ufs_a = self._read_with_root_retry(serial, [f"{d}/life_time_estimation_a" for d in self.UFS_DESC_DIRS])
        ufs_b = self._read_with_root_retry(serial, [f"{d}/life_time_estimation_b" for d in self.UFS_DESC_DIRS])
        ufs_eol = self._read_with_root_retry(serial, [f"{d}/eol_info" for d in self.UFS_DESC_DIRS] +
                                             [f"{d}/pre_eol_info" for d in self.UFS_DESC_DIRS])
        reads = [emmc_life, emmc_eol, ufs_a, ufs_b, ufs_eol]

        life_values: List[int] = []
        pre_eol = None
        if emmc_life.ok and jedec.parse_life_time(emmc_life.text):
            report.storage_type = "eMMC"
            life_values = jedec.parse_life_time(emmc_life.text)
            tokens = emmc_life.text.split()
            report.raw_hex_a = tokens[0] if tokens else ""
            report.raw_hex_b = tokens[1] if len(tokens) > 1 else ""
            pre_eol = jedec.parse_pre_eol(emmc_eol.text) if emmc_eol.ok else None
            report.raw_pre_eol = emmc_eol.text if emmc_eol.ok else ""
        else:
            a = jedec.parse_life_time(ufs_a.text) if ufs_a.ok else []
            b = jedec.parse_life_time(ufs_b.text) if ufs_b.ok else []
            if a or b:
                report.storage_type = "UFS"
                life_values = a + b
                report.raw_hex_a = ufs_a.text if ufs_a.ok else ""
                report.raw_hex_b = ufs_b.text if ufs_b.ok else ""
                pre_eol = jedec.parse_pre_eol(ufs_eol.text) if ufs_eol.ok else None
                report.raw_pre_eol = ufs_eol.text if ufs_eol.ok else ""

        for r in reads:
            if r.ok or r.status == Status.NEEDS_ROOT:
                report.evidence.append(r.evidence())
        if not report.evidence:
            report.evidence.append(emmc_life.evidence("no JEDEC health node found"))

        a_val = jedec.parse_register(report.raw_hex_a) if report.raw_hex_a else None
        b_val = jedec.parse_register(report.raw_hex_b) if report.raw_hex_b else None
        if a_val in jedec.LIFE_TIME_RANGES:
            report.life_time_type_a = jedec.life_time_label(a_val)
        if b_val in jedec.LIFE_TIME_RANGES:
            report.life_time_type_b = jedec.life_time_label(b_val)
        if pre_eol is not None:
            report.pre_eol_info = jedec.pre_eol_label(pre_eol)

        status = jedec.evaluate(life_values, pre_eol)
        if status == Status.UNKNOWN:
            status = failure_status(reads)
        report.status = str(status)
        report.health_verdict = report.status

        if status == Status.FAIL:
            report.is_critical = True
            report.diagnosis = "Storage wear registers report high wear (see evidence)."
            report.possible_causes = ["Flash memory near the end of its rated write endurance; back up user data."]
        elif status == Status.PASS:
            report.diagnosis = f"Storage wear registers read: {report.life_time_type_a}; pre-EOL: {report.pre_eol_info}."
        elif status == Status.NEEDS_ROOT:
            report.diagnosis = "Storage health registers are blocked without root."
        else:
            report.diagnosis = "Storage health registers are not exposed on this device."
        return report

    def benchmark_storage_speed(self, serial: str, test_size_mb: int = 64) -> Tuple[Optional[float], Optional[float], str, str]:
        """
        Sequential write (dd conv=fsync) and read in /data/local/tmp. Returns (write MB/s, read MB/s, status, diagnosis).
        Speeds are None when dd output could not be parsed. Without root the page cache cannot be dropped,
        so the read speed may come from RAM.
        """
        from core.storage_benchmark import parse_dd_speed_mb_s
        tmp_file = "/data/local/tmp/ad_bench.bin"
        w = run_read(self.bridge, f"dd if=/dev/zero of={tmp_file} bs=1048576 count={test_size_mb} conv=fsync",
                     serial=serial, timeout=60)
        r = run_read(self.bridge, f"dd if={tmp_file} of=/dev/null bs=1048576", serial=serial, timeout=60)
        self.bridge.shell(f"rm -f {tmp_file}", serial=serial, timeout=5)

        write_speed = parse_dd_speed_mb_s(f"{w.stdout}\n{w.stderr}") if w.exit_code in (0, None) else None
        read_speed = parse_dd_speed_mb_s(f"{r.stdout}\n{r.stderr}") if r.exit_code in (0, None) else None

        def fmt(v):
            return f"{v} MB/s" if v is not None else "UNKNOWN"

        if write_speed is None:
            status = Status.NEEDS_ROOT if w.status == Status.NEEDS_ROOT else Status.UNKNOWN
            diag = f"Write benchmark output could not be parsed. Write={fmt(write_speed)} Read={fmt(read_speed)}."
        elif write_speed < 25.0:
            status = Status.FAIL
            diag = (f"Write={fmt(write_speed)} is below 25 MB/s. Read={fmt(read_speed)}. Possible causes: worn flash, "
                    f"nearly full storage, thermal throttling, heavy background I/O.")
        else:
            status = Status.PASS
            diag = f"Write={fmt(write_speed)} Read={fmt(read_speed)} (read may be served from page cache)."
        return write_speed, read_speed, str(status), diag

    def run_cpu_pmic_stress_test(
        self,
        serial: str,
        duration_sec: int = 30,
        progress_callback: Optional[Callable[[StressTestSnapshot, str], None]] = None
    ) -> StressTestReport:
        """
        CPU load test: starts 4 busy loops (each limited by `timeout`), samples CPU frequency,
        battery voltage and one thermal zone every 2 seconds. Unreadable samples stay None.
        """
        logger.info(f"Starting {duration_sec}s CPU stress test on {serial}...")
        report = StressTestReport(duration_seconds=duration_sec)
        pid_file = "/data/local/tmp/ad_stress.pids"

        def read_voltage() -> Optional[float]:
            res = read_node(self.bridge, ["/sys/class/power_supply/battery/voltage_now"], serial=serial, timeout=3)
            raw = parse_int(res.text) if res.ok else None
            if raw is None:
                return None
            mv = round(raw / 1000.0, 1)
            return mv if 2500.0 <= mv <= 5000.0 else None

        def read_temp() -> Optional[float]:
            res = read_node(self.bridge, ["/sys/class/thermal/thermal_zone0/temp"], serial=serial, timeout=3)
            raw = parse_int(res.text) if res.ok else None
            if raw is None:
                return None
            t = round(raw / 1000.0, 1) if abs(raw) > 1000 else float(raw)
            return t if -40.0 <= t <= 150.0 else None

        report.initial_voltage_mv = read_voltage()
        report.initial_temp_c = read_temp()
        report.temp_source = "/sys/class/thermal/thermal_zone0/temp"

        limit = duration_sec + 5
        launch_cmd = (
            f"rm -f {pid_file}; for i in 1 2 3 4; do "
            f"timeout {limit} sh -c 'while :; do :; done' >/dev/null 2>&1 & echo $! >> {pid_file}; done"
        )
        launch = run_read(self.bridge, launch_cmd, serial=serial, timeout=8, source="stress launcher")
        report.evidence.append(launch.evidence())

        voltages: List[float] = []
        temps: List[float] = []
        freqs_all: List[float] = []
        interval = 2
        try:
            for step in range(1, max(1, duration_sec // interval) + 1):
                time.sleep(interval)
                fres = run_read(self.bridge, "cat /sys/devices/system/cpu/cpu*/cpufreq/scaling_cur_freq",
                                serial=serial, timeout=4)
                freqs = [round(int(l) / 1000.0, 1) for l in fres.text.splitlines() if l.strip().isdigit()] if fres.ok else []
                cur_v = read_voltage()
                cur_t = read_temp()
                freqs_all.extend(freqs)
                if cur_v is not None:
                    voltages.append(cur_v)
                if cur_t is not None:
                    temps.append(cur_t)
                snap = StressTestSnapshot(
                    second=step * interval, freqs_mhz=freqs,
                    avg_freq_mhz=round(sum(freqs) / len(freqs), 1) if freqs else None,
                    voltage_mv=cur_v, temp_c=cur_t,
                )
                report.samples.append(snap)
                if progress_callback:
                    progress_callback(snap, f"[{snap.second}/{duration_sec}s] CPU avg: {snap.avg_freq_mhz} MHz | "
                                            f"Voltage: {cur_v} mV | Temp: {cur_t}°C")
        finally:
            self.bridge.shell(f"kill $(cat {pid_file}) 2>/dev/null; rm -f {pid_file}", serial=serial, timeout=5)

        if freqs_all:
            report.max_freq_mhz = max(freqs_all)
            report.min_freq_mhz = min(freqs_all)
            report.throttling_detected = (report.max_freq_mhz - report.min_freq_mhz) / report.max_freq_mhz >= 0.35
        if voltages:
            report.min_voltage_mv = min(voltages)
            if report.initial_voltage_mv is not None:
                report.voltage_sag_mv = round(report.initial_voltage_mv - report.min_voltage_mv, 1)
                report.severe_sag_detected = report.voltage_sag_mv >= 350.0
        if temps:
            report.max_temp_c = max(temps)
            if report.initial_temp_c is not None:
                report.temp_rise_c = round(report.max_temp_c - report.initial_temp_c, 1)

        report.evidence.append(Evidence(
            source="stress samples (scaling_cur_freq, battery voltage_now, thermal_zone0)",
            raw_excerpt=f"{len(report.samples)} samples; freq readings={len(freqs_all)}, voltage readings={len(voltages)}, temp readings={len(temps)}",
            exit_code=None))

        if not freqs_all and not voltages:
            report.status = report.verdict = str(Status.UNKNOWN)
            report.diagnosis = "No CPU frequency or battery voltage could be read during the test."
        elif report.severe_sag_detected:
            report.status = report.verdict = str(Status.FAIL)
            report.diagnosis = (f"Battery voltage dropped {report.voltage_sag_mv} mV under load. "
                                f"Possible causes: worn battery (high internal resistance), poor battery contact.")
        elif report.voltage_sag_mv is None:
            report.status = report.verdict = str(Status.UNKNOWN)
            report.diagnosis = "Battery voltage under load could not be measured; only CPU frequencies were read."
        else:
            report.status = report.verdict = str(Status.PASS)
            report.diagnosis = f"Voltage drop under load: {report.voltage_sag_mv} mV."
        if report.throttling_detected:
            report.diagnosis += (f" CPU frequency varied from {report.max_freq_mhz} to {report.min_freq_mhz} MHz "
                                 f"(throttling or normal frequency scaling).")
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
        Root-only kernel log forensics: pstore/last_kmsg + dmesg matched against data/fault_signatures.json,
        I2C error lines, and JEDEC storage wear registers.
        """
        rep = RootForensicsReport(serial=serial)
        is_rooted, root_type = self.check_root_access(serial)
        rep.is_rooted = is_rooted
        rep.root_type = root_type

        if not is_rooted:
            rep.status = str(Status.NEEDS_ROOT)
            rep.diagnosis = "Root (su) is not available; kernel log and raw register forensics need root."
            rep.evidence.append(Evidence(source="su -c id", raw_excerpt="su did not return uid=0", exit_code=None))
            return rep

        rep.evidence.append(Evidence(source="root check", raw_excerpt=f"Root type: {root_type}", exit_code=0))

        ram = run_read(self.bridge, "cat /sys/fs/pstore/console-ramoops* /sys/fs/pstore/dmesg-ramoops* /proc/last_kmsg",
                       serial=serial, timeout=12, root=True, source="pstore ramoops / last_kmsg (su)", log_content=True)
        dmesg = run_read(self.bridge, "dmesg", serial=serial, timeout=10, root=True, source="dmesg (su)", log_content=True)
        rep.kernel_ramoops_log = "\n".join(ram.text.splitlines()[-250:]) if ram.ok else ""
        rep.evidence.append(Evidence(source=ram.source, raw_excerpt=rep.kernel_ramoops_log[-2000:] or (ram.stderr or "(empty)"),
                                     exit_code=ram.exit_code))
        dmesg_text = "\n".join(dmesg.text.splitlines()[-600:]) if dmesg.ok else ""
        rep.evidence.append(dmesg.evidence())
        combined_logs = f"{rep.kernel_ramoops_log}\n{dmesg_text}"

        matched = []
        for sig in self.load_fault_signatures():
            pattern = sig.get("pattern", "")
            if pattern and re.search(pattern, combined_logs, re.IGNORECASE):
                matched.append(sig)
        rep.matched_fault_signatures = matched
        if matched:
            rep.evidence.append(Evidence(source="data/fault_signatures.json",
                                         raw_excerpt="Matched: " + ", ".join(str(m.get("id")) for m in matched), exit_code=None))

        i2c_errors = []
        for line in combined_logs.splitlines():
            l_str = line.strip()
            if re.search(r"(?i)i2c", l_str) and re.search(r"(?i)(error|timeout|nack|-110\b|-121\b|-5\b)", l_str):
                if len(i2c_errors) < 8 and l_str not in i2c_errors:
                    i2c_errors.append(l_str)
        rep.i2c_bus_errors = i2c_errors
        if i2c_errors:
            rep.evidence.append(Evidence(source="I2C error lines (kernel log)", raw_excerpt="\n".join(i2c_errors), exit_code=None))

        life = self.read_emmc_ufs_lifespan(serial)
        rep.nand_exact_lifetime_a = life.raw_hex_a
        rep.nand_exact_lifetime_b = life.raw_hex_b
        rep.nand_exact_pre_eol = life.pre_eol_info
        rep.nand_wear_status = life.status
        rep.evidence.extend(life.evidence)

        logs_read = ram.ok or dmesg.ok
        problems = []
        crit = [m.get("id") for m in matched if m.get("severity") == "CRITICAL"]
        if crit:
            problems.append(f"Critical fault signatures matched: {', '.join(map(str, crit))}")
        if i2c_errors:
            problems.append(f"{len(i2c_errors)} I2C error lines in the kernel log")
        if life.status == str(Status.FAIL):
            problems.append("Storage wear registers report high wear")

        if problems:
            rep.status = str(Status.FAIL)
            rep.diagnosis = "Findings (possible causes, not a definitive diagnosis): " + " | ".join(problems)
        elif logs_read:
            rep.status = str(Status.PASS)
            rep.diagnosis = f"Kernel logs were read with {root_type}: no matching fault signatures or I2C errors."
        else:
            rep.status = str(Status.UNKNOWN)
            rep.diagnosis = "Root is available but no kernel log could be read."
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
