"""
hue_sync_box.py — Hue Sync Box control via HTTPS API.

IP: 192.168.2.36
Protocol: HTTPS REST API with self-signed cert + Bearer token
Token: stored in openclaw.json > hue_sync_box.token (via hue_sync_pair.py)
"""

from __future__ import annotations

import json
import logging
import re
import ssl
from urllib.request import Request, urlopen

try:
    from runtime_config import HUE_SYNC_BOX_IP
except ImportError:
    from workspace.runtime.runtime_config import HUE_SYNC_BOX_IP

logger = logging.getLogger(__name__)

DEVICE_ID = "hue_sync_box"
DEVICE_NAME = "Hue Sync Box"
DEFAULT_IP = HUE_SYNC_BOX_IP
TIMEOUT = 5

ACTIONS = {
    "sync on": "Activa sincronizacion de luces",
    "sync off": "Desactiva sincronizacion",
    "modo video": "Sincronizacion para peliculas/series",
    "modo music": "Sincronizacion para musica",
    "modo game": "Sincronizacion para juegos",
    "intensidad": "Ajusta intensidad (subtle/moderate/high/intense)",
    "input": "Cambia entrada HDMI (1-4)",
    "estado": "Estado actual del Sync Box",
}

# Intensity level names (ES → API value)
_INTENSITY_MAP = {
    "sutil": "subtle", "baja": "subtle", "subtle": "subtle",
    "moderada": "moderate", "media": "moderate", "moderate": "moderate",
    "alta": "high", "high": "high",
    "intensa": "intense", "maxima": "intense", "intense": "intense",
    "máxima": "intense", "max": "intense",
}


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _get_config(config: dict) -> tuple[str, str]:
    """Returns (ip, token) from tenazas_config.json."""
    from devices import load_tenazas_config
    tcfg = load_tenazas_config()
    sb = tcfg.get("hue_sync_box", {})
    ip = sb.get("ip", DEFAULT_IP)
    token = sb.get("token", "")
    return ip, token


def _api_get(ip: str, token: str, path: str = "/api/v1") -> dict:
    """GET request to Sync Box API."""
    req = Request(
        f"https://{ip}{path}",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urlopen(req, timeout=TIMEOUT, context=_ssl_ctx()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _api_put(ip: str, token: str, path: str, data: dict) -> dict:
    """PUT request to Sync Box API. Returns parsed JSON or empty dict."""
    body = json.dumps(data).encode("utf-8")
    req = Request(
        f"https://{ip}{path}",
        data=body, method="PUT",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urlopen(req, timeout=TIMEOUT, context=_ssl_ctx()) as resp:
        raw = resp.read().decode("utf-8").strip()
        if raw:
            return json.loads(raw)
        return {}


def status(config: dict) -> dict:
    """Get Sync Box status."""
    ip, token = _get_config(config)
    if not token:
        return _error("No se encontro el token del Sync Box. "
                       "Ejecuta hue_sync_pair.py para parearlo.")
    try:
        data = _api_get(ip, token, "/api/v1/execution")
        sync_active = data.get("syncActive", False)
        mode = data.get("mode", "?")
        hdmi_source = data.get("hdmiSource", "?")
        # Get intensity for current mode
        mode_data = data.get(mode, {})
        intensity = mode_data.get("intensity", "?")

        lines = [
            f"Hue Sync Box:",
            f"  Sync: {'activo' if sync_active else 'inactivo'}",
            f"  Modo: {mode}",
            f"  Intensidad: {intensity}",
            f"  HDMI: {hdmi_source}",
        ]
        return _ok("\n".join(lines), destructive=False)

    except Exception as e:
        return _error(f"Error consultando Sync Box: {e}")


def execute(query: str, config: dict) -> dict:
    """Execute a Sync Box action based on query text."""
    ip, token = _get_config(config)
    if not token:
        return _error("No se encontro el token del Sync Box. "
                       "Ejecuta hue_sync_pair.py para parearlo.")

    q = query.lower()

    try:
        # Sync off
        if re.search(r"apag|off|desactiv|parar\s*sync", q):
            _api_put(ip, token, "/api/v1/execution", {"syncActive": False})
            return _ok("Sync Box desactivado.")

        # Mode detection (must be before sync on)
        mode = None
        if re.search(r"video|peli|movie|serie|cine|cinema", q):
            mode = "video"
        elif re.search(r"music|m[uú]sica", q):
            mode = "music"
        elif re.search(r"game|juego|gaming", q):
            mode = "game"

        # Sync on (with optional mode)
        # Guard: "sync box" alone shouldn't trigger sync-on, only explicit
        # action verbs like "prende/activa sync" or mode selection
        if re.search(r"(prend|encend|activ)\w*\s*(el\s+)?sync", q) \
                or re.search(r"sync\s*(on|activ)", q) or mode:
            payload: dict = {"syncActive": True}
            if mode:
                payload["mode"] = mode
            _api_put(ip, token, "/api/v1/execution", payload)
            mode_str = f" modo {mode}" if mode else ""
            return _ok(f"Sync Box activado{mode_str}.")

        # Intensity
        int_match = re.search(
            r"intensidad\s*(?:a|en)?\s*"
            r"(sutil|baja|subtle|moderada|media|moderate|alta|high"
            r"|intensa|maxima|máxima|intense|max)", q)
        if int_match:
            intensity = _INTENSITY_MAP.get(int_match.group(1), "high")
            # Get current mode to set intensity on it
            current = _api_get(ip, token, "/api/v1/execution")
            current_mode = current.get("mode", "video")
            _api_put(ip, token, "/api/v1/execution",
                     {current_mode: {"intensity": intensity}})
            return _ok(f"Intensidad: {intensity}.")

        # HDMI input
        hdmi_match = re.search(r"(?:hdmi|input|entrada)\s*(\d)", q)
        if hdmi_match:
            n = int(hdmi_match.group(1))
            if 1 <= n <= 4:
                _api_put(ip, token, "/api/v1/execution",
                         {"hdmiSource": f"input{n}"})
                return _ok(f"Sync Box HDMI {n}.")
            return _error(f"HDMI {n} invalido (1-4).")

        # Estado (default)
        return status(config)

    except Exception as e:
        return _error(f"Error controlando Sync Box: {e}")


def _ok(output: str, raw: str = "", destructive: bool = True) -> dict:
    return {
        "skill": "skill:smart-home", "success": True,
        "output": output, "raw": raw,
        "destructive": destructive, "error": None,
    }


def _error(msg: str) -> dict:
    return {
        "skill": "skill:smart-home", "success": False,
        "output": msg, "raw": "", "destructive": False, "error": "sync_box_error",
    }
