#!/usr/bin/env python3
"""
Tests for model_router.py — Hardware-aware model selection.

Covers:
- Backend detection (ping success/failure)
- Model selection by task (system, reason, embed, analysis, general)
- GPU vs CPU model filtering
- VRAM constraints
- prefer_fast flag
- Fallback when no backends available
- CPU-only small model constraint
- route() convenience function
- get_model_info / list_models helpers
- Future model exclusion

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
    def test_system_task_selects_small_model(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=True, nas=True)
        result = mr.select_model("system")
        self.assertIn(result["model"], ("qwen2.5:3b", "phi4-mini"))
        self.assertEqual(result["backend"], "notebook_gpu")

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_reason_task_selects_7b(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=True, nas=True)
        result = mr.select_model("reason")
        self.assertIn(result["model"], ("qwen2.5:7b", "qwen3:8b"))

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


class TestFutureModels(unittest.TestCase):
    """Future models excluded from selection."""

    @patch("runtime.model_router._log")
    @patch("runtime.model_router.detect_backends")
    def test_future_models_not_selected(self, mock_detect, mock_log):
        mock_detect.side_effect = _mock_backends(notebook=True, nas=True)
        for task in mr.TASK_REQUIREMENTS:
            result = mr.select_model(task)
            if result:
                info = mr.MODEL_CATALOG.get(result["model"], {})
                self.assertFalse(info.get("future", False),
                                 f"Future model {result['model']} selected for {task}")


class TestHelpers(unittest.TestCase):
    """get_model_info and list_models."""

    def test_get_known_model(self):
        info = mr.get_model_info("qwen2.5:3b")
        self.assertEqual(info["params"], "3B")
        self.assertIn("system", info["capabilities"])

    def test_get_unknown_model(self):
        info = mr.get_model_info("nonexistent")
        self.assertEqual(info, {})

    def test_list_models_excludes_future(self):
        models = mr.list_models(include_future=False)
        for name, info in models.items():
            self.assertFalse(info.get("future", False))

    def test_list_models_includes_future(self):
        models = mr.list_models(include_future=True)
        future = [n for n, i in models.items() if i.get("future")]
        self.assertGreater(len(future), 0)

    def test_list_models_default_no_future(self):
        models = mr.list_models()
        for name, info in models.items():
            self.assertFalse(info.get("future", False))


class TestCatalogIntegrity(unittest.TestCase):
    """Model catalog data integrity."""

    def test_all_models_have_required_fields(self):
        for name, info in mr.MODEL_CATALOG.items():
            self.assertIn("params", info, f"{name} missing params")
            self.assertIn("vram_gb", info, f"{name} missing vram_gb")
            self.assertIn("use", info, f"{name} missing use")
            self.assertIn("capabilities", info, f"{name} missing capabilities")

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


if __name__ == "__main__":
    unittest.main()
