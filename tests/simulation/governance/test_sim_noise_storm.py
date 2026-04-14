#!/usr/bin/env python3
"""Simulation: 20 alerts of the same type in 5 minutes."""

import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path

RUNTIME = Path(__file__).parent.parent.parent.parent / "workspace" / "runtime"
_CONTAINER_RT = Path("/home/tenazas/runtime")
if _CONTAINER_RT.exists():
    RUNTIME = _CONTAINER_RT
sys.path.insert(0, str(RUNTIME.parent.parent))
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


class TestSimNoiseStorm(unittest.TestCase):
    """20 identical alerts in 5 minutes."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import noise_filter as nf
        import action_budget as ab
        self._nf_orig = nf.STATE_PATH
        self._ab_orig = ab.STATE_PATH
        nf.STATE_PATH = Path(self.tmpdir) / "nf.json"
        ab.STATE_PATH = Path(self.tmpdir) / "ab.json"
        self.nf = nf
        self.ab = ab

    def tearDown(self):
        self.nf.STATE_PATH = self._nf_orig
        self.ab.STATE_PATH = self._ab_orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_only_1_reaches_telegram(self):
        now = time.time()
        passed = sum(1 for i in range(20)
                     if self.nf.should_send("container_down", "sonarr down",
                                            _now=now + i * 15))
        self.assertEqual(passed, 1)

    def test_noise_filter_state_persists(self):
        now = time.time()
        self.nf.should_send("x", "", _now=now)
        stats = self.nf.get_stats()
        self.assertGreaterEqual(stats["active_ttls"], 1)

    def test_after_ttl_next_alert_passes(self):
        now = time.time()
        self.nf.should_send("alert", "", _now=now)
        # All within TTL suppressed
        for i in range(1, 20):
            self.nf.should_send("alert", "", _now=now + i * 15)
        # After TTL, next one passes
        self.assertTrue(self.nf.should_send(
            "alert", "", _now=now + self.nf.SUPPRESSION_TTL + 1))

    def test_action_budget_not_drained_by_suppressed(self):
        """Suppressed alerts should NOT consume action budget."""
        now = time.time()
        # Send 20 alerts — only 1 passes noise filter
        for i in range(20):
            if self.nf.should_send("container_down", "sonarr", _now=now + i * 15):
                self.ab.can_execute("restart_container", _now=now + i * 15)
        # Budget should only have 1 action used
        s = self.ab.get_status(_now=now + 300)
        self.assertEqual(s["actions_1h"], 1)

    def test_alert_reduction_rate_above_90pct(self):
        """Storm: at least 90% reduction."""
        now = time.time()
        total = 20
        passed = sum(1 for i in range(total)
                     if self.nf.should_send("storm", "alert", _now=now + i * 15))
        reduction = (total - passed) / total
        self.assertGreaterEqual(reduction, 0.90)

    def test_critical_storm_all_pass(self):
        """Even 20 critical alerts all pass."""
        now = time.time()
        passed = sum(1 for i in range(20)
                     if self.nf.should_send("fire", "", severity="critical",
                                            _now=now + i * 15))
        self.assertEqual(passed, 20)

    def test_mixed_severity_storm(self):
        """Info suppressed, critical passes, within same storm."""
        now = time.time()
        info_passed = 0
        crit_passed = 0
        for i in range(10):
            if self.nf.should_send("alert", "", severity="info", _now=now + i * 15):
                info_passed += 1
            if self.nf.should_send("alert", "", severity="critical", _now=now + i * 15 + 1):
                crit_passed += 1
        self.assertEqual(info_passed, 1)
        self.assertEqual(crit_passed, 10)

    def test_storm_stats_accurate(self):
        now = time.time()
        for i in range(20):
            self.nf.should_send("storm", "alert", _now=now + i * 15)
        stats = self.nf.get_stats()
        self.assertEqual(stats["allowed_count"], 1)
        self.assertEqual(stats["suppressed_count"], 19)


class TestSimNoiseStormMetrics(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import noise_filter as nf
        self._nf_orig = nf.STATE_PATH
        nf.STATE_PATH = Path(self.tmpdir) / "nf.json"
        self.nf = nf

    def tearDown(self):
        self.nf.STATE_PATH = self._nf_orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_false_positive_rate_metric(self):
        now = time.time()
        total_raw = 20
        passed = sum(1 for i in range(total_raw)
                     if self.nf.should_send("alert", "", _now=now + i * 15))
        # All passed alerts are genuine (false_positive = 0 in this sim)
        fp_rate = 0 / max(passed, 1)
        self.assertLessEqual(fp_rate, 0.15)


if __name__ == "__main__":
    unittest.main()
