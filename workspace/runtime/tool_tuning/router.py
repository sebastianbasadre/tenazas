#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
router.py
Router robusto para Tenazas/OpenClaw usando Ollama HTTP API + labels cerradas.

Requisitos:
    pip install requests

Variables opcionales:
    OLLAMA_HOST=http://192.168.2.6:11434
    OLLAMA_MODEL=tenazas-router-labels
    OLLAMA_TIMEOUT=30

Uso:
    python router.py "clima y agenda de hoy"
"""

from __future__ import annotations

import json
import logging
import os
import sys
import time
from dataclasses import dataclass, asdict
from functools import lru_cache
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Callable, Dict, Optional

import requests


DEFAULT_MODEL = os.getenv("OLLAMA_MODEL", "tenazas-router-labels")
DEFAULT_OLLAMA_HOST = os.getenv("OLLAMA_HOST", "http://192.168.2.6:11434").rstrip("/")
DEFAULT_TIMEOUT = int(os.getenv("OLLAMA_TIMEOUT", "30"))

VALID_ROUTES = {
    "weather",
    "calendar",
    "weather_calendar",
    "lights",
    "network_scan",
    "infra",
    "tts",
    "none",
}

# ── Structured JSON logging ──────────────────────────────────────────

LOG_DIR = Path("/home/tenazas/runtime/logs")
LOG_FILE = LOG_DIR / "tool_router.log"

logger = logging.getLogger("tenazas-router")
logger.setLevel(logging.INFO)

# JSON log to file (if inside container)
if LOG_DIR.exists():
    _fh = RotatingFileHandler(str(LOG_FILE), maxBytes=5_000_000, backupCount=3)
    _fh.setLevel(logging.INFO)
    logger.addHandler(_fh)

# Stderr for CLI / development
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
        # Evict oldest entry
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
    error: Optional[str] = None
    cached: bool = False

    def to_dict(self) -> dict:
        return asdict(self)


def _make_fallback(error: str, raw_output: str = "") -> RouteResult:
    """Create a safe fallback result with 'none' route."""
    return RouteResult(
        route="none",
        raw_output=raw_output,
        success=False,
        error=error,
    )


# ── Core functions ───────────────────────────────────────────────────

def normalize_route(text: str) -> str:
    """
    Limpia salida del modelo:
    - trim
    - primera línea no vacía
    - lower
    - fallback a none
    """
    if not text:
        return "none"

    for line in text.splitlines():
        line = line.strip().lower()
        if line:
            return line

    return "none"


def validate_label(label: str) -> str:
    """
    Validate that a label is in the known set of valid routes.
    Returns the label if valid, 'none' otherwise.
    Logs a warning on invalid labels.
    """
    if label in VALID_ROUTES:
        return label
    _log({
        "action": "label_validation_failed",
        "invalid_label": label,
        "fallback": "none",
    })
    return "none"


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
    """
    Ejecuta el router usando la HTTP API de Ollama.
    Usa /api/generate con stream=false.
    Never returns None — always a valid RouteResult.
    """
    url = f"{host}/api/generate"

    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
    }

    try:
        response = requests.post(url, json=payload, timeout=timeout)
    except requests.exceptions.Timeout:
        result = _make_fallback(f"Timeout llamando a Ollama en {url}")
        _log({"action": "ollama_error", "error_type": "timeout", "url": url})
        return result
    except requests.exceptions.ConnectionError as e:
        result = _make_fallback(f"No se pudo conectar a Ollama en {url}: {e}")
        _log({"action": "ollama_error", "error_type": "connection", "url": url})
        return result
    except requests.exceptions.RequestException as e:
        result = _make_fallback(f"Error HTTP contra Ollama: {e}")
        _log({"action": "ollama_error", "error_type": "request", "url": url, "detail": str(e)})
        return result

    if response.status_code != 200:
        result = _make_fallback(
            f"Ollama devolvió HTTP {response.status_code}: {response.text.strip()}",
            raw_output=response.text.strip(),
        )
        _log({"action": "ollama_error", "error_type": "http_status", "status": response.status_code})
        return result

    try:
        data = response.json()
    except ValueError:
        result = _make_fallback(
            "Ollama devolvió una respuesta no-JSON",
            raw_output=response.text.strip(),
        )
        _log({"action": "ollama_error", "error_type": "invalid_json"})
        return result

    raw_output = (data.get("response") or "").strip()
    normalized = normalize_route(raw_output)
    normalized = validate_label(normalized)

    return RouteResult(
        route=normalized,
        raw_output=raw_output,
        success=True,
        error=None,
    )


def route_intent(user_input: str, model: str = DEFAULT_MODEL) -> RouteResult:
    """
    Classify user input into a tool label via Ollama.
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
            "action": "classify",
            "route": cached.route,
            "cached": True,
            "input": user_input[:120],
        })
        cached_copy = RouteResult(
            route=cached.route,
            raw_output=cached.raw_output,
            success=cached.success,
            error=cached.error,
            cached=True,
        )
        return cached_copy

    # Classify via Ollama
    t0 = time.time()
    try:
        result = run_ollama_router_http(prompt=user_input, model=model)
    except Exception as e:
        # Catch-all: never propagate uncaught exceptions
        _log({
            "action": "classify_error",
            "error": str(e),
            "error_type": type(e).__name__,
            "input": user_input[:120],
        })
        return _make_fallback(f"Unexpected error in classification: {e}")

    classify_ms = int((time.time() - t0) * 1000)

    # Log classification decision
    _log({
        "action": "classify",
        "input": user_input[:120],
        "route": result.route,
        "raw_label": result.raw_output,
        "success": result.success,
        "cached": False,
        "classify_ms": classify_ms,
        "fallback": result.route == "none" and not result.success,
    })

    # Cache successful results
    if result.success:
        _cache_put(cache_key, result)

    return result


# ── Handlers (stub implementations for standalone use) ───────────────

def handle_weather(user_input: str) -> str:
    return f"[weather] Ejecutaría tool de clima para: {user_input}"


def handle_calendar(user_input: str) -> str:
    return f"[calendar] Ejecutaría tool de agenda/calendario para: {user_input}"


def handle_weather_calendar(user_input: str) -> str:
    weather_result = handle_weather(user_input)
    calendar_result = handle_calendar(user_input)
    return f"{weather_result}\n{calendar_result}"


def handle_lights(user_input: str) -> str:
    return f"[lights] Ejecutaría Home Assistant / control de luces para: {user_input}"


def handle_network_scan(user_input: str) -> str:
    return f"[network_scan] Ejecutaría escaneo de red para: {user_input}"


def handle_infra(user_input: str) -> str:
    return f"[infra] Ejecutaría chequeo de infraestructura / homelab / descargas para: {user_input}"


def handle_tts(user_input: str) -> str:
    return f"[tts] Ejecutaría text-to-speech para: {user_input}"


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
    "none": handle_none,
}


def dispatch(user_input: str, model: str = DEFAULT_MODEL) -> str:
    """
    Route user input to a handler and return the result.
    Never raises — always returns a string.
    """
    try:
        result = route_intent(user_input, model=model)
    except Exception as e:
        _log({"action": "dispatch_error", "error": str(e), "phase": "routing"})
        return handle_none(user_input)

    if not result.success:
        _log({
            "action": "dispatch_fallback",
            "error": result.error,
            "input": user_input[:120],
        })
        return handle_none(user_input)

    handler = HANDLERS.get(result.route, handle_none)

    try:
        return handler(user_input)
    except Exception as e:
        _log({
            "action": "handler_error",
            "route": result.route,
            "error": str(e),
            "error_type": type(e).__name__,
        })
        return handle_none(user_input)


def dispatch_json(user_input: str, model: str = DEFAULT_MODEL) -> dict:
    """
    Route + dispatch, returning full telemetry dict.
    Never raises — always returns a dict with 'ok', 'route', 'error' keys.
    """
    t0 = time.time()

    try:
        result = route_intent(user_input, model=model)
    except Exception as e:
        _log({"action": "dispatch_json_error", "error": str(e)})
        return {
            "ok": False,
            "route": "none",
            "raw_output": "",
            "error": str(e),
            "result": handle_none(user_input),
            "ollama_host": DEFAULT_OLLAMA_HOST,
            "ollama_model": model,
            "classify_ms": int((time.time() - t0) * 1000),
        }

    classify_ms = int((time.time() - t0) * 1000)
    route = validate_label(result.route)
    handler = HANDLERS.get(route, handle_none)

    try:
        action_result = handler(user_input)
    except Exception as e:
        _log({
            "action": "dispatch_json_handler_error",
            "route": route,
            "error": str(e),
        })
        route = "none"
        action_result = handle_none(user_input)

    return {
        "ok": result.success,
        "route": route,
        "raw_output": result.raw_output,
        "error": result.error,
        "result": action_result,
        "ollama_host": DEFAULT_OLLAMA_HOST,
        "ollama_model": model,
        "classify_ms": classify_ms,
        "cached": result.cached,
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Uso: python router.py \"tu mensaje acá\"")
        return 1

    user_input = " ".join(sys.argv[1:]).strip()
    output = dispatch_json(user_input)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
