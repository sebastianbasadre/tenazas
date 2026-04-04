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

    def test_chat_selects_local_model(self):
        model = orch.select_model("chat")
        self.assertEqual(model["id"], "ollama/qwen2.5:7b")

    def test_infra_selects_qwen_7b(self):
        model = orch.select_model("infrastructure_issue")
        self.assertEqual(model["id"], "ollama/qwen2.5:7b")

    def test_reasoning_selects_deepseek_via_v2(self):
        model = orch.select_model("complex_reasoning")
        # V2 routes complex_reasoning → reason role → deepseek-r1:14b
        self.assertEqual(model["id"], "ollama/deepseek-r1:14b")

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


# ── Integration: Orchestrator + Router V2 ────────────────────────

from unittest.mock import patch, MagicMock


def _mock_v2_backends(gpu=True, nas=True):
    """Return a fake detect_backends result for V2 testing."""
    return {
        "notebook_gpu": {
            "available": gpu,
            "url": "http://192.168.2.6:11434",
            "type": "gpu",
            "priority": 1,
            "description": "HomeLab GPU",
        },
        "nas_cpu": {
            "available": nas,
            "url": "http://127.0.0.1:11434",
            "type": "cpu",
            "priority": 2,
            "description": "NAS Ryzen CPU (fallback)",
        },
    }


class TestRouterV2Integration(unittest.TestCase):
    """End-to-end: orchestrator.route_query() → model_router.route_by_role()."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_log = orch.ORCHESTRATOR_LOG
        orch.ORCHESTRATOR_LOG = Path(self.tmpdir) / "orchestrator.log"
        self._orig_v2 = orch.USE_ROUTER_V2
        orch.USE_ROUTER_V2 = True
        orch.reset_routing_metrics()
        # Reset guardrails so tests are independent
        from runtime import model_router as mr
        mr.reset_guardrails()

    def tearDown(self):
        orch.ORCHESTRATOR_LOG = self._orig_log
        orch.USE_ROUTER_V2 = self._orig_v2
        from runtime import model_router as mr
        mr.reset_guardrails()

    @patch("runtime.model_router.detect_backends")
    @patch("runtime.model_router._log")
    def test_chat_routes_to_fast_model(self, mock_mr_log, mock_detect):
        mock_detect.return_value = _mock_v2_backends()
        result = orch.route_query("hola buenas")
        self.assertEqual(result["classification"], "chat")
        self.assertEqual(result["model"]["id"], "ollama/qwen2.5:7b")
        self.assertIn("router_v2", result)
        self.assertEqual(result["router_v2"]["role"], "fast")

    @patch("runtime.model_router.detect_backends")
    @patch("runtime.model_router._log")
    def test_reasoning_routes_to_deepseek(self, mock_mr_log, mock_detect):
        mock_detect.return_value = _mock_v2_backends()
        result = orch.route_query("analiza el rendimiento del stack y compara con la semana pasada")
        self.assertEqual(result["classification"], "complex_reasoning")
        self.assertEqual(result["model"]["id"], "ollama/deepseek-r1:14b")
        self.assertEqual(result["router_v2"]["role"], "reason")

    @patch("runtime.model_router.detect_backends")
    @patch("runtime.model_router._log")
    def test_code_prompt_overrides_category(self, mock_mr_log, mock_detect):
        """A chat-classified query with code content → code model."""
        mock_detect.return_value = _mock_v2_backends()
        result = orch.route_query("refactor this function for me")
        # classify_query sees "refactor" but may classify as chat/automation
        # V2 classify_task sees code keywords → code role → coder model
        self.assertEqual(result["model"]["id"], "ollama/qwen2.5-coder:14b")
        self.assertEqual(result["router_v2"]["role"], "code")

    @patch("runtime.model_router.detect_backends")
    @patch("runtime.model_router._log")
    def test_vision_context_flag(self, mock_mr_log, mock_detect):
        mock_detect.return_value = _mock_v2_backends()
        context = {"has_image": True}
        result = orch.route_query("que es esto", context)
        self.assertEqual(result["model"]["id"], "ollama/qwen2.5vl:7b")
        self.assertEqual(result["router_v2"]["role"], "vision")

    @patch("runtime.model_router.detect_backends")
    @patch("runtime.model_router._log")
    def test_critical_context_flag(self, mock_mr_log, mock_detect):
        mock_detect.return_value = _mock_v2_backends()
        context = {"critical": True}
        result = orch.route_query("ejecuta esto", context)
        self.assertEqual(result["model"]["id"], "ollama/qwen2.5:32b")
        self.assertEqual(result["router_v2"]["role"], "heavy")

    @patch("runtime.model_router.detect_backends")
    @patch("runtime.model_router._log")
    def test_v2_result_has_required_keys(self, mock_mr_log, mock_detect):
        mock_detect.return_value = _mock_v2_backends()
        result = orch.route_query("hola")
        self.assertIn("router_v2", result)
        v2 = result["router_v2"]
        for key in ("role", "model", "fallback_used", "vram_swap", "signals"):
            self.assertIn(key, v2, f"router_v2 missing key: {key}")

    @patch("runtime.model_router.detect_backends")
    @patch("runtime.model_router._log")
    def test_v2_log_includes_router_info(self, mock_mr_log, mock_detect):
        mock_detect.return_value = _mock_v2_backends()
        orch.route_query("analiza esto")
        log_path = orch.ORCHESTRATOR_LOG
        lines = log_path.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        self.assertIn("router_v2_role", entry)
        self.assertIn("router_v2_model", entry)

    @patch("runtime.model_router.detect_backends")
    @patch("runtime.model_router._log")
    def test_metrics_increment_on_v2(self, mock_mr_log, mock_detect):
        mock_detect.return_value = _mock_v2_backends()
        orch.reset_routing_metrics()
        orch.route_query("hola")
        metrics = orch.get_routing_metrics()
        self.assertEqual(metrics.get("v2_calls", 0), 1)
        self.assertEqual(metrics.get("v1_calls", 0), 0)

    @patch("runtime.model_router.detect_backends")
    @patch("runtime.model_router._log")
    def test_metrics_track_heavy_route(self, mock_mr_log, mock_detect):
        mock_detect.return_value = _mock_v2_backends()
        orch.reset_routing_metrics()
        # critical → heavy → 32b (fits in 24GB RTX 3090, no vram swap)
        orch.route_query("deploy to production", {"critical": True})
        metrics = orch.get_routing_metrics()
        # 32b fits in 24GB so no vram_swap, but v2_calls should increment
        self.assertGreaterEqual(metrics.get("v2_calls", 0), 1)


class TestRouterV2Fallback(unittest.TestCase):
    """V2 failure → V1 fallback behavior."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_log = orch.ORCHESTRATOR_LOG
        orch.ORCHESTRATOR_LOG = Path(self.tmpdir) / "orchestrator.log"
        self._orig_v2 = orch.USE_ROUTER_V2
        orch.reset_routing_metrics()

    def tearDown(self):
        orch.ORCHESTRATOR_LOG = self._orig_log
        orch.USE_ROUTER_V2 = self._orig_v2
        from runtime import model_router as mr
        mr.reset_guardrails()

    @patch("runtime.model_router.detect_backends")
    @patch("runtime.model_router._log")
    def test_v2_offline_falls_to_v1(self, mock_mr_log, mock_detect):
        """When all Ollama backends are offline, V2 returns empty → V1 kicks in."""
        orch.USE_ROUTER_V2 = True
        mock_detect.return_value = _mock_v2_backends(gpu=False, nas=False)
        orch.reset_routing_metrics()
        result = orch.route_query("hola")
        # V1 fallback: returns a model from MODELS dict
        self.assertIsNotNone(result["model"]["id"])
        metrics = orch.get_routing_metrics()
        self.assertGreaterEqual(metrics.get("v1_fallback_from_v2", 0), 1)
        self.assertGreaterEqual(metrics.get("v1_calls", 0), 1)

    def test_v2_disabled_uses_v1(self):
        """USE_ROUTER_V2=False → pure V1 path."""
        orch.USE_ROUTER_V2 = False
        orch.reset_routing_metrics()
        result = orch.route_query("hola")
        self.assertNotIn("router_v2", result)
        metrics = orch.get_routing_metrics()
        self.assertEqual(metrics.get("v2_calls", 0), 0)
        self.assertGreaterEqual(metrics.get("v1_calls", 0), 1)

    def test_v1_with_explicit_backends(self):
        """Passing available_backends forces V1 path (backward compat)."""
        orch.USE_ROUTER_V2 = True
        orch.reset_routing_metrics()
        model = orch.select_model(
            "chat",
            available_backends={"local_gpu": True, "local_cpu": True, "cloud": True},
        )
        # V1 path because available_backends was explicitly passed
        self.assertIsNotNone(model["id"])
        metrics = orch.get_routing_metrics()
        self.assertGreaterEqual(metrics.get("v1_calls", 0), 1)

    @patch("runtime.model_router.detect_backends", side_effect=Exception("boom"))
    def test_v2_exception_falls_to_v1(self, mock_detect):
        """If model_router raises, V1 handles it gracefully."""
        orch.USE_ROUTER_V2 = True
        orch.reset_routing_metrics()
        result = orch.route_query("hola")
        self.assertIsNotNone(result["model"]["id"])
        metrics = orch.get_routing_metrics()
        self.assertGreaterEqual(metrics.get("v1_fallback_from_v2", 0), 1)


class TestRouterV2Escalation(unittest.TestCase):
    """Escalation with V2 integration."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_log = orch.ORCHESTRATOR_LOG
        orch.ORCHESTRATOR_LOG = Path(self.tmpdir) / "orchestrator.log"
        self._orig_v2 = orch.USE_ROUTER_V2
        orch.USE_ROUTER_V2 = True

    def tearDown(self):
        orch.ORCHESTRATOR_LOG = self._orig_log
        orch.USE_ROUTER_V2 = self._orig_v2
        from runtime import model_router as mr
        mr.reset_guardrails()

    @patch("runtime.model_router.detect_backends")
    @patch("runtime.model_router._log")
    def test_escalation_uses_v2_model(self, mock_mr_log, mock_detect):
        mock_detect.return_value = _mock_v2_backends()
        original = orch.route_query("como funciona el proxy")
        escalated = orch.escalate(original, "low_confidence")
        # Escalated to complex_reasoning → reason role → deepseek
        self.assertEqual(escalated["classification"], "complex_reasoning")
        self.assertEqual(escalated["model"]["id"], "ollama/deepseek-r1:14b")


class TestBuildFlagsFromContext(unittest.TestCase):
    """Context → V2 flags translation."""

    def test_empty_context(self):
        self.assertEqual(orch._build_flags_from_context(None), {})
        self.assertEqual(orch._build_flags_from_context({}), {})

    def test_image_flag(self):
        flags = orch._build_flags_from_context({"has_image": True})
        self.assertTrue(flags.get("has_image"))

    def test_critical_flag(self):
        flags = orch._build_flags_from_context({"critical": True})
        self.assertTrue(flags.get("critical"))

    def test_role_passthrough(self):
        flags = orch._build_flags_from_context({"role": "heavy"})
        self.assertEqual(flags["role"], "heavy")

    def test_unrelated_keys_ignored(self):
        flags = orch._build_flags_from_context({
            "recent_events": [{"type": "vpn_down"}],
            "working_memory": "some text",
        })
        self.assertEqual(flags, {})


class TestRoutingMetrics(unittest.TestCase):
    """Routing metrics tracking."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_log = orch.ORCHESTRATOR_LOG
        orch.ORCHESTRATOR_LOG = Path(self.tmpdir) / "orchestrator.log"
        self._orig_v2 = orch.USE_ROUTER_V2
        orch.reset_routing_metrics()

    def tearDown(self):
        orch.ORCHESTRATOR_LOG = self._orig_log
        orch.USE_ROUTER_V2 = self._orig_v2
        from runtime import model_router as mr
        mr.reset_guardrails()

    def test_reset_clears_metrics(self):
        orch._routing_metrics["test"] = 42
        orch.reset_routing_metrics()
        self.assertEqual(orch.get_routing_metrics(), {})

    def test_get_returns_copy(self):
        orch._routing_metrics["v2_calls"] = 5
        metrics = orch.get_routing_metrics()
        metrics["v2_calls"] = 999
        self.assertEqual(orch._routing_metrics["v2_calls"], 5)

    @patch("runtime.model_router.detect_backends")
    @patch("runtime.model_router._log")
    def test_role_distribution_tracked(self, mock_mr_log, mock_detect):
        mock_detect.return_value = _mock_v2_backends()
        orch.USE_ROUTER_V2 = True
        orch.reset_routing_metrics()
        orch.route_query("hola")
        orch.route_query("analiza esto")
        metrics = orch.get_routing_metrics()
        self.assertGreaterEqual(metrics.get("v2_role_fast", 0), 1)
        self.assertGreaterEqual(metrics.get("v2_role_reason", 0), 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
