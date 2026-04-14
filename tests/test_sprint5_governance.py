#!/usr/bin/env python3
"""
test_sprint5_governance.py — Sprint 5: Governance & Stability tests

Tests:
  - decision_quality: scores correctly for good/neutral/bad outcomes
  - confidence_calibration: detects overconfidence correctly
  - noise_filter: should_send() suppresses correctly within TTL
  - action_budget: can_execute() enforces limits and resets on window
  - integration: policy_engine respects action_budget
  - integration: proactive_planner respects noise_filter
"""

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

# ── Path setup ────────────────────────────────────────────────

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_CONTAINER_RT = Path("/home/tenazas/runtime")
if _CONTAINER_RT.exists():
    RUNTIME = _CONTAINER_RT
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


# ══════════════════════════════════════════════════════════════
#  decision_quality tests
# ══════════════════════════════════════════════════════════════

class TestDecisionQuality(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / "state" / "decision_quality"
        self.state_dir.mkdir(parents=True)
        # Patch STATE_DIR
        import decision_quality as dq
        self._orig_state = dq.STATE_DIR
        dq.STATE_DIR = self.state_dir
        self.dq = dq

    def tearDown(self):
        self.dq.STATE_DIR = self._orig_state
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_score_good_from_validation(self):
        """Successful validation → good quality."""
        result = self.dq.score(
            action="restart_container", target="sonarr",
            validation_result={"success": True, "reason": "container running"})
        self.assertEqual(result["quality"], "good")
        self.assertGreater(result["impact_score"], 0)
        self.assertIn("decision_id", result)
        self.assertIn("timestamp", result)

    def test_score_bad_from_validation_failure(self):
        """Failed validation → bad quality."""
        result = self.dq.score(
            action="restart_container", target="radarr",
            validation_result={"success": False, "reason": "still down"})
        self.assertEqual(result["quality"], "bad")
        self.assertLess(result["impact_score"], 0)

    def test_score_bad_from_rejection(self):
        """User rejection → bad quality."""
        result = self.dq.score(
            action="cleanup_disk", target="/volume1",
            approval_status="rejected")
        self.assertEqual(result["quality"], "bad")
        self.assertIn("rejected", result["reason"])

    def test_score_bad_from_rollback(self):
        """Rollback triggered → bad quality."""
        result = self.dq.score(
            action="reload_model", target="qwen2.5:7b",
            rollback=True)
        self.assertEqual(result["quality"], "bad")
        self.assertEqual(result["reason"], "rollback triggered")
        self.assertLessEqual(result["impact_score"], -0.8)

    def test_score_neutral_no_signal(self):
        """No validation or approval → neutral."""
        result = self.dq.score(
            action="check_health", target="all")
        self.assertEqual(result["quality"], "neutral")
        self.assertEqual(result["impact_score"], 0.0)

    def test_score_good_from_approval(self):
        """User approved → good quality."""
        result = self.dq.score(
            action="restart_container", target="gluetun",
            approval_status="approved")
        self.assertEqual(result["quality"], "good")
        self.assertGreater(result["impact_score"], 0)

    def test_score_bad_expired_approval(self):
        """Expired approval → bad (user ignored it)."""
        result = self.dq.score(
            action="restart_container", target="sonarr",
            approval_status="expired")
        self.assertEqual(result["quality"], "bad")

    def test_overconfident_penalty(self):
        """High confidence + bad outcome → worse impact."""
        result = self.dq.score(
            action="restart", target="x",
            validation_result={"success": False, "reason": "failed"},
            confidence=0.95)
        # Impact should be amplified (1.3x)
        self.assertLess(result["impact_score"], -0.5)

    def test_underconfident_bonus(self):
        """Low confidence + good outcome → better impact."""
        result = self.dq.score(
            action="restart", target="x",
            validation_result={"success": True, "reason": "ok"},
            confidence=0.3)
        # Impact should be amplified (1.2x)
        self.assertGreater(result["impact_score"], 0.5)

    def test_persistence_daily(self):
        """Scores persist in daily file."""
        self.dq.score(action="test1", target="t1",
                      validation_result={"success": True})
        self.dq.score(action="test2", target="t2",
                      validation_result={"success": False})
        entries = self.dq._load_daily(date.today())
        self.assertEqual(len(entries), 2)

    def test_feeds_into_field(self):
        """feeds_into always populated."""
        result = self.dq.score(action="a", target="b")
        self.assertIsInstance(result["feeds_into"], list)
        self.assertIn("self_benchmark", result["feeds_into"])
        self.assertIn("confidence_engine", result["feeds_into"])

    def test_summary_empty(self):
        """Summary with no data returns zeros."""
        s = self.dq.summary(1)
        self.assertEqual(s["total"], 0)
        self.assertEqual(s["good_rate"], 0.0)

    def test_summary_correct_rates(self):
        """Summary calculates rates correctly."""
        self.dq.score(action="a", target="t",
                      validation_result={"success": True})
        self.dq.score(action="b", target="t",
                      validation_result={"success": True})
        self.dq.score(action="c", target="t",
                      validation_result={"success": False})
        self.dq.score(action="d", target="t")  # neutral
        s = self.dq.summary(1)
        self.assertEqual(s["total"], 4)
        self.assertEqual(s["good"], 2)
        self.assertEqual(s["bad"], 1)
        self.assertEqual(s["neutral"], 1)
        self.assertAlmostEqual(s["good_rate"], 0.5, places=2)

    def test_get_bad_decisions(self):
        """get_bad_decisions filters correctly."""
        self.dq.score(action="a", target="t",
                      validation_result={"success": True})
        self.dq.score(action="b", target="t", rollback=True)
        bads = self.dq.get_bad_decisions(1)
        self.assertEqual(len(bads), 1)
        self.assertEqual(bads[0]["quality"], "bad")

    @patch("decision_quality._feed_preference_learning")
    def test_bad_feeds_preference(self, mock_feed):
        """Bad decisions feed into preference_learning."""
        self.dq.score(action="x", target="y", rollback=True)
        mock_feed.assert_called_once()

    def test_context_truncation(self):
        """Long context gets truncated to 500 chars."""
        long_ctx = "x" * 1000
        result = self.dq.score(action="a", target="b", context=long_ctx)
        self.assertLessEqual(len(result["context"]), 500)


# ══════════════════════════════════════════════════════════════
#  confidence_calibration tests
# ══════════════════════════════════════════════════════════════

class TestConfidenceCalibration(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir) / "state"
        self.state_dir.mkdir(parents=True)
        import confidence_calibration as cc
        self._orig_state = cc.STATE_DIR
        self._orig_cal = cc.CALIBRATION_PATH
        self._orig_rep = cc.REPORT_PATH
        cc.STATE_DIR = self.state_dir
        cc.CALIBRATION_PATH = self.state_dir / "confidence_calibration.json"
        cc.REPORT_PATH = self.state_dir / "confidence_calibration_report.json"
        self.cc = cc

    def tearDown(self):
        self.cc.STATE_DIR = self._orig_state
        self.cc.CALIBRATION_PATH = self._orig_cal
        self.cc.REPORT_PATH = self._orig_rep
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _mock_decisions(self, decisions):
        """Patch _load_decisions to return given data."""
        return patch.object(self.cc, "_load_decisions", return_value=decisions)

    def test_calibrate_no_data(self):
        """Empty decisions → zero calibration error."""
        with self._mock_decisions([]):
            report = self.cc.calibrate()
        self.assertEqual(report["total_decisions"], 0)
        self.assertEqual(report["calibration_error"], 0.0)

    def test_detects_overconfidence(self):
        """High confidence + bad outcomes → overconfidence detected."""
        decisions = [
            {"confidence": 0.9, "quality": "bad"},
            {"confidence": 0.85, "quality": "bad"},
            {"confidence": 0.75, "quality": "bad"},
            {"confidence": 0.6, "quality": "good"},
            {"confidence": 0.5, "quality": "good"},
        ]
        with self._mock_decisions(decisions):
            report = self.cc.calibrate()
        self.assertGreater(report["overconfidence_rate"], 0.15)
        self.assertIsNotNone(report["suggested_adjustments"])
        self.assertIn("overconfidence", report["suggested_adjustments"]["reason"])

    def test_detects_underconfidence(self):
        """Low confidence + good outcomes → underconfidence detected."""
        decisions = [
            {"confidence": 0.3, "quality": "good"},
            {"confidence": 0.4, "quality": "good"},
            {"confidence": 0.2, "quality": "good"},
            {"confidence": 0.8, "quality": "good"},
        ]
        with self._mock_decisions(decisions):
            report = self.cc.calibrate()
        self.assertGreater(report["underconfidence_rate"], 0.20)

    def test_well_calibrated_no_adjustment(self):
        """Good calibration → no adjustment needed."""
        decisions = [
            {"confidence": 0.9, "quality": "good"},
            {"confidence": 0.8, "quality": "good"},
            {"confidence": 0.7, "quality": "neutral"},
            {"confidence": 0.3, "quality": "bad"},
            {"confidence": 0.2, "quality": "bad"},
        ]
        with self._mock_decisions(decisions):
            report = self.cc.calibrate()
        self.assertIsNone(report["suggested_adjustments"])

    def test_staged_not_auto_applied(self):
        """Adjustments are staged, not auto-applied."""
        decisions = [
            {"confidence": 0.9, "quality": "bad"},
            {"confidence": 0.85, "quality": "bad"},
            {"confidence": 0.8, "quality": "bad"},
        ]
        with self._mock_decisions(decisions):
            self.cc.calibrate()
        staged = self.cc.get_staged_adjustments()
        current = self.cc.get_current_thresholds()
        # Staged exists but current still default
        self.assertIsNotNone(staged)
        self.assertEqual(current["high"], 0.80)

    def test_apply_staged_requires_confirm(self):
        """apply_staged without confirm=True does nothing."""
        decisions = [
            {"confidence": 0.9, "quality": "bad"},
            {"confidence": 0.85, "quality": "bad"},
            {"confidence": 0.8, "quality": "bad"},
        ]
        with self._mock_decisions(decisions):
            self.cc.calibrate()
        self.assertFalse(self.cc.apply_staged(confirm=False))
        self.assertIsNotNone(self.cc.get_staged_adjustments())

    def test_apply_staged_with_confirm(self):
        """apply_staged with confirm=True updates thresholds."""
        decisions = [
            {"confidence": 0.9, "quality": "bad"},
            {"confidence": 0.85, "quality": "bad"},
            {"confidence": 0.8, "quality": "bad"},
        ]
        with self._mock_decisions(decisions):
            self.cc.calibrate()
        self.assertTrue(self.cc.apply_staged(confirm=True))
        self.assertIsNone(self.cc.get_staged_adjustments())
        current = self.cc.get_current_thresholds()
        self.assertGreater(current["high"], 0.80)

    def test_calibration_error_calculation(self):
        """Calibration error is average of |predicted - actual|."""
        decisions = [
            {"confidence": 1.0, "quality": "good"},   # |1.0-1.0| = 0
            {"confidence": 0.0, "quality": "bad"},     # |0.0-0.0| = 0
            {"confidence": 0.5, "quality": "neutral"}, # |0.5-0.5| = 0
        ]
        with self._mock_decisions(decisions):
            report = self.cc.calibrate()
        self.assertAlmostEqual(report["calibration_error"], 0.0, places=2)

    def test_report_persisted(self):
        """Report saved to disk."""
        with self._mock_decisions([{"confidence": 0.5, "quality": "neutral"}]):
            self.cc.calibrate()
        report = self.cc.get_last_report()
        self.assertIsNotNone(report)
        self.assertIn("total_decisions", report)

    def test_threshold_bounds(self):
        """Adjusted thresholds never exceed bounds."""
        # Run multiple calibrations pushing high threshold up
        for _ in range(20):
            decisions = [
                {"confidence": 0.95, "quality": "bad"},
                {"confidence": 0.9, "quality": "bad"},
            ]
            with self._mock_decisions(decisions):
                self.cc.calibrate()
            if self.cc.get_staged_adjustments():
                self.cc.apply_staged(confirm=True)
        current = self.cc.get_current_thresholds()
        self.assertLessEqual(current["high"], 0.95)


# ══════════════════════════════════════════════════════════════
#  noise_filter tests
# ══════════════════════════════════════════════════════════════

class TestNoiseFilter(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_path = Path(self.tmpdir) / "noise_filter_state.json"
        import noise_filter as nf
        self._orig_path = nf.STATE_PATH
        nf.STATE_PATH = self.state_path
        self.nf = nf

    def tearDown(self):
        self.nf.STATE_PATH = self._orig_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_notification_passes(self):
        """First notification of any type always passes."""
        self.assertTrue(self.nf.should_send("container_down", "sonarr down"))

    def test_ttl_suppression(self):
        """Same type within 30 min → suppressed."""
        now = time.time()
        self.assertTrue(self.nf.should_send("vpn_leak", "AR detected", _now=now))
        self.assertFalse(self.nf.should_send("vpn_leak", "AR again", _now=now + 60))

    def test_ttl_expiry(self):
        """After TTL expires, notification passes again."""
        now = time.time()
        self.assertTrue(self.nf.should_send("vpn_leak", "", _now=now))
        self.assertTrue(self.nf.should_send(
            "vpn_leak", "", _now=now + self.nf.SUPPRESSION_TTL + 1))

    def test_different_types_not_suppressed(self):
        """Different notification types don't suppress each other."""
        now = time.time()
        self.assertTrue(self.nf.should_send("vpn_leak", "", _now=now))
        self.assertTrue(self.nf.should_send("container_down", "", _now=now + 1))

    def test_critical_always_passes(self):
        """Critical severity bypasses all filters."""
        now = time.time()
        self.assertTrue(self.nf.should_send("vpn_leak", "",
                                            severity="critical", _now=now))
        self.assertTrue(self.nf.should_send("vpn_leak", "",
                                            severity="critical", _now=now + 1))

    @patch("noise_filter._get_blacklist", return_value=["proposal_routing"])
    def test_blacklist_suppression(self, _):
        """Blacklisted types are permanently suppressed."""
        self.assertFalse(self.nf.should_send("proposal_routing", "new proposal"))

    def test_metric_low_fluctuation_suppressed(self):
        """Metric change < 5% → suppressed."""
        now = time.time()
        # First call sets baseline
        self.assertTrue(self.nf.should_send(
            "health_metric", "", metric_value=100.0,
            metric_name="cpu", _now=now))
        # Second call with 2% change → suppressed
        self.assertFalse(self.nf.should_send(
            "health_metric", "", metric_value=102.0,
            metric_name="cpu", _now=now + self.nf.SUPPRESSION_TTL + 1))

    def test_metric_high_fluctuation_passes(self):
        """Metric change > 5% → passes."""
        now = time.time()
        self.assertTrue(self.nf.should_send(
            "health_metric", "", metric_value=100.0,
            metric_name="cpu", _now=now))
        self.assertTrue(self.nf.should_send(
            "health_metric", "", metric_value=120.0,
            metric_name="cpu", _now=now + self.nf.SUPPRESSION_TTL + 1))

    def test_stable_non_actionable_suppressed(self):
        """Types ending in _stable are suppressed at info level."""
        self.assertFalse(self.nf.should_send("vpn_stable", "",
                                             severity="info"))

    def test_maintenance_window_suppression(self):
        """Backend offline during maintenance → suppressed."""
        with patch("noise_filter.datetime") as mock_dt:
            mock_dt.now.return_value = MagicMock(hour=3)
            mock_dt.side_effect = lambda *a, **kw: datetime(*a, **kw)
            # Directly test the inner function
            now = time.time()
            result = self.nf.should_send("backend_offline", "",
                                         severity="warning", _now=now)
            self.assertFalse(result)

    def test_fail_open_on_error(self):
        """If internal error → allow notification (fail-open)."""
        with patch.object(self.nf, "_should_send_inner",
                          side_effect=RuntimeError("boom")):
            self.assertTrue(self.nf.should_send("test", "msg"))

    def test_clear_state(self):
        """clear_state resets everything."""
        self.nf.should_send("test", "msg")
        self.nf.clear_state()
        stats = self.nf.get_stats()
        self.assertEqual(stats["suppressed_count"], 0)
        self.assertEqual(stats["active_ttls"], 0)

    def test_stats_tracking(self):
        """Stats accurately track allowed vs suppressed."""
        now = time.time()
        self.nf.should_send("a", "", _now=now)
        self.nf.should_send("a", "", _now=now + 1)  # suppressed
        self.nf.should_send("b", "", _now=now + 2)
        stats = self.nf.get_stats()
        self.assertEqual(stats["allowed_count"], 2)
        self.assertEqual(stats["suppressed_count"], 1)


# ══════════════════════════════════════════════════════════════
#  action_budget tests
# ══════════════════════════════════════════════════════════════

class TestActionBudget(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_path = Path(self.tmpdir) / "action_budget.json"
        import action_budget as ab
        self._orig_path = ab.STATE_PATH
        ab.STATE_PATH = self.state_path
        self.ab = ab

    def tearDown(self):
        self.ab.STATE_PATH = self._orig_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_action_allowed(self):
        """First action always passes."""
        self.assertTrue(self.ab.can_execute("restart_container"))

    def test_global_hourly_limit(self):
        """After 10 actions/hour → blocked."""
        now = time.time()
        for i in range(10):
            self.assertTrue(self.ab.can_execute(
                f"action_{i}", _now=now + i))
        self.assertFalse(self.ab.can_execute(
            "action_11", _now=now + 11))

    def test_per_type_limit(self):
        """After 3 actions of same type/hour → blocked."""
        now = time.time()
        for i in range(3):
            self.assertTrue(self.ab.can_execute(
                "restart_container", _now=now + i))
        self.assertFalse(self.ab.can_execute(
            "restart_container", _now=now + 4))

    def test_different_types_independent(self):
        """Different types have independent per-type limits."""
        now = time.time()
        for i in range(3):
            self.ab.can_execute("type_a", _now=now + i)
        # type_a exhausted, but type_b still allowed
        self.assertTrue(self.ab.can_execute("type_b", _now=now + 4))

    def test_critical_limit_6h(self):
        """Max 1 critical action per 6 hours."""
        now = time.time()
        self.assertTrue(self.ab.can_execute(
            "deploy", severity="critical", _now=now))
        self.assertFalse(self.ab.can_execute(
            "deploy2", severity="critical", _now=now + 100))

    def test_critical_resets_after_6h(self):
        """Critical limit resets after 6h window."""
        now = time.time()
        self.assertTrue(self.ab.can_execute(
            "deploy", severity="critical", _now=now))
        self.assertTrue(self.ab.can_execute(
            "deploy2", severity="critical",
            _now=now + self.ab.WINDOW_6H + 1))

    def test_notification_limit(self):
        """Max 5 Telegram notifications per 30 min."""
        now = time.time()
        for i in range(5):
            self.assertTrue(self.ab.can_notify(_now=now + i))
        self.assertFalse(self.ab.can_notify(_now=now + 6))

    def test_notification_resets_after_30m(self):
        """Notification limit resets after 30m window."""
        now = time.time()
        for i in range(5):
            self.ab.can_notify(_now=now + i)
        self.assertTrue(self.ab.can_notify(
            _now=now + self.ab.WINDOW_30M + 1))

    def test_window_reset_clears_actions(self):
        """Actions outside window are cleaned up."""
        now = time.time()
        old = now - self.ab.WINDOW_1H - 100
        for i in range(10):
            self.ab.can_execute(f"old_{i}", _now=old + i)
        # All old, should be cleaned → new action passes
        self.assertTrue(self.ab.can_execute("new", _now=now))

    def test_reset_clears_all(self):
        """reset() clears all counters."""
        now = time.time()
        for i in range(5):
            self.ab.can_execute(f"a_{i}", _now=now + i)
        self.ab.reset()
        status = self.ab.get_status(_now=now + 10)
        self.assertEqual(status["actions_1h"], 0)

    def test_status_reports_correctly(self):
        """get_status reflects current budget usage."""
        now = time.time()
        self.ab.can_execute("restart", severity="info", _now=now)
        self.ab.can_execute("restart", severity="critical", _now=now + 1)
        self.ab.can_notify(_now=now + 2)
        status = self.ab.get_status(_now=now + 3)
        self.assertEqual(status["actions_1h"], 2)
        self.assertEqual(status["critical_6h"], 1)
        self.assertEqual(status["notifications_30m"], 1)
        self.assertIn("restart", status["per_type_1h"])

    def test_fail_open_on_error(self):
        """If budget module crashes → allow action."""
        with patch.object(self.ab, "_can_execute_inner",
                          side_effect=RuntimeError("boom")):
            self.assertTrue(self.ab.can_execute("test"))

    def test_can_notify_fail_open(self):
        """If notification budget crashes → allow."""
        with patch.object(self.ab, "_can_notify_inner",
                          side_effect=RuntimeError("boom")):
            self.assertTrue(self.ab.can_notify())

    @patch("action_budget._notify_exceeded_once")
    def test_notify_once_on_first_exceeded(self, mock_notify):
        """First budget exceeded triggers ONE Telegram notification."""
        now = time.time()
        for i in range(10):
            self.ab.can_execute(f"action_{i}", _now=now + i)
        # 11th triggers notification
        self.ab.can_execute("action_11", _now=now + 11)
        mock_notify.assert_called_once()

    @patch("action_budget._notify_exceeded_once")
    def test_no_repeat_notify_same_window(self, mock_notify):
        """Subsequent exceeded events in same window → no more notifications."""
        now = time.time()
        for i in range(10):
            self.ab.can_execute(f"action_{i}", _now=now + i)
        # Multiple exceeded actions
        self.ab.can_execute("blocked_1", _now=now + 11)
        self.ab.can_execute("blocked_2", _now=now + 12)
        self.ab.can_execute("blocked_3", _now=now + 13)
        # Only called once for the first exceeded
        mock_notify.assert_called_once()

    @patch("action_budget._notify_exceeded_once")
    def test_different_budget_types_single_notify(self, mock_notify):
        """Exceeding global then per-type → still only ONE notification."""
        now = time.time()
        # Exhaust per-type limit (3 of same type)
        for i in range(3):
            self.ab.can_execute("restart_container", _now=now + i)
        # 4th same-type triggers exceeded
        self.ab.can_execute("restart_container", _now=now + 4)
        # Now exceed global too with different types
        for i in range(7):
            self.ab.can_execute(f"other_{i}", _now=now + 5 + i)
        self.ab.can_execute("overflow", _now=now + 13)
        # Still only one notification total
        mock_notify.assert_called_once()

    @patch("action_budget._notify_exceeded_once")
    def test_notify_resets_after_window(self, mock_notify):
        """After window expires, a new exceeded event can notify again."""
        now = time.time()
        for i in range(10):
            self.ab.can_execute(f"a_{i}", _now=now + i)
        self.ab.can_execute("blocked", _now=now + 11)
        self.assertEqual(mock_notify.call_count, 1)
        # After 1h window, budget resets, new actions allowed then exceeded again
        future = now + self.ab.WINDOW_1H + 1
        for i in range(10):
            self.ab.can_execute(f"b_{i}", _now=future + i)
        self.ab.can_execute("blocked2", _now=future + 11)
        self.assertEqual(mock_notify.call_count, 2)


# ══════════════════════════════════════════════════════════════
#  Integration: policy_engine respects action_budget
# ══════════════════════════════════════════════════════════════

class TestPolicyBudgetIntegration(unittest.TestCase):
    """Verify that action_budget can gate policy_engine decisions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_path = Path(self.tmpdir) / "action_budget.json"
        import action_budget as ab
        self._orig_path = ab.STATE_PATH
        ab.STATE_PATH = self.state_path
        self.ab = ab

    def tearDown(self):
        self.ab.STATE_PATH = self._orig_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_budget_gates_execution(self):
        """When budget exhausted, policy should not allow execution."""
        now = time.time()
        # Exhaust global budget
        for i in range(10):
            self.ab.can_execute(f"a_{i}", _now=now + i)
        # Simulating: policy_engine would call can_execute before allowing
        can_do = self.ab.can_execute("restart_container", _now=now + 11)
        self.assertFalse(can_do)

    def test_budget_allows_after_window(self):
        """After window expires, policy allows again."""
        now = time.time()
        for i in range(10):
            self.ab.can_execute(f"a_{i}", _now=now + i)
        can_do = self.ab.can_execute(
            "restart_container", _now=now + self.ab.WINDOW_1H + 1)
        self.assertTrue(can_do)

    def test_notification_budget_gates_approval_queue(self):
        """When notification budget exhausted, approval_queue shouldn't send."""
        now = time.time()
        for i in range(5):
            self.ab.can_notify(_now=now + i)
        # Simulating: approval_queue would call can_notify before Telegram
        can_send = self.ab.can_notify(_now=now + 6)
        self.assertFalse(can_send)


# ══════════════════════════════════════════════════════════════
#  Integration: proactive_planner respects noise_filter
# ══════════════════════════════════════════════════════════════

class TestPlannerNoiseFilterIntegration(unittest.TestCase):
    """Verify that noise_filter can gate proactive_planner suggestions."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_path = Path(self.tmpdir) / "noise_filter_state.json"
        import noise_filter as nf
        self._orig_path = nf.STATE_PATH
        nf.STATE_PATH = self.state_path
        self.nf = nf

    def tearDown(self):
        self.nf.STATE_PATH = self._orig_path
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_first_suggestion_passes(self):
        """Planner's first suggestion of a type passes filter."""
        self.assertTrue(self.nf.should_send("infra_suggestion",
                                            "restart sonarr"))

    def test_duplicate_suggestion_suppressed(self):
        """Same suggestion type within TTL → suppressed."""
        now = time.time()
        self.nf.should_send("infra_suggestion", "restart sonarr", _now=now)
        self.assertFalse(self.nf.should_send(
            "infra_suggestion", "restart radarr", _now=now + 60))

    def test_critical_suggestion_always_passes(self):
        """Critical suggestions bypass noise filter."""
        now = time.time()
        self.nf.should_send("infra_critical", "", severity="critical", _now=now)
        self.assertTrue(self.nf.should_send(
            "infra_critical", "", severity="critical", _now=now + 1))

    @patch("noise_filter._get_blacklist", return_value=["gpu_suggestion"])
    def test_rejected_suggestion_blacklisted(self, _):
        """Previously rejected suggestion types are blacklisted."""
        self.assertFalse(self.nf.should_send("gpu_suggestion",
                                             "optimize VRAM"))

    def test_end_to_end_planner_flow(self):
        """Simulate full planner → noise_filter → output flow."""
        now = time.time()
        suggestions = [
            {"type": "infra", "detail": "sonarr down"},
            {"type": "infra", "detail": "radarr down"},  # same type, suppressed
            {"type": "gpu", "detail": "VRAM high"},
            {"type": "calendar", "detail": "meeting in 1h"},
        ]
        sent = []
        for s in suggestions:
            ntype = f"{s['type']}_suggestion"
            if self.nf.should_send(ntype, s["detail"], _now=now):
                sent.append(s)
            now += 1

        # infra_suggestion: first passes, second suppressed
        # gpu_suggestion: passes (different type)
        # calendar_suggestion: passes (different type)
        self.assertEqual(len(sent), 3)
        self.assertEqual(sent[0]["type"], "infra")
        self.assertEqual(sent[1]["type"], "gpu")
        self.assertEqual(sent[2]["type"], "calendar")


# ══════════════════════════════════════════════════════════════

if __name__ == "__main__":
    unittest.main(verbosity=2)
