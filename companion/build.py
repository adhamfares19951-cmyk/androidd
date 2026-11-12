"""
Build companion/build/companion_diag.apk with the Android SDK tools directly (no Gradle, no downloads).

    python companion/build.py

Needs: an Android SDK with platforms;android-35 and build-tools;35.0.0 (ANDROID_HOME, ANDROID_SDK_ROOT
or %LOCALAPPDATA%\\Android\\Sdk) and a JDK 17+ (JAVA_HOME or javac on PATH).

The APK signing key is created on first build in %LOCALAPPDATA%\\AndroidDoctorPro\\companion-signing\\
(outside the project, never committed). Keep that folder: an APK signed with another key cannot
update an installed one (the desktop program then uninstalls and reinstalls the companion).
"""

import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import zipfile

HERE = os.path.dirname(os.path.abspath(__file__))
PACKAGE = "com.androiddoctor.companion"
VERSION_CODE = 1
VERSION_NAME = "1.0"
MIN_SDK = 24
TARGET_SDK = 35
PLATFORM = "android-35"
BUILD_TOOLS = "35.0.0"
OUT_DIR = os.path.join(HERE, "build")
OUT_APK = os.path.join(OUT_DIR, "companion_diag.apk")
IS_WINDOWS = sys.platform == "win32"


def sdk_root() -> str:
    for env in ("ANDROID_HOME", "ANDROID_SDK_ROOT"):
        if os.environ.get(env) and os.path.isdir(os.environ[env]):
            return os.environ[env]
    local = os.environ.get("LOCALAPPDATA")
    if local and os.path.isdir(os.path.join(local, "Android", "Sdk")):
        return os.path.join(local, "Android", "Sdk")
    raise SystemExit("Android SDK not found (set ANDROID_HOME).")


def jdk_tool(name: str) -> str:
    exe = name + (".exe" if IS_WINDOWS else "")
    if os.environ.get("JAVA_HOME"):
        p = os.path.join(os.environ["JAVA_HOME"], "bin", exe)
        if os.path.isfile(p):
            return p
    found = shutil.which(name)
    if found:
        return found
    raise SystemExit(f"{name} not found (set JAVA_HOME to a JDK 17+).")


def run(cmd, **kw):
    print("  $", " ".join(os.path.basename(c) if i == 0 else c for i, c in enumerate(cmd)))
    res = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if res.returncode != 0:
        sys.stderr.write(res.stdout + res.stderr)
        raise SystemExit(f"failed: {os.path.basename(cmd[0])} (exit {res.returncode})")
    return res


def signing_key(keytool: str):
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~")
    folder = os.path.join(base, "AndroidDoctorPro", "companion-signing")
    ks, pw_file = os.path.join(folder, "companion.p12"), os.path.join(folder, "companion.pass")
    if not os.path.isfile(ks):
        os.makedirs(folder, exist_ok=True)
        password = secrets.token_urlsafe(24)
        with open(pw_file, "w", encoding="ascii") as f:
            f.write(password)
        # the password is read from its file, never put on a command line or in the log
        run([keytool, "-genkeypair", "-storetype", "PKCS12", "-keystore", ks, "-storepass:file", pw_file,
             "-alias", "companion", "-keyalg", "RSA", "-keysize", "3072", "-validity", "10000",
             "-dname", "CN=Android Doctor Pro Companion"])
    return ks, pw_file


def main() -> int:
    sdk = sdk_root()
    android_jar = os.path.join(sdk, "platforms", PLATFORM, "android.jar")
    bt = os.path.join(sdk, "build-tools", BUILD_TOOLS)
    if not os.path.isfile(android_jar) or not os.path.isdir(bt):
        raise SystemExit(f"Install the SDK packages: platforms;{PLATFORM} build-tools;{BUILD_TOOLS}")
    script = ".bat" if IS_WINDOWS else ""
    exe = ".exe" if IS_WINDOWS else ""
    aapt2, zipalign = os.path.join(bt, "aapt2" + exe), os.path.join(bt, "zipalign" + exe)
    d8, apksigner = os.path.join(bt, "d8" + script), os.path.join(bt, "apksigner" + script)
    javac, keytool = jdk_tool("javac"), jdk_tool("keytool")

    sources = []
    for root, _, files in os.walk(os.path.join(HERE, "src")):
        sources += [os.path.join(root, f) for f in files if f.endswith(".java")]

    os.makedirs(OUT_DIR, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="adp_companion_") as work:
        classes, dex = os.path.join(work, "classes"), os.path.join(work, "dex")
        os.makedirs(classes)
        os.makedirs(dex)
        unsigned, aligned = os.path.join(work, "unsigned.apk"), os.path.join(work, "aligned.apk")

        print("1/5 manifest")
        run([aapt2, "link", "-o", unsigned, "-I", android_jar, "--manifest", os.path.join(HERE, "AndroidManifest.xml"),
             "--min-sdk-version", str(MIN_SDK), "--target-sdk-version", str(TARGET_SDK),
             "--version-code", str(VERSION_CODE), "--version-name", VERSION_NAME])
        print("2/5 compile")
        run([javac, "--release", "11", "-Xlint:-options", "-encoding", "UTF-8", "-classpath", android_jar,
             "-d", classes] + sources)
        print("3/5 dex")
        class_files = []
        for root, _, files in os.walk(classes):
            class_files += [os.path.join(root, f) for f in files if f.endswith(".class")]
        run([d8, "--release", "--min-api", str(MIN_SDK), "--lib", android_jar, "--output", dex] + class_files)
        with zipfile.ZipFile(unsigned, "a", compression=zipfile.ZIP_DEFLATED) as z:
            z.write(os.path.join(dex, "classes.dex"), "classes.dex")
        print("4/5 align")
        run([zipalign, "-f", "-p", "4", unsigned, aligned])
        print("5/5 sign")
        ks, pw_file = signing_key(keytool)
        if os.path.exists(OUT_APK):
            os.remove(OUT_APK)
        run([apksigner, "sign", "--ks", ks, "--ks-pass", f"file:{pw_file}", "--ks-key-alias", "companion",
             "--out", OUT_APK, aligned])
        run([apksigner, "verify", "--min-sdk-version", str(MIN_SDK), OUT_APK])
    print(f"Built {OUT_APK} ({os.path.getsize(OUT_APK) // 1024} KB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
