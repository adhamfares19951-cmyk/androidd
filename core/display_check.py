"""
Screen originality indicators (read only: cat, getprop, dumpsys, logcat; nothing is written to the phone).

Software cannot prove that a screen is original, so this check never gives PASS or FAIL (its status is
UNKNOWN and it does not count in the overall result). It lists indicators, each with its evidence and an
effect, and sums them up:

- "supports": a reading typical of the original part (e.g. a Samsung Display panel with its serial numbers on
  a Samsung phone, an under-display fingerprint sensor that has worked);
- "against": a reading that conflicts with an original part (panel serial numbers all zero, an under-display
  fingerprint driver that keeps crashing) - possible causes, since a damaged sensor or flex does this too;
- "info": shown, not judged (resolution, refresh rates, HDR, a panel maker that original phones also use).

Assessment: "against" if any indicator is against, "consistent" with two or more supporting and none against,
otherwise "insufficient".
"""
import re
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional

from core.readers import read_node, run_read
from core.result import Evidence, M, ProducesCheckResult, Status

PANEL_DIR = "/sys/class/lcd/panel"        # Samsung kernels expose the panel's own ID registers here
PANEL_MAKERS = {"SDC": "Samsung Display", "BOE": "BOE", "CSOT": "CSOT", "CSO": "CSOT", "TM": "Tianma",
                "TIANMA": "Tianma", "VNX": "Visionox", "VISIONOX": "Visionox", "LGD": "LG Display",
                "AUO": "AU Optronics", "INX": "Innolux", "JDI": "JDI", "SHP": "Sharp", "SHARP": "Sharp"}
HDR_NAMES = {1: "Dolby Vision", 2: "HDR10", 3: "HLG", 4: "HDR10+"}
_UDFPS_WORDS = re.compile(r"udfps|ultrasonic|under.?display|optical sensor|\bfod\b", re.I)


@dataclass
class Indicator:
    key: str
    effect: str                    # "supports" / "against" / "info"
    text: Any                      # Msg
    evidence: List[Evidence] = field(default_factory=list)


@dataclass
class DisplayIndicators(ProducesCheckResult):
    check_id: str = "display.originality_indicators"
    status: str = str(Status.UNKNOWN)          # always: indicators, not proof
    assessment: str = "insufficient"           # "consistent" / "against" / "insufficient"
    manufacturer: str = ""
    model: str = ""
    panel_type: Optional[str] = None
    panel_maker: Optional[str] = None
    native_resolution: Optional[str] = None
    max_refresh_hz: Optional[int] = None
    refresh_rates: List[int] = field(default_factory=list)
    hdr_types: List[str] = field(default_factory=list)
    indicators: List[Indicator] = field(default_factory=list)
    diagnosis: Any = None
    evidence: List[Evidence] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() for e in self.evidence]
        d["indicators"] = [{"key": i.key, "effect": i.effect, "text": i.text,
                            "evidence": [e.to_dict() for e in i.evidence]} for i in self.indicators]
        return d


class DisplayOriginalityCheck:
    def __init__(self, bridge):
        self.bridge = bridge

    def _prop(self, serial: str, name: str) -> str:
        r = run_read(self.bridge, f"getprop {name}", serial=serial, timeout=5)
        return r.text.splitlines()[0].strip() if r.ok and r.text else ""

    def _panel(self, serial: str, name: str):
        r = read_node(self.bridge, [f"{PANEL_DIR}/{name}"], serial=serial)
        return r, (r.text.splitlines()[0].strip() if r.ok and r.text else None)

    def run(self, serial: str) -> DisplayIndicators:
        r = DisplayIndicators(manufacturer=self._prop(serial, "ro.product.manufacturer"),
                              model=self._prop(serial, "ro.product.model"))
        is_samsung = r.manufacturer.lower() == "samsung"
        self._panel_identity(serial, r, is_samsung)
        self._display_modes(serial, r)
        self._under_display_fingerprint(serial, r)

        effects = [i.effect for i in r.indicators]
        if "against" in effects:
            r.assessment = "against"
        elif effects.count("supports") >= 2:
            r.assessment = "consistent"
        r.diagnosis = M(f"display.assess.{r.assessment}", supports=effects.count("supports"),
                        against=effects.count("against"))
        r.evidence = [e for i in r.indicators for e in i.evidence]
        return r

    # ------------------------------------------------------------------ panel identity (Samsung sysfs)
    def _panel_identity(self, serial: str, r: DisplayIndicators, is_samsung: bool) -> None:
        type_read, panel_type = self._panel(serial, "lcd_type")
        if panel_type is None:
            r.indicators.append(Indicator("panel_maker", "info", M("display.ind.panel_unreadable", status=str(type_read.status)),
                                          [type_read.evidence()]))
            return
        r.panel_type = panel_type
        prefix = re.split(r"[_\s-]", panel_type, maxsplit=1)[0].upper()
        r.panel_maker = PANEL_MAKERS.get(prefix)
        ev = [type_read.evidence(f"panel maker prefix {prefix} = {r.panel_maker or 'unknown'}")]
        if is_samsung and prefix == "SDC":
            r.indicators.append(Indicator("panel_maker", "supports",
                                          M("display.ind.panel_maker_sdc", panel=panel_type), ev))
        else:
            r.indicators.append(Indicator("panel_maker", "info",
                                          M("display.ind.panel_maker_other", panel=panel_type,
                                            maker=r.panel_maker or prefix), ev))

        id_reads = [self._panel(serial, n) for n in ("cell_id", "octa_id")]
        ids = [v for _, v in id_reads if v]
        id_ev = [rd.evidence() for rd, _ in id_reads]
        if ids and all(set(v.replace(" ", "")) <= {"0"} for v in ids):
            r.indicators.append(Indicator("panel_ids", "against", M("display.ind.panel_ids_zero"), id_ev))
        elif ids and prefix == "SDC":
            r.indicators.append(Indicator("panel_ids", "supports", M("display.ind.panel_ids_ok", cell=ids[0]), id_ev))
        elif ids:
            r.indicators.append(Indicator("panel_ids", "info", M("display.ind.panel_ids_other", cell=ids[0]), id_ev))

        date_read, date_raw = self._panel(serial, "manufacture_date")
        m = re.search(r"(20\d{2})(\d{2})(\d{2})", date_raw or "")
        if m:
            r.indicators.append(Indicator("panel_date", "info",
                                          M("display.ind.panel_date", date=f"{m.group(1)}-{m.group(2)}-{m.group(3)}"),
                                          [date_read.evidence()]))

    # ------------------------------------------------------------------ resolution, refresh, HDR (info)
    def _display_modes(self, serial: str, r: DisplayIndicators) -> None:
        d = run_read(self.bridge, "dumpsys display", serial=serial, timeout=10)
        modes = [(int(w), int(h), float(f)) for w, h, f in
                 re.findall(r"width=(\d+), height=(\d+), fps=([\d.]+)", d.text if d.ok else "")]
        if not modes:
            return
        w, h = max(((w, h) for w, h, _ in modes), key=lambda wh: wh[0] * wh[1])
        r.native_resolution = f"{w}x{h}"
        r.refresh_rates = sorted({round(f) for mw, mh, f in modes if (mw, mh) == (w, h)})
        r.max_refresh_hz = max(r.refresh_rates)
        hdr = re.search(r"supportedHdrTypes=\[([^\]]*)\]", d.text)
        if hdr:
            r.hdr_types = [HDR_NAMES.get(int(t), t) for t in re.findall(r"\d+", hdr.group(1))]
        r.indicators.append(Indicator(
            "display_modes", "info",
            M("display.ind.modes", res=r.native_resolution, max_hz=r.max_refresh_hz,
              rates=", ".join(str(x) for x in r.refresh_rates), hdr=", ".join(r.hdr_types) or "-"),
            [d.evidence(f"native {r.native_resolution}, refresh {r.refresh_rates} Hz, HDR {r.hdr_types}")]))

    # ------------------------------------------------------------------ under-display fingerprint
    def _under_display_fingerprint(self, serial: str, r: DisplayIndicators) -> None:
        from core.hardware_tester import HardwareTester
        fod = read_node(self.bridge, ["/sys/class/sec/tsp/fod_info"], serial=serial)
        fp = HardwareTester(self.bridge).diagnose_fingerprint(serial)
        under_display = (fod.ok and bool(fod.text)) or bool(_UDFPS_WORDS.search(fp.raw_details or ""))
        if not under_display:
            return
        ev = ([fod.evidence("under-display fingerprint area reported by the touch controller")] if fod.ok else []) \
            + list(fp.evidence)
        if fp.error_status == "HAL_CRASH_LOOP":
            r.indicators.append(Indicator("fp_under_display", "against", M("display.ind.fp_crash"), ev))
            return
        accepted = sum(int(n) for n in re.findall(r'"accept"\s*:\s*(\d+)', fp.raw_details or ""))
        if accepted > 0:
            r.indicators.append(Indicator("fp_under_display", "supports", M("display.ind.fp_works", count=accepted), ev))
        else:
            r.indicators.append(Indicator("fp_under_display", "info", M("display.ind.fp_no_record"), ev))
