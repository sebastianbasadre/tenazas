"""
shield_tv.py — NVIDIA SHIELD TV control via Android TV Remote protocol.

IP: 192.168.2.35
Protocol: Android TV Remote v2 (androidtvremote2)
Requires one-time certificate-based pairing (PIN on screen).

Dependency: androidtvremote2, aiohttp (pip install androidtvremote2 aiohttp)
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket

logger = logging.getLogger(__name__)

DEVICE_ID = "shield_tv"
DEVICE_NAME = "NVIDIA SHIELD"
DEFAULT_IP = "192.168.2.96"
DEFAULT_MAC = "9C:00:D3:94:47:49"

ACTIONS = {
    "encender": "Enciende via Wake-on-LAN",
    "apagar": "Apaga (standby)",
    "app": "Lanza una app (Netflix, Disney, etc)",
    "play/pause": "Control de reproduccion",
    "navegar": "Home, back, dpad",
    "estado": "Verifica si esta online",
}

# Known Android TV app packages
_APPS = {
    "netflix": "com.netflix.ninja",
    "youtube": "com.google.android.youtube.tv",
    "disney": "com.disney.disneyplus",
    "disney+": "com.disney.disneyplus",
    "prime": "com.amazon.amazonvideo.livingroom",
    "amazon": "com.amazon.amazonvideo.livingroom",
    "hbo": "com.hbo.hbonow",
    "max": "com.hbo.hbonow",
    "spotify": "com.spotify.tv.android",
    "plex": "com.plexapp.android",
    "kodi": "org.xbmc.kodi",
    "steam": "com.valvesoftware.steamlink",
    "twitch": "tv.twitch.android.app",
}


def _is_online(ip: str) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((ip, 6466))  # Android TV Remote port
        sock.close()
        return result == 0
    except Exception:
        return False


def _wake_on_lan(mac: str) -> bool:
    try:
        mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
        magic = b"\xff" * 6 + mac_bytes * 16
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic, ("255.255.255.255", 9))
        sock.close()
        return True
    except Exception:
        return False


def _get_config(config: dict) -> tuple[str, str, str, str]:
    """Returns (ip, mac, cert_path, key_path)."""
    dev = config.get("devices", {}).get("shield_tv", {})
    ip = dev.get("ip", DEFAULT_IP)
    mac = dev.get("mac", DEFAULT_MAC)
    cert_path = dev.get("cert_path", "")
    key_path = dev.get("key_path", "")
    return ip, mac, cert_path, key_path


def status(config: dict) -> dict:
    """Check SHIELD status."""
    ip, _, _, _ = _get_config(config)
    online = _is_online(ip)
    return _ok(
        f"NVIDIA SHIELD ({ip}): {'online' if online else 'offline'}",
        destructive=False)


def execute(query: str, config: dict) -> dict:
    """Execute a SHIELD action based on query text."""
    ip, mac, cert_path, key_path = _get_config(config)
    q = query.lower()

    # Power on (WOL)
    if re.search(r"prend[eé]|encend|power\s*on|wake", q):
        if not _is_online(ip):
            _wake_on_lan(mac)
            return _ok("Wake-on-LAN enviado al SHIELD. Esperando...")
        return _ok("SHIELD ya esta encendido.")

    if not _is_online(ip):
        return _error(f"SHIELD ({ip}) no esta accesible. "
                       "Probablemente esta apagado.")

    if not cert_path or not key_path:
        return _error(
            "SHIELD no esta pareado. Necesita pairing one-time "
            "(genera certificado + PIN en pantalla). "
            "Configurar cert_path y key_path en openclaw.json > devices.shield_tv")

    # Try androidtvremote2 for all other commands
    try:
        return _execute_remote(ip, cert_path, key_path, q)
    except ImportError:
        return _error("androidtvremote2 no instalado. "
                       "Agregar a Dockerfile: pip install androidtvremote2 aiohttp")
    except Exception as e:
        return _error(f"Error controlando SHIELD: {e}")


def _execute_remote(ip: str, cert_path: str, key_path: str, q: str) -> dict:
    """Execute via androidtvremote2 (async wrapped)."""
    from androidtvremote2 import AndroidTVRemote, InvalidAuth

    async def _run():
        remote = AndroidTVRemote("Tenazas", cert_path, key_path, ip)
        try:
            await remote.async_connect()
        except InvalidAuth:
            return _error("Certificado invalido. Re-parear el SHIELD.")

        try:
            # Power off
            if re.search(r"apag[aá]|off|standby", q):
                remote.send_key_command("KEYCODE_POWER")
                return _ok("SHIELD apagado.")

            # App launch
            app_match = re.search(
                r"(?:abr[ií]|lanz[aá]|pon[eé]|open|launch)\s*"
                r"(netflix|youtube|disney\+?|prime|amazon|hbo|max|spotify"
                r"|plex|kodi|steam|twitch)", q)
            if app_match:
                app_name = app_match.group(1).lower()
                package = _APPS.get(app_name)
                if package:
                    remote.send_launch_app(package)
                    return _ok(f"{app_name.title()} lanzado en SHIELD.")
                return _error(f"App '{app_name}' no encontrada.")

            # Media controls
            if re.search(r"\bplay\b|reanudar|continuar", q):
                remote.send_key_command("KEYCODE_MEDIA_PLAY")
                return _ok("SHIELD: play")
            if re.search(r"pausa|pause", q):
                remote.send_key_command("KEYCODE_MEDIA_PAUSE")
                return _ok("SHIELD: pausa")
            if re.search(r"\bstop\b|detener", q):
                remote.send_key_command("KEYCODE_MEDIA_STOP")
                return _ok("SHIELD: stop")

            # Navigation
            if re.search(r"\bhome\b|inicio", q):
                remote.send_key_command("KEYCODE_HOME")
                return _ok("SHIELD: home")
            if re.search(r"back|atras|atrás|volver", q):
                remote.send_key_command("KEYCODE_BACK")
                return _ok("SHIELD: back")

            return status({"devices": {"shield_tv": {"ip": ip}}})

        finally:
            remote.disconnect()

    return asyncio.run(_run())


def _ok(output: str, raw: str = "", destructive: bool = True) -> dict:
    return {
        "skill": "skill:smart-home", "success": True,
        "output": output, "raw": raw,
        "destructive": destructive, "error": None,
    }


def _error(msg: str) -> dict:
    return {
        "skill": "skill:smart-home", "success": False,
        "output": msg, "raw": "", "destructive": False, "error": "shield_error",
    }
