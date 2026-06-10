#!/usr/bin/env python3
"""
Tests for autonomy_contracts.py and policy_engine integration.
"""

import sys
import tempfile
import unittest
from pathlib import Path

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))

import autonomy_contracts as ac
import policy_engine as pe


class _AutonomyTempStateMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_ac_state = ac.STATE_PATH
        self._orig_ac_log = ac.LOG_PATH
        self._orig_pe_cooldown = pe.COOLDOWN_STATE_PATH
        self._orig_pe_log = pe.LOG_PATH
        ac.STATE_PATH = self.tmpdir / "autonomy_state.json"
        ac.LOG_PATH = self.tmpdir / "logs" / "autonomy_contracts.log"
        pe.COOLDOWN_STATE_PATH = self.tmpdir / "policy_cooldown_state.json"
        pe.LOG_PATH = self.tmpdir / "logs" / "policy_engine.log"

    def tearDown(self):
        ac.STATE_PATH = self._orig_ac_state
        ac.LOG_PATH = self._orig_ac_log
        pe.COOLDOWN_STATE_PATH = self._orig_pe_cooldown
        pe.LOG_PATH = self._orig_pe_log


class TestAutonomyContracts(_AutonomyTempStateMixin, unittest.TestCase):

    def test_infra_watcher_can_restart_allowed_target(self):
        decision = ac.evaluate(
            "infra_watcher", "restart_container", "sonarr", confidence=0.90
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.execute_now)
        self.assertFalse(decision.requires_approval)

    def test_low_confidence_requires_approval(self):
        decision = ac.evaluate(
            "infra_watcher", "restart_container", "sonarr", confidence=0.50
        )
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.execute_now)
        self.assertTrue(decision.requires_approval)
        self.assertIn("Confidence below", decision.reason)

    def test_target_outside_contract_blocked(self):
        decision = ac.evaluate(
            "infra_watcher", "restart_container", "openclaw", confidence=0.95
        )
        self.assertFalse(decision.allowed)
        self.assertFalse(decision.execute_now)
        self.assertTrue(decision.requires_approval)

    def test_action_outside_contract_blocked(self):
        decision = ac.evaluate(
            "infra_watcher", "cleanup_disk", "", confidence=0.95
        )
        self.assertFalse(decision.allowed)
        self.assertFalse(decision.execute_now)
        self.assertTrue(decision.requires_approval)

    def test_daily_target_budget_requires_approval_after_limit(self):
        ac.record_action("infra_watcher", "restart_container", "sonarr")
        ac.record_action("infra_watcher", "restart_container", "sonarr")

        decision = ac.evaluate(
            "infra_watcher", "restart_container", "sonarr", confidence=0.95
        )

        self.assertTrue(decision.allowed)
        self.assertFalse(decision.execute_now)
        self.assertTrue(decision.requires_approval)
        self.assertIn("target", decision.reason.lower())

    def test_circuit_breaker_opens_after_failure_streak(self):
        for _ in range(3):
            ac.record_outcome(
                "infra_watcher", "restart_container", "sonarr", ok=False
            )

        decision = ac.evaluate(
            "infra_watcher", "restart_container", "sonarr", confidence=0.95
        )

        self.assertFalse(decision.allowed)
        self.assertFalse(decision.execute_now)
        self.assertTrue(decision.circuit_open)
        self.assertIn("Circuit breaker", decision.reason)

    def test_successful_outcome_resets_failure_streak(self):
        ac.record_outcome(
            "infra_watcher", "restart_container", "sonarr", ok=False
        )
        ac.record_outcome(
            "infra_watcher", "restart_container", "sonarr", ok=True
        )

        status = ac.status()
        self.assertEqual(status["agents"]["infra_watcher"]["failure_streak"], 0)

    def test_shadow_agent_cannot_execute_now(self):
        decision = ac.evaluate(
            "learning_worker", "health_check", "", confidence=1.0
        )
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.execute_now)
        self.assertTrue(decision.requires_approval)
        self.assertTrue(decision.shadow_mode)


class TestPolicyEngineAutonomyIntegration(_AutonomyTempStateMixin, unittest.TestCase):

    def test_policy_allows_agent_action_inside_contract(self):
        decision = pe.evaluate(
            "restart_container",
            "sonarr",
            confidence=0.95,
            context="sonarr unhealthy",
            agent="infra_watcher",
        )
        self.assertTrue(decision.allowed)
        self.assertTrue(decision.execute_now)
        self.assertFalse(decision.requires_approval)
        self.assertEqual(decision.agent, "infra_watcher")

    def test_policy_blocks_protected_target_before_contract(self):
        decision = pe.evaluate(
            "restart_container",
            "openclaw",
            confidence=0.95,
            context="protected target",
            agent="infra_watcher",
        )
        self.assertFalse(decision.allowed)
        self.assertFalse(decision.execute_now)
        self.assertIn("blocked", decision.reason.lower())

    def test_policy_requires_approval_when_contract_is_shadow(self):
        decision = pe.evaluate(
            "health_check",
            "",
            confidence=1.0,
            context="learning check",
            agent="learning_worker",
        )
        self.assertTrue(decision.allowed)
        self.assertFalse(decision.execute_now)
        self.assertTrue(decision.requires_approval)
        self.assertIn("shadow", decision.reason.lower())

    def test_policy_record_execution_updates_autonomy_budget(self):
        pe.record_execution("restart_container", "sonarr", agent="infra_watcher")
        status = ac.status()
        self.assertEqual(status["agents"]["infra_watcher"]["actions_today"], 1)
        self.assertEqual(
            status["agents"]["infra_watcher"]["targets_today"]["restart_container:sonarr"],
            1,
        )


if __name__ == "__main__":
    unittest.main()
