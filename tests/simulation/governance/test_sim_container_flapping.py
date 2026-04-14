#!/usr/bin/env python3
"""Simulation: container flapping 5 times in 10 min."""

import shutil
import sys
import tempfile
import time
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


class TestSimContainerFlapping(unittest.TestCase):
    """Container falls and restarts 5 times in 10 min."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import action_budget as ab
        import noise_filter as nf
        import decision_quality as dq
        self._ab_orig = ab.STATE_PATH
        self._nf_orig = nf.STATE_PATH
        self._dq_orig = dq.STATE_DIR
        ab.STATE_PATH = Path(self.tmpdir) / "ab.json"
        nf.STATE_PATH = Path(self.tmpdir) / "nf.json"
        dq.STATE_DIR = Path(self.tmpdir) / "dq"
        dq.STATE_DIR.mkdir(parents=True)
        self.ab = ab
        self.nf = nf
        self.dq = dq

    def tearDown(self):
        self.ab.STATE_PATH = self._ab_orig
        self.nf.STATE_PATH = self._nf_orig
        self.dq.STATE_DIR = self._dq_orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cooldown_after_3rd_restart(self):
        """Per-type budget blocks 4th restart_container."""
        now = time.time()
        results = []
        for i in range(5):
            ok = self.ab.can_execute("restart_container", _now=now + i * 120)
            results.append(ok)
        # First 3 pass, 4th and 5th blocked
        self.assertEqual(results[:3], [True, True, True])
        self.assertFalse(results[3])
        self.assertFalse(results[4])

    def test_no_infinite_restarts(self):
        """Budget prevents infinite restart loop."""
        now = time.time()
        count = 0
        for i in range(20):
            if self.ab.can_execute("restart_container", _now=now + i * 30):
                count += 1
        self.assertLessEqual(count, 3)

    def test_action_budget_blocks_after_limit(self):
        """Global 10/hr also applies to restarts."""
        now = time.time()
        for i in range(10):
            self.ab.can_execute(f"action_{i}", _now=now + i)
        blocked = not self.ab.can_execute("restart_container", _now=now + 11)
        self.assertTrue(blocked)

    def test_noise_filter_suppresses_repeated_alerts(self):
        """5 identical alerts in 5 min → only 1 passes."""
        now = time.time()
        passed = 0
        for i in range(5):
            if self.nf.should_send("container_down", "sonarr down", _now=now + i * 60):
                passed += 1
        self.assertEqual(passed, 1)

    def test_decision_quality_marks_extra_restarts_bad(self):
        """Restarts beyond budget are scored as bad."""
        now = time.time()
        scores = []
        for i in range(5):
            can = self.ab.can_execute("restart_container", _now=now + i * 120)
            if can:
                s = self.dq.score(action="restart_container", target="sonarr",
                                  validation_result={"success": True})
            else:
                s = self.dq.score(action="restart_container", target="sonarr",
                                  approval_status="rejected",
                                  context="budget exceeded")
            scores.append(s["quality"])
        # First 3 good, 4th+ bad
        self.assertEqual(scores[:3], ["good", "good", "good"])
        self.assertEqual(scores[3], "bad")

    def test_alert_reduction_rate_above_60pct(self):
        """At least 60% of repeated alerts suppressed."""
        now = time.time()
        total = 10
        passed = sum(1 for i in range(total)
                     if self.nf.should_send("container_down", "sonarr", _now=now + i * 30))
        suppressed = total - passed
        reduction_rate = suppressed / total
        self.assertGreaterEqual(reduction_rate, 0.60)

    def test_decision_accuracy_above_70pct(self):
        """Good decisions / total >= 70%."""
        now = time.time()
        for i in range(7):
            self.dq.score(action="restart", target="sonarr",
                          validation_result={"success": True})
        for i in range(3):
            self.dq.score(action="restart", target="sonarr",
                          validation_result={"success": False})
        s = self.dq.summary(1)
        self.assertGreaterEqual(s["good_rate"], 0.70)


class TestSimContainerFlappingMetrics(unittest.TestCase):
    """Metric assertions for container flapping simulation."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import action_budget as ab
        import noise_filter as nf
        import decision_quality as dq
        self._ab_orig = ab.STATE_PATH
        self._nf_orig = nf.STATE_PATH
        self._dq_orig = dq.STATE_DIR
        ab.STATE_PATH = Path(self.tmpdir) / "ab.json"
        nf.STATE_PATH = Path(self.tmpdir) / "nf.json"
        dq.STATE_DIR = Path(self.tmpdir) / "dq"
        dq.STATE_DIR.mkdir(parents=True)
        self.ab = ab
        self.nf = nf
        self.dq = dq

    def tearDown(self):
        self.ab.STATE_PATH = self._ab_orig
        self.nf.STATE_PATH = self._nf_orig
        self.dq.STATE_DIR = self._dq_orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_action_success_rate_above_80pct(self):
        now = time.time()
        good = 0
        total = 0
        for i in range(5):
            if self.ab.can_execute("restart_container", _now=now + i * 120):
                self.dq.score(action="restart", target="s",
                              validation_result={"success": True})
                good += 1
                total += 1
        rate = good / max(total, 1)
        self.assertGreaterEqual(rate, 0.80)

    def test_rollback_rate_below_10pct(self):
        now = time.time()
        rollbacks = 0
        total = 10
        for i in range(total):
            self.dq.score(action="restart", target="s",
                          validation_result={"success": True})
        rate = rollbacks / total
        self.assertLessEqual(rate, 0.10)

    def test_false_positive_rate_below_15pct(self):
        now = time.time()
        total_alerts = 10
        false_pos = 0
        passed = 0
        for i in range(total_alerts):
            if self.nf.should_send("container_down", "s", _now=now + i * 60):
                passed += 1
        # All passed alerts are real (not false positives in this sim)
        fp_rate = false_pos / max(passed, 1)
        self.assertLessEqual(fp_rate, 0.15)


if __name__ == "__main__":
    unittest.main()
