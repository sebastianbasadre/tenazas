#!/usr/bin/env python3
"""
Tests for safety_guard.py — Central safety validation for Tenazas runtime.

Covers:
- Blocked actions (docker exec, rm, build, pull)
- Protected containers (openclaw, watchtower)
- Arr-stack container whitelist
- Container name sanitization
- File read/write/delete path validation
- Unknown action default-deny
- is_safe_container() helper
- get_blocked_actions() / get_allowed_actions()
- Logging of all validations

Corre: python3 tests/test_safety_guard.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, "/home/tenazas")
from runtime import safety_guard as sg


class TestBlockedActions(unittest.TestCase):
    """Blocked actions must always be rejected."""

    def test_docker_exec_blocked(self):
        r = sg.validate_action("docker exec", "openclaw")
        self.assertFalse(r["allowed"])
        self.assertIn("blocked", r["reason"].lower())

    def test_docker_rm_blocked(self):
        r = sg.validate_action("docker rm", "sonarr")
        self.assertFalse(r["allowed"])

    def test_docker_build_blocked(self):
        r = sg.validate_action("docker build", ".")
        self.assertFalse(r["allowed"])

    def test_docker_pull_blocked(self):
        r = sg.validate_action("docker pull", "nginx")
        self.assertFalse(r["allowed"])

    def test_network_config_change_blocked(self):
        r = sg.validate_action("network config change", "eth0")
        self.assertFalse(r["allowed"])

    def test_file_delete_outside_blocked(self):
        r = sg.validate_action("file delete outside runtime", "/etc/passwd")
        self.assertFalse(r["allowed"])

    def test_blocked_case_insensitive(self):
        r = sg.validate_action("Docker Exec", "openclaw")
        self.assertFalse(r["allowed"])

    def test_blocked_with_whitespace(self):
        r = sg.validate_action("  docker exec  ", "test")
        self.assertFalse(r["allowed"])


class TestProtectedContainers(unittest.TestCase):
    """openclaw and watchtower must never be modified."""

    def test_restart_openclaw_blocked(self):
        r = sg.validate_action("docker restart", "openclaw")
        self.assertFalse(r["allowed"])
        self.assertIn("protected", r["reason"].lower())

    def test_restart_watchtower_blocked(self):
        r = sg.validate_action("docker restart", "watchtower")
        self.assertFalse(r["allowed"])

    def test_stop_openclaw_blocked(self):
        r = sg.validate_action("docker stop", "openclaw")
        self.assertFalse(r["allowed"])

    def test_start_watchtower_blocked(self):
        r = sg.validate_action("docker start", "watchtower")
        self.assertFalse(r["allowed"])

    def test_protected_case_insensitive(self):
        r = sg.validate_action("docker restart", "OpenClaw")
        self.assertFalse(r["allowed"])

    def test_protected_with_whitespace(self):
        r = sg.validate_action("docker restart", "  watchtower  ")
        self.assertFalse(r["allowed"])


class TestArrStackWhitelist(unittest.TestCase):
    """Only arr-stack containers can be restarted/started/stopped."""

    def test_restart_sonarr_allowed(self):
        r = sg.validate_action("docker restart", "sonarr")
        self.assertTrue(r["allowed"])

    def test_restart_radarr_allowed(self):
        r = sg.validate_action("docker restart", "radarr")
        self.assertTrue(r["allowed"])

    def test_restart_gluetun_allowed(self):
        r = sg.validate_action("docker restart", "gluetun")
        self.assertTrue(r["allowed"])

    def test_restart_all_arr_containers(self):
        """Every container in ARR_CONTAINERS should be allowed."""
        for name in sg.ARR_CONTAINERS:
            r = sg.validate_action("docker restart", name)
            self.assertTrue(r["allowed"], f"{name} should be allowed")

    def test_stop_qbittorrent_allowed(self):
        r = sg.validate_action("docker stop", "qbittorrent")
        self.assertTrue(r["allowed"])

    def test_start_prowlarr_allowed(self):
        r = sg.validate_action("docker start", "prowlarr")
        self.assertTrue(r["allowed"])

    def test_unknown_container_blocked(self):
        r = sg.validate_action("docker restart", "nginx")
        self.assertFalse(r["allowed"])
        self.assertIn("whitelist", r["reason"].lower())

    def test_chromadb_not_in_whitelist(self):
        r = sg.validate_action("docker restart", "chromadb")
        self.assertFalse(r["allowed"])

    def test_no_target_blocked(self):
        r = sg.validate_action("docker restart", "")
        self.assertFalse(r["allowed"])
        self.assertIn("no target", r["reason"].lower())


class TestContainerNameSanitization(unittest.TestCase):
    """Container names must match safe regex pattern."""

    def test_valid_name(self):
        r = sg.validate_action("docker restart", "sonarr")
        self.assertTrue(r["allowed"])

    def test_name_with_dots(self):
        # Valid by regex but not in whitelist
        r = sg.validate_action("docker restart", "my.container")
        self.assertFalse(r["allowed"])
        self.assertIn("whitelist", r["reason"].lower())

    def test_name_with_injection(self):
        r = sg.validate_action("docker restart", "sonarr; rm -rf /")
        self.assertFalse(r["allowed"])
        self.assertIn("invalid", r["reason"].lower())

    def test_name_with_backticks(self):
        r = sg.validate_action("docker restart", "`whoami`")
        self.assertFalse(r["allowed"])

    def test_name_with_pipe(self):
        r = sg.validate_action("docker restart", "sonarr | cat /etc/passwd")
        self.assertFalse(r["allowed"])

    def test_name_with_newline(self):
        r = sg.validate_action("docker restart", "sonarr\nrm -rf /")
        self.assertFalse(r["allowed"])

    def test_name_starting_with_dash(self):
        r = sg.validate_action("docker restart", "-v")
        self.assertFalse(r["allowed"])

    def test_empty_name(self):
        r = sg.validate_action("docker restart", "")
        self.assertFalse(r["allowed"])


class TestDockerPs(unittest.TestCase):
    """docker ps is always allowed (read-only)."""

    def test_docker_ps_allowed(self):
        r = sg.validate_action("docker ps")
        self.assertTrue(r["allowed"])
        self.assertIn("read-only", r["reason"].lower())

    def test_docker_ps_with_target(self):
        r = sg.validate_action("docker ps", "anything")
        self.assertTrue(r["allowed"])


class TestFileRead(unittest.TestCase):
    """File read is always allowed."""

    def test_file_read_allowed(self):
        r = sg.validate_action("file read", "/etc/hosts")
        self.assertTrue(r["allowed"])

    def test_file_read_no_target(self):
        r = sg.validate_action("file read")
        self.assertTrue(r["allowed"])


class TestFileWrite(unittest.TestCase):
    """File write only allowed within runtime directory."""

    def test_write_inside_runtime(self):
        target = str(sg.RUNTIME_DIR / "logs" / "test.log")
        r = sg.validate_action("file write", target)
        self.assertTrue(r["allowed"])

    def test_write_outside_runtime_blocked(self):
        r = sg.validate_action("file write", "/etc/passwd")
        self.assertFalse(r["allowed"])
        self.assertIn("outside", r["reason"].lower())

    def test_write_runtime_keyword(self):
        r = sg.validate_action("file write runtime")
        self.assertTrue(r["allowed"])

    def test_write_no_target_assumed_ok(self):
        r = sg.validate_action("file write")
        self.assertTrue(r["allowed"])

    def test_write_path_traversal_blocked(self):
        target = str(sg.RUNTIME_DIR / ".." / ".." / "etc" / "passwd")
        r = sg.validate_action("file write", target)
        self.assertFalse(r["allowed"])


class TestFileDelete(unittest.TestCase):
    """File delete only within runtime directory."""

    def test_delete_inside_runtime(self):
        target = str(sg.RUNTIME_DIR / "logs" / "old.log")
        r = sg.validate_action("file delete", target)
        self.assertTrue(r["allowed"])

    def test_delete_outside_blocked(self):
        r = sg.validate_action("file delete", "/tmp/important")
        self.assertFalse(r["allowed"])

    def test_delete_no_target_blocked(self):
        r = sg.validate_action("file delete", "")
        self.assertFalse(r["allowed"])


class TestUnknownActions(unittest.TestCase):
    """Unknown actions are blocked by default (whitelist policy)."""

    def test_unknown_action_blocked(self):
        r = sg.validate_action("reboot system")
        self.assertFalse(r["allowed"])
        self.assertIn("unknown", r["reason"].lower())

    def test_random_action_blocked(self):
        r = sg.validate_action("send email", "admin@example.com")
        self.assertFalse(r["allowed"])

    def test_empty_action_blocked(self):
        r = sg.validate_action("")
        self.assertFalse(r["allowed"])


class TestIsSafeContainer(unittest.TestCase):
    """is_safe_container() quick check helper."""

    def test_arr_container_safe(self):
        self.assertTrue(sg.is_safe_container("sonarr"))

    def test_all_arr_safe(self):
        for name in sg.ARR_CONTAINERS:
            self.assertTrue(sg.is_safe_container(name), f"{name} should be safe")

    def test_protected_not_safe(self):
        self.assertFalse(sg.is_safe_container("openclaw"))
        self.assertFalse(sg.is_safe_container("watchtower"))

    def test_unknown_not_safe(self):
        self.assertFalse(sg.is_safe_container("nginx"))

    def test_invalid_name_not_safe(self):
        self.assertFalse(sg.is_safe_container("bad;name"))

    def test_case_insensitive(self):
        self.assertFalse(sg.is_safe_container("OpenClaw"))

    def test_whitespace_stripped(self):
        self.assertTrue(sg.is_safe_container("  sonarr  "))


class TestGetters(unittest.TestCase):
    """get_blocked_actions and get_allowed_actions return copies."""

    def test_blocked_returns_copy(self):
        blocked = sg.get_blocked_actions()
        self.assertIsInstance(blocked, set)
        blocked.add("test")
        self.assertNotIn("test", sg.BLOCKED_ACTIONS)

    def test_allowed_returns_copy(self):
        allowed = sg.get_allowed_actions()
        self.assertIsInstance(allowed, set)
        allowed.add("test")
        self.assertNotIn("test", sg.ALLOWED_ACTIONS)

    def test_blocked_contains_docker_exec(self):
        self.assertIn("docker exec", sg.get_blocked_actions())

    def test_allowed_contains_docker_ps(self):
        self.assertIn("docker ps", sg.get_allowed_actions())


class TestLogging(unittest.TestCase):
    """Every validation is logged to safety.log."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_log = sg.LOG_FILE
        sg.LOG_FILE = Path(self.tmpdir) / "logs" / "safety.log"

    def tearDown(self):
        sg.LOG_FILE = self._orig_log

    def test_validation_creates_log(self):
        sg.validate_action("docker ps")
        self.assertTrue(sg.LOG_FILE.exists())

    def test_log_contains_action(self):
        sg.validate_action("docker restart", "sonarr", "test context")
        lines = sg.LOG_FILE.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        self.assertEqual(entry["action"], "docker restart")
        self.assertEqual(entry["target"], "sonarr")
        self.assertEqual(entry["context"], "test context")
        self.assertTrue(entry["allowed"])

    def test_log_blocked_action(self):
        sg.validate_action("docker exec", "openclaw")
        lines = sg.LOG_FILE.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        self.assertFalse(entry["allowed"])

    def test_log_has_timestamp(self):
        sg.validate_action("docker ps")
        lines = sg.LOG_FILE.read_text().strip().split("\n")
        entry = json.loads(lines[-1])
        self.assertIn("ts", entry)

    def test_multiple_logs_appended(self):
        sg.validate_action("docker ps")
        sg.validate_action("docker restart", "sonarr")
        sg.validate_action("docker exec", "openclaw")
        lines = sg.LOG_FILE.read_text().strip().split("\n")
        self.assertEqual(len(lines), 3)


if __name__ == "__main__":
    unittest.main()
