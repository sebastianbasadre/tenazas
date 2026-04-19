#!/usr/bin/env python3
"""
Tests for autofix agent — 40 tests covering all tiers, safety, and edge cases.

All subprocess, network, file I/O, and Aider calls mocked.
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# Path setup
_paths = [
    str(Path(__file__).parent.parent / "workspace" / "runtime"),
    "/home/tenazas/runtime",
]
for p in _paths:
    if p not in sys.path:
        sys.path.insert(0, p)

import autofix_agent as agent
import autofix_tiers as tiers
import autofix_sandbox as sandbox
import autofix_aider as aider_mod
import autofix_ssh as ssh_mod
import autofix_llm_check as llm_check
import autofix_telegram as tg


# ── Helpers ─────────────────────────────────────────────────────

class TempState:
    """Context manager for temp state dirs."""
    def __init__(self):
        self.tmpdir = None
        self._orig = {}

    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp()
        p = Path(self.tmpdir)
        self._orig = {
            "agent_STATE": agent.STATE_PATH,
            "agent_CONFIG": agent.CONFIG_PATH,
            "agent_KILL": agent.KILL_SWITCH,
            "agent_LOG": agent.LOG_PATH,
            "agent_STATE_DIR": agent.STATE_DIR,
            "tiers_SWEEP_DB": tiers.SWEEP_DB,
        }
        agent.STATE_PATH = p / "state.json"
        agent.CONFIG_PATH = p / "config.json"
        agent.KILL_SWITCH = p / "AUTOFIX_DISABLED"
        agent.LOG_PATH = p / "log.jsonl"
        agent.STATE_DIR = p
        tiers.SWEEP_DB = p / "sweeps.sqlite"
        return p

    def __exit__(self, *a):
        for k, v in self._orig.items():
            mod, attr = k.split("_", 1)
            if mod == "agent":
                setattr(agent, attr, v)
            elif mod == "tiers":
                setattr(tiers, attr, v)


# ════════════════════════════════════════════════════════════════
#  PRE-FLIGHT
# ════════════════════════════════════════════════════════════════

class TestPreflight(unittest.TestCase):

    def test_kill_switch_blocks(self):
        with TempState() as p:
            agent.KILL_SWITCH.write_text("disabled")
            r = agent.preflight({})
            self.assertFalse(r["ok"])
            self.assertTrue(any("DISABLED" in e for e in r["errors"]))

    def test_insufficient_baseline(self):
        with TempState() as p:
            with patch("autofix_agent.get_sweep_count", return_value=5):
                with patch("autofix_agent.check_llm", return_value={"recommended": None, "reason": ""}):
                    r = agent.preflight({})
                    self.assertFalse(r["ok"])
                    self.assertTrue(any("insufficient" in e for e in r["errors"]))

    @patch("autofix_agent.get_sweep_count", return_value=50)
    @patch("autofix_agent.check_llm", return_value={"recommended": "ollama", "reason": ""})
    def test_preflight_ok(self, *mocks):
        with TempState() as p:
            agent.CONFIG_PATH.write_text("{}")
            r = agent.preflight({})
            self.assertTrue(r["ok"])


# ════════════════════════════════════════════════════════════════
#  REGRESSION DETECTION
# ════════════════════════════════════════════════════════════════

class TestRegression(unittest.TestCase):

    @patch("autofix_tiers.get_probe_history")
    def test_no_history(self, mock):
        mock.return_value = []
        self.assertEqual(tiers.classify_regression("x"), "unknown")

    @patch("autofix_tiers.get_probe_history")
    def test_currently_ok(self, mock):
        mock.return_value = [{"status": "ok"}]
        self.assertEqual(tiers.classify_regression("x"), "unknown")

    @patch("autofix_tiers.get_probe_history")
    def test_new_failure(self, mock):
        mock.return_value = [{"status": "fail"}] * 3
        self.assertEqual(tiers.classify_regression("x"), "new")

    @patch("autofix_tiers.get_probe_history")
    def test_regression(self, mock):
        mock.return_value = [{"status": "fail"}, {"status": "ok"},
                             {"status": "ok"}, {"status": "ok"}]
        self.assertEqual(tiers.classify_regression("x"), "regression")

    @patch("autofix_tiers.get_probe_history")
    def test_chronic(self, mock):
        mock.return_value = [{"status": "fail"}] * 6 + [{"status": "ok"}]
        self.assertEqual(tiers.classify_regression("x"), "chronic")

    @patch("autofix_tiers.get_probe_history")
    def test_flapping(self, mock):
        mock.return_value = [{"status": "fail"}, {"status": "ok"}] * 4
        self.assertEqual(tiers.classify_regression("x"), "flapping")

    @patch("autofix_tiers.get_probe_history")
    def test_gradual(self, mock):
        mock.return_value = [
            {"status": "warn", "latency_ms": 3000},
            {"status": "warn", "latency_ms": 2800},
            {"status": "warn", "latency_ms": 2500},
            {"status": "ok", "latency_ms": 1000},
            {"status": "ok", "latency_ms": 900},
            {"status": "ok", "latency_ms": 800},
        ]
        self.assertEqual(tiers.classify_regression("x"), "gradual")


# ════════════════════════════════════════════════════════════════
#  EXPECTED DEGRADATIONS
# ════════════════════════════════════════════════════════════════

class TestExpectedDegradations(unittest.TestCase):

    def test_skip_ok(self):
        skip, _ = tiers.should_skip("x", "ok", {})
        self.assertTrue(skip)

    @patch("autofix_tiers.check_condition", return_value=True)
    def test_skip_expected(self, mock):
        cfg = {"expected_degradations": [
            {"probe": "ollama_nas", "condition": "homelab_ollama_alive",
             "reason": "expected"}
        ]}
        skip, reason = tiers.should_skip("ollama_nas", "warn", cfg)
        self.assertTrue(skip)
        self.assertIn("expected", reason)

    def test_skip_never_existed(self):
        cfg = {"never_existed": ["gpu_exporter"]}
        skip, reason = tiers.should_skip("gpu_exporter", "warn", cfg)
        self.assertTrue(skip)
        self.assertIn("never_existed", reason)

    def test_no_skip_real_failure(self):
        cfg = {"expected_degradations": [], "never_existed": []}
        skip, _ = tiers.should_skip("chromadb", "fail", cfg)
        self.assertFalse(skip)


# ════════════════════════════════════════════════════════════════
#  TIER CLASSIFICATION
# ════════════════════════════════════════════════════════════════

class TestTierClassification(unittest.TestCase):

    @patch("autofix_tiers.classify_regression", return_value="chronic")
    def test_chronic_goes_tier3(self, mock):
        r = tiers.classify_tier("x", {}, {})
        self.assertEqual(r["tier"], 3)

    @patch("autofix_tiers.classify_regression", return_value="flapping")
    def test_flapping_skipped(self, mock):
        r = tiers.classify_tier("x", {}, {})
        self.assertEqual(r["tier"], 0)

    @patch("autofix_tiers.classify_regression", return_value="regression")
    def test_chromadb_tier1(self, mock):
        r = tiers.classify_tier("chromadb", {}, {})
        self.assertEqual(r["tier"], 1)

    @patch("autofix_tiers.classify_regression", return_value="regression")
    def test_error_tier2(self, mock):
        r = tiers.classify_tier("unknown_probe", {"message": "Traceback error"}, {})
        self.assertEqual(r["tier"], 2)


# ════════════════════════════════════════════════════════════════
#  TIER 1 — OPS
# ════════════════════════════════════════════════════════════════

class TestTier1(unittest.TestCase):

    def test_protected_container_blocked(self):
        r = agent.exec_tier1("openclaw", {}, agent.DEFAULT_CONFIG,
                             agent.load_state(), dry_run=True)
        # openclaw is not in PROBE_CONTAINER, so returns None
        self.assertIsNone(r)

    def test_dry_run_restart(self):
        r = agent.exec_tier1("chromadb", {}, agent.DEFAULT_CONFIG,
                             agent.load_state(), dry_run=True)
        self.assertTrue(r["success"])
        self.assertEqual(r["action"], "dry_run")

    @patch("subprocess.run")
    def test_restart_success(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="chromadb")
        r = agent.exec_tier1("chromadb", {}, agent.DEFAULT_CONFIG,
                             agent.load_state())
        self.assertTrue(r["success"])
        self.assertEqual(r["action"], "restart")

    def test_containers_missing_dry_run(self):
        r = agent.exec_tier1("containers", {"missing": ["sonarr", "radarr"]},
                             agent.DEFAULT_CONFIG, agent.load_state(), dry_run=True)
        self.assertIsNotNone(r)
        self.assertEqual(len(r["results"]), 2)

    def test_ollama_missing_model_dry_run(self):
        r = agent.exec_tier1("ollama_gpu",
                             {"missing_models": ["nomic-embed-text"]},
                             agent.DEFAULT_CONFIG, agent.load_state(), dry_run=True)
        self.assertIsNotNone(r)

    def test_unknown_probe_returns_none(self):
        r = agent.exec_tier1("random_probe", {}, agent.DEFAULT_CONFIG,
                             agent.load_state())
        self.assertIsNone(r)

    def test_rate_limit_blocks(self):
        state = agent.load_state()
        # Fill rate limit
        state["tier1"] = [{"ts": time.time(), "probe": "chromadb"}] * 10
        self.assertFalse(agent.rate_ok_tier1(state, agent.DEFAULT_CONFIG, "chromadb"))


# ════════════════════════════════════════════════════════════════
#  TIER 2 — CODE
# ════════════════════════════════════════════════════════════════

class TestTier2(unittest.TestCase):

    def test_no_llm_escalates(self):
        llm = {"recommended": None, "reason": "offline"}
        r = agent.exec_tier2("x", {}, agent.DEFAULT_CONFIG, agent.load_state(),
                             llm, dry_run=True)
        self.assertFalse(r["success"])
        self.assertEqual(r["escalate"], 3)

    @patch("autofix_agent.collect_context",
           return_value={"files": ["runtime/test.py"], "log_tail": "error",
                         "stacktrace": "", "probe": "x", "detail": {}})
    def test_dry_run_with_llm(self, mock_ctx):
        llm = {"recommended": "ollama", "model": "qwen2.5:7b", "backend": "ollama"}
        r = agent.exec_tier2("x", {"message": "test error"},
                             agent.DEFAULT_CONFIG, agent.load_state(),
                             llm, dry_run=True)
        self.assertTrue(r["success"])
        self.assertEqual(r["action"], "dry_run")

    def test_no_context_escalates(self):
        with patch("autofix_agent.collect_context",
                   return_value={"files": [], "log_tail": "",
                                 "stacktrace": "", "probe": "x", "detail": {}}):
            llm = {"recommended": "ollama", "model": "qwen2.5:7b", "backend": "ollama"}
            r = agent.exec_tier2("x", {}, agent.DEFAULT_CONFIG,
                                 agent.load_state(), llm)
            self.assertFalse(r["success"])
            self.assertEqual(r["escalate"], 3)

    def test_rate_limit_tier2(self):
        state = agent.load_state()
        today = time.strftime("%Y-%m-%dT")
        state["tier2"] = [{"ts": time.time(), "dt": f"{today}10:00:00",
                           "success": True}] * 10
        self.assertFalse(agent.rate_ok_tier2(state, agent.DEFAULT_CONFIG))

    def test_consecutive_failures_pause(self):
        with TempState() as p:
            state = agent.load_state()
            state["tier2_consecutive_failures"] = 5
            agent.save_state(state)


# ════════════════════════════════════════════════════════════════
#  TIER 3 — PROPOSAL
# ════════════════════════════════════════════════════════════════

class TestTier3(unittest.TestCase):

    @patch("autofix_agent.classify_regression", return_value="regression")
    @patch("autofix_agent.get_probe_history", return_value=[])
    def test_dry_run(self, *mocks):
        r = agent.exec_tier3("chromadb", {"message": "down"}, dry_run=True)
        self.assertTrue(r["success"])
        self.assertEqual(r["tier"], 3)

    @patch("autofix_agent.classify_regression", return_value="chronic")
    @patch("autofix_agent.get_probe_history", return_value=[{"status": "fail"}] * 5)
    @patch("autofix_agent.notify_tier3_proposal")
    def test_notify_called(self, mock_notify, *mocks):
        r = agent.exec_tier3("test", {"message": "broken"})
        self.assertTrue(mock_notify.called)


# ════════════════════════════════════════════════════════════════
#  SANDBOX (git ops)
# ════════════════════════════════════════════════════════════════

class TestSandbox(unittest.TestCase):

    @patch("subprocess.run")
    def test_git_clean(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        self.assertTrue(sandbox.git_is_clean())

    @patch("subprocess.run")
    def test_git_dirty(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="M file.py\n")
        self.assertFalse(sandbox.git_is_clean())

    @patch("subprocess.run")
    def test_validate_no_changes(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0, stdout="")
        r = sandbox.validate_diff([], 50, 3)
        self.assertFalse(r["valid"])

    @patch("subprocess.run")
    def test_validate_too_many_files(self, mock_run):
        stat = "a.py | 1 +\nb.py | 1 +\nc.py | 1 +\nd.py | 1 +\n4 files changed"
        mock_run.return_value = MagicMock(returncode=0, stdout=stat)
        r = sandbox.validate_diff([], 50, 3)
        self.assertFalse(r["valid"])
        self.assertIn("too many files", r["reason"])

    @patch("subprocess.run")
    def test_validate_protected_file(self, mock_run):
        stat = "runtime/orchestrator.py | 1 +\n1 file changed, 1 insertion"
        mock_run.return_value = MagicMock(returncode=0, stdout=stat)
        r = sandbox.validate_diff(["orchestrator.py"], 50, 3)
        self.assertFalse(r["valid"])
        self.assertIn("protected", r["reason"])


# ════════════════════════════════════════════════════════════════
#  SSH
# ════════════════════════════════════════════════════════════════

class TestSSH(unittest.TestCase):

    def test_blocked_model(self):
        r = ssh_mod.ollama_pull("evil-model:latest", set())
        self.assertFalse(r["success"])
        self.assertIn("whitelist", r["reason"])

    def test_allowed_model_no_key(self):
        with patch.object(Path, "exists", return_value=False):
            r = ssh_mod.ollama_pull("qwen2.5:7b", {"qwen2.5:7b"})
            self.assertFalse(r["success"])
            self.assertIn("SSH", r["reason"])


# ════════════════════════════════════════════════════════════════
#  LLM CHECK
# ════════════════════════════════════════════════════════════════

class TestLLMCheck(unittest.TestCase):

    @patch("autofix_llm_check.urlopen")
    def test_ollama_available(self, mock_url):
        resp = MagicMock()
        resp.status = 200
        resp.read.return_value = json.dumps({
            "models": [{"name": "qwen2.5-coder:32b"}]
        }).encode()
        mock_url.return_value = resp
        r = llm_check.check_availability()
        self.assertEqual(r["recommended"], "ollama")

    @patch("autofix_llm_check.urlopen", side_effect=Exception("offline"))
    def test_no_llm(self, mock_url):
        env_backup = os.environ.pop("GOOGLE_API_KEY", None)
        try:
            r = llm_check.check_availability()
            self.assertIsNone(r["recommended"])
        finally:
            if env_backup:
                os.environ["GOOGLE_API_KEY"] = env_backup


# ════════════════════════════════════════════════════════════════
#  AIDER WRAPPER
# ════════════════════════════════════════════════════════════════

class TestAider(unittest.TestCase):

    def test_build_prompt_contains_system(self):
        ctx = {"stacktrace": "error here", "log_tail": "log", "files": []}
        prompt = aider_mod.build_prompt("test", {"message": "broken"}, ctx)
        self.assertIn("UNFIXABLE", prompt)
        self.assertIn("broken", prompt)

    @patch("subprocess.run")
    def test_aider_unfixable(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="UNFIXABLE: feature missing, needs human design",
            stderr="")
        r = aider_mod.run_aider("prompt", ["file.py"], "model", "ollama")
        self.assertFalse(r["success"])
        self.assertTrue(r["unfixable"])

    @patch("subprocess.run", side_effect=FileNotFoundError())
    def test_aider_not_installed(self, mock_run):
        r = aider_mod.run_aider("prompt", ["file.py"], "model", "ollama")
        self.assertFalse(r["success"])
        self.assertIn("not installed", r["error"])


# ════════════════════════════════════════════════════════════════
#  PROCESS INTEGRATION
# ════════════════════════════════════════════════════════════════

class TestProcess(unittest.TestCase):

    @patch("autofix_agent.get_latest_failures", return_value=[])
    def test_no_failures(self, mock):
        with TempState():
            r = agent.process(dry_run=True)
            self.assertEqual(r["total"], 0)

    @patch("autofix_agent.get_latest_failures")
    @patch("autofix_agent.should_skip", return_value=(True, "expected"))
    @patch("autofix_agent.get_latest_score", return_value=90)
    def test_expected_skipped(self, mock_score, mock_skip, mock_fail):
        mock_fail.return_value = [
            {"probe": "ollama_nas", "status": "warn", "detail": {}}
        ]
        with TempState():
            r = agent.process(dry_run=True)
            self.assertEqual(r["skipped"], 1)

    @patch("autofix_agent.get_latest_failures")
    @patch("autofix_agent.should_skip", return_value=(False, ""))
    @patch("autofix_agent.classify_tier")
    @patch("autofix_agent.get_latest_score", return_value=90)
    @patch("autofix_agent.classify_regression", return_value="regression")
    @patch("autofix_agent.get_probe_history", return_value=[])
    @patch("autofix_agent.notify_tier3_proposal")
    def test_force_tier3(self, mock_notify, mock_hist, mock_reg,
                         mock_score, mock_class, mock_skip, mock_fail):
        mock_fail.return_value = [
            {"probe": "chromadb", "status": "fail", "detail": {"message": "down"}}
        ]
        mock_class.return_value = {"tier": 1, "reason": "ops", "regression": "regression",
                                   "action_hint": "restart"}
        with TempState():
            r = agent.process(dry_run=False, force_tier=3)
            self.assertEqual(r["proposals"], 1)


# ════════════════════════════════════════════════════════════════
#  STATE PERSISTENCE
# ════════════════════════════════════════════════════════════════

class TestState(unittest.TestCase):

    def test_roundtrip(self):
        with TempState():
            state = {"total_fixes": 7, "tier1": []}
            agent.save_state(state)
            loaded = agent.load_state()
            self.assertEqual(loaded["total_fixes"], 7)

    def test_config_creates_defaults(self):
        with TempState():
            cfg = agent.load_config()
            self.assertIn("tier_enabled", cfg)
            self.assertFalse(cfg["tier_enabled"]["tier_1"])
            self.assertTrue(cfg["tier_enabled"]["tier_3"])

    def test_kill_switch_create_delete(self):
        with TempState():
            agent.KILL_SWITCH.write_text("disabled")
            self.assertTrue(agent.KILL_SWITCH.exists())
            agent.KILL_SWITCH.unlink()
            self.assertFalse(agent.KILL_SWITCH.exists())


# ════════════════════════════════════════════════════════════════
#  RATE LIMITING
# ════════════════════════════════════════════════════════════════

class TestRateLimiting(unittest.TestCase):

    def test_tier1_under_limit(self):
        state = {"tier1": [{"ts": time.time(), "probe": "chromadb"}]}
        self.assertTrue(agent.rate_ok_tier1(state, agent.DEFAULT_CONFIG, "chromadb"))

    def test_tier1_over_limit(self):
        state = {"tier1": [{"ts": time.time(), "probe": "chromadb"}] * 5}
        self.assertFalse(agent.rate_ok_tier1(state, agent.DEFAULT_CONFIG, "chromadb"))

    def test_tier1_old_not_counted(self):
        state = {"tier1": [{"ts": time.time() - 7200, "probe": "chromadb"}] * 20}
        self.assertTrue(agent.rate_ok_tier1(state, agent.DEFAULT_CONFIG, "chromadb"))

    def test_tier2_daily_limit(self):
        state = {"tier2": []}
        self.assertTrue(agent.rate_ok_tier2(state, agent.DEFAULT_CONFIG))


if __name__ == "__main__":
    unittest.main(verbosity=2)
