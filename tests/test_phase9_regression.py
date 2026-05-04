#!/usr/bin/env python3
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if _C.exists():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))


class TestPhase9Regression(unittest.TestCase):
    def test_skill_queries_remain_skill_intercepts(self):
        import telegram_router as tr
        cases = [
            ("cuántos emails tengo", "email"),
            ("cómo está el clima", "weather"),
            ("qué tengo en la agenda", "calendar"),
        ]
        for text, expected in cases:
            intercept, category = tr.should_intercept(text)
            self.assertTrue(intercept)
            self.assertIn(expected, category)

    def test_single_domain_query_is_llm_with_flag_off(self):
        import telegram_router as tr
        with patch.object(tr, "use_agent_coordinator", return_value=False):
            with patch.object(tr, "handle_non_skill", return_value="legacy"):
                result = tr._route_free_text_query("containers down", chat_id="1")
        self.assertFalse(result["used_coordinator"])
        self.assertEqual(result["route_path"], "llm_chat")

    def test_single_domain_query_can_use_coordinator_with_flag_on(self):
        import telegram_router as tr
        fake = {"handled": True, "aggregated": "📡 Infra: down", "strategy": "first_wins", "correlation_id": "abcd1234"}
        with patch.object(tr, "use_agent_coordinator", return_value=True):
            with patch.dict("sys.modules", {"agent_coordinator": type("M", (), {"route": staticmethod(lambda _q: fake)})}):
                result = tr._route_free_text_query("containers down", chat_id="1")
        self.assertTrue(result["used_coordinator"])
        self.assertEqual(result["strategy"], "first_wins")

    def test_multi_domain_query_can_use_parallel_coordinator(self):
        import telegram_router as tr
        fake = {"handled": True, "aggregated": "📡 Infra: ok\n💰 Finance: ok", "strategy": "parallel", "correlation_id": "abcd1234"}
        with patch.object(tr, "use_agent_coordinator", return_value=True):
            with patch.dict("sys.modules", {"agent_coordinator": type("M", (), {"route": staticmethod(lambda _q: fake)})}):
                result = tr._route_free_text_query("como esta todo el sistema y mis finanzas", chat_id="1")
        self.assertTrue(result["used_coordinator"])
        self.assertEqual(result["strategy"], "parallel")

    def test_nonsense_query_safely_falls_back_when_flag_on(self):
        import telegram_router as tr
        fake = {"handled": False, "aggregated": None, "strategy": "skill_fallback", "source": "llm_needed"}
        with patch.object(tr, "use_agent_coordinator", return_value=True):
            with patch.dict("sys.modules", {"agent_coordinator": type("M", (), {"route": staticmethod(lambda _q: fake)})}):
                with patch.object(tr, "handle_non_skill", return_value="legacy fallback"):
                    result = tr._route_free_text_query("xyzzy nonsense", chat_id="1")
        self.assertFalse(result["used_coordinator"])
        self.assertEqual(result["response"], "legacy fallback")

    def test_agent_exception_under_flag_on_does_not_drop_reply(self):
        import telegram_router as tr
        class Broken:
            @staticmethod
            def route(_q):
                raise RuntimeError("explode")
        with patch.object(tr, "use_agent_coordinator", return_value=True):
            with patch.dict("sys.modules", {"agent_coordinator": Broken}):
                with patch.object(tr, "handle_non_skill", return_value="safe fallback"):
                    result = tr._route_free_text_query("infra query", chat_id="1")
        self.assertEqual(result["response"], "safe fallback")
        self.assertFalse(result["used_coordinator"])
