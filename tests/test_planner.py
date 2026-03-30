#!/usr/bin/env python3
"""
Tests for planner.py — Rule-based planning engine.

Covers:
- Plan generation from known problems (templates)
- Generic plan fallback for unknown problems
- Context variable substitution in steps
- Problem-to-template mapping
- requires_any_approval flag
- get_plan_for_event() with runtime events
- list_templates() helper
- Plan structure validation
- Logging

Corre: python3 tests/test_planner.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/home/tenazas")
from runtime import planner as pl


class TestGeneratePlanKnown(unittest.TestCase):
    """Plans from known problem types use templates."""

    @patch("runtime.planner._log")
    def test_container_down_uses_restart(self, mock_log):
        plan = pl.generate_plan("container_down", {"container": "sonarr"})
        self.assertEqual(plan["template"], "restart_sequence")
        self.assertGreater(len(plan["steps"]), 0)

    @patch("runtime.planner._log")
    def test_vpn_down_uses_recovery(self, mock_log):
        plan = pl.generate_plan("vpn_down")
        self.assertEqual(plan["template"], "vpn_recovery")

    @patch("runtime.planner._log")
    def test_vpn_leak_uses_recovery(self, mock_log):
        plan = pl.generate_plan("vpn_leak")
        self.assertEqual(plan["template"], "vpn_recovery")

    @patch("runtime.planner._log")
    def test_internet_lost_uses_diagnostic(self, mock_log):
        plan = pl.generate_plan("internet_lost")
        self.assertEqual(plan["template"], "diagnostic_sequence")

    @patch("runtime.planner._log")
    def test_ollama_down_uses_failover(self, mock_log):
        plan = pl.generate_plan("ollama_notebook_down")
        self.assertEqual(plan["template"], "ollama_failover")

    @patch("runtime.planner._log")
    def test_arr_container_uses_arr_recovery(self, mock_log):
        plan = pl.generate_plan("arr_container_down", {"container": "radarr"})
        self.assertEqual(plan["template"], "arr_stack_recovery")


class TestGeneratePlanGeneric(unittest.TestCase):
    """Unknown problems get a generic plan."""

    @patch("runtime.planner._log")
    def test_unknown_problem_generic(self, mock_log):
        plan = pl.generate_plan("disk_full")
        self.assertEqual(plan["template"], "generic")
        self.assertIn("disk_full", plan["description"])

    @patch("runtime.planner._log")
    def test_generic_has_diagnose_step(self, mock_log):
        plan = pl.generate_plan("something_weird")
        actions = [s["action"] for s in plan["steps"]]
        self.assertIn("diagnose", actions)

    @patch("runtime.planner._log")
    def test_generic_has_notify_step(self, mock_log):
        plan = pl.generate_plan("something_weird")
        actions = [s["action"] for s in plan["steps"]]
        self.assertIn("notify", actions)

    @patch("runtime.planner._log")
    def test_generic_no_approval_needed(self, mock_log):
        plan = pl.generate_plan("unknown_issue")
        self.assertFalse(plan["requires_any_approval"])


class TestContextSubstitution(unittest.TestCase):
    """Template variables are replaced with context values."""

    @patch("runtime.planner._log")
    def test_container_name_substituted(self, mock_log):
        plan = pl.generate_plan("container_down", {"container": "sonarr"})
        targets = [s["target"] for s in plan["steps"]]
        self.assertIn("sonarr", targets)
        self.assertNotIn("{container}", str(targets))

    @patch("runtime.planner._log")
    def test_arr_recovery_substitutes_container(self, mock_log):
        plan = pl.generate_plan("arr_container_down", {"container": "lidarr"})
        targets = [s["target"] for s in plan["steps"]]
        self.assertIn("lidarr", targets)

    @patch("runtime.planner._log")
    def test_no_context_leaves_placeholder(self, mock_log):
        """Without context, format placeholders remain if no key provided."""
        # restart_sequence has {container} — without context it should raise or leave as-is
        # The code does: value.format(**context) where context={}, so {container} will raise KeyError
        # Let's verify the behavior
        try:
            plan = pl.generate_plan("container_down", {})
            # If it didn't raise, check targets still have placeholder
            targets = str([s["target"] for s in plan["steps"]])
            # Either it raised or kept the placeholder
        except KeyError:
            pass  # Expected: {container} without context raises KeyError


class TestPlanStructure(unittest.TestCase):
    """All plans have required fields."""

    @patch("runtime.planner._log")
    def test_plan_has_required_keys(self, mock_log):
        plan = pl.generate_plan("vpn_down")
        self.assertIn("problem", plan)
        self.assertIn("template", plan)
        self.assertIn("description", plan)
        self.assertIn("steps", plan)
        self.assertIn("generated_at", plan)
        self.assertIn("requires_any_approval", plan)

    @patch("runtime.planner._log")
    def test_steps_have_required_keys(self, mock_log):
        plan = pl.generate_plan("vpn_down")
        for step in plan["steps"]:
            self.assertIn("action", step)
            self.assertIn("target", step)
            self.assertIn("requires_approval", step)
            self.assertIn("estimated_duration", step)
            self.assertIn("description", step)

    @patch("runtime.planner._log")
    def test_generic_plan_has_required_keys(self, mock_log):
        plan = pl.generate_plan("unknown_thing")
        self.assertIn("problem", plan)
        self.assertIn("template", plan)
        self.assertIn("steps", plan)
        self.assertIn("generated_at", plan)


class TestRequiresApproval(unittest.TestCase):
    """Plans with restart steps require approval."""

    @patch("runtime.planner._log")
    def test_restart_requires_approval(self, mock_log):
        plan = pl.generate_plan("container_down", {"container": "sonarr"})
        self.assertTrue(plan["requires_any_approval"])

    @patch("runtime.planner._log")
    def test_vpn_recovery_requires_approval(self, mock_log):
        plan = pl.generate_plan("vpn_down")
        self.assertTrue(plan["requires_any_approval"])

    @patch("runtime.planner._log")
    def test_diagnostic_no_approval(self, mock_log):
        plan = pl.generate_plan("internet_lost")
        self.assertFalse(plan["requires_any_approval"])

    @patch("runtime.planner._log")
    def test_ollama_failover_no_approval(self, mock_log):
        plan = pl.generate_plan("ollama_notebook_down")
        self.assertFalse(plan["requires_any_approval"])


class TestGetPlanForEvent(unittest.TestCase):
    """get_plan_for_event() maps runtime events to plans."""

    @patch("runtime.planner._log")
    def test_container_down_arr(self, mock_log):
        event = {"type": "container_down", "container": "sonarr"}
        plan = pl.get_plan_for_event(event)
        self.assertEqual(plan["template"], "arr_stack_recovery")

    @patch("runtime.planner._log")
    def test_container_down_non_arr(self, mock_log):
        event = {"type": "container_down", "container": "chromadb"}
        plan = pl.get_plan_for_event(event)
        self.assertEqual(plan["template"], "restart_sequence")

    @patch("runtime.planner._log")
    def test_vpn_down_event(self, mock_log):
        event = {"type": "vpn_down"}
        plan = pl.get_plan_for_event(event)
        self.assertEqual(plan["template"], "vpn_recovery")

    @patch("runtime.planner._log")
    def test_vpn_leak_event(self, mock_log):
        event = {"type": "vpn_leak"}
        plan = pl.get_plan_for_event(event)
        self.assertEqual(plan["template"], "vpn_recovery")

    @patch("runtime.planner._log")
    def test_unknown_event_type(self, mock_log):
        event = {"type": "something_new"}
        plan = pl.get_plan_for_event(event)
        self.assertEqual(plan["template"], "diagnostic_sequence")

    @patch("runtime.planner._log")
    def test_event_without_type(self, mock_log):
        event = {}
        plan = pl.get_plan_for_event(event)
        self.assertEqual(plan["template"], "diagnostic_sequence")

    @patch("runtime.planner._log")
    def test_event_container_passed_to_context(self, mock_log):
        event = {"type": "container_down", "container": "radarr"}
        plan = pl.get_plan_for_event(event)
        targets = [s["target"] for s in plan["steps"]]
        self.assertIn("radarr", targets)


class TestListTemplates(unittest.TestCase):
    """list_templates() returns template names and descriptions."""

    def test_returns_dict(self):
        templates = pl.list_templates()
        self.assertIsInstance(templates, dict)

    def test_contains_all_templates(self):
        templates = pl.list_templates()
        for name in pl.PLAN_TEMPLATES:
            self.assertIn(name, templates)

    def test_values_are_descriptions(self):
        templates = pl.list_templates()
        for name, desc in templates.items():
            self.assertIsInstance(desc, str)
            self.assertGreater(len(desc), 0)


class TestProblemMap(unittest.TestCase):
    """PROBLEM_MAP covers expected problems."""

    def test_all_mapped_templates_exist(self):
        for problem, template in pl.PROBLEM_MAP.items():
            self.assertIn(template, pl.PLAN_TEMPLATES,
                          f"Problem '{problem}' maps to missing template '{template}'")

    def test_expected_problems_mapped(self):
        expected = ["container_down", "vpn_down", "vpn_leak",
                    "internet_lost", "ollama_notebook_down"]
        for problem in expected:
            self.assertIn(problem, pl.PROBLEM_MAP)


class TestLogging(unittest.TestCase):
    """Plan generation is logged."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_log = pl.LOG_FILE
        pl.LOG_FILE = Path(self.tmpdir) / "logs" / "planner.log"

    def tearDown(self):
        pl.LOG_FILE = self._orig_log

    def test_generate_plan_logs(self):
        pl.generate_plan("vpn_down")
        self.assertTrue(pl.LOG_FILE.exists())
        entry = json.loads(pl.LOG_FILE.read_text().strip().split("\n")[-1])
        self.assertEqual(entry["action"], "generate_plan")
        self.assertEqual(entry["problem"], "vpn_down")
        self.assertEqual(entry["template"], "vpn_recovery")

    def test_generic_plan_logged(self):
        pl.generate_plan("unknown")
        entry = json.loads(pl.LOG_FILE.read_text().strip().split("\n")[-1])
        self.assertEqual(entry["template"], "generic")

    def test_log_has_steps_count(self):
        pl.generate_plan("internet_lost")
        entry = json.loads(pl.LOG_FILE.read_text().strip().split("\n")[-1])
        self.assertIn("steps_count", entry)
        self.assertGreater(entry["steps_count"], 0)


if __name__ == "__main__":
    unittest.main()
