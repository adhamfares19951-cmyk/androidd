"""data/fault_signatures.json: every rule is well formed, its patterns match what they mean (and not the common
harmless lines next to it), and its texts give possible causes, never a definite diagnosis."""
import json
import os
import re
import unittest

PATH = os.path.join(os.path.dirname(__file__), "..", "data", "fault_signatures.json")
FIELDS = ("id", "category", "severity", "pattern", "description_en", "description_ar",
          "suspect_components", "action_en", "action_ar")
# One log line cannot show that a part "is failing" or "is dying"; these phrases claimed it.
DEFINITE_CLAIMS = ("is failing", "chip failing", "chip is degrading", "permanent read-only",
                   "تحتضر", "قاربت على التلف", "مؤشر على تلف", "الفحص يشير", "توقف المعالج لحماية العتاد",
                   "تعليق كامل", "إعادة تشغيل متكررة", ">85", "find shorted")


def load():
    with open(PATH, encoding="utf-8") as f:
        return json.load(f)["rules"]


def matching(line):
    return [r["id"] for r in load() if re.search(r["pattern"], line, re.I)]


class TestRulesAreWellFormed(unittest.TestCase):
    def test_fields_ids_and_patterns(self):
        rules = load()
        self.assertEqual(len({r["id"] for r in rules}), len(rules))
        for r in rules:
            for f in FIELDS:
                self.assertTrue(r.get(f), (r.get("id"), f))
            self.assertIn(r["severity"], ("CRITICAL", "HIGH", "MEDIUM", "LOW"), r["id"])
            re.compile(r["pattern"])


BOARD_AREAS = ("storage", "power", "peripherals", "modem", "soc_ram", "thermal", "firmware")


class TestBoardAreas(unittest.TestCase):
    def test_every_rule_names_its_board_areas(self):
        for r in load():
            areas = r.get("board_areas")
            self.assertTrue(areas, r["id"])
            self.assertEqual(len(areas), len(set(areas)), r["id"])
            for a in areas:
                self.assertIn(a, BOARD_AREAS, f"{r['id']}: {a}")

    def test_software_first_where_it_is_the_common_cause(self):
        rules = {r["id"]: r for r in load()}
        self.assertEqual(rules["KERNEL_PANIC_OOPS"]["board_areas"][0], "firmware")
        self.assertEqual(rules["STORAGE_IO_ERROR"]["board_areas"][0], "storage")
        self.assertEqual(rules["UVLO_BROWNOUT"]["board_areas"][0], "power")


class TestWording(unittest.TestCase):
    def test_actions_give_possible_causes(self):
        for r in load():
            self.assertIn("possible cause", r["action_en"].lower(), r["id"])
            self.assertIn("محتمل", r["action_ar"], r["id"])

    def test_no_definite_diagnosis_from_a_log_line(self):
        for r in load():
            text = " ".join([r["description_en"], r["description_ar"], r["action_en"], r["action_ar"]])
            for claim in DEFINITE_CLAIMS:
                self.assertNotIn(claim, text, f"{r['id']}: {claim}")


class TestPatterns(unittest.TestCase):
    def test_probe_defer_and_longer_codes_are_not_i2c_faults(self):
        # -517 (EPROBE_DEFER) is a normal "try again later" at boot; it used to match "error -5"
        self.assertEqual(matching("[ 1.234] i2c i2c-1: probe of 1-0038 failed with error -517"), [])
        self.assertEqual(matching("[ 2.000] i2c i2c-3: error -1100 while reading"), [])

    def test_real_i2c_errors_still_match(self):
        self.assertIn("I2C_IO_ERROR", matching("[ 1.300] i2c_geni 4a84000.i2c: i2c error -5"))
        self.assertIn("I2C_IO_ERROR", matching("[ 1.301] i2c i2c-2: transfer failed"))
        self.assertIn("I2C_BUS_TIMEOUT", matching("[ 3.900] i2c i2c-5: error -110 waiting for bus"))

    def test_other_rules_still_match_their_lines(self):
        cases = {
            "KERNEL_PANIC_OOPS": "[ 4.5] Kernel panic - not syncing: VFS: Unable to mount root fs",
            "WATCHDOG_BITE": "[ 9.0] Watchdog bite! cpu 3",
            "STORAGE_IO_ERROR": "[ 3.2] mmcblk0: error -110 transferring data, sector 2048",
            "UFS_LINK_STARTUP_FAIL": "[ 3.2] ufshcd-qcom 1d84000.ufshc: ufs_init fail, link startup error -110",
            "THERMAL_SHUTDOWN": "[ 8.0] thermal: critical temperature reached, shutting down",
            "UVLO_BROWNOUT": "[ 5.0] pmic: uvlo_detected, vph_pwr collapse",
            "MODEM_RIL_CRASH": "[ 7.0] ModemSSR: modem crashed",
        }
        for rule_id, line in cases.items():
            self.assertIn(rule_id, matching(line), line)


if __name__ == "__main__":
    unittest.main()
