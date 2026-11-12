import re
import logging
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
from core.adb_bridge import ADBBridge

logger = logging.getLogger("Profiler")


@dataclass
class DeviceProfile:
    serial: str = "Unknown"
    state: str = "offline"
    brand: str = "Unknown"
    manufacturer: str = "Unknown"
    model: str = "Unknown"
    device_codename: str = "Unknown"
    product_name: str = "Unknown"
    android_version: str = "Unknown"
    sdk_version: str = "Unknown"
    build_id: str = "Unknown"
    build_fingerprint: str = "Unknown"
    security_patch: str = "Unknown"
    soc_chipset: str = "Unknown"
    hardware: str = "Unknown"
    cpu_architecture: str = "Unknown"
    cpu_cores: Optional[int] = None
    kernel_version: str = "Unknown"
    is_rooted: bool = False
    selinux_mode: str = "Unknown"
    encryption_status: str = "Unknown"
    uptime_seconds: Optional[float] = None
    uptime_human: str = "Unknown"
    ram_total_mb: Optional[float] = None
    ram_free_mb: Optional[float] = None
    ram_used_pct: Optional[float] = None
    storage_total_gb: Optional[float] = None
    storage_free_gb: Optional[float] = None
    storage_used_pct: Optional[float] = None
    battery_level_pct: Optional[int] = None
    battery_status: str = "Unknown"
    battery_health: str = "Unknown"
    battery_technology: str = "Unknown"
    battery_temp_c: Optional[float] = None
    battery_voltage_mv: Optional[float] = None
    display_resolution: str = "Unknown"
    display_density: str = "Unknown"
    display_refresh_rate: str = "Unknown"
    baseband_version: str = "Unknown"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class DeviceProfiler:
    def __init__(self, bridge: Optional[ADBBridge] = None):
        self.bridge = bridge or ADBBridge()

    def profile_device(self, serial: str) -> DeviceProfile:
        profile = DeviceProfile(serial=serial)
        if not self.bridge.is_device_ready(serial):
            profile.state = "offline"
            return profile

        profile.state = "device"
        props = self.bridge.get_all_props(serial)

        profile.brand = props.get("ro.product.brand", props.get("ro.product.vendor.brand", "Unknown")).capitalize()
        profile.manufacturer = props.get("ro.product.manufacturer", props.get("ro.product.vendor.manufacturer", "Unknown")).capitalize()
        profile.model = props.get("ro.product.model", props.get("ro.product.vendor.model", "Unknown"))
        profile.device_codename = props.get("ro.product.device", props.get("ro.build.product", "Unknown"))
        profile.product_name = props.get("ro.product.name", "Unknown")

        profile.android_version = props.get("ro.build.version.release", "Unknown")
        profile.sdk_version = props.get("ro.build.version.sdk", "Unknown")
        profile.build_id = props.get("ro.build.display.id", props.get("ro.build.id", "Unknown"))
        profile.build_fingerprint = props.get("ro.build.fingerprint", "Unknown")
        profile.security_patch = props.get("ro.build.version.security_patch", "Unknown")

        soc = props.get("ro.board.platform", "")
        if not soc:
            soc = props.get("ro.soc.model", props.get("ro.hardware", "Unknown"))
        profile.soc_chipset = soc.upper() if soc else "Unknown"
        profile.hardware = props.get("ro.hardware", "Unknown")
        profile.cpu_architecture = props.get("ro.product.cpu.abi", props.get("ro.product.cpu.abilist", "Unknown"))

        profile.baseband_version = props.get("gsm.version.baseband", props.get("ro.boot.baseband", "Unknown"))

        profile.is_rooted = self.bridge.is_root_available(serial)

        code_enforce, out_enforce, _ = self.bridge.shell("getenforce", serial=serial, timeout=5)
        profile.selinux_mode = out_enforce.strip() if code_enforce == 0 and out_enforce else "Unknown"

        crypto = props.get("ro.crypto.state", "")
        if not crypto:
            code_crypt, out_crypt, _ = self.bridge.shell("getprop vold.decrypt", serial=serial, timeout=5)
            crypto = out_crypt.strip()
        profile.encryption_status = crypto if crypto else "Unknown"  # not reported is not "unencrypted"

        self._parse_cpu_info(serial, profile)
        self._parse_kernel(serial, profile)
        self._parse_uptime(serial, profile)
        self._parse_memory(serial, profile)
        self._parse_storage(serial, profile)
        self._parse_battery(serial, profile)
        self._parse_display(serial, profile)

        return profile

    def _parse_cpu_info(self, serial: str, profile: DeviceProfile):
        code, out, _ = self.bridge.shell("cat /proc/cpuinfo", serial=serial, timeout=8)
        if code == 0 and out:
            processors = re.findall(r"processor\s*:\s*\d+", out, re.IGNORECASE)
            profile.cpu_cores = len(processors) if processors else None
            if profile.hardware == "Unknown":
                hardware_match = re.search(r"Hardware\s*:\s*(.+)", out, re.IGNORECASE)
                if hardware_match:
                    profile.hardware = hardware_match.group(1).strip()
        else:
            profile.cpu_cores = None

    def _parse_kernel(self, serial: str, profile: DeviceProfile):
        code, out, _ = self.bridge.shell("uname -a", serial=serial, timeout=5)
        if code == 0 and out:
            profile.kernel_version = out.strip()
        else:
            code_ver, out_ver, _ = self.bridge.shell("cat /proc/version", serial=serial, timeout=5)
            if code_ver == 0 and out_ver:
                profile.kernel_version = out_ver.strip()

    def _parse_uptime(self, serial: str, profile: DeviceProfile):
        code, out, _ = self.bridge.shell("cat /proc/uptime", serial=serial, timeout=5)
        if code == 0 and out:
            parts = out.split()
            if parts:
                try:
                    secs = float(parts[0])
                    profile.uptime_seconds = secs
                    hours = int(secs // 3600)
                    minutes = int((secs % 3600) // 60)
                    profile.uptime_human = f"{hours}h {minutes}m"
                except ValueError:
                    pass

    def _parse_memory(self, serial: str, profile: DeviceProfile):
        code, out, _ = self.bridge.shell("cat /proc/meminfo", serial=serial, timeout=8)
        if code == 0 and out:
            mem_total_kb = 0
            mem_free_kb = 0
            mem_avail_kb = 0
            for line in out.splitlines():
                if "MemTotal:" in line:
                    m = re.search(r"(\d+)", line)
                    if m:
                        mem_total_kb = int(m.group(1))
                elif "MemAvailable:" in line:
                    m = re.search(r"(\d+)", line)
                    if m:
                        mem_avail_kb = int(m.group(1))
                elif "MemFree:" in line and mem_avail_kb == 0:
                    m = re.search(r"(\d+)", line)
                    if m:
                        mem_free_kb = int(m.group(1))

            effective_free_kb = mem_avail_kb if mem_avail_kb > 0 else mem_free_kb
            if mem_total_kb > 0:
                profile.ram_total_mb = round(mem_total_kb / 1024.0, 1)
                profile.ram_free_mb = round(effective_free_kb / 1024.0, 1)
                used_mb = profile.ram_total_mb - profile.ram_free_mb
                profile.ram_used_pct = round((used_mb / profile.ram_total_mb) * 100.0, 1)

    def _parse_storage(self, serial: str, profile: DeviceProfile):
        code, out, _ = self.bridge.shell("df -k /data", serial=serial, timeout=8)
        if code == 0 and out:
            lines = [l.strip() for l in out.splitlines() if l.strip()]
            for line in lines[1:]:
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        total_1k = int(parts[1])
                        avail_1k = int(parts[3])
                        profile.storage_total_gb = round(total_1k / (1024.0 * 1024.0), 2)
                        profile.storage_free_gb = round(avail_1k / (1024.0 * 1024.0), 2)
                        used_gb = profile.storage_total_gb - profile.storage_free_gb
                        if profile.storage_total_gb > 0:
                            profile.storage_used_pct = round((used_gb / profile.storage_total_gb) * 100.0, 1)
                        break
                    except ValueError:
                        continue

    def _parse_battery(self, serial: str, profile: DeviceProfile):
        code, out, _ = self.bridge.shell("dumpsys battery", serial=serial, timeout=8)
        if code == 0 and out:
            for line in out.splitlines():
                line = line.strip()
                if line.startswith("level:"):
                    m = re.search(r"(\d+)", line)
                    if m:
                        profile.battery_level_pct = int(m.group(1))
                elif line.startswith("status:"):
                    val = line.split(":", 1)[1].strip()
                    status_map = {"1": "Unknown", "2": "Charging", "3": "Discharging", "4": "Not charging", "5": "Full"}
                    profile.battery_status = status_map.get(val, val)
                elif line.startswith("health:"):
                    val = line.split(":", 1)[1].strip()
                    health_map = {"1": "Unknown", "2": "Good", "3": "Overheat", "4": "Dead", "5": "Over voltage", "6": "Unspecified failure", "7": "Cold"}
                    profile.battery_health = health_map.get(val, val)
                elif line.startswith("technology:"):
                    profile.battery_technology = line.split(":", 1)[1].strip()
                elif line.startswith("temperature:"):
                    m = re.search(r"(-?\d+)", line)
                    if m:
                        profile.battery_temp_c = round(int(m.group(1)) / 10.0, 1)
                elif line.startswith("voltage:"):
                    m = re.search(r"(\d+)", line)
                    if m:
                        profile.battery_voltage_mv = float(m.group(1))

    def _parse_display(self, serial: str, profile: DeviceProfile):
        code_size, out_size, _ = self.bridge.shell("wm size", serial=serial, timeout=5)
        if code_size == 0 and out_size:
            m = re.search(r"(\d+x\d+)", out_size)
            if m:
                profile.display_resolution = m.group(1)

        code_den, out_den, _ = self.bridge.shell("wm density", serial=serial, timeout=5)
        if code_den == 0 and out_den:
            m = re.search(r"(\d+)", out_den)
            if m:
                profile.display_density = f"{m.group(1)} dpi"

        code_disp, out_disp, _ = self.bridge.shell("dumpsys display | grep -iE 'mDefaultDisplay|refreshRate|fps'", serial=serial, timeout=5)
        if code_disp == 0 and out_disp:
            m = re.search(r"(\d+(?:\.\d+)?)\s*fps", out_disp, re.IGNORECASE)
            if not m:
                m = re.search(r"refreshRate\s*[:=]\s*(\d+(?:\.\d+)?)", out_disp, re.IGNORECASE)
            if m:
                profile.display_refresh_rate = f"{m.group(1)} Hz"
