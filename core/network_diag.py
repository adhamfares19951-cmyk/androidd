"""
Network and SIM diagnostic engine: graduated diagnosis for cellular modem, SIM reader, and RF hardware.

Follows the same evidence-backed and layered architecture as core.layered_diag and core.board_diag:
Every check reads actual device state, retains full command audit Evidence, and gives a graduated
verdict (Status.PASS, Status.FAIL, Status.UNKNOWN, Status.NEEDS_ROOT) without inventing data:
- Baseband empty/unknown or IMEI null/corrupted -> Modem / EFS failure (EFS restore may fix)
- SIM state ABSENT with known-good SIM verified -> SIM reader hardware fault
- IMEI present + SIM READY/LOADED + Signal constantly 0 -> RF / Antenna / PA hardware fault
- IMEI present + SIM READY/LOADED + Signal verified -> PASS
"""

import re
from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from core.imei_check import luhn_ok
from core.readers import run_read
from core.result import Evidence, M, ProducesCheckResult, Status, coerce_status

# Constants for layered manual answers
FIXED = "fixed"
SAME = "same"
SKIP = "skip"
INSERTED = "inserted"
NO_SIM = "no_sim"


@dataclass
class ManualStep:
    """One manual step requested from the technician to resolve ambiguity."""
    step_id: str
    instruction: Any
    options: List[Tuple[str, Any]] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "step_id": self.step_id,
            "instruction": self.instruction,
            "options": [{"key": k, "label": v} for k, v in self.options],
        }


@dataclass
class NetworkDiagResult(ProducesCheckResult):
    check_id: str = "network.diagnosis"
    status: Status = Status.UNKNOWN
    sim_state: str = ""
    sim_states: List[str] = field(default_factory=list)
    baseband_version: Optional[str] = None
    imei: Optional[str] = None
    signal_level: Optional[int] = None
    signal_dbm: Optional[int] = None
    registration_state: str = ""
    fault_area: Optional[str] = None  # "modem_efs", "sim_reader", "rf_antenna", "sim_card", None
    confidence: str = ""              # "high", "medium"
    manual_step: Optional[ManualStep] = None
    diagnosis: Any = None
    possible_causes: List[Any] = field(default_factory=list)
    evidence: List[Evidence] = field(default_factory=list)

    def _check_value(self):
        val = self.imei or (self.sim_state if self.sim_state else str(self.status))
        unit = f"{self.signal_dbm} dBm" if self.signal_dbm is not None else ""
        return val, unit

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["status"] = str(self.status)
        d["evidence"] = [e.to_dict() if isinstance(e, Evidence) else e for e in self.evidence]
        d["manual_step"] = self.manual_step.to_dict() if self.manual_step else None
        return d


def parse_sim_state(text: Optional[str]) -> List[str]:
    """Parse comma-separated SIM states from getprop gsm.sim.state."""
    if not text:
        return []
    return [s.strip().upper() for s in text.split(",") if s.strip()]


def parse_baseband(text: Optional[str]) -> Optional[str]:
    """Parse baseband version from getprop gsm.version.baseband."""
    if not text:
        return None
    bb = text.split(",")[0].strip()
    if not bb or bb.lower() in ("unknown", "null", "none"):
        return None
    return bb


def parse_imei_service_call(text: Optional[str]) -> Optional[str]:
    """Parse 15-digit IMEI from service call iphonesubinfo parcel dump."""
    if not text:
        return None
    # If the parcel dump has quoted string representation (standard adb output):
    # e.g. Result: Parcel(00000000 ... '....3.5.2.0.9.9.0.0.1.7.6.1.4.8.2.')
    quotes = re.findall(r"'([^']*)'", text)
    if quotes:
        quoted_text = "".join(quotes)
        quoted_digits = "".join(c for c in quoted_text if c.isdigit())
        if len(quoted_digits) >= 15:
            tail = quoted_digits[-15:]
            if set(tail) != {"0"} and luhn_ok(tail):
                return tail
    # Fallback if text is raw or without quotes
    digits = "".join(c for c in text if c.isdigit())
    if len(digits) >= 15:
        tail = digits[-15:]
        if set(tail) != {"0"} and luhn_ok(tail):
            return tail
    return None


def parse_telephony_registry(text: Optional[str]) -> Tuple[str, Optional[int], Optional[int], bool]:
    """
    Parse dumpsys telephony.registry for registration state, signal level (0..4),
    signal dBm, and whether signal is zero / none.
    Returns: (registration_state, signal_level, signal_dbm, is_signal_zero)
    """
    if not text:
        return "", None, None, False

    reg_state = ""
    # Look for voice/data registration state
    m_reg = re.search(r"mVoiceRegState=\d+\(([^)]+)\)", text) or re.search(r"mDataRegState=\d+\(([^)]+)\)", text)
    if m_reg:
        reg_state = m_reg.group(1).strip()
    else:
        m_reg_code = re.search(r"mVoiceRegState=(\d+)", text) or re.search(r"mRegistrationState=(\d+)", text)
        if m_reg_code:
            code = m_reg_code.group(1)
            reg_map = {"0": "IN_SERVICE", "1": "OUT_OF_SERVICE", "2": "EMERGENCY_ONLY", "3": "POWER_OFF"}
            reg_state = reg_map.get(code, f"STATE_{code}")
        elif "IN_SERVICE" in text:
            reg_state = "IN_SERVICE"
        elif "OUT_OF_SERVICE" in text:
            reg_state = "OUT_OF_SERVICE"
        elif "EMERGENCY_ONLY" in text:
            reg_state = "EMERGENCY_ONLY"

    signal_level: Optional[int] = None
    signal_dbm: Optional[int] = None

    # Parse level (0-4)
    m_level = (
        re.search(r"mSignalStrength=.*?level=(\d+)", text)
        or re.search(r"mSignalStrength=.*?mLevel=(\d+)", text)
        or re.search(r"\blevel=(\d+)", text)
        or re.search(r"\bmLevel=(\d+)", text)
    )
    if m_level:
        signal_level = int(m_level.group(1))

    # Parse dBm
    m_dbm = (
        re.search(r"mSignalStrength=.*?rssi=(-?\d+)", text)
        or re.search(r"(-?\d+)\s*dBm", text)
        or re.search(r"lteRssi=(-?\d+)", text)
    )
    if m_dbm:
        signal_dbm = int(m_dbm.group(1))

    # Plain signal strength value if no explicit level/dbm
    if signal_level is None and signal_dbm is None:
        m_plain = re.search(r"mSignalStrength=(-?\d+)", text) or re.search(r"signal=(-?\d+)", text)
        if m_plain:
            val = int(m_plain.group(1))
            if 0 <= val <= 4:
                signal_level = val
            elif val < 0:
                signal_dbm = val
            elif val == 99:
                # 99 in GSM means undetectable/no signal
                signal_level = 0

    # Determine is_signal_zero
    is_signal_zero = False
    if signal_level is not None and signal_level == 0:
        is_signal_zero = True
    elif signal_dbm is not None and (signal_dbm <= -120 or signal_dbm == 0):
        is_signal_zero = True
    elif reg_state in ("OUT_OF_SERVICE", "EMERGENCY_ONLY") and (signal_level is None or signal_level == 0):
        is_signal_zero = True

    return reg_state, signal_level, signal_dbm, is_signal_zero


class NetworkDiagnostics:
    def __init__(self, bridge: Any):
        self.bridge = bridge

    def diagnose(self, serial: Optional[str] = None, answers: Optional[Dict[str, str]] = None) -> NetworkDiagResult:
        answers = answers or {}
        evidence: List[Evidence] = []

        # 1. Read SIM state
        sim_read = run_read(self.bridge, "getprop gsm.sim.state", serial=serial, timeout=5)
        evidence.append(sim_read.evidence())
        sim_states = parse_sim_state(sim_read.text)

        # Multi-SIM fallback for slot 2
        if len(sim_states) <= 1:
            sim2_read = run_read(self.bridge, "getprop gsm.sim.state.2", serial=serial, timeout=5)
            s2 = parse_sim_state(sim2_read.text)
            if s2:
                sim_states.extend(s2)
                evidence.append(sim2_read.evidence())

        primary_sim_state = sim_states[0] if sim_states else "UNKNOWN"

        # 2. Read Baseband version
        bb_read = run_read(self.bridge, "getprop gsm.version.baseband", serial=serial, timeout=5)
        evidence.append(bb_read.evidence())
        baseband = parse_baseband(bb_read.text)

        # 3. Read IMEI via iphonesubinfo service call
        imei_read = run_read(self.bridge, "service call iphonesubinfo 1", serial=serial, timeout=6)
        evidence.append(imei_read.evidence())
        imei = parse_imei_service_call(imei_read.text)

        # 4. Read Telephony Registry (registration state and signal)
        sig_read = run_read(self.bridge, "dumpsys telephony.registry", serial=serial, timeout=6)
        evidence.append(sig_read.evidence())
        reg_state, sig_level, sig_dbm, is_sig_zero = parse_telephony_registry(sig_read.text)

        # Permission check
        if any(r.status == Status.NEEDS_ROOT for r in (sim_read, bb_read, sig_read)):
            return NetworkDiagResult(
                status=Status.NEEDS_ROOT,
                sim_state=primary_sim_state,
                sim_states=sim_states,
                baseband_version=baseband,
                imei=imei,
                signal_level=sig_level,
                signal_dbm=sig_dbm,
                registration_state=reg_state,
                evidence=evidence,
            )

        # Check if telephony is present at all: if all reads returned empty/missing
        if not sim_states and not baseband and not imei and not sig_read.ok:
            return NetworkDiagResult(
                status=Status.UNKNOWN,
                diagnosis=M("sim.no_telephony"),
                evidence=evidence,
            )

        # Layer 1: Baseband & IMEI integrity (Modem / EFS)
        # Empty/unknown baseband or null IMEI indicates modem or EFS failure
        if baseband is None or imei is None:
            return NetworkDiagResult(
                status=Status.FAIL,
                sim_state=primary_sim_state,
                sim_states=sim_states,
                baseband_version=baseband,
                imei=imei,
                signal_level=sig_level,
                signal_dbm=sig_dbm,
                registration_state=reg_state,
                fault_area="modem_efs",
                confidence="medium",
                diagnosis=M("network.fault.modem_efs"),
                possible_causes=[
                    M("network.cause.efs"),
                    M("network.cause.modem_hardware"),
                    M("network.cause.ril"),
                ],
                evidence=evidence,
            )

        # Layer 2: SIM Card Presence and Reader Hardware
        all_absent = bool(sim_states and all(s == "ABSENT" for s in sim_states))
        if all_absent:
            known_good = answers.get("known_good_sim")
            if known_good in (INSERTED, FIXED, SAME, "yes", "true"):
                # Technician verified a known-good working SIM was inserted and it still reports ABSENT.
                # Software observes the symptom; possible_causes lists the electrical/hardware candidates.
                return NetworkDiagResult(
                    status=Status.FAIL,
                    sim_state=primary_sim_state,
                    sim_states=sim_states,
                    baseband_version=baseband,
                    imei=imei,
                    signal_level=sig_level,
                    signal_dbm=sig_dbm,
                    registration_state=reg_state,
                    fault_area="sim_reader",
                    confidence="medium",
                    diagnosis=M("network.fault.sim_reader"),
                    possible_causes=[
                        M("network.cause.reader_pins"),
                        M("network.cause.sim_power"),
                        M("network.cause.modem_hardware"),
                    ],
                    evidence=evidence,
                )
            elif known_good in (NO_SIM, SKIP, "skip", "no"):
                # No card inserted / technician skipped
                return NetworkDiagResult(
                    status=Status.UNKNOWN,
                    sim_state=primary_sim_state,
                    sim_states=sim_states,
                    baseband_version=baseband,
                    imei=imei,
                    diagnosis=M("sim.absent"),
                    evidence=evidence,
                )
            else:
                # Requires manual step: ask technician to test with a known-good SIM
                step = ManualStep(
                    step_id="known_good_sim",
                    instruction=M("network.step_insert_sim"),
                    options=[
                        (INSERTED, M("network.sim_inserted_working")),
                        (NO_SIM, M("network.sim_not_inserted")),
                        (SKIP, M("layered.skip")),
                    ],
                )
                return NetworkDiagResult(
                    status=Status.UNKNOWN,
                    sim_state=primary_sim_state,
                    sim_states=sim_states,
                    baseband_version=baseband,
                    imei=imei,
                    manual_step=step,
                    diagnosis=M("network.sim_absent_prompt"),
                    evidence=evidence,
                )

        # SIM Card IO error
        if any(s in ("CARD_IO_ERROR", "CARD_RESTRICTED") for s in sim_states):
            return NetworkDiagResult(
                status=Status.FAIL,
                sim_state=primary_sim_state,
                sim_states=sim_states,
                baseband_version=baseband,
                imei=imei,
                signal_level=sig_level,
                signal_dbm=sig_dbm,
                registration_state=reg_state,
                fault_area="sim_card",
                confidence="medium",
                diagnosis=M("network.sim_io_error"),
                possible_causes=[
                    M("sim.cause.card"),
                    M("sim.cause.tray"),
                    M("sim.cause.reader"),
                ],
                evidence=evidence,
            )

        # Layer 3: RF Front-End, Antenna, and Power Amplifier (PA)
        sim_ready = any(s in ("READY", "LOADED") for s in sim_states)
        if sim_ready:
            if is_sig_zero:
                # IMEI present, SIM ready, but zero signal constantly.
                # Software observes the symptom; possible_causes lists the electrical/RF candidates.
                return NetworkDiagResult(
                    status=Status.FAIL,
                    sim_state=primary_sim_state,
                    sim_states=sim_states,
                    baseband_version=baseband,
                    imei=imei,
                    signal_level=sig_level,
                    signal_dbm=sig_dbm,
                    registration_state=reg_state,
                    fault_area="rf_antenna",
                    confidence="medium",
                    diagnosis=M("network.fault.rf_antenna"),
                    possible_causes=[
                        M("network.cause.antenna_cable"),
                        M("network.cause.pa_ic"),
                        M("network.cause.transceiver"),
                    ],
                    evidence=evidence,
                )
            elif (sig_level is not None and sig_level > 0) or (sig_dbm is not None and sig_dbm > -120) or reg_state == "IN_SERVICE":
                # IMEI OK, Baseband OK, SIM ready, Signal present -> PASS
                return NetworkDiagResult(
                    status=Status.PASS,
                    sim_state=primary_sim_state,
                    sim_states=sim_states,
                    baseband_version=baseband,
                    imei=imei,
                    signal_level=sig_level,
                    signal_dbm=sig_dbm,
                    registration_state=reg_state,
                    diagnosis=M("network.ok"),
                    evidence=evidence,
                )
            else:
                # Signal data could not be determined conclusively
                return NetworkDiagResult(
                    status=Status.UNKNOWN,
                    sim_state=primary_sim_state,
                    sim_states=sim_states,
                    baseband_version=baseband,
                    imei=imei,
                    signal_level=sig_level,
                    signal_dbm=sig_dbm,
                    registration_state=reg_state,
                    diagnosis=M("sim.inconclusive", state=primary_sim_state),
                    evidence=evidence,
                )

        # Any other SIM state (e.g. PIN_REQUIRED, PUK_REQUIRED)
        return NetworkDiagResult(
            status=Status.UNKNOWN,
            sim_state=primary_sim_state,
            sim_states=sim_states,
            baseband_version=baseband,
            imei=imei,
            signal_level=sig_level,
            signal_dbm=sig_dbm,
            registration_state=reg_state,
            diagnosis=M("sim.inconclusive", state=primary_sim_state),
            evidence=evidence,
        )


def diagnose_network(bridge: Any, serial: Optional[str] = None, answers: Optional[Dict[str, str]] = None) -> NetworkDiagResult:
    """Convenience top-level entry point."""
    return NetworkDiagnostics(bridge).diagnose(serial=serial, answers=answers)
