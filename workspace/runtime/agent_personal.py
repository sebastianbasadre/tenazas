#!/usr/bin/env python3
"""Agent Personal — Specialized in Sebas's daily life.

Ownership: calendar, email, reminders, smoking, briefing.
"""

import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now, load_json, save_json
from agent_protocol import Observation, Recommendation

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "personal_memory.json"
LOG_FILE = BASE_DIR / "logs" / "agent_personal.log"

OWNERSHIP = {"calendar", "email", "reminder", "smoking", "briefing", "agenda",
             "morning", "greeting", "habit", "schedule"}
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
    status = {"agent": "personal", "ts": now().isoformat(), "items": []}
    try:
        from notification_poller import check_calendar
        events = check_calendar()
        if events:
            status["items"].append(f"{len(events)} eventos hoy")
    except Exception: pass
    try:
        from notification_poller import check_email
        emails = check_email()
        if emails:
            status["items"].append(f"{len(emails)} emails")
    except Exception: pass
    _log({"action": "assess", "items": len(status["items"])})
    return status


def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    status = assess()
    items = status.get("items", [])
    obs = Observation(
        agent_name="personal",
        domain="personal",
        summary=f"Personal: {len(items)} items" if items else "Personal: nada pendiente",
        confidence=0.8,
        details=status,
    )
    recs = []
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
