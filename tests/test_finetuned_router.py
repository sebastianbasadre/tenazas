"""
test_finetuned_router.py
Suite de tests para finetuned_router.py.
Cubre: parseo tolerante, keyword fallback, validación de intents, edge cases.
No requiere Ollama corriendo — mockea la llamada HTTP.
"""

import unittest
from unittest.mock import patch, MagicMock
import json
import sys
import os

# Works both on host (../workspace/runtime) and in container (/home/tenazas/runtime)
_host_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "workspace", "runtime")
_container_path = "/home/tenazas/runtime"
sys.path.insert(0, _container_path if os.path.isdir(_container_path) else _host_path)
import finetuned_router as router


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def mock_ollama(response_text: str):
    """Retorna un mock de requests.post que devuelve response_text."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {"response": response_text}
    return patch("finetuned_router.requests.post", return_value=mock_resp)


# ──────────────────────────────────────────────
# Tests: _parse_tolerant (sin red, sin mock)
# ──────────────────────────────────────────────

class TestParseTolerant(unittest.TestCase):

    def test_clean_json(self):
        raw = '{"intent": "lights_on", "args": {"room": "living"}}'
        r = router._parse_tolerant(raw)
        self.assertEqual(r["intent"], "lights_on")
        self.assertEqual(r["args"]["room"], "living")
        self.assertEqual(r["_parsed_by"], "model_json")

    def test_json_with_fences(self):
        raw = '```json\n{"intent": "cinema_mode", "args": {}}\n```'
        r = router._parse_tolerant(raw)
        self.assertEqual(r["intent"], "cinema_mode")
        self.assertEqual(r["_parsed_by"], "model_json_fenced")

    def test_json_fences_no_lang(self):
        raw = '```\n{"intent": "weather", "args": {"location": "Buenos Aires"}}\n```'
        r = router._parse_tolerant(raw)
        self.assertEqual(r["intent"], "weather")

    def test_json_embedded_in_text(self):
        raw = 'El intent es {"intent": "lights_off", "args": {}} según el modelo.'
        r = router._parse_tolerant(raw)
        self.assertEqual(r["intent"], "lights_off")
        self.assertEqual(r["_parsed_by"], "model_json_extracted")

    def test_keyword_fallback_cinema(self):
        raw = "Esto parece cinema o modo película"
        r = router._parse_tolerant(raw)
        self.assertEqual(r["intent"], "cinema_mode")
        self.assertEqual(r["_parsed_by"], "keyword_fallback")

    def test_keyword_fallback_lights_on(self):
        raw = "prender luces del cuarto"
        r = router._parse_tolerant(raw)
        self.assertEqual(r["intent"], "lights_on")

    def test_keyword_fallback_lights_off(self):
        raw = "apagar todo por favor"
        r = router._parse_tolerant(raw)
        self.assertEqual(r["intent"], "lights_off")

    def test_keyword_fallback_weather(self):
        raw = "consulta de clima para mañana"
        r = router._parse_tolerant(raw)
        self.assertEqual(r["intent"], "weather")

    def test_no_match_returns_none(self):
        raw = "xyz abc 123 completamente irrelevante"
        r = router._parse_tolerant(raw)
        self.assertIsNone(r["intent"])
        self.assertEqual(r["_parsed_by"], "no_match")

    def test_empty_string(self):
        r = router._parse_tolerant("")
        self.assertIsNone(r["intent"])

    def test_broken_json_partial(self):
        raw = '{"intent": "lights_on", "args": {'  # JSON incompleto
        r = router._parse_tolerant(raw)
        # No debe explotar — puede ser keyword o no_match
        self.assertIn("_parsed_by", r)

    def test_json_with_extra_whitespace(self):
        raw = '\n\n  {"intent": "infra_check", "args": {"target": "sonarr"}}  \n'
        r = router._parse_tolerant(raw)
        self.assertEqual(r["intent"], "infra_check")
        self.assertEqual(r["args"]["target"], "sonarr")

    def test_args_preserved(self):
        raw = '{"intent": "lights_on", "args": {"room": "cocina", "brightness": 75}}'
        r = router._parse_tolerant(raw)
        self.assertEqual(r["args"]["room"], "cocina")
        self.assertEqual(r["args"]["brightness"], 75)


# ──────────────────────────────────────────────
# Tests: route() con Ollama mockeado
# ──────────────────────────────────────────────

class TestRouteFunction(unittest.TestCase):

    def test_route_clean_json(self):
        with mock_ollama('{"intent": "lights_on", "args": {"room": "living"}}'):
            r = router.route("prendé las luces del living")
        self.assertEqual(r["intent"], "lights_on")
        self.assertEqual(r["args"]["room"], "living")
        self.assertIsNone(r["_error"])

    def test_route_always_has_required_keys(self):
        with mock_ollama('{"intent": "chat", "args": {}}'):
            r = router.route("hola")
        for key in ("intent", "args", "_parsed_by", "_raw", "_error"):
            self.assertIn(key, r, f"missing key: {key}")

    def test_route_unknown_intent_falls_back_to_keyword(self):
        # Modelo inventa un intent que no existe
        with mock_ollama('{"intent": "make_coffee", "args": {}}'):
            r = router.route("prende las luces")
        # Debe caer a keyword fallback (luces → lights_on)
        self.assertEqual(r["intent"], "lights_on")
        self.assertEqual(r["_parsed_by"], "keyword_fallback")

    def test_route_unknown_intent_no_keyword_match(self):
        with mock_ollama('{"intent": "fly_rocket", "args": {}}'):
            r = router.route("hola cómo estás")
        # No hay keyword match → intent None
        self.assertIsNone(r["intent"])

    def test_route_timeout_falls_back_to_keyword(self):
        import requests as req_lib
        with patch("finetuned_router.requests.post",
                   side_effect=req_lib.exceptions.Timeout):
            r = router.route("apagá las luces")
        self.assertEqual(r["_parsed_by"], "ollama_error")
        self.assertEqual(r["_error"], "timeout")
        self.assertEqual(r["intent"], "lights_off")  # keyword fallback activo

    def test_route_connection_error_no_crash(self):
        import requests as req_lib
        with patch("finetuned_router.requests.post",
                   side_effect=req_lib.exceptions.ConnectionError("refused")):
            r = router.route("modo cine")
        self.assertEqual(r["_parsed_by"], "ollama_error")
        self.assertEqual(r["intent"], "cinema_mode")  # keyword fallback
        self.assertIsNotNone(r["_error"])

    def test_route_model_returns_garbage(self):
        with mock_ollama("No sé qué hacer con eso"):
            r = router.route("enciende las luces")
        # Keyword fallback sobre el mensaje original
        self.assertIn(r["intent"], ("lights_on", None))
        self.assertIn("_parsed_by", r)

    def test_route_empty_message(self):
        with mock_ollama('{"intent": "chat", "args": {}}'):
            r = router.route("")
        self.assertIn("intent", r)  # no explota

    def test_route_very_long_message(self):
        with mock_ollama('{"intent": "chat", "args": {}}'):
            r = router.route("x " * 300)
        self.assertIn("intent", r)

    def test_route_raw_preserved(self):
        raw_text = '{"intent": "weather", "args": {}}'
        with mock_ollama(raw_text):
            r = router.route("clima de hoy")
        self.assertEqual(r["_raw"], raw_text)

    def test_route_media_search(self):
        with mock_ollama('{"intent": "media_search", "args": {"title": "The Bear", "type": "series"}}'):
            r = router.route("busca la segunda temporada de The Bear")
        self.assertEqual(r["intent"], "media_search")
        self.assertEqual(r["args"]["title"], "The Bear")

    def test_route_infra_check(self):
        with mock_ollama('{"intent": "infra_check", "args": {"target": "sonarr"}}'):
            r = router.route("está caído el container de sonarr?")
        self.assertEqual(r["intent"], "infra_check")
        self.assertEqual(r["args"]["target"], "sonarr")


# ──────────────────────────────────────────────
# Tests: _keyword_intent directamente
# ──────────────────────────────────────────────

class TestKeywordIntent(unittest.TestCase):

    def test_cinema_variants(self):
        for msg in ["modo cinema", "poner cine", "movie mode", "pelicula"]:
            self.assertEqual(router._keyword_intent(msg), "cinema_mode", msg)

    def test_lights_off_priority_over_lights_on(self):
        # "apag" debe matchear lights_off aunque contenga otras palabras
        self.assertEqual(router._keyword_intent("apagá las luces"), "lights_off")

    def test_lights_on(self):
        for msg in ["prende la luz", "encender luces", "turn on lights", "prender"]:
            self.assertEqual(router._keyword_intent(msg), "lights_on", msg)

    def test_weather(self):
        for msg in ["el clima", "va a llover", "temperatura hoy", "weather"]:
            self.assertEqual(router._keyword_intent(msg), "weather", msg)

    def test_infra(self):
        for msg in ["docker ps", "container caído", "nas no responde", "infra check"]:
            self.assertEqual(router._keyword_intent(msg), "infra_check", msg)

    def test_no_match(self):
        self.assertIsNone(router._keyword_intent("hola buenas noches"))
        self.assertIsNone(router._keyword_intent(""))
        self.assertIsNone(router._keyword_intent("   "))


# ──────────────────────────────────────────────

if __name__ == "__main__":
    unittest.main(verbosity=2)
