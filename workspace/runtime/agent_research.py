#!/usr/bin/env python3
"""Agent Research — Specialized in search and learning.

Ownership: SearXNG, knowledge base, continuous learning.
"""

import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "research_memory.json"
LOG_FILE = BASE_DIR / "logs" / "agent_research.log"

OWNERSHIP = {"research", "search", "searxng", "knowledge", "learning",
             "rag", "investigate", "documentation"}


def _now(): return datetime.now().astimezone()
def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = _now().isoformat()
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


def handle_query(query: str) -> str:
    """Handle a research query."""
    try:
        from continuous_learning_loop import research
        findings = research([query])
        if findings:
            parts = [f"Research ({len(findings)} results):"]
            for f in findings[:3]:
                parts.append(f"  {f.get('title', '?')[:50]}")
            save_memory_event({"query": query, "results": len(findings)})
            return "\n".join(parts)
    except Exception:
        pass
    return "No pude investigar ahora — SearXNG no disponible."


def get_memory() -> dict: return _load(MEMORY_FILE, {"events": []})
def save_memory_event(event: dict):
    mem = get_memory()
    mem["events"].append({**event, "ts": _now().isoformat()})
    mem["events"] = mem["events"][-100:]
    _save(MEMORY_FILE, mem)
