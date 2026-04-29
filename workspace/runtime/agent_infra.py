#!/usr/bin/env python3
"""Agent Infra — Specialized in homelab infrastructure.

Ownership: containers, GPU, RAM, disk, network, VPN.
Reads snapshot, uses predictor + root_cause_engine, proposes via action_registry.
"""

import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now, load_json, save_json
from agent_protocol import Observation, Recommendation

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "infra_memory.json"
STATUS_FILE = AGENT_DIR / "infra_status.json"
LOG_FILE = BASE_DIR / "logs" / "agent_infra.log"

OWNERSHIP = {"container", "containers", "gpu", "ram", "disk", "network", "vpn",
             "ollama", "docker", "gluetun", "infra", "homelab", "backend_offline"}
BLOCKED_TARGETS = {"openclaw", "watchtower"}
PREFERRED_ROLE = "fast"


def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass


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

    save_json(str(STATUS_FILE), status)
    _log({"action": "assess", "health": status["health"], "proposals": len(status["proposals"])})
    return status


def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    status = assess()
    obs = Observation(
        agent_name="infra",
        domain="infrastructure",
        summary=f"Infra: {status['health']}" + (f" - {', '.join(status['issues'][:3])}" if status.get("issues") else ""),
        confidence=0.9 if status["health"] == "ok" else 0.6,
        details=status,
    )
    recs = []
    for p in status.get("proposals", []):
        recs.append(Recommendation(
            action=p.get("action", "investigate"),
            target=p.get("target", ""),
            risk_level=p.get("severity", "low"),
            requires_approval=True,
            rationale=p.get("reason", ""),
        ))
    return (obs, recs)


def handle_query(query: str) -> str:
    """Backward-compat wrapper. Returns string summary."""
    obs, recs = handle(query)
    return obs.summary


def get_memory() -> dict: return load_json(str(MEMORY_FILE), {"events": []})
def save_memory_event(event: dict):
    mem = get_memory()
    mem["events"].append({**event, "ts": now().isoformat()})
    mem["events"] = mem["events"][-100:]
    save_json(str(MEMORY_FILE), mem)
