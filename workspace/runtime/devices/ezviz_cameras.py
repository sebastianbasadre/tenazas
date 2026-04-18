"""
ezviz_cameras.py — EZVIZ camera status check (ping only).

Local control is very limited without EZVIZ cloud API.
This module only checks online/offline status via network ping.
"""

from __future__ import annotations

import socket

DEVICE_ID = "ezviz_cameras"
DEVICE_NAME = "Camaras EZVIZ"

ACTIONS = {
    "estado": "Verifica cuales estan online/offline",
}

_CAMERAS = [
    {"name": "EZVIZ C6N (2)", "ip": "192.168.2.10"},
    {"name": "EZVIZ H3c (2)", "ip": "192.168.2.16"},
    {"name": "EZVIZ C6N (3)", "ip": "192.168.2.17"},
    {"name": "EZVIZ H1c", "ip": "192.168.2.18"},
    {"name": "EZVIZ H1c (2)", "ip": "192.168.2.19"},
    {"name": "EZVIZ H9c", "ip": "192.168.2.25"},
    {"name": "EZVIZ H3c", "ip": "192.168.2.27"},
    {"name": "EZVIZ C6N", "ip": "192.168.2.138"},
    {"name": "Dahua NVR", "ip": "192.168.2.4"},
    {"name": "Hikvision Camera", "ip": "192.168.2.45"},
]


def _is_online(ip: str) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((ip, 80))
        sock.close()
        return result == 0
    except Exception:
        return False


def status(config: dict) -> dict:
    """Check which cameras are online."""
    lines = ["Camaras:"]
    online = 0
    for cam in _CAMERAS:
        up = _is_online(cam["ip"])
        if up:
            online += 1
        lines.append(f"  {cam['name']} ({cam['ip']}): "
                     f"{'online' if up else 'offline'}")
    lines.insert(1, f"  {online}/{len(_CAMERAS)} online")
    return {
        "skill": "skill:smart-home", "success": True,
        "output": "\n".join(lines), "raw": "",
        "destructive": False, "error": None,
    }


def execute(query: str, config: dict) -> dict:
    return status(config)
