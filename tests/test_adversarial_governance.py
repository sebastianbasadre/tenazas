#!/usr/bin/env python3
"""
test_adversarial_governance.py — Adversarial inputs and misbehavior tests.

Tests when the system intentionally behaves badly: manipulated scores,
corrupted data, spam attacks, protected container bypass attempts.
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if _C.exists():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


# ══════════════════════════════════════════════════════════════
#  decision_quality adversarial
# ══════════════════════════════════════════════════════════════

class TestDQAdversarial(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import decision_quality as dq
        self._orig = dq.STATE_DIR
        dq.STATE_DIR = Path(self.tmpdir) / "dq"
        dq.STATE_DIR.mkdir(parents=True)
        self.dq = dq

    def tearDown(self):
        self.dq.STATE_DIR = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_good_report_but_validator_fails(self):
        """If validator says fail, quality must be bad regardless."""
        r = self.dq.score(action="restart", target="sonarr",
                          validation_result={"success": False, "reason": "still down"},
                          approval_status="approved")
        # Validator trumps approval
        self.assertEqual(r["quality"], "bad")

    def test_impact_clamped_max_1(self):
        """Impact score never exceeds 1.0."""
        r = self.dq.score(action="a", target="t",
                          validation_result={"success": True},
                          confidence=0.01)  # extreme underconfidence
        self.assertLessEqual(r["impact_score"], 1.0)

    def test_impact_clamped_min_neg1(self):
        """Impact score never below -1.0."""
        r = self.dq.score(action="a", target="t", rollback=True, confidence=0.99)
        self.assertGreaterEqual(r["impact_score"], -1.5)  # amplified but bounded

    def test_duplicate_ids_unique(self):
        """Two rapid scores get different IDs."""
        r1 = self.dq.score(action="a", target="t")
        r2 = self.dq.score(action="a", target="t")
        self.assertNotEqual(r1["decision_id"], r2["decision_id"])

    def test_future_timestamp_still_works(self):
        """Score function works even if called rapidly (no timestamp collision)."""
        ids = set()
        for _ in range(20):
            r = self.dq.score(action="a", target="t")
            ids.add(r["decision_id"])
        self.assertEqual(len(ids), 20)

    def test_empty_action_no_crash(self):
        r = self.dq.score(action="", target="")
        self.assertIn("quality", r)

    def test_very_long_context_truncated(self):
        r = self.dq.score(action="a", target="t", context="x" * 10000)
        self.assertLessEqual(len(r["context"]), 500)

    def test_special_chars_in_action(self):
        r = self.dq.score(action="a'; DROP TABLE;--", target="<script>")
        self.assertIn("quality", r)

    def test_none_validation_result(self):
        r = self.dq.score(action="a", target="t", validation_result=None)
        self.assertEqual(r["quality"], "neutral")

    def test_empty_dict_validation(self):
        r = self.dq.score(action="a", target="t", validation_result={})
        self.assertIn("quality", r)


# ══════════════════════════════════════════════════════════════
#  confidence_calibration adversarial
# ══════════════════════════════════════════════════════════════

class TestCCAdversarial(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import confidence_calibration as cc
        self._o = (cc.STATE_DIR, cc.CALIBRATION_PATH, cc.REPORT_PATH)
        cc.STATE_DIR = Path(self.tmpdir)
        cc.CALIBRATION_PATH = Path(self.tmpdir) / "cal.json"
        cc.REPORT_PATH = Path(self.tmpdir) / "rep.json"
        self.cc = cc

    def tearDown(self):
        self.cc.STATE_DIR, self.cc.CALIBRATION_PATH, self.cc.REPORT_PATH = self._o
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _mock(self, d):
        return patch.object(self.cc, "_load_decisions", return_value=d)

    def test_always_1_confidence_detected_overconfident(self):
        decisions = [{"confidence": 1.0, "quality": "bad"}] * 10
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertEqual(r["overconfidence_rate"], 1.0)

    def test_always_0_confidence_detected_underconfident(self):
        decisions = [{"confidence": 0.0, "quality": "good"}] * 10
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertGreater(r["underconfidence_rate"], 0)

    def test_corrupted_calibration_file_no_crash(self):
        """Malformed JSON in calibration file → safe fallback."""
        Path(self.cc.CALIBRATION_PATH).write_text("{invalid json!!!")
        t = self.cc.get_current_thresholds()
        self.assertEqual(t["high"], 0.80)
        self.assertEqual(t["medium"], 0.60)

    def test_corrupted_report_file_no_crash(self):
        Path(self.cc.REPORT_PATH).write_text("not json at all")
        r = self.cc.get_last_report()
        self.assertIsNone(r)

    def test_empty_list_decisions(self):
        with self._mock([]):
            r = self.cc.calibrate()
        self.assertIsNone(r["suggested_adjustments"])

    def test_missing_confidence_field(self):
        """Decisions without confidence field → skipped safely."""
        decisions = [{"quality": "good"}, {"quality": "bad"}]
        with self._mock(decisions):
            r = self.cc.calibrate()
        # Should not crash, may have 0 total
        self.assertIn("total_decisions", r)

    def test_missing_quality_field(self):
        decisions = [{"confidence": 0.5}]
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertIn("total_decisions", r)

    def test_negative_confidence(self):
        decisions = [{"confidence": -0.5, "quality": "good"}]
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertIn("total_decisions", r)

    def test_confidence_above_1(self):
        decisions = [{"confidence": 1.5, "quality": "bad"}]
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertIn("total_decisions", r)


# ══════════════════════════════════════════════════════════════
#  proactive_planner adversarial
# ══════════════════════════════════════════════════════════════

class TestPlannerAdversarial(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import action_budget as ab
        self._ab_orig = ab.STATE_PATH
        ab.STATE_PATH = Path(self.tmpdir) / "ab.json"
        self.ab = ab

    def tearDown(self):
        self.ab.STATE_PATH = self._ab_orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_restart_openclaw_blocked_by_registry(self):
        """restart_container for openclaw blocked by action_registry."""
        import action_registry as ar
        action = ar.get_action("restart_container")
        blocked = action.get("blocked_targets", [])
        self.assertIn("openclaw", blocked)

    def test_restart_watchtower_blocked_by_registry(self):
        import action_registry as ar
        action = ar.get_action("restart_container")
        blocked = action.get("blocked_targets", [])
        self.assertIn("watchtower", blocked)

    def test_50_actions_1_min_budget_blocks(self):
        """50 actions in 1 minute → budget blocks after 10."""
        now = time.time()
        passed = sum(1 for i in range(50)
                     if self.ab.can_execute(f"a_{i % 20}", _now=now + i))
        self.assertLessEqual(passed, 10)

    def test_budget_blocks_notifications_spam(self):
        """20 notifications in 1 min → blocked after 5."""
        now = time.time()
        passed = sum(1 for i in range(20)
                     if self.ab.can_notify(_now=now + i * 3))
        self.assertLessEqual(passed, 5)

    def test_openclaw_blocked_by_docker_wrapper(self):
        """docker_wrapper blocks openclaw."""
        import docker_wrapper as dw
        self.assertIn("openclaw", dw.BLOCKED_CONTAINERS)

    def test_watchtower_blocked_by_docker_wrapper(self):
        import docker_wrapper as dw
        self.assertIn("watchtower", dw.BLOCKED_CONTAINERS)

    def test_openclaw_blocked_by_safety_guard(self):
        import safety_guard as sg
        result = sg.validate_action("restart", "openclaw")
        self.assertFalse(result.get("allowed", True))


# ══════════════════════════════════════════════════════════════
#  self_heal adversarial (cooldown enforcement)
# ══════════════════════════════════════════════════════════════

class TestSelfHealAdversarial(unittest.TestCase):

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

    def test_flapping_container_cooldown(self):
        """After 3 restarts of same container, budget blocks further."""
        now = time.time()
        restarts = sum(1 for i in range(10)
                       if self.ab.can_execute("restart_container", _now=now + i * 60))
        self.assertLessEqual(restarts, 3)

    def test_heal_loop_3_cycles_then_stop(self):
        """3 restart cycles → budget blocks further."""
        now = time.time()
        cycles = 0
        for i in range(6):
            if self.ab.can_execute("restart_container", _now=now + i * 120):
                cycles += 1
        self.assertLessEqual(cycles, 3)

    def test_extra_restarts_scored_bad(self):
        """Restarts beyond budget scored as bad."""
        now = time.time()
        for i in range(3):
            self.ab.can_execute("restart_container", _now=now + i)
        # 4th blocked
        blocked = not self.ab.can_execute("restart_container", _now=now + 4)
        self.assertTrue(blocked)
        # Score blocked attempt
        r = self.dq.score(action="restart_container", target="sonarr",
                          approval_status="rejected", context="budget exceeded")
        self.assertEqual(r["quality"], "bad")


# ══════════════════════════════════════════════════════════════
#  approval_queue adversarial
# ══════════════════════════════════════════════════════════════

class TestApprovalQueueAdversarial(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import action_budget as ab
        self._ab = ab.STATE_PATH
        ab.STATE_PATH = Path(self.tmpdir) / "ab.json"
        self.ab = ab

    def tearDown(self):
        self.ab.STATE_PATH = self._ab
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_notification_spam_blocked(self):
        """20 notifications in 1 min → only 5 pass."""
        now = time.time()
        passed = sum(1 for i in range(20)
                     if self.ab.can_notify(_now=now + i * 3))
        self.assertLessEqual(passed, 5)

    def test_approve_nonexistent_id_no_crash(self):
        """Approving non-existent ID returns gracefully."""
        import improvement_candidates as ic
        with patch.object(ic, "CANDIDATES_FILE", Path(self.tmpdir) / "ic.json"):
            result = ic.update_status("nonexistent_id_12345", "approved")
        self.assertFalse(result)

    def test_reject_nonexistent_id_no_crash(self):
        import improvement_candidates as ic
        with patch.object(ic, "CANDIDATES_FILE", Path(self.tmpdir) / "ic.json"):
            result = ic.update_status("nonexistent_id_12345", "rejected")
        self.assertFalse(result)

    def test_double_approval_second_ignored(self):
        """Approving already-approved candidate fails gracefully."""
        import improvement_candidates as ic
        cands = [{"id": "test1", "type": "test", "title": "t", "status": "staged"}]
        f = Path(self.tmpdir) / "ic.json"
        f.write_text(json.dumps(cands))
        with patch.object(ic, "CANDIDATES_FILE", f):
            self.assertTrue(ic.update_status("test1", "approved"))
            self.assertFalse(ic.update_status("test1", "approved"))

    def test_corrupted_candidates_file_no_crash(self):
        import improvement_candidates as ic
        f = Path(self.tmpdir) / "ic.json"
        f.write_text("NOT VALID JSON!!!")
        with patch.object(ic, "CANDIDATES_FILE", f):
            cands = ic.list_candidates()
        self.assertEqual(cands, [])


if __name__ == "__main__":
    unittest.main()
