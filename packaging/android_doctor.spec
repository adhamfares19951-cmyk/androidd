# PyInstaller spec: one folder with two programs that share one runtime.
#   AndroidDoctorPro.exe  GUI (no console window)
#   adp-cli.exe           console: --cli, --check-bins, --verify-report, --gen-signing-key ...
# Build: powershell -ExecutionPolicy Bypass -File packaging\build.ps1
# bin/ is copied as data (never UPX-compressed or stripped), so files stay byte-identical to
# bin/manifest.json and verify_binaries() passes inside the build. The SQLite database is NOT
# bundled: a build keeps it in %LOCALAPPDATA%\AndroidDoctorPro\ (app_paths.py).
import os

from PyInstaller.utils.hooks import collect_data_files

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))

datas = [
    (os.path.join(ROOT, "bin"), "bin"),
    (os.path.join(ROOT, "locales"), "locales"),
    (os.path.join(ROOT, "data", "fault_signatures.json"), "data"),
    (os.path.join(ROOT, "data", "thermal_thresholds.json"), "data"),
    (os.path.join(ROOT, "data", "device_models.json"), "data"),
    (os.path.join(ROOT, "README.md"), "."),
]
datas += collect_data_files("customtkinter")
# Companion APK (built by companion/build.py; build.ps1 runs it first). Without it the GUI says so.
_companion_apk = os.path.join(ROOT, "companion", "build", "companion_diag.apk")
if os.path.isfile(_companion_apk):
    datas.append((_companion_apk, os.path.join("companion", "build")))

a = Analysis(
    [os.path.join(ROOT, "main.py")],
    pathex=[ROOT],
    datas=datas,
    hiddenimports=["darkdetect", "qrcode", "arabic_reshaper", "bidi"],
    # numpy/lxml are optional extras of other packages; nothing in this program imports them
    excludes=["pytest", "pyinstaller", "PyInstaller", "numpy", "lxml"],
    noarchive=False,
)
# scrcpy's DLLs are found as dependencies of bin/scrcpy.exe; scrcpy loads them from bin/, so drop
# the second copy PyInstaller would put next to the program.
_bin_names = {n.lower() for n in os.listdir(os.path.join(ROOT, "bin"))}
# (bin/'s own .exe/.dll files are listed here too, with dest "bin/...": those must stay.)
a.binaries = [b for b in a.binaries
              if os.path.dirname(b[0]).replace("\\", "/") == "bin" or os.path.basename(b[0]).lower() not in _bin_names]

pyz = PYZ(a.pure)

gui = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="AndroidDoctorPro",
    console=False,
    upx=False,
    strip=False,
)
cli = EXE(
    pyz, a.scripts, [],
    exclude_binaries=True,
    name="adp-cli",
    console=True,
    upx=False,
    strip=False,
)
coll = COLLECT(
    gui, cli,
    a.binaries,
    a.datas,
    upx=False,
    strip=False,
    name="AndroidDoctorPro",
)
