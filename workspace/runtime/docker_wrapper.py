#!/usr/bin/env python3
"""
Docker Wrapper — Restricted Docker access layer for Tenazas runtime.

This is the ONLY module allowed to call subprocess for Docker commands.
All other runtime modules must go through this wrapper.

Whitelist: ps, restart, start, stop
Blocked containers: openclaw, watchtower
"""

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "docker_actions.log"

ALLOWED_ACTIONS = {"restart", "start", "stop"}
BLOCKED_CONTAINERS = {"openclaw", "watchtower"}
CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")


def _now():
    return datetime.now().astimezone().isoformat()


def _log(entry: dict):
    """Append a JSON line to the docker actions log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry) + "\n")


def sanitize_name(name: str) -> str:
    """Validate and return a safe container name, or raise ValueError."""
    name = name.strip()
    if not name:
        raise ValueError("Empty container name")
    if not CONTAINER_NAME_RE.match(name):
        raise ValueError(f"Invalid container name: {name!r}")
    if name.lower() in BLOCKED_CONTAINERS:
        raise ValueError(f"Container {name!r} is protected — action blocked")
    return name


def docker_ps() -> list:
    """Run docker ps -a and return list of {name, status, up} dicts."""
    try:
        result = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            _log({"ts": _now(), "action": "ps", "ok": False, "error": result.stderr.strip()})
            return []
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        _log({"ts": _now(), "action": "ps", "ok": False, "error": str(e)})
        return []

    containers = []
    for line in result.stdout.strip().splitlines():
        parts = line.split("\t", 1)
        if len(parts) == 2:
            name, status = parts
            containers.append({
                "name": name.strip(),
                "status": status.strip(),
                "up": status.strip().startswith("Up"),
            })
    return containers


def docker_action(action: str, container: str) -> dict:
    """Execute a whitelisted Docker action on a validated container."""
    ts = _now()

    if action not in ALLOWED_ACTIONS:
        entry = {"ts": ts, "action": action, "container": container,
                 "ok": False, "error": f"Action {action!r} not allowed"}
        _log(entry)
        return entry

    try:
        container = sanitize_name(container)
    except ValueError as e:
        entry = {"ts": ts, "action": action, "container": container,
                 "ok": False, "error": str(e)}
        _log(entry)
        return entry

    try:
        result = subprocess.run(
            ["docker", action, container],
            capture_output=True, text=True, timeout=30,
        )
        entry = {
            "ts": ts, "action": action, "container": container,
            "ok": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "exit_code": result.returncode,
        }
    except (subprocess.TimeoutExpired, FileNotFoundError) as e:
        entry = {"ts": ts, "action": action, "container": container,
                 "ok": False, "error": str(e)}

    _log(entry)
    return entry


# Convenience functions
def restart(container: str) -> dict:
    return docker_action("restart", container)

def start(container: str) -> dict:
    return docker_action("start", container)

def stop(container: str) -> dict:
    return docker_action("stop", container)
