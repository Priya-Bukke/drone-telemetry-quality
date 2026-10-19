"""Run with: python -m pytest -q   (or python -m unittest discover -s tests -t .)"""
import tempfile
import unittest
from pathlib import Path

from telemetry import quality, simulate


class TelemetryQualityTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        csv = Path(cls.tmp.name) / "flights.csv"
        cls.manifest = simulate.generate(csv, duration_s=60, seed=3)
        cls.conn = quality.connect(Path(cls.tmp.name) / "t.db")
        quality.load_csv(cls.conn, csv)
        quality.set_params(cls.conn)
        cls.summary = {r["flight_id"]: r for r in quality.run_checks(cls.conn)}

    @classmethod
    def tearDownClass(cls):
        cls.conn.close()
        cls.tmp.cleanup()

    def test_clean_flight_is_ok(self):
        f = self.summary["F01"]
        self.assertEqual(f["status"], "OK")
        for k in ("gaps", "out_of_order", "frozen_samples", "gps_jumps", "range_issues"):
            self.assertEqual(f[k], 0, k)

    def test_dropouts_detected(self):
        f, m = self.summary["F02"], self.manifest["F02"]
        self.assertEqual(f["gaps"], m["gaps"])
        self.assertEqual(f["expected_samples"] - f["samples"], m["missing_samples"])
        self.assertEqual(f["max_dt_ms"], 1020)
        self.assertEqual(f["status"], "REVIEW")

    def test_out_of_order_detected(self):
        self.assertEqual(self.summary["F03"]["out_of_order"], self.manifest["F03"]["out_of_order"])
        self.assertEqual(self.summary["F03"]["gaps"], 0)   # a swap must not look like a gap

    def test_frozen_sensor_detected(self):
        self.assertEqual(self.summary["F04"]["frozen_samples"], self.manifest["F04"]["frozen_samples"])

    def test_gps_jump_detected_without_false_positives(self):
        self.assertEqual(self.summary["F05"]["gps_jumps"], self.manifest["F05"]["gps_jump_segments"])
        others = sum(r["gps_jumps"] for fid, r in self.summary.items() if fid != "F05")
        self.assertEqual(others, 0)

    def test_accel_saturation_detected(self):
        self.assertEqual(self.summary["F06"]["range_issues"], self.manifest["F06"]["range_violations"])

    def test_haversine(self):
        # 0.001 deg of latitude ~ 111.2 m
        self.assertAlmostEqual(quality.haversine_m(49.0, 8.4, 49.001, 8.4), 111.2, delta=0.5)
        self.assertIsNone(quality.haversine_m(None, 8.4, 49.0, 8.4))

    def test_thresholds_are_configurable(self):
        quality.set_params(self.conn, max_speed_mps=1.0)   # absurdly strict
        strict = {r["flight_id"]: r for r in quality.run_checks(self.conn)}
        self.assertGreater(strict["F01"]["gps_jumps"], 0)
        quality.set_params(self.conn)                      # restore defaults
        quality.run_checks(self.conn)


if __name__ == "__main__":
    unittest.main()
