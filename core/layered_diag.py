"""
Layered diagnosis: narrow a fault to a component, or ask the technician for a manual step.

Software reads tell you a part does not work; they rarely tell you whether the part, its flex, or the
board line behind it is at fault. So each area here walks a short decision tree over readings the
standard checks already collected (no new phone reads). When the readings cannot separate the part from
its flex or the board, it returns ONE manual step with clear answer options (usually "fit a known-good
part and re-run"); the caller shows the step, collects the technician's answer, and calls again with that
answer added. The next call continues from where it left off.

Rules kept from the rest of the program:
- never a definite single-cause verdict from software alone: a conclusion reached without a manual swap
  is "medium" confidence and still worded as the most likely cause;
- a conclusion reached because a known-good part changed the result is "high" confidence;
- nothing is invented: an area with no clear fault signal is NOT_APPLICABLE, and if the technician skips
  a manual step the result is INSUFFICIENT, never a guess.

This module is pure: `run_layered(...)` and each `diagnose_*` take the already-built diagnostic objects
(or plain signals) plus an `answers` dict, and return LayeredResult objects. No I/O, no ADB.
"""
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.result import Evidence, M, ProducesCheckResult, Status

# status values of a LayeredResult
CONCLUDED = "CONCLUDED"
NEEDS_MANUAL = "NEEDS_MANUAL"
INSUFFICIENT = "INSUFFICIENT"
NOT_APPLICABLE = "NOT_APPLICABLE"

# answer keys a manual step offers
FIXED = "fixed"   # the symptom is gone after the step
SAME = "same"     # the symptom is unchanged
SKIP = "skip"     # the technician did not do the step


@dataclass
class ManualStep:
    """One step the technician does by hand, then re-runs. `answers[step_id]` carries the result back."""
    step_id: str
    instruction: Any                      # Msg
    options: List[Tuple[str, Any]] = field(default_factory=list)   # (answer key, label Msg)

    def to_dict(self) -> Dict[str, Any]:
        return {"step_id": self.step_id, "instruction": self.instruction,
                "options": [{"key": k, "label": v} for k, v in self.options]}


@dataclass
class LayeredResult(ProducesCheckResult):
    check_id: str = "diagnosis.layered"
    area: str = ""                         # "fingerprint" / "battery_temp" / "battery_health" / "charging" / "touch"
    status: str = NOT_APPLICABLE
    conclusion: Any = None                 # Msg: the most likely component, when CONCLUDED
    confidence: str = ""                   # "high" (isolated by a known-good swap) / "medium" (from readings)
    manual_step: Optional[ManualStep] = None
    trail: List[Any] = field(default_factory=list)   # the reasoning steps, in order (Msg)
    diagnosis: Any = None                  # the headline Msg (conclusion, the step, or why not applicable)
    evidence: List[Evidence] = field(default_factory=list)

    _check_status_attr = "_overall_status"

    @property
    def _overall_status(self) -> str:
        # layered diagnosis never sets the overall PASS/FAIL: it explains a fault another check already found
        return str(Status.UNKNOWN)

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        d["manual_step"] = self.manual_step.to_dict() if self.manual_step else None
        return d


def _two_choice(step_id: str, instruction, fixed_label, same_label) -> ManualStep:
    return ManualStep(step_id, instruction,
                      [(FIXED, fixed_label), (SAME, same_label), (SKIP, M("layered.skip"))])


def _concluded(area, trail, conclusion, confidence, evidence) -> LayeredResult:
    return LayeredResult(area=area, status=CONCLUDED, conclusion=conclusion, confidence=confidence,
                         trail=trail, diagnosis=conclusion, evidence=evidence)


def _manual(area, trail, step, evidence) -> LayeredResult:
    return LayeredResult(area=area, status=NEEDS_MANUAL, manual_step=step, trail=trail,
                         diagnosis=step.instruction, evidence=evidence)


def _insufficient(area, trail, evidence) -> LayeredResult:
    return LayeredResult(area=area, status=INSUFFICIENT, trail=trail, diagnosis=M("layered.insufficient"),
                         evidence=evidence)


def _na(area) -> LayeredResult:
    return LayeredResult(area=area, status=NOT_APPLICABLE, diagnosis=M("layered.not_applicable"))


# ---------------------------------------------------------------------------- fingerprint
def diagnose_fingerprint(fp, answers: Dict[str, str]) -> LayeredResult:
    """fp: core.hardware_tester.FingerprintDiagnostics. Runs only when the sensor is not reached."""
    area = "fingerprint"
    not_reached = (getattr(fp, "error_status", "") in ("HAL_CRASH_LOOP", "HW_UNAVAILABLE", "SERVICE_MISSING")
                   or getattr(fp, "is_functional", None) is False)
    if not not_reached:
        return _na(area)
    ev = list(getattr(fp, "evidence", []) or [])
    trail = [M("layered.fp.not_reached")]
    flex = answers.get("fp_flex")
    if flex is None:
        return _manual(area, trail, _two_choice("fp_flex", M("layered.fp.step_flex"),
                                                M("layered.fp.flex_fixed"), M("layered.fp.flex_same")), ev)
    if flex == SKIP:
        return _insufficient(area, trail, ev)
    if flex == FIXED:
        trail.append(M("layered.fp.flex_was_fault"))
        return _concluded(area, trail, M("layered.fp.cause_flex"), "high", ev)
    trail.append(M("layered.fp.flex_ruled_out"))
    sensor = answers.get("fp_sensor")
    if sensor is None:
        return _manual(area, trail, _two_choice("fp_sensor", M("layered.fp.step_sensor"),
                                                M("layered.fp.sensor_fixed"), M("layered.fp.sensor_same")), ev)
    if sensor == SKIP:
        return _insufficient(area, trail, ev)
    if sensor == FIXED:
        trail.append(M("layered.fp.sensor_was_fault"))
        return _concluded(area, trail, M("layered.fp.cause_sensor"), "high", ev)
    trail.append(M("layered.fp.sensor_ruled_out"))
    return _concluded(area, trail, M("layered.fp.cause_board"), "medium", ev)


# ---------------------------------------------------------------------------- touch
def diagnose_touch(controller_not_responding: bool, evidence=None, answers: Dict[str, str] = None) -> LayeredResult:
    """controller_not_responding: the touch controller did not answer (TOUCH_CONTROLLER_NOT_RESPONDING, or a
    display/touch check that found the controller dead)."""
    area = "touch"
    answers = answers or {}
    if not controller_not_responding:
        return _na(area)
    ev = list(evidence or [])
    trail = [M("layered.touch.not_reached")]
    screen = answers.get("touch_screen")
    if screen is None:
        return _manual(area, trail, _two_choice("touch_screen", M("layered.touch.step_screen"),
                                                M("layered.touch.screen_fixed"), M("layered.touch.screen_same")), ev)
    if screen == SKIP:
        return _insufficient(area, trail, ev)
    if screen == FIXED:
        trail.append(M("layered.touch.screen_was_fault"))
        return _concluded(area, trail, M("layered.touch.cause_screen"), "high", ev)
    trail.append(M("layered.touch.screen_ruled_out"))
    return _concluded(area, trail, M("layered.touch.cause_board"), "medium", ev)


# ---------------------------------------------------------------------------- battery temperature
def diagnose_battery_temp(battery, answers: Dict[str, str]) -> LayeredResult:
    """battery: core.board_diag.PMICStatus. Runs when the battery temperature reading is impossible for a
    phone sitting idle (<= -10 or >= 65 degrees): that points at the NTC line, the battery, or the board,
    not at real heat."""
    area = "battery_temp"
    t = getattr(battery, "temp_c", None)
    implausible = t is not None and (t <= -10.0 or t >= 65.0)
    if not implausible:
        return _na(area)
    ev = list(getattr(battery, "evidence", []) or [])
    trail = [M("layered.bat_temp.impossible", temp=t)]
    swap = answers.get("bat_temp_swap")
    if swap is None:
        return _manual(area, trail, _two_choice("bat_temp_swap", M("layered.bat_temp.step_swap"),
                                                M("layered.bat_temp.swap_fixed"), M("layered.bat_temp.swap_same")), ev)
    if swap == SKIP:
        return _insufficient(area, trail, ev)
    if swap == FIXED:
        trail.append(M("layered.bat_temp.swap_was_fault"))
        return _concluded(area, trail, M("layered.bat_temp.cause_battery"), "high", ev)
    trail.append(M("layered.bat_temp.swap_ruled_out"))
    return _concluded(area, trail, M("layered.bat_temp.cause_board"), "medium", ev)


# ---------------------------------------------------------------------------- battery health
def diagnose_battery_health(battery, answers: Dict[str, str]) -> LayeredResult:
    """battery: core.board_diag.PMICStatus. Runs when a real measured capacity is below the manufacturer
    limit (health_verdict FAIL, i.e. charge_full well under the design capacity)."""
    area = "battery_health"
    if getattr(battery, "health_verdict", "") != str(Status.FAIL):
        return _na(area)
    ev = list(getattr(battery, "evidence", []) or [])
    pct = getattr(battery, "true_health_pct", None)
    trail = [M("layered.bat_health.low", pct=pct if pct is not None else "-")]
    swap = answers.get("bat_health_swap")
    if swap is None:
        return _manual(area, trail, _two_choice("bat_health_swap", M("layered.bat_health.step_swap"),
                                                M("layered.bat_health.swap_fixed"), M("layered.bat_health.swap_same")), ev)
    if swap == SKIP:
        return _insufficient(area, trail, ev)
    if swap == FIXED:
        trail.append(M("layered.bat_health.swap_was_fault"))
        return _concluded(area, trail, M("layered.bat_health.cause_battery"), "high", ev)
    trail.append(M("layered.bat_health.swap_ruled_out"))
    return _concluded(area, trail, M("layered.bat_health.cause_gauge"), "medium", ev)


# ---------------------------------------------------------------------------- charging port
# Fault-signature ids (data/fault_signatures_kernel.json) that mean a port short or liquid.
PORT_SHORT_RULES = ("USB_CC_VBUS_SHORT", "USB_PORT_MOISTURE", "VBUS_OVERCURRENT")


def _charging_symptom(charging, log_rule_ids) -> Optional[str]:
    """Which charging symptom to work on, or None. 'short' (a port short / liquid in the log) wins over
    'dead' (plugged in, but no voltage and no current measured)."""
    ids = set(log_rule_ids or ())
    if ids & set(PORT_SHORT_RULES):
        return "short"
    plugged = bool(getattr(charging, "charger_type", "UNKNOWN") not in ("UNKNOWN", "")
                   or getattr(charging, "is_ac_plugged", False))
    no_power = getattr(charging, "voltage_mv", None) in (None, 0) and getattr(charging, "current_ma", None) in (None, 0)
    if plugged and no_power:
        return "dead"
    return None


def diagnose_charging(charging, log_rule_ids=(), answers: Dict[str, str] = None) -> LayeredResult:
    """charging: core.board_diag.FastChargeDiagnostics; log_rule_ids: fault-signature ids matched in a log."""
    area = "charging"
    answers = answers or {}
    symptom = _charging_symptom(charging, log_rule_ids)
    if symptom is None:
        return _na(area)
    ev = list(getattr(charging, "evidence", []) or [])

    if symptom == "short":
        trail = [M("layered.charging.short_seen")]
        clean = answers.get("chg_clean")
        if clean is None:
            return _manual(area, trail, _two_choice("chg_clean", M("layered.charging.step_clean"),
                                                    M("layered.charging.clean_fixed"), M("layered.charging.clean_same")), ev)
        if clean == SKIP:
            return _insufficient(area, trail, ev)
        if clean == FIXED:
            trail.append(M("layered.charging.clean_was_fault"))
            return _concluded(area, trail, M("layered.charging.cause_dirt"), "high", ev)
        trail.append(M("layered.charging.clean_ruled_out"))
        return _concluded(area, trail, M("layered.charging.cause_port_flex"), "medium", ev)

    # symptom == "dead": plugged in, no measured voltage or current
    trail = [M("layered.charging.dead_seen")]
    cable = answers.get("chg_cable")
    if cable is None:
        return _manual(area, trail, _two_choice("chg_cable", M("layered.charging.step_cable"),
                                                M("layered.charging.cable_fixed"), M("layered.charging.cable_same")), ev)
    if cable == SKIP:
        return _insufficient(area, trail, ev)
    if cable == FIXED:
        trail.append(M("layered.charging.cable_was_fault"))
        return _concluded(area, trail, M("layered.charging.cause_cable"), "high", ev)
    trail.append(M("layered.charging.cable_ruled_out"))
    port = answers.get("chg_port")
    if port is None:
        return _manual(area, trail, _two_choice("chg_port", M("layered.charging.step_port"),
                                                M("layered.charging.port_fixed"), M("layered.charging.port_same")), ev)
    if port == SKIP:
        return _insufficient(area, trail, ev)
    if port == FIXED:
        trail.append(M("layered.charging.port_was_fault"))
        return _concluded(area, trail, M("layered.charging.cause_port"), "high", ev)
    trail.append(M("layered.charging.port_ruled_out"))
    return _concluded(area, trail, M("layered.charging.cause_charger_ic"), "medium", ev)


# ---------------------------------------------------------------------------- dispatcher
AREAS = ("fingerprint", "touch", "battery_temp", "battery_health", "charging")


def run_layered(fingerprint=None, battery=None, charging=None, touch_not_responding=False,
                touch_evidence=None, log_rule_ids=(), answers: Dict[str, str] = None) -> List[LayeredResult]:
    """Run every area that has a source, keeping only those that apply (a fault to isolate or a pending
    manual step). `answers` carries the technician's replies to earlier manual steps, keyed by step_id."""
    answers = answers or {}
    out = []
    if fingerprint is not None:
        out.append(diagnose_fingerprint(fingerprint, answers))
    out.append(diagnose_touch(touch_not_responding, touch_evidence, answers))
    if battery is not None:
        out.append(diagnose_battery_temp(battery, answers))
        out.append(diagnose_battery_health(battery, answers))
    if charging is not None:
        out.append(diagnose_charging(charging, log_rule_ids, answers))
    return [r for r in out if r.status != NOT_APPLICABLE]
