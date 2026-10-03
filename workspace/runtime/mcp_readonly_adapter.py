#!/usr/bin/env python3
"""Read-only adapter for the Tenazas MCP boundary.

This module intentionally reads precomputed/cached state only. It does not import
homelab_snapshot because that collector can invoke Docker and SSH subprocesses.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

RUNTIME = Path(__file__).resolve().parent
STATE_FILE = RUNTIME / "state.json"
AGENT_STATUS_FILE = Path("/home/tenazas/.openclaw/state/agents/infra_status.json")
INCIDENTS_DIR = Path("/home/tenazas/.openclaw/state/incidents")

TOOLS = (
    "get_status",
    "get_containers",
    "get_models",
    "get_storage",
    "get_network_health",
    "get_recent_alerts",
)

_SECRET_KEYS = {
    "token", "password", "secret", "authorization", "cookie", "api_key",
    "apikey", "botToken", "private_key", "ssh_key",
}


def _load_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return default


def _sanitize(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            str(k): _sanitize(v)
            for k, v in value.items()
            if str(k).lower() not in {x.lower() for x in _SECRET_KEYS}
        }
    if isinstance(value, list):
        return [_sanitize(v) for v in value]
    return value


def _state() -> dict:
    data = _load_json(STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def get_containers() -> dict:
    containers = _state().get("containers", [])
    safe = []
    if isinstance(containers, list):
        for item in containers:
            if not isinstance(item, dict):
                continue
            safe.append({
                "name": str(item.get("name", ""))[:80],
                "up": bool(item.get("up", False)),
                "status": str(item.get("status", ""))[:160],
            })
    return {
        "total": len(safe),
        "up": sum(1 for c in safe if c["up"]),
        "down": sum(1 for c in safe if not c["up"]),
        "containers": safe,
    }


def get_models() -> dict:
    state = _state()
    return _sanitize({
        "ollama_notebook": state.get("ollama_notebook", {}),
        "ollama_nas": state.get("ollama_nas", {}),
        "model": state.get("model", state.get("models", {})),
    })


def get_storage() -> dict:
    state = _state()
    infra = _load_json(AGENT_STATUS_FILE, {})
    return _sanitize({
        "disk": state.get("disk", state.get("storage", {})),
        "infra_disk": infra.get("raw", {}) if infra.get("sub") == "disk" else {},
    })


def get_network_health() -> dict:
    state = _state()
    return _sanitize({
        "internet": state.get("internet", {}),
        "vpn": state.get("vpn", {}),
        "network_devices": state.get("network_devices", {}),
    })


def get_recent_alerts() -> dict:
    alerts = []
    if INCIDENTS_DIR.exists():
        try:
            files = sorted(
                INCIDENTS_DIR.glob("*.json"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:20]
        except OSError:
            files = []
        for path in files:
            inc = _load_json(path, {})
            if not isinstance(inc, dict):
                continue
            alerts.append({
                "id": str(inc.get("incident_id", path.stem))[:100],
                "title": str(inc.get("title", ""))[:240],
                "severity": str(inc.get("severity", ""))[:40],
                "status": str(inc.get("status", ""))[:40],
            })
    return {"alerts": alerts}


def get_status() -> dict:
    state = _state()
    return _sanitize({
        "containers": get_containers(),
        "models": get_models(),
        "storage": get_storage(),
        "network": get_network_health(),
        "alerts": get_recent_alerts(),
        "timestamp": state.get("ts", state.get("timestamp", "")),
    })


_HANDLERS = {
    "get_status": get_status,
    "get_containers": get_containers,
    "get_models": get_models,
    "get_storage": get_storage,
    "get_network_health": get_network_health,
    "get_recent_alerts": get_recent_alerts,
}


def call_tool(name: str) -> dict:
    """Dispatch only a fixed, argument-free, read-only allowlist."""
    if name not in _HANDLERS:
        raise ValueError("tool not allowed")
    return _HANDLERS[name]()
