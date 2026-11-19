"""
Companion app without USB debugging (core/companion_lan.py): the Wi-Fi session server, the saved
result file, and the contract with the app (manifest deep link, query names, private-address rule).
The server is exercised over real sockets on 127.0.0.1; no phone is needed.
"""
import io
import json
import os
import re
import tempfile
import unittest
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from contextlib import redirect_stdout

from core import companion as comp
from core import companion_lan as lan
from core.model_db import ModelInfo
from core.result import Status

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
JAVA = os.path.join(ROOT, "companion", "src", "com", "androiddoctor", "companion", "DiagActivity.java")
MANIFEST = os.path.join(ROOT, "companion", "AndroidManifest.xml")


def lines(run_id, *messages, done=True):
    out = [f'ADP|{json.dumps(dict(m, run=run_id))}' for m in messages]
    if done:
        out.append(f'ADP|{json.dumps({"type": "done", "run": run_id})}')
    return "\n".join(out) + "\n"


START = {"type": "start", "model": "M2003J15SS", "manufacturer": "Xiaomi", "sdk": 31, "channel": "wifi"}
AUDIO = {"type": "result", "test": "audio", "snr_db": 31.2}
BATTERY = {"type": "result", "test": "battery", "charge_counter_uah": 3933000, "capacity_pct": 100}


class WifiSessionTests(unittest.TestCase):
    def setUp(self):
        from ui.i18n import set_current_language
        set_current_language("ar")
        self.addCleanup(set_current_language, "ar")
        self.tmp = tempfile.TemporaryDirectory()
        self.apk = os.path.join(self.tmp.name, "companion_diag.apk")
        with open(self.apk, "wb") as f:
            f.write(b"PK\x03\x04fake-apk")
        self.s = lan.CompanionWifiSession(host_ip="192.168.1.10", port=18765, apk=self.apk, lang="ar")
        self.s.start()
        self.events = []
        self.s.on_event = lambda kind, info: self.events.append(kind)
        self.base = f"http://127.0.0.1:{self.s.port}"

    def tearDown(self):
        self.s.stop()
        self.tmp.cleanup()

    def get(self, path):
        return urllib.request.urlopen(self.base + path, timeout=5)

    def post(self, body, token=None):
        req = urllib.request.Request(f"{self.base}/result?token={token or self.s.token}", data=body.encode(),
                                     method="POST", headers={"Content-Type": "text/plain; charset=utf-8"})
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status
        except urllib.error.HTTPError as e:
            return e.code

    def test_url_and_links_carry_the_session(self):
        self.assertEqual(self.s.url(), f"http://192.168.1.10:{self.s.port}/?k={self.s.token}")
        for link in (self.s.app_link(), self.s.intent_link()):
            self.assertIn(f"run={self.s.run_id}", link)
            self.assertIn(f"token={self.s.token}", link)
            self.assertIn("host=192.168.1.10", link)
            self.assertIn(f"port={self.s.port}", link)
        self.assertTrue(self.s.app_link().startswith("adpcompanion://run?"))
        self.assertIn("scheme=adpcompanion;package=com.androiddoctor.companion;end", self.s.intent_link())

    def test_page_and_apk_need_the_token(self):
        for path in ("/", f"/{lan.APK_NAME}", "/?k=wrong"):
            with self.assertRaises(urllib.error.HTTPError) as cm:
                self.get(path)
            self.assertEqual(cm.exception.code, 403)
        with self.get(f"/?k={self.s.token}") as r:
            page = r.read().decode()
        self.assertIn('dir="rtl"', page)                       # Arabic page for an Arabic program
        self.assertIn("ابدأ الفحص", page)
        self.assertIn(f"run={self.s.run_id}", page)
        with self.get(f"/{lan.APK_NAME}?k={self.s.token}") as r:
            self.assertEqual(r.headers["Content-Type"], "application/vnd.android.package-archive")
            self.assertEqual(r.read(), b"PK\x03\x04fake-apk")
        self.assertIn("page", self.events)
        self.assertIn("apk", self.events)
        self.assertIn("rejected", self.events)

    def test_missing_apk_is_404_not_a_crash(self):
        os.remove(self.apk)
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get(f"/{lan.APK_NAME}?k={self.s.token}")
        self.assertEqual(cm.exception.code, 404)

    def test_rejected_result_is_a_clean_403_not_a_reset(self):
        # Windows resets a connection closed with unread data: the 403 reply was lost about 1 run in 10
        # ("connection aborted", WinError 10053). A large body makes that close to certain on the old code.
        body = "x" * (512 * 1024)
        for _ in range(5):
            self.assertEqual(self.post(body, token="x" * 16), 403)
        self.assertFalse(self.s.received.is_set())

    def test_results_are_accepted_once_with_the_token_and_judged_like_adb(self):
        body = lines(self.s.run_id, START, AUDIO)
        self.assertEqual(self.post(body, token="x" * 16), 403)
        self.assertFalse(self.s.received.is_set())
        self.assertEqual(self.post(body), 200)
        self.assertEqual(self.post(body), 409)                 # one result per session
        run = self.s.wait(1)
        self.assertTrue(run.ok)
        self.assertEqual(lan.device_label(run), "Xiaomi M2003J15SS (Android SDK 31)")
        checks = comp.judge(run)
        self.assertEqual([(c.check_id, c.status) for c in checks], [("companion.audio_loop", Status.PASS)])
        src = checks[0].evidence[0].source
        self.assertIn("received over Wi-Fi from 127.0.0.1", src)
        self.assertIn("not linked to a USB connection", src)
        self.assertIn("result", self.events)

    def test_lines_of_another_run_are_ignored(self):
        self.assertEqual(self.post(lines("OTHER", START, AUDIO)), 409)
        self.assertFalse(self.s.received.is_set())

    def test_body_size_is_limited(self):
        # the length is refused from the header, before any of the body is read
        import http.client
        c = http.client.HTTPConnection("127.0.0.1", self.s.port, timeout=5)
        c.putrequest("POST", f"/result?token={self.s.token}")
        c.putheader("Content-Length", str(lan.MAX_BODY + 1))
        c.endheaders()
        self.assertEqual(c.getresponse().status, 413)
        c.close()
        self.assertFalse(self.s.received.is_set())

    def test_unknown_paths(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self.get(f"/etc/passwd?k={self.s.token}")
        self.assertEqual(cm.exception.code, 404)

    def test_wait_without_results_says_so(self):
        run = self.s.wait(0.05)
        self.assertEqual(run.error.key, "companion.wifi_timeout")
        self.assertEqual(comp.judge(run)[0].status, Status.UNKNOWN)


class SessionStartTests(unittest.TestCase):
    def test_no_lan_address_is_an_error_not_a_bad_url(self):
        s = lan.CompanionWifiSession(host_ip="", port=18800)
        s.host_ip = ""
        with self.assertRaises(RuntimeError):
            s.start()

    def test_next_port_when_the_first_is_taken(self):
        a = lan.CompanionWifiSession(host_ip="10.0.0.2", port=18810)
        b = lan.CompanionWifiSession(host_ip="10.0.0.2", port=18810)
        a.start()
        try:
            b.start()
            try:
                self.assertNotEqual(a.port, b.port)
            finally:
                b.stop()
        finally:
            a.stop()

    def test_only_private_addresses_are_offered(self):
        self.assertTrue(lan._is_private_ipv4("192.168.0.5"))
        self.assertTrue(lan._is_private_ipv4("10.1.2.3"))
        for ip in ("127.0.0.1", "8.8.8.8", "169.254.1.1", "::1", "nonsense"):
            self.assertFalse(lan._is_private_ipv4(ip), ip)
        for ip in lan.local_ipv4_addresses():
            self.assertTrue(lan._is_private_ipv4(ip))


class ResultFileTests(unittest.TestCase):
    def write(self, text, name="ADP_M2003J15SS_20261118_101500.txt"):
        d = tempfile.mkdtemp()
        path = os.path.join(d, name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        return path

    def test_saved_file_is_judged_and_says_where_it_came_from(self):
        spec = ModelInfo(model="M2003J15SS", name="Redmi Note 9", typical_mah=5020, rated_mah=4920,
                         source="https://example.com/spec", checked="2026-11-18")
        path = self.write(lines("PHONE1", START, BATTERY, AUDIO))
        run = lan.load_result_file(path)
        self.assertTrue(run.ok)
        self.assertIsNone(run.error)
        self.assertEqual(lan.reported_model(run), "M2003J15SS")
        checks = comp.judge(run, spec)
        self.assertEqual([c.check_id for c in checks], ["companion.battery_capacity", "companion.audio_loop"])
        self.assertIn("imported from file ADP_M2003J15SS_20261118_101500.txt", checks[0].evidence[0].source)

    def test_interrupted_file_keeps_what_arrived_and_says_it_is_incomplete(self):
        run = lan.load_result_file(self.write(lines("PHONE1", START, AUDIO, done=False)))
        self.assertFalse(run.ok)
        self.assertEqual(run.error.key, "companion.wifi_incomplete")
        checks = comp.judge(run)
        self.assertEqual(checks[0].status, Status.PASS)          # the audio result that did arrive
        self.assertEqual(checks[-1].check_id, "companion.run")   # and the missing part is stated
        self.assertEqual(checks[-1].status, Status.UNKNOWN)

    def test_file_without_results(self):
        run = lan.load_result_file(self.write("hello\nnot a result\n"))
        self.assertEqual(run.error.key, "companion.file_no_run")
        self.assertEqual(comp.judge(run)[0].status, Status.UNKNOWN)

    def test_unreadable_and_oversized_files(self):
        self.assertEqual(lan.load_result_file(os.path.join(tempfile.mkdtemp(), "missing.txt")).error.key,
                         "companion.file_unreadable")
        self.assertEqual(lan.load_result_file(self.write("x" * (lan.MAX_BODY + 10))).error.key,
                         "companion.file_too_large")

    def test_cli_companion_file(self):
        import main
        path = self.write(lines("PHONE1", START, AUDIO))
        args = main.build_parser().parse_args(["--companion-file", path, "--lang", "en"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main.run_companion_without_adb(args)
        self.assertEqual(code, main.EXIT_OK)
        out = buf.getvalue()
        self.assertIn("Xiaomi M2003J15SS", out)
        self.assertIn("[PASS]", out)
        args = main.build_parser().parse_args(["--companion-file", self.write("nothing"), "--lang", "en"])
        with redirect_stdout(io.StringIO()):
            self.assertEqual(main.run_companion_without_adb(args), main.EXIT_INTERNAL)


class AppContractTests(unittest.TestCase):
    """What the Python side sends must be what the Java side reads."""

    @classmethod
    def setUpClass(cls):
        with open(JAVA, encoding="utf-8") as f:
            cls.java = f.read()
        cls.manifest = ET.parse(MANIFEST).getroot()

    def test_manifest_has_the_deep_link_launcher_and_network(self):
        a = "{http://schemas.android.com/apk/res/android}"
        perms = {p.get(a + "name") for p in self.manifest.iter("uses-permission")}
        self.assertIn("android.permission.INTERNET", perms)
        self.assertEqual(self.manifest.find("application").get(a + "usesCleartextTraffic"), "true")
        data = [(d.get(a + "scheme"), d.get(a + "host")) for d in self.manifest.iter("data")]
        self.assertIn((lan.SCHEME, "run"), data)
        actions = {x.get(a + "name") for x in self.manifest.iter("action")}
        self.assertIn("android.intent.action.MAIN", actions)

    def test_query_names_match(self):
        for name in re.findall(r"[?&](\w+)=", lan.CompanionWifiSession(host_ip="10.0.0.1").app_link()):
            self.assertIn(f'getQueryParameter("{name}")', self.java, name)
        self.assertIn('"/result?token="', self.java)
        self.assertIn('"adpcompanion".equals(link.getScheme())', self.java)

    def test_token_and_run_id_fit_what_the_app_accepts(self):
        s = lan.CompanionWifiSession(host_ip="10.0.0.1")
        self.assertRegex(s.token, r"^[A-Za-z0-9_-]{8,64}$")
        self.assertRegex(s.run_id, r"^[A-Za-z0-9_-]{1,64}$")
        self.assertIn('k.matches("[A-Za-z0-9_-]{8,64}")', self.java)

    def test_app_waits_for_a_tap_when_not_started_over_adb(self):
        self.assertIn("startButton.setOnClickListener", self.java)
        self.assertIn("isPrivateIpv4(h)", self.java)

    def test_console_qr(self):
        q = lan.qr_text("http://192.168.1.10:8765/?k=abc")
        self.assertGreater(len(q.splitlines()), 10)


if __name__ == "__main__":
    unittest.main()
