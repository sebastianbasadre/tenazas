#!/usr/bin/env python3
"""
Tests for gpu_scheduler.py lock handling and main flow.

Corre: python3 tests/test_gpu_scheduler.py
"""

import os
import importlib.util
import sys
import tempfile
import time
import unittest
from pathlib import Path
from process_lock import ProcessLock
from unittest.mock import patch

RUNTIME = Path(__file__).resolve().parents[1] / "workspace" / "runtime"
SPEC = importlib.util.spec_from_file_location("gpu_scheduler", RUNTIME / "gpu_scheduler.py")
gs = importlib.util.module_from_spec(SPEC)
sys.modules["gpu_scheduler"] = gs
assert SPEC.loader is not None
SPEC.loader.exec_module(gs)


class _TempSchedulerMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig = {
            "lock": gs.LOCKFILE,
            "log": gs.LOG_FILE,
            "base": gs.BASE_DIR,
        }
        gs.LOCKFILE = os.path.join(self.tmpdir, "gpu_scheduler.lock")
        gs.LOG_FILE = self.tmpdir / "logs" / "gpu_scheduler.log"
        gs.BASE_DIR = self.tmpdir

    def tearDown(self):
        gs.release_lock()
        gs.LOCKFILE = self._orig["lock"]
        gs.LOG_FILE = self._orig["log"]
        gs.BASE_DIR = self._orig["base"]


class TestLockfile(_TempSchedulerMixin, unittest.TestCase):

    def test_acquire_fresh(self):
        self.assertTrue(gs.acquire_lock())
        self.assertTrue(os.path.exists(gs.LOCKFILE))
        gs.release_lock()

    def test_concurrent_blocked(self):
        contender = ProcessLock(gs.LOCKFILE)
        self.assertTrue(contender.acquire())
        self.assertFalse(gs.acquire_lock())
        contender.release()

    def test_stale_removed(self):
        Path(gs.LOCKFILE).touch()
        stale = time.time() - gs.STALE_LOCK_SECONDS - 10
        os.utime(gs.LOCKFILE, (stale, stale))
        self.assertTrue(gs.acquire_lock())
        gs.release_lock()

    def test_release_removes(self):
        Path(gs.LOCKFILE).touch()
        gs.release_lock()
        contender = ProcessLock(gs.LOCKFILE)
        self.assertTrue(contender.acquire())
        contender.release()


class TestMainFlow(_TempSchedulerMixin, unittest.TestCase):

    @patch("gpu_scheduler.optimize")
    def test_optimize_acquires_and_releases(self, mock_optimize):
        with patch.object(sys, "argv", ["gpu_scheduler.py", "--optimize"]):
            gs.main()
        mock_optimize.assert_called_once()
        contender = ProcessLock(gs.LOCKFILE)
        self.assertTrue(contender.acquire())
        contender.release()

    @patch("gpu_scheduler.run_keepalive")
    def test_keepalive_acquires_and_releases(self, mock_keepalive):
        with patch.object(sys, "argv", ["gpu_scheduler.py", "--keepalive"]):
            gs.main()
        mock_keepalive.assert_called_once()
        contender = ProcessLock(gs.LOCKFILE)
        self.assertTrue(contender.acquire())
        contender.release()

    @patch("gpu_scheduler.run_keepalive")
    def test_keepalive_skips_if_locked(self, mock_keepalive):
        contender = ProcessLock(gs.LOCKFILE)
        self.assertTrue(contender.acquire())
        with patch.object(sys, "argv", ["gpu_scheduler.py", "--keepalive"]):
            gs.main()
        mock_keepalive.assert_not_called()
        contender.release()

    @patch("gpu_scheduler.run_keepalive")
    def test_keepalive_releases_lock_on_error(self, mock_keepalive):
        mock_keepalive.side_effect = RuntimeError("boom")
        with patch.object(sys, "argv", ["gpu_scheduler.py", "--keepalive"]):
            with self.assertRaises(RuntimeError):
                gs.main()
        contender = ProcessLock(gs.LOCKFILE)
        self.assertTrue(contender.acquire())
        contender.release()

    @patch("gpu_scheduler.print_status")
    def test_status_does_not_lock(self, mock_status):
        with patch.object(sys, "argv", ["gpu_scheduler.py", "--status"]):
            gs.main()
        mock_status.assert_called_once()
        contender = ProcessLock(gs.LOCKFILE)
        self.assertTrue(contender.acquire())
        contender.release()


if __name__ == "__main__":
    unittest.main()
