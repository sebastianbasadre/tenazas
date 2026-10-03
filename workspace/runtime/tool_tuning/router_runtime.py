#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
router_runtime.py — v2 producción
Dispatcher real para Tenazas: router labels → handlers reales → respuesta.

Flujo:
    usuario → router.py (Ollama HTTP labels) → dispatch() → tool real → output

Cada handler:
  - Tiene timeout individual
  - Logging JSON estructurado
  - Fallback a None (→ LLM chat)
  - No rompe si una tool falla

Uso:
    from router_runtime import dispatch, dispatch_json

    # Simple
    output = dispatch("clima y agenda de hoy")

    # JSON (con telemetría)
    result = dispatch_json("prende las luces")

    # CLI
    python router_runtime.py "escanea la red"
"""

# ─────────────────────────────────────────────────────────────────
# router_runtime.py — Dispatcher de tools (ejecuta, no clasifica)
# Ver contrato: /home/tenazas/runtime/INTERFACE_CONTRACT.md
# VALID_ROUTES se importa de routes_contract.py — NO duplicar acá
# ─────────────────────────────────────────────────────────────────

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import time
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Optional

try:
    from runtime_config import HUE_BRIDGE_IP, LAN_SUBNET
except ImportError:
    from workspace.runtime.runtime_config import HUE_BRIDGE_IP, LAN_SUBNET

# Import canonical labels from contract
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from routes_contract import VALID_ROUTES


# ── Config ──────────────────────────────────────────────────────────

SKILL_TIMEOUT = int(os.getenv("SKILL_TIMEOUT", "30"))
WEATHER_TIMEOUT = 10
CALENDAR_TIMEOUT = 15
LIGHTS_TIMEOUT = 10
NMAP_TIMEOUT = 30
DOCKER_TIMEOUT = 10
TTS_TIMEOUT = 30

HUE_BRIDGE = f"http://{HUE_BRIDGE_IP}/api"
OPENCLAW_CONFIG = "/home/tenazas/.openclaw/openclaw.json"
GCAL_SCRIPT = "/home/tenazas/.openclaw/workspace/skills/calendar-agenda/gcal.py"
TTS_SCRIPT = "/home/tenazas/.openclaw/workspace/skills/tts/tts.sh"

# Freshness tracking file — written by network_scan, read by metrics_server
NETWORK_SCAN_TS_FILE = Path("/home/tenazas/runtime/network_scan_last_ts")

WEATHER_URL = (
    "https://api.open-meteo.com/v1/forecast"
    "?latitude=-34.61&longitude=-58.38"
    "&current_weather=true&timezone=America/Buenos_Aires"
)

# WMO weather codes → español
WMO_CODES = {
    0: "despejado", 1: "mayormente despejado",
    2: "parcialmente nublado", 3: "nublado",
    45: "niebla", 48: "niebla con escarcha",
    51: "llovizna leve", 53: "llovizna", 55: "llovizna intensa",
    61: "lluvia leve", 63: "lluvia", 65: "lluvia intensa",
    71: "nieve leve", 73: "nieve", 75: "nieve intensa",
    80: "chubascos leves", 81: "chubascos", 82: "chubascos intensos",
    95: "tormenta", 96: "tormenta con granizo",
}

# Hue light IDs y escenas
HUE_LIGHT_IDS = ["1", "3", "4", "6", "11", "12", "13", "15"]
HUE_SCENES = {
    "all-off": {"on": False},
    "all-on": {"on": True, "bri": 254},
    "cinema": {"on": True, "bri": 50, "ct": 400},
    "relax": {"on": True, "bri": 140, "ct": 370},
    "work": {"on": True, "bri": 254, "ct": 250},
}


# ── Logging ─────────────────────────────────────────────────────────

LOG_DIR = Path("/home/tenazas/runtime/logs")
LOG_FILE = LOG_DIR / "router_runtime.log"

logger = logging.getLogger("router-runtime")
logger.setLevel(logging.INFO)

# JSON log to file (if inside container)
if LOG_DIR.exists():
    fh = RotatingFileHandler(str(LOG_FILE), maxBytes=5_000_000, backupCount=3)
    fh.setLevel(logging.INFO)
    logger.addHandler(fh)

# Stderr for CLI usage
sh = logging.StreamHandler()
sh.setLevel(logging.WARNING)
logger.addHandler(sh)


def _log(entry: dict):
    """Write structured JSON log line."""
    entry["ts"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    logger.info(json.dumps(entry, ensure_ascii=False))


# ── Helpers ─────────────────────────────────────────────────────────

def _run_cmd(cmd: list[str], timeout: int = SKILL_TIMEOUT) -> tuple[bool, str]:
    """Run a subprocess safely. Returns (success, output)."""
    try:
        result = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout,
        )
        stdout = (result.stdout or "").strip()
        stderr = (result.stderr or "").strip()
        if result.returncode == 0:
            return True, stdout
        return False, stderr or stdout or f"exit code {result.returncode}"
    except subprocess.TimeoutExpired:
        return False, f"timeout ({timeout}s)"
    except FileNotFoundError:
        return False, f"command not found: {cmd[0]}"
    except Exception as e:
        return False, str(e)


def _get_hue_key() -> str:
    """Read Hue API key from env or openclaw.json."""
    key = os.environ.get("HUE_API_KEY", "")
    if key:
        return key
    try:
        with open(OPENCLAW_CONFIG) as f:
            config = json.load(f)
        return config.get("hue", {}).get("apiKey", "")
    except Exception:
        return ""


# ── Real Handlers ───────────────────────────────────────────────────

def run_weather(user_input: str) -> Optional[str]:
    """Open-Meteo → clima actual Buenos Aires."""
    ok, raw = _run_cmd(["curl", "-sf", WEATHER_URL], timeout=WEATHER_TIMEOUT)
    if not ok:
        return None
    try:
        data = json.loads(raw)
        cw = data.get("current_weather", {})
        temp = cw.get("temperature", "?")
        wind = cw.get("windspeed", "?")
        code = cw.get("weathercode", -1)
        desc = WMO_CODES.get(code, f"código {code}")
        return f"{temp}°C, {desc}, viento {wind} km/h"
    except (json.JSONDecodeError, KeyError):
        return raw or None


def run_calendar(user_input: str) -> Optional[str]:
    """Google Calendar → próximos 2 días."""
    ok, raw = _run_cmd(
        ["python3", GCAL_SCRIPT, "2"], timeout=CALENDAR_TIMEOUT,
    )
    if not ok:
        return None
    return raw or "Sin eventos próximos"


def run_lights(user_input: str) -> Optional[str]:
    """Philips Hue — detecta acción del query y ejecuta."""
    hue_key = _get_hue_key()
    if not hue_key:
        return "No se encontró la API key del Hue Bridge"

    base_url = f"{HUE_BRIDGE}/{hue_key}"
    query_lower = user_input.lower()

    # Detect action
    if re.search(r"apag[aá]|off|todo.off|all.off", query_lower):
        action = "all-off"
    elif re.search(r"prend[eé]|encend|on|todo.on|all.on", query_lower):
        action = "all-on"
    elif re.search(r"cine|cinema", query_lower):
        action = "cinema"
    elif re.search(r"relax", query_lower):
        action = "relax"
    elif re.search(r"trabaj|work", query_lower):
        action = "work"
    else:
        # Default: show status
        ok, raw = _run_cmd(
            ["curl", "-sf", f"{base_url}/lights"], timeout=LIGHTS_TIMEOUT,
        )
        if not ok:
            return None
        try:
            lights = json.loads(raw)
            lines = []
            for lid, ldata in sorted(lights.items()):
                name = ldata.get("name", f"Light {lid}")
                on = ldata.get("state", {}).get("on", False)
                lines.append(f"  {name}: {'encendida' if on else 'apagada'}")
            return "Estado de luces:\n" + "\n".join(lines)
        except (json.JSONDecodeError, AttributeError):
            return raw or None

    # Execute scene
    state = HUE_SCENES[action]
    errors = 0
    for lid in HUE_LIGHT_IDS:
        ok, _ = _run_cmd(
            ["curl", "-sf", "-X", "PUT",
             "-d", json.dumps(state),
             f"{base_url}/lights/{lid}/state"],
            timeout=5,
        )
        if not ok:
            errors += 1

    suffix = f" (con {errors} errores)" if errors else ""
    return f"Luces: {action} aplicado{suffix}"


def run_network_scan(user_input: str) -> Optional[str]:
    """nmap -sn LAN scan. Updates freshness timestamp on success."""
    ok, raw = _run_cmd(
        ["nmap", "-sn", LAN_SUBNET], timeout=NMAP_TIMEOUT,
    )
    if not ok:
        return None
    # Update freshness timestamp
    try:
        NETWORK_SCAN_TS_FILE.write_text(str(time.time()))
    except Exception:
        pass
    return raw


def run_infra(user_input: str) -> Optional[str]:
    """docker ps → estado de containers."""
    ok, raw = _run_cmd(
        ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
        timeout=DOCKER_TIMEOUT,
    )
    if not ok:
        return None
    return raw


def run_arr_monitor(user_input: str) -> Optional[str]:
    """docker ps filtrado al arr-stack."""
    arr_containers = [
        "gluetun", "qbittorrent", "sonarr", "radarr", "lidarr",
        "prowlarr", "seerr", "requestrr", "tautulli", "sabnzbd",
    ]
    cmd = ["docker", "ps"]
    for name in arr_containers:
        cmd.extend(["--filter", f"name={name}"])
    cmd.extend(["--format", "{{.Names}}\t{{.Status}}"])

    ok, raw = _run_cmd(cmd, timeout=DOCKER_TIMEOUT)
    if not ok:
        return None
    return raw


def run_tts(user_input: str) -> Optional[str]:
    """Piper TTS → Telegram voice note."""
    # Extract text to speak (remove trigger words)
    text = re.sub(
        r"^(dec[ií]|habl[aá]|le[eé]|mand[aá]\s+audio|text.to.speech|tts)\s*:?\s*",
        "", user_input, flags=re.IGNORECASE,
    ).strip()
    if not text:
        return "¿Qué querés que diga?"

    # Sanitize for shell
    safe_text = text.replace("'", "'\\''")

    ok, raw = _run_cmd(
        ["bash", TTS_SCRIPT, safe_text], timeout=TTS_TIMEOUT,
    )
    if not ok:
        return f"Error TTS: {raw[:100]}"
    return f'Audio enviado: "{text[:60]}"'


def handle_none(user_input: str) -> None:
    """No tool needed — returns None so caller falls back to LLM chat."""
    return None


# ── Route → Handler map ────────────────────────────────────────────

HANDLERS = {
    "weather": run_weather,
    "calendar": run_calendar,
    "weather_calendar": lambda q: _combine(run_weather(q), run_calendar(q)),
    "lights": run_lights,
    "network_scan": run_network_scan,
    "infra": run_infra,
    "arr_monitor": run_arr_monitor,
    "tts": run_tts,
    "none": handle_none,
}


def _combine(*results) -> Optional[str]:
    """Combine multi-tool results. Skips None (failed) results."""
    parts = [r for r in results if r is not None]
    return "\n".join(parts) if parts else None


# ── Public API ──────────────────────────────────────────────────────

def dispatch(user_input: str, model: str | None = None) -> Optional[str]:
    """
    Route + execute. Returns tool output or None (→ fallback to LLM).
    """
    from router import route_intent

    t0 = time.time()

    # Classify
    kwargs = {"model": model} if model else {}
    result = route_intent(user_input, **kwargs)
    classify_ms = int((time.time() - t0) * 1000)

    route = result.route
    handler = HANDLERS.get(route, handle_none)

    # Execute handler
    t1 = time.time()
    try:
        output = handler(user_input)
        success = output is not None
        error = None
    except Exception as e:
        output = None
        success = False
        error = str(e)
        logger.exception("Handler '%s' crashed: %s", route, e)

    handler_ms = int((time.time() - t1) * 1000)
    total_ms = int((time.time() - t0) * 1000)

    # Structured log
    _log({
        "action": "dispatch",
        "input": user_input[:120],
        "route": route,
        "raw_label": result.raw_output,
        "router_ok": result.success,
        "handler_ok": success,
        "error": error or result.error,
        "classify_ms": classify_ms,
        "handler_ms": handler_ms,
        "total_ms": total_ms,
        "fallback_to_chat": not success,
    })

    return output


def dispatch_json(user_input: str, model: str | None = None) -> dict:
    """
    Same as dispatch() but returns full telemetry dict.
    Useful for debugging and integration testing.
    """
    from router import route_intent, DEFAULT_OLLAMA_HOST, DEFAULT_MODEL

    t0 = time.time()

    effective_model = model or DEFAULT_MODEL
    result = route_intent(user_input, model=effective_model)
    classify_ms = int((time.time() - t0) * 1000)

    route = result.route
    handler = HANDLERS.get(route, handle_none)

    t1 = time.time()
    try:
        output = handler(user_input)
        success = output is not None
        error = None
    except Exception as e:
        output = None
        success = False
        error = str(e)

    handler_ms = int((time.time() - t1) * 1000)
    total_ms = int((time.time() - t0) * 1000)

    _log({
        "action": "dispatch_json",
        "input": user_input[:120],
        "route": route,
        "handler_ok": success,
        "total_ms": total_ms,
    })

    return {
        "ok": result.success and success,
        "route": route,
        "raw_label": result.raw_output,
        "router_ok": result.success,
        "router_error": result.error,
        "handler_ok": success,
        "handler_error": error,
        "output": output,
        "classify_ms": classify_ms,
        "handler_ms": handler_ms,
        "total_ms": total_ms,
        "ollama_host": DEFAULT_OLLAMA_HOST,
        "ollama_model": effective_model,
        "fallback_to_chat": not success,
    }


# ── CLI ─────────────────────────────────────────────────────────────

def main() -> int:
    if len(sys.argv) < 2:
        print('Uso: python router_runtime.py "tu mensaje acá"')
        return 1

    user_input = " ".join(sys.argv[1:]).strip()
    result = dispatch_json(user_input)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
