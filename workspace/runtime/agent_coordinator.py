#!/usr/bin/env python3
"""Agent Coordinator — Routes queries to appropriate agent(s).

Additive to orchestrator.py — activates only for multi-domain queries.
Simple queries go through orchestrator directly.

Usage:
    python3 agent_coordinator.py --route "como esta todo"
    python3 agent_coordinator.py --agents   # list agents + status
"""

import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
COORD_LOG = STATE_DIR / "agents" / "coordinator_log.json"
LOG_FILE = BASE_DIR / "logs" / "agent_coordinator.log"

# Import agents lazily to avoid circular imports
AGENT_MODULES = {
    "infra": "agent_infra",
    "personal": "agent_personal",
    "finance": "agent_finance",
    "research": "agent_research",
    "self_improve": "agent_self_improve",
}

# Multi-domain triggers
MULTI_DOMAIN_QUERIES = [
    "como esta todo", "como va todo", "estado general", "resumen completo",
    "que deberia saber", "que paso hoy", "update", "dame el status",
]


def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass

def _save_coord_log(entry: dict):
    COORD_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = []
        if COORD_LOG.exists():
            existing = json.loads(COORD_LOG.read_text())
            if not isinstance(existing, list):
                existing = []
        existing.append(entry)
        existing = existing[-200:]
        with open(COORD_LOG, "w") as f:
            json.dump(existing, f, indent=2, default=str)
    except Exception:
        pass


def _load_agent(name: str):
    """Lazily import an agent module."""
    try:
        import importlib
        return importlib.import_module(AGENT_MODULES[name])
    except Exception:
        return None


# ── Core API ────────────────────────────────────────────────────


def is_multi_domain(query: str) -> bool:
    """Check if query requires multiple agents."""
    q = query.lower().strip()
    if any(trigger in q for trigger in MULTI_DOMAIN_QUERIES):
        return True
    # Count how many agents own this topic
    matching = identify_agents(query)
    return len(matching) > 1


def identify_agents(query: str) -> list:
    """Identify which agent(s) should handle this query."""
    q = query.lower()
    matching = []

    for name, module_name in AGENT_MODULES.items():
        agent = _load_agent(name)
        if agent and hasattr(agent, "owns") and agent.owns(q):
            matching.append(name)

    # If none matched and it's a multi-domain trigger, activate all major ones
    if not matching:
        q_lower = q.strip()
        if any(t in q_lower for t in MULTI_DOMAIN_QUERIES):
            matching = ["infra", "personal", "finance"]

    return matching


def route(query: str) -> dict:
    """Route query to appropriate agent(s) and aggregate responses.

    Returns: {query, agents, responses, aggregated}
    """
    agents = identify_agents(query)
    multi = is_multi_domain(query)

    if not agents:
        _log({"action": "route", "query": query[:80], "agents": [], "reason": "no match"})
        return {"query": query, "agents": [], "responses": {},
                "aggregated": None, "handled": False}

    responses = {}
    for name in agents:
        agent = _load_agent(name)
        if agent and hasattr(agent, "handle_query"):
            try:
                resp = agent.handle_query(query)
                if resp:
                    responses[name] = resp
            except Exception as e:
                responses[name] = f"[{name} error: {str(e)[:50]}]"

    # Aggregate if multiple agents responded
    aggregated = None
    if len(responses) > 1:
        aggregated = _aggregate(responses)
    elif responses:
        aggregated = list(responses.values())[0]

    result = {
        "query": query,
        "agents": agents,
        "responses": responses,
        "aggregated": aggregated,
        "handled": bool(responses),
        "multi_domain": multi,
    }

    _save_coord_log({**result, "ts": now().isoformat()})
    _log({"action": "route", "query": query[:80], "agents": agents,
          "responses": len(responses), "multi": multi})

    return result


def _aggregate(responses: dict) -> str:
    """Aggregate multiple agent responses into one coherent message."""
    parts = []
    for agent, resp in responses.items():
        if resp:
            parts.append(resp)
    return "\n\n".join(parts)


def list_agents() -> list:
    """List all agents with their status."""
    agents = []
    for name in AGENT_MODULES:
        agent = _load_agent(name)
        status = "loaded" if agent else "error"
        agents.append({
            "name": name,
            "module": AGENT_MODULES[name],
            "status": status,
            "has_owns": hasattr(agent, "owns") if agent else False,
            "has_handle": hasattr(agent, "handle_query") if agent else False,
        })
    return agents


def resolve_conflict(proposals: list) -> dict:
    """Delegate conflict resolution to arbitrator."""
    try:
        from agent_arbitrator import resolve
        return resolve(proposals)
    except Exception as e:
        return {"winner": None, "error": str(e)[:200]}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Agent Coordinator")
    p.add_argument("--route", type=str, help="Route a query")
    p.add_argument("--agents", action="store_true", help="List agents")
    args = p.parse_args()

    if args.route:
        result = route(args.route)
        if result["aggregated"]:
            print(result["aggregated"])
        else:
            print("No agent handled this query.")
        print(f"\n--- agents: {result['agents']}, multi: {result['multi_domain']} ---")
    elif args.agents:
        for a in list_agents():
            print(f"  [{a['status']}] {a['name']}")
    else:
        p.print_help()
