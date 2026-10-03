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


class TestPhase9E2E(unittest.TestCase):
    def _update(self, text: str) -> dict:
        return {"message": {"chat": {"id": "1"}, "text": text}}

    def test_process_update_uses_coordinator_reply_when_enabled_and_handled(self):
        import telegram_router as tr
        sent = []
        update = self._update("estado general del sistema")
        with patch.object(tr, "use_agent_coordinator", return_value=True):
            with patch.object(tr, "_append_history"):
                with patch.object(tr, "should_intercept", return_value=(False, "chat")):
                    with patch.object(tr, "_route_free_text_query", return_value={
                        "response": "📡 Infra: ok",
                        "used_coordinator": True,
                        "strategy": "parallel",
                    }):
                        with patch.object(tr, "send_typing"):
                            with patch.object(tr, "send_message", side_effect=lambda _t, _c, msg: sent.append(msg)):
                                tr._process_update("token", update, {"1"})
        self.assertEqual(sent[-1], "📡 Infra: ok")

    def test_process_update_falls_back_to_legacy_reply_when_unhandled(self):
        import telegram_router as tr
        sent = []
        update = self._update("consulta libre")
        with patch.object(tr, "use_agent_coordinator", return_value=True):
            with patch.object(tr, "_append_history"):
                with patch.object(tr, "should_intercept", return_value=(False, "chat")):
                    with patch.object(tr, "_route_free_text_query", return_value={
                        "response": "legacy reply",
                        "used_coordinator": False,
                        "route_path": "llm_chat",
                    }):
                        with patch.object(tr, "send_typing"):
                            with patch.object(tr, "send_message", side_effect=lambda _t, _c, msg: sent.append(msg)):
                                tr._process_update("token", update, {"1"})
        self.assertEqual(sent[-1], "legacy reply")

    def test_process_update_skill_intercept_bypasses_coordinator(self):
        import telegram_router as tr
        sent = []
        update = self._update("clima hoy")
        with patch.object(tr, "_append_history"):
            with patch.object(tr, "should_intercept", return_value=(True, "skill:weather")):
                with patch.object(tr, "execute_intercepted_skill", return_value=("dato crudo", {})):
                    with patch.object(tr, "_humanize_response", return_value="clima listo"):
                        with patch.object(tr, "send_typing"):
                            with patch.dict("sys.modules", {
                                "skill_feedback": type("M", (), {
                                    "send_with_feedback": staticmethod(lambda _t, _c, msg, *_args: sent.append(msg))
                                })
                            }):
                                with patch.object(tr, "_route_free_text_query") as mock_route:
                                    tr._process_update("token", update, {"1"})
        self.assertEqual(sent[-1], "clima listo")
        mock_route.assert_not_called()

    def test_route_free_text_query_falls_back_when_coordinator_raises(self):
        import telegram_router as tr
        class Broken:
            @staticmethod
            def route(_q):
                raise RuntimeError("boom")
        with patch.object(tr, "use_agent_coordinator", return_value=True):
            with patch.dict("sys.modules", {"agent_coordinator": Broken}):
                with patch.object(tr, "handle_non_skill", return_value="fallback"):
                    result = tr._route_free_text_query("consulta", chat_id="1")
        self.assertEqual(result["response"], "fallback")
        self.assertFalse(result["used_coordinator"])

    def test_route_free_text_query_exposes_coordinator_path_metadata(self):
        import telegram_router as tr
        fake = {
            "handled": True,
            "aggregated": "💰 Finance: ok",
            "strategy": "first_wins",
            "correlation_id": "cafefeed",
            "source": None,
        }
        with patch.object(tr, "use_agent_coordinator", return_value=True):
            with patch.dict("sys.modules", {"agent_coordinator": type("M", (), {"route": staticmethod(lambda _q: fake)})}):
                result = tr._route_free_text_query("finanzas", chat_id="1")
        self.assertTrue(result["used_coordinator"])
        self.assertEqual(result["strategy"], "first_wins")
        self.assertEqual(result["correlation_id"], "cafefeed")
