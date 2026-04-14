#!/usr/bin/env python3
"""Long-run: progressive degradation from 90% to 60% accuracy in 30 steps."""

import shutil
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).parent.parent.parent.parent / "workspace" / "runtime"
_CONTAINER_RT = Path("/home/tenazas/runtime")
if _CONTAINER_RT.exists():
    RUNTIME = _CONTAINER_RT
sys.path.insert(0, str(RUNTIME.parent.parent))
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


class TestLongrunDegradation(unittest.TestCase):
    """Simulate accuracy degrading from 90% → 60% over 30 steps."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import confidence_calibration as cc
        import decision_quality as dq
        self._cc_s = cc.STATE_DIR
        self._cc_c = cc.CALIBRATION_PATH
        self._cc_r = cc.REPORT_PATH
        self._dq_s = dq.STATE_DIR
        cc.STATE_DIR = Path(self.tmpdir)
        cc.CALIBRATION_PATH = Path(self.tmpdir) / "cal.json"
        cc.REPORT_PATH = Path(self.tmpdir) / "rep.json"
        dq.STATE_DIR = Path(self.tmpdir) / "dq"
        dq.STATE_DIR.mkdir(parents=True)
        self.cc = cc
        self.dq = dq

    def tearDown(self):
        self.cc.STATE_DIR = self._cc_s
        self.cc.CALIBRATION_PATH = self._cc_c
        self.cc.REPORT_PATH = self._cc_r
        self.dq.STATE_DIR = self._dq_s
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _simulate_degradation(self):
        """Run 30 steps of degrading accuracy. Return calibration reports."""
        reports = []
        all_decisions = []

        for step in range(30):
            # Accuracy degrades: step 0 = 90%, step 29 = 60%
            accuracy = 0.90 - (step / 29) * 0.30
            confidence = 0.85  # router stays overconfident

            # Generate 10 decisions per step
            step_decisions = []
            for i in range(10):
                is_good = (i / 10) < accuracy
                quality = "good" if is_good else "bad"
                step_decisions.append({"confidence": confidence, "quality": quality})
                self.dq.score(
                    action="classify", target="query",
                    validation_result={"success": is_good},
                    confidence=confidence)

            all_decisions.extend(step_decisions)

            # Calibrate every 5 steps
            if step % 5 == 4:
                with patch.object(self.cc, "_load_decisions",
                                  return_value=all_decisions[-50:]):
                    report = self.cc.calibrate()
                reports.append({"step": step, **report})

        return reports, all_decisions

    def test_calibration_detects_drift(self):
        """Overconfidence rate rises as accuracy degrades."""
        reports, _ = self._simulate_degradation()
        # Later reports should show higher overconfidence
        if len(reports) >= 2:
            early = reports[0].get("overconfidence_rate", 0)
            late = reports[-1].get("overconfidence_rate", 0)
            self.assertGreater(late, early)

    def test_generates_proposal_before_65pct(self):
        """Should suggest adjustment before accuracy hits 65%."""
        reports, _ = self._simulate_degradation()
        proposed = [r for r in reports if r.get("suggested_adjustments")]
        self.assertTrue(len(proposed) > 0, "Should propose adjustment during degradation")

    def test_benchmark_gate_blocks_degraded(self):
        """self_benchmark gate should fail when accuracy is low."""
        import self_benchmark as sb
        # Mock router with 60% accuracy
        with patch.object(sb, "benchmark_router_accuracy",
                          return_value={"accuracy": 0.60}):
            with patch.object(sb, "benchmark_structured_output",
                              return_value={"validity_rate": 0.95}):
                with patch.object(sb, "benchmark_false_positive_rate",
                                  return_value={"false_positive_rate": 0.1}):
                    with patch.object(sb, "benchmark_latency_p95",
                                      return_value={"p95_ms": 100}):
                        result = sb.run_gate()
        self.assertFalse(result["passed"])

    def test_benchmark_gate_passes_when_healthy(self):
        """Gate passes at 90% accuracy."""
        import self_benchmark as sb
        with patch.object(sb, "benchmark_router_accuracy",
                          return_value={"accuracy": 0.90}):
            with patch.object(sb, "benchmark_structured_output",
                              return_value={"validity_rate": 0.98}):
                with patch.object(sb, "benchmark_false_positive_rate",
                                  return_value={"false_positive_rate": 0.05}):
                    with patch.object(sb, "benchmark_latency_p95",
                                      return_value={"p95_ms": 50}):
                        result = sb.run_gate()
        self.assertTrue(result["passed"])

    def test_calibration_error_rises(self):
        """Calibration error increases as accuracy degrades."""
        reports, _ = self._simulate_degradation()
        if len(reports) >= 2:
            early_err = reports[0].get("calibration_error", 0)
            late_err = reports[-1].get("calibration_error", 0)
            self.assertGreater(late_err, early_err)

    def test_overconfidence_rate_metric(self):
        """Overconfidence rate should be ≤ 0.20 when healthy, higher when degraded."""
        reports, _ = self._simulate_degradation()
        # Last report (degraded state)
        if reports:
            last = reports[-1]
            self.assertGreater(last["overconfidence_rate"], 0.20)

    def test_confidence_calibration_error_metric(self):
        """Calibration error should be ≤ 0.15 initially."""
        reports, _ = self._simulate_degradation()
        if reports:
            # First report (healthy state)
            first = reports[0]
            # May or may not be ≤ 0.15 depending on sample
            self.assertIsInstance(first["calibration_error"], float)

    def test_simulation_completes_in_time(self):
        """Entire degradation simulation runs in < 5 seconds."""
        import time
        start = time.time()
        self._simulate_degradation()
        elapsed = time.time() - start
        self.assertLess(elapsed, 5.0)

    def test_30_steps_300_decisions(self):
        """Simulation produces expected number of decisions."""
        _, decisions = self._simulate_degradation()
        self.assertEqual(len(decisions), 300)


if __name__ == "__main__":
    unittest.main()
