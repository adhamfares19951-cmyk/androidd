"""
Companion app without USB debugging: the phone and the PC only share a Wi-Fi network.

Flow (GUI: Hardware tab, "Companion without USB debugging"; CLI: --companion-wifi):
1. The PC starts a small HTTP server on the local network and shows a QR code with its address.
2. The phone scans it and opens a page served by the PC: "Download the app" (the same APK the ADB
   flow installs), then "Start the test", a link that opens the app with this session's address,
   token and run id (adpcompanion://run?...).
3. The app asks the technician to tap Start, runs its tests and POSTs the same `ADP|{json}` lines it
   writes to logcat to /result?token=... . It also saves them as a file in Download/AndroidDoctor,
   which can be imported later with load_result_file() when the Wi-Fi way is not available.

Rules:
- Results are judged by core.companion.judge(), exactly like the ADB flow.
- A result received this way is not linked to a USB connection: every result's evidence says that
  it came over Wi-Fi (or from a file) and names the model the phone reported.
- Only this session's token is accepted, only lines of this session's run id are used, the request
  body is limited to MAX_BODY bytes, and the server serves nothing but the page, the APK and /result.
"""

import hmac
import ipaddress
import json
import logging
import os
import secrets
import socket
import threading
import uuid
from html import escape
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, List, Optional
from urllib.parse import parse_qs, quote, urlparse

from core.companion import (LINE_PREFIX, PACKAGE, CompanionRun, apk_path,
                            apply_messages, parse_lines)
from core.result import M

logger = logging.getLogger("CompanionLan")

DEFAULT_PORT = 8765
PORT_TRIES = 20
MAX_BODY = 1024 * 1024
APK_NAME = "adp-companion.apk"
SCHEME = "adpcompanion"


def _is_private_ipv4(ip: str) -> bool:
    try:
        a = ipaddress.ip_address(ip)
    except ValueError:
        return False
    return a.version == 4 and a.is_private and not a.is_loopback and not a.is_link_local


def local_ipv4_addresses() -> List[str]:
    """Private IPv4 addresses of this PC, the one of the default route first. No packet is sent."""
    found: List[str] = []
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("10.255.255.255", 1))  # UDP connect only picks the outgoing interface
            found.append(s.getsockname()[0])
        finally:
            s.close()
    except OSError:
        pass
    try:
        for ip in socket.gethostbyname_ex(socket.gethostname())[2]:
            found.append(ip)
    except OSError:
        pass
    out: List[str] = []
    for ip in found:
        if _is_private_ipv4(ip) and ip not in out:
            out.append(ip)
    return out


def run_from_text(text: str, run_id: Optional[str], note: str) -> CompanionRun:
    """
    A CompanionRun from `ADP|` lines (a POST body or a saved file). Without run_id, the run of the
    last "start" line is used (a file holds one run). The run is complete only with its "done" line.
    """
    if not run_id:
        starts = [m for m in _all_messages(text) if m.get("type") == "start" and m.get("run")]
        run_id = starts[-1]["run"] if starts else ""
    run = CompanionRun(run_id=run_id or "none", app_note=note)
    if not run_id:
        run.error = M("companion.file_no_run")
        return run
    apply_messages(run, parse_lines(text, run_id))
    if not run.ok:
        run.error = M("companion.wifi_incomplete", received=", ".join(sorted(run.results)) or "-")
    return run


def _all_messages(text: str) -> List[dict]:
    out = []
    for line in (text or "").splitlines():
        i = line.find(LINE_PREFIX)
        if i < 0:
            continue
        try:
            msg = json.loads(line[i + len(LINE_PREFIX):])
        except ValueError:
            continue
        if isinstance(msg, dict):
            out.append(msg)
    return out


def load_result_file(path: str) -> CompanionRun:
    """Results the app saved in Download/AndroidDoctor, copied to the PC (USB file transfer, cable or share)."""
    name = os.path.basename(path)
    try:
        with open(path, "rb") as f:
            data = f.read(MAX_BODY + 1)
    except OSError as e:
        run = CompanionRun(run_id="none")
        run.error = M("companion.file_unreadable", name=name, error=str(e))
        return run
    if len(data) > MAX_BODY:
        run = CompanionRun(run_id="none")
        run.error = M("companion.file_too_large", name=name, limit=MAX_BODY)
        return run
    run = run_from_text(data.decode("utf-8", errors="replace"), None, f"imported from file {name}, not linked to a USB connection")
    return run


def device_label(run: CompanionRun) -> str:
    """'Xiaomi Redmi Note 9 (Android SDK 31)' from the app's start line, '' if missing."""
    s = run.start or {}
    parts = " ".join(str(s[k]) for k in ("manufacturer", "model") if s.get(k))
    return f"{parts} (Android SDK {s['sdk']})" if parts and s.get("sdk") else parts


def reported_model(run: CompanionRun) -> str:
    return str((run.start or {}).get("model") or "")


class CompanionWifiSession:
    """One Wi-Fi session: one token, one run id, results accepted once. Start, show url(), wait()."""

    def __init__(self, host_ip: Optional[str] = None, port: int = DEFAULT_PORT, tests: str = "all",
                 apk: Optional[str] = None, lang: str = "en"):
        self.token = secrets.token_urlsafe(12)
        self.run_id = uuid.uuid4().hex[:16]
        self.tests = tests
        self.lang = lang
        self.apk = apk or apk_path()
        ips = local_ipv4_addresses()
        self.host_ip = host_ip or (ips[0] if ips else "")
        self._wanted_port = port
        self.port = 0
        self.run: Optional[CompanionRun] = None
        self.received = threading.Event()
        self.on_event: Optional[Callable[[str, dict], None]] = None   # ("page"|"apk"|"result"|"rejected", info)
        self._server: Optional[ThreadingHTTPServer] = None
        self._thread: Optional[threading.Thread] = None
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- addresses shown to the phone
    def url(self) -> str:
        return f"http://{self.host_ip}:{self.port}/?k={quote(self.token)}"

    def app_link(self) -> str:
        q = f"host={self.host_ip}&port={self.port}&token={quote(self.token)}&run={self.run_id}&tests={quote(self.tests)}"
        return f"{SCHEME}://run?{q}"

    def intent_link(self) -> str:
        """Chrome opens custom schemes from a tap on an intent:// link; other browsers use app_link()."""
        q = f"host={self.host_ip}&port={self.port}&token={quote(self.token)}&run={self.run_id}&tests={quote(self.tests)}"
        return f"intent://run?{q}#Intent;scheme={SCHEME};package={PACKAGE};end"

    # ---------------------------------------------------------------- server
    def start(self) -> None:
        """Binds the first free port from DEFAULT_PORT. Raises OSError (port, firewall) or RuntimeError (no LAN)."""
        if not self.host_ip:
            raise RuntimeError("no private IPv4 address: connect this PC to the same Wi-Fi network as the phone")
        session = self
        last_error: Optional[OSError] = None
        for p in range(self._wanted_port, self._wanted_port + PORT_TRIES):
            try:
                self._server = ThreadingHTTPServer(("0.0.0.0", p), _make_handler(session))
                self.port = self._server.server_address[1]
                break
            except OSError as e:
                last_error = e
        if self._server is None:
            raise last_error or OSError("no free port")
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, name="adp-companion-wifi", daemon=True)
        self._thread.start()
        logger.info(f"Companion Wi-Fi session on {self.host_ip}:{self.port}, run {self.run_id}")

    def stop(self) -> None:
        srv, self._server = self._server, None
        if srv is not None:
            srv.shutdown()
            srv.server_close()

    def wait(self, timeout: float) -> CompanionRun:
        """The received run, or a run whose error says nothing arrived (the server keeps running)."""
        if self.received.wait(timeout) and self.run is not None:
            return self.run
        run = CompanionRun(run_id=self.run_id)
        run.error = M("companion.wifi_timeout", seconds=int(timeout))
        return run

    # ---------------------------------------------------------------- called by the handler
    def _accept(self, body: str, peer: str) -> bool:
        with self._lock:
            if self.received.is_set():
                return False   # one result per session
            run = run_from_text(body, self.run_id, f"received over Wi-Fi from {peer}, not linked to a USB connection")
            if not run.results and not run.start:
                return False
            self.run = run
            self.received.set()
        self._notify("result", {"peer": peer, "model": device_label(run), "complete": run.ok})
        return True

    def _notify(self, kind: str, info: dict) -> None:
        cb = self.on_event
        if cb is not None:
            try:
                cb(kind, info)
            except Exception as e:  # a GUI callback must not break the server
                logger.warning(f"on_event failed: {e}")

    def page_html(self) -> str:
        # Msg renders in the language the UI / CLI set (ui.i18n.set_current_language), like every core text
        t = {k: str(M(f"companion_page.{k}")) for k in _PAGE_KEYS}
        rtl = ' dir="rtl"' if self.lang == "ar" else ""
        return (
            f"<!DOCTYPE html><html lang=\"{escape(self.lang)}\"{rtl}><head><meta charset=\"utf-8\">"
            "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>Android Doctor Pro</title>"
            "<style>body{font-family:sans-serif;margin:24px;line-height:1.5;color:#1f2328}"
            "a.b{display:block;margin:14px 0;padding:14px;border-radius:10px;background:#1f6feb;color:#fff;"
            "text-align:center;text-decoration:none;font-size:18px}a.s{background:#6e7781}"
            "ol{padding-inline-start:20px}small{color:#57606a}</style></head><body>"
            f"<h2>Android Doctor Pro</h2><ol>"
            f"<li>{escape(t['step1'])}<a class=\"b\" href=\"/{APK_NAME}?k={quote(self.token)}\">{escape(t['download'])}</a>"
            f"<small>{escape(t['unknown_sources'])}</small></li>"
            f"<li>{escape(t['step2'])}<a class=\"b\" href=\"{escape(self.intent_link())}\">{escape(t['start'])}</a>"
            f"<a class=\"b s\" href=\"{escape(self.app_link())}\">{escape(t['start_other'])}</a></li>"
            f"<li>{escape(t['step3'])}</li></ol></body></html>"
        )




_PAGE_KEYS = ("step1", "download", "unknown_sources", "step2", "start", "start_other", "step3")


def _make_handler(session: CompanionWifiSession):
    class Handler(BaseHTTPRequestHandler):
        server_version = "ADP"
        sys_version = ""

        def log_message(self, fmt, *args):  # into the program log, not stderr
            logger.info("%s %s", self.client_address[0], fmt % args)

        def _token_ok(self, query: dict) -> bool:
            given = (query.get("k") or query.get("token") or [""])[0]
            return hmac.compare_digest(given.encode(), session.token.encode())

        def _send(self, code: int, body: bytes, ctype: str = "text/plain; charset=utf-8", extra=None):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            q = parse_qs(u.query)
            if not self._token_ok(q):
                session._notify("rejected", {"peer": self.client_address[0], "path": u.path})
                return self._send(403, b"forbidden")
            if u.path == "/":
                session._notify("page", {"peer": self.client_address[0]})
                return self._send(200, session.page_html().encode("utf-8"), "text/html; charset=utf-8")
            if u.path == f"/{APK_NAME}":
                if not os.path.isfile(session.apk):
                    return self._send(404, b"companion APK not built")
                with open(session.apk, "rb") as f:
                    data = f.read()
                session._notify("apk", {"peer": self.client_address[0]})
                return self._send(200, data, "application/vnd.android.package-archive",
                                  {"Content-Disposition": f'attachment; filename="{APK_NAME}"'})
            return self._send(404, b"not found")

        def do_POST(self):
            u = urlparse(self.path)
            if u.path != "/result" or not self._token_ok(parse_qs(u.query)):
                session._notify("rejected", {"peer": self.client_address[0], "path": u.path})
                return self._send(403, b"forbidden")
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                length = -1
            if length <= 0 or length > MAX_BODY:
                return self._send(413, b"bad length")
            body = self.rfile.read(length).decode("utf-8", errors="replace")
            if session._accept(body, self.client_address[0]):
                return self._send(200, b"ok")
            return self._send(409, b"not accepted")

    return Handler


def qr_png(text: str, box_size: int = 8):
    """A PIL image of the QR code, or None when qrcode/Pillow are missing (the URL is shown as text then)."""
    try:
        import qrcode
    except ImportError:
        return None
    qr = qrcode.QRCode(border=2, box_size=box_size)
    qr.add_data(text)
    qr.make(fit=True)
    return qr.make_image(fill_color="black", back_color="white").get_image()


def qr_text(text: str) -> str:
    """The QR code as text blocks for a console window (CLI), '' when qrcode is missing."""
    try:
        import qrcode
    except ImportError:
        return ""
    qr = qrcode.QRCode(border=2)
    qr.add_data(text)
    qr.make(fit=True)
    m = qr.get_matrix()  # True = dark module
    rows = []
    for y in range(0, len(m), 2):
        top, bottom = m[y], (m[y + 1] if y + 1 < len(m) else [False] * len(m[y]))
        # a console is dark: light modules are drawn, dark modules stay background
        rows.append("".join("█" if not a and not b else "▀" if not a else "▄" if not b else " "
                            for a, b in zip(top, bottom)))
    return "\n".join(rows)


__all__ = ["CompanionWifiSession", "load_result_file", "run_from_text", "device_label", "reported_model",
           "local_ipv4_addresses", "qr_png", "qr_text"]
