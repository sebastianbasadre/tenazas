#!/usr/bin/env python3
"""Runbook Agent -- incident-to-runbook suggestions."""

import json
from pathlib import Path

from agent_protocol import Observation, Recommendation
from runtime_utils import load_json, now, save_json

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
STATUS_FILE = AGENT_DIR / "runbook_status.json"
LOG_FILE = BASE_DIR / "logs" / "agent_runbook.log"
EVENTS_FILE = BASE_DIR / "events.json"

OWNERSHIP = {"runbook", "incident", "incidente", "postmortem", "diagnostic",
             "diagnostico", "diagnóstico", "root cause", "rca", "fix steps"}
PREFERRED_ROLE = "reason"


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def owns(topic: str) -> bool:
    q = topic.lower()
    return any(k in q for k in OWNERSHIP)


def assess() -> dict:
    events = load_json(str(EVENTS_FILE), [])
    if not isinstance(events, list):
        events = []
    recent = events[-10:]
    types = sorted({e.get("type", "unknown") for e in recent if isinstance(e, dict)})
    status = {"agent": "runbook", "ts": now().isoformat(), "recent_events": recent, "event_types": types}
    save_json(str(STATUS_FILE), status)
    _log({"action": "assess", "events": len(recent)})
    return status


def handle(query: str, correlation_id: str = None):
    status = assess()
    if status["event_types"]:
        summary = "Runbook: recent event types " + ", ".join(status["event_types"][:5])
    else:
        summary = "Runbook: no recent incidents"
    recs = [Recommendation("generate", "incident_runbook", "low", False,
                           "Generate or refresh runbook for recent incident pattern")] if status["event_types"] else []
    obs = Observation("runbook", "infra", summary, 0.75, status)
    _log({"action": "handle", "query": query[:80], "correlation_id": correlation_id})
    return obs, recs

