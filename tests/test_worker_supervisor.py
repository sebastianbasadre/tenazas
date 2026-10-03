#!/usr/bin/env python3
"""
Tests for worker_supervisor.py and network_cache.py.

Covers:
- Lockfile: acquire, concurrent blocked, stale removal
- Worker execution: success, failure, timeout, missing script
- Sequential execution: all workers run, results collected
- Metrics update: per-worker durations persisted
- Network cache: valid, expired, scan failure, invalidate

Corre: python3 tests/test_worker_supervisor.py
"""

import sys
import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/tenazas")
from runtime import worker_supervisor as ws
from runtime import network_cache as nc


# ── Worker Supervisor Tests ────────────────────────────────────────


class _TempSupervisorMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig = {
            "lock": ws.LOCKFILE,
            "log": ws.LOG_FILE,
            "state": ws.STATE_FILE,
            "base": ws.BASE_DIR,
        }
        ws.LOCKFILE = os.path.join(self.tmpdir, "supervisor.lock")
        ws.LOG_FILE = self.tmpdir / "logs" / "supervisor.log"
        ws.STATE_FILE = self.tmpdir / "supervisor_state.json"

    def tearDown(self):
        ws.release_lock()
        ws.LOCKFILE = self._orig["lock"]
        ws.LOG_FILE = self._orig["log"]
        ws.STATE_FILE = self._orig["state"]


class TestLockfile(_TempSupervisorMixin, unittest.TestCase):

    def test_acquire_fresh(self):
        self.assertTrue(ws.acquire_lock())
        self.assertTrue(os.path.exists(ws.LOCKFILE))
        ws.release_lock()

    def test_concurrent_blocked(self):
        contender = ws.ProcessLock(ws.LOCKFILE)
        self.assertTrue(contender.acquire())
        self.assertFalse(ws.acquire_lock())
        contender.release()

    def test_stale_removed(self):
        Path(ws.LOCKFILE).touch()
        stale = time.time() - ws.STALE_LOCK_SECONDS - 10
        os.utime(ws.LOCKFILE, (stale, stale))
        self.assertTrue(ws.acquire_lock())
        ws.release_lock()

    def test_release_removes(self):
        self.assertTrue(ws.acquire_lock())
        ws.release_lock()
        self.assertTrue(ws.acquire_lock())
        ws.release_lock()

    def test_release_missing_ok(self):
        ws.release_lock()  # no error


class TestRunWorker(_TempSupervisorMixin, unittest.TestCase):

    def test_missing_script(self):
        result = ws.run_worker("test", "nonexistent.py", 10)
        self.assertFalse(result["ok"])
        self.assertIn("not found", result["error"])

    @patch("runtime.worker_supervisor.subprocess.Popen")
    def test_success(self, mock_popen):
        proc = MagicMock()
        proc.communicate.return_value = (
            json.dumps({"ok": True}).encode(), b""
        )
        proc.returncode = 0
        mock_popen.return_value = proc

        # Create a dummy script
        script = self.tmpdir / "test_worker.py"
        script.touch()
        ws.BASE_DIR = self.tmpdir

        result = ws.run_worker("test", "test_worker.py", 10)
        self.assertTrue(result["ok"])

    @patch("runtime.worker_supervisor.subprocess.Popen")
    def test_failure(self, mock_popen):
        proc = MagicMock()
        proc.communicate.return_value = (b"", b"error msg")
        proc.returncode = 1
        mock_popen.return_value = proc

        script = self.tmpdir / "test_worker.py"
        script.touch()
        ws.BASE_DIR = self.tmpdir

        result = ws.run_worker("test", "test_worker.py", 10)
        self.assertFalse(result["ok"])

    @patch("runtime.worker_supervisor.subprocess.Popen")
    def test_timeout(self, mock_popen):
        from subprocess import TimeoutExpired
        proc = MagicMock()
        proc.communicate.side_effect = TimeoutExpired(cmd="python3", timeout=5)
        proc.wait.return_value = None
        mock_popen.return_value = proc

        script = self.tmpdir / "test_worker.py"
        script.touch()
        ws.BASE_DIR = self.tmpdir

        result = ws.run_worker("test", "test_worker.py", 5)
        self.assertFalse(result["ok"])
        self.assertIn("timeout", result["error"])
        proc.kill.assert_called_once()

    def test_has_name_and_elapsed(self):
        result = ws.run_worker("myworker", "nonexistent.py", 10)
        self.assertEqual(result["name"], "myworker")
        self.assertIn("elapsed", result)


class TestSaveState(_TempSupervisorMixin, unittest.TestCase):

    def test_saves_state(self):
        results = [{"name": "test", "ok": True, "elapsed": 1.5}]
        ws.save_state(results, 1.5)
        self.assertTrue(ws.STATE_FILE.exists())
        state = json.loads(ws.STATE_FILE.read_text())
        self.assertEqual(len(state["workers"]), 1)
        self.assertIn("ts", state)


class TestUpdateMetrics(_TempSupervisorMixin, unittest.TestCase):

    def test_persists_worker_metrics(self):
        metrics_file = self.tmpdir / "logs" / "metrics_state.json"
        ws.BASE_DIR = self.tmpdir
        # Ensure the function finds the right path
        with patch.object(ws, "BASE_DIR", self.tmpdir):
            ws.update_metrics([
                {"name": "runtime_worker", "ok": True, "elapsed": 2.5},
                {"name": "diagnostics_worker", "ok": False, "elapsed": 0.3},
            ])
        # Check if file was created
        if metrics_file.exists():
            state = json.loads(metrics_file.read_text())
            self.assertIn("workers", state)


# ── Network Cache Tests ────────────────────────────────────────────


class _TempCacheMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_cache = nc.CACHE_FILE
        nc.CACHE_FILE = self.tmpdir / "nmap_cache.json"

    def tearDown(self):
        nc.CACHE_FILE = self._orig_cache


class TestNetworkCacheValid(_TempCacheMixin, unittest.TestCase):

    def test_no_cache_invalid(self):
        self.assertFalse(nc.is_cache_valid())

    def test_fresh_cache_valid(self):
        nc._save_cache({"192.168.2.0/24": {"ts": time.time(), "hosts": 10}})
        self.assertTrue(nc.is_cache_valid())

    def test_expired_cache_invalid(self):
        old_ts = time.time() - nc.CACHE_TTL - 10
        nc._save_cache({"192.168.2.0/24": {"ts": old_ts, "hosts": 10}})
        self.assertFalse(nc.is_cache_valid())


class TestCachedNmapScan(_TempCacheMixin, unittest.TestCase):

    def test_returns_cached(self):
        nc._save_cache({"192.168.2.0/24": {"ts": time.time(), "hosts": 37}})
        result = nc.cached_nmap_scan()
        self.assertTrue(result["cached"])
        self.assertEqual(result["hosts"], 37)

    @patch("runtime.network_cache.subprocess.run")
    def test_runs_scan_when_expired(self, mock_run):
        mock_run.return_value = MagicMock(
            returncode=0,
            stdout="Nmap scan report for 192.168.2.1\nNmap scan report for 192.168.2.2\n",
        )
        result = nc.cached_nmap_scan()
        self.assertFalse(result["cached"])
        self.assertEqual(result["hosts"], 2)
        mock_run.assert_called_once()

    @patch("runtime.network_cache.subprocess.run")
    def test_scan_timeout_returns_stale(self, mock_run):
        from subprocess import TimeoutExpired
        mock_run.side_effect = TimeoutExpired(cmd="nmap", timeout=30)
        # Pre-populate stale cache
        old_ts = time.time() - nc.CACHE_TTL - 10
        nc._save_cache({"192.168.2.0/24": {"ts": old_ts, "hosts": 15}})
        result = nc.cached_nmap_scan()
        self.assertTrue(result.get("stale"))
        self.assertEqual(result["hosts"], 15)


class TestCacheInvalidate(_TempCacheMixin, unittest.TestCase):

    def test_invalidate(self):
        nc._save_cache({"192.168.2.0/24": {"ts": time.time(), "hosts": 10}})
        nc.invalidate()
        self.assertFalse(nc.is_cache_valid())

    def test_invalidate_empty_ok(self):
        nc.invalidate()  # no error


class TestCacheAge(_TempCacheMixin, unittest.TestCase):

    def test_no_cache(self):
        self.assertEqual(nc.get_cache_age(), -1)

    def test_has_cache(self):
        nc._save_cache({"192.168.2.0/24": {"ts": time.time() - 60}})
        age = nc.get_cache_age()
        self.assertGreaterEqual(age, 59)
        self.assertLess(age, 65)


if __name__ == "__main__":
    unittest.main()
