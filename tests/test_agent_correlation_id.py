#!/usr/bin/env python3
"""
Tests for correlation_id kwarg in all 5 agent handle() functions.
Plan 08-01: coordinator-rewrite — add correlation_id to agent signatures.

RED phase: These tests must FAIL before implementation.
GREEN phase: These tests must PASS after implementation.
"""
import inspect
import sys
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/tenazas/runtime")

# ── helpers ──────────────────────────────────────────────────────────────────

AGENT_MODULES = [
    "agent_infra",
    "agent_personal",
    "agent_finance",
    "agent_research",
    "agent_self_improve",
]


def _import(name):
    if name in sys.modules:
        del sys.modules[name]
    return __import__(name)


# ── signature tests ───────────────────────────────────────────────────────────

class TestHandleSignature(unittest.TestCase):
    """handle() must accept correlation_id as an optional keyword argument."""

    def _check_module(self, mod_name):
        mod = _import(mod_name)
        params = inspect.signature(mod.handle).parameters
        self.assertIn("correlation_id", params,
                      f"{mod_name}.handle() missing 'correlation_id' parameter")
        default = params["correlation_id"].default
        self.assertIsNone(default,
                          f"{mod_name}.handle() correlation_id default must be None, got {default!r}")

    def test_agent_infra_signature(self):
        self._check_module("agent_infra")

    def test_agent_personal_signature(self):
        self._check_module("agent_personal")

    def test_agent_finance_signature(self):
        self._check_module("agent_finance")

    def test_agent_research_signature(self):
        self._check_module("agent_research")

    def test_agent_self_improve_signature(self):
        self._check_module("agent_self_improve")


# ── backward compat tests ─────────────────────────────────────────────────────

class TestBackwardCompat(unittest.TestCase):
    """handle('') with no kwargs must not raise TypeError (agents_worker pattern)."""

    def _check_no_crash(self, mod_name):
        mod = _import(mod_name)
        # Patch all external I/O so unit test doesn't need live services
        with patch.object(mod, "_log", lambda d: None), \
             patch.object(mod, "load_json", return_value={}), \
             patch.object(mod, "save_json", lambda *a, **kw: None):
            try:
                result = mod.handle("")
            except TypeError as e:
                self.fail(f"{mod_name}.handle('') raised TypeError: {e}")
            self.assertIsInstance(result, tuple, f"{mod_name}.handle('') must return tuple")
            self.assertEqual(len(result), 2, f"{mod_name}.handle('') must return 2-tuple")

    def _check_with_kwarg(self, mod_name):
        mod = _import(mod_name)
        with patch.object(mod, "_log", lambda d: None), \
             patch.object(mod, "load_json", return_value={}), \
             patch.object(mod, "save_json", lambda *a, **kw: None):
            try:
                result = mod.handle("test query", correlation_id="abc12345")
            except TypeError as e:
                self.fail(f"{mod_name}.handle('query', correlation_id=...) raised TypeError: {e}")
            self.assertIsInstance(result, tuple)
            self.assertEqual(len(result), 2)

    def test_infra_no_kwarg(self):
        self._check_no_crash("agent_infra")

    def test_personal_no_kwarg(self):
        self._check_no_crash("agent_personal")

    def test_finance_no_kwarg(self):
        self._check_no_crash("agent_finance")

    def test_research_no_kwarg(self):
        self._check_no_crash("agent_research")

    def test_self_improve_no_kwarg(self):
        self._check_no_crash("agent_self_improve")

    def test_infra_with_kwarg(self):
        self._check_with_kwarg("agent_infra")

    def test_personal_with_kwarg(self):
        self._check_with_kwarg("agent_personal")

    def test_finance_with_kwarg(self):
        self._check_with_kwarg("agent_finance")

    def test_research_with_kwarg(self):
        self._check_with_kwarg("agent_research")

    def test_self_improve_with_kwarg(self):
        self._check_with_kwarg("agent_self_improve")


# ── log field tests ───────────────────────────────────────────────────────────

class TestCorrelationIdInLog(unittest.TestCase):
    """handle() log entry must include 'correlation_id' field."""

    def _check_log_field(self, mod_name, cid="test-cid-001"):
        mod = _import(mod_name)
        logged = []

        def fake_log(d):
            logged.append(d)

        with patch.object(mod, "_log", fake_log), \
             patch.object(mod, "load_json", return_value={}), \
             patch.object(mod, "save_json", lambda *a, **kw: None):
            mod.handle("test query", correlation_id=cid)

        self.assertTrue(len(logged) >= 1,
                        f"{mod_name}.handle() did not call _log at all")
        # The handle() log entry (action=handle) must contain correlation_id
        handle_logs = [d for d in logged if d.get("action") == "handle"]
        self.assertTrue(len(handle_logs) >= 1,
                        f"{mod_name} no 'action:handle' log entry found")
        for entry in handle_logs:
            self.assertIn("correlation_id", entry,
                          f"{mod_name} log entry missing 'correlation_id': {entry}")
            self.assertEqual(entry["correlation_id"], cid,
                             f"{mod_name} log entry has wrong correlation_id value")

    def _check_log_null_when_omitted(self, mod_name):
        mod = _import(mod_name)
        logged = []

        def fake_log(d):
            logged.append(d)

        with patch.object(mod, "_log", fake_log), \
             patch.object(mod, "load_json", return_value={}), \
             patch.object(mod, "save_json", lambda *a, **kw: None):
            mod.handle("")

        handle_logs = [d for d in logged if d.get("action") == "handle"]
        for entry in handle_logs:
            self.assertIn("correlation_id", entry,
                          f"{mod_name} log missing 'correlation_id' when omitted")
            self.assertIsNone(entry["correlation_id"],
                              f"{mod_name} correlation_id should be null when not passed")

    def test_infra_log_field(self):
        self._check_log_field("agent_infra")

    def test_personal_log_field(self):
        self._check_log_field("agent_personal")

    def test_finance_log_field(self):
        self._check_log_field("agent_finance")

    def test_research_log_field(self):
        self._check_log_field("agent_research")

    def test_self_improve_log_field(self):
        self._check_log_field("agent_self_improve")

    def test_infra_log_null_when_omitted(self):
        self._check_log_null_when_omitted("agent_infra")

    def test_personal_log_null_when_omitted(self):
        self._check_log_null_when_omitted("agent_personal")

    def test_finance_log_null_when_omitted(self):
        self._check_log_null_when_omitted("agent_finance")

    def test_research_log_null_when_omitted(self):
        self._check_log_null_when_omitted("agent_research")

    def test_self_improve_log_null_when_omitted(self):
        self._check_log_null_when_omitted("agent_self_improve")


if __name__ == "__main__":
    unittest.main()
