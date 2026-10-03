"""Persistent health of IOL authentication and portfolio reads."""

import time
from pathlib import Path

from runtime_utils import StateLock, load_json, write_atomic

ROOT = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else Path(__file__).parent.parent / "state"
STATUS_FILE = ROOT / "dependencies/iol.json"


def record_response(endpoint, code):
    if endpoint.endswith("/token"):
        field = "auth_ok"
    elif "/portafolio/" in endpoint:
        field = "portfolio_ok"
    elif endpoint.endswith("/estadocuenta"):
        field = "account_ok"
    else:
        return
    with StateLock(STATUS_FILE):
        data = load_json(STATUS_FILE, {})
        data.update({field: 200 <= code < 300, "checked_at": time.time(),
                     "last_http_status": code})
        if field == "portfolio_ok" and 200 <= code < 300:
            data["portfolio_success_at"] = time.time()
        data["degraded"] = (not data.get("portfolio_ok", False)
                            or data.get("auth_ok") is False or data.get("account_ok") is False)
        if not data["degraded"] and field != "auth_ok":
            data["last_success_at"] = time.time()
        write_atomic(STATUS_FILE, data)


def status():
    data = load_json(STATUS_FILE, {})
    portfolio_time = data.get("portfolio_success_at", data.get("last_success_at", 0))
    data["degraded"] = data.get("degraded", True) or time.time() - portfolio_time > 900
    return data
