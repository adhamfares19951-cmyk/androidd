import os
import re
import time
import json
import hashlib
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Any, Callable
from app_paths import desktop_dir, safe_name
from core.adb_bridge import ADBBridge
from core.result import Evidence, Status, CheckResult, ProducesCheckResult, status_from_read, aggregate_status, Msg, M
from core.readers import ReadResult, run_read, read_node, failure_status, parse_int
from core import jedec
from database.db_manager import DatabaseManager

logger = logging.getLogger("BoardDiag")


def load_fault_signature_rules() -> List[Dict[str, Any]]:
    """Rules of data/fault_signatures.json; an empty list (logged) when the file is missing or unreadable."""
    sig_file = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "fault_signatures.json")
    if os.path.exists(sig_file):
        try:
            with open(sig_file, "r", encoding="utf-8") as f:
                return json.load(f).get("rules", [])
        except Exception as e:
            logger.warning(f"Error loading fault_signatures.json: {e}")
    return []

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
class PMICStatus(ProducesCheckResult):
    # `status` here is the device's charging text; the check status is `check_status`
    _check_status_attr = "check_status"
    _check_detail_attr = "fault_flags"

    def _check_value(self):
        return self.voltage_mv, "mV"

    """Battery readings from the fuel gauge / power_supply class. Every value is None when not read."""
    check_id: str = "battery.fuel_gauge"
    voltage_mv: Optional[float] = None
    voltage_source: str = "UNKNOWN"
    voltage_status: str = "UNKNOWN"
    current_ma: Optional[float] = None
    current_source: str = "UNKNOWN"
    current_status: str = "UNKNOWN"
    current_note: Any = None
    charge_full_uah: Optional[int] = None
    charge_full_design_uah: Optional[int] = None
    cycle_count: Optional[int] = None
    cycle_status: Optional[str] = None  # why cycle_count is None (NEEDS_ROOT / UNKNOWN); not a verdict
    temp_c: Optional[float] = None
    temp_source: str = "UNKNOWN"
    temp_status: str = "UNKNOWN"
    status: str = "UNKNOWN"          # charging status text reported by the device
    health: str = "UNKNOWN"          # health text reported by the device
    true_health_pct: Optional[float] = None
    health_verdict: str = "UNKNOWN"  # PASS / FAIL / UNKNOWN / NEEDS_ROOT for capacity vs design
    design_capacity_source: str = "UNKNOWN"  # "kernel" (charge_full_design) or "model_db" (manufacturer spec)
    model_spec_name: str = ""                # marketing name from the model database, if the model is listed
    is_charging: bool = False
    is_ac_plugged: bool = False
    fault_flags: List[Msg] = field(default_factory=list)
    raw_details: Dict[str, Any] = field(default_factory=dict)
    evidence: List[Evidence] = field(default_factory=list)

    @property
    def temperature_c(self) -> Optional[float]:
        return self.temp_c

    FAILED_HEALTH = ("dead", "overheat", "over voltage", "over_voltage", "failure", "unspecified failure")

    @property
    def check_status(self) -> str:
        """Key-check status of the battery: FAIL on a fault flag, a measured capacity below the limit
        (health_verdict FAIL) or a failure health reported by the device itself; else the voltage read status."""
        if self.fault_flags or self.health_verdict == str(Status.FAIL):
            return str(Status.FAIL)
        if str(self.health).strip().lower() in self.FAILED_HEALTH:
            return str(Status.FAIL)
        return self.voltage_status

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        d["check_status"] = self.check_status   # a property: asdict() leaves it out
        return d


@dataclass
class ThermalZone:
    zone_id: int
    zone_type: str
    temp_c: float
    is_overheated: bool
    source: str = ""
    category: str = "other"
    limit_c: Optional[float] = None  # None: zone not classified or reading implausible, reported but not judged
    plausible: bool = True           # False: a running phone cannot be this cold (absent sensor), not judged

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LeakageTestResult(ProducesCheckResult):
    def _check_value(self):
        return self.avg_ma, "mA"

    """Screen-off battery current. Only measured over wireless ADB with no charger connected."""
    check_id: str = "battery.screen_off_current"
    status: str = "UNKNOWN"
    precondition: str = ""   # "usb_connection" / "charger_connected" / "power_state_unknown" when not measured
    sample_count: int = 0
    samples: List[float] = field(default_factory=list)
    avg_ma: Optional[float] = None
    max_ma: Optional[float] = None
    source: str = "UNKNOWN"
    diagnosis: Any = None
    possible_causes: List[Msg] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    @property
    def is_faulty(self) -> bool:
        return self.status == "FAIL"

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        d["is_faulty"] = self.is_faulty
        return d


@dataclass
class ThermalReport(ProducesCheckResult):
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
class BlackboxReport(ProducesCheckResult):
    check_id: str = "board.boot_reason"
    boot_reason: str = "UNKNOWN"
    boot_reason_history: str = "UNKNOWN"
    shutdown_category: str = "UNKNOWN"  # "THERMAL", "UVLO", "WATCHDOG_PANIC", "USER_REQUESTED", "NORMAL", "UNKNOWN"
    has_abnormal_shutdown: bool = False
    dropbox_events: List[str] = field(default_factory=list)
    kernel_ramoops_status: str = "UNKNOWN"  # PASS / FAIL / UNKNOWN / NEEDS_ROOT
    kernel_log_snippets: List[str] = field(default_factory=list)
    status: str = "UNKNOWN"
    diagnosis: Any = None
    possible_causes: List[Msg] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class PanicAnalysis(ProducesCheckResult):
    _check_detail_attr = "summary_verdict"

    check_id: str = "board.kernel_log"
    has_critical_error: bool = False
    emmc_ufs_errors: List[str] = field(default_factory=list)
    watchdog_resets: List[Any] = field(default_factory=list)
    thermal_shutdowns: List[Any] = field(default_factory=list)
    kernel_panics: List[str] = field(default_factory=list)
    dmesg_status: str = "UNKNOWN"
    status: str = "UNKNOWN"
    summary_verdict: Any = None
    blackbox: BlackboxReport = field(default_factory=BlackboxReport)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "check_id": self.check_id,
            "has_critical_error": self.has_critical_error,
            "emmc_ufs_errors": self.emmc_ufs_errors,
            "kernel_panics": self.kernel_panics,
            "dmesg_status": self.dmesg_status,
            "status": self.status,
            "summary_verdict": self.summary_verdict.to_dict() if isinstance(self.summary_verdict, Msg) else self.summary_verdict,
            "watchdog_resets": [w.to_dict() if isinstance(w, Msg) else w for w in self.watchdog_resets],
            "thermal_shutdowns": [w.to_dict() if isinstance(w, Msg) else w for w in self.thermal_shutdowns],
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
    summary_diagnosis: Any = field(default_factory=lambda: M("bootrom.none"))
    possible_causes: List[Msg] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FastChargeDiagnostics(ProducesCheckResult):
    def _check_value(self):
        return self.wattage_w, "W"

    """Charger readings. Measured values and reported maximums are kept apart; None = not read."""
    check_id: str = "charging.input"
    charger_type: str = "UNKNOWN"
    charger_type_source: str = "UNKNOWN"     # the kernel node read, or "dumpsys battery" (Android's power source)
    charge_type: str = "UNKNOWN"
    voltage_mv: Optional[float] = None       # measured USB input voltage
    voltage_v: Optional[float] = None
    current_ma: Optional[float] = None       # measured USB input current
    current_a: Optional[float] = None
    wattage_w: Optional[float] = None        # only when both voltage and current were measured
    max_charging_current_ma: Optional[float] = None  # reported maximum, not a measurement
    max_charging_voltage_mv: Optional[float] = None  # reported maximum, not a measurement
    protocol_name: Any = "UNKNOWN"
    is_fast_charging: Optional[bool] = None
    battery_level: Optional[int] = None
    charge_status: str = "UNKNOWN"
    health_state: str = "UNKNOWN"
    status: str = "UNKNOWN"
    diagnosis: Any = None
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class StorageLifeReport(ProducesCheckResult):
    check_id: str = "storage.jedec_life"
    storage_type: str = "UNKNOWN"
    device_model: str = ""
    life_time_type_a: Any = "UNKNOWN"
    life_time_type_b: Any = "UNKNOWN"
    pre_eol_info: Any = "UNKNOWN"
    raw_hex_a: str = ""
    raw_hex_b: str = ""
    raw_pre_eol: str = ""
    status: str = "UNKNOWN"  # "PASS", "FAIL", "UNKNOWN", "NEEDS_ROOT"
    is_warning: bool = False
    is_critical: bool = False
    health_verdict: str = "UNKNOWN"
    diagnosis: Any = None
    possible_causes: List[Msg] = field(default_factory=list)
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
class StressTestReport(ProducesCheckResult):
    def _check_value(self):
        return self.voltage_sag_mv, "mV"

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
    diagnosis: Any = None
    throttling_note: Any = None
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
            "diagnosis": self.diagnosis.to_dict() if isinstance(self.diagnosis, Msg) else self.diagnosis,
            "throttling_note": self.throttling_note.to_dict() if isinstance(self.throttling_note, Msg) else self.throttling_note,
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
    critical_findings: List[Msg] = field(default_factory=list)
    checks: List[CheckResult] = field(default_factory=list)  # key checks; check_statuses mirrors them

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
            "critical_findings": [f.to_dict() if isinstance(f, Msg) else f for f in self.critical_findings],
            "checks": [c.to_dict() for c in self.checks],
        }


@dataclass
class RootForensicsReport(ProducesCheckResult):
    check_id: str = "board.root_forensics"
    serial: str = ""
    is_rooted: bool = False
    root_type: str = "NON_ROOT"  # "Magisk", "KernelSU", "APatch", "su", "NON_ROOT"
    kernel_ramoops_log: str = ""
    matched_fault_signatures: List[Dict[str, Any]] = field(default_factory=list)
    i2c_bus_errors: List[str] = field(default_factory=list)
    nand_exact_lifetime_a: str = ""
    nand_exact_lifetime_b: str = ""
    nand_exact_pre_eol: Any = ""
    nand_wear_status: str = "UNKNOWN"
    status: str = "UNKNOWN"
    diagnosis: Any = None
    findings: List[Msg] = field(default_factory=list)
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
    message: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class MotherboardDiagnostics:
    def __init__(self, bridge: Optional[ADBBridge] = None, db: Optional[DatabaseManager] = None,
                 model_db=None):
        self.bridge = bridge or ADBBridge()
        self.db = db or DatabaseManager()
        self._model_db = model_db  # core.model_db.ModelDatabase; default loaded on first use

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

    def _model_spec(self, serial: str):
        """Manufacturer spec for this phone's model number, or None (unknown model or unreadable DB)."""
        try:
            model = self.bridge.get_prop("ro.product.model", serial=serial) or ""
            if not model:
                return None
            if self._model_db is None:
                from core.model_db import default_db
                self._model_db = default_db()
            return self._model_db.lookup(model)
        except Exception as e:
            logger.warning(f"Model database lookup failed: {e}")
            return None

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
            pmic.current_note = M("battery.current_note.wifi")
        else:
            pmic.current_note = M("battery.current_note.usb")

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
        pmic.cycle_status = None if pmic.cycle_count is not None else str(failure_status([cyc_read]))
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

        # 7. Capacity vs design. The measured value (charge_full) always comes from the phone; the
        #    design value comes from the kernel, or, if the kernel does not give it, from the
        #    manufacturer's spec in the model database (named, with its source, in the evidence).
        spec = self._model_spec(serial)
        if spec is not None:
            pmic.model_spec_name = spec.name
        spec_cap = spec.design_capacity() if spec is not None else None
        design_uah, design_src = pmic.charge_full_design_uah, "charge_full_design"
        if design_uah:
            pmic.design_capacity_source = "kernel"
            if spec_cap:
                diff = (design_uah / 1000.0 - spec_cap[0]) / spec_cap[0] * 100.0
                if abs(diff) > 10.0:
                    ev.append(Evidence(source=f"model database: {spec.name}",
                                       raw_excerpt=(f"kernel charge_full_design {design_uah // 1000} mAh differs {diff:+.0f}% from "
                                                    f"the manufacturer's {spec_cap[1]} capacity {spec_cap[0]} mAh ({spec.source}). "
                                                    "A non-original battery or a different board revision can cause this."),
                                       exit_code=None))
        elif pmic.charge_full_uah and spec_cap:
            design_uah = spec_cap[0] * 1000
            design_src = f"model database: {spec.name}, {spec_cap[1]} capacity {spec_cap[0]} mAh"
            pmic.design_capacity_source = "model_db"
            ev.append(Evidence(source=f"model database: {spec.name}",
                               raw_excerpt=f"{spec.model}: {spec_cap[1]} capacity {spec_cap[0]} mAh, source {spec.source}"
                                           + (f" (checked {spec.checked})" if spec.checked else ""),
                               exit_code=None))

        if pmic.charge_full_uah and design_uah and design_src == "charge_full_design" \
                and pmic.charge_full_uah == pmic.charge_full_design_uah:
            # Some kernels (seen on a Samsung S23 Ultra) report the design value in charge_full instead of a
            # learned capacity, while the real health register is blocked: a 100% here is not a measurement.
            pmic.health_verdict = str(Status.UNKNOWN)
            ev.append(Evidence(source="charge_full / charge_full_design",
                               raw_excerpt=f"charge_full {pmic.charge_full_uah} uAh equals charge_full_design exactly: "
                                           "the kernel repeats the design value, it did not measure the battery; not judged",
                               exit_code=None))
        elif pmic.charge_full_uah and design_uah:
            pct = round(pmic.charge_full_uah / design_uah * 100.0, 1)
            if 1.0 <= pct <= 150.0:
                pmic.true_health_pct = pct
                pmic.health_verdict = str(Status.PASS if pct >= 80.0 else Status.FAIL)
                ev.append(Evidence(source=f"charge_full / {design_src}",
                                   raw_excerpt=f"{pmic.charge_full_uah} / {design_uah} = {pct}% (FAIL below 80%)",
                                   exit_code=None))
            else:
                pmic.health_verdict = str(Status.UNKNOWN)
                ev.append(Evidence(source=f"charge_full / {design_src}",
                                   raw_excerpt=f"ratio {pct}% is implausible; not used", exit_code=None))
        else:
            pmic.design_capacity_source = pmic.design_capacity_source if design_uah else "UNKNOWN"
            pmic.health_verdict = str(failure_status([cf_read, cfd_read] if not design_uah else [cf_read]))

        # 8. Fault flags only from real readings
        if pmic.is_charging and pmic.voltage_mv is not None and pmic.voltage_mv < 3500.0:
            pmic.fault_flags.append(M("battery.flag.low_voltage_charging", limit=3500))
        if pmic.is_ac_plugged and pmic.is_charging and pmic.current_ma is not None and 0 < abs(pmic.current_ma) < 200.0:
            pmic.fault_flags.append(M("battery.flag.low_charge_current", limit=200))

        raw["voltage_source"] = pmic.voltage_source
        raw["current_source"] = pmic.current_source
        pmic.raw_details = raw
        pmic.evidence = ev
        return pmic

    # ------------------------------------------------------------------
    # Battery current with screen off (screen-off drain test)
    # ------------------------------------------------------------------
    LEAK_AVG_LIMIT_MA = 250.0
    LEAK_PEAK_LIMIT_MA = 350.0

    @staticmethod
    def is_wireless_adb(serial: str) -> bool:
        """True for TCP/IP (adb connect host:port) or mDNS wireless-debugging transports."""
        return ":" in serial or "_adb-tls-connect" in serial

    def _external_power_state(self, serial: str) -> Tuple[Optional[bool], ReadResult]:
        """
        (True if any charger/USB/wireless/dock power is reported, False if all are reported false,
        None if dumpsys battery could not be read or parsed).
        """
        res = run_read(self.bridge, "dumpsys battery", serial=serial, timeout=8)
        if not res.ok:
            return None, res
        flags = {}
        for line in res.text.splitlines():
            key, _, val = line.strip().partition(":")
            if key in ("AC powered", "USB powered", "Wireless powered", "Dock powered"):
                if val.strip().lower() in ("true", "false"):
                    flags[key] = val.strip().lower() == "true"
        if not flags:
            return None, res
        return any(flags.values()), res

    def run_leakage_test(
        self,
        serial: str,
        sample_count: int = 10,
        sample_interval: float = 3.0,
        progress_callback: Optional[Callable[[int, Optional[float], str], None]] = None
    ) -> LeakageTestResult:
        """
        Battery discharge current with the screen off.
        Only meaningful over wireless ADB with no charger connected: over USB the cable supplies
        current, so the reading is not the phone's own drain. Otherwise the result is UNKNOWN.
        A high reading is reported as possible causes, never as a detected short.
        """
        result = LeakageTestResult(sample_count=sample_count)

        if not self.is_wireless_adb(serial):
            result.status = str(Status.UNKNOWN)
            result.precondition = "usb_connection"
            result.diagnosis = M("leak.not_measured.usb")
            result.evidence.append(Evidence(source="adb transport", raw_excerpt=f"serial={serial} (USB transport)", exit_code=None))
            return self._save_leakage(serial, result)

        powered, pres = self._external_power_state(serial)
        result.evidence.append(pres.evidence(f"external power={powered}"))
        if powered is None:
            result.status = str(pres.status)
            result.precondition = "power_state_unknown"
            result.diagnosis = M("leak.not_measured.power_unknown")
            return self._save_leakage(serial, result)
        if powered:
            result.status = str(Status.UNKNOWN)
            result.precondition = "charger_connected"
            result.diagnosis = M("leak.not_measured.charger")
            return self._save_leakage(serial, result)

        if progress_callback:
            progress_callback(0, None, M("leak.progress.screen_off"))
        self.bridge.shell("input keyevent 223", serial=serial, timeout=5)
        time.sleep(4.0)

        c_specs = [(f"{b}/current_now", None) for b in BATTERY_SYSFS_DIRS[:5]] + \
                  [(f"{b}/batt_current", 1.0) for b in BATTERY_SYSFS_DIRS[:5]]
        failed_status: Optional[str] = None
        try:
            for idx in range(1, sample_count + 1):
                value, source, status, ev = self._read_scaled(serial, c_specs, -20000.0, 20000.0, "mA")
                if value is None:
                    failed_status = status
                    result.evidence.extend(ev)
                    if progress_callback:
                        progress_callback(int(idx / sample_count * 100), None, M("leak.progress.sample_unreadable", idx=idx, total=sample_count))
                    break
                ma = abs(value)
                result.samples.append(ma)
                result.source = source
                if idx == 1:
                    result.evidence.extend(ev)
                if progress_callback:
                    progress_callback(int(idx / sample_count * 100), ma, M("leak.progress.sample", idx=idx, total=sample_count, value=ma))
                if idx < sample_count:
                    time.sleep(sample_interval)
        finally:
            self.bridge.shell("input keyevent 224", serial=serial, timeout=5)

        powered_after, pres_after = self._external_power_state(serial)
        result.evidence.append(pres_after.evidence(f"external power after test={powered_after}"))

        if failed_status is not None or len(result.samples) < sample_count:
            result.status = failed_status or str(Status.UNKNOWN)
            result.samples = []
            result.diagnosis = M("leak.incomplete_samples")
            return self._save_leakage(serial, result)
        if powered_after is not False:
            result.status = str(Status.UNKNOWN)
            result.precondition = "charger_connected"
            result.samples = []
            result.diagnosis = M("leak.power_changed")
            return self._save_leakage(serial, result)

        result.avg_ma = round(sum(result.samples) / len(result.samples), 1)
        result.max_ma = round(max(result.samples), 1)
        result.evidence.append(Evidence(source=result.source,
                                        raw_excerpt="samples (mA): " + ", ".join(str(s) for s in result.samples),
                                        exit_code=None))
        if result.avg_ma > self.LEAK_AVG_LIMIT_MA or result.max_ma > self.LEAK_PEAK_LIMIT_MA:
            result.status = str(Status.FAIL)
            result.diagnosis = M("leak.high", avg=result.avg_ma, peak=result.max_ma,
                                 avg_limit=self.LEAK_AVG_LIMIT_MA, peak_limit=self.LEAK_PEAK_LIMIT_MA)
            result.possible_causes = [M("leak.cause.wakelocks"), M("leak.cause.radio"), M("leak.cause.board")]
        else:
            result.status = str(Status.PASS)
            result.diagnosis = M("leak.normal", avg=result.avg_ma, peak=result.max_ma,
                                 avg_limit=self.LEAK_AVG_LIMIT_MA, peak_limit=self.LEAK_PEAK_LIMIT_MA)
        return self._save_leakage(serial, result)

    def _save_leakage(self, serial: str, result: LeakageTestResult) -> LeakageTestResult:
        try:
            self.db.save_leakage_test(
                device_serial=serial,
                samples=result.samples,
                avg_leakage_ma=result.avg_ma,
                max_leakage_ma=result.max_ma,
                is_faulty=result.status == str(Status.FAIL),
                verdict=f"{result.status}: {result.diagnosis.key if isinstance(result.diagnosis, Msg) else result.diagnosis}"
            )
        except Exception as e:
            logger.warning(f"Could not store leakage result: {e}")
        return result

    # ------------------------------------------------------------------
    # Thermal zones (per-category thresholds from data/thermal_thresholds.json)
    # ------------------------------------------------------------------
    _thermal_rules_cache: Optional[Dict[str, Any]] = None

    @classmethod
    def load_thermal_rules(cls, path: Optional[str] = None) -> Dict[str, Any]:
        if path is None and cls._thermal_rules_cache is not None:
            return cls._thermal_rules_cache
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        file_path = path or os.path.join(base_dir, "data", "thermal_thresholds.json")
        try:
            with open(file_path, "r", encoding="utf-8") as f:
                rules = json.load(f)
        except Exception as e:
            logger.warning(f"thermal_thresholds.json not loaded ({e}); zones will be reported without limits")
            rules = {"categories": {}, "hal_types": {}, "non_temperature_hal_types": [], "name_patterns": []}
        if path is None:
            cls._thermal_rules_cache = rules
        return rules

    def classify_zone(self, name: str, hal_type: Optional[int] = None) -> Tuple[str, Optional[float]]:
        """Return (category, limit_c). Unknown zones are ('other', None): reported, not judged."""
        rules = self.load_thermal_rules()
        category = None
        if hal_type is not None:
            category = rules.get("hal_types", {}).get(str(hal_type))
        if category is None:
            for pattern, cat in rules.get("name_patterns", []):
                if re.search(pattern, name):
                    category = cat
                    break
        if category is None:
            return "other", None
        limit = rules.get("categories", {}).get(category, {}).get("limit_c")
        return category, (float(limit) if limit is not None else None)

    # Below this a running phone's sensor reading is not believable (seen: an absent power-amplifier
    # sensor reporting -26.0 while the battery read 31 C). Such a zone is listed but never judged.
    PLAUSIBLE_MIN_C = -20.0

    def _make_zone(self, zone_id: int, name: str, temp_c: float, source: str, hal_type: Optional[int] = None) -> ThermalZone:
        category, limit = self.classify_zone(name, hal_type)
        plausible = temp_c >= self.PLAUSIBLE_MIN_C
        if not plausible:
            limit = None
        return ThermalZone(zone_id=zone_id, zone_type=name, temp_c=temp_c,
                           is_overheated=(limit is not None and temp_c > limit),
                           source=source, category=category, limit_c=limit, plausible=plausible)

    def read_thermal(self, serial: str) -> ThermalReport:
        """Read thermal zones from dumpsys thermalservice (non-root), falling back to sysfs."""
        report = ThermalReport()
        zones: Dict[str, ThermalZone] = {}
        non_temp = set(self.load_thermal_rules().get("non_temperature_hal_types", []))

        res = run_read(self.bridge, "dumpsys thermalservice", serial=serial, timeout=8)
        reads = [res]
        if res.ok:
            for m in re.finditer(r"Temperature\{mValue=(-?[\d.]+),\s*mType=(-?\d+),\s*mName=([^,}]+)", res.text):
                name = m.group(3).strip()
                hal_type = int(m.group(2))
                if hal_type in non_temp:
                    continue  # BCL voltage/current/percentage: not a temperature
                try:
                    val = float(m.group(1))
                except ValueError:
                    continue
                if name in zones or not (-40.0 <= val <= 150.0):
                    continue
                zones[name] = self._make_zone(len(zones), name, round(val, 1), "dumpsys thermalservice", hal_type)
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
                    zones[f"{name}#{z_id}"] = self._make_zone(z_id, name, t_c, path)
                report.evidence.append(sys_res.evidence(f"{len(zones)} zones parsed"))
            else:
                report.evidence.append(sys_res.evidence())

        report.zones = list(zones.values())
        judged = [z for z in report.zones if z.limit_c is not None]
        if any(z.is_overheated for z in judged):
            report.status = str(Status.FAIL)
        elif judged:
            report.status = str(Status.PASS)
        elif report.zones:
            report.status = str(Status.UNKNOWN)  # temperatures read, but no zone could be classified
        else:
            report.status = str(failure_status(reads))
        return report

    def scan_thermal_map(self, serial: str) -> List[ThermalZone]:
        return self.read_thermal(serial).zones

    # ------------------------------------------------------------------
    # Boot reason, dropbox and pstore (ramoops)
    # ------------------------------------------------------------------
    _ABNORMAL_RULES = [
        ("THERMAL", r"(thermal|overheat|crit_temp|tsens)", "bootreason.thermal",
         ["bootreason.cause.cooling", "bootreason.cause.battery", "bootreason.cause.load", "bootreason.cause.sensor"]),
        ("UVLO", r"(uvlo|undervoltage|vbat_low|battery_pull|brownout)", "bootreason.uvlo",
         ["bootreason.cause.worn_battery", "bootreason.cause.connector", "bootreason.cause.power_circuit"]),
        ("WATCHDOG_PANIC", r"(watchdog|wdog|kernel_panic|subsys_restart|oops|softlockup|hardlockup)", "bootreason.watchdog",
         ["bootreason.cause.software", "bootreason.cause.firmware", "bootreason.cause.memory_soc"]),
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
        for category, pattern, diag_key, cause_keys in self._ABNORMAL_RULES:
            if re.search(pattern, combined):
                bb.shutdown_category = category
                bb.has_abnormal_shutdown = True
                bb.possible_causes = [M(k) for k in cause_keys]
                bb.status = str(Status.FAIL)
                bb.diagnosis = M(diag_key)
                return bb

        if b_reason:
            bb.shutdown_category = "USER_REQUESTED" if re.search(r"(userrequested|power_menu|shell|recovery|reboot)", combined) else "NORMAL"
            bb.status = str(Status.PASS)
            bb.diagnosis = M("bootreason.normal", reason=b_reason)
        else:
            bb.shutdown_category = "UNKNOWN"
            bb.status = str(Status.UNKNOWN)
            bb.diagnosis = M("bootreason.not_reported")
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
            analysis.summary_verdict = M("kernellog.errors_found")
        else:
            analysis.status = analysis.dmesg_status
            if analysis.dmesg_status == str(Status.PASS):
                analysis.summary_verdict = M("kernellog.clean")
            elif analysis.dmesg_status == str(Status.NEEDS_ROOT):
                analysis.summary_verdict = M("kernellog.needs_root")
            else:
                analysis.summary_verdict = M("kernellog.unreadable")
        return analysis

    def run_full_diagnostics(self, serial: str) -> BoardDiagnosticResult:
        logger.info(f"Running board diagnostics on {serial}...")
        pmic = self.analyze_pmic(serial)
        thermal = self.read_thermal(serial)
        panic = self.inspect_panic_and_emmc(serial)

        findings: List[Msg] = list(pmic.fault_flags)
        hot = [f"{z.zone_type} [{z.category}] {z.temp_c}°C > {z.limit_c:g}°C" for z in thermal.zones if z.is_overheated]
        if hot:
            findings.append(M("thermal.alert", zones=", ".join(hot)))
        if panic.status == str(Status.FAIL):
            findings.append(panic.summary_verdict)

        checks = [pmic.to_check_result(), thermal.to_check_result(),
                  panic.blackbox.to_check_result(), panic.to_check_result()]
        check_statuses = {c.check_id: str(c.status) for c in checks}
        overall = aggregate_status([c.status for c in checks])

        return BoardDiagnosticResult(
            serial=serial,
            pmic=pmic,
            thermal_zones=thermal.zones,
            panic_analysis=panic,
            thermal_status=thermal.status,
            thermal_evidence=thermal.evidence,
            check_statuses=check_statuses,
            overall_health=str(overall),
            critical_findings=findings,
            checks=checks,
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
            diag.summary_diagnosis = M("bootrom.qualcomm_edl")
            diag.possible_causes = [M("bootrom.cause.edl_bootloader"), M("bootrom.cause.edl_gpt"),
                                    M("bootrom.cause.edl_hardware"), M("bootrom.cause.edl_testpoint")]
        elif diag.is_mediatek_brom:
            diag.summary_diagnosis = M("bootrom.mediatek_brom")
            diag.possible_causes = [M("bootrom.cause.mtk_preloader"), M("bootrom.cause.mtk_flash"),
                                    M("bootrom.cause.mtk_power"), M("bootrom.cause.mtk_key")]
        elif diag.is_samsung_odin:
            diag.summary_diagnosis = M("bootrom.samsung_odin")
            diag.possible_causes = [M("bootrom.cause.odin_softbrick"), M("bootrom.cause.odin_lock"),
                                    M("bootrom.cause.odin_pit")]
        elif diag.is_usb_cycling:
            diag.summary_diagnosis = M("bootrom.usb_cycling", observed=diag.cycling_details)
            diag.possible_causes = [M("bootrom.cause.cycling_bootloop"), M("bootrom.cause.cycling_usb_lines"),
                                    M("bootrom.cause.cycling_solder")]
        elif ports and all(p.get("vid") for p in ports):
            # Only phone-vendor devices in a mode this program does not know: a fact, not a fault hypothesis.
            p = ports[0]
            diag.summary_diagnosis = M("bootrom.phone_present", vendor=p.get("vendor"), vid=p.get("vid"),
                                       pid=p.get("pid"), serial=p.get("serial"))
        elif ports:
            diag.summary_diagnosis = M("bootrom.other_port", name=ports[0].get("name"))
            diag.possible_causes = [M("bootrom.cause.vendor_mode")]
        else:
            diag.summary_diagnosis = M("bootrom.none")

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
            result.charger_type_source = type_read.source
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
            # Kernel type blocked (SELinux): Android's own power source flags still name the source
            # (USB / AC / Wireless / Dock). A source, not a measurement: the status and the protocol stay as they are.
            source_note = ""
            if not parsed_type:
                powered = [name for flag, name in (("AC powered", "AC"), ("USB powered", "USB"),
                                                   ("Wireless powered", "Wireless"), ("Dock powered", "Dock"))
                           if re.search(rf"(?m)^\s*{flag}:\s*true\s*$", db.text)]
                if powered:
                    result.charger_type = "+".join(powered)
                    result.charger_type_source = "dumpsys battery"
                    source_note = "power source: " + ", ".join(f"{p} powered=true" for p in powered)
            result.evidence.append(db.evidence(source_note))
        else:
            result.evidence.append(db.evidence())

        if result.voltage_v is not None and result.current_a is not None:
            result.wattage_w = round(result.voltage_v * result.current_a, 2)

        t_lower = parsed_type.lower() if parsed_type else ""
        if "pd" in t_lower:
            result.protocol_name, result.is_fast_charging = M("charging.protocol.pd"), True
        elif "hvdcp" in t_lower or "qc" in t_lower:
            result.protocol_name, result.is_fast_charging = M("charging.protocol.hvdcp"), True
        elif "dcp" in t_lower:
            result.protocol_name, result.is_fast_charging = M("charging.protocol.dcp"), None
        elif "cdp" in t_lower:
            result.protocol_name, result.is_fast_charging = M("charging.protocol.cdp"), False
        elif "sdp" in t_lower:
            result.protocol_name, result.is_fast_charging = M("charging.protocol.sdp"), False
        elif t_lower:
            result.protocol_name = M("charging.protocol.reported", type=result.charger_type)
        if result.is_fast_charging is None and result.wattage_w is not None:
            result.is_fast_charging = result.wattage_w >= 15.0

        if parsed_type or result.voltage_mv is not None or result.current_ma is not None:
            result.status = str(Status.PASS)
        else:
            result.status = str(failure_status(reads))

        result.diagnosis = M("charging.summary", type=result.charger_type, protocol=result.protocol_name,
                             volts=result.voltage_v, amps=result.current_a, watts=result.wattage_w,
                             level=result.battery_level, state=result.charge_status)
        return result

    def reset_battery_fuel_gauge_stats(self, serial: str) -> Tuple[bool, Msg]:
        """
        Reset battery statistics (dumpsys batterystats --reset).
        Clears Android's software battery usage history only; it does not touch the fuel gauge chip.
        """
        logger.info(f"Resetting battery statistics on {serial}...")
        res = run_read(self.bridge, "dumpsys batterystats --reset", serial=serial, timeout=10)
        ok = res.exit_code == 0 and res.status != Status.NEEDS_ROOT
        output = (res.text or res.stderr or "").strip()[:300]
        return ok, M("battery.reset_stats.result", exit=res.exit_code, output=output)

    # Alias for backwards compatibility
    def reset_battery_stats_and_gauge(self, serial: str) -> Tuple[bool, Msg]:
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
            report.diagnosis = M("storage.wear.high")
            report.possible_causes = [M("storage.cause.endurance")]
        elif status == Status.PASS:
            report.diagnosis = M("storage.wear.read", life=report.life_time_type_a, eol=report.pre_eol_info)
        elif status == Status.NEEDS_ROOT:
            report.diagnosis = M("storage.wear.needs_root")
        else:
            report.diagnosis = M("storage.wear.not_exposed")
        return report

    def benchmark_storage_speed(self, serial: str, test_size_mb: int = 64) -> Tuple[Optional[float], Optional[float], str, Msg]:
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

        if write_speed is None:
            status = Status.NEEDS_ROOT if w.status == Status.NEEDS_ROOT else Status.UNKNOWN
            diag = M("bench.unparsed", write=write_speed, read=read_speed)
        elif write_speed < 25.0:
            status = Status.FAIL
            diag = M("bench.slow", write=write_speed, read=read_speed, limit=25)
        else:
            status = Status.PASS
            diag = M("bench.ok", write=write_speed, read=read_speed)
        return write_speed, read_speed, str(status), diag

    STRESS_MARKER = "adp_stress_loop"
    # "[a]dp..." matches the loops but not the cleanup shell, whose own command line holds "[a]dp..."
    _STRESS_PATTERN = "[a]dp_stress_loop"

    def _stop_stress_loops(self, serial: str) -> Evidence:
        """Kill the stress loops by name and count what is left (each loop also ends at its own deadline)."""
        res = run_read(self.bridge,
                       f"pkill -f '{self._STRESS_PATTERN}'; sleep 1; ps -A -o ARGS | grep -c '{self._STRESS_PATTERN}'",
                       serial=serial, timeout=8, source="stress cleanup (pkill, then ps count)")
        left = parse_int(res.text)
        if left:
            logger.warning(f"{left} stress loop(s) still running on {serial}; they end at their deadline")
            return res.evidence(f"{left} stress loop(s) still running after kill; each ends at its deadline")
        return res.evidence("stress loops left running: " + ("0" if left == 0 else "unknown"))

    def run_cpu_pmic_stress_test(
        self,
        serial: str,
        duration_sec: int = 30,
        progress_callback: Optional[Callable[[StressTestSnapshot, str], None]] = None
    ) -> StressTestReport:
        """
        CPU load test: starts 4 busy loops, samples CPU frequency, battery voltage and one thermal
        zone every 2 seconds. Unreadable samples stay None.
        Each loop checks its own deadline, so it ends even if the cleanup below never runs (lost
        connection, crash). It is not wrapped in `timeout`: killing that wrapper orphaned the loop,
        which then ran forever at 100% CPU (seen on a Redmi Note 9).
        """
        logger.info(f"Starting {duration_sec}s CPU stress test on {serial}...")
        report = StressTestReport(duration_seconds=duration_sec)

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
        # $0 = STRESS_MARKER names the loops in `ps`; $1 = the deadline (epoch seconds) each loop checks.
        launch_cmd = (
            f"end=$(( $(date +%s) + {limit} )); for i in 1 2 3 4; do "
            f"sh -c 'while [ $(date +%s) -lt $1 ]; do i=0; while [ $i -lt 5000 ]; do i=$((i+1)); done; done' "
            f"{self.STRESS_MARKER} $end >/dev/null 2>&1 & done"
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
                    progress_callback(snap, M("stress.progress", second=snap.second, total=duration_sec,
                                              freq=snap.avg_freq_mhz, volts=cur_v, temp=cur_t))
        finally:
            report.evidence.append(self._stop_stress_loops(serial))

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
            report.diagnosis = M("stress.no_data")
        elif report.severe_sag_detected:
            report.status = report.verdict = str(Status.FAIL)
            report.diagnosis = M("stress.sag", sag=report.voltage_sag_mv)
        elif report.voltage_sag_mv is None:
            report.status = report.verdict = str(Status.UNKNOWN)
            report.diagnosis = M("stress.no_voltage")
        else:
            report.status = report.verdict = str(Status.PASS)
            report.diagnosis = M("stress.ok", sag=report.voltage_sag_mv)
        if report.throttling_detected:
            report.throttling_note = M("stress.freq_range", max=report.max_freq_mhz, min=report.min_freq_mhz)
        return report

    def load_fault_signatures(self) -> List[Dict[str, Any]]:
        """Fault signatures from data/fault_signatures.json (shared with the offline log analyzer)."""
        return load_fault_signature_rules()

    def check_root_access(self, serial: str) -> Tuple[bool, str]:
        """
        Strict root verification. Returns (is_rooted, root_type).
        root_type: 'Magisk', 'KernelSU', 'APatch', 'su (Standard)', or 'NON_ROOT'.
        """
        cached = self.bridge.get_cached_capability(serial, "root_info")
        if cached is not None:
            return cached

        # ADBBridge.is_root_available needs a real uid 0 and also works without shell_v2
        if not self.bridge.is_root_available(serial):
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
            rep.diagnosis = M("forensics.no_root")
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
        problems: List[Msg] = []
        crit = [m.get("id") for m in matched if str(m.get("severity", "")).upper() in ("CRITICAL", "HIGH")]
        if crit:
            problems.append(M("forensics.finding.signatures", ids=", ".join(map(str, crit))))
        if i2c_errors:
            problems.append(M("forensics.finding.i2c", count=len(i2c_errors)))
        if life.status == str(Status.FAIL):
            problems.append(M("forensics.finding.storage"))
        rep.findings = problems

        if problems:
            rep.status = str(Status.FAIL)
            rep.diagnosis = M("forensics.findings", items=problems)
        elif logs_read:
            rep.status = str(Status.PASS)
            rep.diagnosis = M("forensics.clean", root_type=root_type)
        else:
            rep.status = str(Status.UNKNOWN)
            rep.diagnosis = M("forensics.no_logs")
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
            result.message = M("efs.needs_root")
            return result

        if not dest_dir:
            dest_dir = os.path.join(desktop_dir(), f"Android_EFS_Backup_{safe_name(serial)}_{int(time.time())}")

        os.makedirs(dest_dir, exist_ok=True)
        result.backup_folder = dest_dir

        if progress_callback:
            progress_callback(M("efs.progress.scanning", root_type=root_type))

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
            result.message = M("efs.no_by_name")
            return result

        code_ls, out_ls, _ = self.bridge.shell(f"ls -la {found_by_name} 2>/dev/null", serial=serial, timeout=10, root=True)
        if code_ls != 0 or not out_ls:
            result.success = False
            result.message = M("efs.list_failed", path=found_by_name)
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
            result.message = M("efs.none_matched")
            return result

        if progress_callback:
            progress_callback(M("efs.progress.found", count=len(discovered_targets), names=", ".join(discovered_targets)))

        backed_up = []
        total_size = 0
        hashes = {}

        for part in discovered_targets:
            remote_node = f"{found_by_name}/{part}"
            remote_tmp = f"/data/local/tmp/{part}.img"
            local_file = os.path.join(dest_dir, f"{part}.img")

            if progress_callback:
                progress_callback(M("efs.progress.dumping", part=part))

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
                    progress_callback(M("efs.progress.pulled", part=part, kb=round(f_size / 1024, 1), sha=hashes[part][:12]))

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
        result.message = M("efs.done", count=len(backed_up), mb=round(total_size / (1024 * 1024), 2))
        return result
