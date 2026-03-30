#!/usr/bin/env python3
"""
Tests for self_dev_engine.py — Autonomous improvement proposals.

Corre: python3 tests/test_self_dev_engine.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/tenazas")
from runtime import self_dev_engine as sde


class _TempFilesMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_proposals = sde.PROPOSALS_FILE
        self._orig_log = sde.TELEMETRY_LOG
        sde.PROPOSALS_FILE = self.tmpdir / "dev_proposals.json"
        sde.TELEMETRY_LOG = self.tmpdir / "logs" / "self_dev.log"

    def tearDown(self):
        sde.PROPOSALS_FILE = self._orig_proposals
        sde.TELEMETRY_LOG = self._orig_log


def _patch_debugger(scan_result):
    """Patch self_debugger import inside _analyze_debug_reports."""
    mock = MagicMock()
    mock.scan_all_logs.return_value = scan_result
    return patch.dict("sys.modules", {"runtime.self_debugger": mock, "runtime": MagicMock(self_debugger=mock)})


def _patch_metrics(stats):
    mock_mod = MagicMock()
    mock_mod.summary.return_value = stats
    return patch.dict("sys.modules", {"runtime.metrics_engine": mock_mod, "runtime": MagicMock(metrics_engine=mock_mod)})


def _patch_idle(suggestions):
    mock_mod = MagicMock()
    mock_mod.get_suggestions.return_value = suggestions
    return patch.dict("sys.modules", {"runtime.idle_learning_engine": mock_mod, "runtime": MagicMock(idle_learning_engine=mock_mod)})


class TestAnalyzeDebugReports(_TempFilesMixin, unittest.TestCase):

    def test_high_error_rate_generates_proposal(self):
        scan = {"reports": {"runtime.log": {
            "errors": [{"category": "timeout"}],
            "summary": {"error_rate": 0.5, "total_errors": 10, "categories": {"timeout": 10}},
        }}}
        with _patch_debugger(scan):
            proposals = sde._analyze_debug_reports()
        has_refactor = any("Refactorizar" in p["change_description"] for p in proposals)
        self.assertTrue(has_refactor)

    def test_low_error_rate_no_refactor_proposal(self):
        scan = {"reports": {"runtime.log": {
            "errors": [],
            "summary": {"error_rate": 0.1, "total_errors": 1, "categories": {}},
        }}}
        with _patch_debugger(scan):
            proposals = sde._analyze_debug_reports()
        refactor = [p for p in proposals if "Refactorizar" in p.get("change_description", "")]
        self.assertEqual(len(refactor), 0)

    def test_timeout_threshold(self):
        scan = {"reports": {"executor.log": {
            "errors": [],
            "summary": {"error_rate": 0.2, "total_errors": 5, "categories": {"timeout": 5}},
        }}}
        with _patch_debugger(scan):
            proposals = sde._analyze_debug_reports()
        has_timeout = any("timeout" in p["change_description"].lower() for p in proposals)
        self.assertTrue(has_timeout)

    def test_module_error(self):
        scan = {"reports": {"orch.log": {
            "errors": [],
            "summary": {"error_rate": 0.1, "total_errors": 1, "categories": {"module_error": 1}},
        }}}
        with _patch_debugger(scan):
            proposals = sde._analyze_debug_reports()
        has_dep = any("dependencias" in p["change_description"].lower() for p in proposals)
        self.assertTrue(has_dep)

    def test_import_error_returns_empty(self):
        # Simulate import failure by patching to raise
        with patch.dict("sys.modules", {"runtime.self_debugger": None}):
            proposals = sde._analyze_debug_reports()
        self.assertEqual(proposals, [])

    def test_all_require_approval(self):
        scan = {"reports": {"test.log": {
            "errors": [{"category": "timeout"}],
            "summary": {"error_rate": 0.5, "total_errors": 10, "categories": {"timeout": 10}},
        }}}
        with _patch_debugger(scan):
            proposals = sde._analyze_debug_reports()
        for p in proposals:
            self.assertTrue(p["requires_approval"])

    def test_empty_reports(self):
        with _patch_debugger({"reports": {}}):
            proposals = sde._analyze_debug_reports()
        self.assertEqual(proposals, [])


class TestAnalyzeMetrics(_TempFilesMixin, unittest.TestCase):

    def test_low_confidence(self):
        with _patch_metrics({"avg_confidence": 0.4}):
            proposals = sde._analyze_metrics()
        has_conf = any("confianza" in p["change_description"].lower() for p in proposals)
        self.assertTrue(has_conf)

    def test_normal_confidence(self):
        with _patch_metrics({"avg_confidence": 0.8}):
            proposals = sde._analyze_metrics()
        conf = [p for p in proposals if "confianza" in p.get("change_description", "").lower()]
        self.assertEqual(len(conf), 0)

    def test_high_escalation(self):
        with _patch_metrics({"escalation_rate": 0.5}):
            proposals = sde._analyze_metrics()
        has_esc = any("escalacion" in p["change_description"].lower() for p in proposals)
        self.assertTrue(has_esc)

    def test_high_latency(self):
        with _patch_metrics({"latency": {"p95": 8000}}):
            proposals = sde._analyze_metrics()
        has_lat = any("latencia" in p["change_description"].lower() for p in proposals)
        self.assertTrue(has_lat)

    def test_critical_diagnostics(self):
        with _patch_metrics({"diagnostics": {"critical_total": 15}}):
            proposals = sde._analyze_metrics()
        has_crit = any("criticos" in p["change_description"].lower() for p in proposals)
        self.assertTrue(has_crit)

    def test_import_error(self):
        with patch.dict("sys.modules", {"runtime.metrics_engine": None}):
            proposals = sde._analyze_metrics()
        self.assertEqual(proposals, [])


class TestAnalyzeIdleLearnings(_TempFilesMixin, unittest.TestCase):

    def test_high_priority(self):
        with _patch_idle([{"priority": "high", "description": "optimize X", "recommendation": "do Y"}]):
            proposals = sde._analyze_idle_learnings()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["risk"], "medium")

    def test_medium_priority(self):
        with _patch_idle([{"priority": "medium", "description": "improve Z", "recommendation": "try W"}]):
            proposals = sde._analyze_idle_learnings()
        self.assertEqual(len(proposals), 1)
        self.assertEqual(proposals[0]["risk"], "low")

    def test_low_priority_skipped(self):
        with _patch_idle([{"priority": "low", "description": "minor thing"}]):
            proposals = sde._analyze_idle_learnings()
        self.assertEqual(len(proposals), 0)

    def test_import_error(self):
        with patch.dict("sys.modules", {"runtime.idle_learning_engine": None}):
            proposals = sde._analyze_idle_learnings()
        self.assertEqual(proposals, [])


class TestGenerateProposals(_TempFilesMixin, unittest.TestCase):

    @patch("runtime.self_dev_engine._analyze_idle_learnings", return_value=[])
    @patch("runtime.self_dev_engine._analyze_metrics", return_value=[])
    @patch("runtime.self_dev_engine._analyze_debug_reports", return_value=[
        {"module": "runtime", "change_description": "fix timeout handling",
         "expected_benefit": "less errors", "risk": "low", "requires_approval": True, "source": "debug"},
    ])
    def test_generates_and_saves(self, *mocks):
        proposals = sde.generate_proposals()
        self.assertEqual(len(proposals), 1)
        self.assertTrue(sde.PROPOSALS_FILE.exists())

    @patch("runtime.self_dev_engine._analyze_idle_learnings", return_value=[])
    @patch("runtime.self_dev_engine._analyze_metrics", return_value=[])
    @patch("runtime.self_dev_engine._analyze_debug_reports", return_value=[
        {"module": "A", "change_description": "refactorizar manejo de errores en runtime module A for better resilience and stability", "risk": "low", "requires_approval": True, "source": "x"},
        {"module": "A", "change_description": "refactorizar manejo de errores en runtime module A with improved retry logic and backoff", "risk": "low", "requires_approval": True, "source": "x"},
    ])
    def test_deduplicates(self, *mocks):
        # Both share module + first 50 chars of description
        proposals = sde.generate_proposals()
        self.assertEqual(len(proposals), 1)

    @patch("runtime.self_dev_engine._analyze_idle_learnings", return_value=[])
    @patch("runtime.self_dev_engine._analyze_metrics", return_value=[])
    @patch("runtime.self_dev_engine._analyze_debug_reports", return_value=[])
    def test_no_proposals(self, *mocks):
        proposals = sde.generate_proposals()
        self.assertEqual(proposals, [])

    @patch("runtime.self_dev_engine._analyze_idle_learnings", return_value=[])
    @patch("runtime.self_dev_engine._analyze_metrics", return_value=[])
    @patch("runtime.self_dev_engine._analyze_debug_reports", return_value=[
        {"module": "m", "change_description": f"unique-fix-number-{i}-for-testing", "risk": "low", "requires_approval": True, "source": "x"}
        for i in range(50)
    ])
    def test_max_proposals_cap(self, *mocks):
        proposals = sde.generate_proposals()
        self.assertLessEqual(len(proposals), sde.MAX_PROPOSALS)

    @patch("runtime.self_dev_engine._analyze_idle_learnings", return_value=[])
    @patch("runtime.self_dev_engine._analyze_metrics", return_value=[])
    @patch("runtime.self_dev_engine._analyze_debug_reports", return_value=[
        {"module": "X", "change_description": "new proposal", "risk": "low", "requires_approval": True, "source": "x"},
    ])
    def test_preserves_history(self, *mocks):
        sde._save_json(sde.PROPOSALS_FILE, {"proposals": [
            {"module": "Y", "change_description": "old", "status": "approved"},
        ]})
        sde.generate_proposals()
        all_p = sde.get_all_proposals()
        statuses = [p["status"] for p in all_p]
        self.assertIn("approved", statuses)
        self.assertIn("pending", statuses)

    @patch("runtime.self_dev_engine._analyze_idle_learnings", return_value=[])
    @patch("runtime.self_dev_engine._analyze_metrics", return_value=[])
    @patch("runtime.self_dev_engine._analyze_debug_reports", return_value=[
        {"module": "Z", "change_description": "test", "risk": "low", "requires_approval": True, "source": "x"},
    ])
    def test_all_pending_have_timestamp(self, *mocks):
        proposals = sde.generate_proposals()
        for p in proposals:
            self.assertIn("proposed_at", p)
            self.assertEqual(p["status"], "pending")


class TestGetProposals(_TempFilesMixin, unittest.TestCase):

    def test_pending_empty(self):
        self.assertEqual(sde.get_pending_proposals(), [])

    def test_pending_filters(self):
        sde._save_json(sde.PROPOSALS_FILE, {"proposals": [
            {"status": "pending", "module": "a"},
            {"status": "approved", "module": "b"},
            {"status": "pending", "module": "c"},
        ]})
        self.assertEqual(len(sde.get_pending_proposals()), 2)

    def test_get_all(self):
        sde._save_json(sde.PROPOSALS_FILE, {"proposals": [
            {"status": "pending"}, {"status": "approved"}, {"status": "rejected"},
        ]})
        self.assertEqual(len(sde.get_all_proposals()), 3)

    def test_missing_file(self):
        self.assertEqual(sde.get_all_proposals(), [])


class TestSafetyInvariant(_TempFilesMixin, unittest.TestCase):

    @patch("runtime.self_dev_engine._analyze_idle_learnings", return_value=[
        {"module": "r", "change_description": "x", "risk": "low", "requires_approval": True, "source": "idle"},
    ])
    @patch("runtime.self_dev_engine._analyze_metrics", return_value=[
        {"module": "m", "change_description": "y", "risk": "medium", "requires_approval": True, "source": "metrics"},
    ])
    @patch("runtime.self_dev_engine._analyze_debug_reports", return_value=[
        {"module": "d", "change_description": "z", "risk": "low", "requires_approval": True, "source": "debug"},
    ])
    def test_all_proposals_require_approval(self, *mocks):
        proposals = sde.generate_proposals()
        for p in proposals:
            self.assertTrue(p["requires_approval"])
            self.assertEqual(p["status"], "pending")


if __name__ == "__main__":
    unittest.main()
