"""
nest_doorbell.py — Nest Doorbell Wired status check (ping only).

Google completely locked down local API for Nest devices.
Full control requires Google Device Access API (OAuth + $5 program fee).
This module only checks online/offline via network ping.
"""

from __future__ import annotations

import socket

DEVICE_ID = "nest_doorbell"
DEVICE_NAME = "Nest Doorbell"
DEFAULT_IP = "192.168.2.22"

ACTIONS = {
    "estado": "Verifica si esta online",
}


def _is_online(ip: str) -> bool:
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(1)
        result = sock.connect_ex((ip, 443))
        sock.close()
        return result == 0
    except Exception:
        return False


def status(config: dict) -> dict:
    from devices import load_tenazas_config
    ip = load_tenazas_config().get("devices", {}).get("nest_doorbell", {}).get("ip", DEFAULT_IP)
    online = _is_online(ip)
    return {
        "skill": "skill:smart-home", "success": True,
        "output": f"Nest Doorbell ({ip}): {'online' if online else 'offline'}",
        "raw": "", "destructive": False, "error": None,
    }


def execute(query: str, config: dict) -> dict:
    return status(config)
