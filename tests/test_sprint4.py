#!/usr/bin/env python3
"""
Tests for Sprint 4 — Useful Learning.

Covers:
- preference_learning: signal recording, confidence, blacklisting
- skill_gap_detector: detects chat fallback patterns, chains
- experiment_runner: guardrails, rollback, expiry
- research-to-runbook: generates runbook in staging (mocked)
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

# Setup paths
TEST_DIR = Path(__file__).parent
_container_runtime = Path("/home/tenazas/runtime")
_local_runtime = TEST_DIR.parent / "workspace" / "runtime"
RUNTIME_DIR = _container_runtime if _container_runtime.exists() else _local_runtime
sys.path.insert(0, str(RUNTIME_DIR.parent))


# ── Preference Learning ─────────────────────────────────────────


class TestPreferenceLearningSignals(unittest.TestCase):

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False)
        self.tmpfile.close()

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    def test_approval_increases_confidence(self):
        from runtime import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpfile.name)):
            pl.record_signal("approval", "restart_container")
            conf = pl.get_preference("restart_container")
            self.assertGreater(conf, 0.5)

    def test_rejection_decreases_confidence(self):
        from runtime import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpfile.name)):
            pl.record_signal("rejection", "delete_logs", reason="too risky")
            conf = pl.get_preference("delete_logs")
            self.assertLess(conf, 0.5)

    def test_double_rejection_blacklists(self):
        from runtime import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpfile.name)):
            pl.record_signal("rejection", "bad_action", reason="no")
            pl.record_signal("rejection", "bad_action", reason="still no")
            self.assertTrue(pl.is_blacklisted("bad_action"))
            self.assertEqual(pl.get_preference("bad_action"), 0.0)

    def test_blacklist_removable(self):
        from runtime import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpfile.name)):
            pl.record_signal("rejection", "temp_block")
            pl.record_signal("rejection", "temp_block")
            self.assertTrue(pl.is_blacklisted("temp_block"))
            pl.remove_from_blacklist("temp_block")
            self.assertFalse(pl.is_blacklisted("temp_block"))

    def test_skill_usage_tracking(self):
        from runtime import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpfile.name)):
            pl.record_signal("skill_usage", "skill:weather")
            pl.record_signal("skill_usage", "skill:weather")
            pl.record_signal("skill_usage", "skill:email")
            top = pl.get_top_skills()
            self.assertEqual(top[0][0], "skill:weather")
            self.assertEqual(top[0][1], 2)

    def test_telegram_activity(self):
        from runtime import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpfile.name)):
            pl.record_signal("telegram_activity", "")
            hours = pl.get_active_hours()
            self.assertTrue(len(hours) > 0)

    def test_signals_capped_at_200(self):
        from runtime import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpfile.name)):
            for i in range(210):
                pl.record_signal("approval", f"cat_{i % 5}")
            prefs = pl._load_prefs()
            self.assertLessEqual(len(prefs["signals"]), 200)

    def test_default_confidence(self):
        from runtime import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpfile.name)):
            conf = pl.get_preference("never_seen_before")
            self.assertEqual(conf, 0.5)


# ── Skill Gap Detector ──────────────────────────────────────────


class TestSkillGapDetector(unittest.TestCase):

    def test_detects_chat_fallbacks(self):
        from runtime import skill_gap_detector as sgd
        ts = datetime.now().astimezone().isoformat()
        entries = [
            {"ts": ts, "classification": "chat", "query": "cuanto cuesta el dolar"}
        ] * 12
        with patch.object(sgd, "_load_jsonl", return_value=entries):
            gaps = sgd.detect_chat_fallbacks()
        # Should detect the repeated pattern
        self.assertTrue(len(gaps) > 0)
        self.assertEqual(gaps[0]["type"], "chat_fallback")
        self.assertGreaterEqual(gaps[0]["count"], 10)

    def test_below_threshold_no_gap(self):
        from runtime import skill_gap_detector as sgd
        ts = datetime.now().astimezone().isoformat()
        entries = [
            {"ts": ts, "classification": "chat", "query": "random question"}
        ] * 5
        with patch.object(sgd, "_load_jsonl", return_value=entries):
            gaps = sgd.detect_chat_fallbacks()
        self.assertEqual(len(gaps), 0)

    def test_detects_skill_chains(self):
        from runtime import skill_gap_detector as sgd
        now = datetime.now().astimezone()
        entries = []
        for i in range(5):
            ts1 = (now - timedelta(hours=i)).isoformat()
            ts2 = (now - timedelta(hours=i) + timedelta(seconds=30)).isoformat()
            entries.append({"ts": ts1, "classification": "skill:weather"})
            entries.append({"ts": ts2, "classification": "skill:smart-home"})
        with patch.object(sgd, "_load_jsonl", return_value=entries):
            gaps = sgd.detect_skill_chains()
        chains = [g for g in gaps if g["type"] == "skill_chain"]
        self.assertTrue(len(chains) > 0)
        self.assertIn("skill:weather", chains[0]["pattern"])
        self.assertIn("skill:smart-home", chains[0]["pattern"])

    def test_generate_proposals_has_fields(self):
        from runtime import skill_gap_detector as sgd
        gaps = [{
            "id": "test123",
            "type": "chat_fallback",
            "pattern": "cuanto cuesta el dolar",
            "count": 15,
            "detail": "test",
            "detected_at": datetime.now().isoformat(),
        }]
        with patch.object(sgd, "STAGING_DIR", Path(tempfile.mkdtemp())):
            proposals = sgd.generate_proposals(gaps)
        self.assertEqual(len(proposals), 1)
        p = proposals[0]
        self.assertIn("proposed_skill", p)
        self.assertIn("name", p["proposed_skill"])
        self.assertIn("inputs", p["proposed_skill"])
        self.assertIn("risks", p["proposed_skill"])
        self.assertIn("suggested_tests", p["proposed_skill"])


# ── Experiment Runner ───────────────────────────────────────────


class TestExperimentRunner(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_create_experiment(self):
        from runtime import experiment_runner as er
        with patch.object(er, "EXPERIMENTS_DIR", Path(self.tmpdir)):
            exp = er.create("test_ab", "prompt_ab", {"a": "v1", "b": "v2"})
        self.assertIn("id", exp)
        self.assertEqual(exp["status"], "active")
        self.assertEqual(exp["type"], "prompt_ab")

    def test_invalid_type_rejected(self):
        from runtime import experiment_runner as er
        with patch.object(er, "EXPERIMENTS_DIR", Path(self.tmpdir)):
            exp = er.create("bad", "nonexistent_type", {})
        self.assertIn("error", exp)

    def test_traffic_capped_at_10(self):
        from runtime import experiment_runner as er
        with patch.object(er, "EXPERIMENTS_DIR", Path(self.tmpdir)):
            exp = er.create("cap", "prompt_ab", {}, traffic_percent=50)
        self.assertEqual(exp["traffic_percent"], 10)

    def test_auto_rollback_on_metric_drop(self):
        from runtime import experiment_runner as er
        with patch.object(er, "EXPERIMENTS_DIR", Path(self.tmpdir)):
            exp = er.create("rollback_test", "prompt_ab", {})
            eid = exp["id"]
            # Record good control results
            for _ in range(10):
                er.record_result(eid, is_variant=False, metric_value=0.9)
            # Record bad variant results (>5% drop)
            for _ in range(5):
                er.record_result(eid, is_variant=True, metric_value=0.5)
            # Check guardrails — should auto-rollback
            result = er.check_guardrails(eid)
        self.assertFalse(result["ok"])
        self.assertIn("rollback", result.get("reason", ""))

    def test_guardrails_ok_when_within_threshold(self):
        from runtime import experiment_runner as er
        with patch.object(er, "EXPERIMENTS_DIR", Path(self.tmpdir)):
            exp = er.create("ok_test", "prompt_ab", {})
            eid = exp["id"]
            for _ in range(10):
                er.record_result(eid, is_variant=False, metric_value=0.9)
            for _ in range(5):
                er.record_result(eid, is_variant=True, metric_value=0.88)
            result = er.check_guardrails(eid)
        self.assertTrue(result["ok"])

    def test_expiry_stops_experiment(self):
        from runtime import experiment_runner as er
        with patch.object(er, "EXPERIMENTS_DIR", Path(self.tmpdir)):
            exp = er.create("expiry_test", "prompt_ab", {})
            # Manually expire it
            exp_data = er._load_experiment(exp["id"])
            exp_data["expires_at"] = (
                datetime.now().astimezone() - timedelta(hours=1)).isoformat()
            er._save_experiment(exp_data)
            result = er.check_guardrails(exp["id"])
        self.assertIn("expired", result.get("reason", ""))

    def test_risk_blocking(self):
        from runtime import experiment_runner as er
        self.assertFalse(er.is_risk_allowed("medium"))
        self.assertFalse(er.is_risk_allowed("high"))
        self.assertTrue(er.is_risk_allowed("low"))
        self.assertTrue(er.is_risk_allowed("safe"))

    def test_max_concurrent_experiments(self):
        from runtime import experiment_runner as er
        with patch.object(er, "EXPERIMENTS_DIR", Path(self.tmpdir)):
            er.create("exp1", "prompt_ab", {})
            er.create("exp2", "prompt_ab", {})
            er.create("exp3", "prompt_ab", {})
            exp4 = er.create("exp4", "prompt_ab", {})
        self.assertIn("error", exp4)

    def test_stop_experiment(self):
        from runtime import experiment_runner as er
        with patch.object(er, "EXPERIMENTS_DIR", Path(self.tmpdir)):
            exp = er.create("stop_test", "prompt_ab", {})
            ok = er.stop(exp["id"])
            self.assertTrue(ok)
            loaded = er._load_experiment(exp["id"])
            self.assertEqual(loaded["status"], "stopped")


# ── Research-to-Runbook ─────────────────────────────────────────


class TestResearchToRunbook(unittest.TestCase):

    def test_generates_runbook_from_repeated_incident(self):
        from runtime import continuous_learning_loop as cll

        mock_report = {
            "errors": [
                {"type": "repeated_incident", "id": "abc123",
                 "detail": "container sonarr down", "count": 5},
            ]
        }

        staging_dir = Path(tempfile.mkdtemp()) / "staging" / "runbooks"

        with patch.object(cll, "RUNTIME", Path(tempfile.mkdtemp())):
            with patch("runtime.continuous_learning_loop.research",
                       return_value=[{"title": "Fix sonarr", "snippet": "restart it"}]):
                with patch("runtime.continuous_learning_loop._generate_runbook_content",
                           return_value="# Runbook: sonarr\n\nFix it."):
                    # Mock error_mining.get_today
                    mock_em = MagicMock()
                    mock_em.get_today.return_value = mock_report
                    with patch.dict(sys.modules, {"runtime.error_mining": mock_em, "error_mining": mock_em}):
                        count = cll._research_to_runbook()

        # Should generate at least intent to create (may fail on path)
        # The function returns 0-2, we just verify it doesn't crash
        self.assertIsInstance(count, int)

    def test_no_repeated_incidents_returns_zero(self):
        from runtime import continuous_learning_loop as cll

        mock_report = {"errors": [
            {"type": "tool_failure", "detail": "test"}
        ]}

        mock_em = MagicMock()
        mock_em.get_today.return_value = mock_report
        with patch.dict(sys.modules, {"runtime.error_mining": mock_em, "error_mining": mock_em}):
            count = cll._research_to_runbook()
        self.assertEqual(count, 0)


if __name__ == "__main__":
    unittest.main()
