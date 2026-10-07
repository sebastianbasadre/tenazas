#!/usr/bin/env python3
"""Read-only adapter for the Tenazas MCP boundary.

This module intentionally reads precomputed/cached state only. It does not import
live collectors because those can execute infrastructure commands.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import UTC
from pathlib import Path
from typing import Any

RUNTIME = Path(__file__).resolve().parent
CACHE_ROOT = RUNTIME / "mcp-cache"
STATE_FILE = CACHE_ROOT / "state.json"

TOOLS = (
    "get_status",
    "get_containers",
    "get_models",
    "get_storage",
    "get_network_health",
    "get_recent_alerts",
)

_MAX_BYTES = 1024 * 1024


def _approved_path(path: Path) -> bool:
    return path == STATE_FILE


def _open_snapshot(path: Path):
    if os.name != "posix":
        return path.open("rb")
    directory = os.open(path.anchor, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in path.parts[1:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(
            path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory
        )
        try:
            return os.fdopen(descriptor, "rb")
        except Exception:
            os.close(descriptor)
            raise
    finally:
        os.close(directory)


def _read_snapshot(path: Path, default: Any) -> Any:
    try:
        if not isinstance(path, Path):
            return default
        absolute = path.absolute()
        if absolute.resolve(strict=True) != absolute:
            return default
        for component in (absolute, *absolute.parents):
            info = component.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                return default
        with _open_snapshot(absolute) as stream:
            opened = os.fstat(stream.fileno())
            # The opened descriptor is the generation boundary. A legitimate
            # atomic rename between path checks and open must not reject it.
            if (
                not stat.S_ISREG(opened.st_mode)
                or opened.st_nlink != 1
                or opened.st_size > _MAX_BYTES
            ):
                return default
            if os.name == "posix":
                from workspace.runtime.mcp_cache_policy import expected_identity

                if (
                    stat.S_IMODE(opened.st_mode) != 0o600
                    or (opened.st_uid, opened.st_gid) != expected_identity()
                ):
                    return default
            data = stream.read(_MAX_BYTES + 1)
        if len(data) > _MAX_BYTES:
            return default
        return json.loads(data.decode("utf-8"))
    except (OSError, ValueError, TypeError, RecursionError):
        return default


def _load_json(path: Path, default: Any) -> Any:
    return _read_snapshot(path, default) if _approved_path(path) else default


def _state() -> dict:
    from workspace.runtime.mcp_snapshot_contract import validate_snapshot

    # A rename can unlink an already-open old descriptor before its fstat.
    # Retry boundedly instead of relaxing the single-link/owner checks.
    for _ in range(3):
        data = _load_json(STATE_FILE, None)
        if data is not None:
            return validate_snapshot(data)
    raise ValueError("Snapshot unavailable")


def configure_cache(root):
    """Startup-only configuration; never accepts paths from MCP arguments."""
    from workspace.runtime.mcp_cache_policy import validate_cache

    global CACHE_ROOT, STATE_FILE
    root = Path(root).absolute()
    validate_cache(root)
    CACHE_ROOT, STATE_FILE = root, root / "state.json"


def _result(name, state, now):
    from workspace.runtime.mcp_snapshot_contract import freshness

    source = None
    if name == "get_status":
        result = {
            key: _result(tool, state, now)
            for key, tool in (
                ("containers", "get_containers"),
                ("models", "get_models"),
                ("storage", "get_storage"),
                ("network", "get_network_health"),
                ("alerts", "get_recent_alerts"),
            )
        }
        result["timestamp"] = state["generated_at"]
    elif name == "get_containers":
        items = state["containers"]
        result = {
            "total": len(items),
            "up": sum(c["up"] for c in items),
            "down": sum(not c["up"] for c in items),
            "containers": items,
        }
        source = "containers"
    elif name == "get_models":
        result = {k: state[k] for k in ("ollama_notebook", "ollama_nas", "model")}
        source = "models"
    elif name == "get_storage":
        result = {"disk": state["disk"], "infra_disk": {}}
        source = "storage"
    elif name == "get_network_health":
        result = {k: state[k] for k in ("internet", "vpn", "network_devices")}
        source = "network"
    else:
        result = {"alerts": [], "source_active": False}
    meta = freshness(state, now, source)
    if name == "get_status" and any(
        freshness(state, now, s)["state"] == "stale" for s in state["sources"]
    ):
        meta["state"] = "stale"
    return {**result, "snapshot": meta}


def call_tool(name):
    from datetime import datetime

    if not isinstance(name, str) or name not in TOOLS:
        raise ValueError("Tool forbidden")
    return _result(name, _state(), datetime.now(UTC))


def get_status():
    return call_tool("get_status")


def get_containers():
    return call_tool("get_containers")


def get_models():
    return call_tool("get_models")


def get_storage():
    return call_tool("get_storage")


def get_network_health():
    return call_tool("get_network_health")


def get_recent_alerts():
    return call_tool("get_recent_alerts")
