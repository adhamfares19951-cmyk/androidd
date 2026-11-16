"""Offline log analysis: a saved UART / dmesg / last_kmsg / logcat file checked against data/fault_signatures.json.
No phone and no root needed. A matched fault line is FAIL (with possible causes); no known signature is UNKNOWN,
never PASS: a log without the faults we know is not proof that the phone is healthy."""
import io
import os
import tempfile
import unittest
from types import SimpleNamespace
from contextlib import redirect_stdout
from pathlib import Path

import main as app_main
from core import log_analyzer as la
from ui.i18n import render, t
from ui.ui_helpers import format_log_analysis

# A MediaTek boot captured over UART: preloader, LK, kernel, then the storage link fails and the log ends.
MTK_UART = (
    "[PRELOADER] Build Time: 20220901-101112\r\n"
    "[PLFM] Init I2C: OK(0)\r\n"
    "\x1b[0;32m[LK] mt6768 platform_init\x1b[0m\r\n"
    "[    0.000000] Booting Linux on physical CPU 0x0\n"
    "[    0.000000] Machine model: MT6768\n"
    "[    2.104512] mtk-msdc 11230000.mmc: mmc0: new HS400 MMC card at address 0001\n"
    "[    3.211000] mmcblk0: error -110 transferring data, sector 2048, nr 8\n"
    "[    3.212000] mmcblk0: error -110 transferring data, sector 2056, nr 8\n"
    "[    3.900000] i2c i2c-5: error -110 waiting for bus\n"
    "[    4.500000] Kernel panic - not syncing: VFS: Unable to mount root fs\n"
)

QCOM_UFS = (
    "Format: Log Type - Time(microsec) - Message - Optional Info\n"
    "[    1.002] qcom_scm: convention: SMC64\n"
    "[    3.211] ufshcd-qcom 1d84000.ufshc: ufs_init fail, link startup error -110\n"
)

CLEAN = "[    0.000000] Booting Linux on physical CPU 0x0\n[    1.000000] random: crng init done\n"


def write(tmp, name, data):
    p = os.path.join(tmp, name)
    Path(p).write_bytes(data if isinstance(data, bytes) else data.encode("utf-8"))
    return p


class TestAnalyzeText(unittest.TestCase):
    def test_storage_failure_in_a_mediatek_uart_log(self):
        r = la.analyze_text(MTK_UART, source="uart.txt")
        self.assertEqual(r.status, "FAIL")
        ids = {m.rule_id: m for m in r.matches}
        self.assertIn("STORAGE_IO_ERROR", ids)
        self.assertIn("KERNEL_PANIC_OOPS", ids)
        self.assertIn("I2C_BUS_TIMEOUT", ids)
        storage = ids["STORAGE_IO_ERROR"]
        self.assertEqual(storage.count, 2)                       # two mmcblk0 lines
        self.assertEqual(storage.line_no, 7)                     # first occurrence, 1-based
        self.assertIn("sector 2048", storage.line)
        self.assertEqual(r.soc, "MediaTek")
        self.assertTrue(r.soc_evidence)                          # the line that pointed to it
        self.assertEqual(r.lines, 10)

    def test_fatal_line_is_the_most_severe_and_latest(self):
        r = la.analyze_text(MTK_UART)
        # CRITICAL: storage (lines 7, 8) and kernel panic (line 10); the latest critical line is where boot stopped
        self.assertEqual(r.fatal_line_no, 10)
        self.assertIn("Kernel panic", r.fatal_line)
        self.assertEqual(r.fatal_rule_id, "KERNEL_PANIC_OOPS")

    def test_suspected_board_areas_most_severe_first(self):
        r = la.analyze_text(MTK_UART)
        storage = next(m for m in r.matches if m.rule_id == "STORAGE_IO_ERROR")
        self.assertEqual(storage.board_areas[0], "storage")
        self.assertEqual(r.board_areas[0], "firmware")       # the fatal kernel panic's area comes first
        self.assertIn("storage", r.board_areas)
        self.assertIn("peripherals", r.board_areas)          # the I2C timeout
        self.assertEqual(len(r.board_areas), len(set(r.board_areas)))
        text = "\n".join(format_log_analysis(r, "ar"))
        self.assertIn(t("board_area.storage", "ar"), text)

    def test_ansi_and_carriage_returns_are_cleaned(self):
        r = la.analyze_text(MTK_UART)
        self.assertFalse(any("\x1b" in m.line or "\r" in m.line for m in r.matches))

    def test_ufs_link_startup_failure_is_recognised(self):
        r = la.analyze_text(QCOM_UFS)
        self.assertEqual(r.status, "FAIL")
        self.assertIn("UFS_LINK_STARTUP_FAIL", [m.rule_id for m in r.matches])
        self.assertEqual(r.soc, "Qualcomm")

    def test_no_known_signature_is_unknown_not_pass(self):
        r = la.analyze_text(CLEAN)
        self.assertEqual(r.status, "UNKNOWN")
        self.assertEqual(r.matches, [])
        self.assertIsNone(r.fatal_line)
        self.assertEqual(r.diagnosis.key, "logscan.no_known_fault")

    def test_soc_is_none_without_keywords_and_ambiguous_is_said(self):
        self.assertIsNone(la.analyze_text(CLEAN).soc)
        mixed = "qcom,wdt init\nqcom rpm ok\nmtk_wdt init\nmediatek clk ok\n"
        r = la.analyze_text(mixed)
        self.assertIsNone(r.soc)                                 # 2 vs 2 hits: not guessed
        self.assertEqual({c for c, _ in r.soc_candidates}, {"Qualcomm", "MediaTek"})

    def test_results_without_a_match_still_have_evidence(self):
        # the rule is evidence on every result, also when nothing known was found
        clean = la.analyze_text(CLEAN, source="clean.log")
        self.assertTrue(clean.evidence)
        self.assertIn("Booting Linux", clean.evidence[0].raw_excerpt)          # what the log looked like
        self.assertIn("2 line(s)", clean.evidence[0].raw_excerpt)
        garbage = "".join(chr(c) for c in range(1, 32) if c not in (9, 10, 13)) * 50 + "\nok\n"
        unreadable = la.analyze_text(garbage, source="noise.log")
        self.assertEqual(unreadable.diagnosis.key, "logscan.mostly_unreadable")
        self.assertTrue(unreadable.evidence)
        self.assertIn("unreadable", unreadable.evidence[0].raw_excerpt)
        empty = la.analyze_text("", source="e.log")
        self.assertTrue(empty.evidence)

    def test_every_match_has_evidence_with_its_line_number(self):
        r = la.analyze_text(MTK_UART, source="uart.txt")
        self.assertTrue(r.evidence)
        self.assertTrue(any("line 7" in e.raw_excerpt and "mmcblk0" in e.raw_excerpt for e in r.evidence))
        self.assertTrue(all(e.source.startswith("uart.txt") for e in r.evidence))


class TestAnalyzeFile(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def test_reads_a_file(self):
        r = la.analyze_file(write(self.tmp.name, "uart.log", MTK_UART))
        self.assertEqual(r.status, "FAIL")
        self.assertEqual(r.source, "uart.log")
        self.assertFalse(r.truncated)

    def test_empty_file(self):
        r = la.analyze_file(write(self.tmp.name, "e.log", b""))
        self.assertEqual((r.status, r.diagnosis.key), ("UNKNOWN", "logscan.empty"))

    def test_mostly_garbage_is_said_not_judged(self):
        noise = bytes(range(256)) * 40                           # wrong baud rate looks like this
        r = la.analyze_file(write(self.tmp.name, "noise.log", noise))
        self.assertEqual(r.status, "UNKNOWN")
        self.assertEqual(r.diagnosis.key, "logscan.mostly_unreadable")

    def test_garbage_does_not_hide_a_real_fault_line(self):
        data = bytes(range(128, 256)) * 50 + b"\n[ 9.0] Kernel panic - not syncing: Fatal exception\n"
        r = la.analyze_file(write(self.tmp.name, "mixed.log", data))
        self.assertEqual(r.status, "FAIL")

    def test_large_file_keeps_the_end(self):
        head = ("[ 0.1] noise line\n" * 5000).encode()
        tail = b"[ 9.0] Watchdog bite! cpu 3\n"
        r = la.analyze_file(write(self.tmp.name, "big.log", head + tail), max_bytes=4096)
        self.assertTrue(r.truncated)                            # boot failures are at the end: keep the end
        self.assertIn("WATCHDOG_BITE", [m.rule_id for m in r.matches])

    def test_latin1_bytes_do_not_break_reading(self):
        r = la.analyze_file(write(self.tmp.name, "l1.log", b"caf\xe9 \xff\n" + MTK_UART.encode()))
        self.assertEqual(r.status, "FAIL")

    def test_missing_file_raises_oserror(self):
        with self.assertRaises(OSError):
            la.analyze_file(os.path.join(self.tmp.name, "nope.log"))

    def test_messages_render_in_both_languages(self):
        r = la.analyze_file(write(self.tmp.name, "uart.log", MTK_UART))
        for lang in ("ar", "en", "fr"):
            self.assertTrue(render(r.diagnosis, lang))


class TestCli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_cli(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            with unittest.mock.patch("sys.argv", ["main.py", *argv]):
                code = app_main.main()
        return code, out.getvalue()

    def test_analyze_log_prints_matches_and_fatal_line(self):
        path = write(self.tmp.name, "uart.log", MTK_UART)
        code, out = self.run_cli("--analyze-log", path, "--lang", "en")
        self.assertEqual(code, app_main.EXIT_OK)
        self.assertIn("FAIL", out)
        self.assertIn("Kernel panic", out)                       # the isolated fatal line
        self.assertIn("MediaTek", out)
        self.assertIn("STORAGE_IO_ERROR", out)

    def test_missing_file_is_exit_4(self):
        code, _ = self.run_cli("--analyze-log", os.path.join(self.tmp.name, "nope.log"), "--lang", "en")
        self.assertEqual(code, app_main.EXIT_INTERNAL)


class TestBoardTabButton(unittest.TestCase):
    """The board tab's 'Analyze Log File' handler, run on a stand-in for the window."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def run_handler(self, chosen_path):
        from ui.i18n import t
        from ui.tabs.board_diag import BoardDiagTabMixin

        class RunNow:
            def __init__(self, target=None, daemon=None, **kw):
                self.target = target

            def start(self):
                self.target()

        class Tab:
            current_lang = "en"
            logscan_text = object()

            def __init__(self):
                self.boxes, self.pills = [], {}

            def _tr(self, key, **kw):
                return t(key, "en", **kw)

            def _ensure_tab(self, key):
                pass

            def _set_box(self, box, text):
                self.boxes.append(text)

            def _set_pill(self, key, status):
                self.pills[key] = status

            def after(self, ms, fn):
                fn()

        tab = Tab()
        with unittest.mock.patch("ui.tabs.board_diag.filedialog.askopenfilename", return_value=chosen_path), \
                unittest.mock.patch("ui.tabs.board_diag.threading", SimpleNamespace(Thread=RunNow)):
            BoardDiagTabMixin.run_log_analysis_async(tab)
        return tab

    def test_shows_the_analysis_and_sets_the_pill(self):
        tab = self.run_handler(write(self.tmp.name, "uart.log", MTK_UART))
        self.assertEqual(tab.pills, {"logscan": "FAIL"})
        self.assertIn("Kernel panic", tab.boxes[-1])
        self.assertIn("MediaTek", tab.boxes[-1])

    def test_cancelled_dialog_does_nothing(self):
        tab = self.run_handler("")
        self.assertEqual((tab.boxes, tab.pills), ([], {}))

    def test_unreadable_file_is_reported_not_judged(self):
        tab = self.run_handler(os.path.join(self.tmp.name, "gone.log"))
        self.assertEqual(tab.pills, {"logscan": "UNKNOWN"})
        self.assertIn("Could not read", tab.boxes[-1])


import unittest.mock  # noqa: E402  (used through unittest.mock.patch above)

if __name__ == "__main__":
    unittest.main()
