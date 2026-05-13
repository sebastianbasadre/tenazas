#!/usr/bin/env python3
"""
Tests for health_sweep.py — 25 tests with mocked probes.

All network calls, subprocess, and file I/O are mocked.
Full sweep runs < 100ms.
"""

import json
import os
import sqlite3
import sys
import tempfile
import time
import unittest
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock

# Ensure runtime is importable
_runtime_paths = [
    str(Path(__file__).parent.parent / "workspace" / "runtime"),  # host
    "/home/tenazas/runtime",  # container
]
for _p in _runtime_paths:
    if _p not in sys.path:
        sys.path.insert(0, _p)
import health_sweep as hs


class MockResponse:
    """Mock urllib response."""
    def __init__(self, body: str = "{}", status: int = 200):
        self.status = status
        self._body = body.encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        pass


def _mock_urlopen_factory(responses: dict | None = None):
    """Create mock urlopen that returns different responses by URL pattern."""
    defaults = {
        "heartbeat": MockResponse('{"nanosecond heartbeat": 1}'),
        "healthy": MockResponse("OK"),
        "ready": MockResponse("OK"),
        "health": MockResponse('{"status": "ok", "database": "ok"}'),
        "ip-api": MockResponse('{"countryCode": "ES", "query": "82.140.1.1"}'),
        "tags": MockResponse('{"models": [{"name": "qwen2.5:7b"}, {"name": "qwen3:8b"}, {"name": "nomic-embed-text:latest"}]}'),
        "ps": MockResponse('{"models": [{"name": "qwen2.5:7b", "size": 5000000000}]}'),
        "ping": MockResponse("{}"),
        "ticker": MockResponse('{"lastPrice": "84500.12", "priceChangePercent": "1.5"}'),
        "invertir": MockResponse("", status=401),
        "generativelanguage": MockResponse('{"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}'),
        "getMe": MockResponse('{"ok": true, "result": {"username": "TenazasBot"}}'),
        "open-meteo": MockResponse('{"current": {"temperature_2m": 18.5}}'),
        "metrics": MockResponse("runtime_runs_total 42\nollama_gpu_up 1\n"),
        "collections/tenazas-knowledge/query": MockResponse('{"ids": [["1"]], "documents": [["test"]]}'),
        "collections/tenazas-knowledge/count": MockResponse("251"),
        "collections/tenazas-knowledge": MockResponse('{"name": "tenazas-knowledge"}'),
        "proxy/1/api/v1/query": MockResponse(json.dumps({
            "data": {"result": [{"value": [time.time(), "1"]}]}})),
        "sendMessage": MockResponse('{"ok": true}'),
    }
    if responses:
        defaults.update(responses)

    def mock_urlopen(req, timeout=None, context=None):
        url = req if isinstance(req, str) else req.full_url
        for pattern, resp in defaults.items():
            if pattern in url:
                return resp
        return MockResponse()

    return mock_urlopen


class TestProbeHelpers(unittest.TestCase):
    """Test helper functions."""

    def test_ok_result(self):
        r = hs._ok("test", 42.3, {"key": "val"})
        self.assertEqual(r["probe"], "test")
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["latency_ms"], 42.3)
        self.assertEqual(r["detail"]["key"], "val")

    def test_warn_result(self):
        r = hs._warn("test", "something wrong", 10)
        self.assertEqual(r["status"], "warn")
        self.assertEqual(r["detail"]["message"], "something wrong")

    def test_fail_result(self):
        r = hs._fail("test", "broken")
        self.assertEqual(r["status"], "fail")
        self.assertEqual(r["detail"]["message"], "broken")

    def test_sanitize_detail_strips_secrets(self):
        d = {"bot_token": "123456:ABC", "api_key": "sk-test123", "normal": "ok"}
        sanitized = hs._sanitize_detail(d)
        self.assertNotIn("123456", json.dumps(sanitized))
        self.assertNotIn("sk-test", json.dumps(sanitized))
        self.assertEqual(sanitized["normal"], "ok")


class TestNASProbes(unittest.TestCase):
    """Test NAS section probes."""

    @patch("subprocess.run")
    def test_docker_socket_ok(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0,
            stdout="openclaw\ngluetun\nsonarr\n")
        r = hs._probe_docker_socket()
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["detail"]["containers_up"], 3)

    @patch("subprocess.run")
    def test_docker_socket_fail(self, mock_run):
        mock_run.return_value = MagicMock(returncode=1, stdout="", stderr="error")
        r = hs._probe_docker_socket()
        self.assertEqual(r["status"], "fail")

    @patch("subprocess.run")
    def test_containers_missing(self, mock_run):
        mock_run.return_value = MagicMock(returncode=0,
            stdout="openclaw\ngluetun\n")
        r = hs._probe_containers()
        self.assertEqual(r["status"], "warn")
        self.assertIn("missing", r["detail"])

    @patch("health_sweep.urlopen")
    def test_vpn_ok_spain(self, mock_url):
        mock_url.return_value = MockResponse('{"countryCode": "ES", "query": "82.140.1.1"}')
        # Need to mock the opener too
        with patch("urllib.request.build_opener") as mock_opener:
            mock_opener.return_value.open.return_value = MockResponse(
                '{"countryCode": "ES", "query": "82.140.1.1"}')
            r = hs._probe_vpn()
            self.assertEqual(r["status"], "ok")

    @patch("health_sweep.urlopen")
    def test_vpn_leak_argentina(self, mock_url):
        with patch("urllib.request.build_opener") as mock_opener:
            mock_opener.return_value.open.return_value = MockResponse(
                '{"countryCode": "AR", "query": "206.223.1.1"}')
            r = hs._probe_vpn()
            self.assertEqual(r["status"], "fail")
            self.assertIn("LEAK", r["detail"]["message"])


class TestHomelabProbes(unittest.TestCase):
    """Test HomeLab section probes."""

    @patch("health_sweep._http_get")
    def test_ollama_gpu_ok(self, mock_get):
        models = [{"name": "qwen2.5:7b"}, {"name": "qwen3:8b"},
                  {"name": "nomic-embed-text:latest"}]
        mock_get.return_value = (200, json.dumps({"models": models}), 45.0)
        r = hs._probe_ollama_gpu()
        self.assertEqual(r["status"], "ok")

    @patch("health_sweep._http_get")
    def test_ollama_gpu_missing_models(self, mock_get):
        models = [{"name": "qwen2.5:7b"}]
        mock_get.return_value = (200, json.dumps({"models": models}), 45.0)
        r = hs._probe_ollama_gpu()
        self.assertEqual(r["status"], "warn")
        self.assertIn("missing", r["detail"]["message"])

    @patch("health_sweep._http_get")
    def test_ollama_gpu_unreachable(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        r = hs._probe_ollama_gpu()
        self.assertEqual(r["status"], "fail")

    @patch("health_sweep._http_get")
    def test_gpu_exporter_not_running(self, mock_get):
        mock_get.side_effect = Exception("Connection refused")
        r = hs._probe_gpu_exporter()
        self.assertEqual(r["status"], "ok")
        self.assertIn("suggestion", r["detail"])


class TestAPIProbes(unittest.TestCase):
    """Test external API probes."""

    @patch("health_sweep.urlopen")
    def test_binance_ping_ok(self, mock_url):
        with patch("urllib.request.build_opener") as mock_opener:
            mock_opener.return_value.open.return_value = MockResponse("{}")
            r = hs._probe_binance_ping()
            self.assertEqual(r["status"], "ok")

    @patch("health_sweep._http_get")
    def test_openmeteo_ok(self, mock_get):
        mock_get.return_value = (200, '{"current": {"temperature_2m": 18.5}}', 120.0)
        r = hs._probe_openmeteo()
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["detail"]["temp_c"], 18.5)

    @patch("health_sweep._http_get")
    def test_iol_reachable_401(self, mock_get):
        mock_get.return_value = (401, "", 80.0)
        r = hs._probe_iol()
        self.assertEqual(r["status"], "ok")  # 401 = auth needed but reachable

    @patch("health_sweep._http_get")
    def test_iol_server_error(self, mock_get):
        mock_get.return_value = (500, "", 80.0)
        r = hs._probe_iol()
        self.assertEqual(r["status"], "fail")

    def test_gemini_disabled_by_default(self):
        r = hs._probe_gemini()
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["detail"]["optional"])
        self.assertEqual(r["detail"]["status"], "disabled")


class TestInternalProbes(unittest.TestCase):
    """Test internal service probes."""

    def test_freeze_detector_no_file(self):
        with patch.object(Path, "exists", return_value=False):
            r = hs._probe_freeze_detector()
            self.assertEqual(r["status"], "ok")

    @patch("health_sweep._http_get")
    def test_metrics_exporter_ok(self, mock_get):
        mock_get.return_value = (200, "runtime_runs_total 42\nollama_gpu_up 1\n", 5.0)
        r = hs._probe_metrics_exporter()
        self.assertEqual(r["status"], "ok")
        self.assertEqual(r["detail"]["metric_count"], 2)

    @patch("health_sweep._http_get")
    def test_metrics_exporter_empty(self, mock_get):
        mock_get.return_value = (200, "# HELP\n# TYPE\n", 5.0)
        r = hs._probe_metrics_exporter()
        self.assertEqual(r["status"], "warn")


class TestDashboardProbes(unittest.TestCase):
    """Test dashboard cache detection."""

    @patch("health_sweep._http_get")
    def test_grafana_cache_fresh(self, mock_get):
        now = time.time()
        resp = json.dumps({"data": {"result": [{"value": [now, "1"]}]}})
        mock_get.return_value = (200, resp, 10.0)
        r = hs._probe_grafana_cache()
        self.assertEqual(r["status"], "ok")

    @patch("health_sweep._http_get")
    def test_grafana_cache_stale(self, mock_get):
        now = time.time()
        fresh = json.dumps({"data": {"result": [{"value": [now, "1"]}]}})
        stale = json.dumps({"data": {"result": [{"value": [now - 300, "1"]}]}})
        mock_get.side_effect = [
            (200, fresh, 10.0),  # fresh request
            (200, stale, 10.0),  # cached request
        ]
        r = hs._probe_grafana_cache()
        self.assertEqual(r["status"], "fail")
        self.assertIn("CACHE STALE", r["detail"]["message"])

    @patch("health_sweep._http_get")
    def test_grafana_cache_401_is_auth_not_failure(self, mock_get):
        mock_get.return_value = (401, "", 10.0)
        r = hs._probe_grafana_cache()
        self.assertEqual(r["status"], "ok")
        self.assertTrue(r["detail"]["auth_required"])


class TestScoring(unittest.TestCase):
    """Test scoring logic."""

    def test_all_ok(self):
        results = [hs._ok("a"), hs._ok("b"), hs._ok("c")]
        self.assertEqual(hs._score_section(results), 100)

    def test_all_fail(self):
        results = [hs._fail("a", "x"), hs._fail("b", "x")]
        self.assertEqual(hs._score_section(results), 0)

    def test_mixed(self):
        results = [hs._ok("a"), hs._warn("b", "x"), hs._fail("c", "x")]
        # (100 + 50 + 0) / 3 = 50
        self.assertEqual(hs._score_section(results), 50)

    def test_overall_weighted(self):
        sections = [
            {"section": "nas", "results": [hs._ok("a")]},          # 100 * 30
            {"section": "homelab", "results": [hs._fail("b", "x")]}, # 0 * 25
            {"section": "apis", "results": [hs._ok("c")]},         # 100 * 20
            {"section": "internal", "results": [hs._ok("d")]},     # 100 * 15
            {"section": "dashboards", "results": [hs._ok("e")]},   # 100 * 10
        ]
        score = hs._score_overall(sections)
        # (30*100 + 25*0 + 20*100 + 15*100 + 10*100) / 100 = 75
        self.assertEqual(score, 75)


class TestSQLitePersistence(unittest.TestCase):
    """Test SQLite persistence."""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self._orig_db = hs.DB_PATH
        self._orig_state = hs.STATE_DIR
        hs.DB_PATH = Path(self.tmpdir) / "test_sweeps.sqlite"
        hs.STATE_DIR = Path(self.tmpdir)

    def tearDown(self):
        hs.DB_PATH = self._orig_db
        hs.STATE_DIR = self._orig_state

    def test_init_creates_tables(self):
        hs._init_db()
        conn = sqlite3.connect(str(hs.DB_PATH))
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
        conn.close()
        self.assertIn("sweeps", tables)
        self.assertIn("scores", tables)
        self.assertIn("chroma_counts", tables)

    def test_persist_and_read_score(self):
        sections = [
            {"section": "nas", "results": [hs._ok("docker")]},
        ]
        hs._persist(sections, 95)
        prev = hs._get_previous_score()
        self.assertEqual(prev, 95)

    def test_score_delta(self):
        sections = [{"section": "nas", "results": [hs._ok("a")]}]
        hs._persist(sections, 90)
        hs._persist(sections, 85)
        prev = hs._get_previous_score()
        self.assertEqual(prev, 85)


class TestFormatTelegram(unittest.TestCase):
    """Test Telegram message formatting."""

    def test_format_healthy(self):
        sections = [
            {"section": "nas", "results": [hs._ok("docker"), hs._ok("vpn")]},
            {"section": "homelab", "results": [hs._ok("ollama")]},
        ]
        msg = hs.format_telegram(sections, 100)
        self.assertIn("Health Sweep", msg)
        self.assertIn("100", msg)
        self.assertIn("NAS", msg)

    def test_format_with_failures(self):
        sections = [
            {"section": "homelab", "results": [
                hs._fail("ollama_gpu", "unreachable",
                         detail={"suggestion": "Check HomeLab power"}),
            ]},
        ]
        msg = hs.format_telegram(sections, 50)
        self.assertIn("🔴", msg)
        self.assertIn("Check HomeLab power", msg)


if __name__ == "__main__":
    unittest.main(verbosity=2)
