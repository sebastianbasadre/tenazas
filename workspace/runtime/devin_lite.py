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

try:
    from .state_paths import private_state_path
except ImportError:
    from state_paths import private_state_path
from runtime_utils import now
try:
    from .runtime_utils import load_json
except ImportError:
    from runtime_utils import load_json

BASE_DIR = Path(__file__).parent
CRASH_FILE = BASE_DIR / "logs" / "runtime_crash.json"
EVENTS_FILE = BASE_DIR / "events.json"
STATE_FILE = private_state_path(BASE_DIR)
MAX_CRASHES = 50



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
        system_state = load_json(STATE_FILE, {})

    events_data = load_json(EVENTS_FILE, {})
    recent_events = events_data.get("events", []) if isinstance(events_data, dict) else []

    crash = {
        "timestamp": now().isoformat(),
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
    crashes = load_json(CRASH_FILE, [])
    if not isinstance(crashes, list):
        crashes = []
    crashes.append(crash)
    if len(crashes) > MAX_CRASHES:
        crashes = crashes[-MAX_CRASHES:]

    with open(CRASH_FILE, "w") as f:
        json.dump(crashes, f, indent=2, default=str)


def get_recent_crashes(limit: int = 10) -> list:
    """Get the most recent crash reports."""
    crashes = load_json(CRASH_FILE, [])
    if not isinstance(crashes, list):
        return []
    return crashes[-limit:]


def get_crash_summary() -> dict:
    """Summarize recent crashes."""
    crashes = load_json(CRASH_FILE, [])
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


# ── Improvement Suggestions Engine ──────────────────────────────
# Analyzes crashes + logs to detect recurring patterns and suggest fixes.
# NEVER auto-modifies infrastructure. Only produces suggestions.

SUGGESTIONS_FILE = BASE_DIR / "logs" / "devin_suggestions.json"

KNOWN_PATTERNS = {
    "TimeoutError": {
        "description": "Runtime timeout — ciclo excedió el límite",
        "suggestion": "Verificar nmap scan, subprocess calls, o red lenta",
        "action": "Revisar logs de executor y network_cache",
    },
    "ConnectionRefusedError": {
        "description": "Servicio no responde",
        "suggestion": "Verificar Ollama, ChromaDB, o Telegram API",
        "action": "Chequear docker ps y ollama status",
    },
    "FileNotFoundError": {
        "description": "Archivo faltante",
        "suggestion": "Verificar paths y volúmenes Docker",
        "action": "Revisar docker-compose volumes",
    },
    "JSONDecodeError": {
        "description": "Datos corruptos en JSON",
        "suggestion": "Regenerar state files (se auto-crean en próximo ciclo)",
        "action": "Verificar disk space y permisos",
    },
    "PermissionError": {
        "description": "Error de permisos",
        "suggestion": "Verificar usuario tenazas (uid 1026) y grupo docker",
        "action": "Revisar permisos de /var/run/docker.sock",
    },
}


def analyze_and_suggest() -> list:
    """Analyze recent crashes and generate improvement suggestions.

    Returns:
        List of suggestion dicts. Never auto-modifies anything.
    """
    crashes = load_json(CRASH_FILE, [])
    if not isinstance(crashes, list) or not crashes:
        return []

    # Count error types from recent crashes (last 20)
    recent = crashes[-20:]
    error_counts = {}
    for c in recent:
        et = c.get("error_type", "Unknown")
        error_counts[et] = error_counts.get(et, 0) + 1

    suggestions = []
    for error_type, count in sorted(error_counts.items(), key=lambda x: -x[1]):
        if count < 2:
            continue  # Only suggest for recurring issues

        pattern = KNOWN_PATTERNS.get(error_type, {
            "description": f"Error recurrente: {error_type}",
            "suggestion": "Revisar logs para más detalle",
            "action": "Analizar stacktrace en runtime_crash.json",
        })

        suggestions.append({
            "error_type": error_type,
            "occurrences": count,
            "description": pattern["description"],
            "suggestion": pattern["suggestion"],
            "action": pattern["action"],
            "last_seen": next(
                (c["timestamp"] for c in reversed(recent) if c.get("error_type") == error_type),
                "",
            ),
        })

    # Save suggestions
    if suggestions:
        SUGGESTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SUGGESTIONS_FILE, "w") as f:
            json.dump({
                "ts": now().isoformat(),
                "suggestions": suggestions,
            }, f, indent=2, default=str)

    return suggestions


def format_suggestions_telegram(suggestions: list) -> str:
    """Format suggestions for Telegram notification."""
    if not suggestions:
        return ""

    lines = ["🔍 Devin-Lite — Sugerencias de mejora:\n"]
    for s in suggestions[:5]:  # Max 5 in one message
        lines.append(
            f"⚠️ {s['error_type']} ({s['occurrences']}x)\n"
            f"  {s['description']}\n"
            f"  → {s['suggestion']}\n"
        )
    return "\n".join(lines)
