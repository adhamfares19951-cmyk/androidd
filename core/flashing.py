"""
Legitimate official firmware flashing module (core.flashing).

Calls standalone binaries in bin/ (GPLv3-safe subprocess boundary):
- adb sideload: signed recovery/OTA .zip (works on locked bootloaders)
- fastboot flash: unlocked bootloaders only, with model/codename, lock-state,
  and Anti-Rollback (ARB) safety valves
- SP Flash Tool / mtkclient (BROM): official MediaTek scatter firmware
- Odin / Heimdall: official Samsung .tar / .tar.md5 packages (BL/AP/CP/CSC)

Strictly NO theft-protection bypass: refuses FRP, persistent, devinfo, seccfg,
auth-bypass flags, or any non-official unlock/bypass arguments.
"""

import logging
import os
import re
import subprocess
import sys
import zipfile
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Tuple

from core.result import Evidence, M, Msg, ProducesCheckResult, Status
from setup_bins import (
    ADB_EXE_NAME,
    ADB_SERVER_PORT,
    BIN_DIR,
    CREATE_NO_WINDOW,
    FASTBOOT_EXE_NAME,
    tool_env,
)

logger = logging.getLogger("Flashing")

SubprocessRunner = Callable[[List[str], int], Tuple[int, str, str]]

# Partitions associated with theft-protection, device identity, or lock state.
FORBIDDEN_PARTITIONS = frozenset({
    "frp",
    "persistent",
    "devinfo",
    "config",
    "seccfg",
    "proinfo",
    "nvram",
    "nvdata",
    "protect1",
    "protect2",
    "efuse",
    "misc_frp",
})

# Standard official Android partitions allowed via fastboot flash.
SAFE_FASTBOOT_PARTITIONS = frozenset({
    "boot",
    "init_boot",
    "vendor_boot",
    "recovery",
    "system",
    "vendor",
    "product",
    "system_ext",
    "odm",
    "vbmeta",
    "vbmeta_system",
    "vbmeta_vendor",
    "dtbo",
    "super",
    "cache",
    "userdata",
    "modem",
    "dsp",
    "bluetooth",
    "abl",
    "xbl",
})

# Allowed Samsung Odin slots.
ODIN_SLOTS = frozenset({"BL", "AP", "CP", "CSC"})

# Arguments or patterns strictly refused to prevent auth/FRP bypass misuse.
_FORBIDDEN_ARG_RE = re.compile(
    r"(?:auth[-_]?bypass|frp[-_]?bypass|bypass[-_]?auth|erase[-_]?frp|"
    r"\bfrp\b|\bseccfg\b|\bpersistent\b|format[-_]?all)",
    re.IGNORECASE,
)


def default_subprocess_runner(cmd: List[str], timeout: int = 300) -> Tuple[int, str, str]:
    """Run an external binary in bin/ without importing GPL code into Python."""
    res = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
        env=tool_env(),
        creationflags=CREATE_NO_WINDOW,
    )
    return res.returncode, res.stdout or "", res.stderr or ""


@dataclass
class FlashResult(ProducesCheckResult):
    check_id = "flash.operation"
    _check_detail_attr = "detail"

    mode: str
    status: Status
    detail: Msg
    flashed_partitions: List[str] = field(default_factory=list)
    safety_checks: Dict[str, Any] = field(default_factory=dict)
    evidence: List[Evidence] = field(default_factory=list)
    possible_causes: List[Any] = field(default_factory=list)

    def _check_value(self):
        return self.mode, ""


def _parse_fastboot_vars(text: str) -> Dict[str, str]:
    vars_dict: Dict[str, str] = {}
    for line in (text or "").splitlines():
        clean = line.replace("(bootloader)", "").strip()
        if ":" in clean:
            k, v = clean.split(":", 1)
            vars_dict[k.strip().lower()] = v.strip()
    return vars_dict


def _extract_ota_pre_device(zip_path: str) -> Optional[str]:
    """Best-effort read of pre-device from META-INF/com/android/metadata."""
    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for name in zf.namelist():
                if name.rstrip("/").endswith("META-INF/com/android/metadata"):
                    raw = zf.read(name).decode("utf-8", errors="replace")
                    for line in raw.splitlines():
                        if line.startswith("pre-device="):
                            return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return None


class OfficialFlasher:
    """Official-firmware-only flasher invoking standalone binaries in bin/."""

    def __init__(
        self,
        bridge: Optional[Any] = None,
        runner: Optional[SubprocessRunner] = None,
        bin_dir: str = BIN_DIR,
    ):
        self.bridge = bridge
        self.runner = runner or default_subprocess_runner
        self.bin_dir = bin_dir

    def _resolve_bin(self, *names: str) -> str:
        for name in names:
            cand = os.path.join(self.bin_dir, name)
            if os.path.isfile(cand):
                return cand
        return os.path.join(self.bin_dir, names[0])

    def _run_fastboot_cmd(
        self, args: List[str], serial: Optional[str] = None, timeout: int = 120
    ) -> Tuple[int, str, str]:
        if self.bridge is not None and hasattr(self.bridge, "run_fastboot"):
            return self.bridge.run_fastboot(args, serial=serial, timeout=timeout)
        fb_exe = self._resolve_bin(FASTBOOT_EXE_NAME)
        cmd = [fb_exe]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(args)
        return self.runner(cmd, timeout)

    # ------------------------------------------------------------------ 1. ADB Sideload
    def adb_sideload(
        self,
        zip_path: str,
        serial: Optional[str] = None,
        expected_device: Optional[str] = None,
        timeout: int = 600,
    ) -> FlashResult:
        """Stream a signed recovery/OTA .zip package via `adb sideload`."""
        if not zip_path or not os.path.isfile(zip_path):
            return FlashResult(
                mode="adb_sideload",
                status=Status.FAIL,
                detail=M("sideload.file_missing", path=zip_path or ""),
            )
        if not zip_path.lower().endswith(".zip"):
            return FlashResult(
                mode="adb_sideload",
                status=Status.FAIL,
                detail=M("sideload.not_zip"),
            )

        pre_device = _extract_ota_pre_device(zip_path)
        safety: Dict[str, Any] = {"ota_pre_device": pre_device, "expected_device": expected_device}
        if expected_device and pre_device and expected_device.lower() != pre_device.lower():
            return FlashResult(
                mode="adb_sideload",
                status=Status.FAIL,
                detail=M("flash.safety.model_mismatch", expected=expected_device, actual=pre_device),
                safety_checks=safety,
            )

        adb_exe = self._resolve_bin(ADB_EXE_NAME)
        cmd = [adb_exe, "-P", str(ADB_SERVER_PORT)]
        if serial:
            cmd.extend(["-s", serial])
        cmd.extend(["sideload", zip_path])

        try:
            code, out, err = self.runner(cmd, timeout)
        except Exception as e:
            return FlashResult(
                mode="adb_sideload",
                status=Status.FAIL,
                detail=M("flash.exec_error", error=str(e)),
                safety_checks=safety,
                evidence=[Evidence(source="adb:sideload", raw_excerpt=str(e))],
            )

        combined = f"{out}\n{err}".strip()
        ev = [Evidence(source="adb:sideload", raw_excerpt=combined[:1500])]
        if code == 0:
            return FlashResult(
                mode="adb_sideload",
                status=Status.PASS,
                detail=M("sideload.done", log=combined[:300] or "OK"),
                safety_checks=safety,
                evidence=ev,
            )
        return FlashResult(
            mode="adb_sideload",
            status=Status.FAIL,
            detail=M("sideload.failed", code=code, log=combined[:300] or "-"),
            safety_checks=safety,
            evidence=ev,
        )

    # ------------------------------------------------------------------ 2. Fastboot Flash
    def check_fastboot_safety(
        self,
        serial: Optional[str] = None,
        expected_product: Optional[str] = None,
        package_arb: Optional[int] = None,
    ) -> Tuple[Optional[bool], Dict[str, Any], Optional[Msg], List[Evidence]]:
        """Verify bootloader unlock, model/codename match, and Anti-Rollback (ARB)."""
        code, out, err = self._run_fastboot_cmd(["getvar", "all"], serial=serial, timeout=15)
        combined = f"{out}\n{err}".strip()
        ev = [Evidence(source="fastboot:getvar", raw_excerpt=combined[:1500])]
        fb = _parse_fastboot_vars(combined)

        product = fb.get("product", "")
        unlocked_raw = fb.get("unlocked", "")
        arb_raw = fb.get("anti", fb.get("rollback-ver", ""))
        device_arb = int(arb_raw) if arb_raw.isdigit() else -1

        safety: Dict[str, Any] = {
            "product": product,
            "unlocked": unlocked_raw,
            "device_arb": device_arb,
            "package_arb": package_arb,
            "expected_product": expected_product,
        }

        if not unlocked_raw:
            return None, safety, M("flash.safety.unlock_unknown"), ev
        if unlocked_raw.lower() not in ("yes", "true", "1"):
            return False, safety, M("flash.safety.locked"), ev

        if expected_product:
            if not product or product.lower() != expected_product.lower():
                return (
                    False,
                    safety,
                    M("flash.safety.model_mismatch", expected=expected_product, actual=product or "unknown"),
                    ev,
                )

        if package_arb is not None and device_arb >= 0 and device_arb > package_arb:
            return (
                False,
                safety,
                M("flash.safety.arb_violation", device=device_arb, package=package_arb),
                ev,
            )

        return True, safety, None, ev

    def fastboot_flash(
        self,
        images: Dict[str, str],
        serial: Optional[str] = None,
        expected_product: Optional[str] = None,
        package_arb: Optional[int] = None,
        timeout_per_part: int = 180,
    ) -> FlashResult:
        """Flash official partition images via fastboot after passing all safety valves."""
        if not images:
            return FlashResult(
                mode="fastboot",
                status=Status.FAIL,
                detail=M("flash.no_images"),
            )

        # 1. Partition allowlist & forbidden-partition guard
        for part, path in images.items():
            clean = part.strip().lower()
            if clean in FORBIDDEN_PARTITIONS or clean not in SAFE_FASTBOOT_PARTITIONS:
                return FlashResult(
                    mode="fastboot",
                    status=Status.FAIL,
                    detail=M("flash.refused", partition=part),
                )
            if not path or not os.path.isfile(path):
                return FlashResult(
                    mode="fastboot",
                    status=Status.FAIL,
                    detail=M("flash.file_missing", path=path or ""),
                )

        # 2. Bootloader, codename, and ARB safety valves
        safe_ok, safety, err_msg, ev = self.check_fastboot_safety(
            serial=serial,
            expected_product=expected_product,
            package_arb=package_arb,
        )
        if safe_ok is not True:
            return FlashResult(
                mode="fastboot",
                status=Status.UNKNOWN if safe_ok is None else Status.FAIL,
                detail=err_msg or M("flash.safety.unlock_unknown"),
                safety_checks=safety,
                evidence=ev,
            )

        # 3. Flash partitions sequentially
        flashed: List[str] = []
        for part, path in images.items():
            clean = part.strip().lower()
            try:
                code, out, err = self._run_fastboot_cmd(
                    ["flash", clean, path], serial=serial, timeout=timeout_per_part
                )
            except Exception as e:
                ev.append(Evidence(source=f"fastboot:flash:{clean}", raw_excerpt=str(e)))
                return FlashResult(
                    mode="fastboot",
                    status=Status.FAIL,
                    detail=M("flash.exec_error", error=str(e)),
                    flashed_partitions=flashed,
                    safety_checks=safety,
                    evidence=ev,
                )

            combined = f"{out}\n{err}".strip()
            ev.append(Evidence(source=f"fastboot:flash:{clean}", raw_excerpt=combined[:800]))
            if code != 0:
                return FlashResult(
                    mode="fastboot",
                    status=Status.FAIL,
                    detail=M("flash.partition_failed", partition=clean, code=code),
                    flashed_partitions=flashed,
                    safety_checks=safety,
                    evidence=ev,
                )
            flashed.append(clean)

        return FlashResult(
            mode="fastboot",
            status=Status.PASS,
            detail=M("flash.fastboot_ok", count=len(flashed)),
            flashed_partitions=flashed,
            safety_checks=safety,
            evidence=ev,
        )

    # ------------------------------------------------------------------ 3. MTK BROM (SP Flash Tool / mtkclient)
    def mtk_brom_flash(
        self,
        scatter_path: str,
        tool: str = "spflashtool",
        partitions: Optional[List[str]] = None,
        extra_args: Optional[List[str]] = None,
        timeout: int = 600,
    ) -> FlashResult:
        """Invoke standalone SP Flash Tool or mtkclient in bin/ for official scatter firmware."""
        # Guard against any forbidden partition or bypass flag
        for part in (partitions or []):
            if part.strip().lower() in FORBIDDEN_PARTITIONS:
                return FlashResult(
                    mode="mtk_brom",
                    status=Status.FAIL,
                    detail=M("flash.refused", partition=part),
                )
        for arg in (extra_args or []):
            if _FORBIDDEN_ARG_RE.search(arg):
                return FlashResult(
                    mode="mtk_brom",
                    status=Status.FAIL,
                    detail=M("flash.safety.forbidden_arg", arg=arg),
                )

        if not scatter_path or not os.path.isfile(scatter_path):
            return FlashResult(
                mode="mtk_brom",
                status=Status.FAIL,
                detail=M("flash.file_missing", path=scatter_path or ""),
            )

        is_win = sys.platform == "win32"
        if tool.lower() == "mtkclient":
            exe_name = "mtk.exe" if is_win else "mtk"
        else:
            exe_name = "flash_tool.exe" if is_win else "flash_tool"

        exe_path = os.path.join(self.bin_dir, exe_name)
        if not os.path.isfile(exe_path):
            return FlashResult(
                mode="mtk_brom",
                status=Status.UNKNOWN,
                detail=M("flash.tool_missing", tool=exe_name, path=exe_path),
            )

        if tool.lower() == "mtkclient":
            # Official partition write only (requires explicit safe partitions)
            if not partitions:
                return FlashResult(
                    mode="mtk_brom",
                    status=Status.FAIL,
                    detail=M("flash.no_images"),
                )
            cmd = [exe_path, "wl", scatter_path]
        else:
            cmd = [exe_path, "-s", scatter_path, "-c", "download"]

        if extra_args:
            cmd.extend(extra_args)

        try:
            code, out, err = self.runner(cmd, timeout)
        except Exception as e:
            return FlashResult(
                mode="mtk_brom",
                status=Status.FAIL,
                detail=M("flash.exec_error", error=str(e)),
                evidence=[Evidence(source="mtk_brom", raw_excerpt=str(e))],
            )

        combined = f"{out}\n{err}".strip()
        ev = [Evidence(source="mtk_brom", raw_excerpt=combined[:1500])]
        if code == 0:
            return FlashResult(
                mode="mtk_brom",
                status=Status.PASS,
                detail=M("flash.mtk_ok"),
                flashed_partitions=list(partitions or []),
                evidence=ev,
            )
        return FlashResult(
            mode="mtk_brom",
            status=Status.FAIL,
            detail=M("flash.tool_failed", tool=exe_name, code=code),
            evidence=ev,
        )

    # ------------------------------------------------------------------ 4. Samsung Odin / Heimdall
    def odin_flash(
        self,
        packages: Dict[str, str],
        timeout: int = 600,
    ) -> FlashResult:
        """Invoke standalone Odin CLI in bin/ for official Samsung .tar / .tar.md5 packages."""
        if not packages:
            return FlashResult(
                mode="odin",
                status=Status.FAIL,
                detail=M("flash.no_images"),
            )

        validated: Dict[str, str] = {}
        for slot, path in packages.items():
            slot_up = slot.strip().upper()
            if slot_up not in ODIN_SLOTS:
                return FlashResult(
                    mode="odin",
                    status=Status.FAIL,
                    detail=M("flash.refused", partition=slot),
                )
            if not path or not os.path.isfile(path):
                return FlashResult(
                    mode="odin",
                    status=Status.FAIL,
                    detail=M("flash.file_missing", path=path or ""),
                )
            lower_p = path.lower()
            if not (lower_p.endswith(".tar") or lower_p.endswith(".tar.md5")):
                return FlashResult(
                    mode="odin",
                    status=Status.FAIL,
                    detail=M("flash.odin_bad_ext", path=path),
                )
            validated[slot_up] = path

        is_win = sys.platform == "win32"
        exe_path = self._resolve_bin(
            "odin4.exe" if is_win else "odin4",
            "odin3.exe" if is_win else "odin3",
        )
        if not os.path.isfile(exe_path):
            return FlashResult(
                mode="odin",
                status=Status.UNKNOWN,
                detail=M("flash.tool_missing", tool=os.path.basename(exe_path), path=exe_path),
            )

        flag_map = {"BL": "-b", "AP": "-a", "CP": "-c", "CSC": "-s"}
        cmd = [exe_path]
        for slot_up in ("BL", "AP", "CP", "CSC"):
            if slot_up in validated:
                cmd.extend([flag_map[slot_up], validated[slot_up]])

        try:
            code, out, err = self.runner(cmd, timeout)
        except Exception as e:
            return FlashResult(
                mode="odin",
                status=Status.FAIL,
                detail=M("flash.exec_error", error=str(e)),
                evidence=[Evidence(source="odin", raw_excerpt=str(e))],
            )

        combined = f"{out}\n{err}".strip()
        ev = [Evidence(source="odin", raw_excerpt=combined[:1500])]
        if code == 0:
            return FlashResult(
                mode="odin",
                status=Status.PASS,
                detail=M("flash.odin_ok", slots=", ".join(validated.keys())),
                flashed_partitions=list(validated.keys()),
                evidence=ev,
            )
        return FlashResult(
            mode="odin",
            status=Status.FAIL,
            detail=M("flash.tool_failed", tool=os.path.basename(exe_path), code=code),
            evidence=ev,
        )
