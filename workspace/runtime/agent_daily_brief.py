#!/usr/bin/env python3
"""Daily Brief Agent -- consolidated personal, infra, finance, and home summary."""

import json
from pathlib import Path

from agent_protocol import Observation, Recommendation
from runtime_utils import now, save_json

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
STATUS_FILE = AGENT_DIR / "daily_brief_status.json"
LOG_FILE = BASE_DIR / "logs" / "agent_daily_brief.log"

OWNERSHIP = {"brief", "briefing", "daily", "morning", "resumen", "dia", "día",
             "today", "hoy", "overview", "status general"}
PREFERRED_ROLE = "fast"


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


def _safe_summary(module_name: str, query: str) -> str | None:
    try:
        mod = __import__(module_name)
        obs, _recs = mod.handle(query)
        return getattr(obs, "summary", None)
    except Exception:
        return None


def assess() -> dict:
    sections = []
    for module, query in (
        ("agent_personal", "calendar email"),
        ("agent_infra", "homelab containers"),
        ("agent_finance", "finance"),
        ("agent_media_stack", "media stack"),
        ("agent_home_reliability", "home devices"),
    ):
        summary = _safe_summary(module, query)
        if summary:
            sections.append(summary)
    status = {"agent": "daily_brief", "ts": now().isoformat(), "sections": sections}
    save_json(str(STATUS_FILE), status)
    _log({"action": "assess", "sections": len(sections)})
    return status


def handle(query: str, correlation_id: str = None):
    status = assess()
    summary = "Daily brief: " + " | ".join(status["sections"][:5]) if status["sections"] else "Daily brief: sin datos"
    obs = Observation("daily_brief", "personal", summary, 0.75, status)
    _log({"action": "handle", "query": query[:80], "correlation_id": correlation_id})
    return obs, []

