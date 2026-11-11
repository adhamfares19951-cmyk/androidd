"""Model database: sourced manufacturer specs only, and how the battery capacity check uses them."""
import json
import os
import tempfile
import unittest

from core.board_diag import MotherboardDiagnostics
from core.model_db import BUNDLED_PATH, ModelDatabase, normalize_model
from database.db_manager import DatabaseManager
from tests.fakes import FakeBridge

SRC = "https://example.com/spec"


def _entry(name="Phone X", typical=None, rated=None, source=SRC):
    battery = {}
    if typical is not None:
        battery["typical_mah"] = typical
    if rated is not None:
        battery["rated_mah"] = rated
    e = {"name": name, "battery": battery}
    if source is not None:
        e["source"] = source
    return e


class _Case(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()

    def tearDown(self):
        DatabaseManager.reset_instance()
        self.tmp.cleanup()

    def write(self, name, models, schema=1):
        path = os.path.join(self.tmp.name, name)
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"schema": schema, "models": models}, f)
        return path


class LoaderTests(_Case):
    def test_bundled_file_is_valid_and_fully_sourced(self):
        db = ModelDatabase([BUNDLED_PATH])
        self.assertEqual(db.errors, [])
        self.assertGreaterEqual(len(db.models), 3)
        for info in db.models.values():
            self.assertTrue(info.source.startswith("https://"), info.model)
            self.assertIsNotNone(info.design_capacity(), info.model)

    def test_dual_sim_suffix_and_case_do_not_matter(self):
        db = ModelDatabase([self.write("m.json", {"SM-A525F": _entry(typical=4500, rated=4370)})])
        self.assertEqual(normalize_model(" sm-a525f/ds "), "SM-A525F")
        self.assertEqual(db.lookup("sm-a525f/DS").name, "Phone X")
        self.assertIsNone(db.lookup("SM-A526B"))
        self.assertIsNone(db.lookup(""))

    def test_rated_capacity_is_preferred(self):
        db = ModelDatabase([self.write("m.json", {"A": _entry(typical=4500, rated=4370), "B": _entry(typical=5000)})])
        self.assertEqual(db.lookup("A").design_capacity(), (4370, "rated"))
        self.assertEqual(db.lookup("B").design_capacity(), (5000, "typical"))

    def test_bad_entries_are_rejected_never_guessed(self):
        path = self.write("m.json", {
            "NOSRC": _entry(typical=4000, source=None),
            "BADURL": _entry(typical=4000, source="samsung.com"),
            "NONAME": _entry(name="", typical=4000),
            "TINY": _entry(typical=100),
            "HUGE": _entry(typical=99999),
            "TEXT": _entry(typical="4500"),
            "BOOL": _entry(typical=True),
            "SWAP": _entry(typical=4000, rated=4500),
            "OK": _entry(typical=4000),
        })
        db = ModelDatabase([path])
        self.assertEqual(set(db.models), {"OK"})
        self.assertEqual(len(db.errors), 8)

    def test_broken_files_load_nothing(self):
        bad_json = os.path.join(self.tmp.name, "bad.json")
        with open(bad_json, "w") as f:
            f.write("{not json")
        db = ModelDatabase([bad_json, self.write("old.json", {"A": _entry(typical=4000)}, schema=2),
                            os.path.join(self.tmp.name, "missing.json")])
        self.assertEqual(db.models, {})
        self.assertEqual(len(db.errors), 2)  # a missing file is simply absent, not an error

    def test_user_file_overrides_and_extends(self):
        bundled = self.write("bundled.json", {"A": _entry(name="Old", typical=4000)})
        user = self.write("user.json", {"A": _entry(name="Fixed", typical=4100), "B": _entry(typical=3000)})
        db = ModelDatabase([bundled, user])
        self.assertEqual(db.lookup("A").name, "Fixed")
        self.assertEqual(db.lookup("A").typical_mah, 4100)
        self.assertIsNotNone(db.lookup("B"))


class BatteryCapacityTests(_Case):
    """charge_full is always the phone's own reading; the spec only replaces a missing design value."""

    def pmic(self, routes, model="SM-A525F", models=None):
        db_path = self.write("models.json", models if models is not None else
                             {"SM-A525F": _entry(name="Samsung Galaxy A52", typical=4500, rated=4370)})
        bridge = FakeBridge(routes, props={"ro.product.model": model} if model else {})
        board = MotherboardDiagnostics(bridge, db=DatabaseManager(os.path.join(self.tmp.name, "t.db")),
                                       model_db=ModelDatabase([db_path]))
        return board.analyze_pmic("serial")

    DENIED_DESIGN = ("battery/charge_full_design", (1, "", "cat: charge_full_design: Permission denied"))

    def test_spec_fills_missing_design_capacity(self):
        p = self.pmic(dict([self.DENIED_DESIGN, ("battery/charge_full", (0, "3933000", ""))]))
        self.assertEqual(p.design_capacity_source, "model_db")
        self.assertEqual(p.model_spec_name, "Samsung Galaxy A52")
        self.assertEqual(p.true_health_pct, 90.0)  # 3933 / 4370 rated, not / 4500 typical
        self.assertEqual(p.health_verdict, "PASS")
        spec_ev = [e for e in p.evidence if e.source.startswith("model database")]
        self.assertTrue(spec_ev and SRC in spec_ev[0].raw_excerpt)
        self.assertTrue(any("rated capacity 4370 mAh" in e.source for e in p.evidence))

    def test_spec_can_give_fail(self):
        p = self.pmic(dict([self.DENIED_DESIGN, ("battery/charge_full", (0, "3000000", ""))]))
        self.assertEqual(p.health_verdict, "FAIL")

    def test_no_reading_means_no_verdict_even_with_spec(self):
        p = self.pmic(dict([self.DENIED_DESIGN,
                            ("battery/charge_full", (1, "", "cat: charge_full: Permission denied"))]))
        self.assertIsNone(p.true_health_pct)
        self.assertEqual(p.health_verdict, "NEEDS_ROOT")
        self.assertNotEqual(p.design_capacity_source, "model_db")

    def test_unknown_model_keeps_old_behaviour(self):
        routes = dict([self.DENIED_DESIGN, ("battery/charge_full", (0, "3933000", ""))])
        for model in ("SM-XXXX", ""):
            p = self.pmic(routes, model=model)
            self.assertIsNone(p.true_health_pct)
            self.assertEqual(p.health_verdict, "NEEDS_ROOT")
            self.assertEqual(p.model_spec_name, "")

    def test_kernel_design_wins_and_big_difference_is_noted(self):
        routes = dict([("battery/charge_full_design", (0, "3000000", "")), ("battery/charge_full", (0, "2900000", ""))])
        p = self.pmic(routes)
        self.assertEqual(p.design_capacity_source, "kernel")
        self.assertEqual(p.true_health_pct, 96.7)  # 2900 / 3000 from the kernel
        notes = [e for e in p.evidence if e.source.startswith("model database") and "differs" in e.raw_excerpt]
        self.assertEqual(len(notes), 1)
        self.assertIn("non-original battery", notes[0].raw_excerpt)

    def test_kernel_design_close_to_spec_adds_no_note(self):
        routes = dict([("battery/charge_full_design", (0, "4400000", "")), ("battery/charge_full", (0, "4000000", ""))])
        p = self.pmic(routes)
        self.assertFalse([e for e in p.evidence if "differs" in e.raw_excerpt])


if __name__ == "__main__":
    unittest.main()
