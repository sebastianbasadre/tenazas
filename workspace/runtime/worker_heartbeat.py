#!/usr/bin/env python3
"""
Worker Heartbeat — Tracks worker progress via timestamp file.

Each worker calls heartbeat() periodically. The supervisor checks
if any worker's heartbeat is stale (>60s without update).

Heartbeat file: workspace/runtime/logs/worker_heartbeat.json

Usage (in worker):
    from runtime.worker_heartbeat import heartbeat
    heartbeat("runtime_worker", "update_state")

Usage (in supervisor):
    from runtime.worker_heartbeat import check_all, is_frozen
    frozen = check_all()
"""

import json
import time
from pathlib import Path

BASE_DIR = Path(__file__).parent
HEARTBEAT_FILE = BASE_DIR / "logs" / "worker_heartbeat.json"

FREEZE_THRESHOLD = 60  # seconds without heartbeat = frozen


def _load() -> dict:
    try:
        with open(HEARTBEAT_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict):
    HEARTBEAT_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(HEARTBEAT_FILE, "w") as f:
        json.dump(data, f)


def heartbeat(worker: str, step: str = ""):
    """Record a heartbeat from a worker.

    Args:
        worker: Worker name (e.g. "runtime_worker")
        step: Current step name (e.g. "update_state")
    """
    data = _load()
    data[worker] = {
        "ts": time.time(),
        "step": step,
        "pid": __import__("os").getpid(),
    }
    _save(data)


def is_frozen(worker: str) -> bool:
    """Check if a specific worker's heartbeat is stale."""
    data = _load()
    entry = data.get(worker, {})
    ts = entry.get("ts", 0)
    if ts == 0:
        return False  # Never started, not frozen
    return (time.time() - ts) > FREEZE_THRESHOLD


def check_all() -> list:
    """Check all workers for freeze.

    Returns: list of frozen worker dicts with details.
    """
    data = _load()
    frozen = []
    now = time.time()

    for worker, entry in data.items():
        ts = entry.get("ts", 0)
        if ts > 0 and (now - ts) > FREEZE_THRESHOLD:
            frozen.append({
                "worker": worker,
                "last_step": entry.get("step", ""),
                "since_seconds": round(now - ts, 1),
                "pid": entry.get("pid"),
            })

    return frozen


def clear(worker: str = ""):
    """Clear heartbeat for a worker (or all if empty)."""
    if worker:
        data = _load()
        data.pop(worker, None)
        _save(data)
    else:
        _save({})


def get_status() -> dict:
    """Get all heartbeat status for monitoring."""
    data = _load()
    now = time.time()
    status = {}
    for worker, entry in data.items():
        ts = entry.get("ts", 0)
        status[worker] = {
            "step": entry.get("step", ""),
            "age_seconds": round(now - ts, 1) if ts > 0 else -1,
            "frozen": (now - ts) > FREEZE_THRESHOLD if ts > 0 else False,
        }
    return status
