#!/usr/bin/env python3
"""
IOL Init Token — One-time setup for InvertirOnline API credentials.

Run this interactively to obtain the initial bearer + refresh token pair.
After this, the iol_client.py handles automatic refresh.

The password is NEVER logged or persisted.
"""

import getpass
import json
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

CONFIG_FILE = Path("/home/tenazas/.openclaw/openclaw.json")
IOL_CONFIG_FILE = Path("/home/tenazas/.openclaw/iol_config.json")
TOKEN_URL = "https://api.invertironline.com/token"
TIMEOUT = 10


def main():
    print("═══════════════════════════════════════")
    print("  IOL Init Token — InvertirOnline")
    print("═══════════════════════════════════════")
    print()

    # Try reading credentials from openclaw.json first (non-interactive mode)
    _username_from_config = None
    _password_from_config = None
    try:
        _cfg = json.load(open(CONFIG_FILE))
        _username_from_config = _cfg.get("iol_username")
        _password_from_config = _cfg.get("iol_password")
        # Fallback: read from separate credentials file
        if not _username_from_config or not _password_from_config:
            try:
                import json as _j2
                _creds = _j2.load(open("/home/tenazas/.openclaw/iol_credentials.json"))
                _username_from_config = _username_from_config or _creds.get("username")
                _password_from_config = _password_from_config or _creds.get("password")
            except Exception:
                pass
    except Exception:
        pass

    if _username_from_config and _password_from_config:
        username = _username_from_config
        password = _password_from_config
        print(f"Usando credenciales desde openclaw.json ({username})")
    else:
        username = input("Usuario IOL: ").strip()
        if not username:
            print("Error: usuario vacío")
            exit(1)

        password = getpass.getpass("Contraseña IOL: ")
        if not password:
            print("Error: contraseña vacía")
            exit(1)

    print()
    print(f"Autenticando como {username}...")

    body = urlencode({
        "username": username,
        "password": password,
        "grant_type": "password",
    }).encode()

    req = Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )

    try:
        resp = urlopen(req, timeout=TIMEOUT)
        data = json.loads(resp.read())
    except HTTPError as e:
        if e.code == 401:
            print("❌ Usuario o contraseña incorrectos")
            exit(1)
        elif e.code == 400:
            try:
                detail = json.loads(e.read())
                print(f"❌ Error 400: {json.dumps(detail, indent=2)}")
            except Exception:
                print(f"❌ Error 400: {e}")
            exit(1)
        else:
            print(f"❌ HTTP Error {e.code}: {e}")
            exit(1)
    except URLError as e:
        print(f"❌ Error de conexión: {e}")
        print("  Verificar conectividad a api.invertironline.com")
        exit(1)

    access_token = data.get("access_token")
    refresh_token = data.get("refresh_token")
    expires_in = data.get("expires_in", 899)

    if not access_token or not refresh_token:
        print(f"❌ Respuesta inesperada: {json.dumps(data)[:200]}")
        exit(1)

    token_expires_at = time.time() + expires_in - 10

    # Save to dedicated IOL config file (not openclaw.json to avoid schema conflict)
    iol_config = {
        "bearer_token": access_token,
        "refresh_token": refresh_token,
        "token_expires_at": token_expires_at,
        "base_url": "https://api.invertironline.com",
    }

    IOL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(IOL_CONFIG_FILE, "w") as f:
        json.dump(iol_config, f, indent=2, ensure_ascii=False)

    # Clean up legacy key from openclaw.json if present
    try:
        with open(CONFIG_FILE) as f:
            oc_config = json.load(f)
        if "invertironline" in oc_config:
            del oc_config["invertironline"]
            with open(CONFIG_FILE, "w") as f:
                json.dump(oc_config, f, indent=2, ensure_ascii=False)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    expiry_time = datetime.fromtimestamp(token_expires_at).strftime("%H:%M:%S")

    print()
    print("✅ Token obtenido correctamente")
    print(f"   Expira en: {expires_in} segundos (~{expires_in // 60} min)")
    print(f"   Refresh token: guardado")
    print(f"   Bearer token: guardado")
    print(f"   Próximo refresh automático: {expiry_time}")


if __name__ == "__main__":
    main()


# USO:
# docker exec -it openclaw python3 /home/tenazas/tools/iol_init_token.py
#
# Ejecutar cuando:
#   - Primera vez configurando IOL
#   - El refresh_token expiró (error 401 en runtime)
#
# La contraseña NO se guarda en ningún lado.
# Solo se persisten bearer_token y refresh_token en openclaw.json.
