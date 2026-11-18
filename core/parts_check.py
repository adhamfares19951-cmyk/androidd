"""
Parts indicators (read only: getprop, cat; nothing is written to the phone).

Compares the parts the phone reports with what its model's factory firmware is built for
(core/parts_db.py). Like the screen check (core/display_check.py), software cannot prove that a part is
original, so this check never gives PASS or FAIL: its status is UNKNOWN, it does not count in the
overall result, and it lists indicators with their evidence:

- "supports": the phone reports a camera sensor / fingerprint maker that this model's firmware is built for;
- "against": the phone reports one this model's firmware is NOT built for: a replaced part is the usual
  reason, an unlisted production variant is the other (both are named as possible causes);
- "info": shown, not judged (a sensor known only from sibling models, the reference itself, no reading).

Where the readings come from (each read is evidence; an unreadable source is reported, not skipped):
- MediaTek: /proc/driver/camera_info, the sensors the camera driver actually detected at boot;
- Samsung: /sys/class/camera/{rear,front}/*_camtype, the camera module's own type string;
- any phone: camera and fingerprint system properties (getprop).
The log is not used for sensors: camera drivers log every sensor they probe, including absent ones.
"""
import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional

from core.display_check import Indicator
from core.part_names import fingerprint_maker_of, sensors_in
from core.parts_db import PartsEntry, default_parts_db
from core.readers import read_node, run_read
from core.result import Evidence, M, ProducesCheckResult, Status

MTK_CAMERA_INFO = "/proc/driver/camera_info"
SAMSUNG_CAMTYPE = ["/sys/class/camera/rear/rear_camtype", "/sys/class/camera/rear/rear2_camtype",
                   "/sys/class/camera/rear/rear3_camtype", "/sys/class/camera/rear/rear4_camtype",
                   "/sys/class/camera/front/front_camtype", "/sys/class/camera/front/front2_camtype"]
_CAMERA_PROP_KEY = re.compile(r"camera|cam\.|\.cam|sensor", re.I)
_FP_PROP_KEY = re.compile(r"fingerprint|\.fp\.|fp\.vendor|fpsensor|\.fp_", re.I)


@dataclass
class FoundSensor:
    sensor: str
    module: Optional[str]
    source: str


@dataclass
class PartsIndicators(ProducesCheckResult):
    check_id: str = "parts.originality_indicators"
    status: str = str(Status.UNKNOWN)            # always: indicators, not proof
    assessment: str = "insufficient"             # "consistent" / "against" / "insufficient"
    model: str = ""
    codename: str = ""
    reference: Optional[str] = None              # "Xiaomi Redmi Note 9 (merlinx)"
    matched_by: str = ""
    found_sensors: List[FoundSensor] = field(default_factory=list)
    expected_sensors: List[str] = field(default_factory=list)
    fingerprint_maker: Optional[str] = None
    indicators: List[Indicator] = field(default_factory=list)
    diagnosis: Any = None
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() for e in self.evidence]
        d["indicators"] = [{"key": i.key, "effect": i.effect, "text": i.text,
                            "evidence": [e.to_dict() for e in i.evidence]} for i in self.indicators]
        return d


def _fmt_sensor(sensor: str, modules: List[str]) -> str:
    return f"{sensor.upper()} ({', '.join(modules)})" if modules else sensor.upper()


class PartsCheck:
    def __init__(self, bridge, db=None):
        self.bridge = bridge
        self.db = db

    def _prop(self, serial: str, name: str) -> str:
        r = run_read(self.bridge, f"getprop {name}", serial=serial, timeout=5)
        return r.text.splitlines()[0].strip() if r.ok and r.text else ""

    def run(self, serial: str) -> PartsIndicators:
        db = self.db or default_parts_db()
        r = PartsIndicators(model=self._prop(serial, "ro.product.model"),
                            codename=self._prop(serial, "ro.product.device") or self._prop(serial, "ro.build.product"))
        entry = db.lookup(r.model, r.codename, self._prop(serial, "ro.product.manufacturer"))
        all_props = run_read(self.bridge, "getprop", serial=serial, timeout=10)
        if entry is None:
            r.indicators.append(Indicator("reference", "info",
                                          M("parts.ind.no_reference", model=r.model or "-", codename=r.codename or "-",
                                            count=len(db)), []))
        else:
            r.reference = f"{entry.display_names} ({entry.codename})"
            r.matched_by = entry.matched_by
            r.expected_sensors = [_fmt_sensor(s, entry.modules_of(s)) for s in sorted(entry.sensor_names())]
            r.indicators.append(Indicator(
                "reference", "info",
                M("parts.ind.reference", name=entry.display_names, codename=entry.codename,
                  how=M(f"parts.matched_by.{'model' if entry.matched_by == 'model number' else 'codename'}"),
                  sensors=", ".join(r.expected_sensors) or "-",
                  fingerprint=", ".join(entry.fingerprint_makers) or "-"),
                [Evidence(source=entry.source_tree or entry.source_wiki,
                          raw_excerpt=f"{entry.source_wiki}\n{entry.source_tree or ''}".strip())]))
        self._camera_sensors(serial, r, entry, all_props)
        self._fingerprint(serial, r, entry, all_props)

        effects = [i.effect for i in r.indicators]
        if "against" in effects:
            r.assessment = "against"
        elif effects.count("supports") >= 2:
            r.assessment = "consistent"
        r.diagnosis = M(f"parts.assess.{r.assessment}", supports=effects.count("supports"),
                        against=effects.count("against"))
        r.evidence = [e for i in r.indicators for e in i.evidence]
        return r

    # ------------------------------------------------------------------ camera sensors
    def _read_sensors(self, serial: str, all_props) -> (List[FoundSensor], List[Evidence]):
        found: Dict[str, FoundSensor] = {}
        evidence: List[Evidence] = []
        mtk = read_node(self.bridge, [MTK_CAMERA_INFO], serial=serial)
        evidence.append(mtk.evidence())
        if mtk.ok:
            # the driver writes "CAM[1]:s5kgm1sp_mipi_raw;CAM[2]:..." for the sensors it detected
            for s, mk in sensors_in(mtk.text, camera_lines_only=False):
                found.setdefault(s, FoundSensor(s, mk or None, MTK_CAMERA_INFO))
        for path in SAMSUNG_CAMTYPE:
            rd = read_node(self.bridge, [path], serial=serial)
            if rd.ok:
                evidence.append(rd.evidence())
                for s, mk in sensors_in(rd.text, camera_lines_only=False):
                    found.setdefault(s, FoundSensor(s, mk or None, path))
        if all_props.ok:
            lines = [ln for ln in all_props.text.splitlines()
                     if _CAMERA_PROP_KEY.search(ln.split("]:")[0]) and not re.search(r"supported|list|enable|tuning|lib", ln, re.I)]
            hits = []
            for ln in lines:
                for s, mk in sensors_in(ln.split("]:", 1)[-1], camera_lines_only=False):
                    hits.append(ln)
                    found.setdefault(s, FoundSensor(s, mk or None, "getprop"))
            if hits:
                evidence.append(Evidence(source="getprop (camera properties)", raw_excerpt="\n".join(hits[:20])))
        return list(found.values()), evidence

    def _camera_sensors(self, serial: str, r: PartsIndicators, entry: Optional[PartsEntry], all_props) -> None:
        found, ev = self._read_sensors(serial, all_props)
        r.found_sensors = found
        if not found:
            r.indicators.append(Indicator("camera_sensors", "info", M("parts.ind.sensors_unreadable"), ev))
            return
        if entry is None or not (entry.sensor_names() or entry.platform_sensor_names()):
            r.indicators.append(Indicator("camera_sensors", "info",
                                          M("parts.ind.sensors_no_reference",
                                            sensors=", ".join(f.sensor.upper() for f in found)), ev))
            return
        for f in found:
            if f.sensor in entry.sensor_names():
                r.indicators.append(Indicator(f"camera.{f.sensor}", "supports",
                                              M("parts.ind.sensor_listed", sensor=f.sensor.upper(), name=entry.display_name,
                                                modules=", ".join(entry.modules_of(f.sensor)) or "-"), ev))
            elif f.sensor in entry.platform_sensor_names():
                r.indicators.append(Indicator(f"camera.{f.sensor}", "info",
                                              M("parts.ind.sensor_platform", sensor=f.sensor.upper()), ev))
            elif entry.sensor_names():
                r.indicators.append(Indicator(f"camera.{f.sensor}", "against",
                                              M("parts.ind.sensor_not_listed", sensor=f.sensor.upper(),
                                                name=entry.display_name, expected=", ".join(r.expected_sensors)), ev))
            else:
                r.indicators.append(Indicator(f"camera.{f.sensor}", "info",
                                              M("parts.ind.sensor_platform", sensor=f.sensor.upper()), ev))

    # ------------------------------------------------------------------ fingerprint maker
    def _fingerprint(self, serial: str, r: PartsIndicators, entry: Optional[PartsEntry], all_props) -> None:
        if not all_props.ok:
            return
        hits = [ln for ln in all_props.text.splitlines() if _FP_PROP_KEY.search(ln.split("]:")[0])]
        makers = []
        for ln in hits:
            mk = fingerprint_maker_of(ln.split("]:", 1)[-1])
            if mk and mk not in makers:
                makers.append(mk)
        if not makers:
            return
        r.fingerprint_maker = makers[0]
        ev = [Evidence(source="getprop (fingerprint properties)", raw_excerpt="\n".join(hits[:10]))]
        if entry is None or not entry.fingerprint_makers:
            r.indicators.append(Indicator("fingerprint_maker", "info",
                                          M("parts.ind.fp_no_reference", maker=makers[0]), ev))
        elif makers[0] in entry.fingerprint_makers:
            effect = "supports" if entry.fingerprint_from == "device" else "info"
            r.indicators.append(Indicator("fingerprint_maker", effect,
                                          M("parts.ind.fp_listed", maker=makers[0], name=entry.display_name), ev))
        elif entry.fingerprint_from == "device":
            r.indicators.append(Indicator("fingerprint_maker", "against",
                                          M("parts.ind.fp_not_listed", maker=makers[0], name=entry.display_name,
                                            expected=", ".join(entry.fingerprint_makers)), ev))
        else:
            r.indicators.append(Indicator("fingerprint_maker", "info",
                                          M("parts.ind.fp_platform", maker=makers[0],
                                            expected=", ".join(entry.fingerprint_makers)), ev))
