import os
import sys
import zipfile
import shutil
import urllib.request
import subprocess
import logging

logging.basicConfig(level=logging.INFO, format="[%(asctime)s] %(levelname)s: %(message)s")
logger = logging.getLogger("SetupBins")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BIN_DIR = os.path.join(BASE_DIR, "bin")
os.makedirs(BIN_DIR, exist_ok=True)

IS_WINDOWS = sys.platform == "win32"
CREATE_NO_WINDOW = 0x08000000 if IS_WINDOWS else 0

ADB_EXE_NAME = "adb.exe" if IS_WINDOWS else "adb"
FASTBOOT_EXE_NAME = "fastboot.exe" if IS_WINDOWS else "fastboot"
SCRCPY_EXE_NAME = "scrcpy.exe" if IS_WINDOWS else "scrcpy"

ADB_PATH = os.path.join(BIN_DIR, ADB_EXE_NAME)
FASTBOOT_PATH = os.path.join(BIN_DIR, FASTBOOT_EXE_NAME)
SCRCPY_PATH = os.path.join(BIN_DIR, SCRCPY_EXE_NAME)

PLATFORM_TOOLS_URL = "https://dl.google.com/android/repository/platform-tools-latest-windows.zip"
SCRCPY_URL = "https://github.com/Genymobile/scrcpy/releases/download/v2.7/scrcpy-win64-v2.7.zip"


def kill_running_adb():
    """Terminate any background adb.exe processes to prevent Windows file locking."""
    if IS_WINDOWS:
        try:
            subprocess.run(
                ["taskkill", "/F", "/IM", "adb.exe"],
                creationflags=CREATE_NO_WINDOW,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception:
            pass
    else:
        try:
            subprocess.run(
                ["killall", "-9", "adb"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )
        except Exception:
            pass


def get_adb_path() -> str:
    """Strictly return local bin/adb path to avoid version mismatch conflicts."""
    return ADB_PATH


def get_fastboot_path() -> str:
    """Strictly return local bin/fastboot path."""
    return FASTBOOT_PATH


def get_scrcpy_path() -> str:
    """Strictly return local bin/scrcpy path."""
    return SCRCPY_PATH


def download_file_with_progress(url: str, dest_path: str, progress_callback=None) -> bool:
    logger.info(f"Downloading from {url} to {dest_path}")
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AndroidDoctorPro/4.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as response, open(dest_path, "wb") as out_file:
            total_size_hdr = response.info().get("Content-Length")
            total_size = int(total_size_hdr) if total_size_hdr else 0
            downloaded = 0
            block_size = 65536

            while True:
                buffer = response.read(block_size)
                if not buffer:
                    break
                out_file.write(buffer)
                downloaded += len(buffer)
                if progress_callback and total_size > 0:
                    pct = int(downloaded * 100 / total_size)
                    progress_callback(pct, downloaded, total_size)
                elif progress_callback:
                    progress_callback(50, downloaded, downloaded)

        logger.info(f"Download complete: {dest_path} ({downloaded} bytes)")
        return True
    except Exception as e:
        logger.error(f"Download failed for {url}: {e}")
        if os.path.exists(dest_path):
            try:
                os.remove(dest_path)
            except OSError:
                pass
        return False


def ensure_platform_tools(progress_callback=None) -> bool:
    current_adb = get_adb_path()
    current_fastboot = get_fastboot_path()

    if os.path.exists(current_adb) and os.path.exists(current_fastboot):
        logger.info(f"Platform tools already present: ADB at {current_adb}")
        return True

    kill_running_adb()
    zip_dest = os.path.join(BIN_DIR, "platform-tools.zip")
    logger.info("Platform tools missing. Initiating automatic download from Google...")
    success = download_file_with_progress(PLATFORM_TOOLS_URL, zip_dest, progress_callback)
    if not success:
        logger.error("Failed to download Android Platform Tools.")
        return False

    if not zipfile.is_zipfile(zip_dest):
        logger.error(f"Corrupted archive: {zip_dest} is not a valid zip file.")
        if os.path.exists(zip_dest):
            os.remove(zip_dest)
        return False

    try:
        logger.info(f"Extracting {zip_dest} into {BIN_DIR}...")
        with zipfile.ZipFile(zip_dest, "r") as zip_ref:
            if zip_ref.testzip() is not None:
                logger.error("Corrupted zip archive test failed.")
                return False

            temp_extract_dir = os.path.join(BIN_DIR, "temp_pt")
            os.makedirs(temp_extract_dir, exist_ok=True)
            zip_ref.extractall(temp_extract_dir)

            extracted_pt_dir = os.path.join(temp_extract_dir, "platform-tools")
            source_dir = extracted_pt_dir if os.path.exists(extracted_pt_dir) else temp_extract_dir

            for item in os.listdir(source_dir):
                s_item = os.path.join(source_dir, item)
                d_item = os.path.join(BIN_DIR, item)
                if os.path.isdir(s_item):
                    if os.path.exists(d_item):
                        shutil.rmtree(d_item, ignore_errors=True)
                    shutil.copytree(s_item, d_item)
                else:
                    shutil.copy2(s_item, d_item)

            shutil.rmtree(temp_extract_dir, ignore_errors=True)

        if os.path.exists(zip_dest):
            os.remove(zip_dest)

        logger.info("Android Platform Tools successfully installed.")
        return True
    except Exception as e:
        logger.error(f"Failed to extract platform tools: {e}")
        return False


def ensure_scrcpy(progress_callback=None) -> bool:
    current_scrcpy = get_scrcpy_path()
    if os.path.exists(current_scrcpy):
        logger.info(f"scrcpy already present at {current_scrcpy}")
        return True

    zip_dest = os.path.join(BIN_DIR, "scrcpy.zip")
    logger.info("scrcpy missing. Initiating automatic download...")
    success = download_file_with_progress(SCRCPY_URL, zip_dest, progress_callback)
    if not success:
        logger.warning("Could not download scrcpy automatically.")
        return False

    if not zipfile.is_zipfile(zip_dest):
        logger.error(f"Corrupted archive: {zip_dest} is not a valid zip file.")
        if os.path.exists(zip_dest):
            os.remove(zip_dest)
        return False

    try:
        with zipfile.ZipFile(zip_dest, "r") as zip_ref:
            if zip_ref.testzip() is not None:
                logger.error("Corrupted scrcpy zip archive.")
                return False

            temp_extract_dir = os.path.join(BIN_DIR, "temp_scrcpy")
            os.makedirs(temp_extract_dir, exist_ok=True)
            zip_ref.extractall(temp_extract_dir)

            subdirs = [os.path.join(temp_extract_dir, d) for d in os.listdir(temp_extract_dir) if os.path.isdir(os.path.join(temp_extract_dir, d))]
            source_dir = subdirs[0] if subdirs else temp_extract_dir

            for item in os.listdir(source_dir):
                s_item = os.path.join(source_dir, item)
                d_item = os.path.join(BIN_DIR, item)
                if os.path.isdir(s_item):
                    if os.path.exists(d_item):
                        shutil.rmtree(d_item, ignore_errors=True)
                    shutil.copytree(s_item, d_item)
                else:
                    shutil.copy2(s_item, d_item)

            shutil.rmtree(temp_extract_dir, ignore_errors=True)

        if os.path.exists(zip_dest):
            os.remove(zip_dest)

        logger.info("scrcpy successfully installed.")
        return True
    except Exception as e:
        logger.error(f"Failed to extract scrcpy: {e}")
        return False


def verify_binaries() -> dict:
    adb_binary = get_adb_path()
    fastboot_binary = get_fastboot_path()
    scrcpy_binary = get_scrcpy_path()

    results = {
        "adb": {"available": False, "version": "", "path": adb_binary},
        "fastboot": {"available": False, "version": "", "path": fastboot_binary},
        "scrcpy": {"available": False, "version": "", "path": scrcpy_binary}
    }

    if os.path.exists(adb_binary):
        try:
            p = subprocess.run(
                [adb_binary, "version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
                text=True,
                timeout=10
            )
            if p.returncode == 0:
                first_line = p.stdout.strip().split("\n")[0] if p.stdout else "ADB Active"
                results["adb"]["available"] = True
                results["adb"]["version"] = first_line
        except Exception as e:
            results["adb"]["version"] = f"Execution error: {e}"

    if os.path.exists(fastboot_binary):
        try:
            p = subprocess.run(
                [fastboot_binary, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
                text=True,
                timeout=10
            )
            if p.returncode == 0:
                first_line = p.stdout.strip().split("\n")[0] if p.stdout else "Fastboot Active"
                results["fastboot"]["available"] = True
                results["fastboot"]["version"] = first_line
        except Exception as e:
            results["fastboot"]["version"] = f"Execution error: {e}"

    if os.path.exists(scrcpy_binary):
        try:
            p = subprocess.run(
                [scrcpy_binary, "--version"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                creationflags=CREATE_NO_WINDOW,
                text=True,
                timeout=10
            )
            if p.returncode == 0:
                first_line = p.stdout.strip().split("\n")[0] if p.stdout else "scrcpy Active"
                results["scrcpy"]["available"] = True
                results["scrcpy"]["version"] = first_line
        except Exception as e:
            results["scrcpy"]["version"] = f"Execution error: {e}"

    return results


def ensure_all_binaries(progress_callback=None) -> dict:
    ensure_platform_tools(progress_callback)
    ensure_scrcpy(progress_callback)
    return verify_binaries()


if __name__ == "__main__":
    def cli_progress(pct, cur, total):
        sys.stdout.write(f"\rDownloading: {pct}% [{cur}/{total} bytes]")
        sys.stdout.flush()

    print("Checking and ensuring bundled local binaries in bin/...")
    res = ensure_all_binaries(cli_progress)
    print("\n\nBinary Verification Results:")
    for k, v in res.items():
        status = "OK" if v["available"] else "MISSING"
        print(f"[{status}] {k.upper()}: {v['path']} ({v['version']})")
