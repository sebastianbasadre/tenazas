#!/usr/bin/env python3
"""Agent Research — Specialized in search and learning.

Ownership: SearXNG, knowledge base, continuous learning.
"""

import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now, load_json, save_json
from agent_protocol import Observation, Recommendation

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "research_memory.json"
LOG_FILE = BASE_DIR / "logs" / "agent_research.log"

OWNERSHIP = {"research", "search", "searxng", "knowledge", "learning",
             "rag", "investigate", "documentation"}
PREFERRED_ROLE = "reason"


def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass

def owns(topic: str) -> bool:
    return any(k in topic.lower() for k in OWNERSHIP)


def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    findings = []
    summary = "Research: idle"
    try:
        from continuous_learning_loop import research
        findings = research([query]) or []
        if findings:
            summary = f"Research: {len(findings)} results"
            save_memory_event({"query": query, "results": len(findings)})
    except Exception:
        summary = "Research: SearXNG no disponible"

    obs = Observation(
        agent_name="research",
        domain="research",
        summary=summary,
        confidence=0.7,
        details={"findings": findings[:5]},
    )
    recs = []
    for f in findings[:3]:
        recs.append(Recommendation(
            action="review",
            target=f.get("topic", f.get("title", "")),
            risk_level="low",
            requires_approval=False,
            rationale=f.get("detail", f.get("title", "")),
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
