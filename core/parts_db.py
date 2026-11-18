"""
What each phone model is built with (data/parts_db.json, made by tools/build_parts_db.py from LineageOS
public data: the device wiki and each model's firmware file list).

A model is found by its model number (ro.product.model, e.g. SM-G973F) or, when the database has no
model numbers for it, by its codename (ro.product.device, e.g. lancelot). Nothing is guessed: a phone
whose model and codename are both unknown has no entry.

Meaning of the lists (shown with every result that uses them):
- camera_sensors: sensors the model's factory firmware ships tuning files for. Several per camera are
  normal: makers buy the same camera from two or three module suppliers.
- platform_camera_sensors: sensors found only in a firmware tree shared with sibling models: weaker.
- fingerprint_makers: fingerprint drivers in the firmware ("device" = this model's own tree).
- An empty list means "not known", never "absent".
"""
import json
import logging
import os
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from core.model_db import normalize_model

logger = logging.getLogger("PartsDB")

BUNDLED_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data", "parts_db.json")


@dataclass(frozen=True)
class PartsEntry:
    key: str
    codename: str
    name: str
    vendor: str
    models: tuple
    stock_codenames: tuple
    soc: str
    battery_mah: Optional[int]
    battery_by_variant: Dict[str, int]
    screens: Dict[str, Dict[str, Any]]
    cameras: tuple
    peripherals: tuple
    camera_sensors: tuple            # ((sensor, module or None), ...)
    platform_camera_sensors: tuple
    fingerprint_makers: tuple
    fingerprint_from: Optional[str]  # "device" / "platform" / None
    nfc_chips: tuple
    download_boot: Optional[str]
    recovery_boot: Optional[str]
    source_wiki: str
    source_tree: Optional[str]
    matched_by: str = ""             # "model number" / "codename" (set by lookup)
    variant_names: tuple = ()        # other names of the same hardware when a codename matched several variants

    @property
    def display_name(self) -> str:
        return f"{self.vendor} {self.name}".strip()

    @property
    def display_names(self) -> str:
        """'Motorola moto g9 play / Motorola moto g9' when the codename names several variants of one phone."""
        return " / ".join((self.display_name,) + tuple(self.variant_names))

    def sensor_names(self) -> set:
        return {s for s, _ in self.camera_sensors}

    def platform_sensor_names(self) -> set:
        return {s for s, _ in self.platform_camera_sensors}

    def modules_of(self, sensor: str) -> List[str]:
        return sorted({m for s, m in self.camera_sensors + self.platform_camera_sensors if s == sensor and m})


def _entry(d: Dict[str, Any]) -> PartsEntry:
    src = d.get("source") or {}

    def sensors(key):
        return tuple((x["sensor"], x.get("module")) for x in d.get(key) or [] if x.get("sensor"))

    return PartsEntry(
        key=d["key"], codename=d.get("codename", ""), name=d.get("name", ""), vendor=d.get("vendor", ""),
        models=tuple(d.get("models") or []), stock_codenames=tuple(d.get("stock_codenames") or []),
        soc=d.get("soc") or "",
        battery_mah=d.get("battery_mah"), battery_by_variant=dict(d.get("battery_by_variant") or {}),
        screens=dict(d.get("screens") or {}), cameras=tuple(d.get("cameras") or []),
        peripherals=tuple(d.get("peripherals") or []),
        camera_sensors=sensors("camera_sensors"), platform_camera_sensors=sensors("platform_camera_sensors"),
        fingerprint_makers=tuple(d.get("fingerprint_makers") or []), fingerprint_from=d.get("fingerprint_from"),
        nfc_chips=tuple(d.get("nfc_chips") or []), download_boot=d.get("download_boot"),
        recovery_boot=d.get("recovery_boot"), source_wiki=src.get("wiki", ""), source_tree=src.get("tree"),
    )


class PartsDatabase:
    def __init__(self, path: str = BUNDLED_PATH):
        self.path = path
        self.by_model: Dict[str, PartsEntry] = {}
        self.by_codename: Dict[str, List[PartsEntry]] = {}
        self.meta: Dict[str, Any] = {}
        self.error: Optional[str] = None
        try:
            with open(path, encoding="utf-8") as f:
                doc = json.load(f)
        except (OSError, ValueError) as e:
            self.error = str(e)
            logger.warning(f"Parts database unreadable: {e}")
            return
        if not isinstance(doc, dict) or doc.get("schema") != 1:
            self.error = "not a schema 1 parts file"
            return
        self.meta = {k: doc.get(k) for k in ("built", "sources", "meaning")}
        for d in doc.get("devices") or []:
            try:
                e = _entry(d)
            except (KeyError, TypeError):
                continue
            for m in e.models:
                self.by_model.setdefault(normalize_model(m), e)
            for c in {e.codename.lower(), *e.stock_codenames}:
                self.by_codename.setdefault(c, []).append(e)
        self._count = len(doc.get("devices") or [])

    def __len__(self):
        return getattr(self, "_count", 0)

    def lookup(self, model: str = "", device: str = "", manufacturer: str = "") -> Optional[PartsEntry]:
        """By model number first, then by codename (LineageOS or factory codename). A codename match must
        also match the manufacturer when it is given (the same codename names a Motorola and a Xiaomi
        phone), and must be unique: two candidates left means no entry, never a pick."""
        m = normalize_model(model)
        if m and m in self.by_model:
            return _with(self.by_model[m], "model number")
        cands = list({id(c): c for c in self.by_codename.get((device or "").strip().lower(), [])}.values())
        maker = (manufacturer or "").strip().lower()
        if maker:
            cands = [c for c in cands if c.vendor.lower() == maker]
        if m:   # a phone whose model number is known but not listed: drop candidates that list other numbers
            cands = [c for c in cands if not c.models] or cands
        if len(cands) == 1:
            return _with(cands[0], "codename")
        # region variants built from one firmware tree have the same parts: one answer when they also agree
        # on the battery; anything else stays ambiguous
        if cands and len({(c.source_tree, c.battery_mah, c.camera_sensors) for c in cands}) == 1 and cands[0].source_tree:
            from dataclasses import replace
            others = tuple(sorted({c.display_name for c in cands} - {cands[0].display_name}))
            return replace(_with(cands[0], "codename"), variant_names=others)
        return None


def _with(e: PartsEntry, how: str) -> PartsEntry:
    from dataclasses import replace
    return replace(e, matched_by=how)


_default: Optional[PartsDatabase] = None


def default_parts_db() -> PartsDatabase:
    global _default
    if _default is None:
        _default = PartsDatabase()
    return _default
