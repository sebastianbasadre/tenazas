#!/usr/bin/env python3
"""
Tests para runtime/cognitive_core.py
Corre: python3 tests/test_cognitive.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "/home/tenazas")
from runtime import cognitive_core as cc


class TestIntentInterpreter(unittest.TestCase):

    def test_conversational(self):
        r = cc.interpret_intent("hola buenas")
        self.assertEqual(r["intent"], "conversational")

    def test_infrastructure_issue(self):
        r = cc.interpret_intent("sonarr esta caido no funciona")
        self.assertEqual(r["intent"], "infrastructure_issue")

    def test_infrastructure_crash(self):
        r = cc.interpret_intent("gluetun crash error timeout")
        self.assertEqual(r["intent"], "infrastructure_issue")

    def test_knowledge_question(self):
        r = cc.interpret_intent("como configuro las luces del hue")
        self.assertEqual(r["intent"], "knowledge_question")

    def test_diagnostics(self):
        r = cc.interpret_intent("diagnostico completo del sistema")
        self.assertEqual(r["intent"], "system_diagnostics")

    def test_automation(self):
        r = cc.interpret_intent("prende las luces")
        self.assertEqual(r["intent"], "automation_request")

    def test_monitoring(self):
        r = cc.interpret_intent("monitorea el estado de gluetun")
        self.assertEqual(r["intent"], "monitoring_request")

    def test_context_boosts_infra(self):
        ctx = {"recent_events": [{"type": "container_down"}]}
        r = cc.interpret_intent("que pasa", ctx)
        self.assertIn(r["intent"], ("infrastructure_issue", "knowledge_question"))

    def test_returns_confidence(self):
        r = cc.interpret_intent("diagnostico del sistema")
        self.assertGreater(r["confidence"], 0)
        self.assertLessEqual(r["confidence"], 1.0)

    def test_returns_signals(self):
        r = cc.interpret_intent("sonarr caido error")
        self.assertIsInstance(r["signals"], list)


class TestTaskDecomposer(unittest.TestCase):

    def test_infra_issue_decomposition(self):
        steps = cc.decompose_task(
            "por que el arr stack perdio internet anoche",
            "infrastructure_issue",
        )
        self.assertGreater(len(steps), 3)
        self.assertEqual(steps[0]["step"], 1)
        self.assertEqual(steps[0]["status"], "pending")

    def test_conversational_minimal(self):
        steps = cc.decompose_task("hola", "conversational")
        self.assertEqual(len(steps), 1)

    def test_knowledge_question(self):
        steps = cc.decompose_task("como funciona el RAG", "knowledge_question")
        self.assertGreater(len(steps), 1)
        # Should have a search step
        self.assertTrue(any("buscar" in s["description"].lower() for s in steps))

    def test_complex_query_adds_correlation(self):
        long_query = "por que sonarr se cayo anoche y ademas la vpn estaba con leak y el internet andaba lento"
        steps = cc.decompose_task(long_query, "infrastructure_issue")
        # Should add correlation step for complex query
        self.assertTrue(any("correlacionar" in s["description"].lower() for s in steps))

    def test_steps_have_requires_data(self):
        steps = cc.decompose_task("diagnostico", "system_diagnostics")
        for s in steps:
            self.assertIn("requires_data", s)


class TestReasoningDepth(unittest.TestCase):

    def test_simple_query_fast(self):
        r = cc.select_reasoning_depth("hola", "conversational")
        self.assertEqual(r["depth"], cc.FAST)

    def test_moderate_query_standard(self):
        r = cc.select_reasoning_depth(
            "como configuro las luces del hue bridge",
            "knowledge_question",
        )
        self.assertIn(r["depth"], (cc.FAST, cc.STANDARD))

    def test_complex_query_deep(self):
        r = cc.select_reasoning_depth(
            "por que sonarr se cayo anoche y ademas la vpn estaba con leak y el internet andaba lento y ahora radarr tampoco funciona",
            "infrastructure_issue",
            context={"recent_events": [{"type": "container_down"}, {"type": "vpn_leak"}]},
        )
        self.assertEqual(r["depth"], cc.DEEP)

    def test_complexity_range(self):
        r = cc.select_reasoning_depth("test", "conversational")
        self.assertGreaterEqual(r["complexity"], 0)
        self.assertLessEqual(r["complexity"], 1.0)

    def test_context_increases_complexity(self):
        r_no_ctx = cc.select_reasoning_depth("que pasa", "infrastructure_issue")
        r_ctx = cc.select_reasoning_depth(
            "que pasa", "infrastructure_issue",
            context={"recent_events": [{"type": "x"}, {"type": "y"}, {"type": "z"}]},
        )
        self.assertGreaterEqual(r_ctx["complexity"], r_no_ctx["complexity"])


class TestStrategySelector(unittest.TestCase):

    def test_conversational_fast_direct(self):
        s = cc.select_reasoning_strategy("conversational", cc.FAST)
        self.assertEqual(s["name"], "direct_answer")
        self.assertEqual(s["pipelines"], [])

    def test_knowledge_standard_rag(self):
        s = cc.select_reasoning_strategy("knowledge_question", cc.STANDARD)
        self.assertEqual(s["name"], "rag_retrieval")
        self.assertIn("rag_pipeline", s["pipelines"])

    def test_infra_deep_multiagent(self):
        s = cc.select_reasoning_strategy("infrastructure_issue", cc.DEEP)
        self.assertEqual(s["name"], "multi_agent_reasoning")

    def test_infra_standard_hybrid(self):
        s = cc.select_reasoning_strategy("infrastructure_issue", cc.STANDARD)
        self.assertEqual(s["name"], "hybrid_reasoning")

    def test_diagnostics_fast(self):
        s = cc.select_reasoning_strategy("system_diagnostics", cc.FAST)
        self.assertEqual(s["name"], "infra_diagnostics")

    def test_all_strategies_have_pipelines(self):
        for name, strategy in cc.STRATEGIES.items():
            self.assertIn("pipelines", strategy)
            self.assertIn("description", strategy)


class TestCognitiveLoop(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = cc.TELEMETRY_LOG
        cc.TELEMETRY_LOG = Path(self.tmpdir) / "cognitive.log"

    def tearDown(self):
        cc.TELEMETRY_LOG = self._orig

    def test_full_loop_simple(self):
        result = cc.reason("hola")
        self.assertIn("observation", result)
        self.assertIn("orientation", result)
        self.assertIn("decision", result)
        self.assertIn("execution_plan", result)
        self.assertEqual(result["orientation"]["intent"], "conversational")
        self.assertTrue(result["execution_plan"]["ready"])

    def test_full_loop_infra(self):
        result = cc.reason("sonarr esta caido y no responde")
        self.assertEqual(result["orientation"]["intent"], "infrastructure_issue")
        self.assertGreater(len(result["decision"]["steps"]), 2)
        # Strategy depends on complexity — may be hybrid or multi_agent
        self.assertGreater(len(result["decision"]["pipelines"]), 0)

    def test_full_loop_with_context(self):
        ctx = {"recent_events": [{"type": "vpn_leak"}]}
        result = cc.reason("que esta pasando", ctx)
        self.assertIn("observation", result)
        self.assertGreater(result["observation"]["recent_events_count"], 0)

    def test_latency_recorded(self):
        result = cc.reason("test")
        self.assertGreaterEqual(result["latency_ms"], 0)

    def test_logs_written(self):
        cc.reason("test query")
        self.assertTrue(cc.TELEMETRY_LOG.exists())


class TestConfidenceFeedback(unittest.TestCase):

    def test_high_confidence_no_escalation(self):
        results = [
            {"source": "skills/x/SKILL.md", "score": 0.9, "vector_score": 0.9},
            {"source": "SOUL.md", "score": 0.85, "vector_score": 0.85},
        ]
        strategy = {"name": "rag_retrieval"}
        fb = cc.evaluate_confidence(results, strategy)
        self.assertFalse(fb["should_escalate"])

    def test_low_confidence_escalation(self):
        results = [
            {"source": "memory/old.md", "score": 0.3, "vector_score": 0.3},
        ]
        strategy = {"name": "rag_retrieval"}
        fb = cc.evaluate_confidence(results, strategy)
        self.assertTrue(fb["should_escalate"])

    def test_escalate_depth(self):
        self.assertEqual(cc.escalate_depth(cc.FAST), cc.STANDARD)
        self.assertEqual(cc.escalate_depth(cc.STANDARD), cc.DEEP)
        self.assertEqual(cc.escalate_depth(cc.DEEP), cc.DEEP)


class TestReasonWithFeedback(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = cc.TELEMETRY_LOG
        cc.TELEMETRY_LOG = Path(self.tmpdir) / "cognitive.log"

    def tearDown(self):
        cc.TELEMETRY_LOG = self._orig

    def test_feedback_with_good_results(self):
        results = [{"score": 0.9, "vector_score": 0.9, "source": "SOUL.md"}]
        r = cc.reason_with_feedback("test", results)
        self.assertIn("feedback", r)
        self.assertFalse(r["feedback"]["should_escalate"])

    def test_feedback_with_bad_results_escalates(self):
        results = [{"score": 0.2, "vector_score": 0.2, "source": "x.md"}]
        r = cc.reason_with_feedback("problema complejo de infra", results)
        self.assertIn("feedback", r)
        if r["feedback"]["should_escalate"]:
            self.assertIn("escalation", r)


if __name__ == "__main__":
    unittest.main(verbosity=2)
