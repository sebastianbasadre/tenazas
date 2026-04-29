#!/usr/bin/env python3
"""Agent Research — Specialized in search and learning.

Ownership: SearXNG, knowledge base, continuous learning.
"""

import json
import subprocess
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


def _observe_rag(query: str) -> dict:
    """Query the RAG knowledge base (fast, local)."""
    try:
        # Truncate query and strip control characters for safety (T-06-06 mitigation)
        safe_query = query[:200].replace('\x00', '').replace('\n', ' ').replace('\r', ' ')
        # Try subprocess call to RAG query.py
        result = subprocess.run(
            ["python3", "/home/tenazas/.openclaw/workspace/skills/rag/query.py",
             safe_query, "--json"],
            capture_output=True, text=True, timeout=10
        )
        if result.returncode == 0 and result.stdout.strip():
            try:
                data = json.loads(result.stdout.strip())
                results = data.get("results", data.get("chunks", []))
                if results:
                    return {"summary": f"RAG: {len(results)} results for '{query[:30]}'",
                            "confidence": 0.7, "sub": "rag", "issues": [],
                            "raw": {"results": results[:5]}}
            except json.JSONDecodeError:
                pass
        return {"summary": f"RAG: sin resultados para '{query[:30]}'", "confidence": 0.4,
                "sub": "rag", "issues": [], "raw": {"results": []}}
    except Exception as e:
        return {"summary": "RAG: no disponible", "confidence": 0.3,
                "sub": "rag", "issues": [str(e)[:100]], "raw": {}}


def _observe_research(query: str) -> dict:
    """Search via SearXNG through continuous_learning_loop."""
    try:
        from continuous_learning_loop import research
        findings = research([query]) or []
        if findings:
            save_memory_event({"query": query, "results": len(findings)})
            return {"summary": f"Research: {len(findings)} results", "confidence": 0.7,
                    "sub": "research", "issues": [],
                    "raw": {"findings": findings[:5]}}
        return {"summary": "Research: sin resultados", "confidence": 0.4,
                "sub": "research", "issues": [], "raw": {"findings": []}}
    except Exception as e:
        return {"summary": "Research: SearXNG no disponible", "confidence": 0.3,
                "sub": "research", "issues": [str(e)[:100]], "raw": {}}


def _observe_full(query: str) -> dict:
    """Try RAG first (fast, local), then SearXNG if RAG yields nothing."""
    rag_data = _observe_rag(query)
    rag_results = rag_data.get("raw", {}).get("results", [])
    if rag_results:
        return rag_data
    # RAG returned nothing, try SearXNG
    research_data = _observe_research(query)
    research_findings = research_data.get("raw", {}).get("findings", [])
    if research_findings:
        return research_data
    # Both empty — combine info
    return {"summary": f"Research: sin resultados para '{query[:30]}'", "confidence": 0.3,
            "sub": "full", "issues": rag_data.get("issues", []) + research_data.get("issues", []),
            "raw": {"results": [], "findings": []}}


def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    q = query.lower()
    if any(k in q for k in ("rag", "knowledge", "knowledge base", "conocimiento")):
        data = _observe_rag(query)
    elif any(k in q for k in ("research", "investigar", "buscar", "searxng", "search")):
        data = _observe_research(query)
    else:
        data = _observe_full(query)
    obs = Observation(
        agent_name="research",
        domain="research",
        summary=data.get("summary", "Research: sin datos"),
        confidence=data.get("confidence", 0.5),
        details=data,
    )
    recs = []
    for f in data.get("raw", {}).get("findings", data.get("raw", {}).get("results", []))[:3]:
        if isinstance(f, dict):
            recs.append(Recommendation(
                action="review",
                target=f.get("topic", f.get("title", "")),
                risk_level="low",
                requires_approval=False,
                rationale=f.get("detail", f.get("title", "")),
            ))
    if recs:
        _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"), "recs": len(recs)})
    else:
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
