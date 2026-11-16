"""Live UART capture of a phone's boot log (read only: the program never writes to the port).
No adapter is needed for these tests: pyserial's loop:// port and small fakes stand in for one."""
import io
import os
import tempfile
import threading
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import serial

import main as app_main
from core import uart_capture as uc
from ui.i18n import render

BOOT = b"[PRELOADER] Build Time: 20220901\r\n[    3.211] mmcblk0: error -110 transferring data\r\n" \
       b"[    4.500] Kernel panic - not syncing: VFS: Unable to mount root fs\r\n"


class ScriptedPort:
    """A serial port that returns the given chunks, then nothing; records any write (there must be none)."""

    def __init__(self, chunks, fail_after=None):
        self.chunks = list(chunks)
        self.fail_after = fail_after
        self.reads = 0
        self.writes = []
        self.closed = False

    def read(self, n):
        self.reads += 1
        if self.fail_after is not None and self.reads > self.fail_after:
            raise serial.SerialException("ClearCommError failed (PermissionError(13, 'The device does not recognize the command.'))")
        if self.chunks:
            return self.chunks.pop(0)
        time.sleep(0.01)
        return b""

    def write(self, data):
        self.writes.append(data)

    def close(self):
        self.closed = True


def wait_for(cond, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.02)
    return cond()


class TestListPorts(unittest.TestCase):
    def test_known_adapters_are_named(self):
        fake = [SimpleNamespace(device="COM3", description="USB-SERIAL CH340 (COM3)", vid=0x1A86, pid=0x7523),
                SimpleNamespace(device="COM5", description="Silicon Labs CP210x (COM5)", vid=0x10C4, pid=0xEA60),
                SimpleNamespace(device="COM1", description="Communications Port (COM1)", vid=None, pid=None)]
        with patch("serial.tools.list_ports.comports", return_value=fake):
            ports = uc.list_ports()
        self.assertEqual([(p.device, p.adapter) for p in ports], [("COM3", "CH340"), ("COM5", "CP210x"), ("COM1", None)])


class TestCapture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.out = os.path.join(self.tmp.name, "uart.log")

    def test_loopback_bytes_are_saved_exactly_and_shown(self):
        port = serial.serial_for_url("loop://", baudrate=115200, timeout=0.1)
        shown = []
        cap = uc.UartCapture("loop://", out_path=self.out, on_text=shown.append, open_port=lambda: port)
        cap.start()
        port.write(BOOT)                                  # the test plays the phone
        self.assertTrue(wait_for(lambda: cap.bytes_received >= len(BOOT)))
        result = cap.stop()
        self.assertIsNone(result.error)
        self.assertEqual(Path(self.out).read_bytes(), BOOT)
        self.assertIn("Kernel panic", "".join(shown))
        self.assertEqual(result.bytes_received, len(BOOT))

    def test_never_writes_to_the_port(self):
        port = ScriptedPort([BOOT])
        cap = uc.UartCapture("COM9", out_path=self.out, open_port=lambda: port)
        cap.start()
        self.assertTrue(wait_for(lambda: cap.bytes_received == len(BOOT)))
        cap.stop()
        self.assertEqual(port.writes, [])
        self.assertTrue(port.closed)

    def test_multibyte_text_split_across_reads(self):
        text = "جاهز ok\n".encode("utf-8")
        port = ScriptedPort([text[:3], text[3:]])
        shown = []
        cap = uc.UartCapture("COM9", out_path=self.out, on_text=shown.append, open_port=lambda: port)
        cap.start()
        self.assertTrue(wait_for(lambda: cap.bytes_received == len(text)))
        cap.stop()
        self.assertEqual("".join(shown), "جاهز ok\n")

    def test_port_that_cannot_be_opened(self):
        def refuse():
            raise serial.SerialException("could not open port 'COM9': PermissionError(13, 'Access is denied.')")
        cap = uc.UartCapture("COM9", out_path=self.out, open_port=refuse)
        with self.assertRaises(uc.UartError) as ctx:
            cap.start()
        self.assertEqual(ctx.exception.msg.key, "uart.open_failed")
        self.assertIn("Access is denied", render(ctx.exception.msg, "en"))
        self.assertFalse(os.path.exists(self.out))       # no empty file left behind

    def test_port_released_when_the_save_file_cannot_be_created(self):
        port = ScriptedPort([BOOT])
        blocked = os.path.join(self.tmp.name, "not_a_dir")
        Path(blocked).write_text("a file where the folder should be")      # makedirs / open will fail
        cap = uc.UartCapture("COM9", out_path=os.path.join(blocked, "uart.log"), open_port=lambda: port)
        with self.assertRaises(uc.UartError) as ctx:
            cap.start()
        self.assertEqual(ctx.exception.msg.key, "uart.save_failed")
        self.assertTrue(port.closed)                       # the COM port is not left held
        self.assertFalse(cap.running)

    def test_adapter_unplugged_keeps_what_was_received(self):
        port = ScriptedPort([BOOT], fail_after=1)
        cap = uc.UartCapture("COM9", out_path=self.out, open_port=lambda: port)
        cap.start()
        self.assertTrue(wait_for(lambda: not cap.running))
        result = cap.stop()
        self.assertEqual(result.error.key, "uart.read_failed")
        self.assertEqual(Path(self.out).read_bytes(), BOOT)

    def test_size_limit_stops_the_capture(self):
        port = ScriptedPort([b"x" * 1000] * 50)
        cap = uc.UartCapture("COM9", out_path=self.out, open_port=lambda: port, max_bytes=5000)
        cap.start()
        self.assertTrue(wait_for(lambda: not cap.running))
        result = cap.stop()
        self.assertEqual(result.error.key, "uart.size_limit")
        self.assertLessEqual(os.path.getsize(self.out), 5000)

    def test_default_file_name_is_safe_and_in_the_uart_folder(self):
        with patch.object(uc, "default_uart_dir", return_value=self.tmp.name):
            path = uc.new_capture_path("\\\\.\\COM10")
        self.assertEqual(os.path.dirname(path), self.tmp.name)
        self.assertRegex(os.path.basename(path), r"^uart_COM10_\d{8}_\d{6}\.log$")


class _Widget:
    def __init__(self, value=""):
        self.value, self.state, self.values = value, "normal", []

    def get(self):
        return self.value

    def set(self, v):
        self.value = v

    def configure(self, **kw):
        self.state = kw.get("state", self.state)
        self.values = kw.get("values", self.values)


class _Box:
    def __init__(self):
        self.text = ""

    def insert(self, where, s):
        self.text += s

    def index(self, where):
        return f"{self.text.count(chr(10)) + 1}.0"

    def delete(self, a, b):
        drop = int(b.split(".")[0]) - 1
        self.text = "\n".join(self.text.split("\n")[drop:])

    def see(self, where):
        pass


class TestBoardTabUart(unittest.TestCase):
    """The Motherboard tab's UART card handlers, on a stand-in for the window."""

    def make_tab(self):
        from ui.i18n import t
        from ui.tabs.board_diag import BoardDiagTabMixin

        class Tab(BoardDiagTabMixin):
            current_lang = "en"

            def __init__(self):
                self.uart_port_combo, self.uart_baud_combo = _Widget(), _Widget("115200")
                self.btn_uart_start, self.btn_uart_stop = _Widget(), _Widget()
                self.uart_text, self.logscan_text = _Box(), _Box()
                self.boxes, self.pills = {}, {}
                self._uart_capture, self._uart_port_map = None, {}

            def _tr(self, key, **kw):
                return t(key, "en", **kw)

            def _m(self, value):
                return render(value, "en")

            def _set_box(self, box, text):
                box.text = text

            def _set_pill(self, key, status):
                self.pills[key] = status

            def after(self, ms, fn):
                if ms == 0:
                    fn()                     # the watch loop (500 ms) is driven by the test instead

        return Tab()

    def test_capture_then_analysis_in_the_log_card(self):
        tab = self.make_tab()
        fake = [SimpleNamespace(device="COM3", description="USB-SERIAL CH340 (COM3)", vid=0x1A86, pid=0x7523)]
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        port = ScriptedPort([BOOT])
        with patch("serial.tools.list_ports.comports", return_value=fake), \
                patch.object(uc, "open_serial", return_value=port), \
                patch.object(uc, "default_uart_dir", return_value=tmp.name), \
                patch("ui.tabs.board_diag.threading",       # only the tab's own reference: the capture keeps
                      SimpleNamespace(Thread=lambda target=None, daemon=None, **kw: SimpleNamespace(start=target))):
            tab._uart_refresh_ports()
            self.assertEqual(tab.uart_port_combo.get(), "COM3  CH340")
            tab._uart_start()
            self.assertEqual((tab.btn_uart_start.state, tab.btn_uart_stop.state), ("disabled", "normal"))
            self.assertTrue(wait_for(lambda: tab._uart_capture.bytes_received == len(BOOT)))
            tab._uart_stop()
        self.assertEqual((tab.btn_uart_start.state, tab.btn_uart_stop.state), ("normal", "disabled"))
        self.assertIn("Kernel panic", tab.uart_text.text)          # shown live
        self.assertIn("Kernel panic", tab.logscan_text.text)       # and analysed
        self.assertEqual(tab.pills, {"logscan": "FAIL"})
        self.assertEqual(port.writes, [])

    def test_no_port_selected_warns(self):
        tab = self.make_tab()
        with patch("ui.tabs.board_diag.messagebox") as mb:
            tab._uart_start()
        self.assertTrue(mb.showwarning.called)
        self.assertIsNone(tab._uart_capture)

    def test_screen_keeps_only_the_last_lines(self):
        tab = self.make_tab()
        tab.UART_MAX_SHOWN_LINES = 5
        tab._uart_append("".join(f"line {i}\n" for i in range(20)))
        self.assertNotIn("line 0\n", tab.uart_text.text)
        self.assertIn("line 19", tab.uart_text.text)


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_main(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out), patch("sys.argv", ["main.py", *argv]):
            code = app_main.main()
        return code, out.getvalue()

    def test_uart_list(self):
        fake = [SimpleNamespace(device="COM3", description="USB-SERIAL CH340 (COM3)", vid=0x1A86, pid=0x7523)]
        with patch("serial.tools.list_ports.comports", return_value=fake):
            code, out = self.run_main("--uart-list", "--lang", "en")
        self.assertEqual(code, app_main.EXIT_OK)
        self.assertIn("COM3", out)
        self.assertIn("CH340", out)

    def test_uart_capture_saves_then_analyses(self):
        port = ScriptedPort([BOOT])
        with patch.object(uc, "open_serial", return_value=port), \
                patch.object(uc, "default_uart_dir", return_value=self.tmp.name):
            code, out = self.run_main("--uart-capture", "COM9", "--seconds", "1", "--lang", "en")
        self.assertEqual(code, app_main.EXIT_OK)
        logs = [f for f in os.listdir(self.tmp.name) if f.endswith(".log")]
        self.assertEqual(len(logs), 1)
        self.assertEqual(Path(self.tmp.name, logs[0]).read_bytes(), BOOT)
        self.assertIn("Kernel panic", out)                # the analysis of what was captured
        self.assertIn("FAIL", out)

    def test_uart_capture_port_error_is_exit_4(self):
        with patch.object(uc, "open_serial", side_effect=serial.SerialException("could not open port 'COM9'")), \
                patch.object(uc, "default_uart_dir", return_value=self.tmp.name):
            code, out = self.run_main("--uart-capture", "COM9", "--seconds", "1", "--lang", "en")
        self.assertEqual(code, app_main.EXIT_INTERNAL)
        self.assertIn("COM9", out)


if __name__ == "__main__":
    unittest.main()
