#!/usr/bin/env python3
"""
test_tool_router.py — Consolidated tests for tool_tuning/router.py.

Covers:
  - Regex fast-path: network_scan, infra, arr_monitor, weather, calendar,
    lights, tts, weather_calendar combo, semantic separation
  - normalize_route: cleaning model output
  - validate_label: rejecting unknown labels
  - route_intent: regex fast-path + Ollama fallback + cache + error handling
  - dispatch / dispatch_json: end-to-end with fallback
  - Intent cache: TTL, eviction, clear
  - Error handling: never crashes, always returns valid result
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
    mock_resp = MagicMock()
    mock_resp.status_code = status_code
    mock_resp.text = json.dumps({"response": label})
    mock_resp.json.return_value = {"response": label}
    return mock_resp


# ═══════════════════════════════════════════════════════════════════
# Regex fast-path tests (Agent 2)
# ═══════════════════════════════════════════════════════════════════

class TestRegexNetworkScan(unittest.TestCase):
    """network_scan regex patterns — expanded coverage."""

    def _c(self, text):
        return router._classify_regex(text)

    def test_escanea_la_red(self):
        self.assertEqual(self._c("escanea la red"), "network_scan")

    def test_escanear_red(self):
        self.assertEqual(self._c("escanear red"), "network_scan")

    def test_scan_de_red(self):
        self.assertEqual(self._c("scan de red"), "network_scan")

    def test_escanear_lan(self):
        self.assertEqual(self._c("escanear lan"), "network_scan")

    def test_scan_lan(self):
        self.assertEqual(self._c("scan lan"), "network_scan")

    def test_descubrir_dispositivos(self):
        self.assertEqual(self._c("descubrir dispositivos"), "network_scan")

    def test_dispositivos_activos(self):
        self.assertEqual(self._c("dispositivos activos"), "network_scan")

    def test_hosts_activos(self):
        self.assertEqual(self._c("hosts activos"), "network_scan")

    def test_quien_esta_conectado(self):
        self.assertEqual(self._c("quien está conectado"), "network_scan")

    def test_que_hay_en_la_red(self):
        self.assertEqual(self._c("qué hay en la red"), "network_scan")

    def test_nmap(self):
        self.assertEqual(self._c("nmap"), "network_scan")

    def test_arp_scan(self):
        self.assertEqual(self._c("arp scan"), "network_scan")

    def test_ping_sweep(self):
        self.assertEqual(self._c("ping sweep"), "network_scan")

    def test_host_discovery(self):
        self.assertEqual(self._c("host discovery"), "network_scan")

    def test_network_discovery(self):
        self.assertEqual(self._c("network discovery"), "network_scan")

    def test_scan_ip(self):
        self.assertEqual(self._c("scan ip"), "network_scan")

    def test_mapa_de_red(self):
        self.assertEqual(self._c("mapa de red"), "network_scan")

    def test_dispositivos_en_la_red(self):
        self.assertEqual(self._c("dispositivos en la red"), "network_scan")

    def test_cuantos_dispositivos(self):
        self.assertEqual(self._c("cuántos dispositivos hay"), "network_scan")

    def test_subnet(self):
        self.assertEqual(self._c("escanea 192.168.2.0/24"), "network_scan")

    def test_dispositivos_desconocidos(self):
        self.assertEqual(self._c("hay dispositivos desconocidos en la red?"), "network_scan")

    def test_detectar_dispositivos(self):
        self.assertEqual(self._c("detectá dispositivos nuevos en la red"), "network_scan")

    def test_equipos_en_la_red(self):
        self.assertEqual(self._c("listá los equipos de la red"), "network_scan")

    def test_topologia(self):
        self.assertEqual(self._c("topología de red"), "network_scan")

    def test_ips_activas(self):
        self.assertEqual(self._c("IPs activas"), "network_scan")


class TestRegexInfra(unittest.TestCase):
    """infra patterns — must NOT match network_scan queries."""

    def _c(self, text):
        return router._classify_regex(text)

    def test_containers(self):
        self.assertEqual(self._c("estado de los containers"), "infra")

    def test_docker_status(self):
        self.assertEqual(self._c("docker status"), "infra")

    def test_como_estan_containers(self):
        self.assertEqual(self._c("cómo están los containers"), "infra")

    def test_homelab_status(self):
        self.assertEqual(self._c("cómo está el homelab"), "infra")

    def test_cpu_nas(self):
        self.assertEqual(self._c("uso de cpu del nas"), "infra")

    def test_temperatura_nas(self):
        self.assertEqual(self._c("temperatura del nas"), "infra")

    def test_espacio_disponible(self):
        self.assertEqual(self._c("espacio disponible"), "infra")

    def test_estado_general(self):
        self.assertEqual(self._c("estado general del sistema"), "infra")


class TestRegexSemanticSeparation(unittest.TestCase):
    """Ensure infra and network_scan don't cross-contaminate."""

    def _c(self, text):
        return router._classify_regex(text)

    def test_containers_not_network(self):
        self.assertNotEqual(self._c("cómo andan los containers"), "network_scan")

    def test_docker_not_network(self):
        self.assertNotEqual(self._c("docker ps"), "network_scan")

    def test_homelab_not_network(self):
        self.assertNotEqual(self._c("estado del homelab"), "network_scan")

    def test_nmap_not_infra(self):
        self.assertEqual(self._c("hacé un nmap"), "network_scan")

    def test_scan_red_not_infra(self):
        self.assertEqual(self._c("escaneá la red"), "network_scan")

    def test_dispositivos_not_infra(self):
        self.assertEqual(self._c("dispositivos conectados"), "network_scan")


class TestRegexOtherTools(unittest.TestCase):
    """Weather, calendar, combo, lights, tts, arr_monitor patterns."""

    def _c(self, text):
        return router._classify_regex(text)

    def test_weather_clima(self):
        self.assertEqual(self._c("cómo está el clima"), "weather")

    def test_weather_llover(self):
        self.assertEqual(self._c("va a llover hoy"), "weather")

    def test_weather_calor(self):
        self.assertEqual(self._c("hace calor"), "weather")

    def test_calendar_agenda(self):
        self.assertEqual(self._c("qué tengo hoy"), "calendar")

    def test_calendar_reuniones(self):
        self.assertEqual(self._c("reuniones de hoy"), "calendar")

    def test_combo_clima_agenda(self):
        self.assertEqual(self._c("clima y agenda de hoy"), "weather_calendar")

    def test_lights_prende(self):
        self.assertEqual(self._c("prendé las luces"), "lights")

    def test_lights_modo_cine(self):
        self.assertEqual(self._c("modo cine"), "lights")

    def test_tts_audio(self):
        self.assertEqual(self._c("mandame un audio"), "tts")

    def test_tts_keyword(self):
        self.assertEqual(self._c("tts hola mundo"), "tts")

    def test_arr_sonarr(self):
        self.assertEqual(self._c("estado de sonarr"), "arr_monitor")

    def test_arr_descargas(self):
        self.assertEqual(self._c("cómo están las descargas"), "arr_monitor")

    def test_none_greeting(self):
        self.assertIsNone(self._c("hola"))

    def test_none_chat(self):
        self.assertIsNone(self._c("contame un chiste"))


# ═══════════════════════════════════════════════════════════════════
# Core function tests (Agent 4)
# ═══════════════════════════════════════════════════════════════════

class TestNormalizeRoute(unittest.TestCase):

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


class TestValidateLabel(unittest.TestCase):

    def test_valid_labels(self):
        for label in router.VALID_ROUTES:
            self.assertEqual(router.validate_label(label), label)

    def test_invalid_label_returns_none(self):
        self.assertEqual(router.validate_label("unknown_tool"), "none")

    def test_empty_string_invalid(self):
        self.assertEqual(router.validate_label(""), "none")

    def test_injection_attempt(self):
        self.assertEqual(router.validate_label("weather; rm -rf /"), "none")

    def test_no_network_hyphen(self):
        self.assertNotIn("network-scanner", router.VALID_ROUTES)
        self.assertNotIn("network-scan", router.VALID_ROUTES)

    def test_all_expected_labels(self):
        expected = {
            "weather", "calendar", "weather_calendar", "lights",
            "network_scan", "infra", "tts", "arr_monitor", "none",
        }
        self.assertEqual(router.VALID_ROUTES, expected)


class TestRouteResult(unittest.TestCase):

    def test_to_dict(self):
        r = router.RouteResult(route="weather", raw_output="weather", success=True, method="regex")
        d = r.to_dict()
        self.assertEqual(d["route"], "weather")
        self.assertTrue(d["success"])
        self.assertIn("cached", d)
        self.assertIn("method", d)

    def test_default_cached_false(self):
        r = router.RouteResult(route="none", raw_output="", success=False)
        self.assertFalse(r.cached)

    def test_fallback_helper(self):
        r = router._make_fallback("some error", raw_output="bad")
        self.assertEqual(r.route, "none")
        self.assertFalse(r.success)
        self.assertEqual(r.method, "fallback")


class TestRouteIntent(unittest.TestCase):

    def setUp(self):
        router.clear_intent_cache()

    def test_regex_match_returns_success(self):
        result = router.route_intent("nmap")
        self.assertTrue(result.success)
        self.assertEqual(result.route, "network_scan")
        self.assertEqual(result.method, "regex")

    def test_empty_input(self):
        result = router.route_intent("")
        self.assertEqual(result.route, "none")
        self.assertEqual(result.method, "fallback")

    def test_none_input_returns_fallback(self):
        result = router.route_intent(None)
        self.assertEqual(result.route, "none")
        self.assertFalse(result.success)

    def test_numeric_input_returns_fallback(self):
        result = router.route_intent(12345)
        self.assertEqual(result.route, "none")
        self.assertFalse(result.success)

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_ollama_fallback_on_no_regex(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="none", raw_output="none", success=True, method="ollama",
        )
        result = router.route_intent("hola")
        self.assertTrue(result.success)
        self.assertEqual(result.method, "ollama")

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_ollama_error_returns_fallback(self, mock_http):
        mock_http.side_effect = RuntimeError("unexpected crash")
        result = router.route_intent("hola")
        self.assertFalse(result.success)
        self.assertEqual(result.route, "none")
        self.assertEqual(result.method, "fallback")


class TestIntentCache(unittest.TestCase):

    def setUp(self):
        router.clear_intent_cache()

    def test_regex_cache_hit(self):
        r1 = router.route_intent("nmap")
        self.assertFalse(r1.cached)
        r2 = router.route_intent("nmap")
        self.assertTrue(r2.cached)
        self.assertEqual(r2.route, "network_scan")

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_ollama_cache_hit(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="weather", raw_output="weather", success=True, method="ollama",
        )
        r1 = router.route_intent("algo raro")
        self.assertFalse(r1.cached)
        r2 = router.route_intent("algo raro")
        self.assertTrue(r2.cached)
        self.assertEqual(mock_http.call_count, 1)

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_cache_does_not_store_failures(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="none", raw_output="", success=False, method="fallback", error="timeout",
        )
        router.route_intent("test")
        router.route_intent("test")
        self.assertEqual(mock_http.call_count, 2)

    def test_clear_cache(self):
        router.route_intent("nmap")
        self.assertGreater(len(router._intent_cache), 0)
        router.clear_intent_cache()
        self.assertEqual(len(router._intent_cache), 0)

    def test_cache_ttl_expiry(self):
        expired_ts = time.time() - router._CACHE_TTL_S - 10
        router._intent_cache["model:test"] = (
            router.RouteResult(route="weather", raw_output="w", success=True, method="regex"),
            expired_ts,
        )
        self.assertIsNone(router._cache_get("model:test"))


class TestRunOllamaRouterHttp(unittest.TestCase):

    @patch("runtime.tool_tuning.router.requests.post")
    def test_valid_response(self, mock_post):
        mock_post.return_value = _mock_ollama_response("weather")
        result = router.run_ollama_router_http("clima hoy")
        self.assertEqual(result.route, "weather")
        self.assertTrue(result.success)
        self.assertEqual(result.method, "ollama")

    @patch("runtime.tool_tuning.router.requests.post")
    def test_invalid_label_normalized_to_none(self, mock_post):
        mock_post.return_value = _mock_ollama_response("banana")
        result = router.run_ollama_router_http("test")
        self.assertEqual(result.route, "none")

    @patch("runtime.tool_tuning.router.requests.post")
    def test_timeout(self, mock_post):
        import requests as req
        mock_post.side_effect = req.exceptions.Timeout("timed out")
        result = router.run_ollama_router_http("test")
        self.assertEqual(result.route, "none")
        self.assertFalse(result.success)

    @patch("runtime.tool_tuning.router.requests.post")
    def test_connection_error(self, mock_post):
        import requests as req
        mock_post.side_effect = req.exceptions.ConnectionError("refused")
        result = router.run_ollama_router_http("test")
        self.assertFalse(result.success)

    @patch("runtime.tool_tuning.router.requests.post")
    def test_http_500(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp
        result = router.run_ollama_router_http("test")
        self.assertFalse(result.success)

    @patch("runtime.tool_tuning.router.requests.post")
    def test_non_json_response(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = "<html>not json</html>"
        mock_resp.json.side_effect = ValueError("not json")
        mock_post.return_value = mock_resp
        result = router.run_ollama_router_http("test")
        self.assertFalse(result.success)


class TestDispatch(unittest.TestCase):

    def setUp(self):
        router.clear_intent_cache()

    def test_regex_dispatch_network(self):
        result = router.dispatch("nmap")
        self.assertIn("[network_scan]", result)

    def test_regex_dispatch_weather(self):
        result = router.dispatch("cómo está el clima")
        self.assertIn("[weather]", result)

    def test_empty_input_dispatch(self):
        result = router.dispatch("")
        self.assertIn("[none]", result)

    @patch("runtime.tool_tuning.router.run_ollama_router_http")
    def test_handler_crash_returns_none(self, mock_http):
        mock_http.return_value = router.RouteResult(
            route="weather", raw_output="weather", success=True, method="ollama",
        )
        original = router.HANDLERS["weather"]
        router.HANDLERS["weather"] = lambda q: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            result = router.dispatch("algo")
            self.assertIn("[none]", result)
        finally:
            router.HANDLERS["weather"] = original


class TestDispatchJson(unittest.TestCase):

    def setUp(self):
        router.clear_intent_cache()

    def test_regex_success_keys(self):
        result = router.dispatch_json("nmap")
        self.assertTrue(result["ok"])
        self.assertEqual(result["route"], "network_scan")
        self.assertIn("classify_ms", result)
        self.assertIn("method", result)
        self.assertEqual(result["method"], "regex")

    def test_empty_input_returns_dict(self):
        result = router.dispatch_json("")
        self.assertIsInstance(result, dict)
        self.assertEqual(result["route"], "none")
        self.assertFalse(result["ok"])


class TestValidRoutes(unittest.TestCase):

    def test_none_is_valid(self):
        self.assertIn("none", router.VALID_ROUTES)

    def test_all_handlers_have_routes(self):
        for route in router.HANDLERS:
            self.assertIn(route, router.VALID_ROUTES)

    def test_all_routes_have_handlers(self):
        for route in router.VALID_ROUTES:
            self.assertIn(route, router.HANDLERS)

    def test_network_scan_is_valid(self):
        self.assertIn("network_scan", router.VALID_ROUTES)

    def test_arr_monitor_is_valid(self):
        self.assertIn("arr_monitor", router.VALID_ROUTES)


class TestOllamaHealthcheck(unittest.TestCase):

    @patch("runtime.tool_tuning.router.requests.get")
    def test_healthy(self, mock_get):
        mock_get.return_value = MagicMock(status_code=200)
        self.assertTrue(router.ollama_healthcheck())

    @patch("runtime.tool_tuning.router.requests.get")
    def test_unreachable(self, mock_get):
        import requests as req
        mock_get.side_effect = req.exceptions.ConnectionError("refused")
        self.assertFalse(router.ollama_healthcheck())


if __name__ == "__main__":
    unittest.main()
