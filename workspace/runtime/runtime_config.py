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


def use_agent_coordinator(config: dict | None = None) -> bool:
    """Return whether Telegram free-text queries should use agent_coordinator."""
    raw = os.environ.get("USE_AGENT_COORDINATOR", "").strip()
    if not raw:
        raw = get_telegram_settings(config).get("useAgentCoordinator", "")

    if isinstance(raw, bool):
        return raw

    text = str(raw).strip().lower()
    if text in {"1", "true", "yes", "on"}:
        return True
    if text in {"0", "false", "no", "off", ""}:
        return False
    return False


def get_hue_api_key(config: dict | None = None) -> str:
    key = os.environ.get("HUE_API_KEY", "").strip()
    if key:
        return key
    cfg = config if config is not None else load_config()
    return str(cfg.get("hue", {}).get("apiKey", "")).strip()


# ---------------------------------------------------------------------------
# Infrastructure endpoint constants
# ---------------------------------------------------------------------------
# Pattern: env var (TENAZAS_ prefix) > openclaw.json path > hardcoded default
# ---------------------------------------------------------------------------


def _env_or_default(env_key: str, default: str) -> str:
    """Return env var value if set and non-empty, otherwise default."""
    return os.environ.get(env_key, "").strip() or default


def _cfg_get(keys_path: list[str], env_key: str, default: str) -> str:
    """Try env var first, then walk openclaw.json path, then use default."""
    val = os.environ.get(env_key, "").strip()
    if val:
        return val
    cfg = load_config()
    for key in keys_path:
        if isinstance(cfg, dict):
            cfg = cfg.get(key)
        else:
            return default
    return str(cfg).strip() if cfg else default


# -- Network --
LAN_SUBNET = _env_or_default("TENAZAS_LAN_SUBNET", "192.168.2.0/24")
ROUTER_IP = _env_or_default("TENAZAS_ROUTER_IP", "192.168.2.1")
NAS_IP = _env_or_default("TENAZAS_NAS_IP", "192.168.2.2")
NAS_BACKUP_IP = _env_or_default("TENAZAS_NAS_BACKUP_IP", "192.168.2.3")
HOMELAB_IP = _env_or_default("TENAZAS_HOMELAB_IP", "192.168.2.6")
HOMELAB_USER = _env_or_default("TENAZAS_HOMELAB_USER", "sbasadre")

# -- Ollama --
OLLAMA_GPU_URL = _env_or_default("TENAZAS_OLLAMA_GPU_URL", f"http://{HOMELAB_IP}:11434")
OLLAMA_GPU_TAGS_URL = f"{OLLAMA_GPU_URL}/api/tags"
OLLAMA_GPU_GENERATE_URL = f"{OLLAMA_GPU_URL}/api/generate"
OLLAMA_GPU_CHAT_URL = f"{OLLAMA_GPU_URL}/api/chat"
OLLAMA_NAS_URL = _env_or_default("TENAZAS_OLLAMA_NAS_URL", "http://127.0.0.1:11434")

# -- Services on NAS --
CHROMADB_URL = _env_or_default("TENAZAS_CHROMADB_URL", f"http://{NAS_IP}:8100")
PROMETHEUS_URL = _env_or_default("TENAZAS_PROMETHEUS_URL", f"http://{NAS_IP}:9090")
GRAFANA_URL = _env_or_default("TENAZAS_GRAFANA_URL", f"http://{NAS_IP}:3000")
GLUETUN_PROXY = _env_or_default("TENAZAS_GLUETUN_PROXY", f"http://{NAS_IP}:8888")
SEARXNG_URL = _env_or_default("TENAZAS_SEARXNG_URL", f"http://{NAS_IP}:8890")

# -- Smart Home --
HUE_BRIDGE_IP = _env_or_default("TENAZAS_HUE_BRIDGE_IP", "192.168.2.11")
SAMSUNG_M8_IP = _env_or_default("TENAZAS_SAMSUNG_M8_IP", "192.168.2.46")
SAMSUNG_M8_MAC = "BC:45:5B:9F:F3:2A"
SAMSUNG_Q70_IP = _env_or_default("TENAZAS_SAMSUNG_Q70_IP", "192.168.2.29")
SAMSUNG_Q70_MAC = "B0:E4:5C:20:38:60"
MARANTZ_IP = _env_or_default("TENAZAS_MARANTZ_IP", "192.168.2.80")
HUE_SYNC_BOX_IP = _env_or_default("TENAZAS_HUE_SYNC_BOX_IP", "192.168.2.36")
NEST_DOORBELL_IP = _env_or_default("TENAZAS_NEST_DOORBELL_IP", "192.168.2.22")
XIAOMI_VACUUM_IP = _env_or_default("TENAZAS_XIAOMI_VACUUM_IP", "192.168.2.99")
SHIELD_TV_IP = _env_or_default("TENAZAS_SHIELD_TV_IP", "192.168.2.96")
NEST_MINI_IP = _env_or_default("TENAZAS_NEST_MINI_IP", "192.168.2.62")
HOME_MINI_IP = _env_or_default("TENAZAS_HOME_MINI_IP", "192.168.2.83")
DECO_IP = _env_or_default("TENAZAS_DECO_IP", "192.168.2.30")

# -- HomeLab exporters --
HOMELAB_GPU_EXPORTER_PORT = 9835
HOMELAB_WINDOWS_EXPORTER_PORT = 9182
