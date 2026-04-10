#!/usr/bin/env python3
"""
Tests for tool_tuning/router.py — Tool router classification + dispatch.

Covers:
- normalize_route: cleaning model output
- validate_label: rejecting unknown labels
- route_intent: classification with cache, error handling, input validation
- dispatch / dispatch_json: end-to-end with fallback
- Intent cache: TTL, eviction, clear
- Error handling: never crashes, always returns valid result
- Fallback behavior: unknown/empty/error inputs return 'none'

Corre: python3 tests/test_tool_router.py
"""

import json
import sys
import time
import unittest
from unittest.mock import patch, MagicMock

sys.path.insert(0, "/home/tenazas")
sys.path.insert(0, "/home/tenazas/runtime/tool_tuning")

from runtime.tool_tuning import router


def _mock_ollama_response(label: str, status_code: int = 200):
    """Create a mock requests.Response for Ollama API."""
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = json.dumps({"response": label})
    mock_resp.json.return_value = {"response": label}
    return mock_resp


class TestNormalizeRoute(unittest.TestCase):
    """normalize_route: clean model output to a single label."""

    def test_simple_label(self):
        self.assertEqual(router.normalize_route("weather"), "weather")

    def test_uppercase(self):
        self.assertEqual(router.normalize_route("WEATHER"), "weather")

    def test_whitespace(self):
        self.assertEqual(router.normalize_route("  calendar  \n"), "calendar")

    def test_multiline_takes_first(self):
        self.assertEqual(router.normalize_route("weather\ncalendar"), "weather")

    def test_empty_string(self):
        self.assertEqual(router.normalize_route(""), "none")

    def test_none_input(self):
        self.assertEqual(router.normalize_route(None), "none")

    def test_only_whitespace(self):
        self.assertEqual(router.normalize_route("   \n\n  "), "none")

    def test_mixed_case(self):
        self.assertEqual(router.normalize_route("Network_Scan"), "network_scan")


class TestValidateLabel(unittest.TestCase):
    """validate_label: only allow known labels."""

    def test_valid_labels(self):
        for label in router.VALID_ROUTES:
            self.assertEqual(router.validate_label(label), label)

    def test_invalid_label_returns_none(self):
        self.assertEqual(router.validate_label("unknown_tool"), "none")

    def test_empty_string_invalid(self):
        self.assertEqual(router.validate_label(""), "none")

    def test_injection_attempt(self):
        self.assertEqual(router.validate_label("weather; rm -rf /"), "none")

    def test_close_misspelling(self):
        self.assertEqual(router.validate_label("wether"), "none")


class TestRouteResult(unittest.TestCase):
    """RouteResult dataclass behavior."""

    def test_to_dict(self):
        r = router.RouteResult(route="weather", raw_output="weather", success=True)
        d = r.to_dict()
        self.assertEqual(d["route"], "weather")
        self.assertTrue(d["success"])
        self.assertIn("cached", d)

    def test_default_cached_false(self):
        r = router.RouteResult(route="none", raw_output="", success=False)
        self.assertFalse(r.cached)

    def test_fallback_helper(self):
        r = router._make_fallback("some error", raw_output="bad")
        self.assertEqual(r.route, "none")
        self.assertFalse(r.success)
        self.assertEqual(r.error, "some error")
        self.assertEqual(r.raw_output, "bad")


class TestRouteIntent(unittest.TestCase):
    """route_intent: classification with error handling."""

    def setUp(self):
        router.clear_intent_cache()

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_valid_classification(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="weather", raw_output="weather", success=True,
        )
        result = router.route_intent("qué clima hace")
        self.assertEqual(result.route, "weather")
        self.assertTrue(result.success)

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_none_route_on_no_match(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="none", raw_output="none", success=True,
        )
        result = router.route_intent("hola cómo estás")
        self.assertEqual(result.route, "none")

    def test_empty_input_returns_fallback(self):
        result = router.route_intent("")
        self.assertEqual(result.route, "none")
        self.assertFalse(result.success)
        self.assertIn("Empty", result.error)

    def test_whitespace_only_returns_fallback(self):
        result = router.route_intent("   ")
        self.assertEqual(result.route, "none")
        self.assertFalse(result.success)

    def test_none_input_returns_fallback(self):
        result = router.route_intent(None)
        self.assertEqual(result.route, "none")
        self.assertFalse(result.success)

    def test_numeric_input_returns_fallback(self):
        result = router.route_intent(12345)
        self.assertEqual(result.route, "none")
        self.assertFalse(result.success)

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_exception_in_ollama_returns_fallback(self, mock_http):
        mock_http.side_effect = RuntimeError("unexpected crash")
        result = router.route_intent("test query")
        self.assertEqual(result.route, "none")
        self.assertFalse(result.success)
        self.assertIn("Unexpected", result.error)


class TestIntentCache(unittest.TestCase):
    """Intent cache: TTL, eviction, clear."""

    def setUp(self):
        router.clear_intent_cache()

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_cache_hit(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="weather", raw_output="weather", success=True,
        )
        # First call — cache miss
        r1 = router.route_intent("clima hoy")
        self.assertFalse(r1.cached)
        self.assertEqual(mock_http.call_count, 1)

        # Second call — cache hit
        r2 = router.route_intent("clima hoy")
        self.assertTrue(r2.cached)
        self.assertEqual(mock_http.call_count, 1)  # not called again
        self.assertEqual(r2.route, "weather")

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_cache_miss_different_query(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="weather", raw_output="weather", success=True,
        )
        router.route_intent("clima hoy")
        router.route_intent("agenda de mañana")
        self.assertEqual(mock_http.call_count, 2)

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_cache_does_not_store_failures(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="none", raw_output="", success=False, error="timeout",
        )
        router.route_intent("test")
        # Second call should hit Ollama again since failure wasn't cached
        router.route_intent("test")
        self.assertEqual(mock_http.call_count, 2)

    def test_clear_cache(self):
        router._intent_cache["test"] = (
            router.RouteResult(route="weather", raw_output="w", success=True),
            time.time(),
        )
        self.assertEqual(len(router._intent_cache), 1)
        router.clear_intent_cache()
        self.assertEqual(len(router._intent_cache), 0)

    def test_cache_ttl_expiry(self):
        """Expired entries are not returned."""
        expired_ts = time.time() - router._CACHE_TTL_S - 10
        router._intent_cache["model:test"] = (
            router.RouteResult(route="weather", raw_output="w", success=True),
            expired_ts,
        )
        result = router._cache_get("model:test")
        self.assertIsNone(result)


class TestRunOllamaRouterHttp(unittest.TestCase):
    """run_ollama_router_http: HTTP call + label validation."""

    @patch("runtime.tool_tuning.router.requests.post")
    def test_valid_response(self, mock_post):
        mock_post.return_value = _mock_ollama_response("weather")
        result = router.run_ollama_router_http("clima hoy")
        self.assertEqual(result.route, "weather")
        self.assertTrue(result.success)
        self.assertIsNone(result.error)

    @patch("runtime.tool_tuning.router.requests.post")
    def test_invalid_label_normalized_to_none(self, mock_post):
        mock_post.return_value = _mock_ollama_response("banana")
        result = router.run_ollama_router_http("test")
        self.assertEqual(result.route, "none")
        self.assertTrue(result.success)  # HTTP succeeded, label was just invalid

    @patch("runtime.tool_tuning.router.requests.post")
    def test_timeout(self, mock_post):
        import requests as req
        mock_post.side_effect = req.exceptions.Timeout("timed out")
        result = router.run_ollama_router_http("test")
        self.assertEqual(result.route, "none")
        self.assertFalse(result.success)
        self.assertIn("Timeout", result.error)

    @patch("runtime.tool_tuning.router.requests.post")
    def test_connection_error(self, mock_post):
        import requests as req
        mock_post.side_effect = req.exceptions.ConnectionError("refused")
        result = router.run_ollama_router_http("test")
        self.assertEqual(result.route, "none")
        self.assertFalse(result.success)
        self.assertIn("conectar", result.error)

    @patch("runtime.tool_tuning.router.requests.post")
    def test_http_500(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp
        result = router.run_ollama_router_http("test")
        self.assertEqual(result.route, "none")
        self.assertFalse(result.success)
        self.assertIn("500", result.error)

    @patch("runtime.tool_tuning.router.requests.post")
    def test_non_json_response(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html>not json</html>"
        mock_resp.json.side_effect = ValueError("not json")
        mock_post.return_value = mock_resp
        result = router.run_ollama_router_http("test")
        self.assertEqual(result.route, "none")
        self.assertFalse(result.success)
        self.assertIn("no-JSON", result.error)

    @patch("runtime.tool_tuning.router.requests.post")
    def test_all_valid_labels_accepted(self, mock_post):
        for label in router.VALID_ROUTES:
            mock_post.return_value = _mock_ollama_response(label)
            result = router.run_ollama_router_http(f"test {label}")
            self.assertEqual(result.route, label, f"Label {label} should be accepted")


class TestDispatch(unittest.TestCase):
    """dispatch: route + execute handler, never crash."""

    def setUp(self):
        router.clear_intent_cache()

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_weather_dispatch(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="weather", raw_output="weather", success=True,
        )
        result = router.dispatch("clima hoy")
        self.assertIn("[weather]", result)

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_none_dispatch(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="none", raw_output="none", success=True,
        )
        result = router.dispatch("hola")
        self.assertIn("[none]", result)

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_failed_route_returns_none_handler(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="none", raw_output="", success=False, error="offline",
        )
        result = router.dispatch("test")
        self.assertIn("[none]", result)

    def test_empty_input_dispatch(self):
        result = router.dispatch("")
        self.assertIn("[none]", result)

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_handler_crash_returns_none(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="weather", raw_output="weather", success=True,
        )
        # Temporarily break the weather handler
        original = router.HANDLERS["weather"]
        router.HANDLERS["weather"] = lambda q: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            result = router.dispatch("clima")
            self.assertIn("[none]", result)
        finally:
            router.HANDLERS["weather"] = original


class TestDispatchJson(unittest.TestCase):
    """dispatch_json: full telemetry dict, never crash."""

    def setUp(self):
        router.clear_intent_cache()

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_success_keys(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="infra", raw_output="infra", success=True,
        )
        result = router.dispatch_json("docker status")
        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "infra")
        self.assertIn("classify_ms", result)
        self.assertIn("cached", result)
        self.assertIsNone(result["error"])

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_failure_returns_dict(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="none", raw_output="", success=False, error="timeout",
        )
        result = router.dispatch_json("test")
        self.assertFalse(result["ok"])
        self.assertEqual(result["route"], "none")
        self.assertIn("error", result)

    def test_empty_input_returns_dict(self):
        result = router.dispatch_json("")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["route"], "none")
        self.assertFalse(result["ok"])

    @patch("runtime.tool_tuning.router.route_intent")
    def test_exception_returns_safe_dict(self, mock_intent):
        mock_intent.side_effect = RuntimeError("total crash")
        result = router.dispatch_json("test")
        self.assertIsInstance(result, dict)
        self.assertFalse(result["ok"])
        self.assertEqual(result["route"], "none")


class TestValidRoutes(unittest.TestCase):
    """VALID_ROUTES integrity checks."""

    def test_none_is_valid(self):
        self.assertIn("none", router.VALID_ROUTES)

    def test_all_handlers_have_routes(self):
        for route in router.HANDLERS:
            self.assertIn(route, router.VALID_ROUTES)

    def test_all_routes_have_handlers(self):
        for route in router.VALID_ROUTES:
            self.assertIn(route, router.HANDLERS)

    def test_expected_routes_present(self):
        expected = {"weather", "calendar", "lights", "network_scan", "infra", "tts", "none"}
        self.assertTrue(expected.issubset(router.VALID_ROUTES))


class TestOllamaHealthcheck(unittest.TestCase):
    """ollama_healthcheck: reachability probe."""

    @patch("runtime.tool_tuning.router.requests.get")
    def test_healthy(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200)
        self.assertTrue(router.ollama_healthcheck())

    @patch("runtime.tool_tuning.router.requests.get")
    def test_unhealthy_status(self, mock_get):
        mock_get.return_value = MagicMock(status_code=500)
        self.assertFalse(router.ollama_healthcheck())

    @patch("runtime.tool_tuning.router.requests.get")
    def test_unreachable(self, mock_get):
        import requests as req
        mock_get.side_effect = req.exceptions.ConnectionError("refused")
        self.assertFalse(router.ollama_healthcheck())


if __name__ == "__main__":
    unittest.main()
