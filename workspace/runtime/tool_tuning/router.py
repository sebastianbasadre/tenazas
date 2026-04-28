#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
router.py — Deterministic intent classifier for Tenazas tool routing.

Classifies user queries into one of these labels:
    weather, calendar, weather_calendar, lights, network_scan,
    infra, tts, arr_monitor, none

Uses keyword/regex matching first (fast path), then falls back to
Ollama LLM classification if no confident match is found.

The official label for network scanning is `network_scan` (underscore).
This matches TOOL_REGISTRY in multitool_parser.py, HANDLERS in
router_runtime.py, and the Modelfile_router_labels system prompt.

Features:
- Regex fast-path (no LLM needed for most queries)
- Ollama LLM fallback for ambiguous queries
- Structured JSON logging to logs/tool_router.log
- Label validation against closed set
- Intent cache with TTL
- Safe error handling (never crashes)

Variables opcionales:
    OLLAMA_HOST=http://192.168.2.6:11434
    OLLAMA_MODEL=tenazas-router-labels
    OLLAMA_TIMEOUT=30
    ROUTER_CACHE_TTL=60

Uso:
    python router.py "escanea la red"
"""

# ─────────────────────────────────────────────────────────────────
# router.py — Segunda capa de clasificación de intents
# Ver contrato: /home/tenazas/runtime/INTERFACE_CONTRACT.md
# VALID_ROUTES se importa de routes_contract.py — NO duplicar acá
# ─────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, asdict
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Dict, Optional

import requests

try:
    from runtime_config import OLLAMA_GPU_URL
except ImportError:
    from workspace.runtime.runtime_config import OLLAMA_GPU_URL

# Import canonical labels from contract
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from routes_contract import VALID_ROUTES, normalize_route as _contract_normalize

DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "tenazas-router-labels")
DEFAULT_OLLAMA_HOST = OLLAMA_GPU_URL
DEFAULT_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "30"))

# ── Structured JSON logging ──────────────────────────────────────────

LOG_DIR = Path("/home/tenazas/runtime/logs")
LOG_FILE = LOG_DIR / "tool_router.log"

logger = logging.getLogger("tenazas-router")
logger.setLevel(logging.INFO)

if LOG_DIR.exists():
    _fh = RotatingFileHandler(str(LOG_FILE), maxBytes=5_000_000, backupCount=3)
    _fh.setLevel(logging.INFO)
    logger.addHandler(_fh)

_sh = logging.StreamHandler()
_sh.setLevel(logging.WARNING)
logger.addHandler(_sh)


def _log(entry: dict):
    """Write structured JSON log line."""
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    logger.info(json.dumps(entry, ensure_ascii=False))


# ── Intent cache (LRU with manual TTL) ───────────────────────────────

_CACHE_TTL_S = int(os.getenv("ROUTER_CACHE_TTL", "60"))
_CACHE_MAX_SIZE = 128
_intent_cache: Dict[str, tuple] = {}  # key → (RouteResult, timestamp)


def _cache_get(key: str) -> Optional["RouteResult"]:
    """Return cached RouteResult if still fresh, else None."""
    entry = _intent_cache.get(key)
    if entry is None:
        return None
    result, ts = entry
    if time.time() - ts > _CACHE_TTL_S:
        _intent_cache.pop(key, None)
        return None
    return result


def _cache_put(key: str, result: "RouteResult"):
    """Store RouteResult in cache. Evict oldest if at capacity."""
    if len(_intent_cache) >= _CACHE_MAX_SIZE:
        oldest_key = min(_intent_cache, key=lambda k: _intent_cache[k][1])
        _intent_cache.pop(oldest_key, None)
    _intent_cache[key] = (result, time.time())


def clear_intent_cache():
    """Clear the intent cache. Useful for testing."""
    _intent_cache.clear()


# ── Data classes ─────────────────────────────────────────────────────

@dataclass
class RouteResult:
    route: str
    raw_output: str
    success: bool
    method: str = "unknown"   # "regex" | "ollama" | "fallback"
    error: Optional[str] = None
    cached: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _make_fallback(error: str, raw_output: str = "") -> RouteResult:
    """Create a safe fallback result with 'none' route."""
    return RouteResult(
        route="none", raw_output=raw_output, success=False,
        method="fallback", error=error,
    )


# ── Regex rules (fast path, no LLM needed) ───────────────────────────

# Order matters: more specific patterns first.
# Each entry: (label, list_of_compiled_patterns, min_matches)

_RULES: list[tuple[str, list[re.Pattern], int]] = []


def _add_rule(label: str, patterns: list[str], min_matches: int = 1):
    compiled = [re.compile(p, re.IGNORECASE) for p in patterns]
    _RULES.append((label, compiled, min_matches))


# --- weather + calendar combo (check BEFORE individual) ---
_add_rule("weather_calendar", [
    r"(clima|tiempo|temperatura).{0,30}(agenda|eventos?|reuni[oó]n|calendario)",
    r"(agenda|eventos?|reuni[oó]n|calendario).{0,30}(clima|tiempo|temperatura)",
    r"(pron[oó]stico|llover).{0,30}(reuni[oó]n|eventos?|agenda)",
])

# --- network_scan: device/host/LAN discovery ---
_add_rule("network_scan", [
    r"escan[eé][aá]\w*\s+(la\s+)?red",
    r"escanear\s+(la\s+)?red",
    r"scan\s+de\s+red",
    r"escanear\s+(la\s+)?lan",
    r"scan\s+lan",
    r"descubrir\s+dispositivos",
    r"dispositivos?\s+activos",
    r"hosts?\s+activos",
    r"quien\s+est[aá]\s+conectad",
    r"qu[eé]\s+hay\s+en\s+la\s+red",
    r"\bnmap\b",
    r"\barp\s+scan\b",
    r"ping\s+sweep",
    r"host\s+discovery",
    r"network\s+discovery",
    r"scan\s+ip",
    r"mapa\s+de\s+red",
    r"dispositivos?\s+(en|de)\s+(la\s+)?red",
    r"(cu[aá]ntos|qu[eé])\s+dispositivos",
    r"qui[eé]n\s+(se\s+)?conect[oó]",
    r"equipos?\s+(en|de)\s+(la\s+)?red",
    r"topolog[ií]a\s+de\s+red",
    r"IPs?\s+activas?",
    r"192\.168\.\d+\.\d+/\d+",
    r"dispositivos?\s+desconocidos",
    r"dispositivos?\s+nuevos?\s+(en\s+la\s+)?red",
    r"list[aá]\w*\s+(los\s+)?equipos",
    r"detect[aá]\w*\s+dispositivos",
    r"busca\w*\s+dispositivos\s+(en\s+la\s+)?lan",
    r"dispositivos?\s+conectados?",
    r"qu[eé]\s+est[aá]\s+conectado",
])

# --- infra: services/homelab/system status (NOT device discovery) ---
_add_rule("infra", [
    r"(estado|status)\s+(de(l|\s+los))?\s*(containers?|docker|infra|servidor|homelab|sistema)",
    r"(c[oó]mo\s+)(est[aá]n?|andan?)\s+(los\s+)?(containers?|la\s+infra|el\s+servidor|el\s+homelab|el\s+docker)",
    r"docker\s+(status|ps)",
    r"(cu[aá]ntos|qu[eé])\s+containers?\s+(corren|hay|est[aá]n)",
    r"(uso\s+de\s+)?(cpu|ram|disco|memoria)\s+(del\s+)?nas",
    r"temperatura\s+del\s+nas",
    r"espacio\s+disponible",
    r"performance\s+del\s+servidor",
    r"(hay\s+)?alertas?\s+de\s+sistema",
    r"recursos?\s+del\s+sistema",
    r"est[aá]\s+bien\s+la\s+infra",
    r"estado\s+general\s+del\s+sistema",
    r"hardware\s+(del\s+)?(nas|servidor)",
])

# --- arr_monitor: media/downloads stack ---
_add_rule("arr_monitor", [
    r"arr[\s-]?stack",
    r"\b(sonarr|radarr|lidarr|prowlarr|qbittorrent|sabnzbd|seerr|requestrr|tautulli)\b",
    r"(estado|status)\s+(de(l|\s+las))?\s*(descargas?|torrents?|media\s+stack)",
    r"(c[oó]mo\s+)(est[aá]n?|andan?)\s+(las\s+)?descargas",
    r"(hay\s+)?(algo\s+)?descargando",
    r"cola\s+de\s+descarga",
    r"(cu[aá]ntos?|hay)\s+torrents?\s+activos?",
    r"velocidad\s+de\s+descarga",
    r"indexer.{0,10}prowlarr",
])

# --- weather ---
_add_rule("weather", [
    r"(c[oó]mo\s+est[aá]|qu[eé]\s+onda)\s+(el\s+)?(clima|tiempo)",
    r"(va\s+a\s+)?llover",
    r"temperatura\s+(afuera|exterior|de\s+hoy)",
    r"hace\s+(fr[ií]o|calor)",
    r"pron[oó]stico",
    r"(est[aá]\s+)?lindo\s+afuera",
    r"necesito\s+abrigo",
    r"decime\s+el\s+clima",
    r"clima\s+en\s+",
])

# --- calendar ---
_add_rule("calendar", [
    r"(qu[eé]\s+tengo|mis?\s+eventos?|agenda|calendario|reuniones?)\s+(de\s+)?(hoy|ma[nñ]ana|esta\s+semana)?",
    r"pr[oó]ximos?\s+eventos?",
    r"tengo\s+(algo|reuniones?|eventos?)\s+hoy",
    r"mi\s+calendario",
])

# --- lights ---
_add_rule("lights", [
    r"(prend[eé]|encend[eé]|apag[aá])\s+(las\s+)?luces",
    r"(modo|pon[eé])\s+(cine|cinema|relax|trabajo|work)",
    r"luces?\s+(de\s+)?(cine|cinema|relax|trabajo|work)",
    r"\b(on|off)\b\s+(las\s+)?luces",
    r"smart[\s-]?home",
])

# --- tts ---
_add_rule("tts", [
    r"(mand[aá]|envi[aá])\s*(me\s+)?(un\s+)?audio",
    r"(dec[ií]|habl[aá]|le[eé])\s+(en\s+)?voz\s+alta",
    r"text[\s-]?to[\s-]?speech",
    r"\btts\b",
    r"(convert[ií]|sintetiz[aá])\w*\s+(a\s+)?(voz|audio)",
    r"respuesta?\s+en\s+audio",
    r"(mand[aá]|envi[aá])\s+voz",
    r"quiero\s+escucharlo",
])


def _classify_regex(text: str) -> Optional[str]:
    """Try to classify via regex rules. Returns label or None."""
    for label, patterns, min_matches in _RULES:
        matches = sum(1 for p in patterns if p.search(text))
        if matches >= min_matches:
            return label
    return None


# ── Core functions ───────────────────────────────────────────────────

def normalize_route(text: str) -> str:
    """Clean model output: trim, first non-empty line, lower, fallback."""
    if not text:
        return "none"
    for line in text.splitlines():
        line = line.strip().lower()
        if line:
            return line
    return "none"


def validate_label(label: str) -> str:
    """Validate label against VALID_ROUTES. Returns 'none' on invalid."""
    normalized = _contract_normalize(label)
    if normalized != label and label != "none":
        _log({
            "action": "label_validation_failed",
            "invalid_label": label,
            "fallback": normalized,
        })
    return normalized


def ollama_healthcheck(host: str = DEFAULT_OLLAMA_HOST, timeout: int = 5) -> bool:
    """Quick check if Ollama is reachable."""
    try:
        r = requests.get(f"{host}/api/tags", timeout=timeout)
        return r.status_code == 200
    except requests.RequestException:
        return False


def run_ollama_router_http(
    prompt: str,
    model: str = DEFAULT_MODEL,
    host: str = DEFAULT_OLLAMA_HOST,
    timeout: int = DEFAULT_TIMEOUT,
) -> RouteResult:
    """Call Ollama for classification. Never returns None."""
    url = f"{host}/api/generate"
    payload = {"model": model, "prompt": prompt, "stream": False}

    try:
        response = requests.post(url, json=payload, timeout=timeout)
    except requests.exceptions.Timeout:
        _log({"action": "ollama_error", "error_type": "timeout", "url": url})
        return _make_fallback(f"Timeout llamando a Ollama en {url}")
    except requests.exceptions.ConnectionError as e:
        _log({"action": "ollama_error", "error_type": "connection", "url": url})
        return _make_fallback(f"No se pudo conectar a Ollama en {url}: {e}")
    except requests.exceptions.RequestException as e:
        _log({"action": "ollama_error", "error_type": "request", "url": url, "detail": str(e)})
        return _make_fallback(f"Error HTTP contra Ollama: {e}")

    if response.status_code != 200:
        _log({"action": "ollama_error", "error_type": "http_status", "status": response.status_code})
        return _make_fallback(
            f"Ollama devolvió HTTP {response.status_code}: {response.text.strip()}",
            raw_output=response.text.strip(),
        )

    try:
        data = response.json()
    except ValueError:
        _log({"action": "ollama_error", "error_type": "invalid_json"})
        return _make_fallback(
            "Ollama devolvió una respuesta no-JSON",
            raw_output=response.text.strip(),
        )

    raw_output = (data.get("response") or "").strip()
    normalized = validate_label(normalize_route(raw_output))

    return RouteResult(
        route=normalized, raw_output=raw_output,
        success=True, method="ollama", error=None,
    )


def route_intent(user_input: str, model: str = DEFAULT_MODEL) -> RouteResult:
    """Classify user intent. Fast regex first, Ollama fallback.

    Uses intent cache for repeated identical queries.
    Never raises — always returns a valid RouteResult.
    """
    # Input validation
    if not isinstance(user_input, str) or not user_input.strip():
        _log({"action": "classify", "route": "none", "reason": "empty_input"})
        return _make_fallback("Empty or invalid input")

    user_input = user_input.strip()

    # Check cache
    cache_key = f"{model}:{user_input}"
    cached = _cache_get(cache_key)
    if cached is not None:
        _log({
            "action": "classify", "route": cached.route,
            "cached": True, "input": user_input[:120],
        })
        return RouteResult(
            route=cached.route, raw_output=cached.raw_output,
            success=cached.success, method=cached.method,
            error=cached.error, cached=True,
        )

    t0 = time.time()

    # Fast path: regex
    regex_label = _classify_regex(user_input)
    if regex_label is not None:
        result = RouteResult(
            route=regex_label, raw_output=regex_label,
            success=True, method="regex",
        )
        classify_ms = int((time.time() - t0) * 1000)
        _log({
            "action": "classify", "input": user_input[:120],
            "route": result.route, "method": "regex",
            "success": True, "cached": False, "classify_ms": classify_ms,
        })
        _cache_put(cache_key, result)
        return result

    # Slow path: Ollama LLM
    try:
        result = run_ollama_router_http(prompt=user_input, model=model)
    except Exception as e:
        _log({
            "action": "classify_error", "error": str(e),
            "error_type": type(e).__name__, "input": user_input[:120],
        })
        return _make_fallback(f"Unexpected error in classification: {e}")

    classify_ms = int((time.time() - t0) * 1000)

    _log({
        "action": "classify", "input": user_input[:120],
        "route": result.route, "raw_label": result.raw_output,
        "method": "ollama", "success": result.success,
        "cached": False, "classify_ms": classify_ms,
        "fallback": result.route == "none" and not result.success,
    })

    if result.success:
        _cache_put(cache_key, result)

    return result


# ── Handlers (stub implementations for standalone use) ───────────────

def handle_weather(user_input: str) -> str:
    return f"[weather] Ejecutaría tool de clima para: {user_input}"


def handle_calendar(user_input: str) -> str:
    return f"[calendar] Ejecutaría tool de agenda/calendario para: {user_input}"


def handle_weather_calendar(user_input: str) -> str:
    return f"{handle_weather(user_input)}\n{handle_calendar(user_input)}"


def handle_lights(user_input: str) -> str:
    return f"[lights] Ejecutaría control de luces para: {user_input}"


def handle_network_scan(user_input: str) -> str:
    return f"[network_scan] Ejecutaría escaneo de red para: {user_input}"


def handle_infra(user_input: str) -> str:
    return f"[infra] Ejecutaría chequeo de infraestructura para: {user_input}"


def handle_tts(user_input: str) -> str:
    return f"[tts] Ejecutaría text-to-speech para: {user_input}"


def handle_arr_monitor(user_input: str) -> str:
    return f"[arr_monitor] Ejecutaría chequeo del arr-stack para: {user_input}"


def handle_none(user_input: str) -> str:
    return f"[none] No requiere tool. Responder como chat normal: {user_input}"


HANDLERS: Dict[str, Callable[[str], str]] = {
    "weather": handle_weather,
    "calendar": handle_calendar,
    "weather_calendar": handle_weather_calendar,
    "lights": handle_lights,
    "network_scan": handle_network_scan,
    "infra": handle_infra,
    "tts": handle_tts,
    "arr_monitor": handle_arr_monitor,
    "none": handle_none,
}


def dispatch(user_input: str, model: str = DEFAULT_MODEL) -> str:
    """Route user input to a handler. Never raises."""
    try:
        result = route_intent(user_input, model=model)
    except Exception as e:
        _log({"action": "dispatch_error", "error": str(e), "phase": "routing"})
        return handle_none(user_input)

    if not result.success:
        _log({"action": "dispatch_fallback", "error": result.error, "input": user_input[:120]})
        return handle_none(user_input)

    handler = HANDLERS.get(result.route, handle_none)
    try:
        return handler(user_input)
    except Exception as e:
        _log({"action": "handler_error", "route": result.route, "error": str(e)})
        return handle_none(user_input)


def dispatch_json(user_input: str, model: str = DEFAULT_MODEL) -> dict:
    """Route + dispatch, returning full telemetry dict. Never raises."""
    t0 = time.time()

    try:
        result = route_intent(user_input, model=model)
    except Exception as e:
        _log({"action": "dispatch_json_error", "error": str(e)})
        return {
            "ok": False, "route": "none", "raw_output": "", "error": str(e),
            "result": handle_none(user_input), "method": "fallback",
            "ollama_host": DEFAULT_OLLAMA_HOST, "ollama_model": model,
            "classify_ms": int((time.time() - t0) * 1000),
        }

    classify_ms = int((time.time() - t0) * 1000)
    route = validate_label(result.route)
    handler = HANDLERS.get(route, handle_none)

    try:
        action_result = handler(user_input)
    except Exception as e:
        _log({"action": "dispatch_json_handler_error", "route": route, "error": str(e)})
        route = "none"
        action_result = handle_none(user_input)

    return {
        "ok": result.success, "route": route,
        "raw_output": result.raw_output, "error": result.error,
        "result": action_result, "method": result.method,
        "ollama_host": DEFAULT_OLLAMA_HOST, "ollama_model": model,
        "classify_ms": classify_ms, "cached": result.cached,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print('Uso: python router.py "tu mensaje acá"')
        return 1
    user_input = " ".join(sys.argv[1:]).strip()
    output = dispatch_json(user_input)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
