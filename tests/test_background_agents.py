#!/usr/bin/env python3
"""
Tests for background_agents.py — Persistent background task registry.

Covers:
- _infra_watcher: container down, internet, vpn, ollama flags
- _should_run: first run, interval elapsed, interval not elapsed, bad timestamp
- check_and_run: sequential execution, saves state, skips not-due agents
- get_agents_status: all agents listed with state

Corre: python3 tests/test_background_agents.py
"""

import sys
import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/home/tenazas")
from runtime import background_agents as ba


class _TempFilesMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig = {
            "state": ba.STATE_FILE,
            "agents": ba.AGENTS_STATE_FILE,
            "log": ba.TELEMETRY_LOG,
        }
        ba.STATE_FILE = self.tmpdir / "state.json"
        ba.AGENTS_STATE_FILE = self.tmpdir / "agents_state.json"
        ba.TELEMETRY_LOG = self.tmpdir / "logs" / "background_agents.log"

    def tearDown(self):
        ba.STATE_FILE = self._orig["state"]
        ba.AGENTS_STATE_FILE = self._orig["agents"]
        ba.TELEMETRY_LOG = self._orig["log"]

    def _write_state(self, state):
        ba.STATE_FILE.write_text(json.dumps(state))


class TestInfraWatcher(_TempFilesMixin, unittest.TestCase):

    def test_healthy_no_flags(self):
        self._write_state({
            "containers": [{"name": "sonarr", "up": True}],
            "internet": {"ok": True, "latency_ms": 10},
            "vpn": {"ok": True, "country": "ES"},
            "ollama_notebook": {"ok": True},
        })
        result = ba._infra_watcher()
        self.assertEqual(result["flags"], [])

    def test_container_down_flagged(self):
        self._write_state({
            "containers": [{"name": "sonarr", "up": False}],
            "internet": {"ok": True}, "vpn": {"ok": True},
            "ollama_notebook": {"ok": True},
        })
        result = ba._infra_watcher()
        issues = [f["issue"] for f in result["flags"]]
        self.assertIn("container_down", issues)

    def test_protected_containers_not_flagged(self):
        self._write_state({
            "containers": [
                {"name": "openclaw", "up": False},
                {"name": "watchtower", "up": False},
            ],
            "internet": {"ok": True}, "vpn": {"ok": True},
            "ollama_notebook": {"ok": True},
        })
        result = ba._infra_watcher()
        self.assertEqual(result["flags"], [])

    def test_internet_down_flagged(self):
        self._write_state({
            "containers": [],
            "internet": {"ok": False},
            "vpn": {"ok": True},
            "ollama_notebook": {"ok": True},
        })
        result = ba._infra_watcher()
        issues = [f["issue"] for f in result["flags"]]
        self.assertIn("internet_down", issues)

    def test_high_latency_flagged(self):
        self._write_state({
            "containers": [],
            "internet": {"ok": True, "latency_ms": 500},
            "vpn": {"ok": True},
            "ollama_notebook": {"ok": True},
        })
        result = ba._infra_watcher()
        issues = [f["issue"] for f in result["flags"]]
        self.assertIn("high_latency", issues)

    def test_vpn_down_flagged(self):
        self._write_state({
            "containers": [],
            "internet": {"ok": True},
            "vpn": {"ok": False},
            "ollama_notebook": {"ok": True},
        })
        result = ba._infra_watcher()
        issues = [f["issue"] for f in result["flags"]]
        self.assertIn("vpn_down", issues)

    def test_vpn_leak_flagged(self):
        self._write_state({
            "containers": [],
            "internet": {"ok": True},
            "vpn": {"ok": True, "country": "AR"},
            "ollama_notebook": {"ok": True},
        })
        result = ba._infra_watcher()
        issues = [f["issue"] for f in result["flags"]]
        self.assertIn("vpn_leak", issues)

    def test_ollama_offline_flagged(self):
        self._write_state({
            "containers": [],
            "internet": {"ok": True},
            "vpn": {"ok": True},
            "ollama_notebook": {"ok": False},
        })
        result = ba._infra_watcher()
        issues = [f["issue"] for f in result["flags"]]
        self.assertIn("ollama_notebook_offline", issues)

    def test_missing_state_no_crash(self):
        """Missing state file should not crash. Flags unknown services as down."""
        result = ba._infra_watcher()
        self.assertIsInstance(result["flags"], list)

    def test_multiple_flags(self):
        self._write_state({
            "containers": [{"name": "sonarr", "up": False}],
            "internet": {"ok": False},
            "vpn": {"ok": False},
            "ollama_notebook": {"ok": False},
        })
        result = ba._infra_watcher()
        self.assertGreaterEqual(len(result["flags"]), 3)


class TestShouldRun(_TempFilesMixin, unittest.TestCase):

    def test_first_run(self):
        self.assertTrue(ba._should_run("test", 300, {}))

    def test_interval_elapsed(self):
        past = (datetime.now(timezone.utc) - timedelta(seconds=600)).isoformat()
        state = {"test": {"last_run": past}}
        self.assertTrue(ba._should_run("test", 300, state))

    def test_interval_not_elapsed(self):
        recent = datetime.now(timezone.utc).isoformat()
        state = {"test": {"last_run": recent}}
        self.assertFalse(ba._should_run("test", 300, state))

    def test_bad_timestamp(self):
        state = {"test": {"last_run": "not-a-date"}}
        self.assertTrue(ba._should_run("test", 300, state))

    def test_empty_last_run(self):
        state = {"test": {"last_run": ""}}
        self.assertTrue(ba._should_run("test", 300, state))


class TestCheckAndRun(_TempFilesMixin, unittest.TestCase):

    def test_first_run_executes_all(self):
        self._write_state({
            "containers": [], "internet": {"ok": True},
            "vpn": {"ok": True}, "ollama_notebook": {"ok": True},
        })
        results = ba.check_and_run()
        names = [r["agent"] for r in results]
        self.assertIn("infra_watcher", names)

    def test_saves_state(self):
        self._write_state({
            "containers": [], "internet": {"ok": True},
            "vpn": {"ok": True}, "ollama_notebook": {"ok": True},
        })
        ba.check_and_run()
        self.assertTrue(ba.AGENTS_STATE_FILE.exists())
        state = json.loads(ba.AGENTS_STATE_FILE.read_text())
        self.assertIn("infra_watcher", state)
        self.assertTrue(state["infra_watcher"]["ok"])

    def test_skips_not_due(self):
        # Set all agents as just ran
        state = {}
        for agent in ba.AGENTS:
            state[agent["name"]] = {
                "last_run": datetime.now(timezone.utc).isoformat(),
                "ok": True,
            }
        ba.save_json(ba.AGENTS_STATE_FILE, state)
        self._write_state({"containers": [], "internet": {"ok": True}, "vpn": {"ok": True}, "ollama_notebook": {"ok": True}})
        results = ba.check_and_run()
        self.assertEqual(results, [])

    def test_agent_exception_captured(self):
        # Temporarily replace an agent function with one that raises
        orig_fn = ba.AGENTS[0]["function"]
        ba.AGENTS[0]["function"] = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            results = ba.check_and_run()
            watcher = next((r for r in results if r["agent"] == "infra_watcher"), None)
            if watcher:
                self.assertFalse(watcher["ok"])
                self.assertIn("boom", watcher["result"]["error"])
        finally:
            ba.AGENTS[0]["function"] = orig_fn

    def test_logs_created(self):
        self._write_state({"containers": [], "internet": {"ok": True}, "vpn": {"ok": True}, "ollama_notebook": {"ok": True}})
        ba.check_and_run()
        self.assertTrue(ba.TELEMETRY_LOG.exists())


class TestGetAgentsStatus(_TempFilesMixin, unittest.TestCase):

    def test_all_agents_listed(self):
        status = ba.get_agents_status()
        names = [s["name"] for s in status]
        for agent in ba.AGENTS:
            self.assertIn(agent["name"], names)

    def test_never_run(self):
        status = ba.get_agents_status()
        for s in status:
            self.assertEqual(s["last_run"], "never")

    def test_after_run(self):
        self._write_state({"containers": [], "internet": {"ok": True}, "vpn": {"ok": True}, "ollama_notebook": {"ok": True}})
        ba.check_and_run()
        status = ba.get_agents_status()
        watcher = next(s for s in status if s["name"] == "infra_watcher")
        self.assertNotEqual(watcher["last_run"], "never")
        self.assertTrue(watcher["ok"])

    def test_has_description_and_interval(self):
        status = ba.get_agents_status()
        for s in status:
            self.assertIn("description", s)
            self.assertIn("interval_seconds", s)
            self.assertGreater(s["interval_seconds"], 0)


class TestAgentRegistry(_TempFilesMixin, unittest.TestCase):

    def test_three_agents_registered(self):
        self.assertEqual(len(ba.AGENTS), 3)

    def test_all_have_function(self):
        for agent in ba.AGENTS:
            self.assertTrue(callable(agent["function"]))

    def test_all_have_name(self):
        for agent in ba.AGENTS:
            self.assertIn("name", agent)
            self.assertGreater(len(agent["name"]), 0)


if __name__ == "__main__":
    unittest.main()
