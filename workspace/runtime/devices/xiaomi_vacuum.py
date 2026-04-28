"""
xiaomi_vacuum.py — Xiaomi Robot Vacuum X20+ (c102gl) via MIoT protocol.

IP: 192.168.2.99
Protocol: MIoT over miio UDP (python-miio Device.send)
Token: stored in openclaw.json > devices.xiaomi_vacuum.token

MIoT spec for xiaomi.vacuum.c102gl (Dreame-based):
  SIID 2: Vacuum (sweep)
    AIID 1: Start Sweep
    AIID 2: Stop Sweep
    PIID 1: Status (int) — 1=sweeping, 2=idle, 3=paused, 5=charging, 6=goto_charge, 7=error
    PIID 2: Fault (int)
    PIID 3: Mode (int) — 0=silent, 1=standard, 2=strong, 3=turbo
  SIID 3: Battery
    PIID 1: Battery Level (int, 0-100)
    PIID 2: Charging State (int) — 1=charging, 2=not_charging, 5=goto_charging
  SIID 4: (varies)
  SIID 6: Identify (find)
    AIID 1: Identify
  SIID 18: Home (return to dock)
    AIID 1: Start Charge
"""

from __future__ import annotations

import json
import logging
import re

try:
    from runtime_config import XIAOMI_VACUUM_IP
except ImportError:
    from workspace.runtime.runtime_config import XIAOMI_VACUUM_IP

logger = logging.getLogger(__name__)

DEVICE_ID = "xiaomi_vacuum"
DEVICE_NAME = "Xiaomi Vacuum X20+"
DEFAULT_IP = XIAOMI_VACUUM_IP

ACTIONS = {
    "aspirar": "Inicia limpieza",
    "parar": "Detiene la limpieza",
    "pausar": "Pausa la limpieza",
    "volver": "Regresa a la base de carga",
    "encontrar": "Hace sonar la aspiradora",
    "estado": "Bateria, estado",
}

# Status codes
_STATUS_MAP = {
    1: "aspirando", 2: "idle", 3: "pausado",
    5: "cargando", 6: "volviendo a base", 7: "error",
}

_MODE_MAP = {
    0: "silencioso", 1: "standard", 2: "fuerte", 3: "turbo",
}


def _get_config(config: dict) -> tuple[str, str]:
    from devices import load_tenazas_config
    dev = load_tenazas_config().get("devices", {}).get("xiaomi_vacuum", {})
    ip = dev.get("ip", DEFAULT_IP)
    token = dev.get("token", "")
    return ip, token


def _get_device(ip: str, token: str):
    from miio import Device
    return Device(ip, token)


def _miot_get(dev, siid: int, piid: int):
    """Get a single MIoT property."""
    r = dev.send("get_properties", [{"did": f"p{siid}{piid}", "siid": siid, "piid": piid}])
    if r and isinstance(r, list) and r[0].get("code") == 0:
        return r[0].get("value")
    return None


def _miot_action(dev, siid: int, aiid: int, params: list | None = None) -> bool:
    """Execute a MIoT action. Returns True on success."""
    payload = {"did": f"call-{siid}-{aiid}", "siid": siid, "aiid": aiid,
               "in": params or []}
    r = dev.send("action", payload)
    if isinstance(r, dict):
        return r.get("code", -1) == 0
    return False


def status(config: dict) -> dict:
    ip, token = _get_config(config)
    if not token:
        return _error("Token no configurado. Guardalo en openclaw.json > devices.xiaomi_vacuum.token")

    try:
        dev = _get_device(ip, token)

        battery = _miot_get(dev, 3, 1)
        state_code = _miot_get(dev, 2, 1)
        fault = _miot_get(dev, 2, 2)
        mode = _miot_get(dev, 2, 3)

        state = _STATUS_MAP.get(state_code, f"desconocido ({state_code})")
        mode_name = _MODE_MAP.get(mode, f"desconocido ({mode})")

        lines = [
            "Xiaomi Vacuum X20+:",
            f"  Estado: {state}",
            f"  Bateria: {battery}%" if battery is not None else "  Bateria: ?",
            f"  Modo: {mode_name}",
        ]
        if fault and fault != 0:
            lines.append(f"  Error: codigo {fault}")

        return _ok("\n".join(lines), destructive=False)

    except ImportError:
        return _error("python-miio no instalado.")
    except Exception as e:
        return _error(f"Error consultando aspiradora: {e}")


def execute(query: str, config: dict) -> dict:
    ip, token = _get_config(config)
    if not token:
        return _error("Token no configurado. Guardalo en openclaw.json > devices.xiaomi_vacuum.token")

    q = query.lower()

    try:
        dev = _get_device(ip, token)

        # Order matters: stop/pause/dock/find BEFORE start,
        # because "parar la aspiradora" contains "aspir"

        # Stop
        if re.search(r"parar|stop|detener|frenar", q):
            ok = _miot_action(dev, 2, 2)
            return _ok("Aspiradora detenida.") if ok else _error("No se pudo detener")

        # Pause
        if re.search(r"pausa|pause", q):
            ok = _miot_action(dev, 2, 2)
            return _ok("Aspiradora pausada.") if ok else _error("No se pudo pausar")

        # Return to dock
        if re.search(r"volver|dock|base|cargar|home|regresar", q):
            ok = _miot_action(dev, 18, 1)
            return _ok("Aspiradora volviendo a la base.") if ok else _error("No se pudo enviar a base")

        # Find (beep)
        if re.search(r"encontrar|buscar|find|beep|sonar|donde\s+est", q):
            ok = _miot_action(dev, 6, 1)
            return _ok("La aspiradora esta sonando!") if ok else _error("No se pudo hacer sonar")

        # Estado
        if re.search(r"estado|status|bater|c[oó]mo\s+(est[aá]|anda)", q):
            return status(config)

        # Start cleaning (last — "aspiradora" contains "aspir")
        if re.search(r"aspir|limpi|barr|start|comenzar|iniciar|pasa|activ|prend|encend", q):
            ok = _miot_action(dev, 2, 1)
            return _ok("Aspiradora iniciada!") if ok else _error("No se pudo iniciar")

        # Default: status
        return status(config)

    except ImportError:
        return _error("python-miio no instalado.")
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
