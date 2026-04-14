#!/usr/bin/env python3
"""Long-run: simulate 1 hour of activity (60 events, 1 per minute)."""

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


class TestLongrun1H(unittest.TestCase):
    """Simulate 1 hour: 20 infra alerts, 20 queries, 10 proposals, 10 actions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import noise_filter as nf
        import action_budget as ab
        import decision_quality as dq
        import preference_learning as pl
        self._nf_orig = nf.STATE_PATH
        self._ab_orig = ab.STATE_PATH
        self._dq_orig = dq.STATE_DIR
        self._pl_orig = pl.PREFS_FILE
        nf.STATE_PATH = Path(self.tmpdir) / "nf.json"
        ab.STATE_PATH = Path(self.tmpdir) / "ab.json"
        dq.STATE_DIR = Path(self.tmpdir) / "dq"
        dq.STATE_DIR.mkdir(parents=True)
        pl.PREFS_FILE = Path(self.tmpdir) / "prefs.json"
        self.nf = nf
        self.ab = ab
        self.dq = dq
        self.pl = pl

    def tearDown(self):
        self.nf.STATE_PATH = self._nf_orig
        self.ab.STATE_PATH = self._ab_orig
        self.dq.STATE_DIR = self._dq_orig
        self.pl.PREFS_FILE = self._pl_orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _run_simulation(self):
        """Run the full 1h simulation. Returns stats."""
        base = time.time()
        telegram_sent = 0
        actions_executed = 0
        decisions = []

        for minute in range(60):
            now = base + minute * 60

            if minute < 20:
                # Infra alerts (same type repeated)
                alert_type = "container_down" if minute % 2 == 0 else "ollama_offline"
                if self.nf.should_send(alert_type, f"event {minute}", _now=now):
                    telegram_sent += 1
                    if self.ab.can_execute("restart_container", _now=now):
                        actions_executed += 1
                        d = self.dq.score(action="restart_container", target="sonarr",
                                          validation_result={"success": True})
                        decisions.append(d)

            elif minute < 40:
                # User queries (skill usage)
                self.pl.record_signal("skill_usage", f"skill:weather")

            elif minute < 50:
                # Proposals (approvals)
                self.pl.record_signal("approval", f"proposal_{minute}")

            else:
                # Actions with mixed outcomes
                success = minute % 3 != 0
                d = self.dq.score(
                    action="action", target="t",
                    validation_result={"success": success},
                    confidence=0.7)
                decisions.append(d)
                actions_executed += 1

        return {
            "telegram_sent": telegram_sent,
            "actions_executed": actions_executed,
            "decisions": decisions,
            "total_events": 60,
        }

    def test_max_5_telegram_notifications(self):
        stats = self._run_simulation()
        self.assertLessEqual(stats["telegram_sent"], 5)

    def test_budget_constrains_actions(self):
        """Action budget limits execution even with many events."""
        stats = self._run_simulation()
        # Budget is 10/hr global + some from last block (different types)
        # Total should be reasonable, not unbounded
        self.assertLessEqual(stats["actions_executed"], 15)

    def test_no_infinite_loops(self):
        """Simulation completes without hanging (implicit: under 5s timeout)."""
        stats = self._run_simulation()
        self.assertEqual(stats["total_events"], 60)

    def test_decision_quality_has_records(self):
        stats = self._run_simulation()
        self.assertGreater(len(stats["decisions"]), 0)

    def test_preference_learning_has_signals(self):
        self._run_simulation()
        top = self.pl.get_top_skills()
        self.assertGreater(len(top), 0)

    def test_alert_reduction_above_60pct(self):
        """At least 60% of raw alerts suppressed by noise_filter."""
        stats = self._run_simulation()
        raw_alerts = 20  # first 20 minutes are infra alerts
        reduction = (raw_alerts - stats["telegram_sent"]) / raw_alerts
        self.assertGreaterEqual(reduction, 0.60)

    def test_action_budget_respected(self):
        self._run_simulation()
        s = self.ab.get_status()
        self.assertLessEqual(s["actions_1h"], self.ab.MAX_ACTIONS_PER_HOUR)

    def test_decision_accuracy_above_70pct(self):
        stats = self._run_simulation()
        if stats["decisions"]:
            good = sum(1 for d in stats["decisions"] if d["quality"] == "good")
            rate = good / len(stats["decisions"])
            self.assertGreaterEqual(rate, 0.70)


class TestLongrun1HMetrics(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import noise_filter as nf
        import action_budget as ab
        import decision_quality as dq
        self._nf_orig = nf.STATE_PATH
        self._ab_orig = ab.STATE_PATH
        self._dq_orig = dq.STATE_DIR
        nf.STATE_PATH = Path(self.tmpdir) / "nf.json"
        ab.STATE_PATH = Path(self.tmpdir) / "ab.json"
        dq.STATE_DIR = Path(self.tmpdir) / "dq"
        dq.STATE_DIR.mkdir(parents=True)
        self.nf = nf
        self.ab = ab
        self.dq = dq

    def tearDown(self):
        self.nf.STATE_PATH = self._nf_orig
        self.ab.STATE_PATH = self._ab_orig
        self.dq.STATE_DIR = self._dq_orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_rollback_rate_below_10pct(self):
        for i in range(10):
            self.dq.score(action="a", target="t",
                          validation_result={"success": True})
        s = self.dq.summary(1)
        # 0 rollbacks / 10 = 0%
        self.assertLessEqual(s["bad_rate"], 0.10)

    def test_action_success_rate_above_80pct(self):
        for i in range(8):
            self.dq.score(action="a", target="t",
                          validation_result={"success": True})
        for i in range(2):
            self.dq.score(action="b", target="t",
                          validation_result={"success": False})
        s = self.dq.summary(1)
        self.assertGreaterEqual(s["good_rate"], 0.80)


if __name__ == "__main__":
    unittest.main()
