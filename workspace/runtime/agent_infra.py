#!/usr/bin/env python3
"""Agent Infra — Specialized in homelab infrastructure.

Ownership: containers, GPU, RAM, disk, network, VPN.
Reads snapshot, uses predictor + root_cause_engine, proposes via action_registry.
"""

import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "infra_memory.json"
STATUS_FILE = AGENT_DIR / "infra_status.json"
LOG_FILE = BASE_DIR / "logs" / "agent_infra.log"

OWNERSHIP = {"container", "containers", "gpu", "ram", "disk", "network", "vpn",
             "ollama", "docker", "gluetun", "infra", "homelab", "backend_offline"}
BLOCKED_TARGETS = {"openclaw", "watchtower"}


def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass

def _load(p, d=None):
    try:
        with open(p) as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return d if d is not None else {}

def _save(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f: json.dump(data, f, indent=2, default=str)


def owns(topic: str) -> bool:
    return any(k in topic.lower() for k in OWNERSHIP)


def assess() -> dict:
    """Assess current infra state. Returns status + proposals."""
    status = {"agent": "infra", "ts": now().isoformat(), "health": "ok", "proposals": [], "issues": []}
    try:
        from homelab_snapshot import snapshot
        snap = snapshot()
        c = snap.get("containers", {})
        if c.get("stopped", 0) > 0 or c.get("unhealthy", 0) > 0:
            status["health"] = "degraded"
            status["issues"].append(f"{c.get('stopped',0)} stopped, {c.get('unhealthy',0)} unhealthy")
        o = snap.get("ollama", {})
        if o.get("status") != "online":
            status["health"] = "degraded"
            status["issues"].append("ollama offline")
    except Exception as e:
        status["issues"].append(f"snapshot error: {str(e)[:100]}")

    try:
        from infra_predictor import predict_all
        preds = predict_all()
        for p in preds:
            status["proposals"].append({
                "action": p.get("recommended_action", "investigate"),
                "reason": f"prediction: {p['type']} (p={p['probability']:.0%})",
                "severity": p.get("severity", "info"),
            })
    except Exception: pass

    # Filter out protected targets
    status["proposals"] = [p for p in status["proposals"]
                           if not any(t in p.get("action", "").lower() for t in BLOCKED_TARGETS)]

    _save(STATUS_FILE, status)
    _log({"action": "assess", "health": status["health"], "proposals": len(status["proposals"])})
    return status


def handle_query(query: str) -> str:
    """Handle an infra-related query."""
    status = assess()
    if status["health"] == "ok" and not status["issues"]:
        return "Infra OK — todo verde."
    parts = [f"Infra: {status['health']}"]
    for issue in status["issues"][:3]:
        parts.append(f"  ! {issue}")
    for prop in status["proposals"][:2]:
        parts.append(f"  → {prop['action']}")
    return "\n".join(parts)


def get_memory() -> dict: return _load(MEMORY_FILE, {"events": []})
def save_memory_event(event: dict):
    mem = get_memory()
    mem["events"].append({**event, "ts": now().isoformat()})
    mem["events"] = mem["events"][-100:]
    _save(MEMORY_FILE, mem)
