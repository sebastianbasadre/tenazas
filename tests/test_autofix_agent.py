#!/usr/bin/env python3
"""
Tests for autofix_agent.py — 30 tests.

All subprocess, network, and file I/O mocked.
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "workspace" / "runtime"))
if "/home/tenazas/runtime" not in sys.path:
    sys.path.insert(0, "/home/tenazas/runtime")

import autofix_agent as af


class TestRegressionDetection(unittest.TestCase):
    """Test classify_regression()."""

    @patch("autofix_agent._get_probe_history")
    def test_no_history(self, mock_hist):
        mock_hist.return_value = []
        self.assertEqual(af.classify_regression("test"), "unknown")

    @patch("autofix_agent._get_probe_history")
    def test_currently_ok(self, mock_hist):
        mock_hist.return_value = [{"status": "ok"}]
        self.assertEqual(af.classify_regression("test"), "unknown")

    @patch("autofix_agent._get_probe_history")
    def test_new_failure(self, mock_hist):
        mock_hist.return_value = [{"status": "fail"}, {"status": "fail"}]
        self.assertEqual(af.classify_regression("test"), "new")

    @patch("autofix_agent._get_probe_history")
    def test_regression(self, mock_hist):
        mock_hist.return_value = [{"status": "fail"}, {"status": "ok"},
                                  {"status": "ok"}, {"status": "ok"}]
        self.assertEqual(af.classify_regression("test"), "regression")

    @patch("autofix_agent._get_probe_history")
    def test_chronic(self, mock_hist):
        mock_hist.return_value = [{"status": "fail"}] * 6 + [{"status": "ok"}]
        self.assertEqual(af.classify_regression("test"), "chronic")

    @patch("autofix_agent._get_probe_history")
    def test_flapping(self, mock_hist):
        mock_hist.return_value = [
            {"status": "fail"}, {"status": "ok"},
            {"status": "fail"}, {"status": "ok"},
            {"status": "fail"}, {"status": "ok"},
            {"status": "fail"}, {"status": "ok"},
        ]
        self.assertEqual(af.classify_regression("test"), "flapping")


class TestExpectedDegradations(unittest.TestCase):
    """Test should_skip_probe()."""

    def test_skip_ok_probe(self):
        skip, reason = af.should_skip_probe("test", "ok", {})
        self.assertTrue(skip)

    @patch("autofix_agent._check_condition")
    def test_skip_expected_degradation(self, mock_cond):
        mock_cond.return_value = True
        config = {
            "expected_degradations": [
                {"probe": "ollama_nas", "condition": "homelab_ollama_alive",
                 "reason": "expected offline"},
            ]
        }
        skip, reason = af.should_skip_probe("ollama_nas", "warn", config)
        self.assertTrue(skip)
        self.assertIn("expected", reason)

    def test_skip_never_existed(self):
        config = {"never_existed": ["gpu_exporter"]}
        skip, reason = af.should_skip_probe("gpu_exporter", "warn", config)
        self.assertTrue(skip)
        self.assertIn("never_existed", reason)

    def test_no_skip_real_failure(self):
        config = {"expected_degradations": [], "never_existed": []}
        skip, _ = af.should_skip_probe("chromadb", "fail", config)
        self.assertFalse(skip)


class TestLLMAvailability(unittest.TestCase):
    """Test check_llm_availability()."""

    @patch("autofix_agent.urlopen")
    def test_ollama_available(self, mock_url):
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = json.dumps({
            "models": [{"name": "qwen2.5-coder:32b"}]
        }).encode()
        mock_url.return_value = resp
        result = af.check_llm_availability()
        self.assertEqual(result["recommended"], "ollama")
        self.assertTrue(result["ollama_local"])

    @patch("autofix_agent.urlopen")
    def test_ollama_fallback_model(self, mock_url):
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = json.dumps({
            "models": [{"name": "qwen2.5:7b"}]
        }).encode()
        mock_url.return_value = resp
        result = af.check_llm_availability()
        self.assertEqual(result["recommended"], "ollama")
        self.assertEqual(result["ollama_model"], "qwen2.5:7b")

    @patch("autofix_agent.urlopen")
    def test_no_llm_available(self, mock_url):
        mock_url.side_effect = Exception("Connection refused")
        with patch.dict(os.environ, {}, clear=False):
            # Remove GOOGLE_API_KEY if present
            env_backup = os.environ.pop("GOOGLE_API_KEY", None)
            try:
                result = af.check_llm_availability()
                self.assertIsNone(result["recommended"])
            finally:
                if env_backup:
                    os.environ["GOOGLE_API_KEY"] = env_backup


class TestTier1(unittest.TestCase):
    """Test Tier 1 ops actions."""

    def test_restart_protected_container(self):
        r = af._tier1_restart_container("openclaw")
        self.assertFalse(r["success"])
        self.assertEqual(r["action"], "blocked")

    def test_restart_protected_watchtower(self):
        r = af._tier1_restart_container("watchtower")
        self.assertFalse(r["success"])

    @patch("subprocess.run")
    def test_restart_allowed_container(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="chromadb")
        r = af._tier1_restart_container("chromadb")
        self.assertTrue(r["success"])

    def test_restart_unknown_container(self):
        r = af._tier1_restart_container("malicious_container")
        self.assertFalse(r["success"])
        self.assertIn("whitelist", r["reason"])

    def test_dry_run_restart(self):
        r = af._tier1_restart_container("chromadb", dry_run=True)
        self.assertTrue(r["success"])
        self.assertEqual(r["action"], "dry_run")

    def test_pull_model_not_in_whitelist(self):
        r = af._tier1_pull_model("malicious-model:latest")
        self.assertFalse(r["success"])
        self.assertIn("whitelist", r["reason"])

    def test_pull_model_no_ssh_key(self):
        with patch.object(Path, "exists", return_value=False):
            r = af._tier1_pull_model("qwen2.5:7b")
            self.assertFalse(r["success"])
            self.assertIn("SSH", r["reason"])

    @patch.object(Path, "exists", return_value=True)
    def test_pull_model_dry_run(self, mock_exists):
        r = af._tier1_pull_model("qwen2.5:7b", dry_run=True)
        self.assertTrue(r["success"])
        self.assertEqual(r["action"], "dry_run")

    def test_route_tier1_chromadb(self):
        r = af.route_tier1("chromadb", {}, dry_run=True)
        self.assertIsNotNone(r)
        self.assertEqual(r["action"], "dry_run")

    def test_route_tier1_containers_missing(self):
        r = af.route_tier1("containers", {"missing": ["sonarr"]}, dry_run=True)
        self.assertIsNotNone(r)

    def test_route_tier1_ollama_missing_model(self):
        r = af.route_tier1("ollama_gpu",
                           {"missing_models": ["nomic-embed-text"]}, dry_run=True)
        self.assertIsNotNone(r)

    def test_route_tier1_unknown_probe(self):
        r = af.route_tier1("unknown_probe", {})
        self.assertIsNone(r)


class TestTier3(unittest.TestCase):
    """Test Tier 3 proposals."""

    @patch("autofix_agent._get_probe_history")
    def test_dry_run_proposal(self, mock_hist):
        mock_hist.return_value = [{"status": "fail"}, {"status": "ok"}]
        r = af.run_tier3("chromadb", {"message": "connection refused"}, dry_run=True)
        self.assertTrue(r["success"])
        self.assertEqual(r["tier"], 3)
        self.assertIn("chromadb", r["message_preview"])


class TestRateLimiting(unittest.TestCase):
    """Test rate limiting."""

    def test_under_limit(self):
        actions = [{"ts": time.time() - 100}]
        self.assertTrue(af._rate_check(actions, 10))

    def test_over_limit(self):
        actions = [{"ts": time.time()} for _ in range(11)]
        self.assertFalse(af._rate_check(actions, 10))

    def test_old_actions_ignored(self):
        actions = [{"ts": time.time() - 7200} for _ in range(20)]
        self.assertTrue(af._rate_check(actions, 10))


class TestProcessFailures(unittest.TestCase):
    """Test main orchestration."""

    @patch("autofix_agent._load_autofix_config")
    @patch("autofix_agent._load_state")
    @patch("autofix_agent._save_state")
    def test_kill_switch(self, mock_save, mock_state, mock_config):
        mock_config.return_value = {"kill_switch": True}
        mock_state.return_value = {}
        r = af.process_failures([{"probe": "test", "status": "fail"}])
        self.assertEqual(r["action"], "killed")

    @patch("autofix_agent._load_autofix_config")
    @patch("autofix_agent._load_state")
    @patch("autofix_agent._save_state")
    @patch("autofix_agent.should_skip_probe")
    def test_skip_expected(self, mock_skip, mock_save, mock_state, mock_config):
        mock_config.return_value = {"kill_switch": False, "tier2_enabled": False,
                                    "expected_degradations": [], "never_existed": []}
        mock_state.return_value = {"tier1_actions": [], "tier2_actions": [],
                                   "tier3_proposals": []}
        mock_skip.return_value = (True, "expected degradation")
        r = af.process_failures(
            [{"probe": "ollama_nas", "status": "warn", "detail": {}}],
            dry_run=True)
        self.assertEqual(r["skipped"], 1)


class TestDiffValidation(unittest.TestCase):
    """Test diff validation safety."""

    @patch("subprocess.run")
    def test_no_changes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        r = af._validate_diff()
        self.assertFalse(r["valid"])

    @patch("subprocess.run")
    def test_too_many_files(self, mock_run):
        stat = "a.py | 1 +\nb.py | 1 +\nc.py | 1 +\nd.py | 1 +\n4 files changed"
        mock_run.return_value = MagicMock(returncode=0, stdout=stat)
        r = af._validate_diff()
        self.assertFalse(r["valid"])
        self.assertIn("too many files", r["reason"])


class TestState(unittest.TestCase):
    """Test state persistence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig = af.AUTOFIX_STATE
        af.AUTOFIX_STATE = Path(self.tmpdir) / "state.json"

    def tearDown(self):
        af.AUTOFIX_STATE = self._orig

    def test_save_load_roundtrip(self):
        state = {"total_fixes": 5, "tier1_actions": []}
        af._save_state(state)
        loaded = af._load_state()
        self.assertEqual(loaded["total_fixes"], 5)


if __name__ == "__main__":
    unittest.main(verbosity=2)
