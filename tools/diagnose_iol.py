"""Test the documented IOL token endpoint, persisting rotated tokens on success."""

import json
import sys
import time
from pathlib import Path

import requests

sys.path.insert(0, "/home/tenazas/runtime")
from runtime_utils import write_atomic


def main():
    path = Path("/home/tenazas/.openclaw/iol_config.json")
    config = json.loads(path.read_text())
    session = requests.Session()
    session.headers.update({"User-Agent": "Tenazas/1.0", "Accept": "application/json"})
    response = session.post("https://api.invertironline.com/token", data={
        "grant_type": "refresh_token", "refresh_token": config.get("refresh_token", ""),
    }, timeout=20)
    if response.status_code in (400, 401):
        credentials_path = path.with_name("iol_credentials.json")
        credentials = json.loads(credentials_path.read_text()) if credentials_path.exists() else {}
        if credentials.get("username") and credentials.get("password"):
            response = session.post("https://api.invertironline.com/token", data={
                "grant_type": "password", "username": credentials["username"],
                "password": credentials["password"],
            }, timeout=20)
    report = {"http_status": response.status_code,
              "cloudflare": response.headers.get("server", "").lower() == "cloudflare"}
    try:
        payload = response.json()
    except ValueError:
        payload = {}
    if response.status_code == 200 and payload.get("access_token"):
        config.update({"bearer_token": payload["access_token"],
                       "refresh_token": payload.get("refresh_token", config.get("refresh_token")),
                       "token_expires_at": time.time() + int(payload.get("expires_in", 899)) - 10})
        write_atomic(path, config)
        portfolio = session.get("https://api.invertironline.com/api/v2/portafolio/argentina",
                                headers={"Authorization": "Bearer " + config["bearer_token"]},
                                timeout=20)
        report["portfolio_http_status"] = portfolio.status_code
    for key in ("error_code", "error_name", "retryable", "owner_action_required"):
        if isinstance(payload.get(key), (int, bool)) or (
            isinstance(payload.get(key), str) and payload[key].replace("_", "").isalnum()
        ):
            report[key] = payload[key]
    print(json.dumps(report))


if __name__ == "__main__":
    main()
