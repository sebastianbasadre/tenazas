#!/usr/bin/env python3
"""
Tests for model_router.py — Hardware-aware model selection + Router V2.

Covers:
- Backend detection (ping success/failure)
- V1: Model selection by task (system, reason, embed, analysis, general)
- GPU vs CPU model filtering, VRAM constraints, prefer_fast flag
- Fallback when no backends available, CPU-only small model constraint
- route() convenience function, get_model_info / list_models helpers
- Legacy model exclusion from GPU selection
- V2: classify_task() heuristic classification
- V2: route_by_role() role-based routing with fallback chains

Corre: python3 tests/test_model_router.py
"""

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/tenazas")
from runtime import model_router as mr


def _mock_backends(notebook=True, nas=True):
    """Helper: mock detect_backends with controllable availability."""
    def fake_detect():
        return {
            "notebook_gpu": {
                "available": notebook,
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
    return fake_detect


class TestPingBackend(unittest.TestCase):
    """Backend reachability checks."""

    @patch("runtime.model_router.subprocess.run")
    def test_ping_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        self.assertTrue(mr._ping_backend("notebook_gpu"))

    @patch("runtime.model_router.subprocess.run")
    def test_ping_failure(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1)
        self.assertFalse(mr._ping_backend("notebook_gpu"))

    @patch("runtime.model_router.subprocess.run")
    def test_ping_timeout(self, mock_run):
        from subprocess import TimeoutExpired
        mock_run.side_effect = TimeoutExpired(cmd="curl", timeout=10)
        self.assertFalse(mr._ping_backend("notebook_gpu"))

    def test_ping_unknown_backend(self):
        self.assertFalse(mr._ping_backend("nonexistent"))

    @patch("runtime.model_router.subprocess.run")
    def test_ping_uses_curl_with_timeout(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0)
        mr._ping_backend("notebook_gpu")
        args = mock_run.call_args[0][0]
        self.assertEqual(args[0], "curl")
        self.assertIn("--max-time", args)


class TestDetectBackends(unittest.TestCase):
    """Backend detection returns availability map."""

    @patch("runtime.model_router._ping_backend")
    @patch("runtime.model_router._log")
    def test_both_available(self, mock_log, mock_ping):
        mock_ping.return_value = True
        result = mr.detect_backends()
        self.assertTrue(result["notebook_gpu"]["available"])
        self.assertTrue(result["nas_cpu"]["available"])

    @patch("runtime.model_router._ping_backend")
    @patch("runtime.model_router._log")
    def test_notebook_offline(self, mock_log, mock_ping):
        mock_ping.side_effect = lambda bid: bid != "notebook_gpu"
        result = mr.detect_backends()
        self.assertFalse(result["notebook_gpu"]["available"])
        self.assertTrue(result["nas_cpu"]["available"])

    @patch("runtime.model_router._ping_backend")
    @patch("runtime.model_router._log")
    def test_returns_all_backends(self, mock_log, mock_ping):
        mock_ping.return_value = True
        result = mr.detect_backends()
        self.assertIn("notebook_gpu", result)
        self.assertIn("nas_cpu", result)

    @patch("runtime.model_router._ping_backend")
    @patch("runtime.model_router._log")
    def test_logs_detection(self, mock_log, mock_ping):
        mock_ping.return_value = True
        mr.detect_backends()
        mock_log.assert_called_once()
        entry = mock_log.call_args[0][0]
        self.assertEqual(entry["action"], "detect_backends")


class TestSelectModelGPU(unittest.TestCase):
    """Model selection with GPU backend available."""

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_system_task_selects_capable_model(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=True, nas=True)
        result = mr.select_model("system")
        # V1 selects from non-legacy models that fit in 24GB VRAM (RTX 3090)
        self.assertIn(result["model"], ("qwen3:4b", "qwen3:8b", "qwen2.5:7b"))
        self.assertEqual(result["backend"], "notebook_gpu")

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_reason_task_selects_reasoning_model(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=True, nas=True)
        result = mr.select_model("reason")
        # With 24GB VRAM, V1 may pick any model with reasoning capability
        model_info = mr.MODEL_CATALOG.get(result["model"], {})
        self.assertIn("reasoning", model_info.get("capabilities", []))

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_embed_task_selects_nomic(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=True, nas=True)
        result = mr.select_model("embed")
        self.assertEqual(result["model"], "nomic-embed-text")

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_prefer_fast_selects_smaller(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=True, nas=True)
        result = mr.select_model("general", prefer_fast=True)
        self.assertIsNotNone(result.get("model"))

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_result_has_required_keys(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=True, nas=True)
        result = mr.select_model("system")
        self.assertIn("model", result)
        self.assertIn("backend", result)
        self.assertIn("url", result)
        self.assertIn("reason", result)


class TestSelectModelCPU(unittest.TestCase):
    """Model selection with only CPU backend."""

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_cpu_only_selects_small_model(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=False, nas=True)
        result = mr.select_model("system")
        # CPU should only use small models (<=3.5 VRAM)
        model_info = mr.MODEL_CATALOG.get(result["model"], {})
        self.assertLessEqual(model_info.get("vram_gb", 0), 3.5)

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_cpu_fallback_phi4_mini(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=False, nas=True)
        result = mr.select_model("reason")
        # Even for reasoning, CPU should pick small model
        self.assertIsNotNone(result.get("model"))
        self.assertEqual(result["backend"], "nas_cpu")

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_cpu_uses_nas_url(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=False, nas=True)
        result = mr.select_model("system")
        self.assertIn("127.0.0.1", result["url"])


class TestSelectModelOffline(unittest.TestCase):
    """No backends available."""

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_no_backends_returns_empty(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=False, nas=False)
        result = mr.select_model("system")
        self.assertEqual(result, {})

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_no_backends_logs_failure(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=False, nas=False)
        mr.select_model("system")
        # Should have logged the no_backend_available event
        logged = False
        for call in mock_log.call_args_list:
            entry = call[0][0]
            if entry.get("result") == "no_backend_available":
                logged = True
        self.assertTrue(logged)


class TestRoute(unittest.TestCase):
    """route() convenience function."""

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_route_returns_tuple(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=True, nas=True)
        backend, model, url = mr.route("system")
        self.assertIsNotNone(backend)
        self.assertIsNotNone(model)
        self.assertIsNotNone(url)

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_route_offline_returns_nones(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=False, nas=False)
        backend, model, url = mr.route("system")
        self.assertIsNone(backend)
        self.assertIsNone(model)
        self.assertIsNone(url)

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_route_embed(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=True, nas=True)
        backend, model, url = mr.route("embed")
        self.assertEqual(model, "nomic-embed-text")


class TestLegacyModels(unittest.TestCase):
    """Legacy models excluded from GPU selection."""

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_legacy_models_not_selected_on_gpu(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=True, nas=True)
        for task in mr.TASK_REQUIREMENTS:
            result = mr.select_model(task)
            if result and result["backend"] == "notebook_gpu":
                info = mr.MODEL_CATALOG.get(result["model"], {})
                self.assertFalse(info.get("legacy", False),
                                 f"Legacy model {result['model']} selected for {task} on GPU")


class TestHelpers(unittest.TestCase):
    """get_model_info and list_models."""

    def test_get_known_model(self):
        info = mr.get_model_info("qwen2.5:7b")
        self.assertEqual(info["params"], "7B")
        self.assertIn("general", info["capabilities"])

    def test_get_unknown_model(self):
        info = mr.get_model_info("nonexistent")
        self.assertEqual(info, {})

    def test_list_models_excludes_legacy_by_default(self):
        models = mr.list_models()
        for name, info in models.items():
            self.assertFalse(info.get("legacy", False))

    def test_list_models_includes_legacy(self):
        models = mr.list_models(include_legacy=True)
        legacy = [n for n, i in models.items() if i.get("legacy")]
        self.assertGreater(len(legacy), 0)

    def test_list_models_default_excludes_legacy(self):
        models = mr.list_models()
        for name, info in models.items():
            self.assertFalse(info.get("legacy", False))


class TestCatalogIntegrity(unittest.TestCase):
    """Model catalog data integrity."""

    def test_all_models_have_required_fields(self):
        for name, info in mr.MODEL_CATALOG.items():
            self.assertIn("params", info, f"{name} missing params")
            self.assertIn("vram_gb", info, f"{name} missing vram_gb")
            self.assertIn("use", info, f"{name} missing use")
            self.assertIn("capabilities", info, f"{name} missing capabilities")
            self.assertIn("role", info, f"{name} missing role")

    def test_all_roles_have_valid_values(self):
        for name, info in mr.MODEL_CATALOG.items():
            self.assertIn(info["role"], mr.VALID_ROLES,
                          f"{name} has invalid role '{info['role']}'")

    def test_all_backends_have_required_fields(self):
        for bid, info in mr.BACKENDS.items():
            self.assertIn("url", info, f"{bid} missing url")
            self.assertIn("type", info, f"{bid} missing type")
            self.assertIn("priority", info, f"{bid} missing priority")

    def test_notebook_higher_priority_than_nas(self):
        self.assertLess(
            mr.BACKENDS["notebook_gpu"]["priority"],
            mr.BACKENDS["nas_cpu"]["priority"],
        )

    def test_role_map_models_exist_in_catalog(self):
        for role, chain in mr.ROLE_MAP.items():
            for model in chain:
                self.assertIn(model, mr.MODEL_CATALOG,
                              f"ROLE_MAP[{role}] references unknown model {model}")

    def test_homelab_models_present(self):
        """Preferred local catalog only contains the compact active target set."""
        expected = {"qwen3:4b", "qwen3:8b", "qwen2.5:7b",
                    "qwen2.5-coder:7b", "qwen3-vl:4b", "nomic-embed-text"}
        actual = {k for k, v in mr.MODEL_CATALOG.items()
                  if not v.get("legacy") and not v.get("cloud")}
        self.assertEqual(expected, actual)


# ── V2: Task Classification Tests ──────────────��────────────────


class TestClassifyTask(unittest.TestCase):
    """Heuristic task classification into roles."""

    def test_default_is_fast(self):
        self.assertEqual(mr.classify_task("hola que tal"), "fast")

    def test_explicit_role_flag(self):
        self.assertEqual(mr.classify_task("anything", {"role": "code"}), "code")

    def test_explicit_role_all_valid(self):
        for role in mr.VALID_ROLES:
            self.assertEqual(mr.classify_task("x", {"role": role}), role)

    def test_invalid_role_flag_ignored(self):
        self.assertEqual(mr.classify_task("hello", {"role": "nonexistent"}), "fast")

    def test_vision_flag(self):
        self.assertEqual(mr.classify_task("describe this", {"has_image": True}), "vision")

    def test_attachment_flag(self):
        self.assertEqual(mr.classify_task("what is this", {"has_attachment": True}), "vision")

    def test_embed_flag(self):
        self.assertEqual(mr.classify_task("embed this text", {"embed": True}), "embed")

    def test_code_keyword_refactor(self):
        self.assertEqual(mr.classify_task("refactor the login function"), "code")

    def test_code_keyword_debug(self):
        self.assertEqual(mr.classify_task("debug this function"), "code")

    def test_code_backticks(self):
        self.assertEqual(mr.classify_task("fix this:\n```python\ndef foo(): pass\n```"), "code")

    def test_code_class_definition(self):
        self.assertEqual(mr.classify_task("class MyRouter:"), "code")

    def test_code_high_special_chars(self):
        code = "if (x > 0) { return foo(x); } else { return bar(y); }"
        self.assertEqual(mr.classify_task(code), "code")

    def test_reason_light_stays_fast(self):
        # Short simple reasoning queries stay on fast to avoid VRAM swaps
        self.assertEqual(mr.classify_task("analiza por que subio la latencia"), "fast")
        self.assertEqual(mr.classify_task("evalua el rendimiento del sistema"), "fast")
        self.assertEqual(mr.classify_task("why did the container keep restarting over and over"), "fast")

    def test_reason_deep_uses_deepseek(self):
        # Complex reasoning with deep keywords → reason role
        self.assertEqual(mr.classify_task("investiga step by step por que falla el deploy"), "reason")
        self.assertEqual(mr.classify_task("analiza en detalle profundo los logs de error del ultimo mes"), "reason")
        self.assertEqual(mr.classify_task("diagnostico completo del root cause del crash loop"), "reason")

    def test_reason_long_prompt_uses_deepseek(self):
        # Long reasoning prompts (>300 chars) go to deepseek even without deep keywords
        long_reason = "evalua " + "el rendimiento del sistema con todos los datos " * 8
        self.assertEqual(mr.classify_task(long_reason), "reason")

    def test_reason_long_question(self):
        long = "quiero saber " + "detalle " * 80 + "?"
        self.assertEqual(mr.classify_task(long), "reason")

    def test_heavy_keyword_critical(self):
        self.assertEqual(mr.classify_task("this is a critical production issue"), "heavy")

    def test_heavy_keyword_deploy(self):
        self.assertEqual(mr.classify_task("deploy to production now"), "heavy")

    def test_heavy_critical_flag(self):
        self.assertEqual(mr.classify_task("do this", {"critical": True}), "heavy")

    def test_heavy_very_long_prompt(self):
        long = "context " * 300  # > 2000 chars
        self.assertEqual(mr.classify_task(long), "heavy")

    def test_code_beats_reason_when_code_present(self):
        """Code keywords take priority over reasoning keywords."""
        self.assertEqual(mr.classify_task("refactor and analyze this module"), "code")

    def test_empty_prompt_is_fast(self):
        self.assertEqual(mr.classify_task(""), "fast")

    def test_spanish_genera_codigo(self):
        self.assertEqual(mr.classify_task("genera codigo para parsear JSON"), "code")


# ── V2: Route-by-Role Tests ───���─────────────────────────────────


class TestRouteByRole(unittest.TestCase):
    """Role-based routing with fallback chains."""

    def setUp(self):
        mr.reset_guardrails()

    def tearDown(self):
        mr.reset_guardrails()

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_fast_selects_7b(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        result = mr.route_by_role("hola que tal")
        self.assertEqual(result["model"], "qwen3:8b")
        self.assertEqual(result["role"], "fast")
        self.assertTrue(result["fallback_used"])

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_reason_selects_deepseek(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        # Deep reasoning prompt triggers the larger local reasoning model
        result = mr.route_by_role("investiga step by step por que falla el deploy de produccion")
        self.assertEqual(result["model"], "qwen3:8b")
        self.assertEqual(result["role"], "reason")

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_code_selects_coder(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        result = mr.route_by_role("refactor the auth module")
        self.assertEqual(result["model"], "qwen3:8b")
        self.assertEqual(result["role"], "code")
        self.assertTrue(result["fallback_used"])

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_vision_selects_vl(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        result = mr.route_by_role("describe this", flags={"has_image": True})
        self.assertEqual(result["model"], "qwen3:8b")
        self.assertEqual(result["role"], "vision")
        self.assertTrue(result["fallback_used"])

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_heavy_selects_32b(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        result = mr.route_by_role("critical production deploy")
        self.assertEqual(result["model"], "qwen3:8b")
        self.assertEqual(result["role"], "heavy")

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_embed_selects_nomic(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        result = mr.route_by_role("embed this", flags={"embed": True})
        self.assertEqual(result["model"], "nomic-embed-text")
        self.assertEqual(result["role"], "embed")

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_no_backend_returns_empty(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=False, nas=False)()
        result = mr.route_by_role("hello")
        self.assertEqual(result, {})

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_cpu_only_fallback_to_small(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=False, nas=True)()
        result = mr.route_by_role("analiza los logs")
        # CPU can only run small models
        model_info = mr.MODEL_CATALOG.get(result["model"], {})
        self.assertLessEqual(model_info.get("vram_gb", 0), 3.5)
        self.assertEqual(result["backend"], "nas_cpu")

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_backward_compat_default_fast(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        result = mr.route_by_role("random unclassified stuff")
        self.assertEqual(result["role"], "fast")
        self.assertEqual(result["model"], "qwen3:8b")

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_pre_fetched_backend_status(self, mock_detect, mock_log):
        pre_fetched = _mock_backends(notebook=True, nas=True)()
        result = mr.route_by_role("hello", backend_status=pre_fetched)
        self.assertEqual(result["model"], "qwen3:8b")
        mock_detect.assert_not_called()  # Should not re-detect

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_no_vram_swap_on_rtx3090(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        result = mr.route_by_role("critical production issue")
        # 32b (20GB) on 24GB RTX 3090 → fits, no swap
        self.assertFalse(result["vram_swap"])

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_no_vram_swap_for_small_model(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        result = mr.route_by_role("hola")
        # 7b (4.7GB) on 24GB RTX 3090 → no swap
        self.assertFalse(result["vram_swap"])

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_log_entry_has_required_fields(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        mr.route_by_role("test prompt")
        # Find the route_v2 log entry
        route_log = None
        for call in mock_log.call_args_list:
            entry = call[0][0]
            if entry.get("action") == "route_v2":
                route_log = entry
                break
        self.assertIsNotNone(route_log)
        self.assertIn("task_type", route_log)
        self.assertIn("model_chosen", route_log)
        self.assertIn("role", route_log)
        self.assertIn("fallback_used", route_log)
        self.assertIn("vram_swap", route_log)
        self.assertIn("signals", route_log)

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_result_has_required_keys(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        result = mr.route_by_role("test")
        for key in ("model", "backend", "url", "role", "classified_as",
                     "fallback_used", "degraded", "degrade_reason",
                     "reason", "vram_swap", "signals"):
            self.assertIn(key, result, f"Missing key: {key}")

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_explicit_role_override(self, mock_detect, mock_log):
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        result = mr.route_by_role("hello", flags={"role": "reason"})
        self.assertEqual(result["model"], "qwen3:8b")
        self.assertEqual(result["role"], "reason")


# ── V2: Guardrail Tests ────────────────────────────────────────


class TestGuardrails(unittest.TestCase):
    """Guardrails: cooldown, frequency limiting, latency degradation."""

    def setUp(self):
        mr.reset_guardrails()

    def tearDown(self):
        mr.reset_guardrails()

    def test_no_guardrail_on_fast(self):
        role, degraded, reason = mr.apply_guardrails("fast")
        self.assertEqual(role, "fast")
        self.assertFalse(degraded)

    def test_no_guardrail_on_reason(self):
        role, degraded, reason = mr.apply_guardrails("reason")
        self.assertEqual(role, "reason")
        self.assertFalse(degraded)

    def test_no_guardrail_on_vision(self):
        role, degraded, reason = mr.apply_guardrails("vision")
        self.assertEqual(role, "vision")
        self.assertFalse(degraded)

    def test_heavy_passes_when_clean(self):
        role, degraded, reason = mr.apply_guardrails("heavy")
        self.assertEqual(role, "heavy")
        self.assertFalse(degraded)

    def test_code_passes_when_clean(self):
        role, degraded, reason = mr.apply_guardrails("code")
        self.assertEqual(role, "code")
        self.assertFalse(degraded)

    def test_force_heavy_bypasses_guardrails(self):
        """force_heavy flag bypasses all guardrails."""
        import time
        # Simulate cooldown active
        mr._guardrail_state["last_heavy_ts"] = time.monotonic()
        mr._guardrail_state["last_heavy_model"] = "qwen3:8b"
        role, degraded, reason = mr.apply_guardrails("heavy", {"force_heavy": True})
        self.assertEqual(role, "heavy")
        self.assertFalse(degraded)

    def test_cooldown_degrades_heavy_to_reason(self):
        import time
        mr._guardrail_state["last_heavy_ts"] = time.monotonic()
        mr._guardrail_state["last_heavy_model"] = "qwen3:8b"
        role, degraded, reason = mr.apply_guardrails("heavy")
        self.assertEqual(role, "reason")
        self.assertTrue(degraded)
        self.assertIn("cooldown", reason)

    def test_cooldown_degrades_code_to_fast(self):
        import time
        mr._guardrail_state["last_heavy_ts"] = time.monotonic()
        mr._guardrail_state["last_heavy_model"] = "qwen2.5-coder:7b"
        role, degraded, reason = mr.apply_guardrails("code")
        self.assertEqual(role, "fast")
        self.assertTrue(degraded)
        self.assertIn("cooldown", reason)

    def test_cooldown_expires(self):
        import time
        mr._guardrail_state["last_heavy_ts"] = time.monotonic() - mr.GUARDRAIL_COOLDOWN - 1
        mr._guardrail_state["last_heavy_model"] = "qwen3:8b"
        role, degraded, reason = mr.apply_guardrails("heavy")
        self.assertEqual(role, "heavy")
        self.assertFalse(degraded)

    def test_frequency_limit_degrades(self):
        import time
        now = time.monotonic()
        # Fill up the window
        mr._guardrail_state["heavy_timestamps"] = [
            now - 10, now - 5, now - 1,
        ]
        role, degraded, reason = mr.apply_guardrails("heavy")
        self.assertEqual(role, "reason")
        self.assertTrue(degraded)
        self.assertIn("frequency", reason)

    def test_frequency_under_limit_passes(self):
        import time
        now = time.monotonic()
        mr._guardrail_state["heavy_timestamps"] = [now - 10, now - 5]
        role, degraded, reason = mr.apply_guardrails("heavy")
        self.assertEqual(role, "heavy")
        self.assertFalse(degraded)

    def test_latency_threshold_degrades(self):
        mr._guardrail_state["last_heavy_latency"] = 45.0  # > 30s threshold
        role, degraded, reason = mr.apply_guardrails("heavy")
        self.assertEqual(role, "reason")
        self.assertTrue(degraded)
        self.assertIn("latency", reason)

    def test_latency_under_threshold_passes(self):
        mr._guardrail_state["last_heavy_latency"] = 15.0
        role, degraded, reason = mr.apply_guardrails("heavy")
        self.assertEqual(role, "heavy")
        self.assertFalse(degraded)

    def test_vram_swap_storm_degrades(self):
        import time
        now = time.monotonic()
        mr._guardrail_state["vram_swaps_window"] = [now - 10, now - 5, now - 1]
        role, degraded, reason = mr.apply_guardrails("heavy")
        self.assertEqual(role, "reason")
        self.assertTrue(degraded)
        self.assertIn("vram_swaps", reason)

    def test_record_heavy_usage_tracks(self):
        mr.record_heavy_usage("qwen3:8b", latency=10.0, vram_swap=True)
        self.assertEqual(len(mr._guardrail_state["heavy_timestamps"]), 1)
        self.assertEqual(mr._guardrail_state["last_heavy_model"], "qwen3:8b")
        self.assertEqual(mr._guardrail_state["last_heavy_latency"], 10.0)
        self.assertEqual(len(mr._guardrail_state["vram_swaps_window"]), 1)

    def test_record_non_heavy_model_ignored(self):
        mr.record_heavy_usage("qwen2.5:7b", latency=5.0)
        self.assertEqual(len(mr._guardrail_state["heavy_timestamps"]), 0)
        self.assertIsNone(mr._guardrail_state["last_heavy_model"])

    def test_reset_clears_state(self):
        mr.record_heavy_usage("qwen3:8b", latency=10.0, vram_swap=True)
        mr.reset_guardrails()
        self.assertEqual(len(mr._guardrail_state["heavy_timestamps"]), 0)
        self.assertIsNone(mr._guardrail_state["last_heavy_model"])
        self.assertEqual(mr._guardrail_state["last_heavy_latency"], 0.0)

    def test_cleanup_removes_expired(self):
        import time
        now = time.monotonic()
        mr._guardrail_state["heavy_timestamps"] = [
            now - mr.GUARDRAIL_WINDOW - 100,  # expired
            now - 10,  # valid
        ]
        mr._guardrail_cleanup(now)
        self.assertEqual(len(mr._guardrail_state["heavy_timestamps"]), 1)


class TestRouteByRoleWithGuardrails(unittest.TestCase):
    """Integration: route_by_role respects guardrails."""

    def setUp(self):
        mr.reset_guardrails()

    def tearDown(self):
        mr.reset_guardrails()

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_heavy_degraded_after_cooldown(self, mock_detect, mock_log):
        import time
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        # First call: heavy goes through
        result1 = mr.route_by_role("critical production deploy")
        self.assertEqual(result1["model"], "qwen3:8b")
        # Simulate cooldown active (record_heavy_usage was called internally)
        # Second call: should degrade
        result2 = mr.route_by_role("critical production issue")
        self.assertTrue(result2["degraded"])
        self.assertEqual(result2["classified_as"], "heavy")

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_force_heavy_bypasses_in_route(self, mock_detect, mock_log):
        import time
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        # Trigger cooldown
        mr._guardrail_state["last_heavy_ts"] = time.monotonic()
        mr._guardrail_state["last_heavy_model"] = "qwen3:8b"
        result = mr.route_by_role("critical", flags={"role": "heavy", "force_heavy": True})
        self.assertEqual(result["model"], "qwen3:8b")
        self.assertFalse(result["degraded"])

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_degraded_result_has_signals(self, mock_detect, mock_log):
        import time
        mock_detect.return_value = _mock_backends(notebook=True, nas=True)()
        mr._guardrail_state["last_heavy_ts"] = time.monotonic()
        mr._guardrail_state["last_heavy_model"] = "qwen3:8b"
        result = mr.route_by_role("critical production deploy")
        self.assertTrue(any("guardrail:" in s for s in result["signals"]))


if __name__ == "__main__":
    unittest.main()
