#!/usr/bin/env python3
"""Send startup notifications without exposing Telegram tokens in shell args."""

from __future__ import annotations

import os
import sys
from datetime import datetime
from urllib.parse import urljoin

import requests

try:
    from .runtime_config import (
        get_default_telegram_chat_id,
        get_telegram_bot_token,
        load_config,
        validate_config,
    )
except ImportError:
    from runtime_config import (
        get_default_telegram_chat_id,
        get_telegram_bot_token,
        load_config,
        validate_config,
    )


def send_startup_notification() -> int:
    cfg = load_config()
    report = validate_config(cfg, require_secrets=False)
    for warning in report["warnings"]:
        print(f"[startup] config warning: {warning}", file=sys.stderr)

    token = get_telegram_bot_token(cfg)
    chat_id = get_default_telegram_chat_id(cfg)
    if not token or not chat_id:
        print("[startup] Telegram startup notification skipped: missing token or chat id")
        return 0

    timestamp = datetime.now().strftime("%H:%M")
    text = f"Tenazas Online - {timestamp} ART"
    url = urljoin(f"https://api.telegram.org/bot{token}/", "sendMessage")
    response = requests.post(url, data={"chat_id": chat_id, "text": text}, timeout=15)
    if response.status_code >= 400:
        print(f"[startup] Telegram startup notification failed: HTTP {response.status_code}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(send_startup_notification())
