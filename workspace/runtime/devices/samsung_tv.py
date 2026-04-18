"""
samsung_tv.py — Samsung TV/Monitor control via WebSocket.

Devices:
  Samsung M8 Monitor: 192.168.2.46 (ws://IP:8001)
  Samsung Q70 85" TV: 192.168.2.29 (ws://IP:8001)

Protocol: Samsung SmartThings WebSocket remote control.
First connection requires one-time pairing (user allows on screen).
Token is cached in openclaw.json after pairing.

Dependency: websocket-client (pip install websocket-client)
"""

from __future__ import annotations

import base64
import json
import logging
import re
import socket
import struct
import time

logger = logging.getLogger(__name__)

DEVICE_ID = "samsung_tv"
DEVICE_NAME = "Samsung TV"
TIMEOUT = 5
APP_NAME = "Tenazas"

ACTIONS = {
    "encender": "Enciende via Wake-on-LAN",
    "apagar": "Apaga (standby)",
    "volumen": "Sube/baja volumen",
    "mute": "Silencia",
    "input": "Cambia entrada HDMI",
    "app": "Lanza una aplicacion (Netflix, Disney, etc)",
    "estado": "Verifica si esta online",
}

# Samsung key codes
_KEYS = {
    "power": "KEY_POWER",
    "vol_up": "KEY_VOLUP",
    "vol_down": "KEY_VOLDOWN",
    "mute": "KEY_MUTE",
    "hdmi1": "KEY_HDMI1", "hdmi2": "KEY_HDMI2",
    "hdmi3": "KEY_HDMI3", "hdmi4": "KEY_HDMI4",
    "source": "KEY_SOURCE",
    "home": "KEY_HOME",
    "back": "KEY_RETURN",
    "enter": "KEY_ENTER",
    "up": "KEY_UP", "down": "KEY_DOWN",
    "left": "KEY_LEFT", "right": "KEY_RIGHT",
    "play": "KEY_PLAY", "pause": "KEY_PAUSE",
    "stop": "KEY_STOP",
}

# Known app IDs for Samsung TVs
_APP_IDS = {
    "netflix": "3201907018807",
    "youtube": "111299001912",
    "disney": "3201901017640",
    "disney+": "3201901017640",
    "prime": "3201910019365",
    "amazon": "3201910019365",
    "hbo": "3201601007230",
    "spotify": "3201606009684",
    "plex": "3201512006963",
    "apple tv": "3201807016597",
    "appletv": "3201807016597",
}

# Device registry: name → {ip, mac, token}
_DEVICES = {
    "m8": {"ip": "192.168.2.46", "mac": "BC:45:5B:9F:F3:2A", "config_key": "samsung_m8"},
    "q70": {"ip": "192.168.2.29", "mac": "B0:E4:5C:20:38:60", "config_key": "samsung_q70"},
}


def _detect_target(query: str) -> dict:
    """Detect which Samsung TV the user is targeting. Default: M8."""
    q = query.lower()
    if re.search(r"q70|85|tele(visor)?(?!\s*m8)", q):
        return _DEVICES["q70"]
    return _DEVICES["m8"]


def _get_device_config(config: dict, device: dict) -> dict:
    """Get device config from openclaw.json."""
    devs = config.get("devices", {})
    return devs.get(device["config_key"], {})


def _wake_on_lan(mac: str) -> bool:
    """Send Wake-on-LAN magic packet."""
    try:
        mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
        magic = b"\xff" * 6 + mac_bytes * 16
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.sendto(magic, ("255.255.255.255", 9))
        sock.close()
        return True
    except Exception as e:
        logger.warning("WOL failed for %s: %s", mac, e)
        return False


def _is_online(ip: str, port: int = 8002) -> bool:
    """Check if TV is reachable."""
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(2)
        result = sock.connect_ex((ip, port))
        sock.close()
        return result == 0
    except Exception:
        return False


def _send_key(ip: str, key: str, token: str | None = None,
              port: int = 8002, use_ssl: bool = True) -> dict:
    """Send a key command via WebSocket."""
    try:
        import websocket
    except ImportError:
        return _error("websocket-client no instalado. "
                       "Agregar a Dockerfile: pip install websocket-client")

    name_b64 = base64.b64encode(APP_NAME.encode()).decode()
    proto = "wss" if use_ssl else "ws"
    url = f"{proto}://{ip}:{port}/api/v2/channels/samsung.remote.control?name={name_b64}"
    if token:
        url += f"&token={token}"

    ssl_opts = {"cert_reqs": __import__("ssl").CERT_NONE} if use_ssl else {}

    try:
        ws = websocket.create_connection(url, timeout=TIMEOUT, sslopt=ssl_opts)
        # Read connection response (may contain token)
        resp = json.loads(ws.recv())

        # Send key command
        cmd = {
            "method": "ms.remote.control",
            "params": {
                "Cmd": "Click",
                "DataOfCmd": key,
                "Option": "false",
                "TypeOfRemote": "SendRemoteKey",
            },
        }
        ws.send(json.dumps(cmd))
        time.sleep(0.3)
        ws.close()

        # Extract new token if present
        new_token = None
        data = resp.get("data", {})
        if data.get("token"):
            new_token = data["token"]

        return {"success": True, "new_token": new_token}

    except Exception as e:
        return {"success": False, "error": str(e)}


def _launch_app(ip: str, app_id: str, token: str | None = None,
                port: int = 8002, use_ssl: bool = True) -> dict:
    """Launch an app on Samsung TV."""
    try:
        import websocket
    except ImportError:
        return _error("websocket-client no instalado.")

    name_b64 = base64.b64encode(APP_NAME.encode()).decode()
    proto = "wss" if use_ssl else "ws"
    url = f"{proto}://{ip}:{port}/api/v2/channels/samsung.remote.control?name={name_b64}"
    if token:
        url += f"&token={token}"

    ssl_opts = {"cert_reqs": __import__("ssl").CERT_NONE} if use_ssl else {}

    try:
        ws = websocket.create_connection(url, timeout=TIMEOUT, sslopt=ssl_opts)
        ws.recv()  # connection response

        cmd = {
            "method": "ms.channel.emit",
            "params": {
                "event": "ed.apps.launch",
                "to": "host",
                "data": {"appId": app_id, "action_type": "DEEP_LINK"},
            },
        }
        ws.send(json.dumps(cmd))
        time.sleep(0.5)
        ws.close()
        return {"success": True}

    except Exception as e:
        return {"success": False, "error": str(e)}


def status(config: dict) -> dict:
    """Check Samsung TV status."""
    lines = ["Samsung TVs:"]
    for name, dev in _DEVICES.items():
        online = _is_online(dev["ip"])
        lines.append(f"  {name.upper()} ({dev['ip']}): {'online' if online else 'offline'}")
    return _ok("\n".join(lines), destructive=False)


def execute(query: str, config: dict) -> dict:
    """Execute a Samsung TV action based on query text."""
    q = query.lower()
    target = _detect_target(query)
    ip = target["ip"]
    dev_cfg = _get_device_config(config, target)
    token = dev_cfg.get("token")
    port = dev_cfg.get("port", 8002)
    use_ssl = dev_cfg.get("ssl", True)

    # Power on (WOL)
    if re.search(r"prend[eé]|encend|power\s*on|wake", q):
        if not _is_online(ip):
            _wake_on_lan(target["mac"])
            return _ok(f"Wake-on-LAN enviado a Samsung ({ip}). "
                       "Esperando que encienda...")
        return _ok(f"Samsung ({ip}) ya esta encendido.")

    # Check if online before sending commands
    if not _is_online(ip):
        return _error(f"Samsung ({ip}) no esta accesible. "
                       "Probablemente esta apagado.")

    # Power off
    if re.search(r"apag[aá]|off|standby", q):
        r = _send_key(ip, _KEYS["power"], token, port, use_ssl)
        return _ok("Samsung apagado.") if r.get("success") else \
            _error(f"Error apagando Samsung: {r.get('error')}")

    # Volume
    vol_match = re.search(r"(sub[ií]|m[aá]s)\s*(el\s+)?vol", q)
    if vol_match or re.search(r"vol\w*\s*(up|arriba|\+)", q):
        r = _send_key(ip, _KEYS["vol_up"], token, port, use_ssl)
        return _ok("Volumen +") if r.get("success") else _error("Error con volumen")

    if re.search(r"(baj[aá]|menos)\s*(el\s+)?vol", q) or re.search(r"vol\w*\s*(down|abajo|-)", q):
        r = _send_key(ip, _KEYS["vol_down"], token, port, use_ssl)
        return _ok("Volumen -") if r.get("success") else _error("Error con volumen")

    # Mute
    if re.search(r"mute|silenci|mutear", q):
        r = _send_key(ip, _KEYS["mute"], token, port, use_ssl)
        return _ok("Mute toggle.") if r.get("success") else _error("Error con mute")

    # HDMI input
    hdmi_match = re.search(r"hdmi\s*(\d)", q)
    if hdmi_match:
        n = int(hdmi_match.group(1))
        key = _KEYS.get(f"hdmi{n}")
        if key:
            r = _send_key(ip, key, token, port, use_ssl)
            return _ok(f"HDMI {n}") if r.get("success") else _error(f"Error cambiando a HDMI {n}")

    if re.search(r"source|fuente|input|entrada", q):
        r = _send_key(ip, _KEYS["source"], token, port, use_ssl)
        return _ok("Menu de fuentes abierto.") if r.get("success") else _error("Error")

    # App launch
    app_match = re.search(
        r"(?:abr[ií]|lanz[aá]|pon[eé]|open|launch)\s*"
        r"(netflix|youtube|disney\+?|prime|amazon|hbo|spotify|plex|apple\s*tv)", q)
    if app_match:
        app_name = app_match.group(1).lower().replace("+", "+")
        app_id = _APP_IDS.get(app_name)
        if app_id:
            r = _launch_app(ip, app_id, token, port, use_ssl)
            return _ok(f"{app_name.title()} lanzado.") if r.get("success") else \
                _error(f"Error lanzando {app_name}")
        return _error(f"App '{app_name}' no encontrada.")

    # Navigation keys
    if re.search(r"\bhome\b|inicio", q):
        r = _send_key(ip, _KEYS["home"], token, port, use_ssl)
        return _ok("Home") if r.get("success") else _error("Error")

    # Estado (default)
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
        "output": msg, "raw": "", "destructive": False, "error": "samsung_error",
    }
