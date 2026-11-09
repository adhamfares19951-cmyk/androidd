import re
import logging
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, Tuple
from core.adb_bridge import ADBBridge

logger = logging.getLogger("ProvisioningManager")


@dataclass
class ProvisioningStatus:
    is_device_provisioned: bool = False
    is_user_setup_complete: bool = False
    raw_provisioned_val: str = "0"
    raw_setup_complete_val: str = "0"
    status_summary: str = "Setup Incomplete"

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class ProvisioningManager:
    def __init__(self, bridge: Optional[ADBBridge] = None):
        self.bridge = bridge or ADBBridge()

    def get_provisioning_status(self, serial: str) -> ProvisioningStatus:
        status = ProvisioningStatus()
        code1, out1, _ = self.bridge.shell("settings get global device_provisioned", serial=serial, timeout=8)
        code2, out2, _ = self.bridge.shell("settings get secure user_setup_complete", serial=serial, timeout=8)

        v1 = out1.strip()
        v2 = out2.strip()
        status.raw_provisioned_val = v1
        status.raw_setup_complete_val = v2

        status.is_device_provisioned = (v1 == "1")
        status.is_user_setup_complete = (v2 == "1")

        if status.is_device_provisioned and status.is_user_setup_complete:
            status.status_summary = "Fully Provisioned & Setup Completed"
        elif status.is_device_provisioned:
            status.status_summary = "Device Provisioned (User Setup Pending)"
        else:
            status.status_summary = "Setup Wizard Active / Incomplete"

        return status

    def complete_setup_wizard_flags(self, serial: str) -> Tuple[bool, str]:
        logger.info(f"Completing setup wizard flags on device: {serial}")
        results = []

        code1, out1, err1 = self.bridge.shell("settings put global device_provisioned 1", serial=serial, timeout=8)
        results.append(f"device_provisioned=1: {'OK' if code1 == 0 else err1 or out1}")

        code2, out2, err2 = self.bridge.shell("settings put secure user_setup_complete 1", serial=serial, timeout=8)
        results.append(f"user_setup_complete=1: {'OK' if code2 == 0 else err2 or out2}")

        self.bridge.shell(
            "content insert --uri content://settings/secure --bind name:s:user_setup_complete --bind value:i:1 2>/dev/null",
            serial=serial,
            timeout=8
        )

        code_home, _, _ = self.bridge.shell(
            "am start -a android.intent.action.MAIN -c android.intent.category.HOME",
            serial=serial,
            timeout=8
        )
        results.append(f"Launch Home Activity: {'Dispatched' if code_home == 0 else 'Failed'}")

        success = (code1 == 0 and code2 == 0)
        return success, " | ".join(results)

    def launch_settings_activity(self, serial: str) -> Tuple[bool, str]:
        logger.info(f"Launching Android Settings activity on device: {serial}")
        code, out, err = self.bridge.shell("am start -a android.settings.SETTINGS", serial=serial, timeout=8)
        if code == 0:
            return True, "Settings activity launched successfully on device screen."
        return False, f"Failed to launch Settings: {err or out}"

    def launch_browser_url(self, url: str, serial: str) -> Tuple[bool, str]:
        clean_url = url.strip()
        if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            clean_url = "https://" + clean_url
        logger.info(f"Opening URL {clean_url} on device: {serial}")
        code, out, err = self.bridge.shell(f"am start -a android.intent.action.VIEW -d '{clean_url}'", serial=serial, timeout=8)
        if code == 0:
            return True, f"Browser URL launched: {clean_url}"
        return False, f"Failed to open URL: {err or out}"
