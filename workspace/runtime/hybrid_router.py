#!/usr/bin/env python3
"""
hybrid_router.py — Hybrid rules + model router for Tenazas.

Applies hard rules first (instant, no LLM call). Only falls back to the
finetuned tenazas-multitool model for ambiguous queries.

API:
    route(query) -> list[str]   # list of tool names, empty = no tools

Integration with orchestrator.py:
    Replace the direct _call_multitool_model() call with hybrid_router.route()
    so that obvious queries never hit the model at all.

    # In route_and_execute_multitool(), change:
    #     raw_response = _call_multitool_model(query)
    #     parse_result = parse_multitool_response(raw_response)
    #     tools = parse_result["tools"]
    # To:
    #     from runtime.hybrid_router import route as hybrid_route
    #     tools = hybrid_route(query)
"""

from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from typing import Iterable, Set

import requests

try:
    from runtime_config import OLLAMA_GPU_GENERATE_URL
except ImportError:
    from workspace.runtime.runtime_config import OLLAMA_GPU_GENERATE_URL

# ── Config ───────────────────────────────────────────────────────

OLLAMA_URL = OLLAMA_GPU_GENERATE_URL
MODEL_NAME = "tenazas-multitool"
SYSTEM_PROMPT = (
    "You are a deterministic multi-tool caller. "
    "Respond only with valid XML. Allowed format:\n"
    "<tools>\n  <tool>TOOL_NAME</tool>\n</tools>\n"
    "Allowed tools: weather, email, calendar, infra-monitor, "
    "network-scanner, security-monitor, arr-monitor, self-evolve, reminders, "
    "smart-home-on, smart-home-off, smart-home-cinema, tts.\n"
    "No extra text. If no tools apply, respond with <tools></tools>."
)

ALLOWLIST: Set[str] = {
    "weather", "email", "calendar", "infra-monitor", "network-scanner",
    "security-monitor", "arr-monitor", "self-evolve", "reminders", "smart-home-on",
    "smart-home-off", "smart-home-cinema", "tts",
}


# ── Normalization ────────────────────────────────────────────────

def _strip_accents(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text)
    return "".join(ch for ch in normalized if not unicodedata.combining(ch))


def _normalize(query: str) -> str:
    """Lowercase, strip accents, clean punctuation, collapse whitespace."""
    text = query.strip().lower()
    text = _strip_accents(text)
    # Keep letters, numbers, spaces, ?, ! and hyphens; clean the rest
    text = re.sub(r"[\"'`\u00b4\u00a8^~|<>[\]{}()_=+*/\\:;,.]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


# ── Match helpers ────────────────────────────────────────────────

def _contains_any(text: str, patterns: Iterable[str]) -> bool:
    return any(pattern in text for pattern in patterns)


def _equals_any(text: str, patterns: Iterable[str]) -> bool:
    return text in patterns


# ── Hard Rules ───────────────────────────────────────────────────

def _hard_rule_none(q: str) -> bool:
    """Saludos, social, identidad, humor, estado luces, portfolio."""
    greetings_exact = {
        "hola", "buen dia", "buenos dias", "buenas tardes",
        "buenas noches", "buenas", "hi", "hey",
    }

    social_exact = {
        "como estas", "como andas", "que tal", "todo bien",
        "gracias", "genial", "perfecto", "ok", "dale",
        "joya", "chau", "nos vemos",
    }

    identity_contains = {
        "quien sos", "que sos", "que podes hacer", "que sabes hacer",
        "cuales son tus funciones", "me decis que haces",
        "tus skills", "decime quien sos",
        "me decis tus skills", "podes hablar con",
    }

    humor_contains = {
        "contame un chiste", "sabes chistes", "contame algo gracioso",
        "haceme reir",
    }

    device_state_contains = {
        "cuantas luces estan encendidas", "cuantas luces hay",
        "estado de las luces",
    }

    portfolio_contains = {
        "portafolio", "portfolio",
        "audita mi portafolio", "audita mi portfolio",
        "analiza mi portfolio", "analiza mi portafolio",
        "revisa mi portfolio", "revisa mi portafolio",
    }

    if _equals_any(q, greetings_exact | social_exact):
        return True

    if _contains_any(
        q,
        identity_contains
        | humor_contains
        | device_state_contains
        | portfolio_contains,
    ):
        return True

    return False


def _hard_rule_email(q: str) -> bool:
    """Email sin TTS — matchea frases exactas y como substring."""
    email_patterns = {
        "lee ultimo email", "lee ultimos mails", "leeme los mails",
        "tengo mails", "hay mails",
    }
    return _equals_any(q, email_patterns) or _contains_any(q, email_patterns)


def _hard_rule_tts(q: str) -> bool:
    """Solo dispara si la intencion de voz/audio es explicita.

    Evita falsos positivos por palabras como 'hablar'.
    """
    tts_patterns = {
        "habla por voz", "habla por audio", "habla en voz alta",
        "decilo en voz alta", "respondeme en audio", "responde en audio",
        "mandalo en audio", "manda audio", "ponelo en voz",
        "decilo por el parlante", "por el parlante",
        "quiero escucharlo", "quiero oirlo", "escucho",
        "texto a voz", "pasalo a voz",
        "converti eso a voz", "converti el texto a audio",
    }

    if _equals_any(q, tts_patterns) or _contains_any(q, tts_patterns):
        return True

    # Heuristic: explicit voz/audio/parlante keyword + output verb
    explicit_keywords = {
        " audio", "audio ", " parlante", "parlante ", "voz", "en voz alta",
    }
    output_verbs = {
        "deci", "decime", "decilo", "responde", "respondeme",
        "manda", "ponelo", "converti", "pasalo", "habla",
    }
    if _contains_any(q, explicit_keywords) and _contains_any(q, output_verbs):
        return True

    return False


def _hard_rule_security(q: str) -> bool:
    """Explicit homelab security posture/audit requests."""
    security_patterns = {
        "seguridad", "security", "puertos expuestos", "servicios expuestos",
        "superficie de ataque", "firewall", "credenciales expuestas",
        "paneles admin", "admin panels", "auditoria de seguridad",
        "auditar seguridad", "audita seguridad", "security check",
    }
    return _contains_any(q, security_patterns)


def _hard_rule_network(q: str) -> bool:
    network_patterns = {
        "red", "lan", "nmap", "dispositivos", "hosts", "ips activas",
        "scan de red", "escanear la red", "escanea la red",
    }
    return _contains_any(q, network_patterns)


def _hard_rule_infra(q: str) -> bool:
    infra_patterns = {
        "containers", "container", "docker", "infra", "infraestructura",
        "sistema", "nas", "prometheus", "grafana",
    }
    return _contains_any(q, infra_patterns)


# ── Model fallback ──────────────────────────────────────────────

def _call_model(query: str) -> str | None:
    """Call tenazas-multitool via Ollama. Returns raw text or None."""
    payload = {
        "model": MODEL_NAME,
        "prompt": query,
        "system": SYSTEM_PROMPT,
        "stream": False,
        "options": {"temperature": 0, "top_p": 1, "num_predict": 128},
    }
    try:
        resp = requests.post(OLLAMA_URL, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()
        return str(data.get("response", "")).strip()
    except Exception:
        return None


def _extract_xml_fragment(text: str) -> str:
    """Rescue the <tools>...</tools> block. Returns empty if truncated."""
    start = text.find("<tools>")
    end = text.rfind("</tools>")
    if start == -1 or end == -1 or end < start:
        return ""
    return text[start:end + len("</tools>")]


def _parse_tools_xml(text: str) -> list[str]:
    """Extract valid tool names from model XML response."""
    xml_text = _extract_xml_fragment(text)
    if not xml_text:
        return []

    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    if root.tag != "tools":
        return []

    tools: list[str] = []
    seen: set[str] = set()
    for child in root:
        if child.tag != "tool":
            continue
        name = (child.text or "").strip()
        if name in ALLOWLIST and name not in seen:
            seen.add(name)
            tools.append(name)

    return tools


# ── Public API ───────────────────────────────────────────────────

def route(query: str) -> list[str]:
    """Route a query to tool names. Empty list = no tools (chat/LLM).

    1. Normalize query
    2. Check hard rules (instant, no model call)
    3. If no rule matches -> call tenazas-multitool model
    4. Parse and validate model response against allowlist
    """
    q = _normalize(query)

    if not q:
        return []

    # Hard rules — instant, no model call
    if _hard_rule_none(q):
        return []

    if _hard_rule_email(q):
        return ["email"]

    if _hard_rule_tts(q):
        return ["tts"]

    if _hard_rule_security(q):
        tools = ["security-monitor"]
        if _hard_rule_network(q):
            tools.append("network-scanner")
        if _hard_rule_infra(q) and len(tools) < 3:
            tools.append("infra-monitor")
        return tools

    # Model fallback for ambiguous queries
    raw = _call_model(query)  # pass original (model trained on natural text)
    if raw is None:
        return []

    return _parse_tools_xml(raw)


# ── Self-test ────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        ("buenos dias", []),
        ("contame un chiste", []),
        ("quién sos?", []),
        ("cuantas luces estan encendidas", []),
        ("audita mi portafolio", []),
        ("lee ultimo email", ["email"]),
        ("clima y agenda", ["weather", "calendar"]),
        ("prende las luces", ["smart-home-on"]),
        ("qué recordatorios tengo", ["reminders"]),
        ("me decis tus skills ?", []),
        ("podes hablar con otros modelos?", []),
    ]

    print("=== Tests hybrid_router ===")
    for query, expected in tests:
        result = route(query)
        status = "\u2705" if result == expected else "\u274c"
        print(f"{status} '{query}' \u2192 {result} (esperado: {expected})")

    print("\n=== Diff sugerido para orchestrator.py ===")
    print(
        r"""
--- orchestrator.py (antes)
+++ orchestrator.py (después)

@@ route_and_execute_multitool() @@
-    raw_response = _call_multitool_model(query)
-    if not raw_response:
-        ...fallback...
-    parse_result = parse_multitool_response(raw_response)
-    if not parse_result["valid"]:
-        ...fallback...
-    tools = parse_result["tools"]
+    from runtime.hybrid_router import route as hybrid_route
+    tools = hybrid_route(query)
+    # hybrid_router.route() ya aplica:
+    # 1. reglas duras (sin modelo)
+    # 2. llamada al modelo solo si hace falta
+    # 3. parseo XML
+    # 4. filtro allowlist
"""
    )
