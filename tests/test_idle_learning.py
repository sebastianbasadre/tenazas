#!/usr/bin/env python3
"""
Tests for idle_learning_engine.py — Idle-time optimization analysis.

Covers:
- _analyze_metrics: confidence low, escalation high, latency outliers, model imbalance
- _analyze_log_patterns: recurring failures, recurring critical issues
- _analyze_confidence_trends: declining trend, stable trend, insufficient data
- analyze_idle: end-to-end, dedup, max cap, sort by priority, saves to file
- get_suggestions: empty, existing data

Corre: python3 tests/test_idle_learning.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "/home/tenazas")
from runtime import idle_learning_engine as ile


class _TempFilesMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig = {
            "metrics": ile.METRICS_DIR,
            "logs": ile.LOGS_DIR,
            "learnings": ile.LEARNINGS_FILE,
            "telem": ile.TELEMETRY_LOG,
        }
        ile.METRICS_DIR = self.tmpdir / "metrics"
        ile.LOGS_DIR = self.tmpdir / "logs"
        ile.LEARNINGS_FILE = self.tmpdir / "idle_learnings.json"
        ile.TELEMETRY_LOG = self.tmpdir / "logs" / "idle_learning.log"
        ile.METRICS_DIR.mkdir(parents=True)
        ile.LOGS_DIR.mkdir(parents=True)

    def tearDown(self):
        ile.METRICS_DIR = self._orig["metrics"]
        ile.LOGS_DIR = self._orig["logs"]
        ile.LEARNINGS_FILE = self._orig["learnings"]
        ile.TELEMETRY_LOG = self._orig["telem"]

    def _write_log(self, name, entries):
        path = ile.LOGS_DIR / name
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")


class TestAnalyzeMetricsConfidence(_TempFilesMixin, unittest.TestCase):

    def test_low_confidence_triggers(self):
        entries = [{"confidence": 0.3} for _ in range(5)]
        results = ile._analyze_metrics(entries)
        types = [s["type"] for s in results]
        self.assertIn("confidence_low", types)

    def test_high_confidence_no_trigger(self):
        entries = [{"confidence": 0.9} for _ in range(5)]
        results = ile._analyze_metrics(entries)
        types = [s["type"] for s in results]
        self.assertNotIn("confidence_low", types)

    def test_insufficient_samples_no_trigger(self):
        entries = [{"confidence": 0.3}]  # only 1, need MIN_SAMPLES
        results = ile._analyze_metrics(entries)
        self.assertEqual(results, [])

    def test_confidence_metric_value(self):
        entries = [{"confidence": 0.4} for _ in range(5)]
        results = ile._analyze_metrics(entries)
        conf = next(s for s in results if s["type"] == "confidence_low")
        self.assertAlmostEqual(conf["metric"], 0.4, places=2)
        self.assertEqual(conf["priority"], "high")


class TestAnalyzeMetricsEscalation(_TempFilesMixin, unittest.TestCase):

    def test_high_escalation_triggers(self):
        entries = [
            {"type": "query", "escalated": True},
            {"type": "query", "escalated": True},
            {"type": "query", "escalated": False},
        ]
        results = ile._analyze_metrics(entries)
        types = [s["type"] for s in results]
        self.assertIn("high_escalation", types)

    def test_low_escalation_no_trigger(self):
        entries = [
            {"type": "query", "escalated": False},
            {"type": "query", "escalated": False},
            {"type": "query", "escalated": True},
            {"type": "query", "escalated": False},
            {"type": "query", "escalated": False},
            {"type": "query", "escalated": False},
            {"type": "query", "escalated": False},
            {"type": "query", "escalated": False},
            {"type": "query", "escalated": False},
            {"type": "query", "escalated": False},
        ]
        results = ile._analyze_metrics(entries)
        types = [s["type"] for s in results]
        self.assertNotIn("high_escalation", types)


class TestAnalyzeMetricsLatency(_TempFilesMixin, unittest.TestCase):

    def test_outliers_trigger(self):
        entries = [
            {"type": "query", "latency_ms": 100},
            {"type": "query", "latency_ms": 100},
            {"type": "query", "latency_ms": 100},
            {"type": "query", "latency_ms": 100},
            {"type": "query", "latency_ms": 100},
            {"type": "query", "latency_ms": 100},
            {"type": "query", "latency_ms": 5000},  # outlier >2x avg
            {"type": "query", "latency_ms": 5000},  # outlier >2x avg
            {"type": "query", "latency_ms": 5000},  # outlier >2x avg
        ]
        results = ile._analyze_metrics(entries)
        types = [s["type"] for s in results]
        self.assertIn("latency_outliers", types)

    def test_uniform_latency_no_trigger(self):
        entries = [{"type": "query", "latency_ms": 100} for _ in range(5)]
        results = ile._analyze_metrics(entries)
        types = [s["type"] for s in results]
        self.assertNotIn("latency_outliers", types)


class TestAnalyzeMetricsModelImbalance(_TempFilesMixin, unittest.TestCase):

    def test_imbalanced_triggers(self):
        entries = [{"type": "query", "model": "qwen2.5:7b"} for _ in range(10)]
        entries.append({"type": "query", "model": "phi4-mini"})  # <10%
        results = ile._analyze_metrics(entries)
        types = [s["type"] for s in results]
        self.assertIn("model_imbalance", types)

    def test_balanced_no_trigger(self):
        entries = [{"type": "query", "model": "qwen2.5:7b"} for _ in range(5)]
        entries.extend([{"type": "query", "model": "phi4-mini"} for _ in range(5)])
        results = ile._analyze_metrics(entries)
        types = [s["type"] for s in results]
        self.assertNotIn("model_imbalance", types)

    def test_single_model_no_trigger(self):
        # Only 1 model — len(models)==1 doesn't trigger imbalance
        entries = [{"type": "query", "model": "qwen2.5:7b"} for _ in range(5)]
        results = ile._analyze_metrics(entries)
        types = [s["type"] for s in results]
        self.assertNotIn("model_imbalance", types)


class TestAnalyzeLogPatterns(_TempFilesMixin, unittest.TestCase):

    def test_recurring_failure(self):
        self._write_log("executor.log", [
            {"action": "docker.restart", "target": "sonarr", "ok": False},
            {"action": "docker.restart", "target": "sonarr", "ok": False},
            {"action": "docker.restart", "target": "sonarr", "ok": False},
        ])
        results = ile._analyze_log_patterns()
        types = [s["type"] for s in results]
        self.assertIn("recurring_failure", types)
        self.assertIn("sonarr", results[0]["description"])

    def test_no_failures_no_trigger(self):
        self._write_log("executor.log", [
            {"action": "docker.restart", "target": "sonarr", "ok": True},
        ])
        results = ile._analyze_log_patterns()
        self.assertEqual(results, [])

    def test_recurring_critical(self):
        self._write_log("diagnostics.log", [
            {"issues": [{"severity": "critical", "problem": "vpn_leak"}]},
            {"issues": [{"severity": "critical", "problem": "vpn_leak"}]},
            {"issues": [{"severity": "critical", "problem": "vpn_leak"}]},
        ])
        results = ile._analyze_log_patterns()
        types = [s["type"] for s in results]
        self.assertIn("recurring_critical", types)

    def test_missing_logs_no_error(self):
        results = ile._analyze_log_patterns()
        self.assertEqual(results, [])

    def test_below_threshold(self):
        self._write_log("executor.log", [
            {"action": "docker.restart", "target": "sonarr", "ok": False},
            {"action": "docker.restart", "target": "sonarr", "ok": False},
        ])
        results = ile._analyze_log_patterns()
        failures = [s for s in results if s["type"] == "recurring_failure"]
        self.assertEqual(len(failures), 0)


class TestAnalyzeConfidenceTrends(_TempFilesMixin, unittest.TestCase):

    def test_declining_trend(self):
        entries = []
        # First 3 days high, last 3 days low
        for i in range(6):
            from datetime import datetime, timedelta
            day = (datetime.now() - timedelta(days=5 - i)).isoformat()
            conf = 0.9 if i < 3 else 0.5
            entries.append({"ts": day, "confidence": conf})
        results = ile._analyze_confidence_trends(entries)
        types = [s["type"] for s in results]
        self.assertIn("confidence_declining", types)

    def test_stable_trend_no_trigger(self):
        entries = []
        for i in range(6):
            from datetime import datetime, timedelta
            day = (datetime.now() - timedelta(days=5 - i)).isoformat()
            entries.append({"ts": day, "confidence": 0.8})
        results = ile._analyze_confidence_trends(entries)
        self.assertEqual(results, [])

    def test_insufficient_days(self):
        entries = [
            {"ts": "2026-03-30T10:00:00", "confidence": 0.3},
            {"ts": "2026-03-29T10:00:00", "confidence": 0.3},
        ]
        results = ile._analyze_confidence_trends(entries)
        self.assertEqual(results, [])

    def test_missing_ts_skipped(self):
        entries = [{"confidence": 0.5}] * 10  # no ts
        results = ile._analyze_confidence_trends(entries)
        self.assertEqual(results, [])


class TestAnalyzeIdle(_TempFilesMixin, unittest.TestCase):

    def test_end_to_end_empty(self):
        result = ile.analyze_idle()
        self.assertEqual(result["suggestions"], [])
        self.assertEqual(result["metrics_entries_analyzed"], 0)
        self.assertIn("analyzed_at", result)

    def test_saves_to_file(self):
        ile.analyze_idle()
        self.assertTrue(ile.LEARNINGS_FILE.exists())

    def test_deduplicates(self):
        self._write_log("executor.log", [
            {"action": "a", "target": "b", "ok": False},
            {"action": "a", "target": "b", "ok": False},
            {"action": "a", "target": "b", "ok": False},
        ])
        result = ile.analyze_idle()
        types = [s["type"] for s in result["suggestions"]]
        self.assertEqual(types.count("recurring_failure"), 1)

    def test_sorted_by_priority(self):
        self._write_log("executor.log", [
            {"action": "a", "target": "b", "ok": False} for _ in range(5)
        ])
        result = ile.analyze_idle()
        priorities = [s["priority"] for s in result["suggestions"]]
        expected = sorted(priorities, key=lambda p: {"high": 0, "medium": 1, "low": 2}[p])
        self.assertEqual(priorities, expected)

    def test_logs_analysis(self):
        ile.analyze_idle()
        self.assertTrue(ile.TELEMETRY_LOG.exists())


class TestGetSuggestions(_TempFilesMixin, unittest.TestCase):

    def test_empty(self):
        self.assertEqual(ile.get_suggestions(), [])

    def test_returns_saved(self):
        ile._save_json(ile.LEARNINGS_FILE, {
            "suggestions": [{"type": "test", "priority": "low"}],
        })
        results = ile.get_suggestions()
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["type"], "test")


if __name__ == "__main__":
    unittest.main()
