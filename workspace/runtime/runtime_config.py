#!/usr/bin/env python3
"""
Shared runtime configuration helpers.

Centralizes access to operator-specific settings so runtime modules do not
embed chat IDs or device credentials in tracked source files.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")


def load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except (OSError, json.JSONDecodeError):
        return {}


def get_telegram_settings(config: dict | None = None) -> dict:
    cfg = config if config is not None else load_config()
    return cfg.get("channels", {}).get("telegram", {})


def get_telegram_bot_token(config: dict | None = None) -> str:
    token = os.environ.get("OPENCLAW_TELEGRAM_BOT_TOKEN", "").strip()
    if token:
        return token
    return str(get_telegram_settings(config).get("botToken", "")).strip()


def get_allowed_chat_ids(config: dict | None = None) -> set[str]:
    raw = get_telegram_settings(config).get("allowFrom", [])
    return {str(chat_id).strip() for chat_id in raw if str(chat_id).strip()}


def get_default_telegram_chat_id(config: dict | None = None) -> str:
    chat_id = os.environ.get("OPENCLAW_TELEGRAM_CHAT_ID", "").strip()
    if chat_id:
        return chat_id
    allowed = sorted(get_allowed_chat_ids(config))
    if allowed:
        return allowed[0]
    return ""


def get_hue_api_key(config: dict | None = None) -> str:
    key = os.environ.get("HUE_API_KEY", "").strip()
    if key:
        return key
    cfg = config if config is not None else load_config()
    return str(cfg.get("hue", {}).get("apiKey", "")).strip()
