import os
import re
import time
import wave
import math
import struct
import json
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Any
from core.adb_bridge import ADBBridge
from core.result import Evidence, Status, CheckResult, status_from_read, aggregate_status, Msg, M
from core.readers import ReadResult, run_read, read_node, failure_status, parse_int, parse_bool_feature

logger = logging.getLogger("HardwareTester")


def _ev_dict(items: List[Evidence]) -> List[Dict[str, Any]]:
    return [e.to_dict() if isinstance(e, Evidence) else e for e in items]


@dataclass
class SensorInfo:
    """One sensor as reported by `dumpsys sensorservice`. Fields not printed by the device stay None."""
    handle: str
    name: str
    vendor: str
    sensor_type: str
    version: Optional[str] = None
    power_ma: Optional[float] = None
    max_range: Optional[float] = None
    resolution: Optional[float] = None
    status: str = "Reported"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CameraInfo:
    """One camera as reported by `dumpsys media.camera`. Unparsed fields stay None / UNKNOWN."""
    camera_id: str
    facing: str = "UNKNOWN"
    orientation: Optional[int] = None
    flash_available: Optional[bool] = None
    status: str = "Reported"
    details: Any = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AudioSubsystemInfo:
    mode: str = "UNKNOWN"
    ringer_mode: str = "UNKNOWN"
    speaker_on: Optional[bool] = None
    headset_connected: Optional[bool] = None
    stream_volumes: Dict[str, str] = field(default_factory=dict)
    audio_devices: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FingerprintDiagnostics:
    check_id: str = "fingerprint.sensor"
    vendor: str = "UNKNOWN"
    is_detected: bool = False
    is_functional: Optional[bool] = None
    is_flex_disconnected: bool = False
    error_status: str = "UNKNOWN"
    status: str = "UNKNOWN"
    diagnosis: Any = None
    possible_causes: List[Msg] = field(default_factory=list)
    raw_details: str = ""
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = _ev_dict(self.evidence)
        return d


@dataclass
class PhysicalButtonsDiagnostics:
    """Key test: PASS per key only when both DOWN and UP events were captured from getevent."""
    check_id: str = "buttons.keys"
    vol_up_status: str = "UNKNOWN"
    vol_down_status: str = "UNKNOWN"
    power_status: str = "UNKNOWN"
    is_any_key_stuck: bool = False
    stuck_keys: List[str] = field(default_factory=list)
    status: str = "UNKNOWN"
    diagnosis: Any = None
    captured_events: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = _ev_dict(self.evidence)
        return d


@dataclass
class SIMBasebandDiagnostics:
    check_id: str = "telephony.sim_baseband"
    sim1_state: str = "UNKNOWN"
    sim2_state: str = "UNKNOWN"
    carrier_name: str = "UNKNOWN"
    network_type: str = "UNKNOWN"
    signal_strength: str = "UNKNOWN"
    baseband_version: str = "UNKNOWN"
    has_telephony_feature: Optional[bool] = None
    is_rf_baseband_faulty: bool = False
    is_tray_det_faulty: bool = False
    status: str = "UNKNOWN"
    diagnosis: Any = None
    possible_causes: List[Msg] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = _ev_dict(self.evidence)
        return d


@dataclass
class WiFiDiagnostics:
    check_id: str = "wifi.adapter"
    state: str = "UNKNOWN"
    mac_address: str = "UNKNOWN"
    ssid: str = ""
    rssi_dbm: Optional[int] = None
    link_speed_mbps: Optional[int] = None
    frequency_mhz: Optional[int] = None
    driver_status: str = "UNKNOWN"
    is_hardware_faulty: bool = False
    status: str = "UNKNOWN"
    diagnosis: Any = None
    possible_causes: List[Msg] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = _ev_dict(self.evidence)
        return d


@dataclass
class BluetoothDiagnostics:
    check_id: str = "bluetooth.adapter"
    state: str = "UNKNOWN"
    mac_address: str = "UNKNOWN"
    name: str = "UNKNOWN"
    ble_supported: Optional[bool] = None
    is_hardware_faulty: bool = False
    status: str = "UNKNOWN"
    diagnosis: Any = None
    possible_causes: List[Msg] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = _ev_dict(self.evidence)
        return d


@dataclass
class PeripheralChipsDiagnostics:
    """Features DECLARED by the system image (pm has-system-feature). This is not a hardware test."""
    check_id: str = "system.declared_features"
    gps_present: Optional[bool] = None
    gps_status: str = "UNKNOWN"
    nfc_present: Optional[bool] = None
    nfc_status: str = "UNKNOWN"
    usb_otg_supported: Optional[bool] = None
    status: str = "UNKNOWN"
    diagnosis: Any = None
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = _ev_dict(self.evidence)
        return d


@dataclass
class DisplayBacklightDiagnostics:
    check_id: str = "display.backlight"
    brightness_now: Optional[int] = None
    max_brightness: Optional[int] = None
    brightness_source: str = "UNKNOWN"
    panel_detected: Optional[bool] = None
    is_backlight_faulty: bool = False
    is_panel_disconnected: bool = False
    status: str = "UNKNOWN"
    diagnosis: Any = None
    possible_causes: List[Msg] = field(default_factory=list)
    dsi_error_logs: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = _ev_dict(self.evidence)
        return d


@dataclass
class AudioCodecReport:
    check_id: str = "audio.subsystem"
    cards_detected: List[str] = field(default_factory=list)
    pcm_devices: List[str] = field(default_factory=list)
    audio_flinger_alive: Optional[bool] = None
    output_threads: int = 0
    codec_chip_name: str = "UNKNOWN"
    is_codec_faulty: bool = False
    status: str = "UNKNOWN"
    diagnosis: Any = None
    possible_causes: List[Msg] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = _ev_dict(self.evidence)
        return d


@dataclass
class DisplayPanelInfo:
    """Kernel-reported display panel controller name and verified-boot properties. Not a proof of originality."""
    check_id: str = "display.panel_info"
    panel_controller_name: str = "UNKNOWN"
    driver_source: str = "UNKNOWN"
    verified_boot_state: str = "UNKNOWN"
    vbmeta_device_state: str = "UNKNOWN"
    status: str = "UNKNOWN"
    diagnosis: Any = None
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = _ev_dict(self.evidence)
        return d


@dataclass
class HardwareAuditReport:
    serial: str
    sensors: List[SensorInfo] = field(default_factory=list)
    sensors_status: str = "UNKNOWN"
    missing_critical_sensors: List[str] = field(default_factory=list)  # common sensors NOT reported (not a fault by itself)
    cameras: List[CameraInfo] = field(default_factory=list)
    cameras_status: str = "UNKNOWN"
    audio: AudioSubsystemInfo = field(default_factory=AudioSubsystemInfo)
    audio_codec: AudioCodecReport = field(default_factory=AudioCodecReport)
    fingerprint: FingerprintDiagnostics = field(default_factory=FingerprintDiagnostics)
    buttons: PhysicalButtonsDiagnostics = field(default_factory=PhysicalButtonsDiagnostics)
    sim_baseband: SIMBasebandDiagnostics = field(default_factory=SIMBasebandDiagnostics)
    display_backlight: DisplayBacklightDiagnostics = field(default_factory=DisplayBacklightDiagnostics)
    display_panel_info: DisplayPanelInfo = field(default_factory=DisplayPanelInfo)
    wifi: WiFiDiagnostics = field(default_factory=WiFiDiagnostics)
    bluetooth: BluetoothDiagnostics = field(default_factory=BluetoothDiagnostics)
    peripherals: PeripheralChipsDiagnostics = field(default_factory=PeripheralChipsDiagnostics)
    vibrator_functional: Optional[bool] = None  # software cannot sense vibration; confirmed by the technician only
    pointer_location_active: bool = False
    check_statuses: Dict[str, str] = field(default_factory=dict)
    summary: Any = None
    status: str = "UNKNOWN"
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "serial": self.serial,
            "sensors": [s.to_dict() for s in self.sensors],
            "sensors_status": self.sensors_status,
            "missing_critical_sensors": self.missing_critical_sensors,
            "cameras": [c.to_dict() for c in self.cameras],
            "cameras_status": self.cameras_status,
            "audio": self.audio.to_dict(),
            "audio_codec": self.audio_codec.to_dict(),
            "fingerprint": self.fingerprint.to_dict(),
            "buttons": self.buttons.to_dict(),
            "sim_baseband": self.sim_baseband.to_dict(),
            "display_backlight": self.display_backlight.to_dict(),
            "display_panel_info": self.display_panel_info.to_dict(),
            "wifi": self.wifi.to_dict(),
            "bluetooth": self.bluetooth.to_dict(),
            "peripherals": self.peripherals.to_dict(),
            "vibrator_functional": self.vibrator_functional,
            "pointer_location_active": self.pointer_location_active,
            "check_statuses": dict(self.check_statuses),
            "summary": self.summary.to_dict() if isinstance(self.summary, Msg) else self.summary,
            "status": self.status,
            "evidence": _ev_dict(self.evidence)
        }


COMMON_SENSORS = [
    ("Accelerometer", ["accelerometer"]),
    ("Gyroscope", ["gyroscope"]),
    ("Proximity Sensor", ["proximity"]),
    ("Ambient Light Sensor", ["light"]),
    ("Magnetometer / Compass", ["magnetic_field", "magnetometer", "compass"]),
]

# "0x0000000b) BMI160 Accelerometer | Bosch | ver: 1 | type: android.sensor.accelerometer(1) | ..."
_SENSOR_LINE = re.compile(
    r"0x[0-9a-fA-F]+\)\s*(?P<name>[^|]+?)\s*\|\s*(?P<vendor>[^|]+?)\s*\|\s*ver(?:sion)?:?\s*(?P<ver>\d+)\s*\|\s*type:?\s*(?P<type>[\w.]+)",
    re.IGNORECASE,
)


class HardwareTester:
    def __init__(self, bridge: Optional[ADBBridge] = None):
        self.bridge = bridge or ADBBridge()
        self.last_sensor_read: Optional[ReadResult] = None
        self.last_camera_read: Optional[ReadResult] = None

    # ------------------------------------------------------------------
    # Reported sensors
    # ------------------------------------------------------------------
    def audit_sensors(self, serial: str) -> Tuple[List[SensorInfo], List[str]]:
        """Sensors reported by sensorservice, plus the common sensors that were NOT reported (only if the read succeeded)."""
        sensors: List[SensorInfo] = []
        res = run_read(self.bridge, "dumpsys sensorservice", serial=serial, timeout=12)
        self.last_sensor_read = res
        if not res.ok:
            return sensors, []

        seen = set()
        for line in res.text.splitlines():
            m = _SENSOR_LINE.search(line)
            if not m:
                continue
            key = (m.group("name").strip(), m.group("type").strip())
            if key in seen:
                continue
            seen.add(key)
            p = re.search(r"power\s*[:=]\s*([0-9.]+)\s*mA", line, re.IGNORECASE)
            r = re.search(r"maxRange\s*[:=]\s*([0-9.]+)", line, re.IGNORECASE)
            q = re.search(r"resolution\s*[:=]\s*([0-9.eE\-]+)", line, re.IGNORECASE)
            sensors.append(SensorInfo(
                handle=f"0x{len(sensors) + 1:02X}",
                name=key[0],
                vendor=m.group("vendor").strip(),
                sensor_type=key[1],
                version=m.group("ver"),
                power_ma=float(p.group(1)) if p else None,
                max_range=float(r.group(1)) if r else None,
                resolution=float(q.group(1)) if q else None,
            ))

        missing: List[str] = []
        if sensors:
            reported = [f"{s.name} {s.sensor_type}".lower() for s in sensors]
            for label, keywords in COMMON_SENSORS:
                if not any(kw in r for r in reported for kw in keywords):
                    missing.append(label)
        return sensors, missing

    def trigger_vibrator(self, serial: str, duration_ms: int = 1000) -> bool:
        for cmd in (f"cmd vibrator_manager synced oneshot {duration_ms}", f"cmd vibrator vibrate {duration_ms}"):
            code, _, err = self.bridge.shell(cmd, serial=serial, timeout=5)
            if code == 0 and not err:
                return True
        return False

    # ------------------------------------------------------------------
    # Cameras
    # ------------------------------------------------------------------
    def audit_cameras(self, serial: str) -> List[CameraInfo]:
        """Cameras listed by the camera service. Never invents cameras: empty list when nothing was parsed."""
        cameras: List[CameraInfo] = []
        res = run_read(self.bridge, "dumpsys media.camera", serial=serial, timeout=10)
        self.last_camera_read = res
        if not res.ok:
            return cameras

        current: Optional[CameraInfo] = None
        ids_seen = set()
        for line in res.text.splitlines():
            s = line.strip()
            id_match = (re.search(r"Camera ID:\s*([0-9a-zA-Z_]+)", s)
                        or re.search(r"== Camera HAL device \S*/(\d+)\b", s)
                        or re.search(r"^Device\s+(\d+)\s+(?:is|static)", s))
            if id_match and id_match.group(1) not in ids_seen:
                ids_seen.add(id_match.group(1))
                current = CameraInfo(camera_id=id_match.group(1))
                cameras.append(current)
                continue
            if current is None:
                continue
            low = s.lower()
            if "facing" in low and current.facing == "UNKNOWN":
                if "front" in low:
                    current.facing = "Front"
                elif "back" in low:
                    current.facing = "Back"
                elif "external" in low:
                    current.facing = "External"
            o = re.search(r"orientation[:=]\s*(\d+)", s, re.IGNORECASE)
            if o and current.orientation is None:
                current.orientation = int(o.group(1))
            if "flash" in low and "available" in low and current.flash_available is None:
                if re.search(r"(true|\b1\b|TRUE)", s):
                    current.flash_available = True
                elif re.search(r"(false|\b0\b|FALSE)", s):
                    current.flash_available = False

        if not cameras:
            n = re.search(r"Number of camera devices:\s*(\d+)", res.text)
            if n:
                for i in range(int(n.group(1))):
                    cameras.append(CameraInfo(camera_id=str(i), details=M("camera.count_only")))
        for c in cameras:
            if not c.details:
                c.details = M("camera.facing_detail", facing=M(f"camera.facing.{c.facing}"))
        return cameras

    def audit_audio(self, serial: str) -> AudioSubsystemInfo:
        audio = AudioSubsystemInfo()
        res = run_read(self.bridge, "dumpsys audio", serial=serial, timeout=10)
        if res.ok:
            for line in res.text.splitlines():
                s = line.strip()
                m = re.match(r"Mode:\s*([A-Za-z0-9_]+)", s)
                if m:
                    audio.mode = m.group(1)
                m = re.match(r"Ringer mode:\s*([A-Za-z0-9_]+)", s)
                if m:
                    audio.ringer_mode = m.group(1)
                stream_m = re.search(r"(STREAM_[A-Z0-9_]+).*?index\s*=\s*(\d+)", s)
                if stream_m:
                    audio.stream_volumes[stream_m.group(1)] = stream_m.group(2)
        return audio

    # ------------------------------------------------------------------
    # Fingerprint
    # ------------------------------------------------------------------
    def diagnose_fingerprint(self, serial: str) -> FingerprintDiagnostics:
        fp = FingerprintDiagnostics()
        feat = run_read(self.bridge, "pm has-system-feature android.hardware.fingerprint", serial=serial, timeout=5)
        dfp = run_read(self.bridge, "dumpsys fingerprint", serial=serial, timeout=8)
        dbio = run_read(self.bridge, "dumpsys biometric", serial=serial, timeout=8)
        prop = run_read(self.bridge, "getprop ro.hardware.fingerprint", serial=serial, timeout=5)
        fp.evidence = [feat.evidence(), dfp.evidence(), dbio.evidence()]
        if prop.ok:
            fp.evidence.append(prop.evidence())
            fp.vendor = prop.text.splitlines()[0].strip()

        combined = f"{dfp.text}\n{dbio.text}"
        fp.raw_details = combined[:4000]
        declared = parse_bool_feature(feat.text) if feat.ok else None

        hw_error = re.search(r"(?i)(hw_unavailable|hardware not available|error_hw_unavailable)", combined)
        # A sensor entry reported by the fingerprint HAL / service
        sensor_reported = re.search(r"(?i)(\"sensorId\"|sensorId[=:]\s*\d|FingerprintSensorProperties|\bprints\b\s*[:=]|Fingerprint sensor \d)", combined)

        if hw_error:
            fp.status = str(Status.FAIL)
            fp.is_functional = False
            fp.error_status = "HW_UNAVAILABLE"
            fp.diagnosis = M("fp.hw_unavailable")
            fp.possible_causes = [M("fp.cause.flex"), M("fp.cause.sensor"), M("fp.cause.firmware")]
        elif dfp.service_missing and declared:
            fp.status = str(Status.FAIL)
            fp.error_status = "SERVICE_MISSING"
            fp.diagnosis = M("fp.service_missing")
            fp.possible_causes = [M("fp.cause.hal"), M("fp.cause.not_detected"), M("fp.cause.vendor_fw")]
        elif sensor_reported and dfp.ok:
            fp.status = str(Status.PASS)
            fp.is_detected = True
            fp.error_status = "OK"
            fp.diagnosis = M("fp.reported")
        elif declared is False:
            fp.status = str(Status.UNKNOWN)
            fp.diagnosis = M("fp.not_declared")
        else:
            fp.status = str(failure_status([dfp, dbio]))
            fp.diagnosis = M("fp.unknown")
        return fp

    # ------------------------------------------------------------------
    # Physical keys (interactive: the technician presses the keys during capture)
    # ------------------------------------------------------------------
    KEY_NAMES = {"KEY_VOLUMEUP": "vol_up_status", "KEY_VOLUMEDOWN": "vol_down_status", "KEY_POWER": "power_status"}

    def diagnose_physical_buttons(self, serial: str, capture_seconds: int = 8) -> PhysicalButtonsDiagnostics:
        diag = PhysicalButtonsDiagnostics()
        res = run_read(self.bridge, f"timeout {capture_seconds} getevent -l", serial=serial,
                       timeout=capture_seconds + 4, source=f"timeout {capture_seconds} getevent -l")
        diag.evidence.append(res.evidence())

        seen: Dict[str, set] = {k: set() for k in self.KEY_NAMES}
        lines = [l.strip() for l in (res.stdout or "").splitlines() if "EV_KEY" in l]
        diag.captured_events = lines[:40]
        for l in lines:
            for key in self.KEY_NAMES:
                if re.search(rf"\b{key}\b", l):
                    if re.search(r"\bDOWN\b", l):
                        seen[key].add("DOWN")
                    elif re.search(r"\bUP\b", l):
                        seen[key].add("UP")

        key_statuses = []
        for key, attr in self.KEY_NAMES.items():
            if {"DOWN", "UP"} <= seen[key]:
                setattr(diag, attr, str(Status.PASS))
            elif "DOWN" in seen[key]:
                setattr(diag, attr, "DOWN_NO_UP")
                diag.stuck_keys.append(key)
            else:
                setattr(diag, attr, "NOT_PRESSED")
            key_statuses.append(Status.PASS if {"DOWN", "UP"} <= seen[key] else Status.UNKNOWN)

        if any(seen[k] for k in seen):
            # getevent produced key events: a real reading.
            diag.status = str(aggregate_status(key_statuses))
            diag.is_any_key_stuck = bool(diag.stuck_keys)
            tested = [k for k in seen if {"DOWN", "UP"} <= seen[k]]
            diag.diagnosis = M("keys.tested", keys=", ".join(tested) or "-")
            if diag.stuck_keys:
                diag.possible_causes = [M("keys.cause.stuck", keys=", ".join(diag.stuck_keys))]
        else:
            st = res.status
            # timeout(1) ends getevent with exit 124: that is a normal end of capture, not an error
            diag.status = str(st)
            if st == Status.NEEDS_ROOT:
                diag.diagnosis = M("keys.permission")
            else:
                err = (res.stderr or "").strip()
                diag.diagnosis = M("keys.no_events", seconds=capture_seconds,
                                   error=err if err and res.exit_code not in (0, 124) else "-")
        return diag

    # ------------------------------------------------------------------
    # SIM / baseband
    # ------------------------------------------------------------------
    def diagnose_sim_and_baseband(self, serial: str) -> SIMBasebandDiagnostics:
        diag = SIMBasebandDiagnostics()
        feat = run_read(self.bridge, "pm has-system-feature android.hardware.telephony", serial=serial, timeout=5)
        diag.has_telephony_feature = parse_bool_feature(feat.text) if feat.ok else None
        props = {}
        for name in ("gsm.sim.state", "gsm.sim.state.2", "gsm.version.baseband", "gsm.operator.alpha", "gsm.network.type"):
            r = run_read(self.bridge, f"getprop {name}", serial=serial, timeout=5)
            props[name] = r.text if r.ok else ""
        diag.evidence.append(feat.evidence())
        diag.evidence.append(Evidence(source="getprop gsm.*",
                                      raw_excerpt="\n".join(f"{k}={v or '(empty)'}" for k, v in props.items()),
                                      exit_code=None))

        states = [s.strip().upper() for s in props["gsm.sim.state"].split(",") if s.strip()]
        diag.sim1_state = states[0] if states else "UNKNOWN"
        if len(states) > 1:
            diag.sim2_state = states[1]
        elif props["gsm.sim.state.2"]:
            diag.sim2_state = props["gsm.sim.state.2"].strip().upper()
        bb = props["gsm.version.baseband"].split(",")[0].strip()
        diag.baseband_version = bb or "UNKNOWN"
        diag.carrier_name = props["gsm.operator.alpha"].split(",")[0].strip() or "UNKNOWN"
        diag.network_type = props["gsm.network.type"].split(",")[0].strip() or "UNKNOWN"

        sig = run_read(self.bridge, "dumpsys telephony.registry", serial=serial, timeout=6)
        if sig.ok:
            m = re.search(r"mSignalStrength=.*?rssi=(-?\d+)", sig.text) or re.search(r"(-?\d+)\s*dBm", sig.text)
            if m:
                diag.signal_strength = f"{m.group(1)} dBm"

        if diag.has_telephony_feature is False:
            diag.status = str(Status.UNKNOWN)
            diag.diagnosis = M("sim.no_telephony")
            return diag
        # Empty baseband only counts as a finding when the radio stack is up (it reports a SIM state);
        # if every property is empty nothing was really read.
        if diag.has_telephony_feature and not bb and states:
            diag.status = str(Status.FAIL)
            diag.is_rf_baseband_faulty = True
            diag.diagnosis = M("sim.no_baseband")
            diag.possible_causes = [M("sim.cause.modem_fw"), M("sim.cause.ril"), M("sim.cause.rf_hw")]
            return diag
        if any(s in ("CARD_IO_ERROR", "CARD_RESTRICTED") for s in states):
            diag.status = str(Status.FAIL)
            diag.diagnosis = M("sim.state_error", state=props["gsm.sim.state"])
            diag.possible_causes = [M("sim.cause.card"), M("sim.cause.tray"), M("sim.cause.reader")]
            return diag
        if any(s in ("READY", "LOADED") for s in states):
            diag.status = str(Status.PASS)
            diag.diagnosis = M("sim.ready", carrier=diag.carrier_name, network=diag.network_type, baseband=bb[:40])
            return diag
        diag.status = str(Status.UNKNOWN)
        if any(s == "ABSENT" for s in states):
            diag.diagnosis = M("sim.absent")
        elif states:
            diag.diagnosis = M("sim.inconclusive", state=props["gsm.sim.state"])
        else:
            diag.diagnosis = M("sim.not_reported")
        return diag

    # ------------------------------------------------------------------
    # Wi-Fi
    # ------------------------------------------------------------------
    def diagnose_wifi(self, serial: str) -> WiFiDiagnostics:
        diag = WiFiDiagnostics()
        st = run_read(self.bridge, "cmd wifi status", serial=serial, timeout=6)
        dw = run_read(self.bridge, "dumpsys wifi", serial=serial, timeout=8)
        diag.evidence.append(st.evidence())
        reads = [st, dw]

        if st.ok:
            if re.search(r"Wifi is enabled", st.text):
                diag.state = "ENABLED"
            elif re.search(r"Wifi is disabled", st.text):
                diag.state = "DISABLED"
            if re.search(r"Wifi is connected to", st.text):
                diag.state = "CONNECTED"
        if dw.ok and diag.state == "UNKNOWN":
            if re.search(r"Wi-Fi is enabled", dw.text):
                diag.state = "ENABLED"
            elif re.search(r"Wi-Fi is disabled", dw.text):
                diag.state = "DISABLED"

        info_src = st.text if st.ok else ""
        info = re.search(r"(?:mWifiInfo|WifiInfo:)\s*SSID:\s*\"?([^\",]*)\"?.*", info_src) or \
            (re.search(r"mWifiInfo\s*SSID:\s*\"?([^\",]*)\"?.*", dw.text) if dw.ok else None)
        if info:
            ssid = info.group(1).strip()
            line = info.group(0)
            if ssid and ssid != "<unknown ssid>":
                diag.ssid = ssid
                diag.state = "CONNECTED"
                rssi = re.search(r"RSSI:\s*(-?\d+)", line)
                spd = re.search(r"Link speed:\s*(\d+)\s*Mbps", line)
                frq = re.search(r"Frequency:\s*(\d+)\s*MHz", line)
                diag.rssi_dbm = int(rssi.group(1)) if rssi else None
                diag.link_speed_mbps = int(spd.group(1)) if spd else None
                diag.frequency_mhz = int(frq.group(1)) if frq else None

        hal_fail = dw.ok and re.search(r"(?i)(Failed to start vendor HAL|WifiNative interface creation failed|Driver failed to load)", dw.text)
        if dw.ok:
            diag.evidence.append(dw.evidence("vendor HAL start failure found" if hal_fail else ""))
        else:
            diag.evidence.append(dw.evidence())

        mac = read_node(self.bridge, ["/sys/class/net/wlan0/address"], serial=serial)
        if mac.ok and re.match(r"^[0-9a-fA-F:]{17}$", mac.text.splitlines()[0].strip()):
            diag.mac_address = mac.text.splitlines()[0].strip()
        mac_note = diag.mac_address
        if diag.mac_address == "02:00:00:00:00:00":
            mac_note += " (Android privacy placeholder, not a fault)"
        diag.evidence.append(mac.evidence(f"MAC={mac_note}"))

        if hal_fail:
            diag.status = str(Status.FAIL)
            diag.is_hardware_faulty = True
            diag.driver_status = "HAL_FAILURE"
            diag.diagnosis = M("wifi.hal_failure")
            diag.possible_causes = [M("wifi.cause.driver"), M("wifi.cause.chip"), M("wifi.cause.vendor")]
        elif diag.state in ("ENABLED", "CONNECTED"):
            diag.status = str(Status.PASS)
            diag.driver_status = "ENABLED"
            if diag.state == "CONNECTED":
                diag.diagnosis = M("wifi.connected", ssid=diag.ssid, rssi=diag.rssi_dbm, speed=diag.link_speed_mbps)
            else:
                diag.diagnosis = M("wifi.enabled")
        elif diag.state == "DISABLED":
            diag.status = str(Status.UNKNOWN)
            diag.diagnosis = M("wifi.disabled")
        else:
            diag.status = str(failure_status(reads))
            diag.diagnosis = M("wifi.unreadable")
        return diag

    # ------------------------------------------------------------------
    # Bluetooth
    # ------------------------------------------------------------------
    def diagnose_bluetooth(self, serial: str) -> BluetoothDiagnostics:
        diag = BluetoothDiagnostics()
        feat = run_read(self.bridge, "pm has-system-feature android.hardware.bluetooth", serial=serial, timeout=4)
        ble = run_read(self.bridge, "pm has-system-feature android.hardware.bluetooth_le", serial=serial, timeout=4)
        bt = run_read(self.bridge, "dumpsys bluetooth_manager", serial=serial, timeout=6)
        declared = parse_bool_feature(feat.text) if feat.ok else None
        diag.ble_supported = parse_bool_feature(ble.text) if ble.ok else None
        diag.evidence = [feat.evidence(), bt.evidence()]

        if bt.ok:
            for line in bt.text.splitlines():
                s = line.strip()
                m = re.match(r"enabled:\s*(true|false)", s)
                if m and diag.state == "UNKNOWN":
                    diag.state = "ON" if m.group(1) == "true" else "OFF"
                m = re.match(r"state:\s*(ON|OFF|TURNING_ON|TURNING_OFF|BLE_ON)", s)
                if m and diag.state == "UNKNOWN":
                    diag.state = "ON" if m.group(1) == "ON" else ("OFF" if m.group(1) == "OFF" else m.group(1))
                m = re.match(r"address:\s*([0-9a-fA-F:]{17})", s)
                if m and diag.mac_address == "UNKNOWN":
                    diag.mac_address = m.group(1)
                m = re.match(r"name:\s*(.+)", s)
                if m and diag.name == "UNKNOWN":
                    diag.name = m.group(1).strip()

        if declared is False:
            diag.status = str(Status.UNKNOWN)
            diag.diagnosis = M("bt.not_declared")
        elif declared and bt.service_missing:
            diag.status = str(Status.FAIL)
            diag.is_hardware_faulty = True
            diag.diagnosis = M("bt.service_missing")
            diag.possible_causes = [M("bt.cause.stack"), M("bt.cause.firmware"), M("bt.cause.chip")]
        elif diag.state == "ON":
            diag.status = str(Status.PASS)
            diag.diagnosis = M("bt.on", name=diag.name, address=diag.mac_address)
        elif diag.state == "OFF":
            diag.status = str(Status.UNKNOWN)
            diag.diagnosis = M("bt.off")
        else:
            diag.status = str(failure_status([bt]))
            diag.diagnosis = M("bt.unreadable")
        return diag

    # ------------------------------------------------------------------
    # Declared features (informational only)
    # ------------------------------------------------------------------
    def diagnose_peripherals_chips(self, serial: str) -> PeripheralChipsDiagnostics:
        diag = PeripheralChipsDiagnostics()
        reads = []
        values = {}
        for key, feature in (("gps", "android.hardware.location.gps"), ("nfc", "android.hardware.nfc"),
                             ("otg", "android.hardware.usb.host")):
            r = run_read(self.bridge, f"pm has-system-feature {feature}", serial=serial, timeout=4)
            reads.append(r)
            values[key] = parse_bool_feature(r.text) if r.ok else None
            diag.evidence.append(r.evidence(f"{feature}={values[key]}"))

        def label(v):
            return "UNKNOWN" if v is None else ("DECLARED" if v else "NOT_DECLARED")

        diag.gps_present, diag.nfc_present, diag.usb_otg_supported = values["gps"], values["nfc"], values["otg"]
        diag.gps_status, diag.nfc_status = label(values["gps"]), label(values["nfc"])
        # Declared features say what the system image claims, not that the hardware works: never PASS/FAIL.
        diag.status = str(Status.NEEDS_ROOT if failure_status(reads) == Status.NEEDS_ROOT and all(v is None for v in values.values()) else Status.UNKNOWN)
        diag.diagnosis = M("features.summary", gps=M(f"feature.{diag.gps_status}"), nfc=M(f"feature.{diag.nfc_status}"),
                           otg=M(f"feature.{label(values['otg'])}"))
        return diag

    # ------------------------------------------------------------------
    # Display backlight
    # ------------------------------------------------------------------
    BACKLIGHT_DIRS = ["/sys/class/backlight/panel0-backlight", "/sys/class/leds/lcd-backlight", "/sys/class/backlight/*"]

    def diagnose_display_backlight(self, serial: str) -> DisplayBacklightDiagnostics:
        diag = DisplayBacklightDiagnostics()
        b = read_node(self.bridge, [f"{d}/brightness" for d in self.BACKLIGHT_DIRS], serial=serial)
        bm = read_node(self.bridge, [f"{d}/max_brightness" for d in self.BACKLIGHT_DIRS], serial=serial)
        b_val = parse_int(b.text) if b.ok else None
        bm_val = parse_int(bm.text) if bm.ok else None
        diag.brightness_now = b_val
        diag.max_brightness = bm_val
        diag.brightness_source = b.source if b_val is not None else "UNKNOWN"
        diag.evidence.append(b.evidence(f"brightness={b_val}"))
        if bm_val is not None:
            diag.evidence.append(bm.evidence(f"max_brightness={bm_val}"))

        dm = run_read(self.bridge, "dmesg", serial=serial, timeout=8, log_content=True)
        if dm.ok:
            hits = [l.strip() for l in dm.text.splitlines()
                    if re.search(r"(?i)(no dsi panel detected|panel.*probe failed|panel.*not found|dsi.*timeout)", l)]
            diag.dsi_error_logs = hits[:10]
            diag.evidence.append(Evidence(source="dmesg (panel errors)", raw_excerpt="\n".join(hits[:10]) or "(no panel error lines)",
                                          exit_code=dm.exit_code))
        else:
            diag.evidence.append(dm.evidence())

        if diag.dsi_error_logs:
            diag.status = str(Status.FAIL)
            diag.is_panel_disconnected = True
            diag.panel_detected = False
            diag.diagnosis = M("display.dsi_errors")
            diag.possible_causes = [M("display.cause.flex"), M("display.cause.panel"), M("display.cause.replacement")]
        elif b_val is not None and b_val > 0:
            diag.status = str(Status.PASS)
            diag.diagnosis = M("display.brightness", value=b_val, max=bm_val)
        elif b_val == 0:
            diag.status = str(Status.UNKNOWN)
            diag.diagnosis = M("display.brightness_zero")
        else:
            diag.status = str(b.status)
            diag.diagnosis = M("display.brightness_unreadable")
        return diag

    def set_pointer_location(self, serial: str, enabled: bool) -> bool:
        val = "1" if enabled else "0"
        code, _, _ = self.bridge.shell(f"settings put system pointer_location {val}", serial=serial, timeout=5)
        return code == 0

    def get_pointer_location_state(self, serial: str) -> bool:
        code, out, _ = self.bridge.shell("settings get system pointer_location", serial=serial, timeout=5)
        return code == 0 and out.strip() == "1"

    def play_audio_test(self, serial: str) -> bool:
        code, _, _ = self.bridge.shell(
            "am start -a android.intent.action.VIEW -d 'content://settings/system/ringtone'",
            serial=serial,
            timeout=5
        )
        return code == 0

    # ------------------------------------------------------------------
    # Audio subsystem
    # ------------------------------------------------------------------
    def diagnose_audio_codec(self, serial: str) -> AudioCodecReport:
        report = AudioCodecReport()
        af = run_read(self.bridge, "dumpsys media.audio_flinger", serial=serial, timeout=6)
        cards = run_read(self.bridge, "cat /proc/asound/cards", serial=serial, timeout=5, source="/proc/asound/cards")
        pcm = run_read(self.bridge, "cat /proc/asound/pcm", serial=serial, timeout=5, source="/proc/asound/pcm")
        report.evidence = [af.evidence(), cards.evidence()]
        if pcm.ok:
            report.evidence.append(pcm.evidence())
            report.pcm_devices = [l.strip() for l in pcm.text.splitlines() if l.strip()][:10]

        if af.ok:
            report.output_threads = len(re.findall(r"(?im)^\s*Output thread\s+0x", af.text))
            m = re.search(r"HAL module:\s*([a-zA-Z0-9_.]+)", af.text) or re.search(r"Hw module \d+:\s*(\S+)", af.text)
            if m:
                report.codec_chip_name = m.group(1)
            report.audio_flinger_alive = True
        elif af.service_missing:
            report.audio_flinger_alive = False

        no_cards = cards.ok and "no soundcards" in cards.text.lower()
        if cards.ok and not no_cards:
            for line in cards.text.splitlines():
                m = re.match(r"^\s*\d+\s+\[([^\]]+)\]:\s*(.+)", line)
                if m:
                    report.cards_detected.append(f"{m.group(1).strip()} ({m.group(2).strip()})")

        if report.audio_flinger_alive is False:
            report.status = str(Status.FAIL)
            report.is_codec_faulty = True
            report.diagnosis = M("audio.service_missing")
            report.possible_causes = [M("audio.cause.crash"), M("audio.cause.hal"), M("audio.cause.codec_boot")]
        elif no_cards:
            report.status = str(Status.FAIL)
            report.is_codec_faulty = True
            report.diagnosis = M("audio.no_cards")
            report.possible_causes = [M("audio.cause.codec_kernel"), M("audio.cause.codec_bus"), M("audio.cause.driver")]
        elif report.cards_detected or report.output_threads > 0:
            report.status = str(Status.PASS)
            report.diagnosis = M("audio.ok", cards=len(report.cards_detected), threads=report.output_threads)
        else:
            report.status = str(failure_status([af, cards]))
            report.diagnosis = M("audio.unreadable")
        return report

    # ------------------------------------------------------------------
    # Display panel info (kernel-reported controller name + verified boot props)
    # ------------------------------------------------------------------
    def inspect_display_panel_info(self, serial: str) -> DisplayPanelInfo:
        """
        Reports the display controller name the kernel prints, plus ro.boot.verifiedbootstate / vbmeta state.
        Software cannot prove that a screen is original; this only reports what the kernel says.
        """
        report = DisplayPanelInfo()
        dm = run_read(self.bridge, "dmesg", serial=serial, timeout=8, log_content=True)
        found = False
        if dm.ok:
            for line in dm.text.splitlines():
                if re.search(r"(?i)(panel|dsi|mdss|sde|drm)", line):
                    m = re.search(r"(?i)\b(nt\d{4,}\w*|td\d{4,}\w*|ili\d{4,}\w*|hx\d{4,}\w*|ft\d{4,}\w*|s6e\w+|ea\d{4,}\w*|rm\d{5,}\w*)\b", line)
                    if m:
                        report.panel_controller_name = m.group(1).lower()
                        report.driver_source = "dmesg"
                        report.evidence.append(Evidence(source="dmesg", raw_excerpt=line.strip(), exit_code=dm.exit_code))
                        found = True
                        break
        if not found:
            report.evidence.append(dm.evidence("no panel controller name found" if dm.ok else ""))

        sysfs = None
        if not found:
            sysfs = read_node(self.bridge, ["/sys/class/graphics/fb0/msm_fb_panel_info"], serial=serial)
            if sysfs.ok:
                m = re.search(r"panel_name=(\S+)", sysfs.text)
                if m:
                    report.panel_controller_name = m.group(1)
                    report.driver_source = "/sys/class/graphics/fb0/msm_fb_panel_info"
                    found = True
            report.evidence.append(sysfs.evidence())

        vbs = run_read(self.bridge, "getprop ro.boot.verifiedbootstate", serial=serial, timeout=4)
        vbm = run_read(self.bridge, "getprop ro.boot.vbmeta.device_state", serial=serial, timeout=4)
        if vbs.ok:
            report.verified_boot_state = vbs.text.strip().lower()
        if vbm.ok:
            report.vbmeta_device_state = vbm.text.strip().lower()
        report.evidence.append(vbs.evidence())
        report.evidence.append(vbm.evidence())

        if found:
            report.status = str(Status.PASS)
            report.diagnosis = M("panel.found", name=report.panel_controller_name, source=report.driver_source)
        else:
            reads = [dm] + ([sysfs] if sysfs is not None else [])
            report.status = str(Status.NEEDS_ROOT if dm.status == Status.NEEDS_ROOT else failure_status(reads))
            if report.status == str(Status.NEEDS_ROOT):
                report.diagnosis = M("panel.needs_root")
            else:
                report.diagnosis = M("panel.not_exposed")
        return report

    COMPANION_PKG = "com.androiddoctor.companion"

    def install_companion_apk(self, serial: str, apk_path: Optional[str] = None) -> Tuple[bool, str]:
        if not apk_path:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            apk_path = os.path.join(base_dir, "bin", "companion_diag.apk")

        if not os.path.exists(apk_path):
            return False, M("companion.apk_missing", path=apk_path)

        code, out, err = self.bridge.run_adb(["install", "-r", "-t", "-g", apk_path], serial=serial, timeout=45)
        if code == 0 and "Success" in out:
            return True, M("companion.installed")
        return False, M("companion.install_failed", output=err or out)

    def run_companion_broadcast(self, serial: str, action: str = "TEST_ALL") -> Tuple[bool, str]:
        code, out, err = self.bridge.shell(
            f"am broadcast -a com.androiddoctor.companion.ACTION_DIAG --es action {action}",
            serial=serial, timeout=10
        )
        if code == 0:
            return True, M("companion.broadcast_sent", output=out)
        return False, M("companion.broadcast_failed", output=err or out)

    def read_companion_results(self, serial: str) -> Dict[str, Any]:
        code, out, _ = self.bridge.shell("logcat -d -s AndroidDoctorCompanion:I *:S", serial=serial, timeout=8)
        results = {"raw_logcat": out, "events": []}
        if code == 0 and out:
            for line in out.splitlines():
                if "AndroidDoctorCompanion" in line:
                    results["events"].append(line.strip())
        return results

    def uninstall_companion_apk(self, serial: str) -> Tuple[bool, str]:
        code, out, err = self.bridge.shell(f"pm uninstall {self.COMPANION_PKG}", serial=serial, timeout=15)
        if "Success" in out:
            return True, M("companion.uninstalled")
        return False, M("companion.uninstall_failed", output=out or err)

    def launch_screen_color_test(self, serial: str) -> Tuple[bool, str]:
        """
        Interactive LCD Dead-Pixel & Burn-in Color Tester.
        Deploys an interactive self-contained HTML5 color canvas to /data/local/tmp/screen_test.html
        and launches full-screen via Intent, with touch digitizer pointer location enabled.
        """
        html_content = """<!DOCTYPE html>
<html>
<head>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
<style>
* { margin:0; padding:0; overflow:hidden; }
html, body { width:100%; height:100%; cursor:pointer; font-family:sans-serif; user-select:none; }
#canvas { width:100%; height:100%; display:flex; align-items:center; justify-content:center; color:#888; font-size:22px; font-weight:bold; text-align:center; padding:20px; box-sizing:border-box; }
</style>
</head>
<body onclick="nextColor()">
<div id="canvas">&#128070;</div>
<script>
var colors = [
    {bg: '#FF0000', name: '#FF0000'},
    {bg: '#00FF00', name: '#00FF00'},
    {bg: '#0000FF', name: '#0000FF'},
    {bg: '#FFFFFF', name: '#FFFFFF'},
    {bg: '#000000', name: '#000000'},
    {bg: '#808080', name: '#808080'}
];
var idx = -1;
function nextColor() {
    idx = (idx + 1) % colors.length;
    var c = colors[idx];
    document.body.style.backgroundColor = c.bg;
    var el = document.getElementById('canvas');
    el.innerText = c.name;
    el.style.color = (c.bg === '#FFFFFF' || c.bg === '#808080') ? '#000' : '#FFF';
    setTimeout(function(){ el.innerText = ''; }, 2500);
}
</script>
</body>
</html>"""
        temp_dir = os.path.join(os.path.expanduser("~"), "AppData", "Local", "Temp")
        os.makedirs(temp_dir, exist_ok=True)
        temp_html = os.path.join(temp_dir, "doctor_screen_test.html")
        try:
            with open(temp_html, "w", encoding="utf-8") as f:
                f.write(html_content)
            self.bridge.run_adb(["push", temp_html, "/data/local/tmp/screen_test.html"], serial=serial, timeout=10)
            self.bridge.shell("chmod 644 /data/local/tmp/screen_test.html", serial=serial, timeout=5)
            self.bridge.shell("settings put system pointer_location 1", serial=serial, timeout=5)
            self.bridge.shell("settings put system show_touches 1", serial=serial, timeout=5)
            self.bridge.shell(
                "am start -a android.intent.action.VIEW -d file:///data/local/tmp/screen_test.html -t text/html",
                serial=serial, timeout=10
            )
            return True, M("colortest.started")
        except Exception as e:
            logger.error(f"Failed to launch color test: {e}")
            return False, M("colortest.failed", error=str(e))

    def stop_screen_color_test(self, serial: str) -> Tuple[bool, str]:
        """
        Disables touch pointer location and sends Back key to exit color test.
        """
        self.bridge.shell("settings put system pointer_location 0", serial=serial, timeout=5)
        self.bridge.shell("settings put system show_touches 0", serial=serial, timeout=5)
        self.bridge.shell("input keyevent 4", serial=serial, timeout=5)
        return True, M("colortest.stopped")

    def play_speaker_test_tone(self, serial: str, freq: int = 1000, duration_sec: float = 3.0) -> Tuple[bool, str]:
        """
        Generates a 16-bit PCM mono sine-wave test tone with soft fade-in/out and plays it
        over the phone's primary loudspeaker at maximum volume to audit hardware clarity.
        """
        import tempfile
        # generated file: never written into bin/ (bin/ holds only the pinned, hash-checked tools)
        local_wav = os.path.join(tempfile.gettempdir(), f"adp_test_tone_{freq}hz.wav")
        samplerate = 44100
        n_samples = int(samplerate * duration_sec)

        try:
            with wave.open(local_wav, "wb") as wf:
                wf.setnchannels(1)
                wf.setsampwidth(2)
                wf.setframerate(samplerate)
                fade_samples = int(samplerate * 0.05)
                for i in range(n_samples):
                    t = i / samplerate
                    amp = 32767.0 * 0.85
                    if i < fade_samples:
                        amp *= (i / fade_samples)
                    elif i > (n_samples - fade_samples):
                        amp *= ((n_samples - i) / fade_samples)
                    val = int(amp * math.sin(2.0 * math.pi * freq * t))
                    wf.writeframes(struct.pack("<h", max(-32768, min(32767, val))))

            remote_path = "/data/local/tmp/doctor_speaker_tone.wav"
            self.bridge.run_adb(["push", local_wav, remote_path], serial=serial, timeout=10)
            self.bridge.shell(f"chmod 644 {remote_path}", serial=serial, timeout=5)
            self.bridge.shell("cmd media_session volume --stream 3 --set 15", serial=serial, timeout=5)
            self.bridge.shell(
                f"am start -a android.intent.action.VIEW -d file://{remote_path} -t audio/wav",
                serial=serial, timeout=8
            )
            return True, M("tone.playing", freq=freq)
        except Exception as e:
            logger.error(f"Error generating or playing test tone: {e}")
            return False, M("tone.failed", error=str(e))

    def reset_battery_fuel_gauge_stats(self, serial: str) -> Tuple[bool, str]:
        """
        Reset Battery Fuel-Gauge Stats (batterystats --reset) and clear calibration cache.
        """
        from core.board_diag import MotherboardDiagnostics
        return MotherboardDiagnostics(self.bridge).reset_battery_fuel_gauge_stats(serial)

    def run_full_audit(self, serial: str, capture_buttons: bool = False) -> HardwareAuditReport:
        """
        Run all non-interactive hardware checks. The key test needs the technician to press keys,
        so it only runs when capture_buttons=True; otherwise it stays UNKNOWN.
        Overall: FAIL if any FAIL, else UNKNOWN if any key check is UNKNOWN/NEEDS_ROOT, else PASS.
        """
        logger.info(f"Auditing hardware components for {serial}...")
        sensors, missing = self.audit_sensors(serial)
        cameras = self.audit_cameras(serial)
        audio = self.audit_audio(serial)
        audio_codec = self.diagnose_audio_codec(serial)
        fp = self.diagnose_fingerprint(serial)
        btn = self.diagnose_physical_buttons(serial) if capture_buttons else PhysicalButtonsDiagnostics(
            diagnosis=M("keys.not_run"))
        sim = self.diagnose_sim_and_baseband(serial)
        disp = self.diagnose_display_backlight(serial)
        disp_info = self.inspect_display_panel_info(serial)
        wifi = self.diagnose_wifi(serial)
        bt = self.diagnose_bluetooth(serial)
        periph = self.diagnose_peripherals_chips(serial)
        ptr_loc = self.get_pointer_location_state(serial)

        all_ev: List[Evidence] = []
        sensors_status = str(Status.PASS) if sensors else str(self.last_sensor_read.status if self.last_sensor_read else Status.UNKNOWN)
        if self.last_sensor_read is not None:
            all_ev.append(self.last_sensor_read.evidence(f"{len(sensors)} sensors parsed; common sensors not reported: {missing or 'none'}"))
        cameras_status = str(Status.PASS) if cameras else str(self.last_camera_read.status if self.last_camera_read else Status.UNKNOWN)
        if self.last_camera_read is not None:
            all_ev.append(self.last_camera_read.evidence(f"{len(cameras)} cameras parsed"))

        check_statuses = {
            "sensors.reported": sensors_status,
            "camera.reported": cameras_status,
            audio_codec.check_id: audio_codec.status,
            fp.check_id: fp.status,
            btn.check_id: btn.status,
            sim.check_id: sim.status,
            disp.check_id: disp.status,
            wifi.check_id: wifi.status,
            bt.check_id: bt.status,
        }
        if not capture_buttons:
            del check_statuses[btn.check_id]  # not run: reported separately, not counted
        overall = aggregate_status(list(check_statuses.values()))
        fails = [k for k, v in check_statuses.items() if v == str(Status.FAIL)]
        if overall == Status.FAIL:
            summary = M("hw.summary.fail", checks=[M(k) for k in fails])
        elif overall == Status.UNKNOWN:
            open_checks = [k for k, v in check_statuses.items() if v != str(Status.PASS)]
            summary = M("hw.summary.unknown", checks=[M(k) for k in open_checks])
        else:
            summary = M("hw.summary.pass")
        if not capture_buttons:
            summary = M("hw.summary.keys_not_run", summary=summary)

        for item in [fp, btn, sim, disp, disp_info, wifi, bt, periph, audio_codec]:
            all_ev.extend(item.evidence)

        return HardwareAuditReport(
            serial=serial,
            sensors=sensors,
            sensors_status=sensors_status,
            missing_critical_sensors=missing,
            cameras=cameras,
            cameras_status=cameras_status,
            audio=audio,
            audio_codec=audio_codec,
            fingerprint=fp,
            buttons=btn,
            sim_baseband=sim,
            display_backlight=disp,
            display_panel_info=disp_info,
            wifi=wifi,
            bluetooth=bt,
            peripherals=periph,
            vibrator_functional=None,
            pointer_location_active=ptr_loc,
            check_statuses=check_statuses,
            summary=summary,
            status=str(overall),
            evidence=all_ev
        )
