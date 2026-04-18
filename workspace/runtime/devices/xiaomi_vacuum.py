"""
xiaomi_vacuum.py — Xiaomi Vacuum S20+ control via miio protocol.

IP: 192.168.2.99
Protocol: miio (python-miio, already installed)
Token: must be extracted once and stored in openclaw.json > devices.xiaomi_vacuum.token

Token extraction methods:
  1. miiocli cloud --username USER --password PASS --server cn
  2. Modified Xiaomi Home app + token extractor
  3. Rooted device: /data/misc/wifi/wpa_supplicant.conf
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

DEVICE_ID = "xiaomi_vacuum"
DEVICE_NAME = "Xiaomi Vacuum S20+"
DEFAULT_IP = "192.168.2.99"

ACTIONS = {
    "aspirar": "Inicia limpieza",
    "parar": "Detiene la limpieza",
    "volver": "Regresa a la base de carga",
    "velocidad": "Cambia velocidad del fan (silent/standard/medium/turbo)",
    "encontrar": "Hace sonar la aspiradora",
    "estado": "Bateria, estado, area limpiada",
}

# Fan speed presets
_FAN_SPEEDS = {
    "silencioso": 101, "silent": 101, "bajo": 101, "quiet": 101,
    "standard": 102, "estandar": 102, "normal": 102,
    "medio": 103, "medium": 103,
    "turbo": 104, "maximo": 104, "max": 104, "máximo": 104,
}


def _get_config(config: dict) -> tuple[str, str]:
    """Returns (ip, token)."""
    dev = config.get("devices", {}).get("xiaomi_vacuum", {})
    ip = dev.get("ip", DEFAULT_IP)
    token = dev.get("token", "")
    return ip, token


def _get_vacuum(ip: str, token: str):
    """Get a RoborockVacuum instance."""
    try:
        from miio import RoborockVacuum
    except ImportError:
        try:
            from miio import Vacuum as RoborockVacuum
        except ImportError:
            return None, "python-miio no instalado."

    if not token:
        return None, ("Token de la aspiradora no configurado. "
                       "Guardalo en openclaw.json > devices.xiaomi_vacuum.token")
    try:
        vac = RoborockVacuum(ip, token)
        return vac, None
    except Exception as e:
        return None, str(e)


def status(config: dict) -> dict:
    """Get vacuum status."""
    ip, token = _get_config(config)
    vac, err = _get_vacuum(ip, token)
    if err:
        return _error(err)

    try:
        s = vac.status()
        lines = [
            "Xiaomi Vacuum S20+:",
            f"  Estado: {s.state}",
            f"  Bateria: {s.battery}%",
            f"  Fan: {s.fanspeed}",
        ]
        if hasattr(s, "clean_area") and s.clean_area:
            lines.append(f"  Area limpiada: {s.clean_area / 1_000_000:.1f} m2")
        if hasattr(s, "clean_time") and s.clean_time:
            mins = s.clean_time.total_seconds() / 60 if hasattr(s.clean_time, 'total_seconds') else s.clean_time
            lines.append(f"  Tiempo: {int(mins)} min")
        if hasattr(s, "error_code") and s.error_code:
            lines.append(f"  Error: {s.error_code}")
        return _ok("\n".join(lines), destructive=False)
    except Exception as e:
        return _error(f"Error consultando aspiradora: {e}")


def execute(query: str, config: dict) -> dict:
    """Execute a vacuum action based on query text."""
    ip, token = _get_config(config)
    vac, err = _get_vacuum(ip, token)
    if err:
        return _error(err)

    q = query.lower()

    try:
        # Start cleaning
        if re.search(r"aspir|limpi|barr|start|comenzar|iniciar", q):
            vac.start()
            return _ok("Aspiradora iniciada!")

        # Stop
        if re.search(r"parar|stop|detener|frenar", q):
            vac.stop()
            return _ok("Aspiradora detenida.")

        # Pause
        if re.search(r"pausa|pause", q):
            vac.pause()
            return _ok("Aspiradora pausada.")

        # Return to dock
        if re.search(r"volver|dock|base|cargar|home|regresar", q):
            vac.home()
            return _ok("Aspiradora volviendo a la base.")

        # Fan speed
        fan_match = re.search(
            r"(?:velocidad|fan|speed|potencia)\s*(?:a|en)?\s*"
            r"(silencioso|silent|bajo|quiet|standard|estandar|normal"
            r"|medio|medium|turbo|maximo|máximo|max)", q)
        if fan_match:
            speed_name = fan_match.group(1).lower()
            speed_val = _FAN_SPEEDS.get(speed_name)
            if speed_val:
                vac.set_fan_speed(speed_val)
                return _ok(f"Velocidad: {speed_name}")

        # Find (beep)
        if re.search(r"encontrar|buscar|find|beep|sonar|donde\s+est", q):
            vac.find()
            return _ok("La aspiradora esta sonando!")

        # Estado (default)
        return status(config)

    except Exception as e:
        return _error(f"Error controlando aspiradora: {e}")


def _ok(output: str, raw: str = "", destructive: bool = True) -> dict:
    return {
        "skill": "skill:smart-home", "success": True,
        "output": output, "raw": raw,
        "destructive": destructive, "error": None,
    }


def _error(msg: str) -> dict:
    return {
        "skill": "skill:smart-home", "success": False,
        "output": msg, "raw": "", "destructive": False, "error": "vacuum_error",
    }
