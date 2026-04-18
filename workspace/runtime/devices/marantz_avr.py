"""
marantz_avr.py — Marantz CINEMA 60 AVR control via HTTP.

IP: 192.168.2.80
Protocol: Denon/Marantz HTTP API (no auth required)
Commands: GET http://IP/goform/formiPhoneAppDirect.xml?<CMD>
Status:   GET http://IP/goform/formMainZone_MainZoneXmlStatusLite.xml
"""

from __future__ import annotations

import re
import logging
import xml.etree.ElementTree as ET

import requests
import urllib3
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logger = logging.getLogger(__name__)

DEVICE_ID = "marantz_avr"
DEVICE_NAME = "Marantz CINEMA 60"
DEFAULT_IP = "192.168.2.80"
TIMEOUT = 5

ACTIONS = {
    "encender": "Enciende el receiver",
    "apagar": "Apaga el receiver (standby)",
    "volumen": "Sube/baja volumen o setea nivel",
    "mute": "Silencia / activa sonido",
    "input": "Cambia entrada (HDMI1-4, Bluetooth, etc)",
    "surround": "Cambia modo surround (Dolby, Stereo, etc)",
    "estado": "Estado actual (power, volumen, input, surround)",
}

# Denon/Marantz command codes
_COMMANDS = {
    "power_on": "PWON",
    "power_off": "PWSTANDBY",
    "mute_on": "MUON",
    "mute_off": "MUOFF",
    "mute_toggle": "MUON",
    "vol_up": "MVUP",
    "vol_down": "MVDOWN",
    # Input sources
    "input_hdmi1": "SISAT/CBL",
    "input_hdmi2": "SIDVD",
    "input_hdmi3": "SIMPLAY",
    "input_hdmi4": "SIGAME",
    "input_bluetooth": "SIBT",
    "input_tv": "SITV",
    "input_aux": "SIAUX1",
    # Surround modes
    "surround_stereo": "MSSTEREO",
    "surround_dolby": "MSDOLBY DIGITAL",
    "surround_dts": "MSDTS SURROUND",
    "surround_auto": "MSAUTO",
    "surround_movie": "MSMOVIE",
    "surround_music": "MSMUSIC",
}

# Input name mappings for natural language
_INPUT_MAP = {
    "hdmi1": "input_hdmi1", "hdmi 1": "input_hdmi1",
    "cable": "input_hdmi1", "cabletv": "input_hdmi1",
    "hdmi2": "input_hdmi2", "hdmi 2": "input_hdmi2",
    "dvd": "input_hdmi2", "blu-ray": "input_hdmi2", "bluray": "input_hdmi2",
    "hdmi3": "input_hdmi3", "hdmi 3": "input_hdmi3",
    "media": "input_hdmi3", "mplay": "input_hdmi3",
    "hdmi4": "input_hdmi4", "hdmi 4": "input_hdmi4",
    "game": "input_hdmi4", "consola": "input_hdmi4",
    "ps5": "input_hdmi4", "xbox": "input_hdmi4",
    "bluetooth": "input_bluetooth", "bt": "input_bluetooth",
    "tv": "input_tv", "arc": "input_tv", "earc": "input_tv",
    "aux": "input_aux", "auxiliar": "input_aux",
}


def _get_ip(config: dict) -> str:
    from devices import load_tenazas_config
    return load_tenazas_config().get("devices", {}).get("marantz_avr", {}).get("ip", DEFAULT_IP)


def _send_command(ip: str, cmd: str) -> bool:
    """Send a command to the Marantz. Returns True on success.

    Marantz CINEMA 60 redirects HTTP→HTTPS with self-signed cert.
    Uses verify=False to handle this.
    """
    url = f"http://{ip}/goform/formiPhoneAppDirect.xml?{cmd}"
    try:
        r = requests.get(url, timeout=TIMEOUT, verify=False)
        return r.status_code == 200
    except Exception as e:
        logger.warning("Marantz command %s failed: %s", cmd, e)
        return False


def _set_volume(ip: str, level: int) -> bool:
    """Set volume to a specific level (0-98, typical range 20-70)."""
    # Marantz volume: 2-digit = MV<XX>, half-step = MV<XX>5
    level = max(0, min(98, level))
    cmd = f"MV{level:02d}"
    return _send_command(ip, cmd)


def status(config: dict) -> dict:
    """Get current Marantz status."""
    ip = _get_ip(config)
    url = f"http://{ip}/goform/formMainZone_MainZoneXmlStatusLite.xml"
    try:
        r = requests.get(url, timeout=TIMEOUT, verify=False)
        if r.status_code != 200:
            return _error(f"Marantz no responde (HTTP {r.status_code})")

        root = ET.fromstring(r.text)
        power = root.findtext("Power/value", "?")
        volume = root.findtext("MasterVolume/value", "?")
        mute = root.findtext("Mute/value", "?")
        source = root.findtext("InputFuncSelect/value", "?")
        surround = root.findtext("SurrMode/value", "?")

        lines = [
            f"Marantz CINEMA 60:",
            f"  Power: {power}",
            f"  Volumen: {volume} dB",
            f"  Mute: {'si' if mute == 'on' else 'no'}",
            f"  Input: {source}",
            f"  Surround: {surround}",
        ]
        return _ok("\n".join(lines), destructive=False)

    except requests.ConnectionError:
        return _error("Marantz no accesible (offline?)")
    except Exception as e:
        return _error(f"Error consultando Marantz: {e}")


def execute(query: str, config: dict) -> dict:
    """Execute a Marantz action based on query text."""
    ip = _get_ip(config)
    q = query.lower()

    # Power
    if re.search(r"apag[aá]|off|standby", q):
        ok = _send_command(ip, _COMMANDS["power_off"])
        return _ok("Marantz apagado.") if ok else _error("No se pudo apagar el Marantz")

    if re.search(r"prend[eé]|encend|power\s*on", q):
        ok = _send_command(ip, _COMMANDS["power_on"])
        return _ok("Marantz encendido.") if ok else _error("No se pudo encender el Marantz")

    # Volume
    vol_match = re.search(r"volumen?\s*(?:a|en|al?)?\s*(\d+)", q)
    if vol_match:
        level = int(vol_match.group(1))
        ok = _set_volume(ip, level)
        return _ok(f"Volumen a {level}.") if ok else _error("No se pudo setear el volumen")

    if re.search(r"(sub[ií]|m[aá]s)\s*(el\s+)?vol", q) or re.search(r"vol\w*\s*(up|arriba|\+)", q):
        ok = _send_command(ip, _COMMANDS["vol_up"])
        return _ok("Volumen +") if ok else _error("Error subiendo volumen")

    if re.search(r"(baj[aá]|menos)\s*(el\s+)?vol", q) or re.search(r"vol\w*\s*(down|abajo|-)", q):
        ok = _send_command(ip, _COMMANDS["vol_down"])
        return _ok("Volumen -") if ok else _error("Error bajando volumen")

    # Mute
    if re.search(r"mute|silenci|mutear", q):
        ok = _send_command(ip, _COMMANDS["mute_toggle"])
        return _ok("Mute activado.") if ok else _error("Error con mute")
    if re.search(r"unmute|desmutear|sonido", q):
        ok = _send_command(ip, _COMMANDS["mute_off"])
        return _ok("Mute desactivado.") if ok else _error("Error desmuteando")

    # Input source
    input_match = re.search(
        r"(?:pon[eé]|cambi[aá]|pas[aá]|input|entrada|fuente)\s*(?:a|al?|en)?\s*"
        r"(hdmi\s*[1-4]|bluetooth|bt|tv|arc|earc|aux|dvd|blu.?ray|cable|game|"
        r"consola|ps5|xbox|media|mplay|auxiliar)", q)
    if input_match:
        source_name = input_match.group(1).strip().lower()
        source_name = re.sub(r"\s+", "", source_name)  # "hdmi 1" → "hdmi1"
        cmd_key = _INPUT_MAP.get(source_name)
        if cmd_key:
            ok = _send_command(ip, _COMMANDS[cmd_key])
            return _ok(f"Input: {source_name}") if ok else _error(f"Error cambiando a {source_name}")
        return _error(f"Input '{source_name}' no reconocido.")

    # Surround mode
    surr_match = re.search(
        r"(?:modo|surround|sonido)\s*(?:a|en)?\s*"
        r"(stereo|dolby|dts|auto|movie|music|cine|pel[ií]cula|m[uú]sica)", q)
    if surr_match:
        mode = surr_match.group(1).lower()
        mode_map = {
            "stereo": "surround_stereo",
            "dolby": "surround_dolby",
            "dts": "surround_dts",
            "auto": "surround_auto",
            "movie": "surround_movie", "cine": "surround_movie",
            "pelicula": "surround_movie", "película": "surround_movie",
            "music": "surround_music", "musica": "surround_music",
            "música": "surround_music",
        }
        cmd_key = mode_map.get(mode, "surround_auto")
        ok = _send_command(ip, _COMMANDS[cmd_key])
        return _ok(f"Surround: {mode}") if ok else _error(f"Error cambiando a {mode}")

    # Estado (default)
    if re.search(r"estado|status|c[oó]mo\s+(est[aá]|anda)", q):
        return status(config)

    # If nothing matched, show status
    return status(config)


def _ok(output: str, raw: str = "", destructive: bool = True) -> dict:
    return {
        "skill": "skill:smart-home", "success": True,
        "output": output, "raw": raw,
        "destructive": destructive, "error": None,
    }


def _error(msg: str) -> dict:
    return {
        "skill": "skill:smart-home", "success": False,
        "output": msg, "raw": "", "destructive": False, "error": "marantz_error",
    }
