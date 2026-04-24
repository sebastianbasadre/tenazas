#!/usr/bin/env python3
"""
iol_refresh.py — Automatic IOL token refresh.

Designed to run from cron every 10 minutes. No interactive input.
Reads credentials from openclaw.json, token from iol_config.json.

Usage:
    python3 /home/tenazas/tools/iol_refresh.py            # normal refresh
    python3 /home/tenazas/tools/iol_refresh.py --force     # force even if valid
    python3 /home/tenazas/tools/iol_refresh.py --status    # show current state
"""

import json
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ---------------------------------------------------------------------------
# Paths (match iol_client.py and iol_init_token.py)
# ---------------------------------------------------------------------------
CONFIG_FILE = Path("/home/tenazas/.openclaw/openclaw.json")
IOL_CONFIG_FILE = Path("/home/tenazas/.openclaw/iol_config.json")
TOKEN_URL = "https://api.invertironline.com/token"

REFRESH_MARGIN = 120  # refresh if expiring within 2 min
LOG_PREFIX = "[iol_refresh]"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_FILE.read_text())
    except Exception as e:
        print(f"{LOG_PREFIX} ERROR reading openclaw.json: {e}")
        return {}


def _load_iol_config() -> dict:
    try:
        return json.loads(IOL_CONFIG_FILE.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_iol_config(data: dict):
    IOL_CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    IOL_CONFIG_FILE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def _token_needs_refresh(iol_config: dict, force: bool = False) -> bool:
    if force:
        return True
    expires_at = iol_config.get("token_expires_at")
    if not expires_at:
        return True
    return time.time() > (float(expires_at) - REFRESH_MARGIN)


def _http_post(url: str, data: dict, timeout: int = 15) -> dict:
    body = urlencode(data).encode()
    req = Request(url, data=body,
                  headers={"Content-Type": "application/x-www-form-urlencoded"},
                  method="POST")
    with urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read())


def _notify_telegram(message: str):
    """Send error notification via Telegram."""
    try:
        config = json.loads(CONFIG_FILE.read_text())
        tg = config.get("channels", {}).get("telegram", {})
        token = tg.get("botToken", "")
        chat_id = str(tg.get("allowFrom", [""])[0])
        if not token:
            return
        payload = json.dumps({
            "chat_id": chat_id,
            "text": f"IOL Token: {message}",
        }).encode()
        req = Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        )
        urlopen(req, timeout=10)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Refresh methods
# ---------------------------------------------------------------------------

def _refresh_with_token(refresh_token: str) -> dict | None:
    """Refresh using the stored refresh_token."""
    try:
        return _http_post(TOKEN_URL, {
            "grant_type": "refresh_token",
            "refresh_token": refresh_token,
        })
    except HTTPError as e:
        if e.code in (400, 401):
            print(f"{LOG_PREFIX} Refresh token expired (HTTP {e.code})")
            return None
        raise
    except Exception as e:
        print(f"{LOG_PREFIX} Refresh error: {e}")
        return None


def _auth_with_password(username: str, password: str) -> dict | None:
    """Re-authenticate with username/password (fallback)."""
    try:
        return _http_post(TOKEN_URL, {
            "grant_type": "password",
            "username": username,
            "password": password,
        })
    except Exception as e:
        print(f"{LOG_PREFIX} Password auth error: {e}")
        return None


# ---------------------------------------------------------------------------
# Main flow
# ---------------------------------------------------------------------------

def run(force: bool = False, status_only: bool = False):
    iol_config = _load_iol_config()

    if status_only:
        expires_at = iol_config.get("token_expires_at")
        if not expires_at:
            print(f"{LOG_PREFIX} No token stored")
            return
        remaining = float(expires_at) - time.time()
        refreshed = iol_config.get("refreshed_at", "unknown")
        if remaining > 0:
            print(f"{LOG_PREFIX} Token valid -- expires in {int(remaining)}s "
                  f"({datetime.fromtimestamp(float(expires_at)).strftime('%H:%M:%S')})")
        else:
            print(f"{LOG_PREFIX} Token EXPIRED {int(-remaining)}s ago")
        print(f"{LOG_PREFIX} Last refresh: {refreshed}")
        return

    if not _token_needs_refresh(iol_config, force):
        expires_at = iol_config.get("token_expires_at", 0)
        remaining = int(float(expires_at) - time.time())
        print(f"{LOG_PREFIX} Token OK -- expires in {remaining}s. No action.")
        return

    now_str = datetime.now().strftime("%H:%M:%S")
    print(f"{LOG_PREFIX} Refreshing token ({now_str})...")

    # Try refresh_token first
    result = None
    refresh_token = iol_config.get("refresh_token")
    if refresh_token:
        result = _refresh_with_token(refresh_token)

    # Fallback: re-auth with password from openclaw.json
    if result is None:
        print(f"{LOG_PREFIX} Trying password re-auth...")
        config = _load_config()
        username = config.get("iol_username")
        password = config.get("iol_password")
        # Fallback: separate credentials file
        if not username or not password:
            try:
                creds = json.load(open("/home/tenazas/.openclaw/iol_credentials.json"))
                username = username or creds.get("username")
                password = password or creds.get("password")
            except Exception:
                pass

        if not username or not password:
            msg = "Token expired, no credentials in openclaw.json (iol_username/iol_password). Manual re-auth needed."
            print(f"{LOG_PREFIX} ERROR: {msg}")
            _notify_telegram(msg)
            sys.exit(1)

        result = _auth_with_password(username, password)

    if result is None:
        msg = "Could not refresh IOL token. Check credentials."
        print(f"{LOG_PREFIX} ERROR: {msg}")
        _notify_telegram(msg)
        sys.exit(1)

    # Save new token (same format as iol_init_token.py and iol_client.py)
    expires_in = result.get("expires_in", 899)
    new_config = {
        "bearer_token": result.get("access_token"),
        "refresh_token": result.get("refresh_token", refresh_token),
        "token_expires_at": time.time() + expires_in - 10,
        "base_url": iol_config.get("base_url", "https://api.invertironline.com"),
        "refreshed_at": datetime.now().isoformat(),
    }
    _save_iol_config(new_config)

    print(f"{LOG_PREFIX} Token refreshed -- expires in {expires_in}s")


if __name__ == "__main__":
    force = "--force" in sys.argv
    status_only = "--status" in sys.argv
    run(force=force, status_only=status_only)
