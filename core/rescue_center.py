import os
import re
import sys
import subprocess
import shlex
import threading
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Optional, Tuple, Any, Callable
from setup_bins import get_scrcpy_path, CREATE_NO_WINDOW, tool_env
from app_paths import safe_name
from core.adb_bridge import ADBBridge
from core.result import Msg, M

logger = logging.getLogger("RescueCenter")

# WhatsApp: media only. Chat databases are encrypted (msgstore.db.crypt14/15) and useless without the key.
WHATSAPP_MEDIA_PATHS = (
    "/sdcard/Android/media/com.whatsapp/WhatsApp/Media",
    "/sdcard/WhatsApp/Media",
)
WHATSAPP_NOTE = M("rescue.whatsapp_note")


@dataclass
class FastbootVariables:
    serial: str
    product: str = "Unknown"
    bootloader_version: str = "Unknown"
    baseband_version: str = "Unknown"
    is_secure: bool = True
    is_unlocked: bool = False
    current_slot: str = "a"
    battery_voltage: str = "Unknown"
    cpu_abi: str = "Unknown"
    raw_vars: Dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RescueCenter:
    def __init__(self, bridge: Optional[ADBBridge] = None):
        self.bridge = bridge or ADBBridge()
        self.scrcpy_path = get_scrcpy_path()
        self._scrcpy_process: Optional[subprocess.Popen] = None
        self._proc_lock = threading.Lock()

    def launch_screen_mirror(
        self,
        serial: Optional[str] = None,
        turn_screen_off: bool = True,
        stay_awake: bool = True,
        max_size: int = 1024,
        max_fps: int = 60
    ) -> Tuple[bool, str]:
        with self._proc_lock:
            if self._scrcpy_process is not None and self._scrcpy_process.poll() is None:
                return True, M("scrcpy.already_running")

            if not os.path.exists(self.scrcpy_path):
                return False, M("scrcpy.not_found", path=self.scrcpy_path)

            cmd = [self.scrcpy_path]
            if serial:
                cmd.extend(["-s", serial])
            if turn_screen_off:
                cmd.append("--turn-screen-off")
            if stay_awake:
                cmd.append("--stay-awake")
            if max_size:
                cmd.extend(["--max-size", str(max_size)])
            if max_fps:
                cmd.extend(["--max-fps", str(max_fps)])
            cmd.extend(["--window-title", "Android Doctor Pro - Screen Mirror"])

            try:
                logger.info(f"Launching scrcpy: {' '.join(cmd)}")
                # scrcpy uses our adb (ADB=...) on our private server port; output is discarded so
                # an unread pipe can never block it
                self._scrcpy_process = subprocess.Popen(
                    cmd,
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=tool_env()
                )
                return True, M("scrcpy.started")
            except Exception as e:
                logger.error(f"Failed to launch scrcpy: {e}")
                return False, str(e)

    def launch_otg_mode(self, serial: Optional[str] = None) -> Tuple[bool, str]:
        """
        Launch scrcpy in physical USB HID mode (--otg).
        Simulates physical USB mouse and keyboard via Android Open Accessory (AOA).
        Works completely WITHOUT ADB and WITHOUT USB Debugging!
        Allows controlling devices with broken touch or forgotten PIN/Pattern.
        """
        with self._proc_lock:
            if not os.path.exists(self.scrcpy_path):
                return False, M("scrcpy.not_found", path=self.scrcpy_path)

            cmd = [self.scrcpy_path, "--otg"]
            if serial:
                cmd.extend(["-s", serial])
            cmd.extend(["--window-title", "Android Doctor Pro - OTG HID Physical Control (No ADB)"])

            try:
                logger.info(f"Launching scrcpy in OTG HID mode: {' '.join(cmd)}")
                # scrcpy uses our adb (ADB=...) on our private server port; output is discarded so
                # an unread pipe can never block it
                self._scrcpy_process = subprocess.Popen(
                    cmd,
                    creationflags=CREATE_NO_WINDOW,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    env=tool_env()
                )
                return True, M("scrcpy.otg_started")
            except Exception as e:
                logger.error(f"Failed to launch scrcpy OTG mode: {e}")
                return False, str(e)

    def send_keyevent(self, key_code: int, serial: Optional[str] = None) -> bool:
        """Send hardware keyevent to device (26=Power, 24=Vol+, 25=Vol-, 4=Back, 3=Home)."""
        code, _, _ = self.bridge.shell(f"input keyevent {key_code}", serial=serial, timeout=5)
        return code == 0

    def send_text(self, text: str, serial: Optional[str] = None) -> bool:
        """Send keystrokes / PIN to device screen."""
        # `input text` reads a space as %s; shlex.quote keeps quotes, ; $ ` and the like literal.
        clean = shlex.quote(text.replace(" ", "%s"))
        code, _, _ = self.bridge.shell(f"input text {clean}", serial=serial, timeout=5)
        return code == 0

    def send_swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300, serial: Optional[str] = None) -> bool:
        """Send touch swipe gesture to screen (e.g. swipe up to reveal PIN pad)."""
        code, _, _ = self.bridge.shell(f"input swipe {x1} {y1} {x2} {y2} {duration_ms}", serial=serial, timeout=5)
        return code == 0

    def get_fastboot_variables(self, serial: Optional[str] = None) -> FastbootVariables:
        """Alias for read_fastboot_variables."""
        return self.read_fastboot_variables(serial)

    def stop_screen_mirror(self) -> Tuple[bool, str]:
        with self._proc_lock:
            if self._scrcpy_process is not None and self._scrcpy_process.poll() is None:
                try:
                    self._scrcpy_process.terminate()
                    self._scrcpy_process.wait(timeout=3)
                    self._scrcpy_process = None
                    return True, M("scrcpy.stopped")
                except Exception as e:
                    try:
                        self._scrcpy_process.kill()
                        self._scrcpy_process = None
                        return True, M("scrcpy.killed")
                    except Exception as ke:
                        return False, M("scrcpy.stop_failed", error=str(ke))
            return True, M("scrcpy.not_running")

    def is_screen_mirror_running(self) -> bool:
        with self._proc_lock:
            return self._scrcpy_process is not None and self._scrcpy_process.poll() is None

    def read_fastboot_variables(self, serial: Optional[str] = None) -> FastbootVariables:
        code, out, err = self.bridge.run_fastboot(["getvar", "all"], serial=serial, timeout=15)
        text = f"{out}\n{err}"
        
        fb_vars = FastbootVariables(serial=serial or "Unknown")
        raw_dict: Dict[str, str] = {}

        for line in text.splitlines():
            line_clean = line.strip()
            if "(bootloader)" in line_clean:
                line_clean = line_clean.replace("(bootloader)", "").strip()
            if ":" in line_clean:
                parts = line_clean.split(":", 1)
                k = parts[0].strip()
                v = parts[1].strip()
                raw_dict[k] = v

                k_lower = k.lower()
                if "product" in k_lower:
                    fb_vars.product = v
                elif "version-bootloader" in k_lower:
                    fb_vars.bootloader_version = v
                elif "version-baseband" in k_lower:
                    fb_vars.baseband_version = v
                elif "secure" in k_lower:
                    fb_vars.is_secure = "yes" in v.lower() or "true" in v.lower()
                elif "unlocked" in k_lower:
                    fb_vars.is_unlocked = "yes" in v.lower() or "true" in v.lower()
                elif "current-slot" in k_lower:
                    fb_vars.current_slot = v
                elif "battery-voltage" in k_lower:
                    fb_vars.battery_voltage = f"{v} mV"
                elif "cpu-abi" in k_lower:
                    fb_vars.cpu_abi = v

        fb_vars.raw_vars = raw_dict
        return fb_vars

    def has_cache_partition(self, serial: Optional[str] = None) -> Tuple[Optional[bool], str]:
        """
        Ask the bootloader whether a `cache` partition exists (`fastboot getvar partition-type:cache`).
        Returns (True/False, evidence text), or (None, evidence) when the answer could not be read.
        A/B devices (most phones since 2017) have no cache partition.
        """
        code, out, err = self.bridge.run_fastboot(["getvar", "partition-type:cache"], serial=serial, timeout=10)
        text = f"{out}\n{err}".strip()
        evidence = f"fastboot getvar partition-type:cache -> exit={code}: {text[:300]}"
        m = re.search(r"partition-type:cache:\s*(\S+)", text)
        if m and m.group(1).lower() not in ("", "none"):
            return True, evidence
        if re.search(r"(?i)(FAILED|not found|unknown variable|no such partition)", text):
            return False, evidence
        return None, evidence

    def fix_cache_bootloop(self, serial: Optional[str] = None) -> Tuple[bool, str]:
        """
        Erase the cache partition, only if the bootloader reports one.
        This can clear a corrupted cache that blocks booting; it is not a guaranteed fix.
        """
        has_cache, evidence = self.has_cache_partition(serial)
        if has_cache is not True:
            return False, M("cache.none_confirmed", evidence=evidence)
        logger.info(f"Erasing cache partition on {serial}...")
        code, out, err = self.bridge.run_fastboot(["erase", "cache"], serial=serial, timeout=30)
        text = (out or err or "").strip()
        return code == 0, M("cache.erase_result", evidence=evidence, exit=code, output=text[:300])

    def flash_partition(self, partition: str, file_path: str, serial: Optional[str] = None) -> Tuple[bool, str]:
        if not os.path.exists(file_path):
            return False, M("flash.file_missing", path=file_path)

        clean_partition = partition.strip().lower()
        if clean_partition not in ["boot", "recovery", "system", "vendor", "vbmeta", "dtbo", "cache", "userdata"]:
            return False, M("flash.refused", partition=partition)

        logger.info(f"Flashing partition '{clean_partition}' with {file_path} on {serial}...")
        code, out, err = self.bridge.run_fastboot(["flash", clean_partition, file_path], serial=serial, timeout=120)
        output = f"{out}\n{err}".strip()
        return code == 0, output

    def emergency_data_pull(
        self,
        serial: str,
        target_category: str,
        local_dest_dir: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> Tuple[bool, str]:
        """
        Pull user data (Photos, DCIM, Pictures, WhatsApp media, Downloads, Full Storage)
        to the PC workstation. Handles both single directories and combined bundles.
        WhatsApp: only the Media folder is pulled. Chat databases (msgstore.db.crypt*) are
        encrypted with a key that stays on the phone / in the user's backup, so they are not copied.
        """
        cat_lower = target_category.lower().strip()

        # Define paths per category
        bundles: Dict[str, List[str]] = {
            "all_photos": ["/sdcard/DCIM", "/sdcard/Pictures"],
            "photos": ["/sdcard/DCIM", "/sdcard/Pictures"],
            "camera_only": ["/sdcard/DCIM"],
            "whatsapp": list(WHATSAPP_MEDIA_PATHS),
            "downloads": ["/sdcard/Download"],
            "documents": ["/sdcard/Documents"],
            "full_storage": [
                "/sdcard/DCIM",
                "/sdcard/Pictures",
                "/sdcard/Download",
                "/sdcard/Documents",
                "/sdcard/Movies",
                "/sdcard/Music",
            ] + list(WHATSAPP_MEDIA_PATHS)
        }

        # Resolve paths
        paths_to_pull = bundles.get(cat_lower)
        if not paths_to_pull:
            # the UI passes a category id (first word of the combo entry); only English ids are matched
            if "photo" in cat_lower:
                paths_to_pull = bundles["all_photos"]
            elif "what" in cat_lower:
                paths_to_pull = bundles["whatsapp"]
            elif "full" in cat_lower:
                paths_to_pull = bundles["full_storage"]
            else:
                paths_to_pull = ["/sdcard/DCIM", "/sdcard/Pictures"]

        os.makedirs(local_dest_dir, exist_ok=True)
        total_paths = len(paths_to_pull)
        successes = 0
        logs = []

        for idx, remote_path in enumerate(paths_to_pull, start=1):
            # unique local folder per remote path (both WhatsApp paths end in "Media")
            folder_name = "_".join(remote_path.strip("/").split("/")[1:]) or os.path.basename(remote_path.rstrip("/"))
            sub_dest = os.path.join(local_dest_dir, folder_name)
            os.makedirs(sub_dest, exist_ok=True)

            if progress_callback:
                progress_callback(M("rescue.progress.pulling", idx=idx, total=total_paths, path=remote_path))

            ok, out = self.bridge.pull(remote_path, sub_dest, serial=serial)
            if ok:
                successes += 1
                logs.append(M("rescue.pulled", name=folder_name))
            else:
                logs.append(M("rescue.skipped", name=folder_name, output=out.strip() or "-"))

        if any(p in WHATSAPP_MEDIA_PATHS for p in paths_to_pull):
            logs.append(WHATSAPP_NOTE)
        all_ok = successes > 0
        final_summary = logs
        if progress_callback:
            progress_callback(M("rescue.progress.done", path=local_dest_dir))

        return all_ok, final_summary

    def switch_active_slot(self, target_slot: str, serial: Optional[str] = None) -> Tuple[bool, str]:
        """
        Switch active boot slot on A/B partitioned Android devices (Slot A <=> Slot B).
        Essential for recovering devices stuck in bootloop after corrupted OTA updates.
        """
        slot = target_slot.strip().lower()
        if slot not in ["a", "b", "_a", "_b"]:
            return False, M("slot.invalid", slot=target_slot)

        slot_clean = slot.lstrip("_")
        logger.info(f"Switching active slot to '{slot_clean}' on {serial or 'default device'}...")

        # Try fastboot --set-active=<slot>
        code, out, err = self.bridge.run_fastboot(["--set-active=" + slot_clean], serial=serial, timeout=15)
        combined = f"{out}\n{err}".strip()
        if code == 0 and "error" not in combined.lower():
            return True, M("slot.changed", slot=slot_clean.upper(), output=combined)

        # Fallback syntax for older fastboot implementations: fastboot set_active <slot>
        code2, out2, err2 = self.bridge.run_fastboot(["set_active", slot_clean], serial=serial, timeout=15)
        combined2 = f"{out2}\n{err2}".strip()
        if code2 == 0 and "error" not in combined2.lower():
            return True, M("slot.changed", slot=slot_clean.upper(), output=combined2)

        return False, M("slot.failed", output=combined2 or combined or "-")

    def check_anti_rollback(self, serial: Optional[str] = None) -> Tuple[bool, str, int]:
        """
        Inspect Anti-Rollback (ARB) hardware fuse version via fastboot.
        Prevents hard-bricking when flashing or downgrading firmware.
        """
        import re
        logger.info(f"Querying Anti-Rollback Index on {serial or 'default device'}...")
        code, out, err = self.bridge.run_fastboot(["getvar", "anti"], serial=serial, timeout=10)
        combined = f"{out}\n{err}"

        arb_index = -1
        m = re.search(r"anti:\s*(\d+)", combined, re.IGNORECASE)
        if m:
            arb_index = int(m.group(1))
        else:
            code2, out2, err2 = self.bridge.run_fastboot(["getvar", "rollback-ver"], serial=serial, timeout=10)
            m2 = re.search(r"rollback-ver:\s*(\d+)", f"{out2}\n{err2}", re.IGNORECASE)
            if m2:
                arb_index = int(m2.group(1))

        if arb_index >= 0:
            return True, M("arb.index", index=arb_index), arb_index
        else:
            return False, M("arb.unknown", output=combined.strip() or "-"), -1

    def sideload_firmware(
        self,
        file_path: str,
        serial: Optional[str] = None,
        progress_callback: Optional[Callable[[int, str], None]] = None
    ) -> Tuple[bool, str]:
        """
        Stream an official OTA/ROM .zip file via `adb sideload <file_path>` when the device
        is in Recovery / Sideload mode, parsing stdout for real-time percentage progress.
        """
        import re
        if not os.path.exists(file_path):
            return False, M("sideload.file_missing", path=file_path)

        if not file_path.lower().endswith(".zip"):
            return False, M("sideload.not_zip")

        cmd = [self.bridge.adb_path]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(["sideload", file_path])

        logger.info(f"Starting ADB Sideload: {' '.join(cmd)}")
        if progress_callback:
            progress_callback(0, M("sideload.progress.start", name=os.path.basename(file_path)))

        try:
            process = subprocess.Popen(
                cmd,
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                env=tool_env(),
                bufsize=1
            )

            last_pct = 0
            full_logs = []

            for line in iter(process.stdout.readline, ''):
                if not line:
                    break
                line_clean = line.strip()
                full_logs.append(line_clean)

                m = re.search(r"\(~(\d+)%\)", line_clean)
                if m:
                    pct = int(m.group(1))
                    last_pct = pct
                    if progress_callback:
                        progress_callback(pct, M("sideload.progress.pct", pct=pct))
                elif "Total xfer:" in line_clean or "100%" in line_clean:
                    if progress_callback:
                        progress_callback(100, M("sideload.progress.transferred"))

            process.stdout.close()
            return_code = process.wait()

            success = (return_code == 0)
            log_summary = "\n".join(full_logs[-15:])
            if success:
                msg = M("sideload.done", log=log_summary)
                if progress_callback:
                    progress_callback(100, M("sideload.progress.finished"))
                return True, msg
            else:
                msg = M("sideload.failed", code=return_code, log=log_summary)
                return False, msg

        except Exception as e:
            logger.error(f"Sideload error: {e}")
            return False, str(e)

    def one_click_customer_backup(
        self,
        serial: str,
        progress_callback: Optional[Callable[[str], None]] = None
    ) -> Tuple[bool, str, str]:
        """
        One-Click complete customer data preservation:
        Pulls DCIM (Camera), Pictures, Downloads, Documents, WhatsApp media (not the encrypted chat databases),
        and dumps contacts/SMS tables if accessible.
        Saves everything to ~/Desktop/Android_Customer_Backups/<Serial>_<Timestamp>/
        """
        import re
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        clean_serial = safe_name(serial or "Device")
        backup_dir = os.path.join(
            os.path.expanduser("~"),
            "Desktop",
            "Android_Customer_Backups",
            f"{clean_serial}_{timestamp}"
        )
        os.makedirs(backup_dir, exist_ok=True)

        if progress_callback:
            progress_callback(M("backup.progress.start", path=backup_dir))

        log_lines = []

        # 1. Pull Media folders
        media_targets = [
            ("Camera_DCIM", "/sdcard/DCIM"),
            ("Pictures", "/sdcard/Pictures"),
            ("Downloads", "/sdcard/Download"),
            ("Documents", "/sdcard/Documents"),
            ("WhatsApp_Media", WHATSAPP_MEDIA_PATHS[0]),
            ("WhatsApp_Media_Legacy", WHATSAPP_MEDIA_PATHS[1])
        ]

        total_pulled = 0
        for name, remote_p in media_targets:
            local_target = os.path.join(backup_dir, name)
            if progress_callback:
                progress_callback(M("backup.progress.item", name=name, path=remote_p))
            ok, out = self.bridge.pull(remote_p, local_target, serial=serial)
            if ok:
                total_pulled += 1
                log_lines.append(M("rescue.pulled", name=name))
            else:
                log_lines.append(M("rescue.skipped", name=name, output=out.strip() or "-"))

        log_lines.append(WHATSAPP_NOTE)

        # 2. Extract Contacts where permitted
        if progress_callback:
            progress_callback(M("backup.progress.contacts"))
        contacts_code, contacts_out, _ = self.bridge.shell(
            "content query --uri content://contacts/phones/ --projection display_name:number 2>/dev/null",
            serial=serial,
            timeout=8
        )
        if contacts_code == 0 and contacts_out and "Row:" in contacts_out:
            contacts_file = os.path.join(backup_dir, "contacts_dump.csv")
            try:
                with open(contacts_file, "w", encoding="utf-8") as f:
                    f.write("Name,Number\n")
                    for line in contacts_out.splitlines():
                        m_name = re.search(r"display_name=([^,]+)", line)
                        m_num = re.search(r"number=([^\s,]+)", line)
                        name_val = m_name.group(1).strip() if m_name else "Unknown"
                        num_val = m_num.group(1).strip() if m_num else ""
                        if num_val:
                            f.write(f'"{name_val}","{num_val}"\n')
                log_lines.append(M("backup.contacts_ok", file="contacts_dump.csv"))
            except Exception as e:
                log_lines.append(M("backup.parse_error", item="contacts", error=str(e)))
        else:
            log_lines.append(M("backup.contacts_blocked"))

        # 3. Extract SMS where permitted
        sms_code, sms_out, _ = self.bridge.shell(
            "content query --uri content://sms/inbox --projection address:body:date 2>/dev/null",
            serial=serial,
            timeout=8
        )
        if sms_code == 0 and sms_out and "Row:" in sms_out:
            sms_file = os.path.join(backup_dir, "sms_inbox_dump.csv")
            try:
                with open(sms_file, "w", encoding="utf-8") as f:
                    f.write("Address,Date,Body\n")
                    for line in sms_out.splitlines():
                        m_addr = re.search(r"address=([^,]+)", line)
                        m_body = re.search(r"body=([^,]+)", line)
                        addr_val = m_addr.group(1).strip() if m_addr else ""
                        body_val = m_body.group(1).strip() if m_body else ""
                        if addr_val or body_val:
                            f.write(f'"{addr_val}","","{body_val}"\n')
                log_lines.append(M("backup.sms_ok", file="sms_inbox_dump.csv"))
            except Exception as e:
                log_lines.append(M("backup.parse_error", item="sms", error=str(e)))

        summary = log_lines
        if progress_callback:
            progress_callback(M("backup.progress.done", path=backup_dir))

        return True, summary, backup_dir
