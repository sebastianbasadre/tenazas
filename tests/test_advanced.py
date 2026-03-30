#!/usr/bin/env python3
"""
Tests para modules avanzados: metrics_engine, infra_memory,
runbook_generator, multi_agent_engine, self_optimizer.
Corre: python3 tests/test_advanced.py
"""

import sys
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/tenazas")
from runtime import metrics_engine as me
from runtime import infra_memory as im
from runtime import runbook_generator as rb
from runtime import multi_agent_engine as mae
from runtime import self_optimizer as so


# ── Metrics Engine ───────────────────────────────────────────────


class TestMetricsRecording(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_dir = me.METRICS_DIR
        self._orig_log = me.METRICS_LOG
        me.METRICS_DIR = Path(self.tmpdir) / "metrics"
        me.METRICS_LOG = Path(self.tmpdir) / "metrics.log"

    def tearDown(self):
        me.METRICS_DIR = self._orig_dir
        me.METRICS_LOG = self._orig_log

    def test_record_creates_daily_file(self):
        me.record("test", {"key": "value"})
        today = datetime.now().strftime("%Y-%m-%d")
        path = me.METRICS_DIR / f"{today}.json"
        self.assertTrue(path.exists())
        data = json.loads(path.read_text())
        self.assertEqual(len(data), 1)
        self.assertEqual(data[0]["type"], "test")

    def test_record_query_full(self):
        me.record_query("test", "chat", "rag", "gemini", 0.85, 150.0)
        today = datetime.now().strftime("%Y-%m-%d")
        data = json.loads((me.METRICS_DIR / f"{today}.json").read_text())
        self.assertEqual(data[0]["query_type"], "chat")
        self.assertEqual(data[0]["confidence"], 0.85)

    def test_multiple_records_append(self):
        me.record("a", {})
        me.record("b", {})
        me.record("c", {})
        today = datetime.now().strftime("%Y-%m-%d")
        data = json.loads((me.METRICS_DIR / f"{today}.json").read_text())
        self.assertEqual(len(data), 3)

    def test_record_diagnostic(self):
        me.record_diagnostic(3, 1, ["restart gluetun"])
        today = datetime.now().strftime("%Y-%m-%d")
        data = json.loads((me.METRICS_DIR / f"{today}.json").read_text())
        self.assertEqual(data[0]["type"], "diagnostic")
        self.assertEqual(data[0]["critical"], 1)


class TestMetricsAggregation(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_dir = me.METRICS_DIR
        self._orig_log = me.METRICS_LOG
        me.METRICS_DIR = Path(self.tmpdir) / "metrics"
        me.METRICS_LOG = Path(self.tmpdir) / "metrics.log"

        # Seed some data
        me.record_query("q1", "chat", "direct", "gemini-flash", 0.9, 100)
        me.record_query("q2", "knowledge_query", "rag", "gemini-flash", 0.7, 200)
        me.record_query("q3", "infra_issue", "diagnostics", "qwen-7b", 0.5, 500, escalated=True)

    def tearDown(self):
        me.METRICS_DIR = self._orig_dir
        me.METRICS_LOG = self._orig_log

    def test_average_confidence(self):
        avg = me.average_confidence(1)
        self.assertAlmostEqual(avg, 0.7, places=1)

    def test_pipeline_distribution(self):
        dist = me.pipeline_distribution(1)
        self.assertIn("rag", dist)
        self.assertIn("direct", dist)

    def test_model_distribution(self):
        dist = me.model_distribution(1)
        self.assertIn("gemini-flash", dist)
        self.assertEqual(dist["gemini-flash"], 2)

    def test_latency_stats(self):
        stats = me.latency_stats(1)
        self.assertEqual(stats["count"], 3)
        self.assertGreater(stats["avg"], 0)
        self.assertGreaterEqual(stats["max"], stats["min"])

    def test_escalation_rate(self):
        rate = me.escalation_rate(1)
        self.assertAlmostEqual(rate, 1/3, places=2)

    def test_summary(self):
        s = me.summary(1)
        self.assertIn("avg_confidence", s)
        self.assertIn("pipeline_distribution", s)
        self.assertIn("latency", s)

    def test_empty_metrics(self):
        me.METRICS_DIR = Path(tempfile.mkdtemp()) / "empty"
        self.assertEqual(me.average_confidence(1), 0.0)
        self.assertEqual(me.latency_stats(1)["count"], 0)


class TestMetricsTimer(unittest.TestCase):

    def test_timer_measures(self):
        with me.Timer() as t:
            _ = sum(range(1000))
        self.assertGreaterEqual(t.ms, 0)


# ── Infra Memory ─────────────────────────────────────────────────


class TestInfraMemory(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_mem = im.MEMORY_FILE
        self._orig_log_file = im.MEMORY_LOG_FILE
        self._orig_tel = im.TELEMETRY_LOG
        im.MEMORY_FILE = Path(self.tmpdir) / "infra_memory.json"
        im.MEMORY_LOG_FILE = Path(self.tmpdir) / "memory_log.json"
        im.TELEMETRY_LOG = Path(self.tmpdir) / "infra_memory.log"

    def tearDown(self):
        im.MEMORY_FILE = self._orig_mem
        im.MEMORY_LOG_FILE = self._orig_log_file
        im.TELEMETRY_LOG = self._orig_tel

    def _seed_events(self, event_type, target, count=10, hour=3):
        now = datetime.now(timezone.utc)
        memory = []
        for i in range(count):
            ts = (now - timedelta(days=i)).replace(hour=hour, minute=0)
            memory.append({
                "ts": ts.isoformat(),
                "events": [{"type": event_type, "container": target}],
                "tasks_run": 0,
                "results": [],
            })
        with open(im.MEMORY_LOG_FILE, "w") as f:
            json.dump(memory, f)

    def test_detect_patterns_from_recurring_events(self):
        self._seed_events("container_down", "sonarr", count=10)
        patterns = im.detect_patterns()
        self.assertGreater(len(patterns), 0)
        self.assertEqual(patterns[0]["event"], "container_down")
        self.assertEqual(patterns[0]["target"], "sonarr")

    def test_pattern_confidence_calculation(self):
        conf = im._pattern_confidence(14, days=14)
        self.assertEqual(conf, 1.0)
        conf_low = im._pattern_confidence(1, days=14)
        self.assertLess(conf_low, 0.5)

    def test_no_patterns_below_threshold(self):
        self._seed_events("container_down", "sonarr", count=1)
        patterns = im.detect_patterns()
        self.assertEqual(len(patterns), 0)

    def test_update_memory_saves_file(self):
        self._seed_events("container_down", "sonarr", count=5)
        im.update_memory()
        self.assertTrue(im.MEMORY_FILE.exists())
        data = json.loads(im.MEMORY_FILE.read_text())
        self.assertIn("patterns", data)
        self.assertIn("last_analysis", data)

    def test_get_patterns_for_target(self):
        self._seed_events("container_down", "sonarr", count=10)
        im.update_memory()
        patterns = im.get_patterns_for_target("sonarr")
        self.assertGreater(len(patterns), 0)
        patterns_other = im.get_patterns_for_target("radarr")
        self.assertEqual(len(patterns_other), 0)

    def test_recommend_action(self):
        action = im._recommend_action("container_down", "sonarr")
        self.assertIn("sonarr", action)
        action_vpn = im._recommend_action("vpn_leak", "")
        self.assertIn("gluetun", action_vpn)


# ── Runbook Generator ────────────────────────────────────────────


class TestRunbookGenerator(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_dir = rb.RUNBOOKS_DIR
        self._orig_log = rb.TELEMETRY_LOG
        rb.RUNBOOKS_DIR = Path(self.tmpdir) / "runbooks"
        rb.TELEMETRY_LOG = Path(self.tmpdir) / "runbooks.log"

    def tearDown(self):
        rb.RUNBOOKS_DIR = self._orig_dir
        rb.TELEMETRY_LOG = self._orig_log

    def test_generate_container_crash_loop(self):
        runbook = rb.generate_runbook(
            "sonarr crash loop detected", target="sonarr",
            patterns=[{"event": "restart_executed", "pattern": "x", "confidence": 0.9}],
        )
        self.assertEqual(runbook["problem_type"], "container_crash_loop")
        self.assertGreater(len(runbook["steps"]), 0)
        self.assertEqual(runbook["target"], "sonarr")

    def test_generate_vpn_failure(self):
        runbook = rb.generate_runbook("vpn leak detectado", target="gluetun")
        self.assertEqual(runbook["problem_type"], "vpn_failure")

    def test_generate_network_failure(self):
        runbook = rb.generate_runbook("internet caido sin conexion")
        self.assertEqual(runbook["problem_type"], "network_failure")

    def test_generate_ollama_offline(self):
        runbook = rb.generate_runbook("ollama notebook offline")
        self.assertEqual(runbook["problem_type"], "ollama_offline")

    def test_runbook_saved_to_disk(self):
        rb.generate_runbook("test container down", target="radarr")
        self.assertTrue(rb.RUNBOOKS_DIR.exists())
        files = list(rb.RUNBOOKS_DIR.glob("*.json"))
        self.assertEqual(len(files), 1)

    def test_get_existing_runbook(self):
        rb.generate_runbook("container down", target="sonarr")
        existing = rb.get_runbook("container_down", "sonarr")
        self.assertIsNotNone(existing)

    def test_list_runbooks(self):
        rb.generate_runbook("vpn leak", target="gluetun")
        rb.generate_runbook("container down", target="sonarr")
        runbooks = rb.list_runbooks()
        self.assertEqual(len(runbooks), 2)

    def test_format_runbook(self):
        runbook = rb.generate_runbook("sonarr caido", target="sonarr")
        text = rb.format_runbook(runbook)
        self.assertIn("sonarr", text)
        self.assertIn("automaticamente", text)

    def test_steps_have_approval_flag(self):
        runbook = rb.generate_runbook("crash loop", target="sonarr",
                                       patterns=[{"event": "restart_executed", "pattern": "x", "confidence": 0.9}])
        approval_steps = [s for s in runbook["steps"] if s.get("requires_approval")]
        self.assertGreater(len(approval_steps), 0)

    def test_target_filled_in_commands(self):
        runbook = rb.generate_runbook("container down", target="lidarr")
        for step in runbook["steps"]:
            if "command" in step:
                self.assertNotIn("{target}", step["command"])
                if "lidarr" in step.get("action", "").lower() or "restart" in step.get("command", ""):
                    pass  # Target should be filled


# ── Multi-Agent Engine ───────────────────────────────────────────


class TestMultiAgentEngine(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_log = mae.TELEMETRY_LOG
        mae.TELEMETRY_LOG = Path(self.tmpdir) / "multi_agent.log"

    def tearDown(self):
        mae.TELEMETRY_LOG = self._orig_log

    @patch("runtime.multi_agent_engine._ollama_available", return_value=False)
    def test_offline_returns_fallback(self, _):
        result = mae.reason("test problem")
        self.assertFalse(result["success"])
        self.assertTrue(result["fallback_used"])
        self.assertIn("no disponible", result["conclusion"].lower())

    @patch("runtime.multi_agent_engine._generate")
    @patch("runtime.multi_agent_engine._ollama_available", return_value=True)
    def test_full_reasoning_pipeline(self, _, mock_gen):
        mock_gen.side_effect = [
            "El problema es X por causa Y",     # analysis
            "El analisis es correcto",           # verification
            "Diagnostico: X. Accion: restart Y", # synthesis
        ]
        result = mae.reason("sonarr keeps crashing")
        self.assertTrue(result["success"])
        self.assertIsNotNone(result["analysis"])
        self.assertIsNotNone(result["verification"])
        self.assertIsNotNone(result["conclusion"])
        self.assertEqual(len(result["agents_used"]), 3)

    @patch("runtime.multi_agent_engine._generate")
    @patch("runtime.multi_agent_engine._ollama_available", return_value=True)
    def test_analysis_failure_returns_early(self, _, mock_gen):
        mock_gen.return_value = None
        result = mae.reason("problem")
        self.assertFalse(result["success"])
        self.assertTrue(result["fallback_used"])

    @patch("runtime.multi_agent_engine._generate")
    @patch("runtime.multi_agent_engine._ollama_available", return_value=True)
    def test_verification_failure_continues(self, _, mock_gen):
        mock_gen.side_effect = [
            "Analisis del problema",  # analysis OK
            None,                      # verification fails
            "Conclusion final",        # synthesis OK
        ]
        result = mae.reason("problem")
        self.assertTrue(result["success"])
        self.assertTrue(result["fallback_used"])
        self.assertEqual(len(result["agents_used"]), 2)

    @patch("runtime.multi_agent_engine._generate")
    @patch("runtime.multi_agent_engine._ollama_available", return_value=True)
    def test_format_reasoning(self, _, mock_gen):
        mock_gen.side_effect = ["Analysis", "Verification", "Conclusion"]
        result = mae.reason("test")
        text = mae.format_reasoning(result)
        self.assertIn("Analisis", text)
        self.assertIn("Verificacion", text)
        self.assertIn("Conclusion", text)


# ── Self Optimizer ───────────────────────────────────────────────


class TestSelfOptimizer(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_file = so.OPTIMIZATION_FILE
        self._orig_dir = so.METRICS_DIR
        self._orig_log = so.TELEMETRY_LOG
        so.OPTIMIZATION_FILE = Path(self.tmpdir) / "optimization_state.json"
        so.METRICS_DIR = Path(self.tmpdir) / "metrics"
        so.TELEMETRY_LOG = Path(self.tmpdir) / "optimizer.log"

    def tearDown(self):
        so.OPTIMIZATION_FILE = self._orig_file
        so.METRICS_DIR = self._orig_dir
        so.TELEMETRY_LOG = self._orig_log

    def _seed_metrics(self):
        so.METRICS_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        entries = []
        for i in range(10):
            entries.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": "query",
                "query_type": "knowledge_query",
                "pipeline": "rag",
                "model": "gemini-flash",
                "confidence": 0.8,
                "latency_ms": 200,
                "escalated": i > 7,  # 20% escalation
            })
        for i in range(5):
            entries.append({
                "ts": datetime.now(timezone.utc).isoformat(),
                "type": "query",
                "query_type": "infrastructure_issue",
                "pipeline": "diagnostics",
                "model": "qwen-7b",
                "confidence": 0.5,
                "latency_ms": 500,
                "escalated": True,  # 100% escalation
            })
        with open(so.METRICS_DIR / f"{today}.json", "w") as f:
            json.dump(entries, f)

    def test_optimize_produces_state(self):
        self._seed_metrics()
        state = so.optimize()
        self.assertIn("preferences", state)
        self.assertIn("knowledge_query", state["preferences"])

    def test_knowledge_query_preferences(self):
        self._seed_metrics()
        so.optimize()
        pref = so.get_preference("knowledge_query")
        self.assertEqual(pref["preferred_pipeline"], "rag")
        self.assertEqual(pref["preferred_model"], "gemini-flash")

    def test_high_escalation_suggests_multi_agent(self):
        self._seed_metrics()
        so.optimize()
        pref = so.get_preference("infrastructure_issue")
        self.assertEqual(pref["fallback_pipeline"], "multi_agent")
        self.assertGreater(pref["escalation_rate"], 0.3)

    def test_should_use_multi_agent(self):
        self._seed_metrics()
        so.optimize()
        self.assertTrue(so.should_use_multi_agent("infrastructure_issue"))
        self.assertFalse(so.should_use_multi_agent("knowledge_query"))

    def test_should_expand_retrieval(self):
        self._seed_metrics()
        so.optimize()
        self.assertTrue(so.should_expand_retrieval("infrastructure_issue"))
        self.assertFalse(so.should_expand_retrieval("knowledge_query"))

    def test_get_preferred_model(self):
        self._seed_metrics()
        so.optimize()
        self.assertEqual(so.get_preferred_model("knowledge_query"), "gemini-flash")
        self.assertEqual(so.get_preferred_model("infrastructure_issue"), "qwen-7b")

    def test_empty_metrics_no_crash(self):
        state = so.optimize()
        self.assertEqual(state["preferences"], {})

    def test_below_min_samples_ignored(self):
        so.METRICS_DIR.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        entries = [
            {"ts": "now", "type": "query", "query_type": "chat",
             "pipeline": "direct", "model": "x", "confidence": 0.9,
             "latency_ms": 50, "escalated": False},
        ]
        with open(so.METRICS_DIR / f"{today}.json", "w") as f:
            json.dump(entries, f)
        so.optimize()
        self.assertEqual(so.get_preference("chat"), {})


if __name__ == "__main__":
    unittest.main(verbosity=2)
