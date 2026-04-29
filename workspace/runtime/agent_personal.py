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


def _observe_calendar() -> dict:
    """Observe calendar events via notification_poller."""
    try:
        from notification_poller import check_calendar
        events = check_calendar()
        if events:
            titles = [e.get("summary", e.get("title", "")) for e in events[:3] if isinstance(e, dict)]
            title_str = ", ".join(t for t in titles if t)
            summary = f"Calendario: {len(events)} eventos hoy"
            if title_str:
                summary += f" — {title_str}"
            return {"summary": summary, "confidence": 0.8, "sub": "calendar",
                    "issues": [], "raw": {"events": events[:10] if events else []}}
        return {"summary": "Calendario: sin eventos pendientes", "confidence": 0.9,
                "sub": "calendar", "issues": [], "raw": {"events": []}}
    except Exception as e:
        return {"summary": f"Calendario: error ({str(e)[:60]})", "confidence": 0.3,
                "sub": "calendar", "issues": [str(e)[:100]], "raw": {}}


def _observe_email() -> dict:
    """Observe email count via notification_poller."""
    try:
        from notification_poller import check_email
        emails = check_email()
        if emails:
            return {"summary": f"Email: {len(emails)} mensajes", "confidence": 0.8,
                    "sub": "email", "issues": [],
                    "raw": {"count": len(emails), "emails": emails[:5] if emails else []}}
        return {"summary": "Email: sin mensajes nuevos", "confidence": 0.9,
                "sub": "email", "issues": [], "raw": {"count": 0, "emails": []}}
    except Exception as e:
        return {"summary": f"Email: error ({str(e)[:60]})", "confidence": 0.3,
                "sub": "email", "issues": [str(e)[:100]], "raw": {}}


def _observe_smoking() -> dict:
    """Observe smoking stats from smoking module or direct file read."""
    try:
        try:
            from smoking import get_stats
            stats = get_stats()
        except (ImportError, AttributeError):
            # Fallback: read files directly
            stats = {}
            log_data = load_json(str(BASE_DIR / "smoking" / "log.json"), {"entries": []})
            metrics_data = load_json(str(BASE_DIR / "smoking" / "metrics.json"), {})
            today_count = 0
            week_count = 0
            today_str = now().strftime("%Y-%m-%d")
            from datetime import timedelta
            week_start = (now() - timedelta(days=7)).strftime("%Y-%m-%d")
            for entry in log_data.get("entries", []):
                ts = entry.get("ts", "")
                if ts.startswith(today_str):
                    today_count += 1
                if ts[:10] >= week_start:
                    week_count += 1
            stats = {"today": today_count, "week": metrics_data.get("week", week_count),
                     "total": metrics_data.get("total", len(log_data.get("entries", [])))}
        today = stats.get("today", 0)
        week = stats.get("week", 0)
        return {"summary": f"Smoking: {today} hoy, {week} esta semana", "confidence": 0.7,
                "sub": "smoking", "issues": [], "raw": stats}
    except Exception as e:
        return {"summary": "Smoking: sin datos", "confidence": 0.3,
                "sub": "smoking", "issues": [str(e)[:100]], "raw": {}}


def _observe_reminders() -> dict:
    """Reminders are handled by skill:reminders, not by the agent directly."""
    return {"summary": "Reminders: funcionalidad via skill:reminders", "confidence": 0.5,
            "sub": "reminders", "issues": [], "raw": {}}


def _observe_all_personal() -> dict:
    """Full personal status via assess()."""
    status = assess()
    items = status.get("items", [])
    summary = f"Personal: {', '.join(items)}" if items else "Personal: nada pendiente"
    return {"summary": summary, "confidence": 0.8, "sub": "full",
            "issues": [], "raw": status}


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
    q = query.lower()
    if any(k in q for k in ("calendar", "calendario", "agenda", "evento", "reunion", "reunión")):
        data = _observe_calendar()
    elif any(k in q for k in ("email", "mail", "correo", "inbox", "mensaje")):
        data = _observe_email()
    elif any(k in q for k in ("smoking", "pucho", "cigarrillo", "fumando", "fumé", "fume")):
        data = _observe_smoking()
    elif any(k in q for k in ("reminder", "recordatorio", "recordar", "recordame")):
        data = _observe_reminders()
    else:
        data = _observe_all_personal()
    obs = Observation(
        agent_name="personal",
        domain="personal",
        summary=data.get("summary", "Personal: sin datos"),
        confidence=data.get("confidence", 0.7),
        details=data,
    )
    recs = []  # Personal domain rarely has actionable recommendations
    _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"), "confidence": obs.confidence})
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
