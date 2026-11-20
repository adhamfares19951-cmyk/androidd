"""Charging curve capture: deterministic (injected clock/sleep), honest about missing
readings, and never passing a battery health verdict of its own."""
import unittest

from core.result import Status
from core.charging_curve import ChargingCurveCapture, ChargingCurve, ChargingSample


class _PMIC:
    def __init__(self, v=None, c=None, t=None, charging=False, status="Charging"):
        self.voltage_mv, self.current_ma, self.temp_c = v, c, t
        self.is_charging, self.status = charging, status


class _FakeTime:
    """Virtual clock: reads do not advance time; only sleep() does (like real time)."""
    def __init__(self):
        self.t = 0.0
    def clock(self):
        return self.t
    def sleep(self, s):
        self.t += s


def _capture(pmic_sequence, duration=5, interval=1):
    seq = iter(pmic_sequence)
    last = [pmic_sequence[-1]]

    def sampler(serial):
        try:
            return next(seq)
        except StopIteration:
            return last[0]

    ft = _FakeTime()
    cap = ChargingCurveCapture(diag=None, sampler=sampler, sleep=ft.sleep, clock=ft.clock)
    return cap.capture("S", duration_s=duration, interval_s=interval)


class ChargingCurveTests(unittest.TestCase):
    def test_power_is_volts_times_amps(self):
        s = ChargingSample(0, voltage_mv=4000, current_ma=2000, temp_c=30)
        self.assertAlmostEqual(s.power_w, 8.0)        # 4.0 V * 2.0 A

    def test_missing_reading_leaves_power_none(self):
        self.assertIsNone(ChargingSample(0, voltage_mv=4000, current_ma=None, temp_c=30).power_w)

    def test_capture_builds_series_and_peak(self):
        curve = _capture([
            _PMIC(4000, 1000, 30, charging=True),
            _PMIC(4100, 1500, 31, charging=True),
            _PMIC(4200, 1200, 32, charging=True),
        ], duration=2, interval=1)
        self.assertEqual(curve.status, Status.PASS)
        self.assertGreaterEqual(curve.sample_count, 3)
        self.assertAlmostEqual(curve.peak_power_w, 4.1 * 1.5, places=3)   # 6.15 W
        self.assertTrue(curve.charging_seen)
        self.assertEqual(len(curve.to_rows()), curve.sample_count)

    def test_all_unreadable_is_unknown_not_fabricated(self):
        curve = _capture([_PMIC(None, None, None)], duration=1, interval=1)
        self.assertEqual(curve.status, Status.UNKNOWN)
        self.assertIsNone(curve.peak_power_w)

    def test_hot_while_charging_is_flagged(self):
        curve = _capture([
            _PMIC(4300, 3000, 44, charging=True),
            _PMIC(4350, 3000, 46, charging=True),   # >= 45 C while charging
        ], duration=1, interval=1)
        self.assertTrue(curve.hot_while_charging)
        self.assertEqual(curve.status, Status.PASS)   # hot is noted, not an invented FAIL

    def test_temp_rise_computed(self):
        curve = _capture([_PMIC(4000, 1000, 30, charging=True),
                          _PMIC(4000, 1000, 35, charging=True)], duration=1, interval=1)
        self.assertAlmostEqual(curve.temp_rise_c, 5.0)


if __name__ == "__main__":
    unittest.main()
