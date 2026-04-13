"""
test_finetuned_router.py
Tests for 3-tier router: Gemma → Finetuned → Keyword.
"""

import unittest
from unittest.mock import patch, MagicMock
import json
import sys
import os

_host_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "workspace", "runtime")
_container_path = "/home/tenazas/runtime"
sys.path.insert(0, _container_path if os.path.isdir(_container_path) else _host_path)
import finetuned_router as router


# ── Helpers ──────────────────────────────────────────────────────

def mock_gemma(tool: str):
    """Mock OpenRouter returning a tool."""
    response = json.dumps({"choices": [{"message": {"content": json.dumps({"tool": tool})}}]}).encode()
    mock_resp = MagicMock()
    mock_resp.read.return_value = response
    return patch("finetuned_router.urlopen", return_value=mock_resp)


def mock_gemma_fail():
    """Mock OpenRouter failing."""
    return patch("finetuned_router.urlopen", side_effect=Exception("timeout"))


def mock_finetuned(tool: str):
    """Mock Ollama finetuned model."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"response": json.dumps({"tool": tool, "confidence": 0.95})}
    return patch("finetuned_router.requests.post", return_value=mock_resp)


def mock_finetuned_fail():
    return patch("finetuned_router.requests.post", side_effect=Exception("offline"))


def mock_key(key="test-key"):
    return patch.object(router, "_load_openrouter_key", return_value=key)


# ── Tier 1: Gemma ────────────────────────────────────────────────

class TestGemma(unittest.TestCase):

    def test_gemma_returns_tool(self):
        with mock_key(), mock_gemma("weather"):
            r = router.route("como esta el clima")
        self.assertEqual(r["tool"], "weather")
        self.assertEqual(r["_parsed_by"], "gemma")

    def test_gemma_chat(self):
        with mock_key(), mock_gemma("chat"):
            r = router.route("hola che")
        self.assertEqual(r["tool"], "chat")

    def test_gemma_no_key_falls_to_finetuned(self):
        with patch.object(router, "_load_openrouter_key", return_value=""), \
             mock_finetuned("weather"):
            r = router.route("clima")
        self.assertEqual(r["_parsed_by"], "finetuned")


# ── Tier 2: Finetuned fallback ───────────────────────────────────

class TestFinetuned(unittest.TestCase):

    def test_finetuned_when_gemma_fails(self):
        with mock_key(), mock_gemma_fail(), mock_finetuned("email"):
            r = router.route("mis mails")
        self.assertEqual(r["tool"], "email")
        self.assertEqual(r["_parsed_by"], "finetuned")

    def test_finetuned_returns_tool(self):
        with mock_key(), mock_gemma_fail(), mock_finetuned("smart-home-on"):
            r = router.route("prende luces")
        self.assertEqual(r["tool"], "smart-home-on")


# ── Tier 3: Keyword fallback ────────────────────────────────────

class TestKeyword(unittest.TestCase):

    def test_keyword_when_all_fail(self):
        with mock_key(), mock_gemma_fail(), mock_finetuned_fail():
            r = router.route("prende la luz")
        self.assertEqual(r["tool"], "smart-home-on")
        self.assertEqual(r["_parsed_by"], "keyword")

    def test_keyword_cinema(self):
        with mock_key(), mock_gemma_fail(), mock_finetuned_fail():
            r = router.route("modo cine")
        self.assertEqual(r["tool"], "smart-home-cinema")

    def test_keyword_weather(self):
        with mock_key(), mock_gemma_fail(), mock_finetuned_fail():
            r = router.route("temperatura hoy")
        self.assertEqual(r["tool"], "weather")

    def test_no_match(self):
        with mock_key(), mock_gemma_fail(), mock_finetuned_fail():
            r = router.route("xyz abc 123")
        self.assertIsNone(r["tool"])
        self.assertEqual(r["_parsed_by"], "no_match")


# ── Route guarantees ─────────────────────────────────────────────

class TestRouteContract(unittest.TestCase):

    def test_always_has_required_keys(self):
        with mock_key(), mock_gemma("chat"):
            r = router.route("hola")
        for key in ("tool", "intent", "args", "_parsed_by", "_error"):
            self.assertIn(key, r)

    def test_never_raises(self):
        with mock_key(), mock_gemma_fail(), mock_finetuned_fail():
            r = router.route("")
        self.assertIn("_parsed_by", r)

    def test_unknown_tool_rejected(self):
        with mock_key(), mock_gemma("fly_rocket"), mock_finetuned_fail():
            r = router.route("volar")
        # Gemma returned unknown tool → falls to finetuned → keyword → no_match
        self.assertNotEqual(r["tool"], "fly_rocket")

    def test_intent_mapped(self):
        with mock_key(), mock_gemma("smart-home-on"):
            r = router.route("prende luces")
        self.assertEqual(r["intent"], "lights_on")


if __name__ == "__main__":
    unittest.main(verbosity=2)
