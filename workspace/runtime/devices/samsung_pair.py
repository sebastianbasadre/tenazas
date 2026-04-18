#!/usr/bin/env python3
"""
samsung_pair.py — One-time pairing script for Samsung TVs.

Flow:
    1. Connect to ws://IP:8001/api/v2/channels/samsung.remote.control
    2. TV shows "Allow" popup — user must accept on screen
    3. Server sends token in the connection response
    4. Token saved to openclaw.json > devices.samsung_m8.token (or samsung_q70)

Usage:
    python3 samsung_pair.py 192.168.2.46          # M8 (default key: samsung_m8)
    python3 samsung_pair.py 192.168.2.29 samsung_q70  # Q70
"""

from __future__ import annotations

import base64
import json
import sys
import time
from pathlib import Path

APP_NAME = "Tenazas"
CONFIG_PATH = Path("/home/tenazas/.openclaw/tenazas_config.json")
MAX_WAIT = 30  # seconds to wait for user to press Allow


def pair(ip: str, config_key: str = "samsung_m8") -> int:
    try:
        import websocket
    except ImportError:
        print("[x] websocket-client no instalado.")
        print("    pip install websocket-client")
        return 1

    name_b64 = base64.b64encode(APP_NAME.encode()).decode()
    url = f"ws://{ip}:8001/api/v2/channels/samsung.remote.control?name={name_b64}"

    print(f"[*] Conectando a {url}")
    print(f"[!] >>> Acepta la conexion en la pantalla del Samsung <<<")
    print(f"    Tenes {MAX_WAIT}s para presionar 'Permitir/Allow'")
    print()

    try:
        ws = websocket.create_connection(url, timeout=MAX_WAIT)
    except Exception as e:
        print(f"[x] No se pudo conectar: {e}")
        return 1

    try:
        raw = ws.recv()
        resp = json.loads(raw)
        print(f"[*] Respuesta recibida: event={resp.get('event')}")

        token = resp.get("data", {}).get("token")
        if not token:
            # Some models send token in a different field
            token = resp.get("data", {}).get("clients", [{}])[0].get("token")

        if token:
            print(f"[ok] Token obtenido (len={len(token)})")
            _save_token(config_key, ip, token)
            print(f"[ok] Guardado en {CONFIG_PATH}")
            print(f"     Key: devices.{config_key}.token")

            # Send a test key to verify
            cmd = {
                "method": "ms.remote.control",
                "params": {
                    "Cmd": "Click",
                    "DataOfCmd": "KEY_MUTE",
                    "Option": "false",
                    "TypeOfRemote": "SendRemoteKey",
                },
            }
            ws.send(json.dumps(cmd))
            time.sleep(0.5)
            print("[ok] Test: KEY_MUTE enviado (deberia mutear/desmutear)")
            ws.close()
            return 0
        else:
            print(f"[x] No se recibio token. Respuesta completa:")
            print(f"    {json.dumps(resp, indent=2)}")
            ws.close()
            return 1

    except websocket.WebSocketTimeoutException:
        print(f"[x] Timeout — no se acepto la conexion en {MAX_WAIT}s")
        ws.close()
        return 1
    except Exception as e:
        print(f"[x] Error: {e}")
        try:
            ws.close()
        except Exception:
            pass
        return 1


def _save_token(config_key: str, ip: str, token: str) -> None:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}

    data.setdefault("devices", {})
    data["devices"].setdefault(config_key, {})
    data["devices"][config_key]["ip"] = ip
    data["devices"][config_key]["token"] = token
    data["devices"][config_key]["mac"] = _KNOWN_MACS.get(config_key, "")

    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(CONFIG_PATH)


_KNOWN_MACS = {
    "samsung_m8": "BC:45:5B:9F:F3:2A",
    "samsung_q70": "B0:E4:5C:20:38:60",
}


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(f"uso: {sys.argv[0]} <IP> [config_key]")
        print(f"  ej: python3 {sys.argv[0]} 192.168.2.46")
        print(f"  ej: python3 {sys.argv[0]} 192.168.2.29 samsung_q70")
        sys.exit(2)

    ip = sys.argv[1]
    key = sys.argv[2] if len(sys.argv) > 2 else "samsung_m8"
    sys.exit(pair(ip, key))
