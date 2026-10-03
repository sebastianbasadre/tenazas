#!/usr/bin/env python3
"""Read-only adapter for the Tenazas MCP boundary.

This module intentionally reads precomputed/cached state only. It does not import
live collectors because those can execute infrastructure commands.
"""

from __future__ import annotations

import json
import math
import os
import re
import stat
from itertools import islice
from pathlib import Path
from typing import Any
from urllib.parse import unquote

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

_SECRET_MARKERS = (
    "token", "password", "passwd", "pwd", "secret", "authorization",
    "cookie", "apikey", "accesskey", "privatekey", "sshkey", "credential",
    "session", "authentication",
)
_PRIVATE_FIELDS = {"auth", "headers", "environment", "env", "config", "raw"}
# Cached objects are projected, never forwarded as arbitrary dictionaries.
_OBSERVABLE_FIELDS = {
    "name", "model", "models", "selected_model", "status", "ok", "up",
    "available", "online", "reachable", "count", "total", "used", "free",
    "total_gb", "used_gb", "free_gb", "used_pct", "total_mb", "used_mb",
    "free_mb", "used_percent", "latency_ms", "latency", "devices", "ip",
    "country", "is_es", "connected", "healthy", "unhealthy", "running",
    "stopped", "port", "size", "modified_at", "timestamp", "ts",
}
_SENSITIVE_TEXT = re.compile(
    r"(?i)(?:\b(?:token|password|passwd|pwd|secret|authorization|cookie|credentials?)\b|"
    r"api[\s_-]*key|bearer\s+|basic\s+|(?:access[\s_-]*token|refresh[\s_-]*token|"
    r"id[\s_-]*token|token|password|passwd|pwd|secret|api[\s_-]*key|"
    r"authorization|cookie|set[\s_-]*cookie|credential|session[\s_-]*id)\s*[:=]|"
    r"-----BEGIN[^\n]*PRIVATE KEY-----|ssh-(?:rsa|ed25519)\s+|"
    r"https?://[^\s/]+@|(?:sk-|gh[pousr]_|AIza)[A-Za-z0-9_-]+|"
    r"\d{6,}:[A-Za-z0-9_-]{20,}|[A-Za-z0-9_+/=-]{24,})"
)
_MAX_BYTES = 1024 * 1024
_MAX_ITEMS = 100


def _approved_path(path: Path) -> bool:
    """Only the two configured snapshots and direct JSON incidents are readable."""
    return path in (STATE_FILE, AGENT_STATUS_FILE) or (
        path.parent == INCIDENTS_DIR and path.suffix == ".json"
    )


def _open_snapshot(path: Path):
    """On POSIX, pin every ancestor and refuse link traversal at open time."""
    if os.name != "posix":
        return path.open("rb")
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                            dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=directory)
        try:
            return os.fdopen(descriptor, "rb")
        except Exception:
            os.close(descriptor)
            raise
    finally:
        os.close(directory)


def _load_json(path: Path, default: Any) -> Any:
    try:
        if not isinstance(path, Path) or not _approved_path(path):
            return default
        absolute = path.absolute()
        # Reject symbolic links, junctions and hard links, including ancestors.
        if absolute.resolve(strict=True) != absolute:
            return default
        for component in (absolute, *absolute.parents):
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return default
        before = absolute.lstat()
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > _MAX_BYTES:
            return default
        with _open_snapshot(absolute) as stream:
            opened = os.fstat(stream.fileno())
            if (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
                return default
            if not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1:
                return default
            data = stream.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            return default
        return json.loads(data.decode("utf-8"))
    except (OSError, ValueError, TypeError, RecursionError):
        return default


def _sanitize(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return None
    if isinstance(value, dict):
        return {
            k: _sanitize(v, depth + 1)
            for k, v in list(value.items())[:_MAX_ITEMS]
            if isinstance(k, str) and len(k) <= 80
            and not any(marker in re.sub(r"[^a-z0-9]", "", unquote(k).lower()) for marker in _SECRET_MARKERS)
            and k.lower() not in _PRIVATE_FIELDS
            and not _SENSITIVE_TEXT.search(unquote(k))
        }
    if isinstance(value, list):
        return [_sanitize(v, depth + 1) for v in value[:_MAX_ITEMS]]
    if isinstance(value, str):
        decoded = unquote(unquote(value))
        if _SENSITIVE_TEXT.search(decoded):
            return "[REDACTED]"
        return value[:240]
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value if abs(value) <= 2**53 else None
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    return None


def _project(value: Any, depth: int = 0) -> Any:
    if depth > 12:
        return None
    if isinstance(value, dict):
        return {key: _project(item, depth + 1) for key, item in list(value.items())[:_MAX_ITEMS]
                if key in _OBSERVABLE_FIELDS}
    if isinstance(value, list):
        return [_project(item, depth + 1) for item in value[:_MAX_ITEMS]]
    return _sanitize(value)


def _text(value: Any) -> str:
    return _sanitize(value) if isinstance(value, str) else ""


def _state() -> dict:
    data = _load_json(STATE_FILE, {})
    return data if isinstance(data, dict) else {}


def get_containers() -> dict:
    containers = _state().get("containers", [])
    safe = []
    if isinstance(containers, list):
        for item in containers[:_MAX_ITEMS]:
            if not isinstance(item, dict):
                continue
            safe.append({
                "name": _text(item.get("name", "")),
                "up": item.get("up") is True,
                "status": _text(item.get("status", "")),
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
        "ollama_notebook": _project(state.get("ollama_notebook", {})),
        "ollama_nas": _project(state.get("ollama_nas", {})),
        "model": _project(state.get("model", state.get("models", {}))),
    })


def get_storage() -> dict:
    state = _state()
    infra = _load_json(AGENT_STATUS_FILE, {})
    if not isinstance(infra, dict):
        infra = {}
    return _sanitize({
        "disk": _project(state.get("disk", state.get("storage", {}))),
        "infra_disk": _project(infra.get("raw", {})) if infra.get("sub") == "disk" else {},
    })


def get_network_health() -> dict:
    state = _state()
    return _sanitize({
        "internet": _project(state.get("internet", {})),
        "vpn": _project(state.get("vpn", {})),
        "network_devices": _project(state.get("network_devices", {})),
    })


def get_recent_alerts() -> dict:
    alerts = []
    if INCIDENTS_DIR.exists() and INCIDENTS_DIR.resolve() == INCIDENTS_DIR.absolute():
        try:
            files = sorted(
                islice(INCIDENTS_DIR.glob("*.json"), 1000),
                key=lambda p: p.lstat().st_mtime,
                reverse=True,
            )[:20]
        except OSError:
            files = []
        for path in files:
            inc = _load_json(path, {})
            if not isinstance(inc, dict):
                continue
            alerts.append({
                "id": _text(inc.get("incident_id", "")),
                "title": _text(inc.get("title", "")),
                "severity": _text(inc.get("severity", "")),
                "status": _text(inc.get("status", "")),
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
        "timestamp": _project(state.get("ts", state.get("timestamp", "")))
        if isinstance(state.get("ts", state.get("timestamp", "")), (str, int, float)) else "",
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
    if not isinstance(name, str) or name not in TOOLS or name not in _HANDLERS:
        raise ValueError("tool not allowed")
    return _HANDLERS[name]()
