"""Live charging curve: voltage, current, power and temperature sampled over time.

Builds a time series by taking the existing evidence-backed battery snapshot
(MotherboardDiagnostics.analyze_pmic) once per interval, so it reuses the same
sysfs / dumpsys reads and inherits their honesty: a value that cannot be read is
recorded as None, never invented, and a sample is kept even when partial.

Nothing here passes a health verdict on the battery - that is the fuel-gauge
check's job. This only captures what the charger is doing, second by second, as
evidence (for example to see current taper as the cell fills, or temperature
climb under fast charge). The clock and sleep are injectable so the capture can
be driven deterministically in tests without waiting in real time.
"""
import time
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional

from core.result import Evidence, M, ProducesCheckResult, Status

# A battery temperature at or above this, while charging, is worth flagging as hot.
HOT_TEMP_C = 45.0


@dataclass
class ChargingSample:
    t_seconds: float
    voltage_mv: Optional[float]
    current_ma: Optional[float]
    temp_c: Optional[float]
    is_charging: bool = False
    status: str = ""

    @property
    def power_w(self) -> Optional[float]:
        if self.voltage_mv is None or self.current_ma is None:
            return None
        return (self.voltage_mv / 1000.0) * (self.current_ma / 1000.0)


class ChargingCurve(ProducesCheckResult):
    check_id = "battery.charging_curve"
    _check_detail_attr = "detail"

    def __init__(self, samples: List[ChargingSample], requested_s: float):
        self.samples = samples
        self.requested_s = requested_s
        self.possible_causes: List[Any] = []
        self.evidence: List[Evidence] = []
        self._summarize()

    # ---- derived, honest summary ----
    def _summarize(self):
        valid_p = [s.power_w for s in self.samples if s.power_w is not None]
        temps = [s.temp_c for s in self.samples if s.temp_c is not None]
        self.sample_count = len(self.samples)
        self.duration_s = self.samples[-1].t_seconds if self.samples else 0.0
        self.charging_seen = any(s.is_charging for s in self.samples)
        self.peak_power_w = max(valid_p) if valid_p else None
        self.min_power_w = min(valid_p) if valid_p else None
        self.temp_start_c = temps[0] if temps else None
        self.temp_end_c = temps[-1] if temps else None
        self.temp_rise_c = (temps[-1] - temps[0]) if len(temps) >= 2 else None
        self.max_temp_c = max(temps) if temps else None
        self.hot_while_charging = any(
            s.is_charging and s.temp_c is not None and s.temp_c >= HOT_TEMP_C
            for s in self.samples)

        if not valid_p:
            # Nothing usable was read - say so, do not pretend a curve exists.
            self.status = Status.UNKNOWN
            self.detail = M("charge_curve.no_data")
            return
        self.status = Status.PASS
        if self.hot_while_charging:
            self.detail = M("charge_curve.captured_hot", n=self.sample_count,
                            peak=round(self.peak_power_w, 1), tmax=round(self.max_temp_c, 1))
        else:
            self.detail = M("charge_curve.captured", n=self.sample_count,
                            peak=round(self.peak_power_w, 1))

    def _check_value(self):
        if self.peak_power_w is None:
            return None, ""
        return round(self.peak_power_w, 1), "W"

    def to_rows(self) -> List[dict]:
        """Plain rows for a chart / CSV export: one dict per sample, None where unread."""
        out = []
        for s in self.samples:
            out.append({
                "t": round(s.t_seconds, 1),
                "voltage_mv": s.voltage_mv,
                "current_ma": s.current_ma,
                "power_w": round(s.power_w, 3) if s.power_w is not None else None,
                "temp_c": s.temp_c,
                "charging": s.is_charging,
            })
        return out


class ChargingCurveCapture:
    def __init__(self, diag, sampler: Optional[Callable[[str], Any]] = None,
                 sleep: Callable[[float], None] = time.sleep,
                 clock: Callable[[], float] = time.monotonic):
        self.diag = diag
        self._sampler = sampler or (lambda serial: self.diag.analyze_pmic(serial))
        self._sleep = sleep
        self._clock = clock

    def _sample(self, serial: str, t: float) -> ChargingSample:
        pmic = self._sampler(serial)
        return ChargingSample(
            t_seconds=t,
            voltage_mv=getattr(pmic, "voltage_mv", None),
            current_ma=getattr(pmic, "current_ma", None),
            temp_c=getattr(pmic, "temp_c", None),
            is_charging=bool(getattr(pmic, "is_charging", False)),
            status=getattr(pmic, "status", "") or "",
        )

    def capture(self, serial: str, duration_s: float = 60.0, interval_s: float = 1.0,
                progress: Optional[Callable[[ChargingSample], None]] = None,
                should_stop: Optional[Callable[[], bool]] = None) -> ChargingCurve:
        """Sample once per interval until duration elapses. interval_s is clamped to a
        sane floor so a bad argument can never spin. Always takes at least one sample."""
        interval_s = max(0.1, interval_s)
        samples: List[ChargingSample] = []
        start = self._clock()
        while True:
            elapsed = self._clock() - start
            s = self._sample(serial, elapsed)
            samples.append(s)
            if progress:
                progress(s)
            if elapsed >= duration_s or (should_stop and should_stop()):
                break
            self._sleep(interval_s)
        return ChargingCurve(samples, duration_s)
