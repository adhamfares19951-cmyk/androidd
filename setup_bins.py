"""
Bundled tools (adb, fastboot, scrcpy) in bin/.

- Nothing is downloaded at startup. The binaries ship in bin/ and are checked against
  bin/manifest.json (pinned versions + SHA-256 of every file).
- "Repair tools" (repair_tools) is a manual action: it downloads the pinned archive for this OS,
  checks the archive's SHA-256 BEFORE extracting, extracts safely, then checks the extracted files.
- Our adb server runs on a private port (ADB_SERVER_PORT = 5038) so it never touches another tool's
  adb server on the default port 5037. Stopping it only stops ours: `adb -P 5038 kill-server`.
"""

import hashlib
import json
import logging
import os
import platform
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger("SetupBins")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
MANIFEST_PATH = os.path.join(BIN_DIR, "manifest.json")
INSTALLED_PATH = os.path.join(BIN_DIR, "installed.json")

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

ADB_SERVER_PORT = 5038

ADB_EXE_NAME = "adb.exe" if IS_WINDOWS else "adb"
FASTBOOT_EXE_NAME = "fastboot.exe" if IS_WINDOWS else "fastboot"
SCRCPY_EXE_NAME = "scrcpy.exe" if IS_WINDOWS else "scrcpy"

ADB_PATH = os.path.join(BIN_DIR, ADB_EXE_NAME)
FASTBOOT_PATH = os.path.join(BIN_DIR, FASTBOOT_EXE_NAME)
SCRCPY_PATH = os.path.join(BIN_DIR, SCRCPY_EXE_NAME)

ProgressCallback = Optional[Callable[[str, int], None]]  # (message_key, percent)


def get_adb_path() -> str:
    return ADB_PATH


def get_fastboot_path() -> str:
    return FASTBOOT_PATH


def get_scrcpy_path() -> str:
    return SCRCPY_PATH


def tool_env() -> Dict[str, str]:
    """Environment for adb / scrcpy child processes: our private server port and our adb binary."""
    env = dict(os.environ)
    env["ANDROID_ADB_SERVER_PORT"] = str(ADB_SERVER_PORT)
    env["ADB"] = ADB_PATH
    return env


def platform_key() -> str:
    machine = platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
    if IS_WINDOWS:
        return f"windows-{arch}"
    if sys.platform == "darwin":
        return f"darwin-{arch}"
    return f"linux-{arch}"


def load_manifest(path: str = MANIFEST_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _expected_files(pkg: Dict[str, Any], key: str, pkg_name: str) -> Dict[str, str]:
    files = pkg.get("files", {}).get(key)
    if files:
        return files
    # Other platforms: hashes recorded after a verified repair
    try:
        with open(INSTALLED_PATH, "r", encoding="utf-8") as f:
            installed = json.load(f)
        entry = installed.get(pkg_name, {})
        if entry.get("version") == pkg.get("version") and entry.get("platform") == key:
            return entry.get("files", {})
    except (OSError, ValueError):
        pass
    return {}


def verify_binaries(check_hashes: bool = True, manifest_path: str = MANIFEST_PATH, bin_dir: str = BIN_DIR) -> Dict[str, Any]:
    """
    Fast startup check (no process is run). Returns
    {"ok": bool, "platform": key, "packages": {name: {"version", "ok", "missing": [...], "mismatched": [...]}},
     "adb": {...}, "fastboot": {...}, "scrcpy": {...}}  (per-tool "available"/"path" kept for older callers)
    """
    key = platform_key()
    result: Dict[str, Any] = {"ok": True, "platform": key, "packages": {}}
    try:
        manifest = load_manifest(manifest_path)
    except (OSError, ValueError) as e:
        result["ok"] = False
        result["error"] = f"manifest unreadable: {e}"
        manifest = {"packages": {}}

    for name, pkg in manifest.get("packages", {}).items():
        expected = _expected_files(pkg, key, name)
        info = {"version": pkg.get("version"), "ok": True, "missing": [], "mismatched": [], "unverified": not expected}
        for rel, digest in expected.items():
            path = os.path.join(bin_dir, rel)
            if not os.path.isfile(path):
                info["missing"].append(rel)
            elif check_hashes and sha256_file(path) != digest:
                info["mismatched"].append(rel)
        if not expected:
            exe = {"platform-tools": [ADB_EXE_NAME, FASTBOOT_EXE_NAME], "scrcpy": [SCRCPY_EXE_NAME]}.get(name, [])
            info["missing"] = [e for e in exe if not os.path.isfile(os.path.join(bin_dir, e))]
        info["ok"] = not info["missing"] and not info["mismatched"]
        result["ok"] = result["ok"] and info["ok"]
        result["packages"][name] = info

    for tool, path in (("adb", ADB_PATH), ("fastboot", FASTBOOT_PATH), ("scrcpy", SCRCPY_PATH)):
        pkg_name = "scrcpy" if tool == "scrcpy" else "platform-tools"
        pkg_ok = result["packages"].get(pkg_name, {}).get("ok", False)
        result[tool] = {"available": os.path.isfile(path) and pkg_ok, "path": path,
                        "version": result["packages"].get(pkg_name, {}).get("version") or ""}
    return result


# ----------------------------------------------------------------------------------------------
# Repair (manual, background)
# ----------------------------------------------------------------------------------------------
def stop_adb_server() -> bool:
    """Stop OUR adb server only (private port). Never kills other tools' adb processes."""
    if not os.path.isfile(ADB_PATH):
        return False
    try:
        subprocess.run([ADB_PATH, "-P", str(ADB_SERVER_PORT), "kill-server"], env=tool_env(),
                       creationflags=CREATE_NO_WINDOW, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10)
        return True
    except Exception as e:
        logger.warning(f"Could not stop the adb server on port {ADB_SERVER_PORT}: {e}")
        return False


def _download(url: str, dest: str, progress: ProgressCallback = None) -> None:
    req = urllib.request.Request(url, headers={"User-Agent": "AndroidDoctorPro-RepairTools"})
    with urllib.request.urlopen(req, timeout=60) as resp, open(dest, "wb") as out:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        while True:
            block = resp.read(1 << 16)
            if not block:
                break
            out.write(block)
            done += len(block)
            if progress and total:
                progress("tools.progress.downloading", int(done * 100 / total))


def _safe_members(names: List[str], strip_prefix: str, exclude: List[str]) -> Dict[str, str]:
    """archive member -> relative destination path; rejects absolute paths and '..'."""
    out = {}
    for name in names:
        norm = name.replace("\\", "/")
        if norm.endswith("/"):
            continue
        if not norm.startswith(strip_prefix):
            continue
        rel = norm[len(strip_prefix):]
        if not rel or rel.startswith("/") or ".." in rel.split("/") or ":" in rel:
            raise ValueError(f"unsafe path in archive: {name}")
        if rel in exclude:
            continue
        out[name] = rel
    return out


def _extract(archive: str, kind: str, strip_prefix: str, exclude: List[str], dest_dir: str) -> List[str]:
    written = []
    if kind == "zip":
        with zipfile.ZipFile(archive) as z:
            members = _safe_members(z.namelist(), strip_prefix, exclude)
            for name, rel in members.items():
                target = os.path.join(dest_dir, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with z.open(name) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                written.append(rel)
    else:
        with tarfile.open(archive, "r:gz") as tf:
            regular = [m for m in tf.getmembers() if m.isfile()]
            members = _safe_members([m.name for m in regular], strip_prefix, exclude)
            for m in regular:
                if m.name not in members:
                    continue
                rel = members[m.name]
                target = os.path.join(dest_dir, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                with tf.extractfile(m) as src, open(target, "wb") as dst:
                    shutil.copyfileobj(src, dst)
                if m.mode & 0o111:
                    os.chmod(target, 0o755)
                written.append(rel)
    return written


def repair_tools(progress: ProgressCallback = None, packages: Optional[List[str]] = None,
                 archive_dir: Optional[str] = None, manifest_path: str = MANIFEST_PATH,
                 bin_dir: str = BIN_DIR) -> Dict[str, Any]:
    """
    Re-install pinned packages for this OS. Each archive is checked against the manifest SHA-256 before
    anything is extracted; the extracted files are then checked again. Returns {name: {"ok", "error"}}.
    archive_dir: use already-downloaded archives from this folder (still hash-checked).
    """
    manifest = load_manifest(manifest_path)
    key = platform_key()
    results: Dict[str, Any] = {}
    names = packages or list(manifest.get("packages", {}))
    for name in names:
        pkg = manifest["packages"][name]
        dl = pkg.get("downloads", {}).get(key)
        if not dl:
            results[name] = {"ok": False, "error": f"no pinned download for {key}"}
            continue
        work = tempfile.mkdtemp(prefix="adp_repair_")
        try:
            filename = dl["url"].rsplit("/", 1)[-1]
            archive = os.path.join(work, filename)
            cached = os.path.join(archive_dir, filename) if archive_dir else None
            if cached and os.path.isfile(cached):
                shutil.copy2(cached, archive)
            else:
                if progress:
                    progress("tools.progress.downloading", 0)
                _download(dl["url"], archive, progress)
            if sha256_file(archive) != dl["sha256"]:
                results[name] = {"ok": False, "error": "archive SHA-256 does not match the manifest; nothing was extracted"}
                continue
            if progress:
                progress("tools.progress.extracting", 100)
            staging = os.path.join(work, "staging")
            os.makedirs(staging)
            written = _extract(archive, dl.get("type", "zip"), dl.get("strip_prefix", ""), dl.get("exclude", []), staging)
            expected = pkg.get("files", {}).get(key)
            if expected:
                bad = [rel for rel, digest in expected.items()
                       if not os.path.isfile(os.path.join(staging, rel)) or sha256_file(os.path.join(staging, rel)) != digest]
                if bad:
                    results[name] = {"ok": False, "error": f"extracted files do not match the manifest: {bad[:5]}"}
                    continue
            if name == "platform-tools":
                stop_adb_server()  # release the file lock on our own adb.exe
            os.makedirs(bin_dir, exist_ok=True)
            for rel in written:
                target = os.path.join(bin_dir, rel)
                os.makedirs(os.path.dirname(target), exist_ok=True)
                shutil.copy2(os.path.join(staging, rel), target)
            if not expected:
                _record_installed(name, pkg.get("version"), key, {rel: sha256_file(os.path.join(bin_dir, rel)) for rel in written})
            results[name] = {"ok": True, "error": ""}
        except Exception as e:
            logger.error(f"Repair of {name} failed: {e}")
            results[name] = {"ok": False, "error": str(e)}
        finally:
            shutil.rmtree(work, ignore_errors=True)
    return results


def _record_installed(name: str, version: str, key: str, files: Dict[str, str]) -> None:
    try:
        with open(INSTALLED_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        data = {}
    data[name] = {"version": version, "platform": key, "files": files}
    with open(INSTALLED_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)


def ensure_all_binaries(progress: ProgressCallback = None) -> Dict[str, Any]:
    """Compatibility wrapper: repair only what verification reports as broken, then verify again."""
    status = verify_binaries()
    broken = [n for n, info in status["packages"].items() if not info["ok"]]
    if broken:
        repair_tools(progress, packages=broken)
    return verify_binaries()


if __name__ == "__main__":
    import argparse
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Verify or repair the bundled adb / fastboot / scrcpy")
    parser.add_argument("--repair", action="store_true", help="download and re-install broken packages (hash-checked)")
    parser.add_argument("--archive-dir", default=None, help="use archives already downloaded to this folder")
    a = parser.parse_args()
    if a.repair:
        print(json.dumps(repair_tools(archive_dir=a.archive_dir), indent=2))
    print(json.dumps(verify_binaries(), indent=2))
