"""
Per-model manufacturer specifications (data/device_models.json + the technician's own file).

The database never produces a reading. It only supplies a documented reference value (for now the
battery's design capacity) that a real reading from the phone is compared with. Rules:
- every entry must name its source (http/https URL); entries without one are rejected;
- values must be plausible (battery 500..30000 mAh); anything else is rejected, never guessed;
- the technician's file (%LOCALAPPDATA%\\AndroidDoctorPro\\device_models.json) is read after the
  bundled file, so it can add models or correct an entry;
- rejected entries are logged and listed in ModelDatabase.errors.
"""

import json
import logging
import os
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple

from app_paths import user_data_dir

logger = logging.getLogger("ModelDB")

BUNDLED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "device_models.json")
USER_FILE_NAME = "device_models.json"
MIN_MAH, MAX_MAH = 500, 30000


@dataclass(frozen=True)
class ModelInfo:
    model: str
    name: str
    source: str
    checked: str = ""
    typical_mah: Optional[int] = None
    rated_mah: Optional[int] = None
    origin: str = ""  # file the entry came from

    def design_capacity(self) -> Optional[Tuple[int, str]]:
        """(mAh, "rated" | "typical"): rated (minimum) first, so a battery is never judged against
        a capacity higher than the manufacturer guarantees."""
        if self.rated_mah:
            return self.rated_mah, "rated"
        if self.typical_mah:
            return self.typical_mah, "typical"
        return None


def normalize_model(model: str) -> str:
    """'sm-a525f/ds ' -> 'SM-A525F' (dual-SIM suffixes do not change the hardware spec)."""
    return (model or "").strip().upper().split("/")[0].strip()


def _mah(value) -> Optional[int]:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if MIN_MAH <= value <= MAX_MAH else None


class ModelDatabase:
    def __init__(self, paths: Optional[List[str]] = None):
        self.paths = paths if paths is not None else [BUNDLED_PATH, os.path.join(user_data_dir(), USER_FILE_NAME)]
        self.models: Dict[str, ModelInfo] = {}
        self.errors: List[str] = []
        for path in self.paths:
            self._load(path)

    def _reject(self, path: str, key: str, why: str) -> None:
        msg = f"{os.path.basename(path)}: {key}: {why}"
        self.errors.append(msg)
        logger.warning(f"Model database entry rejected: {msg}")

    def _load(self, path: str) -> None:
        if not os.path.isfile(path):
            return
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            self._reject(path, "*", f"unreadable ({e})")
            return
        models = doc.get("models") if isinstance(doc, dict) else None
        if doc.get("schema") != 1 or not isinstance(models, dict):
            self._reject(path, "*", "not a schema 1 model file")
            return
        for raw_key, entry in models.items():
            key = normalize_model(raw_key)
            if not key or not isinstance(entry, dict):
                self._reject(path, str(raw_key), "empty model or entry")
                continue
            source = entry.get("source")
            if not isinstance(source, str) or not source.startswith(("https://", "http://")):
                self._reject(path, key, "missing source URL")
                continue
            name = entry.get("name")
            if not isinstance(name, str) or not name.strip():
                self._reject(path, key, "missing name")
                continue
            battery = entry.get("battery") or {}
            typical, rated = _mah(battery.get("typical_mah")), _mah(battery.get("rated_mah"))
            if ("typical_mah" in battery and typical is None) or ("rated_mah" in battery and rated is None):
                self._reject(path, key, f"battery capacity outside {MIN_MAH}..{MAX_MAH} mAh")
                continue
            if typical and rated and rated > typical:
                self._reject(path, key, "rated capacity above typical capacity")
                continue
            self.models[key] = ModelInfo(model=key, name=name.strip(), source=source,
                                         checked=str(entry.get("checked", "")), typical_mah=typical,
                                         rated_mah=rated, origin=path)

    def lookup(self, model: str) -> Optional[ModelInfo]:
        return self.models.get(normalize_model(model))


_default: Optional[ModelDatabase] = None


def default_db() -> ModelDatabase:
    global _default
    if _default is None:
        _default = ModelDatabase()
    return _default
