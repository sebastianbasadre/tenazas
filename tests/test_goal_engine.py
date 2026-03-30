#!/usr/bin/env python3
"""
Tests for goal_engine.py — Goal tracking and evaluation.

Covers:
- 3 permanent goals initialization
- Goal evaluation against system state
- _count_today_events / _count_today_restarts
- load/save goals
- get_goal / get_blocked_goals helpers
- All goals met / partially met / none met scenarios

Corre: python3 tests/test_goal_engine.py
"""

import sys
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/home/tenazas")
from runtime import goal_engine as ge


class _TempFilesMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig = {
            "goals": ge.GOALS_FILE,
            "state": ge.STATE_FILE,
            "memory": ge.MEMORY_FILE,
            "log": ge.LOG_FILE,
        }
        ge.GOALS_FILE = self.tmpdir / "goals_state.json"
        ge.STATE_FILE = self.tmpdir / "state.json"
        ge.MEMORY_FILE = self.tmpdir / "memory_log.json"
        ge.LOG_FILE = self.tmpdir / "logs" / "goals.log"

    def tearDown(self):
        ge.GOALS_FILE = self._orig["goals"]
        ge.STATE_FILE = self._orig["state"]
        ge.MEMORY_FILE = self._orig["memory"]
        ge.LOG_FILE = self._orig["log"]

    def _write_state(self, state):
        ge.STATE_FILE.write_text(json.dumps(state))

    def _write_memory(self, entries):
        ge.MEMORY_FILE.write_text(json.dumps(entries))

    def _today_ts(self):
        return datetime.now(timezone.utc).isoformat()

    def _healthy_state(self):
        return {
            "containers": [{"name": f"c{i}", "up": True} for i in range(18)],
            "internet": {"ok": True},
            "vpn": {"ok": True},
            "ollama_notebook": {"ok": True},
            "ollama_nas": {"ok": False},
        }


class TestLoadGoals(_TempFilesMixin, unittest.TestCase):
    """Goal loading and initialization."""

    def test_missing_file_creates_all_goals(self):
        goals = ge.load_goals()
        self.assertEqual(len(goals), 3)

    def test_all_permanent_goals_present(self):
        goals = ge.load_goals()
        ids = {g["id"] for g in goals}
        for pg in ge.PERMANENT_GOALS:
            self.assertIn(pg["id"], ids)

    def test_goals_initialized_as_active(self):
        goals = ge.load_goals()
        for g in goals:
            self.assertEqual(g["status"], "active")

    def test_existing_goals_preserved(self):
        ge._save_json(ge.GOALS_FILE, {"goals": [
            {"id": "maintain_infrastructure_stability", "status": "achieved",
             "description": "test", "priority": 1, "targets": {}, "last_evaluation": "", "metrics": {}},
        ]})
        goals = ge.load_goals()
        stability = next(g for g in goals if g["id"] == "maintain_infrastructure_stability")
        self.assertEqual(stability["status"], "achieved")
        # Missing goals should be added
        self.assertEqual(len(goals), 3)


class TestSaveGoals(_TempFilesMixin, unittest.TestCase):
    def test_save_creates_file(self):
        ge.save_goals([{"id": "test"}])
        self.assertTrue(ge.GOALS_FILE.exists())

    def test_save_roundtrip(self):
        goals = ge.load_goals()
        ge.save_goals(goals)
        loaded = ge.load_goals()
        self.assertEqual(len(loaded), 3)


class TestEvaluateAllMet(_TempFilesMixin, unittest.TestCase):
    """All goals achieved when system is healthy."""

    def test_all_goals_achieved(self):
        self._write_state(self._healthy_state())
        self._write_memory([])
        goals = ge.evaluate_goals()
        for g in goals:
            self.assertEqual(g["status"], "achieved", f"Goal {g['id']} not achieved")

    def test_metrics_populated(self):
        self._write_state(self._healthy_state())
        self._write_memory([])
        goals = ge.evaluate_goals()
        for g in goals:
            self.assertNotEqual(g["metrics"], {})

    def test_last_evaluation_set(self):
        self._write_state(self._healthy_state())
        self._write_memory([])
        goals = ge.evaluate_goals()
        for g in goals:
            self.assertNotEqual(g["last_evaluation"], "")


class TestEvaluateStability(_TempFilesMixin, unittest.TestCase):
    """maintain_infrastructure_stability goal."""

    def test_low_container_pct_not_met(self):
        state = self._healthy_state()
        # Only 10 of 18 up = 55%
        for i in range(8):
            state["containers"][i]["up"] = False
        self._write_state(state)
        self._write_memory([])
        goals = ge.evaluate_goals()
        stability = next(g for g in goals if g["id"] == "maintain_infrastructure_stability")
        self.assertEqual(stability["status"], "active")

    def test_internet_down_not_met(self):
        state = self._healthy_state()
        state["internet"]["ok"] = False
        self._write_state(state)
        self._write_memory([])
        goals = ge.evaluate_goals()
        stability = next(g for g in goals if g["id"] == "maintain_infrastructure_stability")
        self.assertEqual(stability["status"], "active")

    def test_vpn_down_not_met(self):
        state = self._healthy_state()
        state["vpn"]["ok"] = False
        self._write_state(state)
        self._write_memory([])
        goals = ge.evaluate_goals()
        stability = next(g for g in goals if g["id"] == "maintain_infrastructure_stability")
        self.assertEqual(stability["status"], "active")

    def test_container_pct_metric(self):
        self._write_state(self._healthy_state())
        self._write_memory([])
        goals = ge.evaluate_goals()
        stability = next(g for g in goals if g["id"] == "maintain_infrastructure_stability")
        self.assertEqual(stability["metrics"]["containers_up_pct"], 100.0)


class TestEvaluateDowntime(_TempFilesMixin, unittest.TestCase):
    """minimize_downtime goal."""

    def test_too_many_events_not_met(self):
        self._write_state(self._healthy_state())
        entries = [
            {"ts": self._today_ts(), "events": [{"type": f"event_{i}"}]}
            for i in range(10)
        ]
        self._write_memory(entries)
        goals = ge.evaluate_goals()
        downtime = next(g for g in goals if g["id"] == "minimize_downtime")
        self.assertEqual(downtime["status"], "active")

    def test_too_many_restarts_not_met(self):
        self._write_state(self._healthy_state())
        entries = [
            {"ts": self._today_ts(),
             "results": [{"action": "docker.restart", "ok": True}]}
            for _ in range(5)
        ]
        self._write_memory(entries)
        goals = ge.evaluate_goals()
        downtime = next(g for g in goals if g["id"] == "minimize_downtime")
        self.assertEqual(downtime["status"], "active")

    def test_within_limits_achieved(self):
        self._write_state(self._healthy_state())
        self._write_memory([])
        goals = ge.evaluate_goals()
        downtime = next(g for g in goals if g["id"] == "minimize_downtime")
        self.assertEqual(downtime["status"], "achieved")


class TestEvaluateAccuracy(_TempFilesMixin, unittest.TestCase):
    """maximize_response_accuracy goal."""

    def test_notebook_ok_achieved(self):
        self._write_state(self._healthy_state())
        self._write_memory([])
        goals = ge.evaluate_goals()
        accuracy = next(g for g in goals if g["id"] == "maximize_response_accuracy")
        self.assertEqual(accuracy["status"], "achieved")

    def test_only_nas_ok_achieved(self):
        state = self._healthy_state()
        state["ollama_notebook"]["ok"] = False
        state["ollama_nas"]["ok"] = True
        self._write_state(state)
        self._write_memory([])
        goals = ge.evaluate_goals()
        accuracy = next(g for g in goals if g["id"] == "maximize_response_accuracy")
        self.assertEqual(accuracy["status"], "achieved")

    def test_both_offline_not_met(self):
        state = self._healthy_state()
        state["ollama_notebook"]["ok"] = False
        state["ollama_nas"]["ok"] = False
        self._write_state(state)
        self._write_memory([])
        goals = ge.evaluate_goals()
        accuracy = next(g for g in goals if g["id"] == "maximize_response_accuracy")
        self.assertEqual(accuracy["status"], "active")


class TestCountToday(_TempFilesMixin, unittest.TestCase):
    """_count_today_events and _count_today_restarts."""

    def test_count_events_today(self):
        entries = [
            {"ts": self._today_ts(), "events": [{"type": "a"}, {"type": "b"}]},
            {"ts": self._today_ts(), "events": [{"type": "c"}]},
        ]
        self._write_memory(entries)
        self.assertEqual(ge._count_today_events(), 3)

    def test_count_restarts_today(self):
        entries = [
            {"ts": self._today_ts(), "results": [
                {"action": "docker.restart", "ok": True},
                {"action": "docker.restart", "ok": True},
            ]},
        ]
        self._write_memory(entries)
        self.assertEqual(ge._count_today_restarts(), 2)

    def test_failed_restarts_not_counted(self):
        entries = [
            {"ts": self._today_ts(), "results": [
                {"action": "docker.restart", "ok": False},
            ]},
        ]
        self._write_memory(entries)
        self.assertEqual(ge._count_today_restarts(), 0)

    def test_empty_memory(self):
        self._write_memory([])
        self.assertEqual(ge._count_today_events(), 0)
        self.assertEqual(ge._count_today_restarts(), 0)

    def test_missing_memory(self):
        self.assertEqual(ge._count_today_events(), 0)

    def test_non_list_memory(self):
        self._write_memory({"not": "a list"})
        self.assertEqual(ge._count_today_events(), 0)


class TestGetGoal(_TempFilesMixin, unittest.TestCase):
    def test_get_existing(self):
        goal = ge.get_goal("maintain_infrastructure_stability")
        self.assertEqual(goal["id"], "maintain_infrastructure_stability")

    def test_get_nonexistent(self):
        self.assertEqual(ge.get_goal("nonexistent"), {})


class TestGetBlockedGoals(_TempFilesMixin, unittest.TestCase):
    def test_all_active_returned(self):
        blocked = ge.get_blocked_goals()
        self.assertEqual(len(blocked), 3)  # All start as active

    def test_achieved_excluded(self):
        self._write_state(self._healthy_state())
        self._write_memory([])
        ge.evaluate_goals()
        blocked = ge.get_blocked_goals()
        self.assertEqual(len(blocked), 0)


class TestEmptyState(_TempFilesMixin, unittest.TestCase):
    """Evaluation with empty/missing state."""

    def test_missing_state_file(self):
        self._write_memory([])
        goals = ge.evaluate_goals()
        # With no containers, 0% up, stability not met
        stability = next(g for g in goals if g["id"] == "maintain_infrastructure_stability")
        self.assertEqual(stability["status"], "active")


if __name__ == "__main__":
    unittest.main()
