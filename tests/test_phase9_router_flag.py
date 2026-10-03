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


class TestPhase9RouterFlag(unittest.TestCase):
    def test_use_agent_coordinator_defaults_false(self):
        import runtime_config as rc
        with patch.dict("os.environ", {}, clear=False):
            self.assertFalse(rc.use_agent_coordinator({}))

    def test_use_agent_coordinator_from_env(self):
        import runtime_config as rc
        with patch.dict("os.environ", {"USE_AGENT_COORDINATOR": "true"}, clear=False):
            self.assertTrue(rc.use_agent_coordinator({}))

    def test_use_agent_coordinator_from_config(self):
        import runtime_config as rc
        cfg = {"channels": {"telegram": {"useAgentCoordinator": True}}}
        with patch.dict("os.environ", {"USE_AGENT_COORDINATOR": ""}, clear=False):
            self.assertTrue(rc.use_agent_coordinator(cfg))

    def test_route_free_text_flag_disabled_uses_legacy_path(self):
        import telegram_router as tr
        with patch.object(tr, "use_agent_coordinator", return_value=False):
            with patch.object(tr, "handle_non_skill", return_value="legacy") as mock_legacy:
                result = tr._route_free_text_query("hola", chat_id="1")
        self.assertEqual(result["response"], "legacy")
        self.assertFalse(result["used_coordinator"])
        mock_legacy.assert_called_once()

    def test_route_free_text_flag_enabled_uses_coordinator_when_handled(self):
        import telegram_router as tr
        fake_result = {"handled": True, "aggregated": "coord", "strategy": "first_wins", "correlation_id": "deadbeef"}
        with patch.object(tr, "use_agent_coordinator", return_value=True):
            with patch.dict("sys.modules", {"agent_coordinator": type("M", (), {"route": staticmethod(lambda _q: fake_result)})}):
                with patch.object(tr, "handle_non_skill", return_value="legacy") as mock_legacy:
                    result = tr._route_free_text_query("hola", chat_id="1")
        self.assertEqual(result["response"], "coord")
        self.assertTrue(result["used_coordinator"])
        mock_legacy.assert_not_called()

    def test_route_free_text_flag_enabled_falls_back_when_unhandled(self):
        import telegram_router as tr
        fake_result = {"handled": False, "aggregated": None, "strategy": "skill_fallback"}
        with patch.object(tr, "use_agent_coordinator", return_value=True):
            with patch.dict("sys.modules", {"agent_coordinator": type("M", (), {"route": staticmethod(lambda _q: fake_result)})}):
                with patch.object(tr, "handle_non_skill", return_value="legacy") as mock_legacy:
                    result = tr._route_free_text_query("hola", chat_id="1")
        self.assertEqual(result["response"], "legacy")
        self.assertFalse(result["used_coordinator"])
        mock_legacy.assert_called_once()

    def test_route_free_text_flag_enabled_falls_back_on_exception(self):
        import telegram_router as tr
        class Broken:
            @staticmethod
            def route(_q):
                raise RuntimeError("boom")
        with patch.object(tr, "use_agent_coordinator", return_value=True):
            with patch.dict("sys.modules", {"agent_coordinator": Broken}):
                with patch.object(tr, "handle_non_skill", return_value="legacy") as mock_legacy:
                    result = tr._route_free_text_query("hola", chat_id="1")
        self.assertEqual(result["response"], "legacy")
        self.assertFalse(result["used_coordinator"])
        mock_legacy.assert_called_once()

    def test_intercepted_skill_bypasses_coordinator_branch(self):
        import telegram_router as tr
        update = {"message": {"chat": {"id": "1"}, "text": "clima hoy"}}
        with patch.object(tr, "_get_allowed_chat_ids", return_value={"1"}):
            with patch.object(tr, "_append_history"):
                with patch.object(tr, "send_typing"):
                    with patch.object(tr, "should_intercept", return_value=(True, "skill:weather")):
                        with patch.object(tr, "execute_intercepted_skill", return_value=("dato", {})):
                            with patch.object(tr, "_humanize_response", return_value="respuesta"):
                                with patch.object(tr, "send_message"):
                                    with patch.object(tr, "_route_free_text_query") as mock_route:
                                        tr._process_update("token", update, {"1"})
        mock_route.assert_not_called()
