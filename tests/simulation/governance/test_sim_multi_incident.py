#!/usr/bin/env python3
"""Simulation: 3 simultaneous incidents with priority ordering."""

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


class TestSimMultiIncident(unittest.TestCase):
    """3 incidents: container_down + ollama_dead + vpn_leak."""

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

    def test_vpn_leak_highest_priority(self):
        """VPN leak is critical, should be processed first."""
        incidents = [
            {"type": "container_down", "severity": "warning", "target": "sonarr"},
            {"type": "ollama_dead", "severity": "warning", "target": "homelab"},
            {"type": "vpn_leak", "severity": "critical", "target": "gluetun"},
        ]
        # Sort by severity priority
        priority = {"critical": 0, "warning": 1, "info": 2}
        sorted_inc = sorted(incidents, key=lambda x: priority.get(x["severity"], 9))
        self.assertEqual(sorted_inc[0]["type"], "vpn_leak")

    def test_3_incidents_3_separate_alerts(self):
        """Each incident type passes noise filter independently."""
        now = time.time()
        types = ["container_down", "ollama_dead", "vpn_leak"]
        passed = sum(1 for t in types
                     if self.nf.should_send(t, f"{t} detected", _now=now))
        self.assertEqual(passed, 3)

    def test_budget_distributes_across_incidents(self):
        """Each incident gets its own action within budget."""
        now = time.time()
        actions = [
            ("restart_container", "sonarr"),
            ("restart_ollama", "homelab"),
            ("restart_gluetun", "gluetun"),
        ]
        allowed = []
        for i, (action, target) in enumerate(actions):
            if self.ab.can_execute(action, _now=now + i):
                allowed.append(action)
        self.assertEqual(len(allowed), 3)

    def test_3_separate_decision_scores(self):
        """Each incident gets its own decision_quality score."""
        self.dq.score(action="restart_container", target="sonarr",
                      validation_result={"success": True})
        self.dq.score(action="restart_ollama", target="homelab",
                      validation_result={"success": True})
        self.dq.score(action="restart_gluetun", target="gluetun",
                      validation_result={"success": True})
        entries = self.dq.get_today()
        self.assertEqual(len(entries), 3)
        actions = {e["action"] for e in entries}
        self.assertEqual(len(actions), 3)

    def test_critical_uses_critical_budget(self):
        """VPN leak (critical) uses the critical budget slot."""
        now = time.time()
        self.ab.can_execute("restart_gluetun", severity="critical", _now=now)
        s = self.ab.get_status(_now=now + 1)
        self.assertEqual(s["critical_6h"], 1)

    def test_second_critical_blocked(self):
        """Only 1 critical action per 6h window."""
        now = time.time()
        self.ab.can_execute("critical_1", severity="critical", _now=now)
        self.assertFalse(self.ab.can_execute("critical_2", severity="critical",
                                             _now=now + 10))

    def test_non_critical_still_allowed_after_critical(self):
        """Warning actions still pass after critical budget used."""
        now = time.time()
        self.ab.can_execute("critical", severity="critical", _now=now)
        self.assertTrue(self.ab.can_execute("restart_container", _now=now + 1))

    def test_all_3_actions_scored_independently(self):
        """Decision quality tracks each action separately."""
        self.dq.score(action="restart_gluetun", target="gluetun",
                      validation_result={"success": True}, confidence=0.9)
        self.dq.score(action="restart_container", target="sonarr",
                      validation_result={"success": False}, confidence=0.7)
        self.dq.score(action="restart_ollama", target="homelab",
                      validation_result={"success": True}, confidence=0.6)
        s = self.dq.summary(1)
        self.assertEqual(s["good"], 2)
        self.assertEqual(s["bad"], 1)


class TestSimMultiIncidentMetrics(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import decision_quality as dq
        self._dq_orig = dq.STATE_DIR
        dq.STATE_DIR = Path(self.tmpdir) / "dq"
        dq.STATE_DIR.mkdir(parents=True)
        self.dq = dq

    def tearDown(self):
        self.dq.STATE_DIR = self._dq_orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_decision_accuracy_multi_incident(self):
        """2 good + 1 bad = 66% accuracy (above 70% would need 3 good)."""
        self.dq.score(action="a", target="t", validation_result={"success": True})
        self.dq.score(action="b", target="t", validation_result={"success": True})
        self.dq.score(action="c", target="t", validation_result={"success": False})
        s = self.dq.summary(1)
        self.assertAlmostEqual(s["good_rate"], 0.67, places=1)

    def test_action_success_rate(self):
        """Track success rate across multi-incident."""
        self.dq.score(action="a", target="t", validation_result={"success": True})
        self.dq.score(action="b", target="t", validation_result={"success": True})
        self.dq.score(action="c", target="t", validation_result={"success": True})
        s = self.dq.summary(1)
        self.assertGreaterEqual(s["good_rate"], 0.80)


if __name__ == "__main__":
    unittest.main()
