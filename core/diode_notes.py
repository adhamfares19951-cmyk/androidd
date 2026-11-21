"""Diode-mode notebook: the technician's own hand measurements (multimeter in diode mode, red probe to ground,
black probe on the test point) per phone model and test point, for a known-good reference board and for the
board under test.

The readings are the technician's, not the program's: the comparison shows both numbers and their difference and
marks large gaps, an open line on one side only, or a reading near 0 mV (often a short to ground). It is a mark
to look closer, never a PASS/FAIL verdict. Stored locally in one JSON file; nothing leaves the PC."""
import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

from app_paths import user_data_dir
from core.model_db import normalize_model
from core.result import M

BOARDS = ("reference", "test")
DIFF_MARK_PCT = 15.0          # a gap of this many percent or more is marked "differs"
NEAR_SHORT_MV = 50.0          # a reading below this (when the good board reads more) is marked "near short"
MAX_MV = 3000.0               # diode mode on a phone board stays well below 3 V
_OPEN = {"ol", "o.l", "o.l.", "open", "1"}         # "1" alone: what many cheap meters show for an open line
UNMARKED = ("same", "both_open")                  # comparison kinds that get no [!] mark


def default_path() -> str:
    return os.path.join(user_data_dir(), "diode_notes.json")


def parse_reading(text: str) -> Optional[Tuple[Optional[float], bool]]:
    """(millivolts, False) for a value, (None, True) for an open line, None when it is not a reading.
    "452" and "452 mV" are millivolts; "0.452", "0,452" and "1.2 V" are volts."""
    s = (text or "").strip().lower().replace(",", ".")
    if not s:
        return None
    if s in _OPEN:
        return None, True
    m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*(mv|v)?", s)
    if not m:
        return None
    value = float(m.group(1))
    unit = m.group(2)
    if unit == "v" or (unit is None and "." in m.group(1)):
        value *= 1000.0
    if not 0.0 <= value <= MAX_MV:
        return None
    return round(value, 1), False


def format_reading(r: Optional[Dict[str, Any]]) -> str:
    if not r:
        return "-"
    return "OL" if r.get("ol") else f"{r.get('mv'):g} mV"


@dataclass
class PointComparison:
    point: str
    reference: Optional[Dict[str, Any]]
    test: Optional[Dict[str, Any]]
    kind: str            # same / both_open / differs / open_vs_value / near_short / only_reference / only_test
    diff_mv: Optional[float] = None   # test minus reference
    diff_pct: Optional[float] = None


class DiodeNotebook:
    def __init__(self, path: Optional[str] = None):
        self.path = path or default_path()

    # ------------------------------------------------------------ file
    def _read(self) -> Tuple[Optional[Dict[str, Any]], Optional[str]]:
        """(data, None), or (None, error) when the file exists but cannot be read."""
        if not os.path.exists(self.path):
            return {"schema": 1, "boards": {}}, None
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict) or not isinstance(data.get("boards", {}), dict):
                raise ValueError("not a notebook object")
            data.setdefault("boards", {})
            return data, None
        except (OSError, ValueError) as e:
            return None, str(e)

    def _write(self, data: Dict[str, Any]) -> Optional[str]:
        folder = os.path.dirname(self.path) or "."
        tmp = ""
        try:
            os.makedirs(folder, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=folder, suffix=".part")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.path)
            return None
        except OSError as e:
            if tmp and os.path.exists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            return str(e)

    # ------------------------------------------------------------ use
    def save(self, model: str, point: str, reading: str, board: str, note: str = ""):
        """Store one reading (a new one for the same point and board replaces the old). Returns (ok, Msg)."""
        model, point = (model or "").strip(), (point or "").strip()
        if not model:
            return False, M("diode.no_model")
        if not point:
            return False, M("diode.no_point")
        if board not in BOARDS:
            return False, M("diode.bad_board", board=board or "-")
        parsed = parse_reading(reading)
        if parsed is None:
            return False, M("diode.bad_reading", text=(reading or "").strip() or "-")
        data, error = self._read()
        if data is None:
            return False, M("diode.unreadable", path=self.path, error=error)
        key = normalize_model(model)
        entry = data["boards"].setdefault(key, {"model": model, "points": {}})
        mv, is_open = parsed
        entry["points"].setdefault(point, {})[board] = {
            "mv": mv, "ol": is_open, "note": (note or "").strip(), "date": time.strftime("%Y-%m-%d %H:%M")}
        error = self._write(data)
        if error:
            return False, M("diode.save_failed", error=error)
        return True, M("diode.saved", point=point, board=M(f"diode.board.{board}"),
                       value=format_reading(entry["points"][point][board]), model=model)

    def points(self, model: str) -> Dict[str, Dict[str, Any]]:
        data, _ = self._read()
        if not data:
            return {}
        return dict(data["boards"].get(normalize_model(model), {}).get("points", {}))

    def models(self) -> List[str]:
        data, _ = self._read()
        return [b.get("model", k) for k, b in (data or {}).get("boards", {}).items()]

    def compare(self, model: str) -> List[PointComparison]:
        rows = []
        for point, readings in sorted(self.points(model).items()):
            ref, test = readings.get("reference"), readings.get("test")
            rows.append(_compare(point, ref, test))
        return rows


def _compare(point: str, ref: Optional[Dict[str, Any]], test: Optional[Dict[str, Any]]) -> PointComparison:
    if ref is None:
        return PointComparison(point, ref, test, "only_test")
    if test is None:
        return PointComparison(point, ref, test, "only_reference")
    if ref.get("ol") or test.get("ol"):
        return PointComparison(point, ref, test, "both_open" if ref.get("ol") and test.get("ol") else "open_vs_value")
    r, t = float(ref["mv"]), float(test["mv"])
    diff = round(t - r, 1)
    pct = round(diff / r * 100.0, 1) if r else None
    if t < NEAR_SHORT_MV <= r:
        kind = "near_short"
    elif pct is None:
        kind = "same" if t == r else "differs"
    else:
        kind = "differs" if abs(pct) >= DIFF_MARK_PCT else "same"
    return PointComparison(point, ref, test, kind, diff, pct)
