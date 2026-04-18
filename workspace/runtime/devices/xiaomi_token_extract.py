#!/usr/bin/env python3
"""
xiaomi_token_extract.py — Extract miio token from Xiaomi Cloud.

Handles 2FA/security verification via notification URL flow.

Usage:
    python3 xiaomi_token_extract.py
    python3 xiaomi_token_extract.py --username EMAIL --password PASS
    python3 xiaomi_token_extract.py --token TOKEN  # skip cloud, save directly

The token is saved to openclaw.json > devices.xiaomi_vacuum.token
"""

from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode, urlparse, parse_qs
from urllib.request import Request, urlopen, build_opener, HTTPCookieProcessor
from http.cookiejar import CookieJar

CONFIG_PATH = Path("/home/tenazas/.openclaw/tenazas_config.json")
DEFAULT_IP = "192.168.2.99"


def save_token(token: str, ip: str = DEFAULT_IP) -> None:
    if CONFIG_PATH.exists():
        with CONFIG_PATH.open("r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        data = {}
    data.setdefault("devices", {})
    data["devices"]["xiaomi_vacuum"] = {"ip": ip, "token": token}
    tmp = CONFIG_PATH.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    tmp.replace(CONFIG_PATH)
    print(f"[ok] Token guardado en {CONFIG_PATH}")


def _api_request(url: str, data: dict | None = None,
                 cookies: dict | None = None, timeout: int = 10) -> str:
    headers = {"User-Agent": "Android-7.1.1-1.0.0-ONEPLUS A3010-136-QKQ1.190903.002 "
                              "APP/xiaomi.smarthome APPV/62830"}
    if cookies:
        headers["Cookie"] = "; ".join(f"{k}={v}" for k, v in cookies.items())
    if data is not None:
        body = urlencode(data).encode() if isinstance(data, dict) else data.encode()
        req = Request(url, data=body, headers=headers)
    else:
        req = Request(url, headers=headers)
    with urlopen(req, timeout=timeout) as resp:
        return resp.read().decode()


def login_and_extract(username: str, password: str) -> str | None:
    """Login to Xiaomi cloud and extract vacuum token."""

    # Step 1: Get sign
    print("[*] Step 1: Getting login sign...")
    url1 = "https://account.xiaomi.com/pass/serviceLogin?sid=xiaomiio&_json=true"
    text = _api_request(url1)
    data1 = json.loads(text.replace("&&&START&&&", ""))
    sign = data1.get("_sign", "")

    # Step 2: Login
    print("[*] Step 2: Logging in...")
    pwd_hash = hashlib.md5(password.encode()).hexdigest().upper()
    login_data = {
        "_json": "true", "sid": "xiaomiio", "user": username,
        "hash": pwd_hash, "_sign": sign,
        "callback": "https://sts.api.io.mi.com/sts",
    }
    text2 = _api_request(
        "https://account.xiaomi.com/pass/serviceLoginAuth2", login_data)
    data2 = json.loads(text2.replace("&&&START&&&", ""))

    location = data2.get("location", "")
    notification_url = data2.get("notificationUrl", "")
    security_status = data2.get("securityStatus", 0)

    # If security verification needed
    if not location and notification_url:
        print(f"[!] Xiaomi requiere verificacion de seguridad (status={security_status})")
        print(f"[!] Abrí este link en un navegador y completá la verificacion:")
        print(f"")
        print(f"    {notification_url}")
        print(f"")
        print(f"[*] Esperando que completes la verificacion...")
        print(f"    (tenes 120 segundos)")

        # Poll until verification is complete
        for attempt in range(24):  # 24 * 5s = 120s
            time.sleep(5)
            sys.stdout.write(f"    intento {attempt + 1}/24...\r")
            sys.stdout.flush()
            try:
                text_retry = _api_request(
                    "https://account.xiaomi.com/pass/serviceLoginAuth2", login_data)
                data_retry = json.loads(text_retry.replace("&&&START&&&", ""))
                location = data_retry.get("location", "")
                if location:
                    print(f"\n[ok] Verificacion completada!")
                    data2 = data_retry
                    break
            except Exception:
                pass
        else:
            print(f"\n[x] Timeout esperando verificacion.")
            return None

    if not location:
        print(f"[x] Login fallido: {data2.get('desc', 'unknown')}")
        return None

    # Step 3: Get service token
    print("[*] Step 3: Getting service token...")
    jar = CookieJar()
    opener = build_opener(HTTPCookieProcessor(jar))
    req = Request(location, headers={
        "User-Agent": "Android-7.1.1-1.0.0-ONEPLUS A3010-136-QKQ1.190903.002"})
    opener.open(req, timeout=10)

    service_token = None
    user_id = str(data2.get("userId", ""))
    for cookie in jar:
        if cookie.name == "serviceToken":
            service_token = cookie.value

    if not service_token:
        print("[x] No se obtuvo service token")
        return None

    # Step 4: Get devices from all servers
    print("[*] Step 4: Buscando dispositivos...")
    cookies = {"serviceToken": service_token, "userId": user_id}

    for server in ["de", "us", "sg", "i2", "ru", "cn"]:
        url = f"https://{server}.api.io.mi.com/app/home/device_list"
        body = json.dumps({"getVirtualModel": False, "getHuamiDevices": 0})
        try:
            headers = {
                "Content-Type": "application/json",
                "Cookie": f"serviceToken={service_token};userId={user_id}",
                "User-Agent": "Android-7.1.1-1.0.0-ONEPLUS A3010-136-QKQ1.190903.002 "
                              "APP/xiaomi.smarthome APPV/62830",
            }
            req = Request(url, data=body.encode(), headers=headers)
            with urlopen(req, timeout=10) as resp:
                resp_data = json.loads(resp.read().decode())

            devlist = resp_data.get("result", {}).get("list", [])
            if devlist:
                print(f"    {server}: {len(devlist)} dispositivos")
                for d in devlist:
                    name = d.get("name", "?")
                    model = d.get("model", "?")
                    token = d.get("token", "")
                    local_ip = d.get("localip", "?")
                    print(f"      {name} ({model}) ip={local_ip} token={token[:8]}...")
                    if "vacuum" in model.lower():
                        print(f"    >>> VACUUM ENCONTRADO!")
                        return token
        except Exception as e:
            print(f"    {server}: error ({e})")

    print("[x] No se encontro vacuum en ningún servidor")
    return None


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="Extract Xiaomi vacuum token")
    parser.add_argument("--username", "-u", help="Xiaomi account email")
    parser.add_argument("--password", "-p", help="Xiaomi account password")
    parser.add_argument("--token", "-t", help="Save token directly (skip cloud)")
    parser.add_argument("--ip", default=DEFAULT_IP, help="Vacuum IP")
    args = parser.parse_args()

    # Direct token save
    if args.token:
        if len(args.token) != 32:
            print(f"[x] Token debe ser 32 hex chars (recibido: {len(args.token)})")
            return 1
        save_token(args.token, args.ip)
        return 0

    # Interactive login
    username = args.username or input("Email Xiaomi: ")
    password = args.password or input("Password Xiaomi: ")

    token = login_and_extract(username, password)
    if token:
        save_token(token, args.ip)
        return 0

    # Manual fallback
    print()
    print("[*] Alternativa: ingresá el token manualmente")
    manual = input("Token (32 hex chars, o Enter para cancelar): ").strip()
    if manual:
        save_token(manual, args.ip)
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
