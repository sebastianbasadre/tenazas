#!/usr/bin/env python3
"""
Tests for runtime stability: lockfile, stale detection, timeout,
nmap cache, devin_lite crash capture.
Corre: python3 tests/test_stability.py
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
from runtime import runtime as rt
from runtime import devin_lite


class TestLockfile(unittest.TestCase):
    """Lockfile prevents concurrent runs and detects stale locks."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_lock = rt.LOCK_FILE
        self._orig_log_dir = rt.LOGS_DIR
        rt.LOCK_FILE = Path(self.tmpdir) / "runtime.lock"
        rt.LOGS_DIR = Path(self.tmpdir) / "logs"

    def tearDown(self):
        rt.LOCK_FILE = self._orig_lock
        rt.LOGS_DIR = self._orig_log_dir

    def test_acquire_fresh(self):
        """No lock file → acquire succeeds."""
        self.assertTrue(rt._acquire_lock())
        self.assertTrue(rt.LOCK_FILE.exists())
        rt._release_lock()

    def test_double_acquire_blocked(self):
        """Second acquire fails while first holds lock."""
        self.assertTrue(rt._acquire_lock())
        self.assertFalse(rt._acquire_lock())
        rt._release_lock()

    def test_release_removes_file(self):
        rt._acquire_lock()
        rt._release_lock()
        self.assertFalse(rt.LOCK_FILE.exists())

    def test_stale_lock_recovered(self):
        """Lock older than LOCK_STALE_SECONDS is treated as stale."""
        rt.LOCK_FILE.write_text("99999")
        # Backdate the file 15 minutes
        old_time = time.time() - 900
        os.utime(rt.LOCK_FILE, (old_time, old_time))
        self.assertTrue(rt._acquire_lock())
        rt._release_lock()

    def test_recent_lock_blocks(self):
        """Lock younger than LOCK_STALE_SECONDS blocks."""
        rt.LOCK_FILE.write_text("99999")
        # Touch recently
        rt.LOCK_FILE.touch()
        self.assertFalse(rt._acquire_lock())


class TestNmapCache(unittest.TestCase):
    """nmap scans are rate-limited via cache file."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_cache = rt.NMAP_CACHE_FILE
        self._orig_state = rt.STATE_FILE
        rt.NMAP_CACHE_FILE = Path(self.tmpdir) / "nmap_cache.json"
        rt.STATE_FILE = Path(self.tmpdir) / "state.json"

    def tearDown(self):
        rt.NMAP_CACHE_FILE = self._orig_cache
        rt.STATE_FILE = self._orig_state

    @patch("runtime.executors.check_network_devices", return_value={"count": 15})
    @patch("runtime.executors.check_ollama_nas", return_value={"ok": False})
    @patch("runtime.executors.check_ollama_notebook", return_value={"ok": True})
    @patch("runtime.executors.check_public_ip", return_value={"ip": "1.2.3.4"})
    @patch("runtime.executors.check_vpn", return_value={"ok": True, "ip": "82.1.2.3"})
    @patch("runtime.executors.check_vpn_country", return_value="ES")
    @patch("runtime.executors.check_internet", return_value={"ok": True, "latency_ms": 10})
    @patch("runtime.executors.check_containers", return_value=[])
    @patch("runtime.executors.write_ollama_status")
    def test_first_run_scans(self, *mocks):
        """First run should actually call nmap."""
        rt.update_state()
        from runtime import executors
        executors.check_network_devices.assert_called_once()

    @patch("runtime.executors.check_network_devices", return_value={"count": 15})
    @patch("runtime.executors.check_ollama_nas", return_value={"ok": False})
    @patch("runtime.executors.check_ollama_notebook", return_value={"ok": True})
    @patch("runtime.executors.check_public_ip", return_value={"ip": "1.2.3.4"})
    @patch("runtime.executors.check_vpn", return_value={"ok": True, "ip": "82.1.2.3"})
    @patch("runtime.executors.check_vpn_country", return_value="ES")
    @patch("runtime.executors.check_internet", return_value={"ok": True, "latency_ms": 10})
    @patch("runtime.executors.check_containers", return_value=[])
    @patch("runtime.executors.write_ollama_status")
    def test_cached_run_skips_nmap(self, *mocks):
        """Second run within 30 min should skip nmap."""
        # Seed cache as recent
        rt.save_json(rt.NMAP_CACHE_FILE, {"last_scan": time.time(), "hosts": 12})
        rt.update_state()
        from runtime import executors
        executors.check_network_devices.assert_not_called()

    def test_cache_file_format(self):
        rt.save_json(rt.NMAP_CACHE_FILE, {"last_scan": time.time(), "hosts": 10})
        data = rt.load_json(rt.NMAP_CACHE_FILE)
        self.assertIn("last_scan", data)
        self.assertIn("hosts", data)


class TestDevinLite(unittest.TestCase):
    """Crash capture and reporting."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_crash = devin_lite.CRASH_FILE
        self._orig_events = devin_lite.EVENTS_FILE
        self._orig_state = devin_lite.STATE_FILE
        devin_lite.CRASH_FILE = Path(self.tmpdir) / "runtime_crash.json"
        devin_lite.EVENTS_FILE = Path(self.tmpdir) / "events.json"
        devin_lite.STATE_FILE = Path(self.tmpdir) / "state.json"

    def tearDown(self):
        devin_lite.CRASH_FILE = self._orig_crash
        devin_lite.EVENTS_FILE = self._orig_events
        devin_lite.STATE_FILE = self._orig_state

    def test_capture_creates_file(self):
        try:
            raise ValueError("test error")
        except ValueError as e:
            devin_lite.capture_crash(e)

        self.assertTrue(devin_lite.CRASH_FILE.exists())
        crashes = json.loads(devin_lite.CRASH_FILE.read_text())
        self.assertEqual(len(crashes), 1)
        self.assertEqual(crashes[0]["error"], "test error")
        self.assertEqual(crashes[0]["error_type"], "ValueError")
        self.assertIn("Traceback", crashes[0]["stacktrace"])

    def test_capture_with_state(self):
        state = {
            "ts": "2026-03-29T23:00:00",
            "containers": [{"name": "sonarr", "up": False}],
            "internet": {"ok": True},
            "vpn": {"ok": True},
            "ollama_notebook": {"ok": True},
        }
        devin_lite.capture_crash(RuntimeError("timeout"), state)

        crashes = json.loads(devin_lite.CRASH_FILE.read_text())
        self.assertEqual(crashes[0]["system_state"]["containers_down"], ["sonarr"])

    def test_multiple_crashes_appended(self):
        for i in range(3):
            devin_lite.capture_crash(Exception(f"error {i}"))

        crashes = json.loads(devin_lite.CRASH_FILE.read_text())
        self.assertEqual(len(crashes), 3)

    def test_max_crashes_trimmed(self):
        for i in range(60):
            devin_lite.capture_crash(Exception(f"error {i}"))

        crashes = json.loads(devin_lite.CRASH_FILE.read_text())
        self.assertLessEqual(len(crashes), devin_lite.MAX_CRASHES)

    def test_get_recent_crashes(self):
        devin_lite.capture_crash(Exception("a"))
        devin_lite.capture_crash(Exception("b"))
        recent = devin_lite.get_recent_crashes(1)
        self.assertEqual(len(recent), 1)
        self.assertEqual(recent[0]["error"], "b")

    def test_crash_summary(self):
        devin_lite.capture_crash(ValueError("x"))
        devin_lite.capture_crash(TimeoutError("y"))
        devin_lite.capture_crash(ValueError("z"))

        summary = devin_lite.get_crash_summary()
        self.assertEqual(summary["total"], 3)
        self.assertEqual(summary["error_types"]["ValueError"], 2)
        self.assertEqual(summary["error_types"]["TimeoutError"], 1)

    def test_empty_crash_summary(self):
        summary = devin_lite.get_crash_summary()
        self.assertEqual(summary["total"], 0)


class TestGlobalTimeout(unittest.TestCase):
    """GLOBAL_TIMEOUT_SECONDS configuration."""

    def test_timeout_is_120(self):
        self.assertEqual(rt.GLOBAL_TIMEOUT_SECONDS, 120)

    def test_sigalrm_handler_registered(self):
        """On Linux, SIGALRM handler should be registered."""
        import signal
        if hasattr(signal, "SIGALRM"):
            handler = signal.getsignal(signal.SIGALRM)
            self.assertEqual(handler, rt._timeout_handler)

    def test_timeout_handler_raises(self):
        with self.assertRaises(TimeoutError):
            rt._timeout_handler(None, None)


class TestRotatingLog(unittest.TestCase):
    """RotatingFileHandler-based logging."""

    def test_runtime_log_does_not_crash(self):
        """_runtime_log should never raise, even with broken paths."""
        # Just verify it doesn't throw — the actual file is in the container
        rt._runtime_log("test stability message")
        # If we get here without exception, the test passes


if __name__ == "__main__":
    unittest.main(verbosity=2)
