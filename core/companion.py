"""
Companion app on the phone (companion/): measures what ADB cannot read without root.

Flow: install the APK (`adb install -r -g`; a copy signed with another key is used, never uninstalled) -> grant camera and
microphone -> `am start` the test screen with a unique run id -> read this run's lines
(`ADP|{json}`, tag AndroidDoctorCompanion) from logcat until "done" or the timeout.

The app only sends raw readings. Everything is judged here, with the raw line as evidence:
- battery capacity: Android's charge counter (uAh) / charge level gives the present full capacity
  (an estimate from the phone's fuel gauge, only computed at >= 50% charge); compared with the
  manufacturer's capacity from the model database, never with a guess. No spec -> UNKNOWN + value.
- sensors: a listed sensor that delivers no reading in 2.5 s is FAIL; delivering readings is PASS.
- vibration: accelerometer jitter while vibrating vs. still. Only judged when the phone lay still.
- speaker -> microphone: strength of the 1 kHz test tone recorded vs. without it (dB).
- cameras: the camera opens and delivers frames (PASS) or not (FAIL, with Android's error). Image
  brightness is reported as information only (a camera facing the table sees black).
"""

import json
import os
import re
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from core.result import CheckResult, Evidence, M, Status

PACKAGE = "com.androiddoctor.companion"
ACTIVITY = f"{PACKAGE}/.DiagActivity"
LOG_TAG = "AndroidDoctorCompanion"
LINE_PREFIX = "ADP|"
APP_VERSION_CODE = 1                 # companion/build.py VERSION_CODE of the APK shipped with this program

# Thresholds (documented in the module docstring and README)
MIN_LEVEL_FOR_CAPACITY = 50          # % charge needed for a capacity estimate
CAPACITY_FAIL_BELOW = 80.0           # % of the manufacturer's capacity
VIB_STILL_MAX_STD = 0.5              # m/s^2: above this the phone was moving, vibration not judged
VIB_PASS_RATIO, VIB_FAIL_RATIO = 3.0, 1.5
AUDIO_PASS_DB, AUDIO_FAIL_DB = 20.0, 6.0
CAMERA_DARK_MEAN, CAMERA_FLAT_STD = 8.0, 2.0


def apk_path() -> str:
    """companion/build/companion_diag.apk from source; the same relative path inside a build."""
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "companion", "build", "companion_diag.apk")


@dataclass
class CompanionRun:
    run_id: str
    ok: bool = False                       # the app ran and reported "done"
    error: Any = None                      # Msg when the run could not happen
    start: Dict[str, Any] = field(default_factory=dict)
    results: Dict[str, Dict[str, Any]] = field(default_factory=dict)
    raw_lines: Dict[str, str] = field(default_factory=dict)
    install_output: str = ""
    notice: Any = None                     # Msg: the app already on the phone was used (install refused)
    app_note: str = ""                     # added to every result's evidence source


def parse_lines(text: str, run_id: str) -> List[Dict[str, Any]]:
    """Messages of this run only, in order. Lines from other runs or malformed JSON are ignored."""
    out = []
    for line in (text or "").splitlines():
        i = line.find(LINE_PREFIX)
        if i < 0:
            continue
        try:
            msg = json.loads(line[i + len(LINE_PREFIX):])
        except ValueError:
            continue
        if isinstance(msg, dict) and msg.get("run") == run_id:
            msg["_raw"] = line[i:]
            out.append(msg)
    return out


def _install_failure_line(text: str) -> str:
    """The line that says why `adb install` failed (Failure [...] or the exception), not the stack trace."""
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    for line in lines:
        if line.startswith("Failure [") or re.match(r"^[\w.$]+(Exception|Error): ", line):
            return line[:400]
    useful = [l for l in lines if not l.startswith("at ")]
    return (useful[-1] if useful else text.strip())[:400]


def install(bridge, serial: str) -> tuple:
    """
    (ok, output). A companion signed with a different key is left in place and used, never uninstalled.
    `-g` grants the permissions at install time. MIUI refuses that flag (and `pm grant`) unless
    "USB debugging (Security settings)" is on, so the install is repeated without it and the app
    asks for camera and microphone itself.
    """
    path = apk_path()
    if not os.path.isfile(path):
        return False, M("companion.apk_missing", path=path)
    args = ["install", "-r", "-g", path]
    code, out, err = bridge.run_adb(args, serial=serial, timeout=120)
    text = f"{out}\n{err}".strip()
    if "INSTALL_GRANT_RUNTIME_PERMISSIONS" in text:
        args = ["install", "-r", path]
        code, out, err = bridge.run_adb(args, serial=serial, timeout=120)
        text = f"{out}\n{err}".strip()
    if "INSTALL_FAILED_UPDATE_INCOMPATIBLE" in text or "signatures do not match" in text.lower():
        # Signed with another key (installed from another PC). Never uninstalled here: a phone that then refuses
        # the install (Xiaomi with "Install via USB" off) would be left with no companion app at all.
        return False, M("companion.install_other_key", output=_install_failure_line(text))
    if code == 0 and "Success" in text:
        return True, text
    if "INSTALL_FAILED_USER_RESTRICTED" in text:
        # MIUI and some other ROMs: "Install via USB" is off, or its on-screen prompt was not accepted.
        return False, M("companion.install_user_restricted", output=_install_failure_line(text))
    return False, M("companion.install_failed", output=_install_failure_line(text))


def installed_version(bridge, serial: str) -> tuple:
    """(versionCode, versionName) of the companion app already on the phone, or (None, "") if it is not."""
    _, out, _ = bridge.shell(f"dumpsys package {PACKAGE}", serial=serial, timeout=15)
    text = out or ""
    if f"Package [{PACKAGE}]" not in text:
        return None, ""
    code = re.search(r"versionCode=(\d+)", text)
    name = re.search(r"versionName=(\S+)", text)
    if not code:
        return None, ""
    return int(code.group(1)), (name.group(1) if name else "")


def run_companion(bridge, serial: str, tests: str = "all", timeout: float = 120.0,
                  poll_seconds: float = 2.0, sleep=time.sleep) -> CompanionRun:
    run = CompanionRun(run_id=uuid.uuid4().hex[:16])
    ok, out = install(bridge, serial)
    run.install_output = out if isinstance(out, str) else ""
    if not ok:
        # Xiaomi with "Install via USB" off (or no APK in this build): an app installed by hand still works.
        code, name = installed_version(bridge, serial)
        if code is None:
            run.error = out
            return run
        args = getattr(out, "args", None) or {}
        reason = args.get("output") or args.get("path") or str(out)
        if getattr(out, "key", "") == "companion.install_other_key":
            key = "companion.using_installed_other_key"
        elif code == APP_VERSION_CODE:
            key = "companion.using_installed"
        else:
            key = "companion.using_installed_other_version"
        run.notice = M(key, version=name or str(code), code=code, expected=APP_VERSION_CODE, reason=reason)
        run.app_note = f"installed app {name or code}, not updated"
    for perm in ("android.permission.CAMERA", "android.permission.RECORD_AUDIO"):
        bridge.shell(f"pm grant {PACKAGE} {perm}", serial=serial, timeout=15)
    code, out, err = bridge.shell(f"am start -W -n {ACTIVITY} --es run_id {run.run_id} --es tests {tests}",
                                  serial=serial, timeout=30)
    if "Error" in (out or "") or "Error" in (err or ""):
        run.error = M("companion.start_failed", output=(out or err or "")[-300:])
        return run

    deadline = time.monotonic() + timeout
    while True:
        _, log, _ = bridge.shell(f"logcat -d -v raw -s {LOG_TAG}:I", serial=serial, timeout=20)
        for msg in parse_lines(log, run.run_id):
            kind = msg.get("type")
            if kind == "start":
                run.start = msg
            elif kind == "result" and msg.get("test"):
                run.results[msg["test"]] = msg
                run.raw_lines[msg["test"]] = msg["_raw"]
            elif kind == "done":
                run.ok = True
        if run.ok or time.monotonic() >= deadline:
            break
        sleep(poll_seconds)
    if not run.ok:
        run.error = M("companion.timeout", seconds=int(timeout), received=", ".join(sorted(run.results)) or "-")
    return run


# ---------------------------------------------------------------------------------------- judging
def _ev(run: CompanionRun, test: str, note: str = "") -> List[Evidence]:
    raw = run.raw_lines.get(test, "")
    notes = ", ".join(n for n in (note, run.app_note) if n)
    return [Evidence(source=f"companion app: {test}" + (f" ({notes})" if notes else ""), raw_excerpt=raw, exit_code=None)]


def judge_battery(run: CompanionRun, spec=None) -> CheckResult:
    r = run.results.get("battery", {})
    cr = CheckResult(check_id="companion.battery_capacity", evidence=_ev(run, "battery"))
    counter, pct = r.get("charge_counter_uah"), r.get("capacity_pct")
    if not isinstance(counter, int) or counter <= 0 or not isinstance(pct, int) or not 0 < pct <= 100:
        cr.detail = M("companion.battery.no_counter")
        return cr
    if pct < MIN_LEVEL_FOR_CAPACITY:
        cr.detail = M("companion.battery.charge_first", level=pct, need=MIN_LEVEL_FOR_CAPACITY)
        return cr
    full_mah = round(counter / 1000.0 * 100.0 / pct)
    cr.value, cr.unit = full_mah, "mAh"
    design = spec.design_capacity() if spec is not None else None
    if not design:
        cr.detail = M("companion.battery.no_spec", mah=full_mah)
        return cr
    ratio = round(full_mah / design[0] * 100.0, 1)
    if not 30.0 <= ratio <= 130.0:  # an implausible gauge, not a battery verdict
        cr.detail = M("companion.battery.implausible", mah=full_mah, design=design[0], ratio=ratio)
        return cr
    cr.status = Status.PASS if ratio >= CAPACITY_FAIL_BELOW else Status.FAIL
    cr.detail = M("companion.battery.ratio", mah=full_mah, design=design[0], kind=design[1], ratio=ratio, name=spec.name)
    cr.evidence.append(Evidence(source=f"model database: {spec.name}",
                                raw_excerpt=f"{design[1]} capacity {design[0]} mAh, source {spec.source}", exit_code=None))
    if cr.status == Status.FAIL:
        cr.possible_causes = [M("companion.cause.battery_worn"), M("companion.cause.gauge_not_calibrated")]
    return cr


def judge_sensors(run: CompanionRun) -> List[CheckResult]:
    r = run.results.get("sensors", {})
    out = []
    for name in ("accelerometer", "gyroscope", "magnetometer", "proximity", "light", "barometer"):
        s = r.get(name)
        if not isinstance(s, dict) or not s.get("present"):
            continue  # not on this phone: nothing to judge
        cr = CheckResult(check_id=f"companion.sensor.{name}", evidence=_ev(run, "sensors", name))
        events = s.get("events")
        if isinstance(events, int) and events > 0:
            cr.status, cr.value = Status.PASS, events
            cr.detail = M("companion.sensor.live", events=events)
        elif isinstance(events, int):
            cr.status, cr.value = Status.FAIL, 0
            cr.detail = M("companion.sensor.silent")
            cr.possible_causes = [M("companion.cause.sensor_ic_or_flex"), M("companion.cause.sensor_firmware")]
        out.append(cr)
    return out


def judge_vibration(run: CompanionRun) -> CheckResult:
    r = run.results.get("vibration", {})
    cr = CheckResult(check_id="companion.vibration", evidence=_ev(run, "vibration"))
    if not r.get("has_vibrator") or not r.get("has_accelerometer"):
        cr.detail = M("companion.vibration.not_measurable")
        return cr
    still, buzz = r.get("still_std"), r.get("vibrating_std")
    if not isinstance(still, (int, float)) or not isinstance(buzz, (int, float)) or still != still or buzz != buzz:
        cr.detail = M("companion.vibration.not_measurable")
        return cr
    if still > VIB_STILL_MAX_STD:
        cr.detail = M("companion.vibration.moving", still=round(still, 3))
        return cr
    ratio = buzz / max(still, 1e-3)
    cr.value = round(ratio, 1)
    if ratio >= VIB_PASS_RATIO:
        cr.status, cr.detail = Status.PASS, M("companion.vibration.felt", ratio=cr.value)
    elif ratio < VIB_FAIL_RATIO:
        cr.status, cr.detail = Status.FAIL, M("companion.vibration.not_felt", ratio=cr.value)
        cr.possible_causes = [M("companion.cause.vibrator_motor"), M("companion.cause.vibrator_contacts"), M("companion.cause.soft_surface")]
    else:
        cr.detail = M("companion.vibration.weak", ratio=cr.value)
    return cr


def _permission_denied(result: dict) -> str:
    """The Android permission the app was refused for this test ("" if none). Never a hardware verdict."""
    err = str(result.get("error") or "")
    return err.split(":", 1)[1] if err.startswith("permission_denied:") else ""


def judge_audio(run: CompanionRun) -> CheckResult:
    r = run.results.get("audio", {})
    cr = CheckResult(check_id="companion.audio_loop", evidence=_ev(run, "audio"))
    snr = r.get("snr_db")
    if _permission_denied(r):
        cr.detail = M("companion.permission_denied", permission=_permission_denied(r))
        return cr
    if r.get("error") or not isinstance(snr, (int, float)):
        cr.detail = M("companion.audio.not_measured", error=r.get("error", "-"))
        return cr
    cr.value, cr.unit = round(snr, 1), "dB"
    if snr >= AUDIO_PASS_DB:
        cr.status, cr.detail = Status.PASS, M("companion.audio.heard", db=cr.value)
    elif snr < AUDIO_FAIL_DB:
        cr.status, cr.detail = Status.FAIL, M("companion.audio.not_heard", db=cr.value)
        cr.possible_causes = [M("companion.cause.speaker"), M("companion.cause.microphone"), M("companion.cause.grill_blocked"), M("companion.cause.audio_policy")]
    else:
        cr.detail = M("companion.audio.weak", db=cr.value)
    return cr


def judge_cameras(run: CompanionRun) -> List[CheckResult]:
    r = run.results.get("cameras", {})
    if _permission_denied(r):
        return [CheckResult(check_id="companion.cameras", evidence=_ev(run, "cameras"),
                            detail=M("companion.permission_denied", permission=_permission_denied(r)))]
    if r.get("error"):
        return [CheckResult(check_id="companion.cameras", detail=M("companion.camera.not_tested", error=r["error"]),
                            evidence=_ev(run, "cameras"))]
    out = []
    for cam in r.get("cameras") or []:
        if not isinstance(cam, dict):
            continue
        cid, facing = str(cam.get("id", "?")), str(cam.get("facing", "unknown"))
        cr = CheckResult(check_id=f"companion.camera.{facing}", evidence=_ev(run, "cameras", f"camera {cid}"))
        frames = cam.get("frames") if isinstance(cam.get("frames"), int) else 0
        if frames > 0 and not cam.get("error"):
            cr.status, cr.value = Status.PASS, frames
            mean, std = cam.get("mean_luma"), cam.get("std_luma")
            dark = isinstance(mean, (int, float)) and isinstance(std, (int, float)) \
                and mean < CAMERA_DARK_MEAN and std < CAMERA_FLAT_STD
            cr.detail = M("companion.camera.dark" if dark else "companion.camera.ok", id=cid, frames=frames,
                          mean=round(mean, 1) if isinstance(mean, (int, float)) else "-")
        else:
            cr.status = Status.FAIL
            cr.detail = M("companion.camera.failed", id=cid, error=cam.get("error", "no_frames"))
            cr.possible_causes = [M("companion.cause.camera_module"), M("companion.cause.camera_flex"), M("companion.cause.camera_in_use")]
        out.append(cr)
    return out


def judge(run: CompanionRun, spec=None) -> List[CheckResult]:
    """CheckResults for every test the app reported. A run that did not happen gives one UNKNOWN."""
    if run.error is not None and not run.results:
        return [CheckResult(check_id="companion.run", detail=run.error)]
    checks: List[CheckResult] = []
    if "battery" in run.results:
        checks.append(judge_battery(run, spec))
    if "sensors" in run.results:
        checks += judge_sensors(run)
    if "vibration" in run.results:
        checks.append(judge_vibration(run))
    if "audio" in run.results:
        checks.append(judge_audio(run))
    if "cameras" in run.results:
        checks += judge_cameras(run)
    if run.error is not None:  # partial run: say which part is missing
        checks.append(CheckResult(check_id="companion.run", detail=run.error))
    return checks
