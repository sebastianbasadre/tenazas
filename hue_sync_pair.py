#!/usr/bin/env python3
"""
hue_sync_pair.py — Script one-time para obtener el access token del Sync Box.

Protocolo (Signify):
    1. POST https://<IP>/api/v1/registrations con {"appName","instanceName"}
       -> primera respuesta: {"code": 16, "message": "Invalid State"}
    2. Presionar y sostener el boton del Sync Box ~3 segundos hasta LED verde
    3. Soltar cuando LED esta verde y repetir el POST dentro de 5s
    4. Segunda respuesta: {"accessToken": "...", "registrationId": "..."}
    5. Guardar accessToken en state/openclaw.json como hue_sync_box.token

Uso:
    python3 hue_sync_pair.py 192.168.2.36
"""

from __future__ import annotations

import json
import ssl
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

APP_NAME = "tenazas"
INSTANCE_NAME = "tenazas-openclaw"
CONFIG_PATH = Path("/home/tenazas/.openclaw/tenazas_config.json")
MAX_ATTEMPTS = 15
RETRY_DELAY = 2.0


def _ssl_ctx() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def register(ip: str) -> dict:
    url = f"https://{ip}/api/v1/registrations"
    body = json.dumps({"appName": APP_NAME, "instanceName": INSTANCE_NAME}).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=5, context=_ssl_ctx()) as resp:
        return json.loads(resp.read().decode("utf-8"))


def save_token(config_path: Path, ip: str, token: str) -> None:
    """Merge non-destructivo en openclaw.json."""
    if config_path.exists():
        with config_path.open("r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}
    data.setdefault("hue_sync_box", {})
    data["hue_sync_box"]["ip"] = ip
    data["hue_sync_box"]["token"] = token
    tmp = config_path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(config_path)


def main() -> int:
    if len(sys.argv) < 2:
        print(f"uso: {sys.argv[0]} <IP_SYNC_BOX>")
        print("    ej: python3 hue_sync_pair.py 192.168.2.36")
        return 2
    ip = sys.argv[1]

    print(f"[*] Intentando registrarse en https://{ip}/api/v1/registrations")
    print("[*] Cuando veas 'Presiona el boton ahora',")
    print("    mantene apretado el boton del Sync Box ~3s hasta LED VERDE,")
    print("    solta apenas se ponga verde, y espera que el script haga retry.")
    print()

    token = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            resp = register(ip)
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = {"raw": body}
            if parsed.get("code") == 16 or e.code == 400:
                if attempt == 1:
                    print("[!] >>> Presiona el boton del Sync Box AHORA <<<")
                    print("    (tenes 5s desde que el LED se pone verde)")
                print(f"    intento {attempt}/{MAX_ATTEMPTS} — esperando pairing...")
                time.sleep(RETRY_DELAY)
                continue
            print(f"[x] HTTPError {e.code}: {body}")
            return 1
        except (urllib.error.URLError, TimeoutError, OSError) as e:
            print(f"[x] No se pudo conectar a {ip}: {e}")
            return 1

        if resp.get("accessToken"):
            token = resp["accessToken"]
            print(f"[ok] Token obtenido (len={len(token)})")
            break
        print(f"    respuesta inesperada: {resp}")
        time.sleep(RETRY_DELAY)

    if not token:
        print(f"[x] Timeout despues de {MAX_ATTEMPTS} intentos.")
        return 1

    save_token(CONFIG_PATH, ip, token)
    print(f"[ok] Guardado en {CONFIG_PATH} bajo hue_sync_box.token")
    return 0


if __name__ == "__main__":
    sys.exit(main())
