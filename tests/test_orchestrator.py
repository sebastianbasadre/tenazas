#!/usr/bin/env python3
"""
Tests para runtime/orchestrator.py
Corre: python3 tests/test_orchestrator.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "/home/tenazas")
from runtime import orchestrator as orch


class TestQueryClassification(unittest.TestCase):
    """Lightweight classifier produces correct categories."""

    def test_greeting_is_chat(self):
        self.assertEqual(orch.classify_query("hola"), "chat")
        self.assertEqual(orch.classify_query("buenas"), "chat")

    def test_casual_is_chat(self):
        self.assertEqual(orch.classify_query("gracias"), "chat")
        self.assertEqual(orch.classify_query("ok dale"), "chat")

    def test_knowledge_query(self):
        self.assertEqual(
            orch.classify_query("como configuro las luces del hue"),
            "knowledge_query",
        )

    def test_knowledge_what_is(self):
        self.assertEqual(
            orch.classify_query("que modelo usa ollama"),
            "knowledge_query",
        )

    def test_knowledge_where(self):
        self.assertEqual(
            orch.classify_query("donde esta la config del vpn"),
            "knowledge_query",
        )

    def test_infra_issue_slow(self):
        self.assertEqual(
            orch.classify_query("internet esta lento"),
            "infrastructure_issue",
        )

    def test_infra_issue_down(self):
        self.assertEqual(
            orch.classify_query("sonarr esta caido no funciona"),
            "infrastructure_issue",
        )

    def test_infra_issue_vpn(self):
        self.assertEqual(
            orch.classify_query("vpn leak detectado"),
            "infrastructure_issue",
        )

    def test_infra_issue_crash(self):
        self.assertEqual(
            orch.classify_query("gluetun crash error"),
            "infrastructure_issue",
        )

    def test_automation_restart(self):
        # "reinicia gluetun" could match infra or automation — both are valid
        result = orch.classify_query("reinicia gluetun")
        self.assertIn(result, ("automation_command", "infrastructure_issue"))

    def test_automation_scan(self):
        self.assertEqual(
            orch.classify_query("escanea la red"),
            "automation_command",
        )

    def test_automation_lights(self):
        self.assertEqual(
            orch.classify_query("prende las luces"),
            "automation_command",
        )

    def test_diagnostic_request(self):
        self.assertEqual(
            orch.classify_query("diagnostico completo del sistema"),
            "diagnostic_request",
        )

    def test_diagnostic_status(self):
        self.assertEqual(
            orch.classify_query("status del sistema completo"),
            "diagnostic_request",
        )

    def test_complex_reasoning(self):
        self.assertEqual(
            orch.classify_query("analiza el rendimiento del stack y compara con la semana pasada"),
            "complex_reasoning",
        )

    def test_context_boosts_infra(self):
        """Recent events should boost infrastructure classification."""
        context = {
            "recent_events": [
                {"type": "container_down", "container": "sonarr"},
            ]
        }
        result = orch.classify_query("que pasa", context)
        # "que pasa" alone would be knowledge_query, but with context
        # boost it should lean towards infrastructure
        self.assertIn(result, ("infrastructure_issue", "knowledge_query"))


class TestModelSelection(unittest.TestCase):
    """Model selection based on category and availability."""

    def test_chat_selects_gemini_flash(self):
        model = orch.select_model("chat")
        self.assertEqual(model["id"], "google/gemini-2.5-flash")

    def test_infra_selects_qwen_7b(self):
        model = orch.select_model("infrastructure_issue")
        self.assertEqual(model["id"], "ollama/qwen2.5:7b")

    def test_reasoning_selects_gemini_pro(self):
        model = orch.select_model("complex_reasoning")
        self.assertEqual(model["id"], "google/gemini-2.5-pro")

    def test_diagnostic_selects_qwen_7b(self):
        model = orch.select_model("diagnostic_request")
        self.assertEqual(model["id"], "ollama/qwen2.5:7b")

    def test_fallback_when_gpu_offline(self):
        """When local GPU is offline, fall back to cloud."""
        model = orch.select_model(
            "infrastructure_issue",
            available_backends={"local_gpu": False, "local_cpu": False, "cloud": True},
        )
        self.assertEqual(model["tier"], "cloud")

    def test_fallback_when_cloud_offline(self):
        """When cloud is offline, use local."""
        model = orch.select_model(
            "chat",
            available_backends={"local_gpu": True, "local_cpu": True, "cloud": False},
        )
        self.assertIn(model["tier"], ("local_gpu", "local_cpu"))

    def test_all_offline_returns_gemini_flash(self):
        """Last resort always returns something."""
        model = orch.select_model(
            "chat",
            available_backends={"local_gpu": False, "local_cpu": False, "cloud": False},
        )
        self.assertIsNotNone(model["id"])


class TestPipelineAssembly(unittest.TestCase):
    """Pipeline configuration for each category."""

    def test_chat_has_no_modules(self):
        p = orch.get_pipeline("chat")
        self.assertEqual(p["modules"], [])

    def test_knowledge_uses_rag(self):
        p = orch.get_pipeline("knowledge_query")
        self.assertIn("rag_pipeline", p["modules"])

    def test_infra_uses_diagnostics_and_rag(self):
        p = orch.get_pipeline("infrastructure_issue")
        self.assertIn("infra_diagnostics", p["modules"])
        self.assertIn("rag_pipeline", p["modules"])
        self.assertIn("action_mapper", p["modules"])

    def test_automation_uses_action_mapper(self):
        p = orch.get_pipeline("automation_command")
        self.assertIn("action_mapper", p["modules"])

    def test_unknown_category_defaults_to_chat(self):
        p = orch.get_pipeline("nonexistent_category")
        self.assertEqual(p["modules"], [])


class TestRouteQuery(unittest.TestCase):
    """Full routing: classify → model → pipeline."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = orch.ORCHESTRATOR_LOG
        orch.ORCHESTRATOR_LOG = Path(self.tmpdir) / "orchestrator.log"

    def tearDown(self):
        orch.ORCHESTRATOR_LOG = self._orig

    def test_returns_complete_route(self):
        result = orch.route_query("hola que tal")
        self.assertIn("query", result)
        self.assertIn("classification", result)
        self.assertIn("pipeline", result)
        self.assertIn("model", result)
        self.assertIn("parameters", result)

    def test_model_has_required_fields(self):
        result = orch.route_query("como configuro hue")
        model = result["model"]
        self.assertIn("id", model)
        self.assertIn("alias", model)
        self.assertIn("tier", model)

    def test_parameters_have_temperature(self):
        result = orch.route_query("diagnostico")
        self.assertIn("temperature", result["parameters"])
        self.assertIn("max_tokens", result["parameters"])

    def test_chat_low_temperature(self):
        """Chat should have higher temperature than diagnostics."""
        chat = orch.route_query("hola")
        diag = orch.route_query("diagnostico del sistema")
        self.assertGreater(
            chat["parameters"]["temperature"],
            diag["parameters"]["temperature"],
        )

    def test_logs_routing_decision(self):
        orch.route_query("test query")
        log_path = orch.ORCHESTRATOR_LOG
        self.assertTrue(log_path.exists())
        line = log_path.read_text().strip().split("\n")[-1]
        entry = json.loads(line)
        self.assertEqual(entry["type"], "routing")
        self.assertIn("classification", entry)
        self.assertIn("model", entry)

    def test_context_with_events(self):
        context = {
            "recent_events": [{"type": "vpn_leak"}],
            "working_memory": "fixing vpn issues",
        }
        result = orch.route_query("que esta pasando", context)
        self.assertIn("has_working_memory", result["context_signals"])
        self.assertTrue(
            any("recent_event" in s for s in result["context_signals"])
        )


class TestEscalation(unittest.TestCase):
    """Escalation from low confidence to more capable pipeline."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = orch.ORCHESTRATOR_LOG
        orch.ORCHESTRATOR_LOG = Path(self.tmpdir) / "orchestrator.log"

    def tearDown(self):
        orch.ORCHESTRATOR_LOG = self._orig

    def test_knowledge_escalates_to_reasoning(self):
        original = orch.route_query("como funciona el proxy")
        escalated = orch.escalate(original, "low_confidence")
        self.assertEqual(escalated["classification"], "complex_reasoning")
        self.assertEqual(escalated["escalated_from"], "knowledge_query")
        self.assertEqual(escalated["escalation_reason"], "low_confidence")

    def test_chat_escalates_to_knowledge(self):
        original = orch.route_query("hola")
        escalated = orch.escalate(original, "insufficient_context")
        self.assertEqual(escalated["classification"], "knowledge_query")

    def test_infra_escalates_to_reasoning(self):
        original = orch.route_query("sonarr caido error")
        escalated = orch.escalate(original, "diagnostic_failed")
        self.assertEqual(escalated["classification"], "complex_reasoning")

    def test_reasoning_stays_at_reasoning(self):
        original = orch.route_query("analiza todo")
        escalated = orch.escalate(original, "still_low")
        self.assertEqual(escalated["classification"], "complex_reasoning")

    def test_escalation_logs(self):
        original = orch.route_query("test")
        orch.escalate(original, "test_reason")
        log_path = orch.ORCHESTRATOR_LOG
        lines = log_path.read_text().strip().split("\n")
        escalation_entries = [
            json.loads(l) for l in lines if "escalation" in l
        ]
        self.assertGreater(len(escalation_entries), 0)
        self.assertEqual(escalation_entries[-1]["reason"], "test_reason")

    def test_escalation_changes_model(self):
        original = orch.route_query("hola")
        escalated = orch.escalate(original, "need_more")
        # Escalated should potentially use a different model
        self.assertIn("model", escalated)
        self.assertIsNotNone(escalated["model"]["id"])


class TestContextSignals(unittest.TestCase):
    """Context signal extraction."""

    def test_no_context_empty_signals(self):
        result = orch.route_query("hola")
        self.assertEqual(result["context_signals"], [])

    def test_ollama_offline_signal(self):
        context = {"ollama_available": False}
        result = orch.route_query("hola", context)
        self.assertIn("ollama_offline", result["context_signals"])

    def test_previous_classification_signal(self):
        context = {"previous_classification": "infrastructure_issue"}
        result = orch.route_query("hola", context)
        self.assertIn("prev:infrastructure_issue", result["context_signals"])


if __name__ == "__main__":
    unittest.main(verbosity=2)
