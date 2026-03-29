#!/usr/bin/env python3
"""
Executors — Monitoring probes and task execution for Tenazas runtime.

Probes run read-only system commands (ping, curl, nmap).
Task execution routes through docker_wrapper — never calls Docker directly.
"""

import json
import re
import subprocess
from datetime import datetime
from pathlib import Path

from . import docker_wrapper

BASE_DIR = Path(__file__).parent
EXECUTOR_LOG = BASE_DIR / "logs" / "executor.log"

# ── Endpoints (from HEARTBEAT.md / infra-monitor) ────────────────

OLLAMA_NOTEBOOK = "http://192.168.2.76:11434/api/tags"
OLLAMA_NAS = "http://127.0.0.1:11434/api/tags"
GLUETUN_PROXY = "http://192.168.2.2:8888"
IFCONFIG_URL = "https://ifconfig.me"
NETWORK_SUBNET = "192.168.2.0/24"


def _now():
    return datetime.now().astimezone().isoformat()


def _log_exec(entry: dict):
    """Append a JSON line to executor.log."""
    EXECUTOR_LOG.parent.mkdir(parents=True, exist_ok=True)
    with open(EXECUTOR_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def _run_cmd(cmd: list, timeout: int = 15) -> subprocess.CompletedProcess:
    """Run a non-Docker shell command with timeout."""
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except (subprocess.TimeoutExpired, FileNotFoundError):
        class _Fail:
            returncode = -1
            stdout = ""
            stderr = "timeout_or_not_found"
        return _Fail()


# ── Monitoring Probes ─────────────────────────────────────────────

def check_containers() -> list:
    """List all containers with status."""
    return docker_wrapper.docker_ps()


def check_internet() -> dict:
    """Ping 8.8.8.8 — returns {ok, latency_ms}."""
    r = _run_cmd(["ping", "-c", "1", "-W", "3", "8.8.8.8"])
    if r.returncode == 0:
        match = re.search(r"time[=<](\d+\.?\d*)", r.stdout)
        latency = float(match.group(1)) if match else None
        return {"ok": True, "latency_ms": latency}
    return {"ok": False, "latency_ms": None}


def check_vpn() -> dict:
    """Get VPN IP via gluetun proxy."""
    r = _run_cmd(
        ["curl", "-sf", "--max-time", "10", "-x", GLUETUN_PROXY, IFCONFIG_URL],
        timeout=15,
    )
    ip = r.stdout.strip() if r.returncode == 0 else None
    return {"ip": ip, "ok": ip is not None}


def check_public_ip() -> dict:
    """Get public IP (no proxy)."""
    r = _run_cmd(["curl", "-sf", "--max-time", "10", IFCONFIG_URL], timeout=15)
    ip = r.stdout.strip() if r.returncode == 0 else None
    return {"ip": ip}


def check_ollama_notebook() -> dict:
    """Check Ollama on notebook GPU (192.168.2.76)."""
    r = _run_cmd(["curl", "-sf", "--max-time", "5", OLLAMA_NOTEBOOK], timeout=10)
    return {"ok": r.returncode == 0}


def check_ollama_nas() -> dict:
    """Check Ollama on NAS CPU (localhost)."""
    r = _run_cmd(["curl", "-sf", "--max-time", "5", OLLAMA_NAS], timeout=10)
    return {"ok": r.returncode == 0}


def check_network_devices() -> dict:
    """Count devices on LAN via nmap."""
    r = _run_cmd(
        ["nmap", "-sn", NETWORK_SUBNET, "--host-timeout", "3s"],
        timeout=20,
    )
    count = 0
    if r.returncode == 0:
        count = r.stdout.count("Host is up")
    return {"count": count}


# ── Task Execution ────────────────────────────────────────────────

TASK_HANDLERS = {
    "docker.restart": lambda t: docker_wrapper.restart(t["target"]),
    "docker.start": lambda t: docker_wrapper.start(t["target"]),
    "docker.stop": lambda t: docker_wrapper.stop(t["target"]),
}


def execute_task(task: dict) -> dict:
    """Execute a single task and return the result."""
    action = task.get("action", "")
    handler = TASK_HANDLERS.get(action)

    if not handler:
        result = {"ok": False, "error": f"Unknown action: {action!r}"}
    else:
        result = handler(task)

    entry = {
        "ts": _now(),
        "action": action,
        "target": task.get("target", ""),
        "reason": task.get("reason", ""),
        "ok": result.get("ok", False),
        "detail": result.get("error", result.get("stdout", "")),
    }
    _log_exec(entry)
    return entry
