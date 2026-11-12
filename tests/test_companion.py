"""Companion app: protocol shared with the Java app, the run flow, and how raw readings are judged."""
import json
import os
import re
import tempfile
import unittest
from unittest.mock import patch

from core import companion as comp
from core.model_db import ModelInfo
from core.result import Status

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA = os.path.join(ROOT, "companion", "src", "com", "androiddoctor", "companion", "DiagActivity.java")
MANIFEST = os.path.join(ROOT, "companion", "AndroidManifest.xml")
SPEC = ModelInfo(model="SM-A525F", name="Samsung Galaxy A52", source="https://example.com/a52",
                 typical_mah=4500, rated_mah=4370)


def run_with(**results):
    r = comp.CompanionRun(run_id="r1", ok=True)
    for test, data in results.items():
        msg = dict(data, type="result", test=test, run="r1")
        r.results[test] = msg
        r.raw_lines[test] = "ADP|" + json.dumps(msg)
    return r


class ProtocolTests(unittest.TestCase):
    """The Java app and this module must agree on every name."""

    def test_java_and_python_agree(self):
        java = open(JAVA, encoding="utf-8").read()
        manifest = open(MANIFEST, encoding="utf-8").read()
        self.assertIn(f'package="{comp.PACKAGE}"', manifest)
        self.assertIn(f'TAG = "{comp.LOG_TAG}"', java)
        self.assertIn(f'"{comp.LINE_PREFIX}"', java)
        self.assertIn('getStringExtra("run_id")', java)
        self.assertIn('getStringExtra("tests")', java)
        self.assertIn(comp.ACTIVITY.split("/")[1].lstrip("."), java)
        for test in ("battery", "sensors", "vibration", "audio", "cameras"):
            self.assertIn(f'"{test}"', java)
        for key in ("charge_counter_uah", "capacity_pct", "still_std", "vibrating_std", "snr_db",
                    "frames", "mean_luma", "std_luma", "events", "present"):
            self.assertIn(f'"{key}"', java)

    def test_app_stores_nothing(self):
        java = open(JAVA, encoding="utf-8").read()
        for forbidden in ("FileOutputStream", "openFileOutput", "MediaStore", "compressToJpeg", "MediaRecorder.setOutputFile"):
            self.assertNotIn(forbidden, java)

    def test_parse_lines_keeps_only_this_run(self):
        text = "\n".join([
            'ADP|{"type":"start","run":"r1"}',
            'ADP|{"type":"result","test":"battery","run":"old"}',
            "garbage line",
            'ADP|{not json',
            'I/AndroidDoctorCompanion( 123): ADP|{"type":"done","run":"r1"}',
        ])
        msgs = comp.parse_lines(text, "r1")
        self.assertEqual([m["type"] for m in msgs], ["start", "done"])


class FakePhone:
    def __init__(self, logs, install_replies=None, apk=True):
        self.logs = list(logs)
        self.install_replies = list(install_replies or [(0, "Success", "")])
        self.calls = []

    def run_adb(self, args, serial=None, timeout=30):
        self.calls.append(("adb", tuple(args)))
        return self.install_replies.pop(0) if self.install_replies else (0, "Success", "")

    def shell(self, command, serial=None, timeout=30, **kw):
        self.calls.append(("shell", command))
        if command.startswith("am start"):
            self.run_id = re.search(r"--es run_id (\w+)", command).group(1)
            return 0, "Status: ok", ""
        if command.startswith("logcat"):
            text = self.logs.pop(0) if self.logs else ""
            return 0, text.replace("RUN", self.run_id), ""
        return 0, "", ""


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.apk = os.path.join(self.tmp.name, "companion_diag.apk")
        with open(self.apk, "wb") as f:
            f.write(b"PK")
        p = patch.object(comp, "apk_path", return_value=self.apk)
        p.start()
        self.addCleanup(p.stop)

    def tearDown(self):
        self.tmp.cleanup()

    def test_full_run_collects_results_until_done(self):
        phone = FakePhone([
            'ADP|{"type":"start","run":"RUN"}\nADP|{"type":"result","test":"battery","run":"RUN","charge_counter_uah":3933000,"capacity_pct":100}',
            'ADP|{"type":"result","test":"audio","run":"RUN","snr_db":31.2}\nADP|{"type":"done","run":"RUN"}',
        ])
        run = comp.run_companion(phone, "S1", sleep=lambda s: None)
        self.assertTrue(run.ok)
        self.assertIsNone(run.error)
        self.assertEqual(sorted(run.results), ["audio", "battery"])
        cmds = [c[1] for c in phone.calls if c[0] == "shell"]
        self.assertTrue(any(c.startswith("pm grant") and "CAMERA" in c for c in cmds))
        self.assertTrue(any(c.startswith("am start -W -n com.androiddoctor.companion/.DiagActivity") for c in cmds))
        self.assertEqual(phone.calls[0], ("adb", ("install", "-r", "-g", self.apk)))
        checks = comp.judge(run, SPEC)
        self.assertEqual({c.check_id: c.status for c in checks},
                         {"companion.battery_capacity": Status.PASS, "companion.audio_loop": Status.PASS})

    def test_timeout_keeps_partial_results_and_says_so(self):
        phone = FakePhone(['ADP|{"type":"result","test":"audio","run":"RUN","snr_db":30}'])
        with patch.object(comp.time, "monotonic", side_effect=[0.0, 0.0, 999.0, 999.0]):
            run = comp.run_companion(phone, "S1", timeout=5, sleep=lambda s: None)
        self.assertFalse(run.ok)
        self.assertEqual(run.error.key, "companion.timeout")
        ids = [c.check_id for c in comp.judge(run)]
        self.assertEqual(ids, ["companion.audio_loop", "companion.run"])

    def test_other_signing_key_triggers_reinstall(self):
        phone = FakePhone(['ADP|{"type":"done","run":"RUN"}'],
                          install_replies=[(1, "", "Failure [INSTALL_FAILED_UPDATE_INCOMPATIBLE]"), (0, "Success", "")])
        run = comp.run_companion(phone, "S1", sleep=lambda s: None)
        self.assertTrue(run.ok)
        self.assertIn(("shell", "pm uninstall com.androiddoctor.companion"), phone.calls)

    def test_missing_apk_and_failed_install(self):
        with patch.object(comp, "apk_path", return_value=os.path.join(self.tmp.name, "missing.apk")):
            run = comp.run_companion(FakePhone([]), "S1", sleep=lambda s: None)
        self.assertEqual(run.error.key, "companion.apk_missing")
        self.assertEqual([c.status for c in comp.judge(run)], [Status.UNKNOWN])
        run = comp.run_companion(FakePhone([], install_replies=[(1, "", "Failure [INSTALL_FAILED_INSUFFICIENT_STORAGE]")]),
                                 "S1", sleep=lambda s: None)
        self.assertEqual(run.error.key, "companion.install_failed")


class BatteryTests(unittest.TestCase):
    def test_capacity_against_rated_spec(self):
        c = comp.judge_battery(run_with(battery={"charge_counter_uah": 3933000, "capacity_pct": 100}), SPEC)
        self.assertEqual((c.status, c.value, c.unit), (Status.PASS, 3933, "mAh"))  # 3933 / 4370 rated = 90%
        c = comp.judge_battery(run_with(battery={"charge_counter_uah": 1500000, "capacity_pct": 50}), SPEC)
        self.assertEqual(c.status, Status.FAIL)  # 3000 / 4370 = 68.6%
        self.assertTrue(c.possible_causes)
        self.assertTrue(any("model database" in e.source for e in c.evidence))

    def test_no_verdict_without_reading_level_or_spec(self):
        cases = [({"charge_counter_uah": -2147483648, "capacity_pct": 90}, SPEC),
                 ({"charge_counter_uah": 0, "capacity_pct": 90}, SPEC),
                 ({"charge_counter_uah": 2000000, "capacity_pct": 40}, SPEC),
                 ({"charge_counter_uah": 3900000, "capacity_pct": 100}, None),
                 ({"charge_counter_uah": 9000000, "capacity_pct": 100}, SPEC)]  # 206%: implausible gauge
        for data, spec in cases:
            with self.subTest(data=data, spec=spec is not None):
                self.assertEqual(comp.judge_battery(run_with(battery=data), spec).status, Status.UNKNOWN)


class OtherJudgeTests(unittest.TestCase):
    def test_sensors(self):
        checks = comp.judge_sensors(run_with(sensors={
            "accelerometer": {"present": True, "events": 120}, "gyroscope": {"present": True, "events": 0},
            "barometer": {"present": False}}))
        self.assertEqual({c.check_id: c.status for c in checks},
                         {"companion.sensor.accelerometer": Status.PASS, "companion.sensor.gyroscope": Status.FAIL})

    def test_vibration(self):
        j = lambda still, buzz, **kw: comp.judge_vibration(run_with(vibration=dict(
            has_vibrator=True, has_accelerometer=True, still_std=still, vibrating_std=buzz, **kw))).status
        self.assertEqual(j(0.02, 0.4), Status.PASS)
        self.assertEqual(j(0.02, 0.025), Status.FAIL)
        self.assertEqual(j(0.02, 0.04), Status.UNKNOWN)       # weak: check by hand
        self.assertEqual(j(0.9, 0.9), Status.UNKNOWN)         # phone was moving
        self.assertEqual(comp.judge_vibration(run_with(vibration={"has_vibrator": False})).status, Status.UNKNOWN)

    def test_audio(self):
        j = lambda data: comp.judge_audio(run_with(audio=data)).status
        self.assertEqual(j({"snr_db": 35.0}), Status.PASS)
        self.assertEqual(j({"snr_db": 2.0}), Status.FAIL)
        self.assertEqual(j({"snr_db": 12.0}), Status.UNKNOWN)
        self.assertEqual(j({"error": "permission_denied:RECORD_AUDIO"}), Status.UNKNOWN)

    def test_cameras(self):
        checks = comp.judge_cameras(run_with(cameras={"cameras": [
            {"id": "0", "facing": "back", "frames": 18, "mean_luma": 3.0, "std_luma": 0.5},
            {"id": "1", "facing": "front", "frames": 0, "error": "open_error_3"}]}))
        self.assertEqual([(c.check_id, c.status) for c in checks],
                         [("companion.camera.back", Status.PASS), ("companion.camera.front", Status.FAIL)])
        self.assertEqual(checks[0].detail.key, "companion.camera.dark")  # dark image is information, not a fault
        denied = comp.judge_cameras(run_with(cameras={"error": "permission_denied:CAMERA"}))
        self.assertEqual([c.status for c in denied], [Status.UNKNOWN])


if __name__ == "__main__":
    unittest.main()
