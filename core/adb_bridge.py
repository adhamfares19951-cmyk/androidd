import os
import sys
import subprocess
import threading
import logging
import re
import time
from typing import List, Dict, Tuple, Optional, Callable, Any
from setup_bins import get_adb_path, get_fastboot_path, get_scrcpy_path, CREATE_NO_WINDOW

logger = logging.getLogger("ADBBridge")


ADB_SERVER_PORT = int(os.environ.get("ANDROID_ADB_SERVER_PORT", "5037"))

# shell v2 packet ids (see adb shell_protocol.h)
_SHELL_V2_STDOUT = 1
_SHELL_V2_STDERR = 2
_SHELL_V2_EXIT = 3


def _recv_exact(sock, n: int) -> bytes:
    buf = b""
    while len(buf) < n:
        part = sock.recv(n - len(buf))
        if not part:
            raise ConnectionError("adb socket closed")
        buf += part
    return buf


def _adb_send(sock, service: str) -> None:
    payload = service.encode("utf-8")
    sock.sendall(f"{len(payload):04x}".encode("ascii") + payload)


def _adb_read_okay(sock) -> bool:
    status = _recv_exact(sock, 4)
    if status == b"OKAY":
        return True
    if status == b"FAIL":
        try:
            length = int(_recv_exact(sock, 4).decode("ascii"), 16)
            msg = _recv_exact(sock, length).decode("utf-8", errors="replace")
            logger.debug(f"adb server FAIL: {msg}")
        except Exception:
            pass
    return False


def _normalize_output(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace")
    return text.replace("\r\r\n", "\n").replace("\r\n", "\n").strip()


def _iter_shell_v2_packets(data: bytes):
    pos = 0
    while pos + 5 <= len(data):
        pkt_id = data[pos]
        length = int.from_bytes(data[pos + 1:pos + 5], "little")
        if pos + 5 + length > len(data):
            break
        yield pkt_id, data[pos + 5:pos + 5 + length]
        pos += 5 + length


def _shell_v2_has_exit(data: bytes) -> bool:
    return any(pkt_id == _SHELL_V2_EXIT for pkt_id, _ in _iter_shell_v2_packets(data))


def parse_shell_v2_stream(data: bytes) -> Tuple[bytes, bytes, Optional[int]]:
    """Split a shell v2 byte stream into (stdout, stderr, exit_code). exit_code is None if no exit packet arrived."""
    out, err, code = b"", b"", None
    for pkt_id, payload in _iter_shell_v2_packets(data):
        if pkt_id == _SHELL_V2_STDOUT:
            out += payload
        elif pkt_id == _SHELL_V2_STDERR:
            err += payload
        elif pkt_id == _SHELL_V2_EXIT and payload:
            code = payload[0]
    return out, err, code


class ADBBridge:
    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ADBBridge, cls).__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self):
        if getattr(self, "_initialized", False):
            return
        self.adb_path = get_adb_path()
        self.fastboot_path = get_fastboot_path()
        self.scrcpy_path = get_scrcpy_path()
        self._cmd_lock = threading.Lock()
        self._capability_cache: Dict[str, Dict[str, Any]] = {}
        self._initialized = True
        logger.info(f"ADBBridge initialized with ADB: {self.adb_path}, Fastboot: {self.fastboot_path}")

    def get_cached_capability(self, serial: str, key: str) -> Optional[Any]:
        if not serial:
            return None
        return self._capability_cache.get(serial, {}).get(key)

    def set_cached_capability(self, serial: str, key: str, value: Any) -> None:
        if not serial:
            return
        if serial not in self._capability_cache:
            self._capability_cache[serial] = {}
        self._capability_cache[serial][key] = value

    def clear_capability_cache(self, serial: Optional[str] = None) -> None:
        if serial:
            self._capability_cache.pop(serial, None)
        else:
            self._capability_cache.clear()

    # ------------------------------------------------------------------
    # Direct ADB server socket client
    # ------------------------------------------------------------------
    def _socket_open_transport(self, serial: Optional[str], timeout: float):
        """Open a socket to the local adb server and switch it to the device transport."""
        import socket
        sock = socket.create_connection(("127.0.0.1", ADB_SERVER_PORT), timeout=timeout)
        sock.settimeout(timeout)
        _adb_send(sock, f"host:transport:{serial}" if serial else "host:transport-any")
        if not _adb_read_okay(sock):
            sock.close()
            return None
        return sock

    def _device_supports_shell_v2(self, serial: Optional[str], timeout: float = 5.0) -> Optional[bool]:
        """Ask the adb server for the device feature list (cached per serial)."""
        cache_key = serial or "__any__"
        cached = self._capability_cache.get(cache_key, {}).get("shell_v2")
        if cached is not None:
            return cached
        import socket
        try:
            sock = socket.create_connection(("127.0.0.1", ADB_SERVER_PORT), timeout=timeout)
            sock.settimeout(timeout)
            _adb_send(sock, f"host-serial:{serial}:features" if serial else "host:features")
            if not _adb_read_okay(sock):
                sock.close()
                return None
            length = int(_recv_exact(sock, 4).decode("ascii"), 16)
            features = _recv_exact(sock, length).decode("utf-8", errors="replace")
            sock.close()
        except Exception as e:
            logger.debug(f"adb features query failed: {e}")
            return None
        supported = "shell_v2" in features.split(",")
        self._capability_cache.setdefault(cache_key, {})["shell_v2"] = supported
        return supported

    def _direct_socket_shell(self, command: str, serial: Optional[str] = None, timeout: int = 15) -> Optional[Tuple[Optional[int], str, str]]:
        """
        Run a shell command through the local adb server socket.

        Uses the shell v2 protocol when the device supports it, which keeps stdout and
        stderr separate and reports the real remote exit code. Devices without shell v2
        (Android 6 and older) use the legacy protocol: stderr is merged into stdout and
        the exit code is unknown, so it is returned as None (never a fake 0).

        Returns None when the adb server or device transport could not be reached.
        On timeout returns exit code -1 with the partial output and a timeout note in stderr.
        """
        import socket
        v2 = self._device_supports_shell_v2(serial)
        if v2 is None:
            return None
        try:
            sock = self._socket_open_transport(serial, timeout)
            if sock is None:
                return None
            _adb_send(sock, f"shell,v2,raw:{command}" if v2 else f"shell:{command}")
            if not _adb_read_okay(sock):
                sock.close()
                return None

            chunks = []
            timed_out = False
            while True:
                try:
                    data = sock.recv(65536)
                except socket.timeout:
                    timed_out = True
                    break
                if not data:
                    break
                chunks.append(data)
                if v2 and _shell_v2_has_exit(b"".join(chunks)):
                    break
            sock.close()
        except Exception as e:
            logger.debug(f"Direct ADB socket exception: {e}")
            return None

        raw = b"".join(chunks)
        if v2:
            out_b, err_b, code = parse_shell_v2_stream(raw)
        else:
            out_b, err_b, code = raw, b"", None
        out = _normalize_output(out_b)
        err = _normalize_output(err_b)
        if timed_out:
            err = (err + "\n" if err else "") + f"Command timed out after {timeout} seconds"
            code = -1
        return code, out, err

    def refresh_paths(self):
        self.adb_path = get_adb_path()
        self.fastboot_path = get_fastboot_path()
        self.scrcpy_path = get_scrcpy_path()

    def run_adb(self, args: List[str], serial: Optional[str] = None, timeout: int = 30) -> Tuple[int, str, str]:
        cmd = [self.adb_path]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(args)

        with self._cmd_lock:
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=CREATE_NO_WINDOW,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout
                )
                return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
            except subprocess.TimeoutExpired:
                logger.warning(f"ADB command timed out after {timeout}s: {' '.join(cmd)}")
                return -1, "", f"Command timed out after {timeout} seconds"
            except FileNotFoundError:
                logger.error(f"ADB binary not found at {self.adb_path}")
                return -2, "", f"ADB binary not found at {self.adb_path}"
            except Exception as e:
                logger.error(f"Unexpected error running ADB: {e}")
                return -3, "", str(e)

    def shell(self, command: str, serial: Optional[str] = None, timeout: int = 30, root: bool = False, use_socket: bool = True) -> Tuple[Optional[int], str, str]:
        """
        Run a shell command on the device. Returns (exit_code, stdout, stderr).
        exit_code is the real remote exit code, or None when the device cannot report it.
        With root=True the command is wrapped in `su -c`; if that fails the command is
        re-run without root so the caller sees the real (usually permission) error.
        """
        if root:
            escaped_cmd = command.replace('"', '\\"')
            wrapped_cmd = f'su -c "{escaped_cmd}"'
            res = self._shell_once(wrapped_cmd, serial, timeout, use_socket)
            if res[0] == 0:
                return res
        return self._shell_once(command, serial, timeout, use_socket)

    def _shell_once(self, command: str, serial: Optional[str], timeout: int, use_socket: bool) -> Tuple[Optional[int], str, str]:
        if use_socket:
            sock_res = self._direct_socket_shell(command, serial=serial, timeout=timeout)
            if sock_res is not None:
                return sock_res
        return self.run_adb(["shell", command], serial=serial, timeout=timeout)

    def shell_stream(
        self,
        command: str,
        serial: Optional[str] = None,
        line_callback: Optional[Callable[[str], None]] = None,
        timeout: int = 180
    ) -> int:
        cmd = [self.adb_path]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(["shell", command])

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                creationflags=CREATE_NO_WINDOW,
                text=True,
                encoding="utf-8",
                errors="replace",
                bufsize=1
            )

            if hasattr(proc.stdout, "readline"):
                for line in iter(proc.stdout.readline, ""):
                    clean_line = line.strip()
                    if clean_line and line_callback:
                        line_callback(clean_line)
                proc.stdout.close()

            proc.wait(timeout=timeout)
            return proc.returncode
        except subprocess.TimeoutExpired:
            proc.kill()
            if line_callback:
                line_callback(f"[ERROR] Process timed out after {timeout}s")
            return -1
        except Exception as e:
            if line_callback:
                line_callback(f"[ERROR] Subprocess error: {e}")
            return -2

    def run_fastboot(self, args: List[str], serial: Optional[str] = None, timeout: int = 30) -> Tuple[int, str, str]:
        cmd = [self.fastboot_path]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(args)

        with self._cmd_lock:
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=CREATE_NO_WINDOW,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=timeout
                )
                return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
            except subprocess.TimeoutExpired:
                logger.warning(f"Fastboot command timed out after {timeout}s: {' '.join(cmd)}")
                return -1, "", f"Fastboot command timed out after {timeout}s"
            except FileNotFoundError:
                return -2, "", f"Fastboot binary not found at {self.fastboot_path}"
            except Exception as e:
                return -3, "", str(e)

    def list_devices(self) -> List[Dict[str, str]]:
        """
        Full state parser for adb devices (-l) and fastboot devices.
        Identifies: 'device', 'unauthorized', 'offline', 'recovery', 'sideload', 'fastboot'.
        """
        devices: List[Dict[str, str]] = []

        code, out, _ = self.run_adb(["devices", "-l"], timeout=10)
        if code == 0 and out:
            lines = out.splitlines()
            for line in lines[1:]:
                line = line.strip()
                if not line or line.startswith("*"):
                    continue
                parts = line.split()
                if len(parts) >= 2:
                    serial = parts[0]
                    state = parts[1].lower()
                    info = {"serial": serial, "state": state, "type": "adb", "model": "", "product": "", "device": ""}
                    for p in parts[2:]:
                        if ":" in p:
                            k, v = p.split(":", 1)
                            info[k] = v
                    if state == "unauthorized" and not info["model"]:
                        info["model"] = "Unauthorized (Awaiting RSA Approval on screen)"
                    elif state == "offline" and not info["model"]:
                        info["model"] = "Offline (Replug USB Cable)"
                    elif state == "recovery" and not info["model"]:
                        info["model"] = "Android Recovery Mode"
                    elif state == "sideload" and not info["model"]:
                        info["model"] = "Sideload / OTA Flashing Mode"
                    elif state == "device" and not info["model"]:
                        info["model"] = "Android Device"
                    devices.append(info)

        fb_code, fb_out, _ = self.run_fastboot(["devices"], timeout=10)
        if fb_code == 0 and fb_out:
            for line in fb_out.splitlines():
                line = line.strip()
                if not line:
                    continue
                parts = line.split()
                if len(parts) >= 2 and parts[1].lower() == "fastboot":
                    devices.append({
                        "serial": parts[0],
                        "state": "fastboot",
                        "type": "fastboot",
                        "model": "Fastboot Bootloader Mode",
                        "product": "bootloader",
                        "device": "fastboot"
                    })

        return devices

    def get_windows_pnp_usb_devices(self) -> List[Dict[str, str]]:
        if sys.platform != "win32":
            return []

        import json
        ps_cmd = 'Get-PnpDevice -PresentOnly -Class WPD,AndroidUSBDevice,USB | Select-Object FriendlyName, InstanceId, Class, Status | ConvertTo-Json -Depth 2'
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
                text=True,
                timeout=8
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                return []

            known_vendors = {
                "04E8": "Samsung", "18D1": "Google Pixel", "2717": "Xiaomi / Redmi / Poco",
                "22D9": "Oppo / Realme", "2A70": "OnePlus", "12D1": "Huawei / Honor",
                "22B8": "Motorola", "0BB4": "HTC", "0E8D": "MediaTek Device",
                "05C6": "Qualcomm Device", "1782": "Unisoc Device", "17EF": "Lenovo",
                "2E04": "Nokia / HMD", "2833": "Oculus", "19D2": "ZTE"
            }

            results: List[Dict[str, str]] = []
            data = json.loads(proc.stdout)
            items = data if isinstance(data, list) else [data]

            for it in items:
                name = str(it.get("FriendlyName") or "").strip()
                iid = str(it.get("InstanceId") or "").strip()
                cls = str(it.get("Class") or "").strip()
                status = str(it.get("Status") or "OK").strip()

                if not name or re.search(r"(?i)(mouse|keyboard|audio|generic hub|root hub|controller|webcam|bluetooth)", name):
                    continue

                m_vid = re.search(r"VID_([0-9A-Fa-f]{4})", iid)
                vid = m_vid.group(1).upper() if m_vid else ""
                vendor_name = known_vendors.get(vid, "")

                is_phone = False
                if cls in ["WPD", "AndroidUSBDevice"]:
                    is_phone = True
                elif vid in known_vendors:
                    is_phone = True
                elif re.search(r"(?i)(phone|android|galaxy|samsung|xiaomi|redmi|poco|oppo|vivo|realme|pixel|huawei|honor|oneplus|motorola|infinix|tecno|mtp)", name):
                    is_phone = True

                if is_phone:
                    display_name = name if name else (f"{vendor_name} USB Device" if vendor_name else "Android Phone")
                    results.append({
                        "name": display_name,
                        "class": cls,
                        "vendor": vendor_name,
                        "instance_id": iid,
                        "status": status
                    })

            return results
        except Exception as e:
            logger.debug(f"Error querying Windows PnP USB devices: {e}")
            return []

    def get_device_connection_diagnostics(self) -> Dict[str, Any]:
        all_adb = self.list_devices()
        authorized = [d for d in all_adb if d.get("state") == "device"]
        unauthorized = [d for d in all_adb if d.get("state") == "unauthorized"]
        offline = [d for d in all_adb if d.get("state") == "offline"]
        recovery = [d for d in all_adb if d.get("state") == "recovery"]
        sideload = [d for d in all_adb if d.get("state") == "sideload"]
        fastboot = [d for d in all_adb if d.get("state") == "fastboot"]

        pnp_phones = []
        if not authorized:
            pnp_phones = self.get_windows_pnp_usb_devices()

        return {
            "all_adb": all_adb,
            "authorized": authorized,
            "unauthorized": unauthorized,
            "offline": offline,
            "recovery": recovery,
            "sideload": sideload,
            "fastboot": fastboot,
            "pnp_phones": pnp_phones,
            "has_unauthorized": len(unauthorized) > 0,
            "has_offline": len(offline) > 0,
            "has_recovery": len(recovery) > 0,
            "has_sideload": len(sideload) > 0,
            "has_pnp_without_adb": len(pnp_phones) > 0 and len(all_adb) == 0
        }

    def is_device_ready(self, serial: Optional[str] = None) -> bool:
        code, out, _ = self.run_adb(["get-state"], serial=serial, timeout=5)
        return code == 0 and out.strip() == "device"

    def is_root_available(self, serial: Optional[str] = None) -> bool:
        code, out, _ = self.shell("su -c id", serial=serial, timeout=5)
        if code == 0 and "uid=0" in out:
            return True
        code2, out2, _ = self.shell("which su", serial=serial, timeout=5)
        return code2 == 0 and "/su" in out2

    def get_prop(self, prop_name: str, serial: Optional[str] = None) -> str:
        code, out, _ = self.shell(f"getprop {prop_name}", serial=serial, timeout=8)
        if code == 0:
            return out.strip()
        return ""

    def get_all_props(self, serial: Optional[str] = None) -> Dict[str, str]:
        code, out, _ = self.shell("getprop", serial=serial, timeout=15)
        props: Dict[str, str] = {}
        if code == 0 and out:
            matches = re.findall(r"\[(.*?)\]:\s*\[(.*?)\]", out)
            for k, v in matches:
                props[k.strip()] = v.strip()
        return props

    def read_sysfs(self, paths_list: List[str], serial: Optional[str] = None) -> Optional[str]:
        for path in paths_list:
            target = path if "*" in path else f"'{path}'"
            code, out, err = self.shell(f"cat {target}", serial=serial, timeout=5)
            clean_out = out.strip()
            if code == 0 and clean_out and "No such file" not in clean_out and "Permission denied" not in clean_out:
                return clean_out
        return None

    def read_sysfs_int(self, paths_list: List[str], serial: Optional[str] = None, default: Optional[int] = None) -> Optional[int]:
        val = self.read_sysfs(paths_list, serial=serial)
        if val is not None:
            clean_num = re.sub(r"[^\d\-]", "", val)
            if clean_num:
                try:
                    return int(clean_num)
                except ValueError:
                    pass
        return default

    def push(self, local_path: str, remote_path: str, serial: Optional[str] = None) -> Tuple[bool, str]:
        code, out, err = self.run_adb(["push", local_path, remote_path], serial=serial, timeout=60)
        return code == 0, out if code == 0 else err

    def pull(self, remote_path: str, local_path: str, serial: Optional[str] = None) -> Tuple[bool, str]:
        code, out, err = self.run_adb(["pull", remote_path, local_path], serial=serial, timeout=60)
        return code == 0, out if code == 0 else err

    def reboot(self, mode: str = "", serial: Optional[str] = None) -> Tuple[bool, str]:
        args = ["reboot"]
        clean_mode = mode.strip().lower()
        if clean_mode in ["recovery", "bootloader", "fastboot", "edl", "sideload"]:
            args.append(clean_mode)
        code, out, err = self.run_adb(args, serial=serial, timeout=20)
        return code == 0, out if code == 0 else err

    def fastboot_reboot(self, mode: str = "", serial: Optional[str] = None) -> Tuple[bool, str]:
        args = ["reboot"]
        clean_mode = mode.strip().lower()
        if clean_mode in ["recovery", "bootloader", "fastboot"]:
            args = ["reboot", clean_mode]
        code, out, err = self.run_fastboot(args, serial=serial, timeout=20)
        return code == 0, out if code == 0 else err

    def restart_server(self) -> Tuple[bool, str]:
        with self._cmd_lock:
            try:
                subprocess.run([self.adb_path, "kill-server"], creationflags=CREATE_NO_WINDOW, timeout=10)
                proc = subprocess.run([self.adb_path, "start-server"], stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW, text=True, timeout=15)
                return proc.returncode == 0, proc.stdout.strip() or "ADB server restarted."
            except Exception as e:
                logger.error(f"Error restarting ADB server: {e}")
                return False, str(e)

    def scan_bootrom_usb_ports(self) -> List[Dict[str, str]]:
        """
        Fast WMI / PnP query to detect low-level Emergency BootROM USB states
        (Qualcomm EDL 9008, MediaTek BROM/Preloader, Samsung Odin, and MTP mode).
        """
        if sys.platform != "win32":
            return []

        import json
        ps_cmd = 'Get-PnpDevice -PresentOnly | Where-Object { $_.InstanceId -match "^USB" } | Select-Object FriendlyName, InstanceId, Class, Status | ConvertTo-Json -Depth 2'
        try:
            proc = subprocess.run(
                ["powershell", "-NoProfile", "-Command", ps_cmd],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
                text=True,
                timeout=8
            )
            if proc.returncode != 0 or not proc.stdout.strip():
                return []

            data = json.loads(proc.stdout)
            items = data if isinstance(data, list) else [data]
            detected_ports = []

            for it in items:
                name = str(it.get("FriendlyName") or "").strip()
                iid = str(it.get("InstanceId") or "").upper()
                cls = str(it.get("Class") or "").strip()
                status = str(it.get("Status") or "OK").strip()

                # Qualcomm 9008 EDL Mode
                if "VID_05C6&PID_9008" in iid or "QDLOADER 9008" in name.upper():
                    detected_ports.append({
                        "mode": "Qualcomm EDL 9008",
                        "chipset": "Qualcomm Snapdragon SoC",
                        "status": status,
                        "name": name or "Qualcomm HS-USB QDLoader 9008",
                        "instance_id": iid,
                        "diagnosis": "Phone CPU is alive in Emergency Download Mode (EDL 9008). Indicates corrupted boot partition, dead boot, or damaged eMMC/UFS storage chip (Emergency unbricking required)."
                    })
                # Qualcomm Diagnostics
                elif "VID_05C6&PID_900E" in iid:
                    detected_ports.append({
                        "mode": "Qualcomm Diagnostics 900E",
                        "chipset": "Qualcomm Snapdragon SoC",
                        "status": status,
                        "name": name or "Qualcomm HS-USB Diagnostics 900E",
                        "instance_id": iid,
                        "diagnosis": "Phone in Qualcomm 900E diagnostic mode (Boot interrupted before kernel stage)."
                    })
                # MediaTek Preloader / BROM
                elif "VID_0E8D&PID_0003" in iid or "VID_0E8D&PID_2000" in iid or "PRELOADER" in name.upper() or "MTK USB" in name.upper():
                    detected_ports.append({
                        "mode": "MediaTek BROM / Preloader",
                        "chipset": "MediaTek Dimensity / Helio SoC",
                        "status": status,
                        "name": name or "MediaTek USB Port (BROM/Preloader)",
                        "instance_id": iid,
                        "diagnosis": "MediaTek SoC detected in BootROM / Preloader mode. Indicates bootloader corruption, storage IC communication failure, or power rail drop."
                    })
                # Samsung Odin / Download Mode
                elif "VID_04E8&PID_685D" in iid or "SAMSUNG MOBILE USB CDC COMPOSITE" in name.upper():
                    detected_ports.append({
                        "mode": "Samsung Odin Download Mode",
                        "chipset": "Samsung Exynos / Snapdragon",
                        "status": status,
                        "name": name or "Samsung Download / Odin Interface",
                        "instance_id": iid,
                        "diagnosis": "Samsung device detected in Odin / Download mode. Ready for firmware flashing or emergency kernel recovery."
                    })
                # Google Fastboot
                elif "VID_18D1&PID_D00D" in iid:
                    detected_ports.append({
                        "mode": "Android Fastboot Interface",
                        "chipset": "Universal Android",
                        "status": status,
                        "name": name or "Android Bootloader Interface",
                        "instance_id": iid,
                        "diagnosis": "Device in Fastboot bootloader mode."
                    })
                # MTP / Media Device (USB Debugging Disabled)
                elif cls in ["WPD", "PortableDevice"] or "MTP" in name.upper():
                    if re.search(r"(?i)(phone|galaxy|android|xiaomi|redmi|oppo|realme|pixel|huawei)", name):
                        detected_ports.append({
                            "mode": "MTP / Portable Device (ADB Disabled)",
                            "chipset": "Universal Android",
                            "status": status,
                            "name": name,
                            "instance_id": iid,
                            "diagnosis": "Device is recognized by Windows over USB in MTP mode, but USB Debugging is currently turned OFF on the phone."
                        })

            return detected_ports
        except Exception as e:
            logger.debug(f"Error scanning BootROM USB ports: {e}")
            return []

    def detect_usb_port_cycling(self, interval_seconds: int = 6) -> Dict[str, Any]:
        if sys.platform != "win32":
            return {"is_cycling": False, "details": "Not supported on this platform"}

        initial_devices = set(d.get("name") for d in self.get_windows_pnp_usb_devices())
        time.sleep(interval_seconds)
        second_devices = set(d.get("name") for d in self.get_windows_pnp_usb_devices())

        difference = initial_devices.symmetric_difference(second_devices)
        if difference:
            return {
                "is_cycling": True,
                "cycled_devices": list(difference),
                "diagnosis": "Power Management IC (PMIC) reset loop, shorted secondary power rail, or severely depleted battery (< 3.1V) causing rapid USB connection cycling."
            }
        return {
            "is_cycling": False,
            "cycled_devices": [],
            "diagnosis": "USB port connection is stable (no reboot cycling detected)."
        }

    def get_device_ip_address(self, serial: str) -> Optional[str]:
        """Auto-detect device Wi-Fi IP address via non-root commands."""
        # Method 1: ip route
        code, out, _ = self.shell("ip route", serial=serial, timeout=5)
        if code == 0 and out:
            m = re.search(r"\bsrc\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\b", out)
            if m:
                ip = m.group(1)
                if not ip.startswith("127."):
                    return ip

        # Method 2: ip -f inet addr show wlan0
        code, out, _ = self.shell("ip -f inet addr show wlan0 2>/dev/null", serial=serial, timeout=5)
        if code == 0 and out:
            m = re.search(r"inet\s+(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", out)
            if m:
                ip = m.group(1)
                if not ip.startswith("127."):
                    return ip

        # Method 3: getprop dhcp.wlan0.ipaddress
        prop_ip = self.get_prop("dhcp.wlan0.ipaddress", serial=serial)
        if prop_ip and re.match(r"^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$", prop_ip):
            return prop_ip

        # Method 4: ifconfig wlan0
        code, out, _ = self.shell("ifconfig wlan0 2>/dev/null", serial=serial, timeout=5)
        if code == 0 and out:
            m = re.search(r"inet\s+(?:addr:)?(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})", out)
            if m:
                return m.group(1)

        return None

    def enable_wireless_adb(self, serial: str, port: int = 5555) -> Tuple[bool, str, str]:
        """
        Switch target device to TCP/IP mode and connect wirelessly so USB cable can be unplugged
        for true battery discharge testing without USB charging interference.
        """
        logger.info(f"Enabling Wireless ADB on {serial} (port {port})...")
        ip = self.get_device_ip_address(serial)
        if not ip:
            return False, "", "Could not detect Wi-Fi IP address on device. Ensure phone is connected to Wi-Fi."

        code, out, err = self.run_adb(["tcpip", str(port)], serial=serial, timeout=10)
        if code != 0:
            return False, "", f"Failed to switch ADB to TCP/IP mode: {err or out}"

        target_addr = f"{ip}:{port}"
        time.sleep(1.0)
        code_conn, out_conn, err_conn = self.run_adb(["connect", target_addr], timeout=15)
        conn_output = f"{out_conn} {err_conn}"
        if "connected to" in conn_output.lower():
            logger.info(f"Wireless ADB connected to {target_addr}")
            return True, target_addr, f"Connected to {target_addr} wirelessly! You may now safely unplug the USB cable."
        else:
            return False, target_addr, f"TCP/IP enabled, but connect returned: {conn_output.strip()}"

    def pair_wireless_adb(self, host_port: str, pairing_code: str) -> Tuple[bool, str]:
        """
        Pair with Android 11+ Wireless Debugging using 6-digit pairing code.
        Format: host_port = '192.168.1.50:37123', pairing_code = '123456'
        """
        logger.info(f"Pairing with {host_port} using code {pairing_code}...")
        code, out, err = self.run_adb(["pair", host_port.strip(), pairing_code.strip()], timeout=20)
        msg = f"{out}\n{err}".strip()
        if code == 0 and "successfully paired" in msg.lower():
            return True, msg
        return False, msg or "Pairing failed. Verify IP, Port, and Pairing Code."

    def connect_wireless_adb(self, ip_port: str) -> Tuple[bool, str]:
        """Directly connect to an already-paired or TCP/IP device by IP:Port."""
        code, out, err = self.run_adb(["connect", ip_port.strip()], timeout=15)
        msg = f"{out}\n{err}".strip()
        if code == 0 and "connected to" in msg.lower():
            return True, msg
        return False, msg or "Connection failed."

    def disconnect_wireless_adb(self, ip_port: Optional[str] = None) -> Tuple[bool, str]:
        """Disconnect wireless device or all wireless devices."""
        args = ["disconnect"]
        if ip_port:
            args.append(ip_port.strip())
        code, out, err = self.run_adb(args, timeout=10)
        return code == 0, f"{out}\n{err}".strip()
