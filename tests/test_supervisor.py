#!/usr/bin/env python3
"""
Tests for runtime_supervisor.py — Deadlock/timeout protection for runtime loop.

Covers:
- Lock acquisition (fresh, concurrent, stale)
- Lock release
- Runtime execution (normal exit, error exit, timeout kill)
- Main flow (lock → run → release)
- Logging

Corre: python3 tests/test_supervisor.py
"""

import sys
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/tenazas")
from runtime import runtime_supervisor as sv


class TestAcquireLock(unittest.TestCase):
    """Lock acquisition: fresh, concurrent, stale."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_lockfile = sv.LOCKFILE
        self._orig_logfile = sv.LOGFILE
        sv.LOCKFILE = os.path.join(self.tmpdir, "runtime.lock")
        sv.LOGFILE = os.path.join(self.tmpdir, "supervisor.log")

    def tearDown(self):
        sv.LOCKFILE = self._orig_lockfile
        sv.LOGFILE = self._orig_logfile

    def test_fresh_lock_acquired(self):
        """No lockfile exists → acquire succeeds."""
        self.assertTrue(sv.acquire_lock())
        self.assertTrue(os.path.exists(sv.LOCKFILE))

    def test_concurrent_lock_blocked(self):
        """Recent lockfile → acquire fails (another run in progress)."""
        Path(sv.LOCKFILE).touch()
        self.assertFalse(sv.acquire_lock())

    def test_stale_lock_removed(self):
        """Old lockfile (>STALE_LOCK_SECONDS) → removed and re-acquired."""
        Path(sv.LOCKFILE).touch()
        stale_time = time.time() - sv.STALE_LOCK_SECONDS - 10
        os.utime(sv.LOCKFILE, (stale_time, stale_time))
        self.assertTrue(sv.acquire_lock())
        self.assertTrue(os.path.exists(sv.LOCKFILE))

    def test_lock_just_under_stale_threshold(self):
        """Lockfile just under stale threshold → still blocked."""
        Path(sv.LOCKFILE).touch()
        recent_time = time.time() - sv.STALE_LOCK_SECONDS + 30
        os.utime(sv.LOCKFILE, (recent_time, recent_time))
        self.assertFalse(sv.acquire_lock())


class TestReleaseLock(unittest.TestCase):
    """Lock release."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_lockfile = sv.LOCKFILE
        self._orig_logfile = sv.LOGFILE
        sv.LOCKFILE = os.path.join(self.tmpdir, "runtime.lock")
        sv.LOGFILE = os.path.join(self.tmpdir, "supervisor.log")

    def tearDown(self):
        sv.LOCKFILE = self._orig_lockfile
        sv.LOGFILE = self._orig_logfile

    def test_release_removes_lockfile(self):
        Path(sv.LOCKFILE).touch()
        sv.release_lock()
        self.assertFalse(os.path.exists(sv.LOCKFILE))

    def test_release_no_lockfile_ok(self):
        """Release when no lockfile → no error."""
        sv.release_lock()  # should not raise


class TestRunRuntime(unittest.TestCase):
    """Runtime execution: normal, error, timeout."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_logfile = sv.LOGFILE
        self._orig_script = sv.RUNTIME_SCRIPT
        self._orig_timeout = sv.MAX_RUNTIME_SECONDS
        sv.LOGFILE = os.path.join(self.tmpdir, "supervisor.log")

    def tearDown(self):
        sv.LOGFILE = self._orig_logfile
        sv.RUNTIME_SCRIPT = self._orig_script
        sv.MAX_RUNTIME_SECONDS = self._orig_timeout

    @patch("runtime.runtime_supervisor.subprocess.Popen")
    def test_normal_exit(self, mock_popen):
        """Runtime exits with code 0 → logged as normal."""
        proc = MagicMock()
        proc.poll.return_value = 0
        proc.returncode = 0
        mock_popen.return_value = proc

        sv.run_runtime()

        log_content = Path(sv.LOGFILE).read_text()
        self.assertIn("finished normally", log_content)

    @patch("runtime.runtime_supervisor.subprocess.Popen")
    def test_error_exit(self, mock_popen):
        """Runtime exits with non-zero code → logged with stderr."""
        proc = MagicMock()
        proc.poll.return_value = 1
        proc.returncode = 1
        proc.stderr.read.return_value = b"some error"
        mock_popen.return_value = proc

        sv.run_runtime()

        log_content = Path(sv.LOGFILE).read_text()
        self.assertIn("exited with code 1", log_content)

    @patch("runtime.runtime_supervisor.time.sleep")
    @patch("runtime.runtime_supervisor.subprocess.Popen")
    def test_timeout_kills_process(self, mock_popen, mock_sleep):
        """Runtime exceeds MAX_RUNTIME_SECONDS → killed."""
        proc = MagicMock()
        proc.poll.return_value = None  # never finishes
        proc.wait.return_value = None
        mock_popen.return_value = proc

        sv.MAX_RUNTIME_SECONDS = 0  # immediate timeout

        sv.run_runtime()

        proc.kill.assert_called_once()
        log_content = Path(sv.LOGFILE).read_text()
        self.assertIn("timeout exceeded", log_content)

    @patch("runtime.runtime_supervisor.time.sleep")
    @patch("runtime.runtime_supervisor.subprocess.Popen")
    def test_timeout_terminate_fallback(self, mock_popen, mock_sleep):
        """If wait() after kill times out → terminate() called."""
        from subprocess import TimeoutExpired

        proc = MagicMock()
        proc.poll.return_value = None
        proc.wait.side_effect = TimeoutExpired(cmd="python3", timeout=5)
        mock_popen.return_value = proc

        sv.MAX_RUNTIME_SECONDS = 0

        sv.run_runtime()

        proc.kill.assert_called_once()
        proc.terminate.assert_called_once()

    @patch("runtime.runtime_supervisor.subprocess.Popen")
    def test_popen_uses_list_not_shell(self, mock_popen):
        """Subprocess must use list args (not shell=True)."""
        proc = MagicMock()
        proc.poll.return_value = 0
        proc.returncode = 0
        mock_popen.return_value = proc

        sv.run_runtime()

        args, kwargs = mock_popen.call_args
        self.assertIsInstance(args[0], list)
        self.assertNotIn("shell", kwargs)


class TestMainFlow(unittest.TestCase):
    """Main entry: lock → run → release."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_lockfile = sv.LOCKFILE
        self._orig_logfile = sv.LOGFILE
        sv.LOCKFILE = os.path.join(self.tmpdir, "runtime.lock")
        sv.LOGFILE = os.path.join(self.tmpdir, "supervisor.log")

    def tearDown(self):
        sv.LOCKFILE = self._orig_lockfile
        sv.LOGFILE = self._orig_logfile

    @patch("runtime.runtime_supervisor.run_runtime")
    def test_main_acquires_and_releases(self, mock_run):
        """main() acquires lock, runs, releases lock."""
        sv.main()
        mock_run.assert_called_once()
        self.assertFalse(os.path.exists(sv.LOCKFILE))

    @patch("runtime.runtime_supervisor.run_runtime")
    def test_main_releases_lock_on_error(self, mock_run):
        """Lock is released even if run_runtime raises."""
        mock_run.side_effect = RuntimeError("boom")
        with self.assertRaises(RuntimeError):
            sv.main()
        self.assertFalse(os.path.exists(sv.LOCKFILE))

    @patch("runtime.runtime_supervisor.run_runtime")
    def test_main_skips_if_locked(self, mock_run):
        """main() does nothing if lock is held."""
        Path(sv.LOCKFILE).touch()
        sv.main()
        mock_run.assert_not_called()

    @patch("runtime.runtime_supervisor.run_runtime")
    def test_main_stale_lock_proceeds(self, mock_run):
        """main() removes stale lock and proceeds."""
        Path(sv.LOCKFILE).touch()
        stale_time = time.time() - sv.STALE_LOCK_SECONDS - 10
        os.utime(sv.LOCKFILE, (stale_time, stale_time))
        sv.main()
        mock_run.assert_called_once()


class TestLogging(unittest.TestCase):
    """Supervisor logging."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_logfile = sv.LOGFILE
        sv.LOGFILE = os.path.join(self.tmpdir, "supervisor.log")

    def tearDown(self):
        sv.LOGFILE = self._orig_logfile

    def test_log_creates_directory(self):
        sv.LOGFILE = os.path.join(self.tmpdir, "subdir", "supervisor.log")
        sv.log("test message")
        self.assertTrue(os.path.exists(sv.LOGFILE))

    def test_log_contains_timestamp(self):
        sv.log("hello")
        content = Path(sv.LOGFILE).read_text()
        # format: YYYY-MM-DD HH:MM:SS
        self.assertRegex(content, r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}")

    def test_log_contains_message(self):
        sv.log("test message 123")
        content = Path(sv.LOGFILE).read_text()
        self.assertIn("test message 123", content)

    def test_log_appends(self):
        sv.log("first")
        sv.log("second")
        lines = Path(sv.LOGFILE).read_text().strip().split("\n")
        self.assertEqual(len(lines), 2)


class TestConstants(unittest.TestCase):
    """Verify safety constants."""

    def test_timeout_is_reasonable(self):
        """Timeout should be 60-300 seconds."""
        self.assertGreaterEqual(sv.MAX_RUNTIME_SECONDS, 60)
        self.assertLessEqual(sv.MAX_RUNTIME_SECONDS, 300)

    def test_stale_threshold_greater_than_timeout(self):
        """Stale lock must be longer than max runtime."""
        self.assertGreater(sv.STALE_LOCK_SECONDS, sv.MAX_RUNTIME_SECONDS)

    def test_runtime_script_path(self):
        self.assertIn("runtime.py", sv.RUNTIME_SCRIPT)


if __name__ == "__main__":
    unittest.main()
