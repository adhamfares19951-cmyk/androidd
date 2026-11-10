import os
import re
import time
import logging
from dataclasses import dataclass, field, asdict
from typing import Dict, Any, Optional, List, Tuple
from core.adb_bridge import ADBBridge

from core.result import Msg, M

logger = logging.getLogger("BatchAPKManager")


@dataclass
class InstallResult:
    file_path: str
    file_name: str
    size_mb: float
    success: bool
    status_message: Any
    duration_sec: float

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class ExtractResult:
    package_name: str
    remote_path: str
    local_path: str
    size_mb: float
    success: bool
    error_message: Any

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class BatchAPKManager:
    """
    Handles bulk APK installation, automated runtime permission granting,
    and extraction of installed applications to the technician's workstation.
    """
    def __init__(self, bridge: Optional[ADBBridge] = None):
        self.bridge = bridge or ADBBridge()

    def install_single_apk(self, serial: str, apk_path: str, grant_permissions: bool = True) -> InstallResult:
        start_time = time.time()
        file_name = os.path.basename(apk_path)
        file_size_mb = round(os.path.getsize(apk_path) / (1024 * 1024), 2) if os.path.exists(apk_path) else 0.0

        if not os.path.exists(apk_path):
            return InstallResult(
                file_path=apk_path,
                file_name=file_name,
                size_mb=0.0,
                success=False,
                status_message=M("apk.file_missing"),
                duration_sec=0.0
            )

        # Build adb install command with flags:
        # -r: replace existing
        # -d: allow version downgrade
        # -g: grant all runtime permissions (Android 6.0+)
        flags = "-r -d"
        if grant_permissions:
            flags += " -g"

        code, stdout, stderr = self.bridge.run_adb(["install"] + flags.split() + [apk_path], serial=serial, timeout=120)

        duration = round(time.time() - start_time, 2)
        combined = (stdout + " " + stderr).strip()

        if "Success" in combined:
            return InstallResult(
                file_path=apk_path,
                file_name=file_name,
                size_mb=file_size_mb,
                success=True,
                status_message=M("apk.installed"),
                duration_sec=duration
            )
        else:
            # Extract common failure reasons
            fail_reason = "Unknown Error"
            m = re.search(r"INSTALL_FAILED_[A-Z_]+", combined)
            if m:
                fail_reason = m.group(0)
            elif "Failure [" in combined:
                m2 = re.search(r"Failure \[(.*?)\]", combined)
                if m2:
                    fail_reason = m2.group(1)
            else:
                fail_reason = combined[:80]

            return InstallResult(
                file_path=apk_path,
                file_name=file_name,
                size_mb=file_size_mb,
                success=False,
                status_message=M("apk.install_failed", reason=fail_reason),
                duration_sec=duration
            )

    def install_batch(self, serial: str, apk_paths: List[str], progress_callback=None) -> List[InstallResult]:
        results = []
        total = len(apk_paths)
        for i, path in enumerate(apk_paths):
            if progress_callback:
                progress_callback(i, total, os.path.basename(path))
            res = self.install_single_apk(serial, path)
            results.append(res)
        return results

    def list_installed_apps(self, serial: str, third_party_only: bool = True) -> List[Dict[str, str]]:
        flag = "-3" if third_party_only else ""
        cmd = f"pm list packages {flag} -f"
        code, out, _ = self.bridge.shell(cmd, serial=serial, timeout=15)
        apps = []
        if code == 0 and out:
            for line in out.splitlines():
                # Format: package:/data/app/.../base.apk=com.example.app
                line = line.strip()
                if line.startswith("package:"):
                    clean = line[8:]
                    if "=" in clean:
                        apk_path, pkg = clean.rsplit("=", 1)
                        apps.append({
                            "package": pkg.strip(),
                            "apk_path": apk_path.strip()
                        })
        apps.sort(key=lambda x: x["package"])
        return apps

    def extract_app(self, serial: str, package_name: str, output_dir: Optional[str] = None) -> ExtractResult:
        if not output_dir:
            user_profile = os.environ.get("USERPROFILE", "C:\\Users\\Default")
            output_dir = os.path.join(user_profile, "Desktop", "Android_Doctor_Extracted_APKs", serial)

        os.makedirs(output_dir, exist_ok=True)

        # 1. Find APK remote path
        code, out, _ = self.bridge.shell(f"pm path {package_name}", serial=serial, timeout=5)
        if code != 0 or not out.strip().startswith("package:"):
            return ExtractResult(
                package_name=package_name,
                remote_path="",
                local_path="",
                size_mb=0.0,
                success=False,
                error_message=M("apk.package_missing")
            )

        # If split APKs exist, take base.apk
        lines = out.strip().splitlines()
        base_line = next((l for l in lines if "base.apk" in l), lines[0])
        remote_apk = base_line.replace("package:", "").strip()

        local_apk = os.path.join(output_dir, f"{package_name}.apk")
        ok, msg = self.bridge.pull(remote_apk, local_apk, serial=serial, timeout=60)

        if ok and os.path.exists(local_apk):
            size_mb = round(os.path.getsize(local_apk) / (1024 * 1024), 2)
            return ExtractResult(
                package_name=package_name,
                remote_path=remote_apk,
                local_path=local_apk,
                size_mb=size_mb,
                success=True,
                error_message=""
            )
        else:
            return ExtractResult(
                package_name=package_name,
                remote_path=remote_apk,
                local_path="",
                size_mb=0.0,
                success=False,
                error_message=M("apk.extract_failed", output=msg)
            )
