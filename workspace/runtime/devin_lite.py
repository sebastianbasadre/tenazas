#!/usr/bin/env python3
"""
Devin-Lite — Automatic crash capture and debugging for Tenazas runtime.

When runtime fails or times out:
  1. Captures exception + stacktrace
  2. Saves recent events from events.json
  3. Saves system state snapshot from state.json
  4. Writes structured crash report to logs/runtime_crash.json

Reports are consumed by self_dev_engine for improvement proposals.
"""

import json
import traceback
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
CRASH_FILE = BASE_DIR / "logs" / "runtime_crash.json"
EVENTS_FILE = BASE_DIR / "events.json"
STATE_FILE = BASE_DIR / "state.json"
MAX_CRASHES = 50


def _now():
    return datetime.now().astimezone()


def _load_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def capture_crash(
    error: Exception,
    system_state: dict | None = None,
    extra: dict | None = None,
):
    """Capture a runtime crash with full context.

    Args:
        error: the exception that caused the crash
        system_state: current state.json snapshot (optional)
        extra: any additional context
    """
    if system_state is None:
        system_state = _load_json(STATE_FILE, {})

    events_data = _load_json(EVENTS_FILE, {})
    recent_events = events_data.get("events", []) if isinstance(events_data, dict) else []

    crash = {
        "timestamp": _now().isoformat(),
        "error": str(error),
        "error_type": type(error).__name__,
        "stacktrace": traceback.format_exc(),
        "recent_events": recent_events[-10:],
        "system_state": {
            "ts": system_state.get("ts", ""),
            "internet": system_state.get("internet", {}),
            "vpn": system_state.get("vpn", {}),
            "ollama_notebook": system_state.get("ollama_notebook", {}),
            "containers_count": len(system_state.get("containers", [])),
            "containers_down": [
                c["name"] for c in system_state.get("containers", [])
                if not c.get("up", True)
            ],
        },
    }

    if extra:
        crash["extra"] = extra

    # Append to crash log (keep last MAX_CRASHES)
    CRASH_FILE.parent.mkdir(parents=True, exist_ok=True)
    crashes = _load_json(CRASH_FILE, [])
    if not isinstance(crashes, list):
        crashes = []
    crashes.append(crash)
    if len(crashes) > MAX_CRASHES:
        crashes = crashes[-MAX_CRASHES:]

    with open(CRASH_FILE, "w") as f:
        json.dump(crashes, f, indent=2, default=str)


def get_recent_crashes(limit: int = 10) -> list:
    """Get the most recent crash reports."""
    crashes = _load_json(CRASH_FILE, [])
    if not isinstance(crashes, list):
        return []
    return crashes[-limit:]


def get_crash_summary() -> dict:
    """Summarize recent crashes."""
    crashes = _load_json(CRASH_FILE, [])
    if not isinstance(crashes, list) or not crashes:
        return {"total": 0, "recent": []}

    error_types = {}
    for c in crashes:
        et = c.get("error_type", "Unknown")
        error_types[et] = error_types.get(et, 0) + 1

    return {
        "total": len(crashes),
        "error_types": error_types,
        "last_crash": crashes[-1].get("timestamp", ""),
        "last_error": crashes[-1].get("error", ""),
    }
