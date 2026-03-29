#!/usr/bin/env python3
"""
Tests para runtime/ — docker_wrapper, event detection, task queue, patterns.
Corre: python3 tests/test_runtime.py
"""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

# Agregar el path del runtime al sys.path
sys.path.insert(0, "/home/tenazas")
from runtime import docker_wrapper, executors
from runtime import runtime as rt


class TestDockerWrapperSanitize(unittest.TestCase):
    """Container name sanitization."""

    def test_valid_names(self):
        self.assertEqual(docker_wrapper.sanitize_name("sonarr"), "sonarr")
        self.assertEqual(docker_wrapper.sanitize_name("my-container"), "my-container")
        self.assertEqual(docker_wrapper.sanitize_name("app_v2.1"), "app_v2.1")

    def test_rejects_empty(self):
        with self.assertRaises(ValueError):
            docker_wrapper.sanitize_name("")
        with self.assertRaises(ValueError):
            docker_wrapper.sanitize_name("   ")

    def test_rejects_injection(self):
        with self.assertRaises(ValueError):
            docker_wrapper.sanitize_name("; rm -rf /")
        with self.assertRaises(ValueError):
            docker_wrapper.sanitize_name("$(evil)")
        with self.assertRaises(ValueError):
            docker_wrapper.sanitize_name("foo bar")

    def test_rejects_protected_containers(self):
        with self.assertRaises(ValueError):
            docker_wrapper.sanitize_name("openclaw")
        with self.assertRaises(ValueError):
            docker_wrapper.sanitize_name("watchtower")

    def test_rejects_protected_case_insensitive(self):
        with self.assertRaises(ValueError):
            docker_wrapper.sanitize_name("OpenClaw")
        with self.assertRaises(ValueError):
            docker_wrapper.sanitize_name("WATCHTOWER")


class TestDockerWrapperWhitelist(unittest.TestCase):
    """Only allowed actions pass through."""

    def test_rejected_actions(self):
        for action in ["exec", "rm", "run", "build", "pull", "commit"]:
            result = docker_wrapper.docker_action(action, "sonarr")
            self.assertFalse(result["ok"], f"{action} should be rejected")
            self.assertIn("not allowed", result["error"])

    @patch("runtime.docker_wrapper.subprocess.run")
    def test_restart_allowed(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0, stdout="sonarr", stderr=""
        )
        result = docker_wrapper.docker_action("restart", "sonarr")
        self.assertTrue(result["ok"])
        mock_run.assert_called_once_with(
            ["docker", "restart", "sonarr"],
            capture_output=True, text=True, timeout=30,
        )

    @patch("runtime.docker_wrapper.subprocess.run")
    def test_start_allowed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = docker_wrapper.docker_action("start", "radarr")
        self.assertTrue(result["ok"])

    @patch("runtime.docker_wrapper.subprocess.run")
    def test_stop_allowed(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
        result = docker_wrapper.docker_action("stop", "lidarr")
        self.assertTrue(result["ok"])


class TestDockerWrapperLogging(unittest.TestCase):
    """All docker actions must be logged."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.log_path = Path(self.tmpdir) / "docker_actions.log"
        self._orig = docker_wrapper.LOG_FILE
        docker_wrapper.LOG_FILE = self.log_path

    def tearDown(self):
        docker_wrapper.LOG_FILE = self._orig

    def test_rejected_action_logged(self):
        docker_wrapper.docker_action("exec", "sonarr")
        self.assertTrue(self.log_path.exists())
        entry = json.loads(self.log_path.read_text().strip())
        self.assertFalse(entry["ok"])
        self.assertEqual(entry["action"], "exec")

    @patch("runtime.docker_wrapper.subprocess.run")
    def test_successful_action_logged(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="ok", stderr="")
        docker_wrapper.docker_action("restart", "gluetun")
        entry = json.loads(self.log_path.read_text().strip())
        self.assertTrue(entry["ok"])
        self.assertEqual(entry["container"], "gluetun")


class TestDockerPs(unittest.TestCase):
    """docker ps output parsing."""

    @patch("runtime.docker_wrapper.subprocess.run")
    def test_parses_output(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="sonarr\tUp 2 hours\nradarr\tExited (1) 5 minutes ago\n",
            stderr="",
        )
        result = docker_wrapper.docker_ps()
        self.assertEqual(len(result), 2)
        self.assertTrue(result[0]["up"])
        self.assertFalse(result[1]["up"])
        self.assertEqual(result[0]["name"], "sonarr")
        self.assertEqual(result[1]["name"], "radarr")


class TestEventDetection(unittest.TestCase):
    """State diffing produces correct events."""

    def _make_state(self, **overrides):
        base = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "containers": [
                {"name": "sonarr", "status": "Up 2h", "up": True},
                {"name": "gluetun", "status": "Up 1h", "up": True},
            ],
            "internet": {"ok": True, "latency_ms": 10},
            "vpn": {"ip": "82.140.1.2", "ok": True},
            "public_ip": {"ip": "190.12.3.4"},
            "ollama_notebook": {"ok": True},
            "ollama_nas": {"ok": False},
            "network_devices": {"count": 12},
        }
        base.update(overrides)
        return base

    def test_no_events_on_first_run(self):
        curr = self._make_state()
        events = rt.detect_events({}, curr)
        self.assertEqual(events, [])

    def test_container_down_detected(self):
        prev = self._make_state()
        curr = self._make_state(containers=[
            {"name": "sonarr", "status": "Exited", "up": False},
            {"name": "gluetun", "status": "Up 1h", "up": True},
        ])
        events = rt.detect_events(prev, curr)
        types = [e["type"] for e in events]
        self.assertIn("container_down", types)
        down_event = next(e for e in events if e["type"] == "container_down")
        self.assertEqual(down_event["container"], "sonarr")

    def test_container_recovered_detected(self):
        prev = self._make_state(containers=[
            {"name": "sonarr", "status": "Exited", "up": False},
            {"name": "gluetun", "status": "Up 1h", "up": True},
        ])
        curr = self._make_state()
        events = rt.detect_events(prev, curr)
        types = [e["type"] for e in events]
        self.assertIn("container_recovered", types)

    def test_skip_containers_ignored(self):
        prev = self._make_state(containers=[
            {"name": "openclaw", "status": "Up 5h", "up": True},
            {"name": "watchtower", "status": "Up 3h", "up": True},
        ])
        curr = self._make_state(containers=[
            {"name": "openclaw", "status": "Exited", "up": False},
            {"name": "watchtower", "status": "Exited", "up": False},
        ])
        events = rt.detect_events(prev, curr)
        self.assertEqual(events, [])

    def test_internet_lost(self):
        prev = self._make_state()
        curr = self._make_state(internet={"ok": False, "latency_ms": None})
        events = rt.detect_events(prev, curr)
        types = [e["type"] for e in events]
        self.assertIn("internet_lost", types)

    def test_internet_restored(self):
        prev = self._make_state(internet={"ok": False, "latency_ms": None})
        curr = self._make_state()
        events = rt.detect_events(prev, curr)
        types = [e["type"] for e in events]
        self.assertIn("internet_restored", types)

    def test_vpn_down(self):
        prev = self._make_state()
        curr = self._make_state(vpn={"ip": None, "ok": False})
        events = rt.detect_events(prev, curr)
        types = [e["type"] for e in events]
        self.assertIn("vpn_down", types)

    def test_vpn_changed(self):
        prev = self._make_state()
        curr = self._make_state(vpn={"ip": "99.99.99.99", "ok": True})
        events = rt.detect_events(prev, curr)
        types = [e["type"] for e in events]
        self.assertIn("vpn_changed", types)

    def test_device_count_change_ignored_if_small(self):
        prev = self._make_state(network_devices={"count": 12})
        curr = self._make_state(network_devices={"count": 13})
        events = rt.detect_events(prev, curr)
        types = [e["type"] for e in events]
        self.assertNotIn("device_count_changed", types)

    def test_device_count_change_detected_if_large(self):
        prev = self._make_state(network_devices={"count": 12})
        curr = self._make_state(network_devices={"count": 16})
        events = rt.detect_events(prev, curr)
        types = [e["type"] for e in events]
        self.assertIn("device_count_changed", types)

    def test_ollama_notebook_down(self):
        prev = self._make_state()
        curr = self._make_state(ollama_notebook={"ok": False})
        events = rt.detect_events(prev, curr)
        types = [e["type"] for e in events]
        self.assertIn("ollama_notebook_down", types)


class TestTaskEnqueueing(unittest.TestCase):
    """Events produce correct tasks."""

    def test_container_down_generates_restart(self):
        events = [{"type": "container_down", "container": "sonarr", "ts": "t"}]
        tasks = rt.enqueue_tasks(events)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["action"], "docker.restart")
        self.assertEqual(tasks[0]["target"], "sonarr")

    def test_vpn_down_restarts_gluetun(self):
        events = [{"type": "vpn_down", "ts": "t"}]
        tasks = rt.enqueue_tasks(events)
        self.assertEqual(len(tasks), 1)
        self.assertEqual(tasks[0]["target"], "gluetun")

    def test_gluetun_prioritized(self):
        events = [
            {"type": "container_down", "container": "sonarr", "ts": "t"},
            {"type": "container_down", "container": "gluetun", "ts": "t"},
        ]
        tasks = rt.enqueue_tasks(events)
        self.assertEqual(tasks[0]["target"], "gluetun")

    def test_deduplication(self):
        events = [
            {"type": "container_down", "container": "gluetun", "ts": "t"},
            {"type": "vpn_down", "ts": "t"},
        ]
        tasks = rt.enqueue_tasks(events)
        targets = [t["target"] for t in tasks]
        self.assertEqual(targets.count("gluetun"), 1)

    def test_informational_events_no_task(self):
        events = [
            {"type": "internet_lost", "ts": "t"},
            {"type": "device_count_changed", "prev": 12, "curr": 16, "ts": "t"},
        ]
        tasks = rt.enqueue_tasks(events)
        self.assertEqual(tasks, [])

    def test_non_arr_container_not_restarted(self):
        events = [{"type": "container_down", "container": "randomthing", "ts": "t"}]
        tasks = rt.enqueue_tasks(events)
        self.assertEqual(tasks, [])


class TestPatternEngine(unittest.TestCase):
    """Pattern detection and triggering."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_memory = rt.MEMORY_FILE
        self._orig_patterns = rt.PATTERNS_FILE
        rt.MEMORY_FILE = Path(self.tmpdir) / "memory_log.json"
        rt.PATTERNS_FILE = Path(self.tmpdir) / "patterns.json"

    def tearDown(self):
        rt.MEMORY_FILE = self._orig_memory
        rt.PATTERNS_FILE = self._orig_patterns

    def test_pattern_detected_from_recurring_events(self):
        now = datetime.now(timezone.utc)
        # Simulate 4 occurrences at hour 3 over different days
        memory = []
        for i in range(4):
            ts = (now - timedelta(days=i)).replace(hour=3, minute=0)
            memory.append({
                "ts": ts.isoformat(),
                "events": [{"type": "container_down", "container": "sonarr"}],
                "tasks_run": 1,
                "results": [],
            })
        rt.save_json(rt.MEMORY_FILE, memory)
        rt.save_json(rt.PATTERNS_FILE, {"patterns": [], "last_analysis": ""})

        rt.analyze_patterns()

        patterns_data = rt.load_json(rt.PATTERNS_FILE)
        self.assertTrue(len(patterns_data["patterns"]) > 0)
        p = patterns_data["patterns"][0]
        self.assertEqual(p["event"], "container_down")
        self.assertEqual(p["target"], "sonarr")
        self.assertEqual(p["hour"], 3)

    def test_analysis_throttled(self):
        rt.save_json(rt.MEMORY_FILE, [])
        rt.save_json(rt.PATTERNS_FILE, {
            "patterns": [],
            "last_analysis": datetime.now(timezone.utc).isoformat(),
        })
        # Should not re-analyze (too recent)
        rt.analyze_patterns()
        data = rt.load_json(rt.PATTERNS_FILE)
        # last_analysis unchanged (no re-write)
        self.assertEqual(data["patterns"], [])

    def test_trigger_not_fired_twice_same_day(self):
        today = datetime.now(timezone.utc).date().isoformat()
        current_hour = datetime.now(timezone.utc).hour
        rt.save_json(rt.PATTERNS_FILE, {
            "patterns": [{
                "event": "container_down", "target": "sonarr",
                "hour": current_hour, "occurrences": 5,
                "last_seen": "", "last_triggered": today + "T00:00:00",
            }],
            "last_analysis": "",
        })
        tasks = rt.trigger_patterns()
        self.assertEqual(tasks, [])


class TestIntegration(unittest.TestCase):
    """End-to-end: two runs, second detects changes."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = {
            "STATE_FILE": rt.STATE_FILE,
            "EVENTS_FILE": rt.EVENTS_FILE,
            "TASKS_FILE": rt.TASKS_FILE,
            "MEMORY_FILE": rt.MEMORY_FILE,
            "PATTERNS_FILE": rt.PATTERNS_FILE,
            "LOGS_DIR": rt.LOGS_DIR,
        }
        rt.STATE_FILE = Path(self.tmpdir) / "state.json"
        rt.EVENTS_FILE = Path(self.tmpdir) / "events.json"
        rt.TASKS_FILE = Path(self.tmpdir) / "tasks.json"
        rt.MEMORY_FILE = Path(self.tmpdir) / "memory_log.json"
        rt.PATTERNS_FILE = Path(self.tmpdir) / "patterns.json"
        rt.LOGS_DIR = Path(self.tmpdir) / "logs"

    def tearDown(self):
        for k, v in self._orig.items():
            setattr(rt, k, v)

    @patch("runtime.executors.check_network_devices", return_value={"count": 12})
    @patch("runtime.executors.check_ollama_nas", return_value={"ok": False})
    @patch("runtime.executors.check_ollama_notebook", return_value={"ok": True})
    @patch("runtime.executors.check_public_ip", return_value={"ip": "190.1.2.3"})
    @patch("runtime.executors.check_vpn", return_value={"ip": "82.1.2.3", "ok": True})
    @patch("runtime.executors.check_internet", return_value={"ok": True, "latency_ms": 10})
    @patch("runtime.executors.check_containers")
    def test_two_runs_detect_container_down(self, mock_containers, *_):
        # Run 1: all up (baseline)
        mock_containers.return_value = [
            {"name": "sonarr", "status": "Up 2h", "up": True},
            {"name": "gluetun", "status": "Up 1h", "up": True},
        ]
        rt.main()

        events = rt.load_json(rt.EVENTS_FILE)
        self.assertEqual(events["events"], [])  # Baseline, no events

        # Run 2: sonarr down
        mock_containers.return_value = [
            {"name": "sonarr", "status": "Exited (1)", "up": False},
            {"name": "gluetun", "status": "Up 1h", "up": True},
        ]

        with patch("runtime.executors.execute_task") as mock_exec:
            mock_exec.return_value = {"ok": True, "action": "docker.restart",
                                       "target": "sonarr", "detail": ""}
            rt.main()

        events = rt.load_json(rt.EVENTS_FILE)
        types = [e["type"] for e in events["events"]]
        self.assertIn("container_down", types)

        tasks = rt.load_json(rt.TASKS_FILE)
        self.assertTrue(any(t["target"] == "sonarr" for t in tasks["tasks"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
