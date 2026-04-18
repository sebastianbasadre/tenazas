"""
hue_lights.py — Philips Hue light control via Bridge REST API.

Bridge: 192.168.2.11
Lights: 8 fixtures (IDs: 1, 3, 4, 6, 11, 12, 13, 15)
Scenes: all-on, all-off, cinema, relax, work
"""

from __future__ import annotations

import json
import os
import re
import subprocess

DEVICE_ID = "hue_lights"
DEVICE_NAME = "Luces Hue"
ACTIONS = {
    "encender": "Enciende todas las luces",
    "apagar": "Apaga todas las luces",
    "cinema": "Modo cine (tenue, calido)",
    "relax": "Modo relax (intermedio, calido)",
    "work": "Modo trabajo (brillo maximo, neutro)",
    "estado": "Estado de cada luz",
}

BRIDGE_IP = "192.168.2.11"
LIGHT_IDS = ["1", "3", "4", "6", "11", "12", "13", "15"]

SCENES = {
    "all-on": {"on": True, "bri": 254},
    "all-off": {"on": False},
    "cinema": {"on": True, "bri": 50, "ct": 400},
    "relax": {"on": True, "bri": 140, "ct": 370},
    "work": {"on": True, "bri": 254, "ct": 250},
}

_ACTION_MSGS = {
    "all-on": "Luces encendidas!",
    "all-off": "Luces apagadas!",
    "cinema": "Luces en modo cine!",
    "relax": "Modo relax activado!",
    "work": "Modo trabajo activado!",
}


def _get_hue_key(config: dict) -> str:
    key = os.environ.get("HUE_API_KEY", "")
    if not key:
        key = config.get("hue", {}).get("apiKey", "")
    return key


def _detect_action(query: str) -> str | None:
    """Detect light action from query text."""
    q = query.lower()
    _cinema_re = (
        r"cine|cinema|pel[ií]cula|movie|netflix|hbo|disney|serie"
        r"|modo\s*cine|modo\s*pel[ií]cula"
    )
    if re.search(_cinema_re, q):
        return "cinema"
    if re.search(r"apag[aá]|off|todo.off|all.off", q):
        return "all-off"
    if re.search(r"prend[eé]|encend|on|todo.on|all.on", q):
        return "all-on"
    if re.search(r"relax", q):
        return "relax"
    if re.search(r"trabaj|work", q):
        return "work"
    return None


def status(config: dict) -> dict:
    """Get status of all lights."""
    hue_key = _get_hue_key(config)
    if not hue_key:
        return _error("No se encontro la API key del Hue Bridge")

    base_url = f"http://{BRIDGE_IP}/api/{hue_key}"
    try:
        result = subprocess.run(
            ["curl", "-sf", f"{base_url}/lights"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            lights = json.loads(result.stdout)
            lines = []
            for lid, ldata in sorted(lights.items()):
                name = ldata.get("name", f"Light {lid}")
                on = ldata.get("state", {}).get("on", False)
                lines.append(f"  {name}: {'encendida' if on else 'apagada'}")
            return _ok("Estado de luces:\n" + "\n".join(lines),
                       raw=result.stdout, destructive=False)
    except Exception as e:
        return _error(f"Error consultando luces: {e}")
    return _error("No se pudo consultar el estado de las luces")


def execute(query: str, config: dict) -> dict:
    """Execute a light action based on query text."""
    hue_key = _get_hue_key(config)
    if not hue_key:
        return _error("No se encontro la API key del Hue Bridge")

    action = _detect_action(query)
    if action is None:
        return status(config)

    base_url = f"http://{BRIDGE_IP}/api/{hue_key}"
    state = SCENES[action]
    errors = []

    for lid in LIGHT_IDS:
        try:
            subprocess.run(
                ["curl", "-sf", "-X", "PUT",
                 "-d", json.dumps(state),
                 f"{base_url}/lights/{lid}/state"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception as e:
            errors.append(f"Light {lid}: {e}")

    if errors:
        return _ok(f"Luces: {action} (con {len(errors)} errores)",
                    raw="\n".join(errors))

    return _ok(_ACTION_MSGS.get(action, f"Luces: {action}"))


def _ok(output: str, raw: str = "", destructive: bool = True) -> dict:
    return {
        "skill": "skill:smart-home", "success": True,
        "output": output, "raw": raw,
        "destructive": destructive, "error": None,
    }


def _error(msg: str) -> dict:
    return {
        "skill": "skill:smart-home", "success": False,
        "output": msg, "raw": "", "destructive": False, "error": "hue_error",
    }
