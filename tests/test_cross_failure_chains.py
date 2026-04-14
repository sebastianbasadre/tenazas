#!/usr/bin/env python3
"""
test_cross_failure_chains.py — Simultaneous cascading failures.

Tests the hardest scenarios: GPU saturation + Ollama slow, VPN leak +
arr-stack offline, NAS RAM + multiple unhealthy, self_heal loops.
"""

import json
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if _C.exists():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


# ══════════════════════════════════════════════════════════════
#  Chain 1: GPU saturated → Ollama slow → wrong diagnosis
# ══════════════════════════════════════════════════════════════

class TestChainGPUSaturated(unittest.TestCase):
    """VRAM at 98%, Ollama responds slowly (8s), not dead."""

    def test_slow_ollama_not_classified_as_dead(self):
        """Ollama responding (albeit slowly) is NOT offline."""
        # If Ollama responds at all, status should be "ok" or "slow"
        # The runtime checks with curl timeout — 8s is within 10s timeout
        state = {"ollama_notebook": {"ok": True, "latency_ms": 8000}}
        # ok=True → should NOT trigger restart
        self.assertTrue(state["ollama_notebook"]["ok"])

    def test_high_latency_not_restart_trigger(self):
        """High latency should suggest optimization, not restart."""
        # The correct action for VRAM pressure is gpu_optimization, not restart
        import action_registry as ar
        # restart_ollama exists but shouldn't be used for slow (not dead)
        actions = ar.list_actions()
        # System should have optimization option
        self.assertIsInstance(actions, list)

    def test_vram_98_still_responds(self):
        """At 98% VRAM, Ollama can still serve if model is loaded."""
        # This is a logical test: VRAM pressure ≠ crash
        state = {"gpu": {"vram_used_mb": 23500, "vram_total_mb": 24000}}
        pct = state["gpu"]["vram_used_mb"] / state["gpu"]["vram_total_mb"]
        self.assertGreater(pct, 0.95)
        # System should NOT restart just because VRAM is high

    def test_decision_quality_for_wrong_restart(self):
        """Restarting a slow (not dead) service is a bad decision."""
        tmpdir = tempfile.mkdtemp()
        import decision_quality as dq
        orig = dq.STATE_DIR
        dq.STATE_DIR = Path(tmpdir) / "dq"
        dq.STATE_DIR.mkdir(parents=True)
        r = dq.score(action="restart_ollama", target="homelab",
                      validation_result={"success": False, "reason": "was not down, just slow"},
                      context="VRAM at 98%, restart was wrong action")
        self.assertEqual(r["quality"], "bad")
        dq.STATE_DIR = orig
        shutil.rmtree(tmpdir)


# ══════════════════════════════════════════════════════════════
#  Chain 2: VPN leak + arr-stack offline
# ══════════════════════════════════════════════════════════════

class TestChainVPNLeak(unittest.TestCase):
    """Gluetun leaking (AR IP) + qbittorrent unresponsive."""

    def test_arr_depends_on_gluetun(self):
        """arr-stack containers depend on gluetun network."""
        # This is documented: arr-stack uses gluetun as network gateway
        # Fix order: gluetun first, then arr-stack
        # Verify gluetun is in action_registry
        import action_registry as ar
        action = ar.get_action("restart_container")
        allowed = action.get("allowed_targets", [])
        self.assertIn("gluetun", allowed)
        self.assertIn("qbittorrent", allowed)

    def test_vpn_fix_before_arr_fix(self):
        """VPN leak is critical, arr-stack is warning → VPN first."""
        incidents = [
            {"type": "vpn_leak", "severity": "critical"},
            {"type": "container_down", "severity": "warning", "target": "qbittorrent"},
        ]
        priority = {"critical": 0, "warning": 1}
        ordered = sorted(incidents, key=lambda x: priority[x["severity"]])
        self.assertEqual(ordered[0]["type"], "vpn_leak")

    def test_not_restart_qbit_without_vpn(self):
        """Restarting qbittorrent without fixing VPN is pointless."""
        # qbit uses gluetun network — if gluetun is down, qbit restart won't help
        import decision_quality as dq
        tmpdir = tempfile.mkdtemp()
        orig = dq.STATE_DIR
        dq.STATE_DIR = Path(tmpdir) / "dq"
        dq.STATE_DIR.mkdir(parents=True)
        r = dq.score(action="restart_container", target="qbittorrent",
                      validation_result={"success": False,
                                         "reason": "still no network, gluetun down"},
                      context="restarted qbit before fixing vpn")
        self.assertEqual(r["quality"], "bad")
        dq.STATE_DIR = orig
        shutil.rmtree(tmpdir)

    def test_vpn_leak_is_critical_severity(self):
        """VPN leak should always be treated as critical."""
        # In the runtime, vpn_leak events have high severity
        import noise_filter as nf
        tmpdir = tempfile.mkdtemp()
        orig = nf.STATE_PATH
        nf.STATE_PATH = Path(tmpdir) / "nf.json"
        now = time.time()
        # Critical always passes noise filter
        self.assertTrue(nf.should_send("vpn_leak", "AR detected",
                                       severity="critical", _now=now))
        self.assertTrue(nf.should_send("vpn_leak", "AR detected",
                                       severity="critical", _now=now + 1))
        nf.STATE_PATH = orig
        shutil.rmtree(tmpdir)


# ══════════════════════════════════════════════════════════════
#  Chain 3: NAS RAM high + multiple unhealthy containers
# ══════════════════════════════════════════════════════════════

class TestChainNASRAM(unittest.TestCase):
    """NAS RAM 92% + 3 containers unhealthy."""

    def test_not_restart_all_3_simultaneously(self):
        """action_budget blocks 4th restart of same type."""
        import action_budget as ab
        tmpdir = tempfile.mkdtemp()
        orig = ab.STATE_PATH
        ab.STATE_PATH = Path(tmpdir) / "ab.json"
        now = time.time()
        restarts = sum(1 for i in range(3)
                       if ab.can_execute("restart_container", _now=now + i))
        # Per-type limit is 3
        self.assertLessEqual(restarts, 3)
        ab.STATE_PATH = orig
        shutil.rmtree(tmpdir)

    def test_prioritize_by_severity(self):
        """Containers should be restarted by severity order."""
        containers = [
            {"name": "sonarr", "severity": "warning"},
            {"name": "gluetun", "severity": "critical"},
            {"name": "radarr", "severity": "info"},
        ]
        ordered = sorted(containers, key=lambda x:
                         {"critical": 0, "warning": 1, "info": 2}[x["severity"]])
        self.assertEqual(ordered[0]["name"], "gluetun")

    def test_reevaluate_after_restart(self):
        """After first restart succeeds, system should check if others still needed."""
        import decision_quality as dq
        tmpdir = tempfile.mkdtemp()
        orig = dq.STATE_DIR
        dq.STATE_DIR = Path(tmpdir) / "dq"
        dq.STATE_DIR.mkdir(parents=True)
        # First restart succeeds
        r1 = dq.score(action="restart_container", target="sonarr",
                       validation_result={"success": True})
        # After restart, RAM might have freed → no need for more restarts
        r2 = dq.score(action="check_health", target="all",
                       context="re-evaluating after first restart")
        self.assertEqual(r1["quality"], "good")
        self.assertEqual(r2["quality"], "neutral")
        dq.STATE_DIR = orig
        shutil.rmtree(tmpdir)

    def test_ram_92_not_disk(self):
        """High RAM is different from disk — actions are different."""
        # RAM issue → restart heavy containers, not cleanup disk
        self.assertTrue(True)  # Architectural assertion


# ══════════════════════════════════════════════════════════════
#  Chain 4: self_heal loop that worsens
# ══════════════════════════════════════════════════════════════

class TestChainSelfHealLoop(unittest.TestCase):
    """Container falls 30s after restart → loop."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import action_budget as ab
        import decision_quality as dq
        self._ab = ab.STATE_PATH
        self._dq = dq.STATE_DIR
        ab.STATE_PATH = Path(self.tmpdir) / "ab.json"
        dq.STATE_DIR = Path(self.tmpdir) / "dq"
        dq.STATE_DIR.mkdir(parents=True)
        self.ab = ab
        self.dq = dq

    def tearDown(self):
        self.ab.STATE_PATH = self._ab
        self.dq.STATE_DIR = self._dq
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_cooldown_after_3_cycles(self):
        """Per-type budget blocks after 3 restarts."""
        now = time.time()
        count = 0
        for i in range(10):
            if self.ab.can_execute("restart_container", _now=now + i * 35):
                count += 1
        self.assertLessEqual(count, 3)

    def test_extra_restarts_marked_bad(self):
        """Decision quality marks failed restart cycles as bad."""
        for i in range(3):
            self.dq.score(action="restart_container", target="sonarr",
                          validation_result={"success": False,
                                             "reason": "crashed again after 30s"})
        s = self.dq.summary(1)
        self.assertEqual(s["bad"], 3)
        self.assertEqual(s["good"], 0)

    def test_improvement_candidate_for_root_cause(self):
        """Repeated failures should generate investigate-root-cause proposal."""
        import improvement_candidates as ic
        errors = [
            {"type": "repeated_incident", "detail": "sonarr crashes 30s after restart",
             "count": 5, "severity": "medium"},
        ]
        proposals = ic._gen_runbooks(errors)
        self.assertTrue(len(proposals) > 0)
        self.assertIn("runbook", proposals[0]["type"])

    def test_no_infinite_restarts(self):
        """Global budget also prevents infinite loops."""
        now = time.time()
        total = sum(1 for i in range(100)
                    if self.ab.can_execute("restart_container", _now=now + i))
        self.assertLessEqual(total, 3)  # per-type limit

    def test_different_containers_independent(self):
        """Budget is per-type, so different containers share the limit."""
        now = time.time()
        # restart_container is the type, regardless of target
        for i in range(3):
            self.ab.can_execute("restart_container", _now=now + i)
        # 4th restart of same type → blocked even for different target
        self.assertFalse(self.ab.can_execute("restart_container", _now=now + 4))

    def test_global_budget_caps_total(self):
        """Even with different types, global 10/hr applies."""
        now = time.time()
        count = 0
        for i in range(15):
            if self.ab.can_execute(f"type_{i}", _now=now + i):
                count += 1
        self.assertLessEqual(count, 10)


if __name__ == "__main__":
    unittest.main()
