#!/usr/bin/env python3
"""
Tests for multitool_parser.py and orchestrator multitool integration.
Corre: python3 tests/test_multitool.py
"""

import sys
import json
import unittest
from unittest.mock import patch, MagicMock
from pathlib import Path

sys.path.insert(0, "/home/tenazas")
from runtime import multitool_parser as mp
from runtime import orchestrator as orch


# ── Parser Tests ─────────────────────────────────────────────────

class TestParseMultitoolResponse(unittest.TestCase):
    """XML parser: valid, invalid, edge cases."""

    def test_single_tool(self):
        r = mp.parse_multitool_response("<tools><tool>weather</tool></tools>")
        self.assertTrue(r["valid"])
        self.assertEqual(r["tools"], ["weather"])
        self.assertIsNone(r["error"])

    def test_two_tools(self):
        r = mp.parse_multitool_response(
            "<tools><tool>weather</tool><tool>calendar</tool></tools>")
        self.assertTrue(r["valid"])
        self.assertEqual(r["tools"], ["weather", "calendar"])

    def test_three_tools(self):
        r = mp.parse_multitool_response(
            "<tools><tool>weather</tool><tool>calendar</tool><tool>email</tool></tools>")
        self.assertTrue(r["valid"])
        self.assertEqual(r["tools"], ["weather", "calendar", "email"])

    def test_empty_tools(self):
        r = mp.parse_multitool_response("<tools></tools>")
        self.assertTrue(r["valid"])
        self.assertEqual(r["tools"], [])

    def test_whitespace_in_xml(self):
        r = mp.parse_multitool_response(
            "<tools>\n  <tool>weather</tool>\n  <tool>email</tool>\n</tools>")
        self.assertTrue(r["valid"])
        self.assertEqual(r["tools"], ["weather", "email"])

    def test_deduplicate_tools(self):
        r = mp.parse_multitool_response(
            "<tools><tool>weather</tool><tool>weather</tool></tools>")
        self.assertTrue(r["valid"])
        self.assertEqual(r["tools"], ["weather"])

    def test_dedup_preserves_order(self):
        r = mp.parse_multitool_response(
            "<tools><tool>email</tool><tool>weather</tool><tool>email</tool></tools>")
        self.assertTrue(r["valid"])
        self.assertEqual(r["tools"], ["email", "weather"])

    def test_unknown_tool_rejected(self):
        r = mp.parse_multitool_response("<tools><tool>hacking</tool></tools>")
        self.assertFalse(r["valid"])
        self.assertIn("unknown_tool", r["error"])

    def test_too_many_tools(self):
        r = mp.parse_multitool_response(
            "<tools><tool>weather</tool><tool>email</tool>"
            "<tool>calendar</tool><tool>infra-monitor</tool></tools>")
        self.assertFalse(r["valid"])
        self.assertIn("too_many", r["error"])

    def test_text_outside_xml(self):
        r = mp.parse_multitool_response("Sure! <tools><tool>weather</tool></tools>")
        self.assertFalse(r["valid"])
        self.assertEqual(r["error"], "text_outside_xml")

    def test_text_after_xml(self):
        r = mp.parse_multitool_response("<tools><tool>weather</tool></tools> Done!")
        self.assertFalse(r["valid"])
        self.assertEqual(r["error"], "text_outside_xml")

    def test_no_tools_block(self):
        r = mp.parse_multitool_response("I can help with that!")
        self.assertFalse(r["valid"])
        self.assertEqual(r["error"], "no_tools_block")

    def test_empty_response(self):
        r = mp.parse_multitool_response("")
        self.assertFalse(r["valid"])
        self.assertEqual(r["error"], "empty_response")

    def test_malformed_xml(self):
        r = mp.parse_multitool_response("<tools><tool>weather</tools>")
        self.assertFalse(r["valid"])
        self.assertIn("xml_parse_error", r["error"])

    def test_unexpected_element(self):
        r = mp.parse_multitool_response("<tools><action>weather</action></tools>")
        self.assertFalse(r["valid"])
        self.assertIn("unexpected_element", r["error"])

    def test_case_insensitive_tool_names(self):
        r = mp.parse_multitool_response("<tools><tool>Weather</tool></tools>")
        self.assertTrue(r["valid"])
        self.assertEqual(r["tools"], ["weather"])

    def test_all_allowed_tools(self):
        for tool in mp.ALLOWED_TOOLS:
            r = mp.parse_multitool_response(f"<tools><tool>{tool}</tool></tools>")
            self.assertTrue(r["valid"], f"Tool {tool} should be valid")
            self.assertEqual(r["tools"], [tool])


# ── Registry Tests ───────────────────────────────────────────────

class TestToolRegistry(unittest.TestCase):

    def test_all_tools_have_skill_category(self):
        for name, entry in mp.TOOL_REGISTRY.items():
            self.assertIn("skill_category", entry, f"{name} missing skill_category")

    def test_skill_categories_match_orchestrator(self):
        """All skill_category values must exist in orchestrator SKILL_MAP."""
        for name, entry in mp.TOOL_REGISTRY.items():
            cat = entry["skill_category"]
            self.assertIn(cat, orch.SKILL_MAP,
                          f"Registry {name} → {cat} not in SKILL_MAP")

    def test_get_skill_category(self):
        self.assertEqual(mp.get_skill_category("weather"), "skill:weather")
        self.assertEqual(mp.get_skill_category("email"), "skill:email")
        self.assertIsNone(mp.get_skill_category("nonexistent"))

    def test_smart_home_has_action(self):
        self.assertEqual(mp.get_tool_action("smart-home-on"), "on")
        self.assertEqual(mp.get_tool_action("smart-home-cinema"), "cinema")
        self.assertIsNone(mp.get_tool_action("weather"))

    def test_registry_size(self):
        self.assertEqual(len(mp.TOOL_REGISTRY), 14)


# ── Policy Tests ─────────────────────────────────────────────────

class TestMultitoolPolicy(unittest.TestCase):

    def test_single_domain_is_false(self):
        self.assertFalse(mp.should_use_multitool("cómo está el clima"))
        self.assertFalse(mp.should_use_multitool("revisá mis mails"))

    def test_two_domains_with_y(self):
        self.assertTrue(mp.should_use_multitool("clima y agenda de hoy"))
        self.assertTrue(mp.should_use_multitool("mails y calendario"))

    def test_two_domains_with_connector(self):
        self.assertTrue(mp.should_use_multitool(
            "fijate el clima y además la agenda"))

    def test_three_domains_without_connector(self):
        self.assertTrue(mp.should_use_multitool(
            "clima, mails, containers"))

    def test_greeting_is_false(self):
        self.assertFalse(mp.should_use_multitool("hola"))
        self.assertFalse(mp.should_use_multitool("buen día"))

    def test_ambiguous_is_false(self):
        self.assertFalse(mp.should_use_multitool("qué hacemos"))
        self.assertFalse(mp.should_use_multitool("ayudame"))

    def test_single_skill_complex_query(self):
        self.assertFalse(mp.should_use_multitool(
            "decime la temperatura actual en buenos aires"))


# ── Orchestrator Integration Tests ───────────────────────────────

class TestRouteAndExecuteMultitool(unittest.TestCase):

    @patch("runtime.orchestrator._call_multitool_model")
    def test_two_tools_execution(self, mock_model):
        """Two valid tools should both execute."""
        mock_model.return_value = "<tools><tool>weather</tool><tool>calendar</tool></tools>"

        with patch("runtime.orchestrator.execute_skill") as mock_exec:
            mock_exec.return_value = {
                "skill": "test", "success": True,
                "output": "test output", "raw": "", "destructive": False,
                "error": None,
            }
            result = orch.route_and_execute_multitool(
                "clima y agenda de hoy")

        self.assertTrue(result["executed"])
        self.assertTrue(result["multitool"])
        self.assertEqual(result["tools_used"], ["weather", "calendar"])
        self.assertEqual(len(result["results"]), 2)
        self.assertFalse(result["fallback_used"])

    @patch("runtime.orchestrator._call_multitool_model")
    def test_parse_failure_falls_back(self, mock_model):
        """Invalid XML should fallback to route_and_execute."""
        mock_model.return_value = "Sorry I can't do that"

        with patch("runtime.orchestrator.route_and_execute") as mock_route:
            mock_route.return_value = {"executed": False, "classification": "chat"}
            result = orch.route_and_execute_multitool(
                "clima y agenda de hoy")

        # Should have fallen back
        mock_route.assert_called_once()
        self.assertFalse(result.get("multitool", False))

    @patch("runtime.orchestrator._call_multitool_model")
    def test_model_offline_falls_back(self, mock_model):
        """Model returning None should fallback."""
        mock_model.return_value = None

        with patch("runtime.orchestrator.route_and_execute") as mock_route:
            mock_route.return_value = {"executed": False}
            result = orch.route_and_execute_multitool(
                "clima y agenda")

        mock_route.assert_called_once()

    def test_policy_skip_falls_back(self):
        """Single-skill query should skip multitool entirely."""
        with patch("runtime.orchestrator.route_and_execute") as mock_route:
            mock_route.return_value = {"executed": False}
            result = orch.route_and_execute_multitool("cómo está el clima")

        mock_route.assert_called_once()

    @patch("runtime.orchestrator._call_multitool_model")
    def test_partial_failure(self, mock_model):
        """If one tool fails, others still execute."""
        mock_model.return_value = "<tools><tool>weather</tool><tool>email</tool></tools>"

        call_count = [0]
        def fake_exec(skill_cat, query=""):
            call_count[0] += 1
            if "weather" in skill_cat:
                return {"skill": skill_cat, "success": True,
                        "output": "22°C", "raw": "", "destructive": False,
                        "error": None}
            return {"skill": skill_cat, "success": False,
                    "output": "timeout", "raw": "", "destructive": False,
                    "error": "timeout"}

        with patch("runtime.orchestrator.execute_skill", side_effect=fake_exec):
            result = orch.route_and_execute_multitool(
                "clima y mails")

        self.assertTrue(result["executed"])
        self.assertEqual(len(result["results"]), 2)
        self.assertTrue(result["results"][0]["success"])
        self.assertFalse(result["results"][1]["success"])
        self.assertIn("22°C", result["aggregated_text"])

    @patch("runtime.orchestrator._call_multitool_model")
    def test_empty_tools_falls_back(self, mock_model):
        """Model returning empty tools should fallback."""
        mock_model.return_value = "<tools></tools>"

        with patch("runtime.orchestrator.route_and_execute") as mock_route:
            mock_route.return_value = {"executed": False}
            result = orch.route_and_execute_multitool("clima y agenda")

        mock_route.assert_called_once()

    @patch("runtime.orchestrator._call_multitool_model")
    def test_smart_home_action_injection(self, mock_model):
        """Smart-home tools should inject the correct action query."""
        mock_model.return_value = "<tools><tool>smart-home-cinema</tool><tool>weather</tool></tools>"

        exec_calls = []
        def fake_exec(skill_cat, query=""):
            exec_calls.append((skill_cat, query))
            return {"skill": skill_cat, "success": True,
                    "output": "ok", "raw": "", "destructive": False,
                    "error": None}

        with patch("runtime.orchestrator.execute_skill", side_effect=fake_exec):
            orch.route_and_execute_multitool(
                "poné modo cine y decime el clima")

        # First call should be smart-home with cinema query
        self.assertEqual(exec_calls[0][0], "skill:smart-home")
        self.assertIn("cinema", exec_calls[0][1].lower())
        # Second call should be weather
        self.assertEqual(exec_calls[1][0], "skill:weather")


# ── Dataset Tests ────────────────────────────────────────────────

class TestDataset(unittest.TestCase):

    def test_dataset_stats(self):
        sys.path.insert(0, "/home/tenazas/runtime/tool_tuning")
        from build_multitool_dataset import (
            SINGLE_TOOL, MULTI_TOOL, NO_TOOL, AMBIGUOUS_CONSERVATIVE,
        )
        self.assertGreaterEqual(len(SINGLE_TOOL), 80)
        self.assertGreaterEqual(len(MULTI_TOOL), 60)
        self.assertGreaterEqual(len(NO_TOOL), 20)
        self.assertGreaterEqual(len(AMBIGUOUS_CONSERVATIVE), 20)

    def test_dataset_format(self):
        sys.path.insert(0, "/home/tenazas/runtime/tool_tuning")
        from build_multitool_dataset import generate_dataset
        entries = generate_dataset()
        self.assertTrue(len(entries) > 0)
        for entry in entries[:5]:
            self.assertIn("messages", entry)
            msgs = entry["messages"]
            self.assertEqual(len(msgs), 3)
            self.assertEqual(msgs[0]["role"], "system")
            self.assertEqual(msgs[1]["role"], "user")
            self.assertEqual(msgs[2]["role"], "assistant")
            # Assistant content must be valid XML
            r = mp.parse_multitool_response(msgs[2]["content"])
            self.assertTrue(r["valid"],
                            f"Invalid XML in dataset: {msgs[2]['content']}")


if __name__ == "__main__":
    unittest.main(verbosity=2)
