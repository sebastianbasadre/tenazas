#!/usr/bin/env python3
"""
Tests for Sprint 3 — Self-Improvement Pipeline.

Covers:
- error_mining: all 7 miners detect correct error types
- improvement_candidates: generates proposals with all required fields
- self_benchmark: gate rejects candidates that break metrics
- staging: directory doesn't affect runtime
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

# Setup paths — works both in container (/home/tenazas/) and locally
TEST_DIR = Path(__file__).parent
# In container: tests/ is at /home/tenazas/tests/, runtime at /home/tenazas/runtime/
# Locally: tests/ is at Z:/openclaw/tests/, runtime at Z:/openclaw/workspace/runtime/
_container_runtime = Path("/home/tenazas/runtime")
_local_runtime = TEST_DIR.parent / "workspace" / "runtime"
RUNTIME_DIR = _container_runtime if _container_runtime.exists() else _local_runtime
sys.path.insert(0, str(RUNTIME_DIR.parent))  # so "from runtime import X" works


class TestErrorMiningToolFailures(unittest.TestCase):
    """error_mining detects tool failures from logs."""

    def test_detects_router_fallback(self):
        from runtime import error_mining as em
        entry = {
            "ts": datetime.now().astimezone().isoformat(),
            "action": "route_v2",
            "fallback_used": True,
            "model_chosen": "qwen2.5:7b",
            "role": "fast",
        }
        with patch.object(em, "_load_jsonl", return_value=[entry]):
            errors = em.mine_tool_failures()
        fallbacks = [e for e in errors if e["subtype"] == "router_fallback"]
        self.assertTrue(len(fallbacks) > 0)
        self.assertEqual(fallbacks[0]["type"], "tool_failure")
        self.assertEqual(fallbacks[0]["severity"], "medium")

    def test_detects_cloud_fallback(self):
        from runtime import error_mining as em
        entry = {
            "ts": datetime.now().astimezone().isoformat(),
            "type": "cloud_fallback",
            "reason": "all backends offline",
        }
        with patch.object(em, "_load_jsonl", return_value=[entry]):
            errors = em.mine_tool_failures()
        cloud = [e for e in errors if e.get("subtype") == "cloud_fallback"]
        self.assertTrue(len(cloud) > 0)
        self.assertEqual(cloud[0]["severity"], "high")

    def test_empty_logs_no_errors(self):
        from runtime import error_mining as em
        with patch.object(em, "_load_jsonl", return_value=[]):
            errors = em.mine_tool_failures()
        self.assertEqual(errors, [])


class TestErrorMiningTimeouts(unittest.TestCase):

    def test_detects_timeout(self):
        from runtime import error_mining as em
        entry = {
            "ts": datetime.now().astimezone().isoformat(),
            "action": "query",
            "error": "timeout after 30s",
        }
        with patch.object(em, "_load_jsonl", return_value=[entry]):
            errors = em.mine_timeouts()
        self.assertTrue(len(errors) > 0)
        self.assertEqual(errors[0]["type"], "timeout")


class TestErrorMiningDubiousClassifications(unittest.TestCase):

    def test_detects_keyword_fallback(self):
        from runtime import error_mining as em
        entry = {
            "ts": datetime.now().astimezone().isoformat(),
            "signals": ["keyword:weather"],
            "_parsed_by": "keyword",
            "prompt_preview": "que onda el clima",
            "task_type": "fast",
        }
        with patch.object(em, "_load_jsonl", return_value=[entry]):
            errors = em.mine_dubious_classifications()
        self.assertTrue(len(errors) > 0)
        self.assertEqual(errors[0]["type"], "dubious_classification")
        self.assertEqual(errors[0]["severity"], "low")


class TestErrorMiningRepeatedIncidents(unittest.TestCase):

    def test_detects_3plus_occurrences(self):
        from runtime import error_mining as em
        ts = datetime.now().astimezone().isoformat()
        entries = [
            {"ts": ts, "issues": [{"problem": "container sonarr down"}]},
            {"ts": ts, "issues": [{"problem": "container sonarr down"}]},
            {"ts": ts, "issues": [{"problem": "container sonarr down"}]},
        ]
        with patch.object(em, "_load_jsonl", return_value=entries):
            with patch.object(em, "INCIDENTS_DIR", Path("/nonexistent")):
                errors = em.mine_repeated_incidents()
        repeated = [e for e in errors if e["type"] == "repeated_incident"]
        self.assertTrue(len(repeated) > 0)
        self.assertGreaterEqual(repeated[0]["count"], 3)

    def test_below_threshold_ignored(self):
        from runtime import error_mining as em
        ts = datetime.now().astimezone().isoformat()
        entries = [
            {"ts": ts, "issues": [{"problem": "something rare"}]},
            {"ts": ts, "issues": [{"problem": "something rare"}]},
        ]
        with patch.object(em, "_load_jsonl", return_value=entries):
            with patch.object(em, "INCIDENTS_DIR", Path("/nonexistent")):
                errors = em.mine_repeated_incidents()
        self.assertEqual(len(errors), 0)


class TestErrorMiningRejectedProposals(unittest.TestCase):

    def test_detects_rejected(self):
        from runtime import error_mining as em
        approvals = {
            "abc123": {"status": "rejected", "action_summary": "bad idea", "created_at": "2026-01-01"},
        }
        with patch.object(em, "_load_json", return_value=approvals):
            errors = em.mine_rejected_proposals()
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["type"], "rejected_proposal")

    def test_approved_not_flagged(self):
        from runtime import error_mining as em
        approvals = {
            "abc123": {"status": "approved", "action_summary": "good idea"},
        }
        with patch.object(em, "_load_json", return_value=approvals):
            errors = em.mine_rejected_proposals()
        self.assertEqual(len(errors), 0)


class TestErrorMiningMineAll(unittest.TestCase):

    def test_mine_all_returns_report(self):
        from runtime import error_mining as em
        with patch.object(em, "_load_jsonl", return_value=[]):
            with patch.object(em, "_load_json", return_value={}):
                with patch.object(em, "INCIDENTS_DIR", Path("/nonexistent")):
                    with patch.object(em, "OUTPUT_DIR", Path(tempfile.mkdtemp())):
                        report = em.mine_all()
        self.assertIn("date", report)
        self.assertIn("total_errors", report)
        self.assertIn("by_type", report)
        self.assertIn("errors", report)
        self.assertIsInstance(report["errors"], list)

    def test_errors_have_ids(self):
        from runtime import error_mining as em
        entry = {
            "ts": datetime.now().astimezone().isoformat(),
            "action": "query",
            "error": "timeout",
        }
        with patch.object(em, "_load_jsonl", return_value=[entry]):
            with patch.object(em, "_load_json", return_value={}):
                with patch.object(em, "INCIDENTS_DIR", Path("/nonexistent")):
                    with patch.object(em, "OUTPUT_DIR", Path(tempfile.mkdtemp())):
                        report = em.mine_all()
        for error in report["errors"]:
            self.assertIn("id", error)
            self.assertEqual(len(error["id"]), 12)


class TestImprovementCandidatesGeneration(unittest.TestCase):

    def test_required_fields(self):
        from runtime import improvement_candidates as ic
        errors = [
            {"type": "dubious_classification", "detail": "classified as chat via keyword",
             "severity": "low", "ts": "2026-01-01"},
            {"type": "dubious_classification", "detail": "classified as chat via keyword",
             "severity": "low", "ts": "2026-01-01"},
        ]
        proposals = ic._gen_prompt_tweaks(errors)
        for p in proposals:
            self.assertIn("type", p)
            self.assertIn("title", p)
            self.assertIn("description", p)
            self.assertIn("expected_gain", p)
            self.assertIn("risk", p)
            self.assertIn("evidence", p)
            self.assertIn(p["risk"], ("low", "medium", "high"))

    def test_generates_router_rule(self):
        from runtime import improvement_candidates as ic
        errors = [
            {"type": "tool_failure", "subtype": "router_fallback",
             "detail": "fallback triggered", "severity": "medium"},
            {"type": "tool_failure", "subtype": "router_fallback",
             "detail": "fallback triggered again", "severity": "medium"},
        ]
        proposals = ic._gen_router_rules(errors)
        self.assertTrue(len(proposals) > 0)
        self.assertEqual(proposals[0]["type"], "router_rule")

    def test_generates_runbook(self):
        from runtime import improvement_candidates as ic
        errors = [
            {"type": "repeated_incident", "detail": "sonarr down 5x",
             "count": 5, "severity": "medium"},
        ]
        proposals = ic._gen_runbooks(errors)
        self.assertTrue(len(proposals) > 0)
        self.assertEqual(proposals[0]["type"], "new_runbook")

    def test_generates_test(self):
        from runtime import improvement_candidates as ic
        errors = [
            {"type": "chain_failure", "detail": "chain error", "severity": "high"},
        ]
        proposals = ic._gen_tests(errors)
        self.assertTrue(len(proposals) > 0)
        self.assertEqual(proposals[0]["type"], "new_test")

    def test_generates_guardrail(self):
        from runtime import improvement_candidates as ic
        errors = [
            {"type": "false_alert", "detail": "auto-resolved in 30s",
             "severity": "low", "duration_seconds": 30},
            {"type": "false_alert", "detail": "auto-resolved in 60s",
             "severity": "low", "duration_seconds": 60},
        ]
        proposals = ic._gen_guardrail_policies(errors)
        self.assertTrue(len(proposals) > 0)
        self.assertEqual(proposals[0]["type"], "guardrail_policy")

    def test_status_transitions(self):
        from runtime import improvement_candidates as ic
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            candidates = [{"id": "test123", "type": "prompt_tweak",
                           "title": "test", "status": "proposed"}]
            json.dump(candidates, f)
            f.flush()
            with patch.object(ic, "CANDIDATES_FILE", Path(f.name)):
                # proposed → staged
                self.assertTrue(ic.update_status("test123", "staged"))
                # staged → approved
                self.assertTrue(ic.update_status("test123", "approved"))
                # Can't go back
                self.assertFalse(ic.update_status("test123", "proposed"))
            os.unlink(f.name)

    def test_invalid_transition_rejected(self):
        from runtime import improvement_candidates as ic
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
            candidates = [{"id": "test456", "type": "test", "title": "t", "status": "proposed"}]
            json.dump(candidates, f)
            f.flush()
            with patch.object(ic, "CANDIDATES_FILE", Path(f.name)):
                # proposed → approved is invalid (must go through staged)
                self.assertFalse(ic.update_status("test456", "approved"))
            os.unlink(f.name)


class TestSelfBenchmarkGate(unittest.TestCase):

    def test_gate_returns_structure(self):
        from runtime import self_benchmark as sb
        with patch.object(sb, "benchmark_router_accuracy",
                          return_value={"accuracy": 0.85, "total": 20, "correct": 17, "failures": []}):
            with patch.object(sb, "benchmark_structured_output",
                              return_value={"validity_rate": 1.0, "total": 10, "valid": 10}):
                with patch.object(sb, "benchmark_false_positive_rate",
                                  return_value={"false_positive_rate": 0.1, "total_alerts": 10}):
                    with patch.object(sb, "benchmark_latency_p95",
                                      return_value={"p95_ms": 100, "samples": 50}):
                        result = sb.run_gate("test_candidate")
        self.assertIn("passed", result)
        self.assertIn("checks", result)
        self.assertIn("failures", result)
        self.assertTrue(result["passed"])

    def test_gate_fails_low_accuracy(self):
        from runtime import self_benchmark as sb
        with patch.object(sb, "benchmark_router_accuracy",
                          return_value={"accuracy": 0.50, "total": 20, "correct": 10, "failures": []}):
            with patch.object(sb, "benchmark_structured_output",
                              return_value={"validity_rate": 1.0}):
                with patch.object(sb, "benchmark_false_positive_rate",
                                  return_value={"false_positive_rate": 0.1}):
                    with patch.object(sb, "benchmark_latency_p95",
                                      return_value={"p95_ms": 100}):
                        result = sb.run_gate()
        self.assertFalse(result["passed"])
        self.assertTrue(any("router_accuracy" in f for f in result["failures"]))

    def test_gate_fails_high_latency(self):
        from runtime import self_benchmark as sb
        with patch.object(sb, "benchmark_router_accuracy",
                          return_value={"accuracy": 0.90}):
            with patch.object(sb, "benchmark_structured_output",
                              return_value={"validity_rate": 1.0}):
                with patch.object(sb, "benchmark_false_positive_rate",
                                  return_value={"false_positive_rate": 0.1}):
                    with patch.object(sb, "benchmark_latency_p95",
                                      return_value={"p95_ms": 10000}):
                        result = sb.run_gate()
        self.assertFalse(result["passed"])
        self.assertTrue(any("latency" in f for f in result["failures"]))

    def test_gate_fails_high_false_positives(self):
        from runtime import self_benchmark as sb
        with patch.object(sb, "benchmark_router_accuracy",
                          return_value={"accuracy": 0.90}):
            with patch.object(sb, "benchmark_structured_output",
                              return_value={"validity_rate": 1.0}):
                with patch.object(sb, "benchmark_false_positive_rate",
                                  return_value={"false_positive_rate": 0.80}):
                    with patch.object(sb, "benchmark_latency_p95",
                                      return_value={"p95_ms": 100}):
                        result = sb.run_gate()
        self.assertFalse(result["passed"])


class TestStagingIsolation(unittest.TestCase):
    """Verify staging/ doesn't affect runtime."""

    def _staging_dir(self):
        """Find staging dir — works both in container and locally."""
        candidates = [
            RUNTIME_DIR.parent / "staging",
            Path("/home/tenazas/.openclaw/workspace/staging"),
        ]
        for p in candidates:
            if p.exists():
                return p
        return candidates[0]

    def test_staging_readme_exists(self):
        staging = self._staging_dir()
        if not staging.exists():
            self.skipTest("staging/ not mounted in container")
        self.assertTrue((staging / "README.md").exists())

    def test_staging_dirs_exist(self):
        staging = self._staging_dir()
        if not staging.exists():
            self.skipTest("staging/ not mounted in container")
        for subdir in ["prompts", "router_rules", "runbooks", "tests", "datasets"]:
            self.assertTrue((staging / subdir).is_dir(),
                            f"staging/{subdir} should exist")

    def test_staging_has_no_py_files(self):
        """No .py files in staging that could be imported by runtime."""
        staging = self._staging_dir()
        if not staging.exists():
            self.skipTest("staging/ not mounted in container")
        py_files = list(staging.rglob("*.py"))
        self.assertEqual(py_files, [],
                         f"staging/ should not contain .py files: {py_files}")

    def test_runtime_does_not_import_staging(self):
        """Verify no runtime module imports from staging/."""
        runtime = RUNTIME_DIR
        for py_file in runtime.glob("*.py"):
            content = py_file.read_text()
            self.assertNotIn("from staging", content,
                             f"{py_file.name} imports from staging/")
            self.assertNotIn("import staging", content,
                             f"{py_file.name} imports staging")


if __name__ == "__main__":
    unittest.main()
