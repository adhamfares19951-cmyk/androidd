"""Diode-mode notebook: readings a technician measures by hand, per model and test point, for a known-good
reference board and for the board under test. The comparison shows the numbers and marks large gaps; it never
turns a hand measurement into a PASS/FAIL verdict."""
import json
import os
import tempfile
import unittest

from core.diode_notes import DiodeNotebook, parse_reading, DIFF_MARK_PCT, NEAR_SHORT_MV


class ParseTests(unittest.TestCase):
    def test_millivolts_volts_and_open_line(self):
        self.assertEqual(parse_reading("452"), (452.0, False))
        self.assertEqual(parse_reading("452 mV"), (452.0, False))
        self.assertEqual(parse_reading("0.452"), (452.0, False))           # a meter showing volts
        self.assertEqual(parse_reading("0,452"), (452.0, False))           # decimal comma
        self.assertEqual(parse_reading("1.2 V"), (1200.0, False))
        self.assertEqual(parse_reading("0"), (0.0, False))                  # a dead short
        for text in ("OL", "ol", "O.L", "open", "1"):                       # "1" alone: many meters' open reading
            self.assertEqual(parse_reading(text), (None, True), text)

    def test_nonsense_is_refused(self):
        for text in ("", "abc", "-5", "99999", "4.5.6"):
            self.assertIsNone(parse_reading(text), text)


class NotebookTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = os.path.join(self.tmp.name, "diode_notes.json")
        self.nb = DiodeNotebook(self.path)

    def tearDown(self):
        self.tmp.cleanup()

    def save(self, point, value, board, model="SM-A525F"):
        ok, msg = self.nb.save(model, point, value, board)
        self.assertTrue(ok, msg)
        return msg

    def test_saved_readings_survive_a_new_notebook(self):
        msg = self.save("VBAT", "0.412", "reference")
        self.assertEqual(msg.key, "diode.saved")
        self.save("VBAT", "405", "test")
        again = DiodeNotebook(self.path)
        point = again.points("sm-a525f")["VBAT"]                            # the model is matched like elsewhere
        self.assertEqual(point["reference"]["mv"], 412.0)
        self.assertEqual(point["test"]["mv"], 405.0)

    def test_comparison_marks_only_large_gaps_and_mismatches(self):
        for point, ref, test in (("VBAT", "412", "405"),          # 1.7 %: not marked
                                 ("VDD_CPU", "380", "300"),       # 21 %: marked
                                 ("USB_D+", "OL", "520"),         # open on the good board, a value here
                                 ("VPH_PWR", "350", "4"),         # near 0 mV: often a short to ground
                                 ("MIC_BIAS", "OL", "OL")):
            self.save(point, ref, "reference")
            self.save(point, test, "test")
        self.save("ONLY_REF", "600", "reference")
        self.save("ONLY_TEST", "610", "test")
        rows = {r.point: r for r in self.nb.compare("SM-A525F")}
        self.assertEqual(rows["VBAT"].kind, "same")
        self.assertEqual(rows["VBAT"].diff_mv, -7.0)
        self.assertEqual(rows["VDD_CPU"].kind, "differs")
        self.assertGreaterEqual(abs(rows["VDD_CPU"].diff_pct), DIFF_MARK_PCT)
        self.assertEqual(rows["USB_D+"].kind, "open_vs_value")
        self.assertEqual(rows["VPH_PWR"].kind, "near_short")
        self.assertEqual(rows["MIC_BIAS"].kind, "both_open")         # open on both boards: not marked
        self.assertEqual(rows["ONLY_REF"].kind, "only_reference")
        self.assertEqual(rows["ONLY_TEST"].kind, "only_test")
        self.assertLess(NEAR_SHORT_MV, 100)
        for r in rows.values():
            self.assertFalse(hasattr(r, "status"))                          # no PASS/FAIL from a hand reading

    def test_bad_input_is_refused_and_nothing_is_written(self):
        for args, key in ((("", "VBAT", "400", "test"), "diode.no_model"),
                          (("SM-A525F", " ", "400", "test"), "diode.no_point"),
                          (("SM-A525F", "VBAT", "abc", "test"), "diode.bad_reading"),
                          (("SM-A525F", "VBAT", "400", "other"), "diode.bad_board")):
            ok, msg = self.nb.save(*args)
            self.assertFalse(ok)
            self.assertEqual(msg.key, key)
        self.assertFalse(os.path.exists(self.path))

    def test_an_unreadable_notebook_is_not_overwritten(self):
        with open(self.path, "w", encoding="utf-8") as f:
            f.write("{ broken")
        nb = DiodeNotebook(self.path)
        ok, msg = nb.save("SM-A525F", "VBAT", "400", "test")
        self.assertFalse(ok)
        self.assertEqual(msg.key, "diode.unreadable")
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(f.read(), "{ broken")

    def test_models_are_kept_apart(self):
        self.save("VBAT", "400", "reference", model="SM-A525F")
        self.save("VBAT", "500", "reference", model="SM-A125F")
        self.assertEqual(self.nb.points("SM-A525F")["VBAT"]["reference"]["mv"], 400.0)
        self.assertEqual(sorted(self.nb.models()), ["SM-A125F", "SM-A525F"])
        with open(self.path, encoding="utf-8") as f:
            self.assertEqual(json.load(f)["schema"], 1)


if __name__ == "__main__":
    unittest.main()
