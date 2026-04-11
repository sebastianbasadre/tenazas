"""
finetuned_router.py
Wrapper para el modelo finetuneado (o cualquier Ollama model).
Extrae intent + args con parseo tolerante en 4 niveles.
Nunca explota — siempre retorna un dict.
"""

import re
import json
import logging
import requests
from typing import Optional

logger = logging.getLogger(__name__)

# ──────────────────────────────────────────────
# Config
# ──────────────────────────────────────────────

# Cuando el finetuned esté listo, cambiar esta línea solamente
FINETUNED_MODEL = "qwen3:8b"          # swap → "tenazas-router-q8:latest"
OLLAMA_URL      = "http://192.168.2.6:11434/api/generate"
TIMEOUT_SECS    = 15
TEMPERATURE     = 0.1                 # bajo: queremos outputs determinísticos

SYSTEM_PROMPT = """Sos un clasificador de intenciones para un asistente domótico personal.
Dado un mensaje en español, respondé ÚNICAMENTE con un JSON en este formato exacto:
{"intent": "...", "args": {...}}

Intents válidos:
  lights_on       → encender luces. Args opcionales: room (string), brightness (0-100), color (string)
  lights_off      → apagar luces. Args opcionales: room (string)
  cinema_mode     → modo cine. Args opcionales: brightness (0-100)
  weather         → consulta de clima. Args opcionales: location (string)
  media_search    → buscar película/serie. Args opcionales: title (string), type ("movie"|"series")
  infra_check     → estado de infraestructura/containers. Args opcionales: target (string)
  chat            → conversación general, sin acción específica

Reglas:
- Respondé SOLO el JSON, sin texto extra, sin ```
- Si no estás seguro del intent, usá "chat"
- Si no hay args claros, devolvé "args": {}
"""

# ──────────────────────────────────────────────
# Intents conocidos (para validación)
# ──────────────────────────────────────────────

KNOWN_INTENTS = {
    "lights_on", "lights_off", "cinema_mode",
    "weather", "media_search", "infra_check", "chat"
}

# ──────────────────────────────────────────────
# Keyword fallback (último recurso)
# ──────────────────────────────────────────────

KEYWORD_RULES = [
    (["cinema", "película", "pelicula", "movie", "cine"],           "cinema_mode"),
    (["apag", "turn off", "lights off", "luces off"],               "lights_off"),
    (["luz", "luces", "light", "encend", "prender", "turn on"],     "lights_on"),
    (["clima", "lluvia", "temperatura", "weather", "va a llover"],  "weather"),
    # "nas" como palabra completa para no matchear "buenas noches"
    (["container", "docker", r"\bnas\b", "infra", "caído", "caido"], "infra_check"),
    (["busca", "buscar", "serie", "episodio", "temporada"],         "media_search"),
]

def _keyword_intent(text: str) -> Optional[str]:
    text = text.lower()
    for keywords, intent in KEYWORD_RULES:
        for k in keywords:
            # strings con \b usan regex, el resto substring plain
            if "\\b" in k:
                if re.search(k, text):
                    return intent
            elif k in text:
                return intent
    return None

# ──────────────────────────────────────────────
# Parseo tolerante — 4 niveles
# ──────────────────────────────────────────────

def _parse_tolerant(raw: str) -> dict:
    """
    Intenta extraer intent+args del output crudo del modelo.
    Nivel 1: JSON directo
    Nivel 2: JSON dentro de ```json ... ```
    Nivel 3: primer { ... } en el string
    Nivel 4: keyword matching en texto libre
    """
    raw = raw.strip()

    # Nivel 1 — JSON limpio
    try:
        parsed = json.loads(raw)
        if "intent" in parsed:
            return {**parsed, "_parsed_by": "model_json"}
    except Exception:
        pass

    # Nivel 2 — bloque ```json ... ``` o ``` ... ```
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(1))
            if "intent" in parsed:
                return {**parsed, "_parsed_by": "model_json_fenced"}
        except Exception:
            pass

    # Nivel 3 — primer objeto JSON en el string (soporta un nivel de anidado: args: {})
    match = re.search(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)?\}", raw, re.DOTALL)
    if match:
        try:
            parsed = json.loads(match.group(0))
            if "intent" in parsed:
                return {**parsed, "_parsed_by": "model_json_extracted"}
        except Exception:
            pass

    # Nivel 4 — keyword fallback determinístico
    intent = _keyword_intent(raw)
    if intent:
        return {"intent": intent, "args": {}, "_parsed_by": "keyword_fallback"}

    # Sin resultado
    return {"intent": None, "args": {}, "_parsed_by": "no_match"}

# ──────────────────────────────────────────────
# Función principal
# ──────────────────────────────────────────────

def route(message: str) -> dict:
    """
    Clasifica un mensaje y retorna intent + args.
    Garantía: nunca lanza excepción, siempre retorna dict con clave 'intent'.

    Retorno:
    {
        "intent":     str | None,
        "args":       dict,
        "_parsed_by": "model_json" | "model_json_fenced" |
                      "model_json_extracted" | "keyword_fallback" |
                      "no_match" | "ollama_error",
        "_raw":       str,          # output crudo del modelo
        "_error":     str | None    # si hubo excepción
    }
    """
    raw_output = ""
    try:
        resp = requests.post(
            OLLAMA_URL,
            json={
                "model":  FINETUNED_MODEL,
                "prompt": message,
                "system": SYSTEM_PROMPT,
                "stream": False,
                "options": {"temperature": TEMPERATURE, "num_predict": 128}
            },
            timeout=TIMEOUT_SECS
        )
        resp.raise_for_status()
        raw_output = resp.json().get("response", "")
        result = _parse_tolerant(raw_output)

    except requests.exceptions.Timeout:
        logger.warning(f"[router] timeout after {TIMEOUT_SECS}s — model={FINETUNED_MODEL}")
        result = {
            "intent": _keyword_intent(message),
            "args": {},
            "_parsed_by": "ollama_error",
            "_error": "timeout"
        }
    except Exception as e:
        logger.error(f"[router] error: {e}")
        result = {
            "intent": _keyword_intent(message),
            "args": {},
            "_parsed_by": "ollama_error",
            "_error": str(e)
        }

    # Validar que el intent sea conocido
    if result.get("intent") and result["intent"] not in KNOWN_INTENTS:
        logger.warning(f"[router] unknown intent '{result['intent']}' → fallback to keyword")
        result["intent"] = _keyword_intent(message)
        result["_parsed_by"] = "keyword_fallback"

    result["_raw"] = raw_output
    result.setdefault("args", {})
    result.setdefault("_error", None)

    logger.debug(f"[router] msg={message!r} → intent={result['intent']} via={result['_parsed_by']}")
    return result


# ──────────────────────────────────────────────
# CLI rápido para testear sin pytest
# ──────────────────────────────────────────────

if __name__ == "__main__":
    import sys

    test_messages = [
        "prende las luces del living",
        "modo cine",
        "apagá todo",
        "cómo está el clima?",
        "está caído el container de sonarr?",
        "busca la segunda temporada de The Bear",
        "hola, cómo estás?",
        # edge cases
        "",
        "   ",
        "x" * 500,
    ]

    if len(sys.argv) > 1:
        test_messages = [" ".join(sys.argv[1:])]

    print(f"Model: {FINETUNED_MODEL}\n{'─'*60}")
    for msg in test_messages:
        result = route(msg)
        status = "✓" if result["intent"] else "?"
        print(f"{status} [{result['_parsed_by']:25s}] {msg!r:40s} → {result['intent']} {result['args']}")
        if result["_error"]:
            print(f"  ⚠ error: {result['_error']}")
