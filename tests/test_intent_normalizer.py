"""
test_intent_normalizer.py
Tests para intent_normalizer.py — schemas, slot filling, confianza, edge cases.
"""

import unittest
import sys
import os

# Works both on host (../workspace/runtime) and in container (/home/tenazas/runtime)
_host_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "workspace", "runtime")
_container_path = "/home/tenazas/runtime"
sys.path.insert(0, _container_path if os.path.isdir(_container_path) else _host_path)
from intent_normalizer import normalize, normalize_or_chat, NormalizedCall, INTENT_SCHEMAS


# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────

def router_out(intent, args=None, parsed_by="model_json"):
    return {"intent": intent, "args": args or {}, "_parsed_by": parsed_by}

def keyword_out(intent):
    return router_out(intent, parsed_by="keyword_fallback")


# ──────────────────────────────────────────────
# Tests: estructura básica
# ──────────────────────────────────────────────

class TestNormalizeBasic(unittest.TestCase):

    def test_returns_normalized_call(self):
        r = normalize(router_out("lights_on"), "prende las luces")
        self.assertIsInstance(r, NormalizedCall)

    def test_none_on_no_intent(self):
        r = normalize({"intent": None, "args": {}, "_parsed_by": "no_match"})
        self.assertIsNone(r)

    def test_none_on_missing_intent_key(self):
        r = normalize({"args": {}, "_parsed_by": "no_match"})
        self.assertIsNone(r)

    def test_normalize_or_chat_never_none(self):
        r = normalize_or_chat({"intent": None, "args": {}, "_parsed_by": "no_match"})
        self.assertIsNotNone(r)
        self.assertEqual(r.intent, "chat")
        self.assertEqual(r.skill, "chat")

    def test_unknown_intent_falls_back_to_chat(self):
        r = normalize(router_out("fly_to_moon"), "llevame a la luna")
        self.assertIsNotNone(r)
        self.assertEqual(r.skill, "chat")
        self.assertEqual(r.confidence, "low")

    def test_to_dict_has_required_keys(self):
        r = normalize(router_out("weather"), "clima")
        d = r.to_dict()
        for key in ("intent", "skill", "action", "args", "confidence",
                     "source", "missing_slots", "valid"):
            self.assertIn(key, d, f"missing key: {key}")


# ──────────────────────────────────────────────
# Tests: skill names correctos
# ──────────────────────────────────────────────

class TestSkillNames(unittest.TestCase):

    def test_lights_on_skill(self):
        r = normalize(router_out("lights_on"))
        self.assertEqual(r.skill, "skill:smart-home")
        self.assertEqual(r.action, "on")

    def test_lights_off_skill(self):
        r = normalize(router_out("lights_off"))
        self.assertEqual(r.skill, "skill:smart-home")
        self.assertEqual(r.action, "off")

    def test_cinema_skill(self):
        r = normalize(router_out("cinema_mode"))
        self.assertEqual(r.skill, "skill:smart-home")
        self.assertEqual(r.action, "cinema")

    def test_weather_skill(self):
        r = normalize(router_out("weather"))
        self.assertEqual(r.skill, "skill:weather")
        self.assertEqual(r.action, "current")

    def test_media_search_skill(self):
        r = normalize(router_out("media_search"))
        self.assertEqual(r.skill, "skill:arr-monitor")
        self.assertEqual(r.action, "search")

    def test_infra_check_skill(self):
        r = normalize(router_out("infra_check"))
        self.assertEqual(r.skill, "skill:infra-monitor")
        self.assertEqual(r.action, "check")

    def test_email_skill(self):
        r = normalize(router_out("email"))
        self.assertEqual(r.skill, "skill:email")

    def test_calendar_skill(self):
        r = normalize(router_out("calendar"))
        self.assertEqual(r.skill, "skill:calendar-agenda")

    def test_chat_skill(self):
        r = normalize(router_out("chat"))
        self.assertEqual(r.skill, "chat")


# ──────────────────────────────────────────────
# Tests: defaults aplicados
# ──────────────────────────────────────────────

class TestDefaults(unittest.TestCase):

    def test_lights_on_default_brightness(self):
        r = normalize(router_out("lights_on"))
        self.assertEqual(r.args["brightness"], 100)
        self.assertEqual(r.args["room"], "all")

    def test_lights_off_default_room(self):
        r = normalize(router_out("lights_off"))
        self.assertEqual(r.args["room"], "all")

    def test_cinema_default_brightness(self):
        r = normalize(router_out("cinema_mode"))
        self.assertEqual(r.args["brightness"], 10)
        self.assertEqual(r.args["room"], "living")

    def test_weather_default_location(self):
        r = normalize(router_out("weather"))
        self.assertEqual(r.args["location"], "Buenos Aires")
        self.assertEqual(r.args["days"], 1)

    def test_infra_default_target(self):
        r = normalize(router_out("infra_check"))
        self.assertEqual(r.args["target"], "all")


# ──────────────────────────────────────────────
# Tests: args del modelo sobreescriben defaults
# ──────────────────────────────────────────────

class TestArgOverrides(unittest.TestCase):

    def test_lights_on_custom_room(self):
        r = normalize(router_out("lights_on", {"room": "cocina"}))
        self.assertEqual(r.args["room"], "cocina")
        self.assertEqual(r.args["brightness"], 100)  # default preservado

    def test_lights_on_custom_brightness(self):
        r = normalize(router_out("lights_on", {"brightness": 50}))
        self.assertEqual(r.args["brightness"], 50)

    def test_cinema_custom_brightness(self):
        r = normalize(router_out("cinema_mode", {"brightness": 5}))
        self.assertEqual(r.args["brightness"], 5)

    def test_weather_custom_location(self):
        r = normalize(router_out("weather", {"location": "Mendoza"}))
        self.assertEqual(r.args["location"], "Mendoza")

    def test_infra_custom_target(self):
        r = normalize(router_out("infra_check", {"target": "sonarr"}))
        self.assertEqual(r.args["target"], "sonarr")

    def test_media_search_with_title(self):
        r = normalize(router_out("media_search",
                                 {"title": "The Bear", "type": "series"}))
        self.assertEqual(r.args["title"], "The Bear")
        self.assertEqual(r.args["type"], "series")

    def test_unknown_arg_ignored(self):
        r = normalize(router_out("lights_on",
                                 {"room": "living", "hack": "DROP TABLE"}))
        self.assertNotIn("hack", r.args)
        self.assertEqual(r.args["room"], "living")

    def test_none_arg_uses_default(self):
        r = normalize(router_out("lights_on", {"room": None}))
        self.assertEqual(r.args["room"], "all")

    def test_empty_string_arg_uses_default(self):
        r = normalize(router_out("lights_on", {"room": ""}))
        self.assertEqual(r.args["room"], "all")


# ──────────────────────────────────────────────
# Tests: confianza según fuente
# ──────────────────────────────────────────────

class TestConfidence(unittest.TestCase):

    def test_model_json_is_high(self):
        r = normalize(router_out("weather", parsed_by="model_json"))
        self.assertEqual(r.confidence, "high")

    def test_model_json_fenced_is_high(self):
        r = normalize(router_out("weather", parsed_by="model_json_fenced"))
        self.assertEqual(r.confidence, "high")

    def test_model_json_extracted_is_medium(self):
        r = normalize(router_out("weather", parsed_by="model_json_extracted"))
        self.assertEqual(r.confidence, "medium")

    def test_keyword_fallback_is_low(self):
        r = normalize(keyword_out("lights_on"))
        self.assertEqual(r.confidence, "low")

    def test_ollama_error_is_low(self):
        r = normalize(router_out("cinema_mode", parsed_by="ollama_error"))
        self.assertEqual(r.confidence, "low")


# ──────────────────────────────────────────────
# Tests: valid flag
# ──────────────────────────────────────────────

class TestValidFlag(unittest.TestCase):

    def test_valid_when_no_required_slots(self):
        for intent in INTENT_SCHEMAS:
            r = normalize(router_out(intent))
            self.assertTrue(r.valid, f"intent {intent} debería ser valid")

    def test_valid_true_sets_missing_slots_empty(self):
        r = normalize(router_out("lights_on"))
        self.assertEqual(r.missing_slots, [])
        self.assertTrue(r.valid)


# ──────────────────────────────────────────────
# Tests: raw_message preservado
# ──────────────────────────────────────────────

class TestRawMessage(unittest.TestCase):

    def test_raw_message_preserved(self):
        msg = "prendé las luces del living por favor"
        r = normalize(router_out("lights_on"), msg)
        self.assertEqual(r.raw_message, msg)

    def test_raw_message_empty_default(self):
        r = normalize(router_out("lights_on"))
        self.assertEqual(r.raw_message, "")


# ──────────────────────────────────────────────
# Tests: pipeline completo router → normalizer
# ──────────────────────────────────────────────

class TestPipeline(unittest.TestCase):

    def test_keyword_lights_on_pipeline(self):
        router_result = {
            "intent": "lights_on", "args": {},
            "_parsed_by": "keyword_fallback", "_raw": "", "_error": None,
        }
        r = normalize_or_chat(router_result, "prende las luces")
        self.assertEqual(r.skill, "skill:smart-home")
        self.assertEqual(r.action, "on")
        self.assertEqual(r.confidence, "low")
        self.assertTrue(r.valid)

    def test_model_cinema_with_brightness_pipeline(self):
        router_result = {
            "intent": "cinema_mode", "args": {"brightness": 5},
            "_parsed_by": "model_json", "_raw": "", "_error": None,
        }
        r = normalize_or_chat(router_result, "modo cine con poca luz")
        self.assertEqual(r.skill, "skill:smart-home")
        self.assertEqual(r.action, "cinema")
        self.assertEqual(r.args["brightness"], 5)
        self.assertEqual(r.confidence, "high")

    def test_no_intent_goes_to_chat(self):
        router_result = {
            "intent": None, "args": {},
            "_parsed_by": "no_match", "_raw": "", "_error": None,
        }
        r = normalize_or_chat(router_result, "hola cómo estás")
        self.assertEqual(r.skill, "chat")
        self.assertEqual(r.intent, "chat")

    def test_infra_with_target(self):
        router_result = {
            "intent": "infra_check", "args": {"target": "sonarr"},
            "_parsed_by": "keyword_fallback", "_raw": "", "_error": None,
        }
        r = normalize_or_chat(router_result, "está caído sonarr?")
        self.assertEqual(r.skill, "skill:infra-monitor")
        self.assertEqual(r.args["target"], "sonarr")


if __name__ == "__main__":
    unittest.main(verbosity=2)
