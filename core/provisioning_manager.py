import shlex
import re
import logging
from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any, Tuple
from core.adb_bridge import ADBBridge

from core.result import Msg, M

logger = logging.getLogger("ProvisioningManager")


@dataclass
class ProvisioningStatus:
    is_device_provisioned: bool = False
    is_user_setup_complete: bool = False
    raw_provisioned_val: str = "0"
    raw_setup_complete_val: str = "0"
    status_summary: Any = None
    # Informational system properties, shown as read; "UNKNOWN" when empty or unreadable.
    pst_block_path: str = "UNKNOWN"         # ro.frp.pst: the FRP (factory reset protection) block
    oem_unlock_allowed: str = "UNKNOWN"     # sys.oem_unlock_allowed: "OEM unlocking" switched on (1) or off (0)
    oem_unlock_supported: str = "UNKNOWN"   # ro.oem_unlock_supported: bootloader unlock supported; not "allowed"

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
            status.status_summary = M("prov.complete")
        elif status.is_device_provisioned:
            status.status_summary = M("prov.user_pending")
        else:
            status.status_summary = M("prov.incomplete")

        # Each field reads exactly one property: no fallback to a property with another meaning
        # (ro.boot.cid is a carrier id, not the FRP path; "supported" is not "allowed").
        status.pst_block_path = self._prop(serial, "ro.frp.pst")
        status.oem_unlock_allowed = self._prop(serial, "sys.oem_unlock_allowed")
        status.oem_unlock_supported = self._prop(serial, "ro.oem_unlock_supported")
        return status

    def _prop(self, serial: str, name: str) -> str:
        code, out, _ = self.bridge.shell(f"getprop {name}", serial=serial, timeout=8)
        value = (out or "").strip().splitlines()[0].strip() if (out or "").strip() else ""
        return value if code in (0, None) and value else "UNKNOWN"

    def complete_setup_wizard_flags(self, serial: str) -> Tuple[bool, str]:
        logger.info(f"Completing setup wizard flags on device: {serial}")
        results = []

        code1, out1, err1 = self.bridge.shell("settings put global device_provisioned 1", serial=serial, timeout=8)
        results.append(f"device_provisioned=1: exit={code1} {err1 or out1}".strip())

        code2, out2, err2 = self.bridge.shell("settings put secure user_setup_complete 1", serial=serial, timeout=8)
        results.append(f"user_setup_complete=1: exit={code2} {err2 or out2}".strip())

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
        results.append(f"am start HOME: exit={code_home}")

        success = (code1 == 0 and code2 == 0)
        return success, M("prov.flags_result", output=" | ".join(results))

    def launch_settings_activity(self, serial: str) -> Tuple[bool, str]:
        logger.info(f"Launching Android Settings activity on device: {serial}")
        code, out, err = self.bridge.shell("am start -a android.settings.SETTINGS", serial=serial, timeout=8)
        if code == 0:
            return True, M("prov.settings_opened")
        return False, M("prov.settings_failed", output=err or out)

    def launch_browser_url(self, url: str, serial: str) -> Tuple[bool, str]:
        clean_url = url.strip()
        if not clean_url.startswith("http://") and not clean_url.startswith("https://"):
            clean_url = "https://" + clean_url
        logger.info(f"Opening URL {clean_url} on device: {serial}")
        code, out, err = self.bridge.shell(f"am start -a android.intent.action.VIEW -d {shlex.quote(clean_url)}",
                                       serial=serial, timeout=8)
        # `am start` can exit 0 and still print "Error: Activity not started" (no app for the link).
        error_lines = [l.strip() for l in f"{out}\n{err}".splitlines() if l.strip().startswith(("Error", "Exception"))]
        if code == 0 and not error_lines:
            return True, M("prov.url_opened", url=clean_url)
        return False, M("prov.url_failed", output="\n".join(error_lines) or (err or out).strip())
