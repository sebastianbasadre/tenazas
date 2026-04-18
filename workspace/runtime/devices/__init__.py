"""
devices — Modular IoT device controllers for Tenazas.

Each module exports:
    DEVICE_ID:  str            — unique identifier (e.g. "hue_lights")
    DEVICE_NAME: str           — human name (e.g. "Luces Hue")
    ACTIONS:    dict[str, str] — action_name -> description
    execute(action, params, config) -> dict   — run an action
    status(config) -> dict                    — current state

The dispatcher detects which device the user is targeting from the query
and delegates to the right module.
"""

from __future__ import annotations

import importlib
import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")
TENAZAS_CONFIG_PATH = Path("/home/tenazas/.openclaw/tenazas_config.json")

# ── Device detection patterns ───────────────────────────────────────
# Order matters: more specific patterns first.

_DEVICE_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("hue_sync_box", re.compile(
        r"sync\s*box|hue\s*sync|sincroniz\w+\s+lu[cz]", re.I)),
    ("marantz_avr", re.compile(
        r"marantz|receiver|amplificador|avr|home\s*theater"
        r"|sonido\s*(del?\s*)?(living|cine|tele)", re.I)),
    ("samsung_tv", re.compile(
        r"\btv\b|tele(visor|visi[oó]n)?|samsung\s*(m8|q70|monitor)"
        r"|pantalla|monitor\s*(grande|samsung)", re.I)),
    ("shield_tv", re.compile(
        r"shield|nvidia\s*shield|android\s*tv", re.I)),
    ("xiaomi_vacuum", re.compile(
        r"aspiradora|vacuum|robot\s*(de\s*)?limpieza|barrer|roomba"
        r"|limpiar\s*(el\s+)?piso|pasar\s*(la\s+)?aspiradora", re.I)),
    ("google_cast", re.compile(
        r"parlante|speaker|nest\s*(mini)?|google\s*home|castear|chromecast", re.I)),
    ("ezviz_cameras", re.compile(
        r"c[aá]mara|camera|ezviz|nvr|dahua|hikvision|vigilancia", re.I)),
    ("nest_doorbell", re.compile(
        r"timbre|doorbell|nest\s*doorbell|puerta\s*(de\s*)?entrada", re.I)),
    # Hue lights is the default / fallback for smart-home
    ("hue_lights", re.compile(
        r"lu[cz]|light|l[aá]mpara|brillo|hue(?!\s*sync)|alumbrad", re.I)),
]

# Devices that are NOT yet supported (status-only or stub)
_UNSUPPORTED_DEVICES = {"ezviz_cameras", "nest_doorbell"}


def _load_config() -> dict:
    """Load openclaw.json config."""
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def load_tenazas_config() -> dict:
    """Load tenazas_config.json — device configs separate from gateway config.

    This file is NOT read/validated by the OpenClaw gateway, so custom keys
    (hue_sync_box, devices, etc.) won't cause crash loops.
    """
    try:
        return json.loads(TENAZAS_CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_tenazas_config(data: dict) -> None:
    """Save tenazas_config.json atomically."""
    tmp = TENAZAS_CONFIG_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(TENAZAS_CONFIG_PATH)


def detect_device(query: str) -> str | None:
    """Detect which device the user is targeting from the query text.

    Returns device_id or None if no device detected.
    """
    for device_id, pattern in _DEVICE_PATTERNS:
        if pattern.search(query):
            return device_id
    return None


def _get_module(device_id: str):
    """Dynamically import a device module."""
    try:
        return importlib.import_module(f"devices.{device_id}")
    except ImportError as e:
        logger.warning("Device module %s not found: %s", device_id, e)
        return None


def dispatch(query: str, skill_cat: str = "skill:smart-home") -> dict:
    """Main dispatcher: detect device from query and execute.

    This replaces the monolithic _handle_smart_home in orchestrator.py.
    """
    config = _load_config()
    device_id = detect_device(query)

    # If no device detected, check if query has an action that implies lights
    if device_id is None:
        query_lower = query.lower()
        # Action words without device → assume lights
        if re.search(r"prend[eé]|encend|apag|cinema|cine|relax|work|brillo", query_lower):
            device_id = "hue_lights"
        else:
            # No device, no action → list all device statuses
            return _list_devices(config, skill_cat)

    # Check if device is unsupported stub
    if device_id in _UNSUPPORTED_DEVICES:
        mod = _get_module(device_id)
        if mod and hasattr(mod, "status"):
            try:
                return mod.status(config)
            except Exception as e:
                return _error(skill_cat, f"Error consultando {device_id}: {e}")
        return _error(skill_cat,
                      f"'{device_id}' solo soporta consulta de estado (ping).")

    # Check if device module is enabled in config
    # Device config lives in tenazas_config.json (separate from gateway config)
    tenazas = load_tenazas_config()
    dev_cfg = tenazas.get("devices", {}).get(device_id, {})
    if dev_cfg.get("enabled") is False:
        mod = _get_module(device_id)
        name = getattr(mod, "DEVICE_NAME", device_id) if mod else device_id
        return _error(skill_cat,
                      f"{name} no esta habilitado. "
                      "Configuralo en openclaw.json > devices.")

    # Load and execute
    mod = _get_module(device_id)
    if mod is None:
        return _error(skill_cat,
                      f"Modulo '{device_id}' no encontrado.")

    try:
        return mod.execute(query, config)
    except Exception as e:
        logger.exception("Device %s execute failed", device_id)
        return _error(skill_cat, f"Error ejecutando {device_id}: {e}")


def dispatch_composite(action: str, query: str,
                       skill_cat: str = "skill:smart-home") -> dict:
    """Execute a composite action across multiple devices.

    Used for orchestrated scenes like "cinema mode" that span
    lights + AVR + sync box.
    """
    config = _load_config()

    if action == "cinema":
        return _cinema_mode(config, skill_cat)
    elif action == "cinema-off":
        return _cinema_off(config, skill_cat)

    return _error(skill_cat, f"Accion compuesta '{action}' no reconocida.")


def _cinema_mode(config: dict, skill_cat: str) -> dict:
    """Composite cinema mode: lights dim + Marantz ON + Sync Box ON."""
    results = []
    errors = []

    # 1. Hue lights → cinema scene
    mod = _get_module("hue_lights")
    if mod:
        try:
            r = mod.execute("modo cine", config)
            results.append(f"Luces: {r.get('output', 'ok')}")
            if not r.get("success"):
                errors.append("luces")
        except Exception as e:
            errors.append(f"luces: {e}")

    # 2. Marantz → power on + input
    mod = _get_module("marantz_avr")
    if mod:
        try:
            r = mod.execute("encender marantz", config)
            results.append(f"Marantz: {r.get('output', 'ok')}")
            if not r.get("success"):
                errors.append("marantz")
        except Exception as e:
            errors.append(f"marantz: {e}")

    # 3. Hue Sync Box → sync on, mode video, intensity high
    mod = _get_module("hue_sync_box")
    if mod:
        try:
            r = mod.execute("sync box modo video", config)
            results.append(f"Sync Box: {r.get('output', 'ok')}")
            if not r.get("success"):
                errors.append("sync box")
        except Exception as e:
            errors.append(f"sync box: {e}")

    output = "Modo cine activado!\n" + "\n".join(results)
    if errors:
        output += f"\n(errores: {', '.join(errors)})"

    return {
        "skill": skill_cat, "success": len(errors) == 0,
        "output": output, "raw": json.dumps(results),
        "destructive": True, "error": None if not errors else "partial",
    }


def _cinema_off(config: dict, skill_cat: str) -> dict:
    """Turn off cinema mode: lights on + Sync Box off."""
    results = []

    mod = _get_module("hue_lights")
    if mod:
        try:
            r = mod.execute("encender luces", config)
            results.append(f"Luces: {r.get('output', 'ok')}")
        except Exception:
            pass

    mod = _get_module("hue_sync_box")
    if mod:
        try:
            r = mod.execute("apagar sync box", config)
            results.append(f"Sync Box: {r.get('output', 'ok')}")
        except Exception:
            pass

    return {
        "skill": skill_cat, "success": True,
        "output": "Modo cine desactivado.\n" + "\n".join(results),
        "raw": "", "destructive": True, "error": None,
    }


def _list_devices(config: dict, skill_cat: str) -> dict:
    """List available devices and their status."""
    lines = ["Dispositivos disponibles:"]
    for device_id, _ in _DEVICE_PATTERNS:
        mod = _get_module(device_id)
        if mod:
            name = getattr(mod, "DEVICE_NAME", device_id)
            actions = getattr(mod, "ACTIONS", {})
            status = "activo" if device_id not in _UNSUPPORTED_DEVICES else "solo estado"
            lines.append(f"  {name} ({status}): {', '.join(actions.keys()) or 'status'}")
    return {
        "skill": skill_cat, "success": True,
        "output": "\n".join(lines), "raw": "",
        "destructive": False, "error": None,
    }


def _error(skill_cat: str, msg: str) -> dict:
    return {
        "skill": skill_cat, "success": False,
        "output": msg, "raw": "", "destructive": False, "error": "device_error",
    }
