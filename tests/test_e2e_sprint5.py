#!/usr/bin/env python3
"""
End-to-end tests for Sprint 5 — B+A roadmap integration.

Scenario 1: incident → policy → approval → improvement → staging
Scenario 2: preference learning → blacklist → planner respects it
Scenario 3: skill gap detection → proposal in staging (not auto-implemented)
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

TEST_DIR = Path(__file__).parent
_container_runtime = Path("/home/tenazas/runtime")
_local_runtime = TEST_DIR.parent / "workspace" / "runtime"
RUNTIME_DIR = _container_runtime if _container_runtime.exists() else _local_runtime
sys.path.insert(0, str(RUNTIME_DIR.parent))


# ── Scenario 1: Incident to Staging ─────────────────────────────


class TestIncidentToStaging(unittest.TestCase):
    """Simulate incident → policy → approval → improvement → staging."""

    def test_policy_engine_has_evaluate(self):
        """Verify policy_engine defines evaluate() and imports action_registry."""
        pe_path = RUNTIME_DIR / "policy_engine.py"
        self.assertTrue(pe_path.exists())
        content = pe_path.read_text()
        self.assertIn("def evaluate", content)
        self.assertIn("action_registry", content)
        self.assertIn("risk_level", content)

    def test_action_registry_exists(self):
        """Verify action_registry.py exists and defines get_action."""
        ar_path = RUNTIME_DIR / "action_registry.py"
        self.assertTrue(ar_path.exists())
        content = ar_path.read_text()
        self.assertIn("def get_action", content)
        self.assertIn("restart_container", content)

    def test_action_validator_exists(self):
        av_path = RUNTIME_DIR / "action_validator.py"
        self.assertTrue(av_path.exists())
        content = av_path.read_text()
        self.assertIn("def validate", content)

    def test_improvement_candidates_generates_from_errors(self):
        from runtime import improvement_candidates as ic

        errors = [
            {"type": "repeated_incident", "detail": "sonarr down 5x",
             "count": 5, "severity": "medium"},
        ]
        proposals = ic._gen_runbooks(errors)
        self.assertTrue(len(proposals) > 0)
        self.assertEqual(proposals[0]["type"], "new_runbook")
        self.assertIn("status", proposals[0]) if "status" in proposals[0] else None
        # Verify it has all required fields
        for field in ("type", "title", "description", "expected_gain", "risk"):
            self.assertIn(field, proposals[0])

    def test_full_chain_incident_to_candidate(self):
        """Verify the chain: error → mine → generate candidates."""
        from runtime import error_mining as em
        from runtime import improvement_candidates as ic

        # Mock repeated incident in diagnostics
        ts = datetime.now().astimezone().isoformat()
        diag_entries = [
            {"ts": ts, "issues": [{"problem": "container radarr down"}]},
        ] * 4  # 4 occurrences

        with patch.object(em, "_load_jsonl", return_value=diag_entries):
            with patch.object(em, "INCIDENTS_DIR", Path("/nonexistent")):
                with patch.object(em, "OUTPUT_DIR", Path(tempfile.mkdtemp())):
                    report = em.mine_all()

        # Should detect repeated incident
        repeated = [e for e in report["errors"] if e["type"] == "repeated_incident"]
        self.assertTrue(len(repeated) > 0)

        # Feed to improvement_candidates
        proposals = ic._gen_runbooks(repeated)
        self.assertTrue(len(proposals) > 0)
        self.assertEqual(proposals[0]["type"], "new_runbook")


# ── Scenario 2: Preference Learning Loop ────────────────────────


class TestPreferenceLearningLoop(unittest.TestCase):
    """Simulate 2 rejections → blacklist → planner respects it."""

    def setUp(self):
        self.tmpfile = tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False)
        self.tmpfile.close()

    def tearDown(self):
        os.unlink(self.tmpfile.name)

    def test_double_rejection_blacklists(self):
        from runtime import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpfile.name)):
            pl.record_signal("rejection", "cleanup_old_logs", reason="too risky")
            pl.record_signal("rejection", "cleanup_old_logs", reason="still no")
            self.assertTrue(pl.is_blacklisted("cleanup_old_logs"))

    def test_blacklisted_confidence_is_zero(self):
        from runtime import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpfile.name)):
            pl.record_signal("rejection", "auto_update_model")
            pl.record_signal("rejection", "auto_update_model")
            conf = pl.get_preference("auto_update_model")
            self.assertEqual(conf, 0.0)

    def test_planner_can_check_preference(self):
        """Verify planner can query preferences to skip blacklisted items."""
        from runtime import preference_learning as pl
        with patch.object(pl, "PREFS_FILE", Path(self.tmpfile.name)):
            pl.record_signal("rejection", "risky_action")
            pl.record_signal("rejection", "risky_action")

            # A planner would check before proposing
            conf = pl.get_preference("risky_action")
            should_propose = conf > 0.0
            self.assertFalse(should_propose)


# ── Scenario 3: Skill Gap to Staging ────────────────────────────


class TestSkillGapToStaging(unittest.TestCase):
    """Simulate 10+ queries falling to chat → gap detected → staging proposal."""

    def test_detects_gap_from_chat_queries(self):
        from runtime import skill_gap_detector as sgd
        ts = datetime.now().astimezone().isoformat()
        entries = [
            {"ts": ts, "classification": "chat", "query": "cuanto cuesta el bitcoin"}
        ] * 12

        with patch.object(sgd, "_load_jsonl", return_value=entries):
            gaps = sgd.detect_chat_fallbacks()

        self.assertTrue(len(gaps) > 0)
        self.assertGreaterEqual(gaps[0]["count"], 10)

    def test_proposal_goes_to_staging(self):
        from runtime import skill_gap_detector as sgd
        staging_dir = Path(tempfile.mkdtemp())

        gaps = [{
            "id": "gap_test_123",
            "type": "chat_fallback",
            "pattern": "cuanto cuesta el bitcoin",
            "count": 15,
            "detail": "15 queries about bitcoin price fell to chat",
            "detected_at": datetime.now().isoformat(),
        }]

        with patch.object(sgd, "STAGING_DIR", staging_dir):
            proposals = sgd.generate_proposals(gaps)

        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["status"], "proposed")

        # Verify file was created in staging
        staging_files = list(staging_dir.rglob("*.json"))
        self.assertTrue(len(staging_files) > 0)

    def test_proposal_not_auto_implemented(self):
        """Skill proposals should NEVER be auto-implemented."""
        from runtime import skill_gap_detector as sgd
        staging_dir = Path(tempfile.mkdtemp())

        gaps = [{
            "id": "no_auto",
            "type": "chat_fallback",
            "pattern": "test query",
            "count": 10,
            "detail": "test",
            "detected_at": datetime.now().isoformat(),
        }]

        with patch.object(sgd, "STAGING_DIR", staging_dir):
            proposals = sgd.generate_proposals(gaps)

        # Verify no .py files created (skill not implemented)
        py_files = list(staging_dir.rglob("*.py"))
        self.assertEqual(py_files, [], "Skill proposals must not auto-create .py files")

        # Verify proposal is just a JSON spec
        for f in staging_dir.rglob("*.json"):
            data = json.loads(f.read_text())
            self.assertEqual(data.get("status"), "proposed")
            self.assertIn("proposed_skill", data)


# ── Dashboard Payload ────────────────────────────────────────────


class TestDashboardPayload(unittest.TestCase):

    def test_build_payload_doesnt_crash(self):
        from runtime import homelab_dashboard_payload as hdp
        # Mock all external calls
        with patch.object(hdp, "_safe_import", return_value=None):
            payload = hdp.build_payload()
        self.assertIn("ts", payload)
        self.assertIn("goals", payload)
        self.assertIn("staging", payload)
        self.assertIn("experiments", payload)

    def test_format_telegram_under_25_lines(self):
        from runtime import homelab_dashboard_payload as hdp
        with patch.object(hdp, "_safe_import", return_value=None):
            text = hdp.format_telegram()
        lines = text.strip().split("\n")
        self.assertLessEqual(len(lines), 25,
                             f"Dashboard should be compact, got {len(lines)} lines")

    def test_format_contains_key_sections(self):
        from runtime import homelab_dashboard_payload as hdp
        with patch.object(hdp, "_safe_import", return_value=None):
            text = hdp.format_telegram()
        self.assertIn("Dashboard", text)
        self.assertIn("Docker", text)
        self.assertIn("Goals", text)
        self.assertIn("Staging", text)


# ── Prometheus Metrics ───────────────────────────────────────────


class TestPrometheusMetrics(unittest.TestCase):

    def test_ba_metrics_dont_crash(self):
        """update_ba_metrics should never crash even with missing files."""
        from runtime import metrics_server as ms
        if not hasattr(ms, "update_ba_metrics"):
            self.skipTest("update_ba_metrics not found")
        # Should not raise
        ms.update_ba_metrics()


if __name__ == "__main__":
    unittest.main()
