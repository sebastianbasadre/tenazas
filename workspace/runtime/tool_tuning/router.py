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
from dataclasses import dataclass
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

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
)
logger = logging.getLogger("tenazas-router")


@dataclass
class RouteResult:
    route: str
    raw_output: str
    success: bool
    error: Optional[str] = None


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
        return RouteResult(
            route="none",
            raw_output="",
            success=False,
            error=f"Timeout llamando a Ollama en {url}",
        )
    except requests.exceptions.ConnectionError as e:
        return RouteResult(
            route="none",
            raw_output="",
            success=False,
            error=f"No se pudo conectar a Ollama en {url}: {e}",
        )
    except requests.exceptions.RequestException as e:
        return RouteResult(
            route="none",
            raw_output="",
            success=False,
            error=f"Error HTTP contra Ollama: {e}",
        )

    if response.status_code != 200:
        return RouteResult(
            route="none",
            raw_output=response.text.strip(),
            success=False,
            error=f"Ollama devolvió HTTP {response.status_code}: {response.text.strip()}",
        )

    try:
        data = response.json()
    except ValueError:
        return RouteResult(
            route="none",
            raw_output=response.text.strip(),
            success=False,
            error="Ollama devolvió una respuesta no-JSON",
        )

    raw_output = (data.get("response") or "").strip()
    normalized = normalize_route(raw_output)

    if normalized not in VALID_ROUTES:
        logger.warning("Salida inválida del router: %r -> fallback none", raw_output)
        normalized = "none"

    return RouteResult(
        route=normalized,
        raw_output=raw_output,
        success=True,
        error=None,
    )


def route_intent(user_input: str, model: str = DEFAULT_MODEL) -> RouteResult:
    return run_ollama_router_http(prompt=user_input, model=model)


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
    result = route_intent(user_input, model=model)

    if not result.success:
        logger.error("Fallo router: %s", result.error)
        return handle_none(user_input)

    handler = HANDLERS.get(result.route, handle_none)
    logger.info("Route=%s | raw=%r", result.route, result.raw_output)

    try:
        return handler(user_input)
    except Exception as e:
        logger.exception("Error en handler '%s': %s", result.route, e)
        return handle_none(user_input)


def dispatch_json(user_input: str, model: str = DEFAULT_MODEL) -> dict:
    result = route_intent(user_input, model=model)

    route = result.route if result.route in VALID_ROUTES else "none"
    handler = HANDLERS.get(route, handle_none)

    try:
        action_result = handler(user_input)
    except Exception as e:
        logger.exception("Error en dispatch_json handler '%s': %s", route, e)
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
