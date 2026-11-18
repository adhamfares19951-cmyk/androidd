"""data/fault_signatures_kernel.json: rules taken from the error messages of real phone kernel drivers.

Every rule names the driver files and the exact format strings it was written from. The tests print each
format string the way the kernel would (with sample values, after a dmesg time stamp) and check that its
own rule matches it, that no other kernel rule does, and that the harmless lines next to it match nothing.
"""
import json
import os
import re
import unittest

from core.board_diag import load_fault_signature_rules
from core.log_analyzer import analyze_text

ROOT = os.path.join(os.path.dirname(__file__), "..")
PATH = os.path.join(ROOT, "data", "fault_signatures_kernel.json")
FIELDS = ("id", "category", "severity", "pattern", "description_en", "description_ar", "suspect_components",
          "suspect_components_ar", "action_en", "action_ar", "board_areas", "kernel_source")
AREAS = ("storage", "power", "peripherals", "modem", "soc_ram", "thermal", "firmware", "charging", "display")
ARABIC = re.compile("[" + chr(0x0600) + "-" + chr(0x06FF) + "]")


def load():
    with open(PATH, encoding="utf-8") as f:
        return json.load(f)["rules"]


def printed(fmt: str, url: str = "") -> str:
    """A kernel format string as the log shows it: placeholders filled, a dmesg time stamp and the driver's
    name in front (dev_err / pr_fmt print it)."""
    s = fmt.replace("\\n", "").replace("\\r", "")
    s = re.sub(r"(error|rc|ret)=%d", r"\1=-110", s)
    s = re.sub(r"SOH=%d", "SOH=62", s)
    s = re.sub(r"%[-0-9.]*(?:ll|l|z)?[dui]", "7", s)
    s = re.sub(r"%[-0-9.]*[xX]", "1f", s)
    s = s.replace("%s", "mtk_chg")
    driver = os.path.splitext(os.path.basename(url))[0] or "driver"
    return f"[  152.348211] {driver}: {s}"


class TestRulesAreWellFormed(unittest.TestCase):
    def test_fields(self):
        rules = load()
        with open(os.path.join(ROOT, "data", "fault_signatures.json"), encoding="utf-8") as f:
            base_ids = {r["id"] for r in json.load(f)["rules"]}
        self.assertEqual(len({r["id"] for r in rules}), len(rules))
        for r in rules:
            for f in FIELDS:
                self.assertTrue(r.get(f), (r["id"], f))
            self.assertNotIn(r["id"], base_ids)
            self.assertIn(r["severity"], ("CRITICAL", "HIGH", "MEDIUM", "LOW"))
            for a in r["board_areas"]:
                self.assertIn(a, AREAS, r["id"])
            self.assertEqual(len(r["suspect_components"]), len(r["suspect_components_ar"]), r["id"])
            for part in r["suspect_components_ar"] + [r["description_ar"], r["action_ar"]]:
                self.assertRegex(part, ARABIC, r["id"])
            self.assertTrue(r["action_en"].startswith("Possible causes"), r["id"])
            self.assertTrue(r["action_ar"].startswith("أسباب محتملة"), r["id"])
            for ks in r["kernel_source"]:
                self.assertRegex(ks["url"], r"^https://github\.com/LineageOS/android_kernel_[a-z0-9_]+/blob/", r["id"])
                self.assertTrue(ks["message"], r["id"])

    def test_loaded_with_the_base_rules(self):
        ids = [r["id"] for r in load_fault_signature_rules()]
        self.assertIn("KERNEL_PANIC_OOPS", ids)
        self.assertIn("USB_CC_VBUS_SHORT", ids)
        self.assertEqual(len(ids), len(set(ids)))


class TestEachDriverMessage(unittest.TestCase):
    def test_every_source_message_matches_its_own_rule_only(self):
        rules = load()
        for r in rules:
            for ks in r["kernel_source"]:
                line = printed(ks["message"], ks["url"])
                hits = [x["id"] for x in rules if re.search(x["pattern"], line, re.I)]
                self.assertEqual(hits, [r["id"]], f"{line!r}")

    HARMLESS = [
        "[ 10.1] rt_pd_manager: tcpc_notifier_call Water is removed in KPOC",
        "[ 10.2] qpnp_qg: SOH update: SOH=96 esr_actual=120 esr_nominal=118",
        "[ 10.3] qpnp_qg: SOH update: SOH=100 esr_actual=110 esr_nominal=118",
        "[ 10.4] fpc1022 fpc_irq: fpc_probe, detected sensor, error=0",
        "[ 10.5] mtk_charger: [SW_JEITA] Battery Temperature not recovery to normal temperature charging mode yet!!",
        "[ 10.6] msdc0 -> !!! Set<sd clock=200MHz, timing=hs400>",
        "[ 10.7] mmc0: new HS400 MMC card at address 0001",
        "[ 10.8] max77705: fac_water_enable(0), skip",
        "[ 10.9] goodix_ts: goodix_hw_ops->reset done",
        "[ 11.0] qpnp_qg: batt_id_mv=210, batt_id_ohm=68000",
        "[ 11.1] mtk_charger: battery recharge",
    ]

    def test_harmless_lines_match_nothing(self):
        rules = load()
        for line in self.HARMLESS:
            self.assertEqual([r["id"] for r in rules if re.search(r["pattern"], line, re.I)], [], line)

    def test_low_soh_threshold(self):
        r = next(x for x in load() if x["id"] == "BATTERY_GAUGE_LOW_SOH")
        for soh, hit in ((69, True), (5, True), (70, False), (85, False)):
            self.assertEqual(bool(re.search(r["pattern"], f"SOH update: SOH={soh} esr_actual=1 esr_nominal=1")), hit, soh)


class TestInLogAnalysis(unittest.TestCase):
    def test_a_samsung_port_short_is_found_with_its_areas(self):
        log = "\n".join(["[    1.0] Booting Linux on physical CPU 0x0",
                         "[   45.2] max77705_muic: max77705_muic_afc_handle: CC-Vbus Short case",
                         "[   46.0] healthd: battery l=54 v=3912"])
        r = analyze_text(log, "dmesg.txt")
        self.assertEqual(r.status, "FAIL")
        m = next(x for x in r.matches if x.rule_id == "USB_CC_VBUS_SHORT")
        self.assertEqual(m.line_no, 2)
        self.assertEqual(m.board_areas[0], "charging")
        self.assertIn("charging", r.board_areas)


if __name__ == "__main__":
    unittest.main()
