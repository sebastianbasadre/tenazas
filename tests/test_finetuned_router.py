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
    """Mock Gemma local returning a tool."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"choices": [{"message": {"content": json.dumps({"tool": tool})}}]}
    return patch("finetuned_router.requests.post", return_value=mock_resp)


def mock_gemma_fail():
    """Mock Gemma failing (both gemma and finetuned use requests.post)."""
    return patch("finetuned_router.requests.post", side_effect=Exception("timeout"))


def _make_side_effect(gemma_tool=None, finetuned_tool=None, gemma_fail=False, finetuned_fail=False):
    """Create a side_effect for requests.post that routes by URL."""
    def side_effect(url, **kwargs):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        if "11434" in str(url) and "v1/chat" in str(url):
            # Gemma local
            if gemma_fail:
                raise Exception("gemma timeout")
            mock_resp.json.return_value = {"choices": [{"message": {"content": json.dumps({"tool": gemma_tool})}}]}
        elif "11434" in str(url):
            # Finetuned Ollama
            if finetuned_fail:
                raise Exception("finetuned offline")
            mock_resp.json.return_value = {"response": json.dumps({"tool": finetuned_tool, "confidence": 0.95})}
        else:
            raise Exception("unknown url")
        return mock_resp
    return side_effect


def mock_gemma_then_finetuned(gemma_tool=None, finetuned_tool=None, gemma_fail=False, finetuned_fail=False):
    return patch("finetuned_router.requests.post",
                 side_effect=_make_side_effect(gemma_tool, finetuned_tool, gemma_fail, finetuned_fail))


# ── Tier 1: Gemma ────────────────────────────────────────────────

class TestGemma(unittest.TestCase):

    def test_gemma_returns_tool(self):
        with mock_gemma_then_finetuned(gemma_tool="weather"):
            r = router.route("como esta el clima")
        self.assertEqual(r["tool"], "weather")
        self.assertEqual(r["_parsed_by"], "gemma")

    def test_gemma_chat(self):
        with mock_gemma_then_finetuned(gemma_tool="chat"):
            r = router.route("hola che")
        self.assertEqual(r["tool"], "chat")


# ── Tier 2: Finetuned fallback ───────────────────────────────────

class TestFinetuned(unittest.TestCase):

    def test_finetuned_when_gemma_fails(self):
        with mock_gemma_then_finetuned(gemma_fail=True, finetuned_tool="email"):
            r = router.route("mis mails")
        self.assertEqual(r["tool"], "email")
        self.assertEqual(r["_parsed_by"], "finetuned")

    def test_finetuned_returns_tool(self):
        with mock_gemma_then_finetuned(gemma_fail=True, finetuned_tool="smart-home-on"):
            r = router.route("prende luces")
        self.assertEqual(r["tool"], "smart-home-on")


# ── Tier 3: Keyword fallback ────────────────────────────────────

class TestKeyword(unittest.TestCase):

    def test_keyword_when_all_fail(self):
        with mock_gemma_then_finetuned(gemma_fail=True, finetuned_fail=True):
            r = router.route("prende la luz")
        self.assertEqual(r["tool"], "smart-home-on")
        self.assertEqual(r["_parsed_by"], "keyword")

    def test_keyword_cinema(self):
        with mock_gemma_then_finetuned(gemma_fail=True, finetuned_fail=True):
            r = router.route("modo cine")
        self.assertEqual(r["tool"], "smart-home-cinema")

    def test_keyword_weather(self):
        with mock_gemma_then_finetuned(gemma_fail=True, finetuned_fail=True):
            r = router.route("temperatura hoy")
        self.assertEqual(r["tool"], "weather")

    def test_no_match(self):
        with mock_gemma_then_finetuned(gemma_fail=True, finetuned_fail=True):
            r = router.route("xyz abc 123")
        self.assertIsNone(r["tool"])
        self.assertEqual(r["_parsed_by"], "no_match")


# ── Route guarantees ─────────────────────────────────────────────

class TestRouteContract(unittest.TestCase):

    def test_always_has_required_keys(self):
        with mock_gemma_then_finetuned(gemma_tool="chat"):
            r = router.route("hola")
        for key in ("tool", "intent", "args", "_parsed_by", "_error"):
            self.assertIn(key, r)

    def test_never_raises(self):
        with mock_gemma_then_finetuned(gemma_fail=True, finetuned_fail=True):
            r = router.route("")
        self.assertIn("_parsed_by", r)

    def test_unknown_tool_rejected(self):
        with mock_gemma_then_finetuned(gemma_fail=True, finetuned_fail=True):
            r = router.route("volar")
        self.assertNotEqual(r.get("tool"), "fly_rocket")

    def test_intent_mapped(self):
        with mock_gemma_then_finetuned(gemma_tool="smart-home-on"):
            r = router.route("prende luces")
        self.assertEqual(r["intent"], "lights_on")


if __name__ == "__main__":
    unittest.main(verbosity=2)
