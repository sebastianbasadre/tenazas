#!/usr/bin/env python3
"""
Tests for self_debugger.py — Runtime error analysis.

Covers:
- _load_log_lines (JSON, non-JSON, missing file, max lines)
- _is_error_entry (ok=False, error field, keyword matching)
- _classify_error (timeout, connection, permission, not_found, parse, module, general)
- _extract_error_message (error, stderr, detail, raw, action fields)
- diagnose() (empty log, healthy log, errors, root cause, suggestion, summary)
- scan_all_logs() (no dir, empty, mixed health, overall health levels)

Corre: python3 tests/test_self_debugger.py
"""

import sys
import json
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, "/home/tenazas")
from runtime import self_debugger as sd


class _TempLogsMixin:
    def setUp(self):
        self.tmpdir = Path(tempfile.mkdtemp())
        self._orig_logs = sd.LOGS_DIR
        self._orig_telem = sd.TELEMETRY_LOG
        sd.LOGS_DIR = self.tmpdir / "logs"
        sd.LOGS_DIR.mkdir()
        sd.TELEMETRY_LOG = sd.LOGS_DIR / "self_debug.log"

    def tearDown(self):
        sd.LOGS_DIR = self._orig_logs
        sd.TELEMETRY_LOG = self._orig_telem

    def _write_log(self, name, entries):
        path = sd.LOGS_DIR / name
        with open(path, "w") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        return path


class TestLoadLogLines(_TempLogsMixin, unittest.TestCase):

    def test_load_json_lines(self):
        path = self._write_log("test.log", [
            {"action": "a"}, {"action": "b"},
        ])
        lines = sd._load_log_lines(path)
        self.assertEqual(len(lines), 2)

    def test_load_non_json_lines(self):
        path = sd.LOGS_DIR / "test.log"
        path.write_text("not json\nalso not json\n")
        lines = sd._load_log_lines(path)
        self.assertEqual(len(lines), 2)
        self.assertIn("raw", lines[0])

    def test_missing_file(self):
        lines = sd._load_log_lines(sd.LOGS_DIR / "nope.log")
        self.assertEqual(lines, [])

    def test_max_lines_respected(self):
        path = self._write_log("big.log", [{"i": i} for i in range(50)])
        lines = sd._load_log_lines(path, max_lines=10)
        self.assertEqual(len(lines), 10)
        self.assertEqual(lines[-1]["i"], 49)  # keeps most recent


class TestIsErrorEntry(_TempLogsMixin, unittest.TestCase):

    def test_ok_false(self):
        self.assertTrue(sd._is_error_entry({"ok": False}))

    def test_ok_true(self):
        self.assertFalse(sd._is_error_entry({"ok": True, "action": "ps"}))

    def test_error_field(self):
        self.assertTrue(sd._is_error_entry({"error": "something broke"}))

    def test_keyword_timeout(self):
        self.assertTrue(sd._is_error_entry({"detail": "connection timeout"}))

    def test_keyword_exception(self):
        self.assertTrue(sd._is_error_entry({"raw": "Traceback (most recent call)"}))

    def test_clean_entry(self):
        self.assertFalse(sd._is_error_entry({"action": "docker.ps", "ok": True}))


class TestClassifyError(_TempLogsMixin, unittest.TestCase):

    def test_timeout(self):
        self.assertEqual(sd._classify_error({"error": "request timeout"}), "timeout")

    def test_connection(self):
        self.assertEqual(sd._classify_error({"error": "connection refused"}), "connection_error")

    def test_permission(self):
        self.assertEqual(sd._classify_error({"error": "permission denied"}), "permission_error")

    def test_not_found(self):
        self.assertEqual(sd._classify_error({"error": "FileNotFoundError"}), "not_found")

    def test_parse(self):
        self.assertEqual(sd._classify_error({"error": "JSON decode error"}), "parse_error")

    def test_module(self):
        self.assertEqual(sd._classify_error({"error": "No module named foo"}), "module_error")

    def test_general(self):
        self.assertEqual(sd._classify_error({"error": "something weird"}), "general_error")


class TestExtractErrorMessage(_TempLogsMixin, unittest.TestCase):

    def test_error_field(self):
        msg = sd._extract_error_message({"error": "boom"})
        self.assertEqual(msg, "boom")

    def test_stderr_field(self):
        msg = sd._extract_error_message({"stderr": "stderr output"})
        self.assertEqual(msg, "stderr output")

    def test_detail_field(self):
        msg = sd._extract_error_message({"detail": "details here"})
        self.assertEqual(msg, "details here")

    def test_raw_field(self):
        msg = sd._extract_error_message({"raw": "raw log line"})
        self.assertEqual(msg, "raw log line")

    def test_action_target(self):
        msg = sd._extract_error_message({"action": "restart", "target": "sonarr"})
        self.assertIn("restart", msg)
        self.assertIn("sonarr", msg)

    def test_empty_entry(self):
        msg = sd._extract_error_message({})
        self.assertEqual(msg, "unknown error")

    def test_truncation(self):
        msg = sd._extract_error_message({"error": "x" * 500})
        self.assertLessEqual(len(msg), 200)


class TestDiagnose(_TempLogsMixin, unittest.TestCase):

    def test_empty_log(self):
        path = self._write_log("empty.log", [])
        result = sd.diagnose(path)
        self.assertEqual(result["errors"], [])
        self.assertIn("No log data", result["root_cause"])

    def test_missing_log(self):
        result = sd.diagnose(sd.LOGS_DIR / "nope.log")
        self.assertEqual(result["errors"], [])

    def test_healthy_log(self):
        path = self._write_log("healthy.log", [
            {"action": "docker.ps", "ok": True},
            {"action": "check_vpn", "ok": True},
        ])
        result = sd.diagnose(path)
        self.assertEqual(result["errors"], [])
        self.assertIn("No errors", result["root_cause"])

    def test_log_with_errors(self):
        path = self._write_log("errors.log", [
            {"action": "restart", "ok": False, "error": "timeout"},
            {"action": "restart", "ok": False, "error": "timeout again"},
            {"action": "check", "ok": True},
        ])
        result = sd.diagnose(path)
        self.assertEqual(len(result["errors"]), 2)
        self.assertIn("timeout", result["root_cause"].lower())

    def test_root_cause_most_common(self):
        path = self._write_log("mixed.log", [
            {"error": "timeout"}, {"error": "timeout"}, {"error": "timeout"},
            {"error": "permission denied"},
        ])
        result = sd.diagnose(path)
        self.assertIn("timeout", result["root_cause"].lower())

    def test_suggestion_present(self):
        path = self._write_log("conn.log", [
            {"error": "connection refused"},
        ])
        result = sd.diagnose(path)
        self.assertIn("suggestion", result)
        self.assertTrue(len(result["suggestion"]) > 0)

    def test_summary_stats(self):
        path = self._write_log("stats.log", [
            {"ok": True}, {"ok": True}, {"error": "fail"},
        ])
        result = sd.diagnose(path)
        self.assertEqual(result["summary"]["total_lines"], 3)
        self.assertEqual(result["summary"]["total_errors"], 1)

    def test_error_rate(self):
        path = self._write_log("rate.log", [
            {"error": "a"}, {"error": "b"}, {"ok": True}, {"ok": True},
        ])
        result = sd.diagnose(path)
        self.assertAlmostEqual(result["summary"]["error_rate"], 0.5)

    def test_max_errors_capped(self):
        path = self._write_log("many.log", [
            {"error": f"error-{i}"} for i in range(50)
        ])
        result = sd.diagnose(path)
        self.assertLessEqual(len(result["errors"]), sd.MAX_ERRORS_PER_REPORT)

    def test_string_path_accepted(self):
        path = self._write_log("str.log", [{"ok": True}])
        result = sd.diagnose(str(path))
        self.assertIn("No errors", result["root_cause"])


class TestScanAllLogs(_TempLogsMixin, unittest.TestCase):

    def test_no_logs_dir(self):
        import shutil
        shutil.rmtree(sd.LOGS_DIR)
        result = sd.scan_all_logs()
        self.assertEqual(result["scanned"], 0)
        self.assertEqual(result["overall_health"], "healthy")

    def test_empty_logs_dir(self):
        result = sd.scan_all_logs()
        self.assertEqual(result["scanned"], 0)

    def test_all_healthy(self):
        self._write_log("a.log", [{"ok": True}])
        self._write_log("b.log", [{"ok": True}])
        result = sd.scan_all_logs()
        self.assertEqual(result["scanned"], 2)
        self.assertEqual(result["files_with_errors"], 0)
        self.assertEqual(result["overall_health"], "healthy")

    def test_degraded(self):
        self._write_log("a.log", [{"ok": True}])
        self._write_log("b.log", [{"ok": True}])
        self._write_log("c.log", [{"error": "fail"}])
        result = sd.scan_all_logs()
        self.assertEqual(result["overall_health"], "degraded")

    def test_unhealthy(self):
        self._write_log("a.log", [{"error": "fail"}])
        self._write_log("b.log", [{"error": "fail"}])
        result = sd.scan_all_logs()
        self.assertEqual(result["overall_health"], "unhealthy")

    def test_skips_self_debug_log(self):
        self._write_log("self_debug.log", [{"error": "internal"}])
        self._write_log("other.log", [{"ok": True}])
        result = sd.scan_all_logs()
        self.assertNotIn("self_debug.log", result["reports"])

    def test_reports_per_file(self):
        self._write_log("runtime.log", [{"error": "timeout"}])
        self._write_log("safety.log", [{"ok": True}])
        result = sd.scan_all_logs()
        self.assertIn("runtime.log", result["reports"])
        self.assertIn("safety.log", result["reports"])


if __name__ == "__main__":
    unittest.main()
