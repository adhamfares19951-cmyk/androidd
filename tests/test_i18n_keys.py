"""
Translation coverage: fails if a key used in the code is missing in any language, if the languages
have different keys or placeholders, or if user-facing text is written directly in the code again.
"""

import ast
import glob
import os
import re
import string
import sys
import unittest

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, ROOT)

from ui.i18n import STRINGS, available_languages  # noqa: E402

CODE_FILES = sorted(glob.glob(os.path.join(ROOT, "core", "*.py")) + glob.glob(os.path.join(ROOT, "ui", "*.py")))
KEY_CALLS = {"M", "Msg", "t", "_tr", "tr"}
ARABIC = re.compile(r"[؀-ۿ]")


def _call_name(node: ast.Call) -> str:
    f = node.func
    if isinstance(f, ast.Name):
        return f.id
    if isinstance(f, ast.Attribute):
        return f.attr
    return ""


def _scan():
    """(static keys, dynamic key prefixes) used in code."""
    static, dynamic = {}, {}
    for path in CODE_FILES:
        with open(path, encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and _call_name(node) in KEY_CALLS and node.args:
                first = node.args[0]
                where = f"{os.path.relpath(path, ROOT)}:{node.lineno}"
                if isinstance(first, ast.Constant) and isinstance(first.value, str):
                    static.setdefault(first.value, where)
                elif isinstance(first, ast.JoinedStr) and first.values and isinstance(first.values[0], ast.Constant):
                    dynamic.setdefault(first.values[0].value, where)
    return static, dynamic


def _placeholders(text: str):
    return {name.split(".")[0].split("[")[0] for _, name, _, _ in string.Formatter().parse(text) if name}


class TestLocaleFiles(unittest.TestCase):
    def test_languages_available(self):
        self.assertEqual(set(available_languages()), {"ar", "en"})

    def test_same_keys_in_every_language(self):
        langs = available_languages()
        base = set(STRINGS[langs[0]])
        for lang in langs[1:]:
            self.assertEqual(sorted(base - set(STRINGS[lang])), [], f"missing in {lang}")
            self.assertEqual(sorted(set(STRINGS[lang]) - base), [], f"missing in {langs[0]}")

    def test_same_placeholders_and_no_empty_values(self):
        for key, en in STRINGS["en"].items():
            ar = STRINGS["ar"][key]
            self.assertTrue(en.strip() and ar.strip(), key)
            self.assertEqual(_placeholders(en), _placeholders(ar), key)


class TestKeysUsedInCode(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.static, cls.dynamic = _scan()

    def assertKey(self, key, where):
        for lang in available_languages():
            self.assertIn(key, STRINGS[lang], f"{key} (used at {where}) missing in {lang}")

    def test_static_keys_exist(self):
        self.assertGreater(len(self.static), 300)
        for key, where in self.static.items():
            self.assertKey(key, where)

    def test_dynamic_key_families_exist(self):
        from core.malware_hunter import OEM_DEBLOAT_PACKAGES, SENSITIVE_PERMISSIONS
        from core import jedec
        from core.board_diag import MotherboardDiagnostics
        families = {
            "camera.facing.": ["Front", "Back", "External", "UNKNOWN"],
            "keys.state.": ["PASS", "DOWN_NO_UP", "NOT_PRESSED", "UNKNOWN"],
            "feature.": ["DECLARED", "NOT_DECLARED", "UNKNOWN"],
            "wifi.driver.": ["HAL_FAILURE", "ENABLED", "UNKNOWN"],
            "bloat.desc.": list(OEM_DEBLOAT_PACKAGES),
            "bloat.impact.": sorted({v["battery_impact"] for v in OEM_DEBLOAT_PACKAGES.values()}),
            "bloat.safety.": sorted({v["safety"] for v in OEM_DEBLOAT_PACKAGES.values()}),
        }
        unknown = sorted(set(self.dynamic) - set(families))
        self.assertEqual(unknown, [], "dynamic key prefix without an enumerated test family")
        for prefix, codes in families.items():
            for code in codes:
                self.assertKey(prefix + code, self.dynamic.get(prefix, "family"))
        for key in list(SENSITIVE_PERMISSIONS.values()) + list(jedec.PRE_EOL_LABELS.values()):
            self.assertKey(key, "constant table")
        for _, _, diag_key, cause_keys in MotherboardDiagnostics._ABNORMAL_RULES:
            for key in [diag_key] + cause_keys:
                self.assertKey(key, "_ABNORMAL_RULES")

    def test_check_ids_have_names(self):
        """Every check_id default in a result dataclass has a translated display name."""
        for path in CODE_FILES:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name) and node.target.id == "check_id" \
                        and isinstance(node.value, ast.Constant):
                    self.assertKey(node.value.value, os.path.relpath(path, ROOT))
        from tests.fakes import FakeBridge
        from core.hardware_tester import HardwareTester
        for cid in HardwareTester(FakeBridge()).run_full_audit("s").check_statuses:
            self.assertKey(cid, "run_full_audit")


class TestNoTextInCode(unittest.TestCase):
    def test_no_arabic_literals_in_code(self):
        for path in CODE_FILES:
            with open(path, encoding="utf-8") as fh:
                lines = fh.readlines()
            for i, line in enumerate(lines, 1):
                self.assertIsNone(ARABIC.search(line), f"{os.path.relpath(path, ROOT)}:{i}")

    def test_no_language_ternaries_for_text(self):
        for path in CODE_FILES:
            with open(path, encoding="utf-8") as fh:
                tree = ast.parse(fh.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.IfExp):
                    test = ast.unparse(node.test)
                    if test in ("is_ar", "self.current_lang == 'ar'", "lang == 'ar'"):
                        for branch in (node.body, node.orelse):
                            if isinstance(branch, ast.JoinedStr) or (isinstance(branch, ast.Constant) and isinstance(branch.value, str)
                                                                     and not re.fullmatch(r"#[0-9a-fA-F]{6}|ar|en|rtl|ltr", branch.value)):
                                self.fail(f"{os.path.relpath(path, ROOT)}:{node.lineno} text chosen by language in code")


if __name__ == "__main__":
    unittest.main()
