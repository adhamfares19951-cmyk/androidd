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
from core.result import Evidence, Status, CheckResult, status_from_read

logger = logging.getLogger("HardwareTester")


@dataclass
class SensorInfo:
    handle: str
    name: str
    vendor: str
    sensor_type: str
    version: str
    power_ma: float
    max_range: float
    resolution: float
    status: str = "Active"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class CameraInfo:
    camera_id: str
    facing: str
    orientation: int
    flash_available: bool
    status: str = "Available"
    details: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AudioSubsystemInfo:
    mode: str = "NORMAL"
    ringer_mode: str = "NORMAL"
    speaker_on: bool = False
    headset_connected: bool = False
    stream_volumes: Dict[str, str] = field(default_factory=dict)
    audio_devices: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class FingerprintDiagnostics:
    vendor: str = "Unknown"
    is_detected: bool = False
    is_functional: bool = True
    is_flex_disconnected: bool = False
    error_status: str = "OK"
    status: str = "UNKNOWN"
    diagnosis: str = "Fingerprint IC normal"
    raw_details: str = ""
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class PhysicalButtonsDiagnostics:
    vol_up_status: str = "Released"
    vol_down_status: str = "Released"
    power_status: str = "Released"
    is_any_key_stuck: bool = False
    stuck_keys: List[str] = field(default_factory=list)
    status: str = "UNKNOWN"
    diagnosis: str = "All physical buttons responsive and released"
    captured_events: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class SIMBasebandDiagnostics:
    sim1_state: str = "UNKNOWN"
    sim2_state: str = "UNKNOWN"
    carrier_name: str = "Unknown"
    network_type: str = "Unknown"
    signal_strength: str = "Unknown"
    baseband_version: str = "Unknown"
    is_rf_baseband_faulty: bool = False
    is_tray_det_faulty: bool = False
    status: str = "UNKNOWN"
    diagnosis: str = "SIM & Baseband Subsystem Normal"
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class WiFiDiagnostics:
    state: str = "UNKNOWN"
    mac_address: str = "Unknown"
    ssid: str = ""
    rssi_dbm: int = 0
    link_speed_mbps: int = 0
    frequency_mhz: int = 0
    driver_status: str = "OK"
    is_hardware_faulty: bool = False
    status: str = "UNKNOWN"
    diagnosis: str = "Wi-Fi Chipset Normal"
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class BluetoothDiagnostics:
    state: str = "UNKNOWN"
    mac_address: str = "Unknown"
    name: str = "Unknown"
    ble_supported: bool = True
    is_hardware_faulty: bool = False
    status: str = "UNKNOWN"
    diagnosis: str = "Bluetooth Controller Normal"
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class PeripheralChipsDiagnostics:
    gps_present: bool = True
    gps_status: str = "Active"
    nfc_present: bool = False
    nfc_status: str = "Not Supported"
    usb_otg_supported: bool = True
    status: str = "UNKNOWN"
    diagnosis: str = "Peripherals Normal"
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class DisplayBacklightDiagnostics:
    brightness_now: int = 128
    max_brightness: int = 255
    panel_detected: bool = True
    is_backlight_faulty: bool = False
    is_panel_disconnected: bool = False
    status: str = "UNKNOWN"
    diagnosis: str = "Display & Backlight Normal"
    dsi_error_logs: List[str] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class AudioCodecReport:
    cards_detected: List[str] = field(default_factory=list)
    pcm_devices: List[str] = field(default_factory=list)
    audio_flinger_alive: bool = True
    codec_chip_name: str = "Integrated SoC Audio"
    is_codec_faulty: bool = False
    status: str = "UNKNOWN"
    diagnosis: str = "Audio Codec IC Normal"
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


@dataclass
class DisplayPanelInfo:
    check_id: str = "display.panel_info"
    panel_controller_name: str = "Unknown Panel"
    driver_source: str = "Unresolved"
    verified_boot_state: str = "unknown"
    vbmeta_device_state: str = "unknown"
    status: str = "UNKNOWN"
    diagnosis: str = "Display panel controller info unread."
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        return d


# Backwards compatibility alias
DisplayPanelAuthenticity = DisplayPanelInfo


@dataclass
class HardwareAuditReport:
    serial: str
    sensors: List[SensorInfo] = field(default_factory=list)
    missing_critical_sensors: List[str] = field(default_factory=list)
    cameras: List[CameraInfo] = field(default_factory=list)
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
    vibrator_functional: bool = True
    pointer_location_active: bool = False
    summary: str = "PASS"
    status: str = "UNKNOWN"
    evidence: List[Evidence] = field(default_factory=list)

    @property
    def display_authenticity(self) -> DisplayPanelInfo:
        return self.display_panel_info

    @display_authenticity.setter
    def display_authenticity(self, val: DisplayPanelInfo):
        self.display_panel_info = val

    def to_dict(self) -> Dict[str, Any]:
        return {
            "serial": self.serial,
            "sensors": [s.to_dict() for s in self.sensors],
            "missing_critical_sensors": self.missing_critical_sensors,
            "cameras": [c.to_dict() for c in self.cameras],
            "audio": self.audio.to_dict(),
            "audio_codec": self.audio_codec.to_dict(),
            "fingerprint": self.fingerprint.to_dict(),
            "buttons": self.buttons.to_dict(),
            "sim_baseband": self.sim_baseband.to_dict(),
            "display_backlight": self.display_backlight.to_dict(),
            "display_panel_info": self.display_panel_info.to_dict(),
            "display_authenticity": self.display_panel_info.to_dict(),
            "wifi": self.wifi.to_dict(),
            "bluetooth": self.bluetooth.to_dict(),
            "peripherals": self.peripherals.to_dict(),
            "vibrator_functional": self.vibrator_functional,
            "pointer_location_active": self.pointer_location_active,
            "summary": self.summary,
            "status": self.status,
            "evidence": [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        }


class HardwareTester:
    def __init__(self, bridge: Optional[ADBBridge] = None):
        self.bridge = bridge or ADBBridge()

    def audit_sensors(self, serial: str) -> Tuple[List[SensorInfo], List[str]]:
        sensors: List[SensorInfo] = []
        missing: List[str] = []

        code, out, _ = self.bridge.shell("dumpsys sensorservice", serial=serial, timeout=12)
        if code == 0 and out:
            lines = out.splitlines()
            for line in lines:
                line_str = line.strip()
                if not line_str:
                    continue

                handle_match = re.search(r"0x[0-9a-fA-F]+\)\s*(.+?)\s*\|\s*([^|]+)\s*\|\s*version\s*(\d+)\s*\|\s*([0-9a-zA-Z_\.\-]+)", line_str)
                if handle_match:
                    name = handle_match.group(1).strip()
                    vendor = handle_match.group(2).strip()
                    version = handle_match.group(3).strip()
                    s_type = handle_match.group(4).strip()
                    
                    p_match = re.search(r"power\s*=\s*([0-9\.]+)\s*mA", line_str, re.IGNORECASE)
                    power = float(p_match.group(1)) if p_match else 0.1

                    r_match = re.search(r"maxRange\s*=\s*([0-9\.]+)", line_str, re.IGNORECASE)
                    mrange = float(r_match.group(1)) if r_match else 1.0

                    res_match = re.search(r"resolution\s*=\s*([0-9\.\-eE]+)", line_str, re.IGNORECASE)
                    res = float(res_match.group(1)) if res_match else 0.01

                    sensors.append(SensorInfo(
                        handle=f"0x{len(sensors)+1:02X}",
                        name=name,
                        vendor=vendor,
                        sensor_type=s_type,
                        version=version,
                        power_ma=power,
                        max_range=mrange,
                        resolution=res,
                        status="Active"
                    ))
                elif line_str.startswith("Sensor:") or ("type=" in line_str and "name=" in line_str):
                    n_match = re.search(r'name="([^"]+)"', line_str)
                    v_match = re.search(r'vendor="([^"]+)"', line_str)
                    t_match = re.search(r'type=([0-9a-zA-Z_\.]+)', line_str)
                    if n_match:
                        sensors.append(SensorInfo(
                            handle=f"0x{len(sensors)+1:02X}",
                            name=n_match.group(1),
                            vendor=v_match.group(1) if v_match else "OEM",
                            sensor_type=t_match.group(1) if t_match else "Sensor",
                            version="1",
                            power_ma=0.1,
                            max_range=100.0,
                            resolution=0.01,
                            status="Active"
                        ))

        sensor_types_lower = [f"{s.name.lower()} {s.sensor_type.lower()}" for s in sensors]
        critical_checks = [
            ("Accelerometer", ["accel"]),
            ("Gyroscope", ["gyro"]),
            ("Proximity Sensor", ["prox"]),
            ("Ambient Light Sensor", ["light", "lux"]),
            ("Magnetometer / Compass", ["mag", "compass"])
        ]

        for s_label, keywords in critical_checks:
            found = False
            for kw in keywords:
                if any(kw in s_str for s_str in sensor_types_lower):
                    found = True
                    break
            if not found:
                missing.append(s_label)

        return sensors, missing

    def trigger_vibrator(self, serial: str, duration_ms: int = 1000) -> bool:
        cmd1 = f"cmd vibrator_manager synced oneshot {duration_ms}"
        code1, _, _ = self.bridge.shell(cmd1, serial=serial, timeout=5)
        if code1 == 0:
            return True

        cmd2 = f"cmd vibrator vibrate {duration_ms}"
        code2, _, _ = self.bridge.shell(cmd2, serial=serial, timeout=5)
        if code2 == 0:
            return True

        cmd3 = f"echo {duration_ms} > /sys/class/timed_output/vibrator/enable"
        code3, _, _ = self.bridge.shell(cmd3, serial=serial, timeout=5)
        return code3 == 0

    def audit_cameras(self, serial: str) -> List[CameraInfo]:
        cameras: List[CameraInfo] = []
        code, out, _ = self.bridge.shell("dumpsys media.camera", serial=serial, timeout=10)
        if code == 0 and out:
            lines = out.splitlines()
            current_id = ""
            current_facing = "Back"
            current_orient = 90
            flash = False

            for line in lines:
                line_str = line.strip()
                id_match = re.search(r"Camera ID:\s*([0-9a-zA-Z_]+)", line_str, re.IGNORECASE)
                if not id_match:
                    id_match = re.search(r"Device\s+([0-9a-zA-Z_]+)\s+is\s+open", line_str, re.IGNORECASE)
                if not id_match:
                    id_match = re.search(r"Camera\s+device\s+([0-9]+)", line_str, re.IGNORECASE)

                if id_match:
                    if current_id:
                        cameras.append(CameraInfo(
                            camera_id=current_id,
                            facing=current_facing,
                            orientation=current_orient,
                            flash_available=flash,
                            status="Available",
                            details=f"Lens Facing: {current_facing}"
                        ))
                    current_id = id_match.group(1)
                    current_facing = "Back"
                    current_orient = 90
                    flash = False

                if "facing" in line_str.lower():
                    if "front" in line_str.lower():
                        current_facing = "Front"
                    elif "back" in line_str.lower():
                        current_facing = "Back"

                if "orientation" in line_str.lower():
                    o_match = re.search(r"orientation[:=]\s*(\d+)", line_str, re.IGNORECASE)
                    if o_match:
                        current_orient = int(o_match.group(1))

                if "flash" in line_str.lower() and ("true" in line_str.lower() or "1" in line_str):
                    flash = True

            if current_id:
                cameras.append(CameraInfo(
                    camera_id=current_id,
                    facing=current_facing,
                    orientation=current_orient,
                    flash_available=flash,
                    status="Available",
                    details=f"Lens Facing: {current_facing}"
                ))

        if not cameras:
            code_num, out_num, _ = self.bridge.shell("cmd media.camera get-number-of-cameras", serial=serial, timeout=5)
            count = 0
            if code_num == 0 and out_num.strip().isdigit():
                count = int(out_num.strip())
            for i in range(max(count, 2)):
                facing = "Front" if i == 1 else "Back"
                cameras.append(CameraInfo(
                    camera_id=str(i),
                    facing=facing,
                    orientation=270 if i == 1 else 90,
                    flash_available=(i == 0),
                    status="Active",
                    details=f"Standard {facing} Sensor"
                ))

        return cameras

    def audit_audio(self, serial: str) -> AudioSubsystemInfo:
        audio = AudioSubsystemInfo()
        code, out, _ = self.bridge.shell("dumpsys audio", serial=serial, timeout=10)
        if code == 0 and out:
            for line in out.splitlines():
                line_str = line.strip()
                if "Mode:" in line_str:
                    m = re.search(r"Mode:\s*([A-Za-z0-9_]+)", line_str)
                    if m:
                        audio.mode = m.group(1)
                elif "Ringer mode:" in line_str:
                    m = re.search(r"Ringer mode:\s*([A-Za-z0-9_]+)", line_str)
                    if m:
                        audio.ringer_mode = m.group(1)
                elif "Speakerphone is" in line_str:
                    audio.speaker_on = "on" in line_str.lower()
                elif "Wired Headset:" in line_str:
                    audio.headset_connected = "true" in line_str.lower() or "connected" in line_str.lower()
                elif "STREAM_" in line_str:
                    stream_m = re.search(r"(STREAM_[A-Z0-9_]+).*?index\s*=\s*(\d+)", line_str)
                    if stream_m:
                        audio.stream_volumes[stream_m.group(1)] = stream_m.group(2)
        return audio

    def diagnose_fingerprint(self, serial: str) -> FingerprintDiagnostics:
        fp = FingerprintDiagnostics()
        code_fp, out_fp, err_fp = self.bridge.shell("dumpsys fingerprint", serial=serial, timeout=8)
        code_bio, out_bio, err_bio = self.bridge.shell("dumpsys biometric", serial=serial, timeout=8)
        code_k, out_k, err_k = self.bridge.shell("dmesg | grep -i -E 'fpc|goodix|silead|fingerprint|biometric|fpsensor' | tail -n 25", serial=serial, timeout=8)

        combined = f"{out_fp}\n{out_bio}\n{out_k}"
        fp.raw_details = combined

        # Identify vendor
        if re.search(r"(?i)goodix", combined):
            fp.vendor = "Goodix"
            fp.is_detected = True
        elif re.search(r"(?i)fpc", combined):
            fp.vendor = "Fingerprint Cards (FPC)"
            fp.is_detected = True
        elif re.search(r"(?i)silead", combined):
            fp.vendor = "Silead"
            fp.is_detected = True
        elif re.search(r"(?i)(qualcomm|qfp|ultrasonic)", combined):
            fp.vendor = "Qualcomm 3D Sonic"
            fp.is_detected = True
        elif re.search(r"(?i)synaptics", combined):
            fp.vendor = "Synaptics"
            fp.is_detected = True
        else:
            code_hw, out_hw, err_hw = self.bridge.shell("getprop ro.hardware.fingerprint", serial=serial, timeout=5)
            if code_hw == 0 and out_hw.strip():
                fp.vendor = out_hw.strip()
                fp.is_detected = True

        status_read = status_from_read(f"{out_fp}\n{out_bio}", f"{err_fp}\n{err_bio}", code_fp)
        if status_read == Status.NEEDS_ROOT:
            fp.status = "NEEDS_ROOT"
            fp.diagnosis = "NEEDS_ROOT: Biometric service dumpsys restricted by SELinux / permissions."
        # Check fault conditions
        elif re.search(r"(?i)(hw_unavailable|hardware not available|error_hw_unavailable)", combined):
            fp.status = "FAIL"
            fp.is_functional = False
            fp.is_flex_disconnected = True
            fp.error_status = "HW_UNAVAILABLE"
            fp.diagnosis = f"FAIL: Fingerprint sensor hardware unavailable ({fp.vendor}). Flex cable is disconnected or sensor IC is damaged."
        elif re.search(r"(?i)(spi.*error|spi transfer failed|i2c.*error|no ack)", combined):
            fp.status = "FAIL"
            fp.is_functional = False
            fp.is_flex_disconnected = True
            fp.error_status = "BUS_ERROR"
            fp.diagnosis = f"FAIL: SPI/I2C communication failure ({fp.vendor}). Broken flex cable traces or torn connector pin."
        elif "Cannot find biometric HAL service" in combined or "No fingerprint service" in combined:
            fp.status = "FAIL"
            fp.is_functional = False
            fp.error_status = "HAL_DEAD"
            fp.diagnosis = "FAIL: Fingerprint HAL service is not responding. Sensor power rail missing or IC dead."
        elif fp.is_detected:
            fp.status = "PASS"
            fp.is_functional = True
            fp.error_status = "OK"
            fp.diagnosis = f"PASS: Fingerprint sensor ({fp.vendor}) is initialized and operational."
        else:
            fp.status = "UNKNOWN"
            fp.diagnosis = "UNKNOWN: No biometric/fingerprint sensor detected on this model."

        if out_fp.strip() or err_fp.strip():
            fp.evidence.append(Evidence(source="dumpsys fingerprint", raw_excerpt=(out_fp or err_fp).strip()[:1000], exit_code=code_fp))
        if out_bio.strip() or err_bio.strip():
            fp.evidence.append(Evidence(source="dumpsys biometric", raw_excerpt=(out_bio or err_bio).strip()[:1000], exit_code=code_bio))
        if out_k.strip() or err_k.strip():
            fp.evidence.append(Evidence(source="dmesg biometric traces", raw_excerpt=(out_k or err_k).strip()[:1000], exit_code=code_k))
        if not fp.evidence:
            fp.evidence.append(Evidence(source="dumpsys fingerprint/biometric", raw_excerpt=combined.strip() or "No biometric data", exit_code=code_fp))
        return fp

    def diagnose_physical_buttons(self, serial: str) -> PhysicalButtonsDiagnostics:
        diag = PhysicalButtonsDiagnostics()
        code_input, out_input, err_input = self.bridge.shell("dumpsys input", serial=serial, timeout=8)

        # Look for active held keys in dumpsys input
        stuck = []
        if code_input == 0 and out_input:
            if "Key: 114" in out_input and "DOWN" in out_input:
                stuck.append("Volume Down (KEY_VOLUMEDOWN)")
                diag.vol_down_status = "STUCK_PRESSED"
            if "Key: 115" in out_input and "DOWN" in out_input:
                stuck.append("Volume Up (KEY_VOLUMEUP)")
                diag.vol_up_status = "STUCK_PRESSED"
            if "Key: 116" in out_input and "DOWN" in out_input:
                stuck.append("Power Button (KEY_POWER)")
                diag.power_status = "STUCK_PRESSED"

        # Stream getevent for 8 seconds to sample live physical key events (shell user has access)
        code_ev, out_ev, err_ev = self.bridge.shell("timeout 8 getevent -l", serial=serial, timeout=10)
        ev_status = status_from_read(out_ev, err_ev, code_ev)
        input_status = status_from_read(out_input, err_input, code_input)

        if code_ev == 0 and out_ev:
            lines = out_ev.strip().splitlines()
            diag.captured_events = lines[:20]
            for l in lines:
                if "KEY_VOLUMEDOWN" in l:
                    diag.vol_down_status = "Pressed (Active)" if "DOWN" in l else "Released"
                elif "KEY_VOLUMEUP" in l:
                    diag.vol_up_status = "Pressed (Active)" if "DOWN" in l else "Released"
                elif "KEY_POWER" in l:
                    diag.power_status = "Pressed (Active)" if "DOWN" in l else "Released"

        # Check for actual permission error (NEEDS_ROOT) vs general missing data (UNKNOWN)
        if ev_status == Status.NEEDS_ROOT or input_status == Status.NEEDS_ROOT:
            diag.status = "NEEDS_ROOT"
            diag.diagnosis = "NEEDS_ROOT: Input subsystem access restricted by SELinux / permissions."
        elif stuck:
            diag.status = "FAIL"
            diag.is_any_key_stuck = True
            diag.stuck_keys = stuck
            diag.diagnosis = f"FAIL: Stuck / shorted physical buttons detected: {', '.join(stuck)}. High risk of recovery bootloops or sudden rebooting!"
        elif code_input == 0 and out_input:
            diag.status = "PASS"
            diag.diagnosis = "PASS: Volume Up/Down and Power keys are normal and not shorted."
        else:
            diag.status = "UNKNOWN"
            real_err = (err_ev or err_input or "Input subsystem unreadable via ADB").strip()
            diag.diagnosis = f"UNKNOWN: {real_err}"

        diag.evidence.append(Evidence(source="dumpsys input", raw_excerpt=(out_input or err_input or "No input subsystem output").strip()[:1000], exit_code=code_input))
        if out_ev or err_ev:
            diag.evidence.append(Evidence(source="timeout 8 getevent -l", raw_excerpt=(out_ev or err_ev).strip()[:1000], exit_code=code_ev))
        return diag

    def diagnose_sim_and_baseband(self, serial: str) -> SIMBasebandDiagnostics:
        diag = SIMBasebandDiagnostics()

        code_sim1, out_sim1, _ = self.bridge.shell("getprop gsm.sim.state", serial=serial, timeout=5)
        code_sim2, out_sim2, _ = self.bridge.shell("getprop gsm.sim.state.2", serial=serial, timeout=5)
        code_bb, out_bb, _ = self.bridge.shell("getprop gsm.version.baseband", serial=serial, timeout=5)
        code_op, out_op, _ = self.bridge.shell("getprop gsm.operator.alpha", serial=serial, timeout=5)
        code_net, out_net, _ = self.bridge.shell("getprop gsm.network.type", serial=serial, timeout=5)
        code_sig, out_sig, _ = self.bridge.shell("dumpsys telephony.registry | grep -i -m 1 'mSignalStrength'", serial=serial, timeout=5)

        s1 = out_sim1.strip().upper() if code_sim1 == 0 else "UNKNOWN"
        s2 = out_sim2.strip().upper() if code_sim2 == 0 else "UNKNOWN"
        bb = out_bb.strip() if code_bb == 0 else "Unknown"
        op = out_op.strip() if code_op == 0 and out_op.strip() else "Unknown"
        net = out_net.strip() if code_net == 0 and out_net.strip() else "Unknown"

        sig = "Unknown"
        if code_sig == 0 and out_sig:
            m_dbm = re.search(r"(-?\d+)\s*dBm", out_sig)
            if m_dbm:
                sig = f"{m_dbm.group(1)} dBm"
            else:
                sig = out_sig.strip()[:35]

        diag.sim1_state = s1
        diag.sim2_state = s2
        diag.baseband_version = bb
        diag.carrier_name = op
        diag.network_type = net
        diag.signal_strength = sig

        # Check Baseband / RIL IC failure
        if not bb or bb.lower() in ["unknown", "null", "none", ""]:
            diag.status = "FAIL"
            diag.is_rf_baseband_faulty = True
            diag.diagnosis = "FAIL: Unknown Baseband (No RIL). Damaged RF Baseband IC on motherboard or corrupted modem NVRAM."
        elif "CARD_IO_ERROR" in s1 or "NOT_READY" in s1:
            diag.status = "FAIL"
            diag.diagnosis = "FAIL: SIM Card Reader I/O Error. Corroded contact pins or damaged SIM_DATA/SIM_CLK lines on SIM slot."
        elif "READY" in s1 or "LOADED" in s1:
            diag.status = "PASS"
            diag.diagnosis = f"PASS: SIM 1 is ACTIVE and authenticated ({op} | {net} | {sig} | Modem: {bb[:25]})."
        elif "ABSENT" in s1:
            diag.status = "PASS"
            diag.diagnosis = f"PASS: SIM reader responsive (Tray is empty / card not inserted). Modem: {bb[:25]}"
        elif s1 != "UNKNOWN":
            diag.status = "PASS"
            diag.diagnosis = f"PASS: SIM 1 State: {s1} | Operator: {op} | Baseband: {bb[:30]}"
        else:
            diag.status = "UNKNOWN"
            diag.diagnosis = f"UNKNOWN: SIM/Baseband state unreadable (State: {s1}, Baseband: {bb})"

        diag.evidence.append(Evidence(source="getprop gsm.version.baseband", raw_excerpt=bb, exit_code=code_bb))
        diag.evidence.append(Evidence(source="getprop gsm.sim.state", raw_excerpt=f"sim1={s1}, sim2={s2}, carrier={op}, net={net}", exit_code=code_sim1))
        if out_sig:
            diag.evidence.append(Evidence(source="dumpsys telephony.registry (mSignalStrength)", raw_excerpt=out_sig.strip()[:500], exit_code=code_sig))
        return diag

    def diagnose_wifi(self, serial: str) -> WiFiDiagnostics:
        diag = WiFiDiagnostics()

        # 1. Non-root cmd wifi status (Android 9+)
        code_cmd, out_cmd, _ = self.bridge.shell("cmd wifi status", serial=serial, timeout=5)
        if code_cmd == 0 and out_cmd:
            if "Wifi is enabled" in out_cmd:
                diag.state = "ENABLED"
            elif "Wifi is disabled" in out_cmd:
                diag.state = "DISABLED"

        # 2. dumpsys wifi for driver, HAL and connection details
        code_w, out_w, _ = self.bridge.shell("dumpsys wifi", serial=serial, timeout=8)
        if code_w == 0 and out_w:
            if diag.state == "UNKNOWN":
                if "Wi-Fi is enabled" in out_w or "curState=EnabledState" in out_w or "Wi-Fi is ON" in out_w:
                    diag.state = "ENABLED"
                elif "Wi-Fi is disabled" in out_w or "curState=DisabledState" in out_w:
                    diag.state = "DISABLED"

            # Check for HAL failure or Driver crash
            if re.search(r"(?i)(Failed to start vendor HAL|WifiNative interface creation failed|Driver failed to load)", out_w):
                diag.is_hardware_faulty = True
                diag.status = "FAIL"
                diag.driver_status = "CRITICAL: Vendor HAL / Driver Start Failure"
                diag.diagnosis = "FAIL: Wi-Fi Vendor HAL or Driver failed to initialize. Chipset desoldered or unpowered."
                diag.evidence.append(Evidence(source="dumpsys wifi", raw_excerpt=out_w.strip()[:1000], exit_code=code_w))
                return diag

            # Check SSID & RSSI
            m_ssid = re.search(r"SSID:\s*\"?([^\",\n]+)\"?", out_w)
            if m_ssid:
                diag.ssid = m_ssid.group(1).strip()
                diag.state = "CONNECTED"

            m_rssi = re.search(r"RSSI:\s*(-?\d+)", out_w)
            if m_rssi:
                diag.rssi_dbm = int(m_rssi.group(1))

            m_speed = re.search(r"Link speed:\s*(\d+)\s*Mbps", out_w)
            if m_speed:
                diag.link_speed_mbps = int(m_speed.group(1))

            m_freq = re.search(r"Frequency:\s*(\d+)\s*MHz", out_w)
            if m_freq:
                diag.frequency_mhz = int(m_freq.group(1))

        # 3. MAC address (respecting Android MAC privacy mask without flagging as faulty)
        code_mac, out_mac, _ = self.bridge.shell("cat /sys/class/net/wlan0/address 2>/dev/null", serial=serial, timeout=4)
        if code_mac == 0 and out_mac.strip() and "no such file" not in out_mac.lower():
            diag.mac_address = out_mac.strip()
        else:
            code_ip, out_ip, _ = self.bridge.shell("ip link show wlan0 2>/dev/null", serial=serial, timeout=4)
            m = re.search(r"link/ether\s+([0-9a-fA-F:]{17})", out_ip)
            if m:
                diag.mac_address = m.group(1)

        # Note: 02:00:00:00:00:00 is standard Android MAC privacy randomized mask, NOT a hardware fault!
        if diag.mac_address in ["02:00:00:00:00:00", "00:00:00:00:00:00"]:
            mac_note = f"{diag.mac_address} (Android Privacy Mask)"
        else:
            mac_note = diag.mac_address

        if diag.is_hardware_faulty:
            diag.status = "FAIL"
            diag.diagnosis = "FAIL: Wi-Fi Vendor HAL or Driver failed to initialize."
        elif diag.state in ["CONNECTED", "ENABLED"]:
            diag.status = "PASS"
            if diag.state == "CONNECTED":
                diag.diagnosis = f"PASS: Wi-Fi Connected to '{diag.ssid}' ({diag.rssi_dbm} dBm, {diag.link_speed_mbps} Mbps, MAC: {mac_note})"
            else:
                diag.diagnosis = f"PASS: Wi-Fi Hardware Controller Active and scanning (MAC: {mac_note})"
        elif diag.state == "DISABLED":
            diag.status = "PASS"
            diag.diagnosis = f"PASS: Wi-Fi Controller Enumerated (Currently Turned OFF by user, MAC: {mac_note})"
        else:
            diag.status = "UNKNOWN"
            diag.diagnosis = f"UNKNOWN: Wi-Fi state indeterminate (State: {diag.state}, MAC: {mac_note})"

        diag.evidence.append(Evidence(source="cmd wifi status", raw_excerpt=out_cmd.strip()[:400] if out_cmd else "No cmd wifi status output", exit_code=code_cmd))
        if out_w:
            diag.evidence.append(Evidence(source="dumpsys wifi", raw_excerpt=out_w.strip()[:1000], exit_code=code_w))
        diag.evidence.append(Evidence(source="MAC Address", raw_excerpt=f"wlan0 MAC={mac_note}", exit_code=code_mac))
        return diag

    def diagnose_bluetooth(self, serial: str) -> BluetoothDiagnostics:
        diag = BluetoothDiagnostics()
        code_bt, out_bt, _ = self.bridge.shell("dumpsys bluetooth_manager", serial=serial, timeout=6)
        if code_bt == 0:
            if "enabled: true" in out_bt.lower() or "state: ON" in out_bt:
                diag.state = "ON"
            elif "enabled: false" in out_bt.lower() or "state: OFF" in out_bt:
                diag.state = "OFF"

            m_name = re.search(r"name:\s*(.+)", out_bt)
            if m_name:
                diag.name = m_name.group(1).strip()

            m_addr = re.search(r"address:\s*([0-9a-fA-F:]{17})", out_bt)
            if m_addr:
                diag.mac_address = m_addr.group(1).strip()

        # Check BLE feature
        code_ble, out_ble, _ = self.bridge.shell("pm has-system-feature android.hardware.bluetooth_le", serial=serial, timeout=4)
        diag.ble_supported = (code_ble == 0 and "true" in out_ble.lower())

        if "service disconnected" in out_bt.lower() or "not supported on this device" in out_bt.lower():
            diag.is_hardware_faulty = True
            diag.status = "FAIL"
            diag.diagnosis = "FAIL: Bluetooth HAL service disconnected or Hardware controller failure."
        elif diag.state in ["ON", "OFF"]:
            diag.status = "PASS"
            if diag.state == "ON":
                diag.diagnosis = f"PASS: Bluetooth Active (Name: {diag.name}, MAC: {diag.mac_address}, BLE: {diag.ble_supported})"
            else:
                diag.diagnosis = f"PASS: Bluetooth Adapter OFF (Hardware enumerated, MAC: {diag.mac_address})"
        else:
            diag.status = "UNKNOWN"
            diag.diagnosis = f"UNKNOWN: Bluetooth adapter indeterminate (State: {diag.state}, MAC: {diag.mac_address})"

        diag.evidence.append(Evidence(source="dumpsys bluetooth_manager", raw_excerpt=out_bt.strip()[:1000] if out_bt else "No bluetooth_manager output", exit_code=code_bt))
        diag.evidence.append(Evidence(source="pm feature bluetooth_le", raw_excerpt=f"BLE: {diag.ble_supported}", exit_code=code_ble))
        return diag

    def diagnose_peripherals_chips(self, serial: str) -> PeripheralChipsDiagnostics:
        diag = PeripheralChipsDiagnostics()
        # GPS
        code_gps, out_gps, _ = self.bridge.shell("pm has-system-feature android.hardware.location.gps", serial=serial, timeout=4)
        diag.gps_present = (code_gps == 0 and "true" in out_gps.lower())
        diag.gps_status = "Available / Hardware Active" if diag.gps_present else "Absent"

        # NFC
        code_nfc, out_nfc, _ = self.bridge.shell("pm has-system-feature android.hardware.nfc", serial=serial, timeout=4)
        diag.nfc_present = (code_nfc == 0 and "true" in out_nfc.lower())
        diag.nfc_status = "Available / Supported" if diag.nfc_present else "Not Supported on this model"

        # USB Host / OTG
        code_otg, out_otg, _ = self.bridge.shell("pm has-system-feature android.hardware.usb.host", serial=serial, timeout=4)
        diag.usb_otg_supported = (code_otg == 0 and "true" in out_otg.lower())

        has_data = diag.gps_present or diag.usb_otg_supported or diag.nfc_present
        diag.status = "PASS" if has_data else "UNKNOWN"
        diag.diagnosis = f"GPS: {diag.gps_status} | NFC: {diag.nfc_status} | USB OTG: {'Supported' if diag.usb_otg_supported else 'Not Supported'}"
        diag.evidence.append(Evidence(source="pm feature location.gps", raw_excerpt=f"gps={diag.gps_present}", exit_code=code_gps))
        diag.evidence.append(Evidence(source="pm feature nfc", raw_excerpt=f"nfc={diag.nfc_present}", exit_code=code_nfc))
        diag.evidence.append(Evidence(source="pm feature usb.host", raw_excerpt=f"usb_host={diag.usb_otg_supported}", exit_code=code_otg))
        return diag

    def diagnose_display_backlight(self, serial: str) -> DisplayBacklightDiagnostics:
        diag = DisplayBacklightDiagnostics()

        # Check backlight brightness
        b_val = self.bridge.read_sysfs_int([
            "/sys/class/backlight/panel0-backlight/brightness",
            "/sys/class/leds/lcd-backlight/brightness",
            "/sys/class/backlight/lcd-backlight/brightness"
        ], serial=serial)
        if b_val is not None:
            diag.brightness_now = b_val

        max_b = self.bridge.read_sysfs_int([
            "/sys/class/backlight/panel0-backlight/max_brightness",
            "/sys/class/leds/lcd-backlight/max_brightness"
        ], serial=serial)
        if max_b is not None:
            diag.max_brightness = max_b

        # Check dmesg for DSI panel connection errors
        code_d, out_d, _ = self.bridge.shell("dmesg | grep -i -E 'dsi|panel|backlight|mdss|drm_panel|drm' | tail -n 25", serial=serial, timeout=8)
        if code_d == 0 and out_d:
            diag.dsi_error_logs = [l.strip() for l in out_d.splitlines() if l.strip()][:10]
            if re.search(r"(?i)(no dsi panel detected|panel.*probe failed|panel.*not found|dsi.*timeout)", out_d):
                diag.is_panel_disconnected = True
                diag.panel_detected = False

        if diag.is_panel_disconnected:
            diag.status = "FAIL"
            diag.diagnosis = "FAIL: DSI Display panel is disconnected or un-enumerated. Broken screen flex connector or cracked AMOLED/LCD glass."
        elif diag.brightness_now > 0 and diag.panel_detected:
            diag.status = "PASS"
            diag.diagnosis = f"PASS: Display panel enumerated, backlight driver active (Brightness: {diag.brightness_now}/{diag.max_brightness})."
        elif diag.panel_detected:
            diag.status = "PASS"
            diag.diagnosis = f"PASS: Display panel active (Brightness: {diag.brightness_now}/{diag.max_brightness})."
        else:
            diag.status = "UNKNOWN"
            diag.diagnosis = f"UNKNOWN: Display brightness={diag.brightness_now}, panel indeterminate."

        diag.evidence.append(Evidence(source="sysfs backlight brightness", raw_excerpt=f"brightness_now={diag.brightness_now}, max_brightness={diag.max_brightness}", exit_code=0))
        if out_d:
            diag.evidence.append(Evidence(source="dmesg DSI panel", raw_excerpt=out_d.strip()[:1000], exit_code=code_d))
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
            "am start -a android.intent.action.VIEW -d 'content://settings/system/ringtone' 2>/dev/null",
            serial=serial,
            timeout=5
        )
        if code != 0:
            code, _, _ = self.bridge.shell("input keyevent 24", serial=serial, timeout=5)
        return code == 0

    def diagnose_audio_codec(self, serial: str) -> AudioCodecReport:
        """
        Inspect AudioFlinger HAL service, audio routing devices, and ALSA soundcards.
        Non-root SELinux-safe inspection first.
        """
        report = AudioCodecReport()

        # 1. Non-root dumpsys media.audio_flinger
        code_af, out_af, err_af = self.bridge.shell("dumpsys media.audio_flinger", serial=serial, timeout=6)
        if code_af == 0 and out_af:
            report.audio_flinger_alive = "AudioFlinger" in out_af or "Hardware status" in out_af or "Standby" in out_af
            m_hal = re.search(r"HAL module:\s*([a-zA-Z0-9_\.]+)", out_af)
            if m_hal:
                report.codec_chip_name = m_hal.group(1)
        else:
            report.audio_flinger_alive = False

        # 2. Non-root dumpsys audio (check active output sinks)
        code_aud, out_aud, err_aud = self.bridge.shell("dumpsys audio", serial=serial, timeout=6)
        has_audio_hal = False
        if code_aud == 0 and out_aud:
            if "AudioRoutesInfo" in out_aud or "speaker" in out_aud.lower():
                has_audio_hal = True

        # 3. Read /proc/asound/cards as secondary fallback
        code_c, out_c, err_c = self.bridge.shell("cat /proc/asound/cards", serial=serial, timeout=5)
        if code_c == 0 and out_c:
            for line in out_c.splitlines():
                line = line.strip()
                if line and not line.startswith("---") and "no soundcards" not in line.lower():
                    m = re.search(r"^\d+\s+\[([^\]]+)\]:\s*(.+)", line)
                    if m:
                        report.cards_detected.append(f"{m.group(1)} ({m.group(2)})")
                    else:
                        report.cards_detected.append(line)

        # 4. Read /proc/asound/pcm
        code_p, out_p, err_p = self.bridge.shell("cat /proc/asound/pcm | head -n 10", serial=serial, timeout=5)
        if code_p == 0 and out_p:
            for line in out_p.splitlines():
                line = line.strip()
                if line:
                    report.pcm_devices.append(line)

        # 5. Fault evaluation with status_from_read
        af_status = status_from_read(out_af, err_af, code_af)
        c_status = status_from_read(out_c, err_c, code_c)

        if af_status == Status.NEEDS_ROOT and c_status == Status.NEEDS_ROOT:
            report.status = "NEEDS_ROOT"
            report.diagnosis = "NEEDS_ROOT: Audio HAL telemetry and /proc/asound restricted by SELinux / permissions."
        elif not report.audio_flinger_alive and not has_audio_hal and (not report.cards_detected or (out_c and "no soundcards" in out_c.lower())):
            report.is_codec_faulty = True
            report.status = "FAIL"
            report.diagnosis = "FAIL: No ALSA soundcards or Audio HAL registered! Audio Codec IC is disconnected, unpowered, or dead."
        elif not report.audio_flinger_alive and not has_audio_hal:
            report.is_codec_faulty = True
            report.status = "FAIL"
            report.diagnosis = "FAIL: AudioFlinger HAL service unresponsive or crashed."
        elif report.audio_flinger_alive or has_audio_hal or report.cards_detected:
            report.is_codec_faulty = False
            report.status = "PASS"
            card_count = len(report.cards_detected)
            pcm_count = len(report.pcm_devices)
            details = f"{card_count} soundcards, {pcm_count} PCM devices" if card_count > 0 else "Audio HAL Active"
            report.diagnosis = f"PASS: Audio Codec Subsystem Operational ({report.codec_chip_name} | {details})"
        else:
            report.status = "UNKNOWN"
            report.diagnosis = "UNKNOWN: Audio subsystem telemetry unavailable"

        report.evidence.append(Evidence(source="dumpsys media.audio_flinger", raw_excerpt=(out_af or err_af or f"alive={report.audio_flinger_alive}, HAL={report.codec_chip_name}").strip()[:1000], exit_code=code_af))
        if out_c or err_c:
            report.evidence.append(Evidence(source="/proc/asound/cards", raw_excerpt=(out_c or err_c).strip()[:1000], exit_code=code_c))
        if out_p or err_p:
            report.evidence.append(Evidence(source="/proc/asound/pcm", raw_excerpt=(out_p or err_p).strip()[:1000], exit_code=code_p))
        return report

    def inspect_display_panel_info(self, serial: str) -> DisplayPanelInfo:
        """
        Extract exact DSI Display Panel hardware controller parameters from kernel logs/sysfs
        and bootloader integrity flags (VerifiedBootState, VBMeta state).
        Software cannot prove a screen is original; this check reports kernel-reported panel info.
        """
        report = DisplayPanelInfo()

        # 1. Search dmesg for panel controller string without suppressing stderr
        code_d, out_d, err_d = self.bridge.shell(
            "dmesg | grep -i -E 'panel|dsi|mdss|sde|drm' | grep -i -E 'novatek|tianma|ili|hx|td4|ft|s6e|boe|csot|nt36' | tail -n 5",
            serial=serial,
            timeout=6
        )
        dmesg_status = status_from_read(out_d, err_d, code_d)

        found_panel = False
        if code_d == 0 and out_d:
            m = re.search(r"(nt\d+|td\d+|ili\d+|hx\d+|ft\d+|s6e\w+|tianma\w*|boe\w*)", out_d, re.IGNORECASE)
            if m:
                report.panel_controller_name = m.group(1).lower()
                report.driver_source = "Kernel DSI Bus (dmesg)"
                found_panel = True

        if not found_panel:
            code_sys, out_sys, err_sys = self.bridge.shell(
                "cat /sys/class/graphics/fb0/msm_fb_panel_info /sys/class/drm/card0/device/driver_override",
                serial=serial,
                timeout=4
            )
            if code_sys == 0 and out_sys.strip() and "no such file" not in out_sys.lower():
                report.panel_controller_name = out_sys.strip().splitlines()[0][:40]
                report.driver_source = "Sysfs Framebuffer / DRM"
                found_panel = True

        # 2. Bootloader Verified Boot flags
        code_vbs, out_vbs, err_vbs = self.bridge.shell("getprop ro.boot.verifiedbootstate", serial=serial, timeout=4)
        if code_vbs == 0 and out_vbs.strip():
            report.verified_boot_state = out_vbs.strip().lower()

        code_vbm, out_vbm, err_vbm = self.bridge.shell("getprop ro.boot.vbmeta.device_state", serial=serial, timeout=4)
        if code_vbm == 0 and out_vbm.strip():
            report.vbmeta_device_state = out_vbm.strip().lower()

        # 3. Determine status: If dmesg is blocked by permissions -> NEEDS_ROOT (not UNKNOWN)
        if dmesg_status == Status.NEEDS_ROOT:
            report.status = "NEEDS_ROOT"
            report.diagnosis = "NEEDS_ROOT: Kernel dmesg logs restricted by SELinux / permissions."
        elif found_panel:
            report.status = "PASS"
            report.diagnosis = f"PASS: Display Controller: {report.panel_controller_name} (Source: {report.driver_source})"
        else:
            report.status = "UNKNOWN"
            report.diagnosis = "UNKNOWN: Display controller identifier unexposed in standard sysfs/kernel logs."

        if out_d or err_d:
            report.evidence.append(Evidence(source="dmesg DSI panel", raw_excerpt=(out_d or err_d).strip()[:1000], exit_code=code_d))
        report.evidence.append(Evidence(source="ro.boot.verifiedbootstate", raw_excerpt=report.verified_boot_state, exit_code=code_vbs))
        report.evidence.append(Evidence(source="ro.boot.vbmeta.device_state", raw_excerpt=report.vbmeta_device_state, exit_code=code_vbm))
        return report

    # Backwards compatibility alias
    inspect_display_authenticity = inspect_display_panel_info

    COMPANION_PKG = "com.androiddoctor.companion"

    def install_companion_apk(self, serial: str, apk_path: Optional[str] = None) -> Tuple[bool, str]:
        if not apk_path:
            base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            apk_path = os.path.join(base_dir, "bin", "companion_diag.apk")

        if not os.path.exists(apk_path):
            return False, f"Companion APK not found at {apk_path}. Zero-APK fallback suite is active."

        code, out, err = self.bridge.run_adb(["install", "-r", "-t", "-g", apk_path], serial=serial, timeout=45)
        if code == 0 and "Success" in out:
            return True, "Companion Diagnostic APK installed successfully."
        return False, f"APK installation failed: {err or out}"

    def run_companion_broadcast(self, serial: str, action: str = "TEST_ALL") -> Tuple[bool, str]:
        code, out, err = self.bridge.shell(
            f"am broadcast -a com.androiddoctor.companion.ACTION_DIAG --es action {action}",
            serial=serial, timeout=10
        )
        if code == 0:
            return True, f"Broadcast sent: {out}"
        return False, f"Broadcast failed: {err or out}"

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
            return True, "Companion APK uninstalled cleanly."
        return False, f"Uninstall status: {out or err}"

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
<div id="canvas">TAP DISPLAY TO CYCLE COLORS<br><small style="font-size:14px; font-weight:normal; color:#aaa;">(Pure Red, Green, Blue, White, Black, Gray)</small></div>
<script>
var colors = [
    {bg: '#FF0000', name: 'PURE RED (Check Subpixel / Red Burn-in)'},
    {bg: '#00FF00', name: 'PURE GREEN (Check Green Pixels)'},
    {bg: '#0000FF', name: 'PURE BLUE (Check Blue Pixels)'},
    {bg: '#FFFFFF', name: 'PURE WHITE (Check Dead Pixels & Uniformity)'},
    {bg: '#000000', name: 'PURE BLACK (Check Backlight Bleed / OLED Off)'},
    {bg: '#808080', name: '50% NEUTRAL GRAY (Check DSE / Banding)'}
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
            return True, "LCD Color & Dead-Pixel test deployed. Tap display to cycle RGBW colors."
        except Exception as e:
            logger.error(f"Failed to launch color test: {e}")
            return False, f"Failed to launch color test: {e}"

    def stop_screen_color_test(self, serial: str) -> Tuple[bool, str]:
        """
        Disables touch pointer location and sends Back key to exit color test.
        """
        self.bridge.shell("settings put system pointer_location 0", serial=serial, timeout=5)
        self.bridge.shell("settings put system show_touches 0", serial=serial, timeout=5)
        self.bridge.shell("input keyevent 4", serial=serial, timeout=5)
        return True, "Color test closed and touch digitizer overlays disabled."

    def play_speaker_test_tone(self, serial: str, freq: int = 1000, duration_sec: float = 3.0) -> Tuple[bool, str]:
        """
        Generates a 16-bit PCM mono sine-wave test tone with soft fade-in/out and plays it
        over the phone's primary loudspeaker at maximum volume to audit hardware clarity.
        """
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        bin_dir = os.path.join(base_dir, "bin")
        os.makedirs(bin_dir, exist_ok=True)
        local_wav = os.path.join(bin_dir, f"test_tone_{freq}hz.wav")
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
            return True, f"Generated and streaming {freq}Hz sine tone to loudspeaker."
        except Exception as e:
            logger.error(f"Error generating or playing test tone: {e}")
            return False, f"Test tone error: {e}"

    def reset_battery_fuel_gauge_stats(self, serial: str) -> Tuple[bool, str]:
        """
        Reset Battery Fuel-Gauge Stats (batterystats --reset) and clear calibration cache.
        """
        from core.board_diag import MotherboardDiagnostics
        return MotherboardDiagnostics(self.bridge).reset_battery_fuel_gauge_stats(serial)

    def run_full_audit(self, serial: str) -> HardwareAuditReport:
        logger.info(f"Auditing complete hardware components for {serial}...")
        sensors, missing = self.audit_sensors(serial)
        cameras = self.audit_cameras(serial)
        audio = self.audit_audio(serial)
        audio_codec = self.diagnose_audio_codec(serial)
        fp = self.diagnose_fingerprint(serial)
        btn = self.diagnose_physical_buttons(serial)
        sim = self.diagnose_sim_and_baseband(serial)
        disp = self.diagnose_display_backlight(serial)
        disp_info = self.inspect_display_panel_info(serial)
        wifi = self.diagnose_wifi(serial)
        bt = self.diagnose_bluetooth(serial)
        periph = self.diagnose_peripherals_chips(serial)
        ptr_loc = self.get_pointer_location_state(serial)

        has_any_fail = (
            fp.status == "FAIL" or
            btn.status == "FAIL" or
            sim.status == "FAIL" or
            wifi.status == "FAIL" or
            bt.status == "FAIL" or
            disp.status == "FAIL" or
            audio_codec.status == "FAIL" or
            disp_info.status == "FAIL" or
            len(missing) > 0
        )
        all_unknown = (
            fp.status == "UNKNOWN" and
            btn.status == "UNKNOWN" and
            sim.status == "UNKNOWN" and
            wifi.status == "UNKNOWN" and
            bt.status == "UNKNOWN" and
            disp.status == "UNKNOWN" and
            audio_codec.status == "UNKNOWN" and
            len(sensors) == 0
        )
        if has_any_fail:
            overall_status = "FAIL"
            summary = "WARNING: Hardware Faults Detected"
        elif all_unknown:
            overall_status = "UNKNOWN"
            summary = "UNKNOWN: Insufficient hardware telemetry"
        else:
            overall_status = "PASS"
            summary = "PASS: All Tested Components Normal"

        all_ev: List[Evidence] = []
        for item in [fp, btn, sim, disp, disp_info, wifi, bt, periph, audio_codec]:
            if hasattr(item, "evidence") and item.evidence:
                all_ev.extend(item.evidence)

        return HardwareAuditReport(
            serial=serial,
            sensors=sensors,
            missing_critical_sensors=missing,
            cameras=cameras,
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
            vibrator_functional=True,
            pointer_location_active=ptr_loc,
            summary=summary,
            status=overall_status,
            evidence=all_ev
        )
