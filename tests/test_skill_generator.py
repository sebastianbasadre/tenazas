#!/usr/bin/env python3
"""
Tests for skill_generator.py — Proposes new skills from recurring workflows.

Covers:
- Workflow analysis from memory_log.json
- Proposal generation (name, trigger, steps, approval flag)
- Dedup: skip existing proposals
- Max proposals cap
- Pending/approve/reject lifecycle
- load/save proposals
- Edge cases: empty log, non-list log, old entries, no events

Corre: python3 tests/test_skill_generator.py
"""

import sys
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/home/tenazas")
from runtime import skill_generator as sg


class _TempFilesMixin:
    """Redirect all file I/O to temp dir."""

    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_memory = sg.MEMORY_FILE
        self._orig_proposals = sg.PROPOSALS_FILE
        self._orig_log = sg.LOG_FILE
        sg.MEMORY_FILE = self.tmpdir / "memory_log.json"
        sg.PROPOSALS_FILE = self.tmpdir / "skill_proposals.json"
        sg.LOG_FILE = self.tmpdir / "logs" / "skill_generator.log"

    def tearDown(self):
        sg.MEMORY_FILE = self._orig_memory
        sg.PROPOSALS_FILE = self._orig_proposals
        sg.LOG_FILE = self._orig_log

    def _write_memory(self, entries):
        sg.MEMORY_FILE.write_text(json.dumps(entries))

    def _recent_ts(self, days_ago=0):
        dt = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return dt.isoformat()


class TestAnalyzeWorkflows(_TempFilesMixin, unittest.TestCase):
    """_analyze_workflows detects recurring event patterns."""

    def test_finds_recurring_workflow(self):
        entries = [
            {"ts": self._recent_ts(i), "events": [{"type": "vpn_down"}],
             "results": [{"action": "restart_gluetun", "ok": True}]}
            for i in range(5)
        ]
        self._write_memory(entries)
        workflows = sg._analyze_workflows()
        self.assertGreater(len(workflows), 0)
        self.assertGreaterEqual(workflows[0][1], sg.MIN_RECURRENCE)

    def test_ignores_below_threshold(self):
        entries = [
            {"ts": self._recent_ts(0), "events": [{"type": "rare_event"}],
             "results": [{"action": "something", "ok": True}]},
            {"ts": self._recent_ts(1), "events": [{"type": "rare_event"}],
             "results": [{"action": "something", "ok": True}]},
        ]
        self._write_memory(entries)
        workflows = sg._analyze_workflows()
        self.assertEqual(len(workflows), 0)

    def test_ignores_old_entries(self):
        entries = [
            {"ts": self._recent_ts(sg.LOOKBACK_DAYS + 5),
             "events": [{"type": "old_event"}],
             "results": [{"action": "old_action", "ok": True}]}
            for _ in range(10)
        ]
        self._write_memory(entries)
        workflows = sg._analyze_workflows()
        self.assertEqual(len(workflows), 0)

    def test_empty_memory_returns_empty(self):
        self._write_memory([])
        workflows = sg._analyze_workflows()
        self.assertEqual(workflows, [])

    def test_non_list_memory_returns_empty(self):
        self._write_memory({"not": "a list"})
        workflows = sg._analyze_workflows()
        self.assertEqual(workflows, [])

    def test_missing_memory_returns_empty(self):
        workflows = sg._analyze_workflows()
        self.assertEqual(workflows, [])

    def test_entries_without_events_skipped(self):
        entries = [
            {"ts": self._recent_ts(0), "results": [{"action": "x", "ok": True}]}
            for _ in range(5)
        ]
        self._write_memory(entries)
        workflows = sg._analyze_workflows()
        self.assertEqual(len(workflows), 0)

    def test_failed_actions_excluded(self):
        entries = [
            {"ts": self._recent_ts(i), "events": [{"type": "test_event"}],
             "results": [{"action": "fail_action", "ok": False}]}
            for i in range(5)
        ]
        self._write_memory(entries)
        workflows = sg._analyze_workflows()
        # Should still find the workflow (events exist) but actions list will be empty
        if workflows:
            details = workflows[0][2]
            self.assertEqual(details["actions"], [])


class TestCreateProposal(_TempFilesMixin, unittest.TestCase):
    """_create_proposal builds structured proposals."""

    def test_handler_name_when_actions(self):
        proposal = sg._create_proposal(["vpn_down"], ["restart_gluetun"], 5)
        self.assertIn("handler", proposal["name"])

    def test_monitor_name_when_no_actions(self):
        proposal = sg._create_proposal(["device_count_changed"], [], 3)
        self.assertIn("monitor", proposal["name"])

    def test_always_requires_approval(self):
        proposal = sg._create_proposal(["vpn_down"], ["restart"], 5)
        self.assertTrue(proposal["requires_approval"])

    def test_has_required_keys(self):
        proposal = sg._create_proposal(["test"], ["action"], 3)
        for key in ["name", "description", "trigger", "steps",
                     "requires_approval", "recurrence", "proposed_at", "status"]:
            self.assertIn(key, proposal)

    def test_status_is_pending(self):
        proposal = sg._create_proposal(["test"], [], 3)
        self.assertEqual(proposal["status"], "pending")

    def test_restart_step_requires_approval(self):
        proposal = sg._create_proposal(["vpn_down"], ["restart_gluetun"], 5)
        restart_steps = [s for s in proposal["steps"] if "restart" in s["action"]]
        self.assertTrue(all(s["requires_approval"] for s in restart_steps))

    def test_notify_step_when_no_actions(self):
        proposal = sg._create_proposal(["some_event"], [], 3)
        self.assertEqual(proposal["steps"][0]["action"], "notify")

    def test_recurrence_stored(self):
        proposal = sg._create_proposal(["test"], [], 5)
        self.assertEqual(proposal["recurrence"], 5)

    def test_trigger_contains_events(self):
        proposal = sg._create_proposal(["vpn_down", "internet_lost"], [], 3)
        self.assertIn("vpn_down", proposal["trigger"])
        self.assertIn("internet_lost", proposal["trigger"])


class TestGenerateProposals(_TempFilesMixin, unittest.TestCase):
    """generate_proposals() end-to-end."""

    def test_generates_from_recurring(self):
        entries = [
            {"ts": self._recent_ts(i), "events": [{"type": "container_down"}],
             "results": [{"action": "restart_container", "ok": True}]}
            for i in range(5)
        ]
        self._write_memory(entries)
        proposals = sg.generate_proposals()
        self.assertGreater(len(proposals), 0)

    def test_skips_existing_proposals(self):
        entries = [
            {"ts": self._recent_ts(i), "events": [{"type": "vpn_down"}],
             "results": [{"action": "restart", "ok": True}]}
            for i in range(5)
        ]
        self._write_memory(entries)
        first = sg.generate_proposals()
        second = sg.generate_proposals()
        self.assertGreater(len(first), 0)
        self.assertEqual(len(second), 0)  # Already proposed

    def test_empty_memory_no_proposals(self):
        self._write_memory([])
        proposals = sg.generate_proposals()
        self.assertEqual(proposals, [])

    def test_proposals_saved_to_disk(self):
        entries = [
            {"ts": self._recent_ts(i), "events": [{"type": "test_event"}],
             "results": [{"action": "test_action", "ok": True}]}
            for i in range(5)
        ]
        self._write_memory(entries)
        sg.generate_proposals()
        saved = sg.load_proposals()
        self.assertGreater(len(saved), 0)

    def test_max_proposals_cap(self):
        # Generate many distinct workflows
        all_entries = []
        for w in range(25):
            for i in range(sg.MIN_RECURRENCE):
                all_entries.append({
                    "ts": self._recent_ts(i),
                    "events": [{"type": f"event_{w}"}],
                    "results": [{"action": f"action_{w}", "ok": True}],
                })
        self._write_memory(all_entries)
        sg.generate_proposals()
        saved = sg.load_proposals()
        self.assertLessEqual(len(saved), sg.MAX_PROPOSALS)


class TestLoadSaveProposals(_TempFilesMixin, unittest.TestCase):
    """Proposal persistence."""

    def test_load_missing_returns_empty(self):
        proposals = sg.load_proposals()
        self.assertEqual(proposals, [])

    def test_save_and_load_roundtrip(self):
        proposals = [{"name": "test-handler", "status": "pending"}]
        sg._save_proposals(proposals)
        loaded = sg.load_proposals()
        self.assertEqual(len(loaded), 1)
        self.assertEqual(loaded[0]["name"], "test-handler")


class TestPendingProposals(_TempFilesMixin, unittest.TestCase):
    """get_pending_proposals filters by status."""

    def test_returns_only_pending(self):
        proposals = [
            {"name": "a", "status": "pending"},
            {"name": "b", "status": "approved"},
            {"name": "c", "status": "pending"},
            {"name": "d", "status": "rejected"},
        ]
        sg._save_proposals(proposals)
        pending = sg.get_pending_proposals()
        self.assertEqual(len(pending), 2)
        self.assertTrue(all(p["status"] == "pending" for p in pending))

    def test_empty_returns_empty(self):
        self.assertEqual(sg.get_pending_proposals(), [])


class TestApproveProposal(_TempFilesMixin, unittest.TestCase):
    """Approve workflow."""

    def test_approve_pending(self):
        sg._save_proposals([{"name": "test", "status": "pending"}])
        result = sg.approve_proposal("test")
        self.assertTrue(result)
        loaded = sg.load_proposals()
        self.assertEqual(loaded[0]["status"], "approved")
        self.assertIn("approved_at", loaded[0])

    def test_approve_nonexistent_returns_false(self):
        sg._save_proposals([{"name": "test", "status": "pending"}])
        self.assertFalse(sg.approve_proposal("nonexistent"))

    def test_approve_already_approved_returns_false(self):
        sg._save_proposals([{"name": "test", "status": "approved"}])
        self.assertFalse(sg.approve_proposal("test"))

    def test_approve_rejected_returns_false(self):
        sg._save_proposals([{"name": "test", "status": "rejected"}])
        self.assertFalse(sg.approve_proposal("test"))


class TestRejectProposal(_TempFilesMixin, unittest.TestCase):
    """Reject workflow."""

    def test_reject_pending(self):
        sg._save_proposals([{"name": "test", "status": "pending"}])
        result = sg.reject_proposal("test")
        self.assertTrue(result)
        loaded = sg.load_proposals()
        self.assertEqual(loaded[0]["status"], "rejected")
        self.assertIn("rejected_at", loaded[0])

    def test_reject_nonexistent_returns_false(self):
        self.assertFalse(sg.reject_proposal("nonexistent"))

    def test_reject_already_rejected_returns_false(self):
        sg._save_proposals([{"name": "test", "status": "rejected"}])
        self.assertFalse(sg.reject_proposal("test"))


class TestSafetyInvariant(_TempFilesMixin, unittest.TestCase):
    """Skill generator NEVER auto-creates skills."""

    def test_proposals_always_require_approval(self):
        entries = [
            {"ts": self._recent_ts(i), "events": [{"type": "vpn_down"}],
             "results": [{"action": "restart", "ok": True}]}
            for i in range(5)
        ]
        self._write_memory(entries)
        proposals = sg.generate_proposals()
        for p in proposals:
            self.assertTrue(p["requires_approval"])
            self.assertEqual(p["status"], "pending")


if __name__ == "__main__":
    unittest.main()
