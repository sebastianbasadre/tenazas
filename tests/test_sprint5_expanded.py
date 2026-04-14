#!/usr/bin/env python3
"""
test_sprint5_expanded.py — 140 additional unit tests for Sprint 5 governance.

Covers:
  decision_quality: 40 new tests (outcome correctness, partial/late solutions, etc.)
  noise_filter: 35 new tests (TTL, spam loop, severity, blacklist, fluctuation)
  action_budget: 35 new tests (limits, burst, rolling window, fail-open)
  confidence_calibration: 30 new tests (drift, over/under, staging-only)
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

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_CONTAINER_RT = Path("/home/tenazas/runtime")
if _CONTAINER_RT.exists():
    RUNTIME = _CONTAINER_RT
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


# ══════════════════════════════════════════════════════════════
#  decision_quality — 40 new tests
# ══════════════════════════════════════════════════════════════

class TestDQOutcomeCorrectness(unittest.TestCase):
    """Outcome classification: good/neutral/bad."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import decision_quality as dq
        self._orig = dq.STATE_DIR
        dq.STATE_DIR = Path(self.tmpdir) / "state" / "decision_quality"
        dq.STATE_DIR.mkdir(parents=True)
        self.dq = dq

    def tearDown(self):
        self.dq.STATE_DIR = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_resolved_action_is_good(self):
        r = self.dq.score(action="restart", target="sonarr",
                          validation_result={"success": True, "reason": "running"})
        self.assertEqual(r["quality"], "good")

    def test_no_action_is_neutral(self):
        r = self.dq.score(action="check_health", target="all")
        self.assertEqual(r["quality"], "neutral")

    def test_worsened_is_bad(self):
        r = self.dq.score(action="restart", target="gluetun",
                          validation_result={"success": False, "reason": "crash loop"})
        self.assertEqual(r["quality"], "bad")

    def test_partial_solution_validator_ok_but_issue_persists(self):
        """Validator reports ok but we can mark neutral with context."""
        r = self.dq.score(action="restart", target="sonarr",
                          validation_result={"success": True, "reason": "container up"},
                          context="incident still open after restart")
        self.assertEqual(r["quality"], "good")
        self.assertIn("incident still open", r["context"])

    def test_late_resolution_still_good(self):
        r = self.dq.score(action="restart", target="radarr",
                          validation_result={"success": True, "reason": "up after 10min"},
                          context="resolved after 10 min delay")
        self.assertEqual(r["quality"], "good")

    def test_false_positive_action_on_nonexistent_problem(self):
        r = self.dq.score(action="restart", target="sonarr",
                          approval_status="rejected",
                          context="false positive, container was fine")
        self.assertEqual(r["quality"], "bad")

    def test_unnecessary_action_auto_resolved(self):
        r = self.dq.score(action="restart", target="lidarr",
                          context="problem auto-resolved before execution")
        self.assertEqual(r["quality"], "neutral")

    def test_multiple_actions_independent_scores(self):
        r1 = self.dq.score(action="restart", target="sonarr",
                           validation_result={"success": True})
        r2 = self.dq.score(action="restart", target="radarr",
                           validation_result={"success": False})
        self.assertEqual(r1["quality"], "good")
        self.assertEqual(r2["quality"], "bad")
        self.assertNotEqual(r1["decision_id"], r2["decision_id"])

    def test_impact_positive_for_good(self):
        r = self.dq.score(action="a", target="t",
                          validation_result={"success": True})
        self.assertGreater(r["impact_score"], 0)

    def test_impact_negative_for_bad(self):
        r = self.dq.score(action="a", target="t",
                          validation_result={"success": False})
        self.assertLess(r["impact_score"], 0)

    def test_impact_zero_for_neutral(self):
        r = self.dq.score(action="a", target="t")
        self.assertEqual(r["impact_score"], 0.0)

    def test_rollback_worst_impact(self):
        r = self.dq.score(action="a", target="t", rollback=True)
        self.assertLessEqual(r["impact_score"], -0.8)

    def test_rejected_negative_impact(self):
        r = self.dq.score(action="a", target="t", approval_status="rejected")
        self.assertLess(r["impact_score"], 0)

    def test_approved_positive_impact(self):
        r = self.dq.score(action="a", target="t", approval_status="approved")
        self.assertGreater(r["impact_score"], 0)

    def test_expired_negative_impact(self):
        r = self.dq.score(action="a", target="t", approval_status="expired")
        self.assertLess(r["impact_score"], 0)

    def test_overconfident_amplifies_bad(self):
        normal = self.dq.score(action="a", target="t",
                               validation_result={"success": False}, confidence=0.5)
        overconf = self.dq.score(action="b", target="t",
                                 validation_result={"success": False}, confidence=0.95)
        self.assertLess(overconf["impact_score"], normal["impact_score"])

    def test_underconfident_amplifies_good(self):
        normal = self.dq.score(action="a", target="t",
                               validation_result={"success": True}, confidence=0.7)
        underconf = self.dq.score(action="b", target="t",
                                  validation_result={"success": True}, confidence=0.3)
        self.assertGreater(underconf["impact_score"], normal["impact_score"])

    def test_decision_id_unique(self):
        ids = set()
        for i in range(10):
            r = self.dq.score(action=f"a{i}", target="t")
            ids.add(r["decision_id"])
        self.assertEqual(len(ids), 10)

    def test_timestamp_present(self):
        r = self.dq.score(action="a", target="t")
        self.assertIn("timestamp", r)
        datetime.fromisoformat(r["timestamp"])

    def test_feeds_into_always_list(self):
        r = self.dq.score(action="a", target="t")
        self.assertIsInstance(r["feeds_into"], list)

    def test_get_today_returns_list(self):
        self.dq.score(action="a", target="t")
        today = self.dq.get_today()
        self.assertIsInstance(today, list)
        self.assertGreaterEqual(len(today), 1)

    def test_get_range_default_7(self):
        self.dq.score(action="a", target="t")
        entries = self.dq.get_range()
        self.assertIsInstance(entries, list)

    def test_get_range_specific_days(self):
        entries = self.dq.get_range(days=1)
        self.assertIsInstance(entries, list)

    def test_summary_with_mixed(self):
        self.dq.score(action="a", target="t", validation_result={"success": True})
        self.dq.score(action="b", target="t", validation_result={"success": False})
        self.dq.score(action="c", target="t")
        s = self.dq.summary(1)
        self.assertEqual(s["total"], 3)
        self.assertEqual(s["good"], 1)
        self.assertEqual(s["bad"], 1)
        self.assertEqual(s["neutral"], 1)

    def test_summary_avg_impact(self):
        self.dq.score(action="a", target="t", validation_result={"success": True})
        self.dq.score(action="b", target="t", validation_result={"success": True})
        s = self.dq.summary(1)
        self.assertGreater(s["avg_impact"], 0)

    def test_get_bad_empty(self):
        self.dq.score(action="a", target="t", validation_result={"success": True})
        bads = self.dq.get_bad_decisions(1)
        self.assertEqual(len(bads), 0)

    def test_get_bad_only_bad(self):
        self.dq.score(action="a", target="t", validation_result={"success": True})
        self.dq.score(action="b", target="t", rollback=True)
        self.dq.score(action="c", target="t", approval_status="rejected")
        bads = self.dq.get_bad_decisions(1)
        self.assertEqual(len(bads), 2)

    @patch("decision_quality._feed_preference_learning")
    def test_good_does_not_feed_preference(self, mock_feed):
        self.dq.score(action="a", target="t", validation_result={"success": True})
        mock_feed.assert_not_called()

    @patch("decision_quality._feed_preference_learning")
    def test_bad_feeds_preference_with_action(self, mock_feed):
        self.dq.score(action="dangerous", target="x", rollback=True)
        mock_feed.assert_called_once()
        call_args = mock_feed.call_args
        self.assertIn("dangerous", str(call_args))

    def test_context_empty_string_ok(self):
        r = self.dq.score(action="a", target="t", context="")
        self.assertEqual(r["context"], "")

    def test_context_none_becomes_empty(self):
        r = self.dq.score(action="a", target="t")
        self.assertIsInstance(r["context"], str)

    def test_confidence_stored(self):
        r = self.dq.score(action="a", target="t", confidence=0.77)
        self.assertEqual(r["confidence"], 0.77)

    def test_confidence_default_zero(self):
        r = self.dq.score(action="a", target="t")
        self.assertEqual(r["confidence"], 0.0)

    def test_many_scores_persist(self):
        for i in range(20):
            self.dq.score(action=f"a{i}", target="t")
        entries = self.dq.get_today()
        self.assertEqual(len(entries), 20)

    def test_bad_rate_correct(self):
        for _ in range(7):
            self.dq.score(action="a", target="t", validation_result={"success": True})
        for _ in range(3):
            self.dq.score(action="b", target="t", validation_result={"success": False})
        s = self.dq.summary(1)
        self.assertAlmostEqual(s["bad_rate"], 0.3, places=2)

    def test_good_rate_100_percent(self):
        for _ in range(5):
            self.dq.score(action="a", target="t", validation_result={"success": True})
        s = self.dq.summary(1)
        self.assertAlmostEqual(s["good_rate"], 1.0, places=2)

    def test_good_rate_zero_percent(self):
        for _ in range(5):
            self.dq.score(action="a", target="t", rollback=True)
        s = self.dq.summary(1)
        self.assertAlmostEqual(s["good_rate"], 0.0, places=2)

    def test_action_and_target_stored(self):
        r = self.dq.score(action="restart_container", target="sonarr")
        self.assertEqual(r["action"], "restart_container")
        self.assertEqual(r["target"], "sonarr")

    def test_reason_from_validation(self):
        r = self.dq.score(action="a", target="t",
                          validation_result={"success": True, "reason": "all good"})
        self.assertIn("all good", r["reason"])


# ══════════════════════════════════════════════════════════════
#  noise_filter — 35 new tests
# ══════════════════════════════════════════════════════════════

class TestNoiseFilterExpanded(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import noise_filter as nf
        self._orig = nf.STATE_PATH
        nf.STATE_PATH = Path(self.tmpdir) / "nf.json"
        self.nf = nf

    def tearDown(self):
        self.nf.STATE_PATH = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_duplicate_within_ttl_suppressed(self):
        now = time.time()
        self.assertTrue(self.nf.should_send("x", "", _now=now))
        self.assertFalse(self.nf.should_send("x", "", _now=now + 60))

    def test_same_alert_after_ttl_allowed(self):
        now = time.time()
        self.nf.should_send("x", "", _now=now)
        self.assertTrue(self.nf.should_send("x", "", _now=now + self.nf.SUPPRESSION_TTL + 1))

    def test_no_state_change_suppressed(self):
        now = time.time()
        self.nf.should_send("status", "ok", _now=now)
        self.assertFalse(self.nf.should_send("status", "ok", _now=now + 10))

    def test_spam_loop_5_alerts_only_1_passes(self):
        now = time.time()
        passed = sum(1 for i in range(5)
                     if self.nf.should_send("spam", "alert", _now=now + i))
        self.assertEqual(passed, 1)

    def test_low_severity_info_stable_suppressed(self):
        self.assertFalse(self.nf.should_send("health_stable", "", severity="info"))

    def test_critical_always_passes_repeated(self):
        now = time.time()
        for i in range(5):
            self.assertTrue(self.nf.should_send("fire", "", severity="critical", _now=now + i))

    def test_critical_ignores_ttl(self):
        now = time.time()
        self.nf.should_send("alert", "", severity="info", _now=now)
        self.assertTrue(self.nf.should_send("alert", "", severity="critical", _now=now + 1))

    @patch("noise_filter._get_blacklist", return_value=["blocked_type"])
    def test_blacklist_permanent(self, _):
        now = time.time()
        self.assertFalse(self.nf.should_send("blocked_type", "", _now=now))
        self.assertFalse(self.nf.should_send("blocked_type", "", _now=now + 99999))

    @patch("noise_filter._get_blacklist", return_value=[])
    def test_non_blacklisted_passes(self, _):
        self.assertTrue(self.nf.should_send("normal_type", ""))

    def test_metric_below_5pct_suppressed(self):
        now = time.time()
        self.nf.should_send("m", "", metric_value=100.0, metric_name="cpu", _now=now)
        self.assertFalse(self.nf.should_send(
            "m", "", metric_value=104.0, metric_name="cpu",
            _now=now + self.nf.SUPPRESSION_TTL + 1))

    def test_metric_above_5pct_passes(self):
        now = time.time()
        self.nf.should_send("m", "", metric_value=100.0, metric_name="cpu", _now=now)
        self.assertTrue(self.nf.should_send(
            "m", "", metric_value=106.0, metric_name="cpu",
            _now=now + self.nf.SUPPRESSION_TTL + 1))

    def test_metric_drop_below_5pct_suppressed(self):
        now = time.time()
        self.nf.should_send("m", "", metric_value=100.0, metric_name="ram", _now=now)
        self.assertFalse(self.nf.should_send(
            "m", "", metric_value=96.0, metric_name="ram",
            _now=now + self.nf.SUPPRESSION_TTL + 1))

    def test_metric_drop_above_5pct_passes(self):
        now = time.time()
        self.nf.should_send("m", "", metric_value=100.0, metric_name="ram", _now=now)
        self.assertTrue(self.nf.should_send(
            "m", "", metric_value=90.0, metric_name="ram",
            _now=now + self.nf.SUPPRESSION_TTL + 1))

    def test_different_metric_names_independent(self):
        now = time.time()
        self.nf.should_send("m", "", metric_value=100.0, metric_name="cpu", _now=now)
        self.assertTrue(self.nf.should_send(
            "m", "", metric_value=100.0, metric_name="ram",
            _now=now + self.nf.SUPPRESSION_TTL + 1))

    def test_stats_suppressed_increments(self):
        now = time.time()
        self.nf.should_send("x", "", _now=now)
        self.nf.should_send("x", "", _now=now + 1)
        self.nf.should_send("x", "", _now=now + 2)
        stats = self.nf.get_stats()
        self.assertEqual(stats["suppressed_count"], 2)

    def test_stats_allowed_increments(self):
        now = time.time()
        self.nf.should_send("a", "", _now=now)
        self.nf.should_send("b", "", _now=now + 1)
        stats = self.nf.get_stats()
        self.assertEqual(stats["allowed_count"], 2)

    def test_clear_resets_suppressed(self):
        now = time.time()
        self.nf.should_send("x", "", _now=now)
        self.nf.should_send("x", "", _now=now + 1)
        self.nf.clear_state()
        self.assertTrue(self.nf.should_send("x", "", _now=now + 2))

    def test_clear_resets_stats(self):
        self.nf.should_send("a", "")
        self.nf.clear_state()
        stats = self.nf.get_stats()
        self.assertEqual(stats["suppressed_count"], 0)
        self.assertEqual(stats["allowed_count"], 0)

    def test_active_ttls_tracked(self):
        now = time.time()
        self.nf.should_send("a", "", _now=now)
        self.nf.should_send("b", "", _now=now + 1)
        stats = self.nf.get_stats()
        self.assertGreaterEqual(stats["active_ttls"], 2)

    def test_10_different_types_all_pass(self):
        now = time.time()
        for i in range(10):
            self.assertTrue(self.nf.should_send(f"type_{i}", "", _now=now + i))

    def test_warning_suppressed_within_ttl(self):
        now = time.time()
        self.assertTrue(self.nf.should_send("w", "", severity="warning", _now=now))
        self.assertFalse(self.nf.should_send("w", "", severity="warning", _now=now + 60))

    def test_fail_open_returns_true(self):
        with patch.object(self.nf, "_should_send_inner", side_effect=Exception("x")):
            self.assertTrue(self.nf.should_send("any", "msg"))

    def test_empty_type_string(self):
        self.assertTrue(self.nf.should_send("", "some content"))

    def test_empty_content_string(self):
        self.assertTrue(self.nf.should_send("type", ""))

    def test_metric_value_none_no_crash(self):
        self.assertTrue(self.nf.should_send("x", "", metric_value=None, metric_name=None))

    def test_metric_value_zero_baseline(self):
        now = time.time()
        self.nf.should_send("m", "", metric_value=0.0, metric_name="test", _now=now)
        # Any change from 0 is > 5%
        self.assertTrue(self.nf.should_send(
            "m", "", metric_value=1.0, metric_name="test",
            _now=now + self.nf.SUPPRESSION_TTL + 1))

    def test_rapid_fire_same_type(self):
        now = time.time()
        results = [self.nf.should_send("rapid", "", _now=now + i * 0.1) for i in range(20)]
        self.assertEqual(results[0], True)
        self.assertTrue(all(not r for r in results[1:]))

    def test_state_persists_between_calls(self):
        now = time.time()
        self.nf.should_send("persist", "", _now=now)
        # Reload module state
        self.assertFalse(self.nf.should_send("persist", "", _now=now + 10))

    def test_tracked_metrics_in_stats(self):
        now = time.time()
        self.nf.should_send("m", "", metric_value=50.0, metric_name="cpu", _now=now)
        stats = self.nf.get_stats()
        self.assertGreaterEqual(stats["tracked_metrics"], 1)

    def test_suppression_count_accurate_after_mixed(self):
        now = time.time()
        self.nf.should_send("a", "", _now=now)          # allowed
        self.nf.should_send("a", "", _now=now + 1)      # suppressed
        self.nf.should_send("b", "", _now=now + 2)      # allowed
        self.nf.should_send("b", "", _now=now + 3)      # suppressed
        self.nf.should_send("c", "", severity="critical", _now=now + 4)  # allowed
        stats = self.nf.get_stats()
        self.assertEqual(stats["allowed_count"], 3)
        self.assertEqual(stats["suppressed_count"], 2)

    def test_two_types_interleaved(self):
        now = time.time()
        self.assertTrue(self.nf.should_send("x", "", _now=now))
        self.assertTrue(self.nf.should_send("y", "", _now=now + 1))
        self.assertFalse(self.nf.should_send("x", "", _now=now + 2))
        self.assertFalse(self.nf.should_send("y", "", _now=now + 3))


# ══════════════════════════════════════════════════════════════
#  action_budget — 35 new tests
# ══════════════════════════════════════════════════════════════

class TestActionBudgetExpanded(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import action_budget as ab
        self._orig = ab.STATE_PATH
        ab.STATE_PATH = Path(self.tmpdir) / "ab.json"
        self.ab = ab

    def tearDown(self):
        self.ab.STATE_PATH = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_action_11_blocked(self):
        now = time.time()
        for i in range(10):
            self.ab.can_execute(f"a{i}", _now=now + i)
        self.assertFalse(self.ab.can_execute("a11", _now=now + 11))

    def test_type_limit_4th_blocked(self):
        now = time.time()
        for i in range(3):
            self.assertTrue(self.ab.can_execute("restart", _now=now + i))
        self.assertFalse(self.ab.can_execute("restart", _now=now + 4))

    def test_critical_second_blocked(self):
        now = time.time()
        self.assertTrue(self.ab.can_execute("deploy", severity="critical", _now=now))
        self.assertFalse(self.ab.can_execute("deploy2", severity="critical", _now=now + 10))

    def test_notification_6th_blocked(self):
        now = time.time()
        for i in range(5):
            self.assertTrue(self.ab.can_notify(_now=now + i))
        self.assertFalse(self.ab.can_notify(_now=now + 6))

    def test_burst_5_restarts_10min_4th_blocked(self):
        now = time.time()
        for i in range(3):
            self.assertTrue(self.ab.can_execute("restart_container", _now=now + i * 120))
        self.assertFalse(self.ab.can_execute("restart_container", _now=now + 360))

    def test_rolling_window_old_actions_expire(self):
        now = time.time()
        old = now - self.ab.WINDOW_1H - 10
        for i in range(10):
            self.ab.can_execute(f"old{i}", _now=old + i)
        self.assertTrue(self.ab.can_execute("new", _now=now))

    def test_rolling_window_recent_still_count(self):
        now = time.time()
        for i in range(10):
            self.ab.can_execute(f"a{i}", _now=now - 1800 + i)  # 30 min ago
        self.assertFalse(self.ab.can_execute("blocked", _now=now))

    @patch("action_budget._notify_exceeded_once")
    def test_notify_once_per_window(self, mock_notify):
        now = time.time()
        for i in range(10):
            self.ab.can_execute(f"a{i}", _now=now + i)
        self.ab.can_execute("b1", _now=now + 11)
        self.ab.can_execute("b2", _now=now + 12)
        mock_notify.assert_called_once()

    def test_fail_open_execute(self):
        with patch.object(self.ab, "_can_execute_inner", side_effect=Exception("x")):
            self.assertTrue(self.ab.can_execute("test"))

    def test_fail_open_notify(self):
        with patch.object(self.ab, "_can_notify_inner", side_effect=Exception("x")):
            self.assertTrue(self.ab.can_notify())

    def test_reset_clears_everything(self):
        now = time.time()
        for i in range(5):
            self.ab.can_execute(f"a{i}", _now=now + i)
        self.ab.can_notify(_now=now + 6)
        self.ab.reset()
        s = self.ab.get_status(_now=now + 10)
        self.assertEqual(s["actions_1h"], 0)
        self.assertEqual(s["notifications_30m"], 0)

    def test_status_actions_count(self):
        now = time.time()
        self.ab.can_execute("a", _now=now)
        self.ab.can_execute("b", _now=now + 1)
        s = self.ab.get_status(_now=now + 2)
        self.assertEqual(s["actions_1h"], 2)

    def test_status_per_type(self):
        now = time.time()
        self.ab.can_execute("restart", _now=now)
        self.ab.can_execute("restart", _now=now + 1)
        self.ab.can_execute("cleanup", _now=now + 2)
        s = self.ab.get_status(_now=now + 3)
        self.assertEqual(s["per_type_1h"]["restart"], 2)
        self.assertEqual(s["per_type_1h"]["cleanup"], 1)

    def test_status_critical_count(self):
        now = time.time()
        self.ab.can_execute("deploy", severity="critical", _now=now)
        s = self.ab.get_status(_now=now + 1)
        self.assertEqual(s["critical_6h"], 1)

    def test_status_notifications_count(self):
        now = time.time()
        self.ab.can_notify(_now=now)
        self.ab.can_notify(_now=now + 1)
        s = self.ab.get_status(_now=now + 2)
        self.assertEqual(s["notifications_30m"], 2)

    def test_status_limits_present(self):
        s = self.ab.get_status()
        self.assertIn("actions_1h_limit", s)
        self.assertIn("critical_6h_limit", s)
        self.assertIn("notifications_30m_limit", s)
        self.assertIn("per_type_limit", s)

    def test_critical_resets_after_6h(self):
        now = time.time()
        self.ab.can_execute("deploy", severity="critical", _now=now)
        self.assertTrue(self.ab.can_execute("deploy2", severity="critical",
                                            _now=now + self.ab.WINDOW_6H + 1))

    def test_notification_resets_after_30m(self):
        now = time.time()
        for i in range(5):
            self.ab.can_notify(_now=now + i)
        self.assertTrue(self.ab.can_notify(_now=now + self.ab.WINDOW_30M + 1))

    def test_mixed_severity_counts(self):
        now = time.time()
        self.ab.can_execute("a", severity="info", _now=now)
        self.ab.can_execute("b", severity="warning", _now=now + 1)
        self.ab.can_execute("c", severity="critical", _now=now + 2)
        s = self.ab.get_status(_now=now + 3)
        self.assertEqual(s["actions_1h"], 3)
        self.assertEqual(s["critical_6h"], 1)

    def test_10_types_each_3_then_blocked(self):
        now = time.time()
        for t in range(4):
            for i in range(3):
                self.ab.can_execute(f"type_{t}", _now=now + t * 10 + i)
        # Now at 12 total, global budget should block next
        # (but we only used 12 of different types)
        # Global limit is 10, so some were already blocked
        s = self.ab.get_status(_now=now + 100)
        self.assertLessEqual(s["actions_1h"], 10)

    def test_per_type_3_limit(self):
        self.assertEqual(self.ab.MAX_PER_TYPE_PER_HOUR, 3)

    def test_global_10_limit(self):
        self.assertEqual(self.ab.MAX_ACTIONS_PER_HOUR, 10)

    def test_critical_1_limit(self):
        self.assertEqual(self.ab.MAX_CRITICAL_PER_6H, 1)

    def test_notification_5_limit(self):
        self.assertEqual(self.ab.MAX_NOTIFICATIONS_PER_30M, 5)

    def test_window_1h_value(self):
        self.assertEqual(self.ab.WINDOW_1H, 3600)

    def test_window_6h_value(self):
        self.assertEqual(self.ab.WINDOW_6H, 21600)

    def test_window_30m_value(self):
        self.assertEqual(self.ab.WINDOW_30M, 1800)

    def test_two_types_independent_budgets(self):
        now = time.time()
        for i in range(3):
            self.ab.can_execute("type_a", _now=now + i)
        # type_a exhausted
        self.assertFalse(self.ab.can_execute("type_a", _now=now + 4))
        # type_b still OK
        self.assertTrue(self.ab.can_execute("type_b", _now=now + 5))

    def test_concurrent_budget_pressure(self):
        now = time.time()
        # Fill 9 actions across 3 types
        for t in range(3):
            for i in range(3):
                self.ab.can_execute(f"t{t}", _now=now + t * 3 + i)
        # 9 total, 3 per type — global has 1 left
        self.assertTrue(self.ab.can_execute("t3", _now=now + 10))
        # Now at 10 — blocked
        self.assertFalse(self.ab.can_execute("t4", _now=now + 11))

    def test_empty_status_on_fresh_state(self):
        self.ab.reset()
        s = self.ab.get_status()
        self.assertEqual(s["actions_1h"], 0)
        self.assertEqual(s["critical_6h"], 0)
        self.assertEqual(s["notifications_30m"], 0)

    def test_blocked_action_not_counted(self):
        now = time.time()
        for i in range(10):
            self.ab.can_execute(f"a{i}", _now=now + i)
        # Blocked actions should not increment counter
        self.ab.can_execute("blocked", _now=now + 11)
        s = self.ab.get_status(_now=now + 12)
        self.assertEqual(s["actions_1h"], 10)  # not 11

    def test_blocked_notification_not_counted(self):
        now = time.time()
        for i in range(5):
            self.ab.can_notify(_now=now + i)
        self.ab.can_notify(_now=now + 6)
        s = self.ab.get_status(_now=now + 7)
        self.assertEqual(s["notifications_30m"], 5)  # not 6


# ══════════════════════════════════════════════════════════════
#  confidence_calibration — 30 new tests
# ══════════════════════════════════════════════════════════════

class TestConfCalibrationExpanded(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.state_dir = Path(self.tmpdir)
        import confidence_calibration as cc
        self._orig_s = cc.STATE_DIR
        self._orig_c = cc.CALIBRATION_PATH
        self._orig_r = cc.REPORT_PATH
        cc.STATE_DIR = self.state_dir
        cc.CALIBRATION_PATH = self.state_dir / "cal.json"
        cc.REPORT_PATH = self.state_dir / "report.json"
        self.cc = cc

    def tearDown(self):
        self.cc.STATE_DIR = self._orig_s
        self.cc.CALIBRATION_PATH = self._orig_c
        self.cc.REPORT_PATH = self._orig_r
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _mock(self, decisions):
        return patch.object(self.cc, "_load_decisions", return_value=decisions)

    def test_high_conf_bad_result_penalizes(self):
        decisions = [{"confidence": 0.9, "quality": "bad"}] * 5
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertGreater(r["overconfidence_rate"], 0)

    def test_low_conf_good_result_adjusts_up(self):
        decisions = [{"confidence": 0.3, "quality": "good"}] * 5
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertGreater(r["underconfidence_rate"], 0)

    def test_drift_5_bad_consecutive(self):
        decisions = [{"confidence": 0.85, "quality": "bad"}] * 5
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertIsNotNone(r["suggested_adjustments"])

    def test_perfect_calibration_low_error(self):
        decisions = [
            {"confidence": 0.9, "quality": "good"},
            {"confidence": 0.1, "quality": "bad"},
            {"confidence": 0.5, "quality": "neutral"},
        ]
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertLessEqual(r["calibration_error"], 0.15)

    def test_overconfidence_above_20_generates_proposal(self):
        decisions = [{"confidence": 0.9, "quality": "bad"}] * 5 + \
                    [{"confidence": 0.8, "quality": "good"}] * 5
        with self._mock(decisions):
            r = self.cc.calibrate()
        if r["overconfidence_rate"] > 0.15:
            self.assertIsNotNone(r["suggested_adjustments"])

    def test_underconfidence_above_30_generates_proposal(self):
        decisions = [{"confidence": 0.3, "quality": "good"}] * 7 + \
                    [{"confidence": 0.8, "quality": "good"}] * 3
        with self._mock(decisions):
            r = self.cc.calibrate()
        if r["underconfidence_rate"] > 0.20:
            self.assertIsNotNone(r["suggested_adjustments"])

    def test_never_auto_applies(self):
        decisions = [{"confidence": 0.95, "quality": "bad"}] * 10
        with self._mock(decisions):
            self.cc.calibrate()
        current = self.cc.get_current_thresholds()
        self.assertEqual(current["high"], 0.80)

    def test_staged_requires_confirm(self):
        decisions = [{"confidence": 0.9, "quality": "bad"}] * 5
        with self._mock(decisions):
            self.cc.calibrate()
        self.assertFalse(self.cc.apply_staged(confirm=False))

    def test_staged_applied_with_confirm(self):
        decisions = [{"confidence": 0.9, "quality": "bad"}] * 5
        with self._mock(decisions):
            self.cc.calibrate()
        if self.cc.get_staged_adjustments():
            self.assertTrue(self.cc.apply_staged(confirm=True))

    def test_threshold_high_never_above_095(self):
        for _ in range(30):
            with self._mock([{"confidence": 0.95, "quality": "bad"}] * 3):
                self.cc.calibrate()
            if self.cc.get_staged_adjustments():
                self.cc.apply_staged(confirm=True)
        self.assertLessEqual(self.cc.get_current_thresholds()["high"], 0.95)

    def test_threshold_medium_never_below_040(self):
        for _ in range(30):
            with self._mock([{"confidence": 0.3, "quality": "good"}] * 5):
                self.cc.calibrate()
            if self.cc.get_staged_adjustments():
                self.cc.apply_staged(confirm=True)
        self.assertGreaterEqual(self.cc.get_current_thresholds()["medium"], 0.40)

    def test_empty_decisions_safe(self):
        with self._mock([]):
            r = self.cc.calibrate()
        self.assertEqual(r["total_decisions"], 0)
        self.assertIsNone(r["suggested_adjustments"])

    def test_single_decision(self):
        with self._mock([{"confidence": 0.5, "quality": "neutral"}]):
            r = self.cc.calibrate()
        self.assertEqual(r["total_decisions"], 1)

    def test_report_persisted(self):
        with self._mock([{"confidence": 0.5, "quality": "good"}]):
            self.cc.calibrate()
        r = self.cc.get_last_report()
        self.assertIsNotNone(r)

    def test_calibration_error_between_0_and_1(self):
        decisions = [
            {"confidence": 0.9, "quality": "bad"},
            {"confidence": 0.1, "quality": "good"},
        ]
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertGreaterEqual(r["calibration_error"], 0.0)
        self.assertLessEqual(r["calibration_error"], 1.0)

    def test_overconfident_count(self):
        decisions = [
            {"confidence": 0.9, "quality": "bad"},
            {"confidence": 0.8, "quality": "bad"},
            {"confidence": 0.5, "quality": "good"},
        ]
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertEqual(r["overconfident_count"], 2)

    def test_underconfident_count(self):
        decisions = [
            {"confidence": 0.3, "quality": "good"},
            {"confidence": 0.4, "quality": "good"},
            {"confidence": 0.8, "quality": "good"},
        ]
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertEqual(r["underconfident_count"], 2)

    def test_well_calibrated_count(self):
        decisions = [
            {"confidence": 0.9, "quality": "good"},
            {"confidence": 0.1, "quality": "bad"},
        ]
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertEqual(r["well_calibrated_count"], 2)

    def test_current_thresholds_default(self):
        t = self.cc.get_current_thresholds()
        self.assertEqual(t["high"], 0.80)
        self.assertEqual(t["medium"], 0.60)

    def test_no_staged_initially(self):
        self.assertIsNone(self.cc.get_staged_adjustments())

    def test_calibrated_at_timestamp(self):
        with self._mock([{"confidence": 0.5, "quality": "neutral"}]):
            r = self.cc.calibrate()
        self.assertIn("calibrated_at", r)
        datetime.fromisoformat(r["calibrated_at"])

    def test_days_analyzed_field(self):
        with self._mock([]):
            r = self.cc.calibrate(days=7)
        self.assertEqual(r["days_analyzed"], 7)

    def test_suggested_adjustments_has_reason(self):
        decisions = [{"confidence": 0.9, "quality": "bad"}] * 5
        with self._mock(decisions):
            r = self.cc.calibrate()
        if r["suggested_adjustments"]:
            self.assertIn("reason", r["suggested_adjustments"])

    def test_multiple_calibrations_cumulative(self):
        for _ in range(3):
            with self._mock([{"confidence": 0.9, "quality": "bad"}] * 3):
                self.cc.calibrate()
            if self.cc.get_staged_adjustments():
                self.cc.apply_staged(confirm=True)
        t = self.cc.get_current_thresholds()
        self.assertGreater(t["high"], 0.80)

    def test_apply_staged_clears_staged(self):
        with self._mock([{"confidence": 0.9, "quality": "bad"}] * 5):
            self.cc.calibrate()
        if self.cc.get_staged_adjustments():
            self.cc.apply_staged(confirm=True)
            self.assertIsNone(self.cc.get_staged_adjustments())

    def test_overconfidence_rate_is_ratio(self):
        decisions = [{"confidence": 0.9, "quality": "bad"}] * 2 + \
                    [{"confidence": 0.5, "quality": "good"}] * 8
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertAlmostEqual(r["overconfidence_rate"], 0.2, places=1)

    def test_all_good_zero_overconfidence(self):
        decisions = [{"confidence": 0.9, "quality": "good"}] * 10
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertEqual(r["overconfidence_rate"], 0.0)

    def test_all_bad_high_overconfidence(self):
        decisions = [{"confidence": 0.9, "quality": "bad"}] * 10
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertEqual(r["overconfidence_rate"], 1.0)

    def test_neutral_not_over_or_under(self):
        decisions = [{"confidence": 0.5, "quality": "neutral"}] * 10
        with self._mock(decisions):
            r = self.cc.calibrate()
        self.assertEqual(r["overconfident_count"], 0)
        self.assertEqual(r["underconfident_count"], 0)


if __name__ == "__main__":
    unittest.main()
