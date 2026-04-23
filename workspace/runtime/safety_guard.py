#!/usr/bin/env python3
"""
Safety Guard — Central safety validation for Tenazas runtime.

All modules should call validate_action() before suggesting destructive actions.
Defines BLOCKED and ALLOWED action lists. Logs every validation to logs/safety.log.
"""

import json
import re
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "safety.log"

# ── Constants ────────────────────────────────────────────────────

ARR_CONTAINERS = {
    "gluetun", "qbittorrent", "sonarr", "radarr", "lidarr",
    "prowlarr", "seerr", "requestrr", "tautulli", "sabnzbd",
}

PROTECTED_CONTAINERS = {"openclaw", "watchtower"}

BLOCKED_ACTIONS = {
    "docker exec",
    "docker rm",
    "docker build",
    "docker pull",
    "network config change",
    "file delete outside runtime",
}

ALLOWED_ACTIONS = {
    "docker ps",
    "docker restart",
    "docker start",
    "docker stop",
    "file read",
    "file write runtime",
}

RUNTIME_DIR = BASE_DIR
CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    """Append a JSON line to safety.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def validate_action(action: str, target: str = "", context: str = "") -> dict:
    """
    Validate whether an action is safe to execute.

    Args:
        action: The action type (e.g. 'docker restart', 'file delete', 'docker exec')
        target: The target (container name, file path, etc.)
        context: Optional description of why this action is being requested

    Returns:
        dict with keys: allowed (bool), reason (str)
    """
    ts = _now()
    result = _check_action(action, target)

    _log({
        "ts": ts.isoformat(),
        "action": action,
        "target": target,
        "context": context,
        "allowed": result["allowed"],
        "reason": result["reason"],
    })

    return result


def _check_action(action: str, target: str) -> dict:
    """Internal validation logic."""
    action_lower = action.lower().strip()

    # Check blocked actions first
    for blocked in BLOCKED_ACTIONS:
        if action_lower.startswith(blocked):
            return {
                "allowed": False,
                "reason": f"Action '{action}' is in the blocked list",
            }

    # Docker restart/start/stop — validate container
    if action_lower in ("docker restart", "docker start", "docker stop"):
        if not target:
            return {"allowed": False, "reason": "No target container specified"}

        if not CONTAINER_NAME_RE.match(target.strip()):
            return {"allowed": False, "reason": f"Invalid container name: {target!r}"}

        if target.strip().lower() in PROTECTED_CONTAINERS:
            return {
                "allowed": False,
                "reason": f"Container '{target}' is protected and cannot be modified",
            }

        if target.strip().lower() not in ARR_CONTAINERS:
            return {
                "allowed": False,
                "reason": f"Container '{target}' is not in the arr-stack whitelist",
            }

        return {"allowed": True, "reason": "Container action permitted on arr-stack member"}

    # Docker ps — always allowed
    if action_lower == "docker ps":
        return {"allowed": True, "reason": "Read-only Docker query"}

    # File read — always allowed
    if action_lower == "file read":
        return {"allowed": True, "reason": "File read is always permitted"}

    # File write — only within runtime directory
    if action_lower == "file write" or action_lower == "file write runtime":
        if target:
            target_path = Path(target).resolve()
            runtime_resolved = RUNTIME_DIR.resolve()
            try:
                target_path.relative_to(runtime_resolved)
                return {"allowed": True, "reason": "File write within runtime directory"}
            except ValueError:
                pass
            return {
                "allowed": False,
                "reason": f"File write outside runtime directory: {target}",
            }
        return {"allowed": True, "reason": "File write within runtime directory assumed"}

    # File delete — only within runtime directory
    if action_lower.startswith("file delete"):
        if target:
            target_path = Path(target).resolve()
            runtime_resolved = RUNTIME_DIR.resolve()
            try:
                target_path.relative_to(runtime_resolved)
                return {"allowed": True, "reason": "File delete within runtime directory"}
            except ValueError:
                pass
        return {
            "allowed": False,
            "reason": "File deletion outside runtime directory is blocked",
        }

    # Unknown action — block by default
    return {
        "allowed": False,
        "reason": f"Unknown action '{action}' — blocked by default (whitelist policy)",
    }


def is_safe_container(name: str) -> bool:
    """Quick check: is this container name safe to operate on?"""
    name = name.strip().lower()
    if name in PROTECTED_CONTAINERS:
        return False
    if not CONTAINER_NAME_RE.match(name):
        return False
    return name in ARR_CONTAINERS


def get_blocked_actions() -> set:
    """Return the set of blocked action patterns."""
    return BLOCKED_ACTIONS.copy()


def get_allowed_actions() -> set:
    """Return the set of allowed action patterns."""
    return ALLOWED_ACTIONS.copy()
