import os
import sys
import subprocess
import threading
import logging
import re
import time
from typing import List, Dict, Tuple, Optional, Callable, Any
from setup_bins import get_adb_path, get_fastboot_path, get_scrcpy_path, CREATE_NO_WINDOW, ADB_SERVER_PORT, tool_env

from core.result import Msg, M

logger = logging.getLogger("ADBBridge")


# Our adb server listens on a private port (setup_bins.ADB_SERVER_PORT = 5038), never the default 5037.

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


_SU_RC_MARKER = "__ADP_SU_RC:"


def _strip_su_rc_marker(res: Tuple[Optional[int], str, str]) -> Tuple[Optional[int], str, str]:
    """Remove the su exit-status marker line from stdout. When the transport gave no exit
    code (legacy shell without shell_v2) the marker's value becomes the exit code."""
    code, out, err = res
    lines = out.splitlines()
    for i in range(len(lines) - 1, -1, -1):
        line = lines[i].strip()
        if line.startswith(_SU_RC_MARKER):
            value = line[len(_SU_RC_MARKER):]
            del lines[i]
            out = "\n".join(lines)
            if code is None and value.isdigit():
                code = int(value)
            elif code == 0 and value.isdigit() and int(value) != 0:
                code = int(value)
            break
    return code, out, err


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
        self._cmd_lock = threading.Lock()  # server-wide calls (no serial): devices, start/kill-server
        self._serial_locks: Dict[str, threading.Lock] = {}
        self._serial_locks_guard = threading.Lock()
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

    def _lock_for(self, serial: Optional[str]) -> threading.Lock:
        """One lock per device, so commands to different phones run in parallel while commands to
        the same phone stay in order. Calls without a serial share the server-wide lock."""
        if not serial:
            return self._cmd_lock
        with self._serial_locks_guard:
            lock = self._serial_locks.get(serial)
            if lock is None:
                lock = self._serial_locks[serial] = threading.Lock()
            return lock

    def run_adb(self, args: List[str], serial: Optional[str] = None, timeout: int = 30) -> Tuple[int, str, str]:
        cmd = [self.adb_path]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(args)

        with self._lock_for(serial):
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=CREATE_NO_WINDOW,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=tool_env(),
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
            # The marker echoes su's exit status so it is known even without shell_v2.
            wrapped_cmd = f'su -c "{escaped_cmd}"; echo "{_SU_RC_MARKER}$?"'
            res = _strip_su_rc_marker(self._shell_once(wrapped_cmd, serial, timeout, use_socket))
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
                env=tool_env(),
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

        with self._lock_for(serial):
            try:
                proc = subprocess.run(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    creationflags=CREATE_NO_WINDOW,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env=tool_env(),
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
                        info["model"] = M("device.unauthorized")
                    elif state == "offline" and not info["model"]:
                        info["model"] = M("device.offline")
                    elif state == "recovery" and not info["model"]:
                        info["model"] = M("device.recovery")
                    elif state == "sideload" and not info["model"]:
                        info["model"] = M("device.sideload")
                    elif state == "device" and not info["model"]:
                        info["model"] = M("device.generic")
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
                        "model": M("device.fastboot"),
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
        """True only when `su -c id` really ran as uid 0. A present but denied su is not root."""
        code, out, _ = self.shell(f'su -c id; echo "{_SU_RC_MARKER}$?"', serial=serial, timeout=5)
        code, out, _ = _strip_su_rc_marker((code, out, ""))
        return code == 0 and "uid=0(" in out

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

    def push(self, local_path: str, remote_path: str, serial: Optional[str] = None, timeout: int = 60) -> Tuple[bool, str]:
        code, out, err = self.run_adb(["push", local_path, remote_path], serial=serial, timeout=timeout)
        return code == 0, out if code == 0 else err

    def pull(self, remote_path: str, local_path: str, serial: Optional[str] = None, timeout: int = 60) -> Tuple[bool, str]:
        code, out, err = self.run_adb(["pull", remote_path, local_path], serial=serial, timeout=timeout)
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
                # only our own server (private port); other tools' adb servers are left alone
                subprocess.run([self.adb_path, "-P", str(ADB_SERVER_PORT), "kill-server"], env=tool_env(),
                               creationflags=CREATE_NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
                proc = subprocess.run([self.adb_path, "-P", str(ADB_SERVER_PORT), "start-server"], stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, creationflags=CREATE_NO_WINDOW, text=True, env=tool_env(), timeout=15)
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
            present: Dict[Tuple[str, str], Dict[str, Any]] = {}

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
                        "diagnosis": M("port.edl")
                    })
                # Qualcomm Diagnostics
                elif "VID_05C6&PID_900E" in iid:
                    detected_ports.append({
                        "mode": "Qualcomm Diagnostics 900E",
                        "chipset": "Qualcomm Snapdragon SoC",
                        "status": status,
                        "name": name or "Qualcomm HS-USB Diagnostics 900E",
                        "instance_id": iid,
                        "diagnosis": M("port.qcom_900e")
                    })
                # MediaTek Preloader / BROM
                elif "VID_0E8D&PID_0003" in iid or "VID_0E8D&PID_2000" in iid or "PRELOADER" in name.upper() or "MTK USB" in name.upper():
                    detected_ports.append({
                        "mode": "MediaTek BROM / Preloader",
                        "chipset": "MediaTek Dimensity / Helio SoC",
                        "status": status,
                        "name": name or "MediaTek USB Port (BROM/Preloader)",
                        "instance_id": iid,
                        "diagnosis": M("port.mtk_brom")
                    })
                # Samsung Odin / Download Mode
                elif "VID_04E8&PID_685D" in iid or "SAMSUNG MOBILE USB CDC COMPOSITE" in name.upper():
                    detected_ports.append({
                        "mode": "Samsung Odin Download Mode",
                        "chipset": "Samsung Exynos / Snapdragon",
                        "status": status,
                        "name": name or "Samsung Download / Odin Interface",
                        "instance_id": iid,
                        "diagnosis": M("port.odin")
                    })
                # Google Fastboot
                elif "VID_18D1&PID_D00D" in iid:
                    detected_ports.append({
                        "mode": "Android Fastboot Interface",
                        "chipset": "Universal Android",
                        "status": status,
                        "name": name or "Android Bootloader Interface",
                        "instance_id": iid,
                        "diagnosis": M("port.fastboot")
                    })
                # MTP / Media Device (USB Debugging Disabled)
                elif (cls in ["WPD", "PortableDevice"] or "MTP" in name.upper()) and \
                        re.search(r"(?i)(phone|galaxy|android|xiaomi|redmi|oppo|realme|pixel|huawei)", name):
                    detected_ports.append({
                        "mode": "MTP / Portable Device (ADB Disabled)",
                        "chipset": "Universal Android",
                        "status": status,
                        "name": name,
                        "instance_id": iid,
                        "diagnosis": M("port.mtp_no_adb")
                    })
                else:
                    self._note_phone_vendor_device(present, iid, name, cls, status)

            # Any other device of a phone/SoC vendor: shown as present, mode not identified. A switched-off
            # phone on USB (seen: MediaTek 0E8D:20FF with the phone's serial) proves the board is powered and
            # USB data works; saying "nothing detected" would hide that. The mode is never guessed.
            claimed = {self._vid_pid(p["instance_id"]) for p in detected_ports}
            for key, dev in present.items():
                if key in claimed:
                    continue
                detected_ports.append({
                    "mode": "Phone on USB (mode not identified)",
                    "chipset": dev["vendor"],
                    "vendor": dev["vendor"], "vid": key[0], "pid": key[1], "serial": dev["serial"] or "UNKNOWN",
                    "status": dev["status"],
                    "name": dev["name"],
                    "instance_id": dev["instance_id"],
                    "diagnosis": M("port.phone_present", vendor=dev["vendor"], vid=key[0], pid=key[1],
                                   serial=dev["serial"] or "UNKNOWN", device_class=dev["class"] or "-"),
                })
            return detected_ports
        except Exception as e:
            logger.debug(f"Error scanning BootROM USB ports: {e}")
            return []

    # USB vendor ids of phone makers and phone SoC makers (others, e.g. keyboards, are not reported).
    PHONE_USB_VENDORS = {
        "0E8D": "MediaTek", "05C6": "Qualcomm", "04E8": "Samsung", "2717": "Xiaomi", "18D1": "Google/Android",
        "22B8": "Motorola", "12D1": "Huawei", "22D9": "OPPO", "2A70": "OnePlus", "0BB4": "HTC", "1004": "LG",
        "0FCE": "Sony", "19D2": "ZTE", "2D95": "vivo", "1782": "Unisoc (Spreadtrum)",
    }
    _USB_IID_RE = re.compile(r"VID_([0-9A-F]{4})&PID_([0-9A-F]{4})(&MI_[0-9A-F]+)?\\(.+)$")

    @classmethod
    def _vid_pid(cls, instance_id: str) -> Tuple[str, str]:
        m = cls._USB_IID_RE.search(str(instance_id).upper())
        return (m.group(1), m.group(2)) if m else ("", "")

    def _note_phone_vendor_device(self, present: Dict[Tuple[str, str], Dict[str, Any]], iid: str, name: str,
                                  cls: str, status: str) -> None:
        """Remember a present device of a phone vendor, one entry per VID:PID (composite interfaces merged;
        the serial comes from the parent device, interface ids like 6&1DC9924&0&0000 are not serials)."""
        m = self._USB_IID_RE.search(iid)
        if not m or m.group(1) not in self.PHONE_USB_VENDORS or status.upper() not in ("OK", ""):
            return
        key = (m.group(1), m.group(2))
        dev = present.setdefault(key, {"vendor": self.PHONE_USB_VENDORS[m.group(1)], "serial": None, "name": name,
                                       "class": cls, "status": status, "instance_id": iid})
        if not m.group(3):                     # the parent device: its last part is the serial ...
            serial = m.group(4) if "&" not in m.group(4) else None   # ... unless Windows made one up (5&25CD7C4&0&5)
            dev.update(serial=serial, name=name or dev["name"], **{"class": cls or dev["class"]}, instance_id=iid)

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
                "diagnosis": M("port.cycling")
            }
        return {
            "is_cycling": False,
            "cycled_devices": [],
            "diagnosis": M("port.stable")
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
            return False, "", M("wireless.no_ip")

        code, out, err = self.run_adb(["tcpip", str(port)], serial=serial, timeout=10)
        if code != 0:
            return False, "", M("wireless.tcpip_failed", output=err or out)

        target_addr = f"{ip}:{port}"
        time.sleep(1.0)
        code_conn, out_conn, err_conn = self.run_adb(["connect", target_addr], timeout=15)
        conn_output = f"{out_conn} {err_conn}"
        if "connected to" in conn_output.lower():
            logger.info(f"Wireless ADB connected to {target_addr}")
            return True, target_addr, M("wireless.enabled", addr=target_addr)
        else:
            return False, target_addr, M("wireless.connect_failed", output=conn_output.strip())

    def pair_wireless_adb(self, host_port: str, pairing_code: str) -> Tuple[bool, str]:
        """
        Pair with Android 11+ Wireless Debugging using 6-digit pairing code.
        Format: host_port = '192.168.1.50:37123', pairing_code = '123456'
        """
        logger.info(f"Pairing with {host_port}...")  # the pairing code is a secret: never logged
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
