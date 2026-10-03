#!/usr/bin/env python3
"""Agent Coordinator — Routes queries to appropriate agent(s).

Phase 8 rewrite: parallel owns() evaluation, strategy selection,
correlation ID tracing, emoji-labeled aggregation.

Usage:
    python3 agent_coordinator.py --route "como esta todo"
    python3 agent_coordinator.py --agents   # list agents + status
"""

import json
import time
import uuid
from bounded_processes import bounded_calls
from runtime_utils import StateLock, load_json, write_atomic
from pathlib import Path

from agent_metrics import record_agent_result, record_route_result
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
    "security": "agent_security",
    "portfolio_risk": "agent_portfolio_risk",
    "subscriptions": "agent_subscriptions",
    "job_search": "agent_job_search",
    "home_reliability": "agent_home_reliability",
    "media_stack": "agent_media_stack",
    "daily_brief": "agent_daily_brief",
    "runbook": "agent_runbook",
}

DOMAIN_EMOJI = {
    "infra": "📡",
    "personal": "👤",
    "finance": "💰",
    "research": "🔍",
    "self_improve": "🔧",
    "security": "🔐",
    "portfolio_risk": "📊",
    "subscriptions": "🧾",
    "job_search": "💼",
    "home_reliability": "🏠",
    "media_stack": "🎬",
    "daily_brief": "🗓",
    "runbook": "📘",
}


def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(e, default=str) + "\n")
    except OSError:
        pass


def _save_coord_log(entry: dict):
    try:
        with StateLock(COORD_LOG):
            existing = load_json(COORD_LOG, [])
            if not isinstance(existing, list):
                existing = []
            write_atomic(COORD_LOG, (existing + [entry])[-200:])
    except OSError as exc:
        _log({"action": "coordinator_log_error", "error": str(exc)[:100]})


def _load_agent(name: str):
    """Lazily import an agent module."""
    try:
        import importlib

        return importlib.import_module(AGENT_MODULES[name])
    except Exception:
        return None


# ── Core Routing ─────────────────────────────────────────────────


def _call_agent(name, method, query, correlation_id=None):
    agent = _load_agent(name)
    if agent is None or not hasattr(agent, method):
        return False if method == "owns" else None
    if method == "owns":
        return agent.owns(query)
    return agent.handle(query, correlation_id=correlation_id)


def _get_claimants(query: str) -> list:
    calls = {name: (_call_agent, (name, "owns", query)) for name in AGENT_MODULES}
    results = bounded_calls(calls, timeout=2, max_workers=5)
    return [name for name, result in results.items() if result.get("ok") and result.get("value")]


def _run_single(name: str, query: str, correlation_id: str) -> dict:
    return _run_parallel([name], query, correlation_id)


def _run_parallel(claimants: list, query: str, correlation_id: str) -> dict:
    calls = {name: (_call_agent, (name, "handle", query, correlation_id)) for name in claimants}
    started = time.perf_counter()
    outcomes = bounded_calls(calls, timeout=30, max_workers=5)
    results = {}
    for name, outcome in outcomes.items():
        status = "ok" if outcome.get("ok") else "timeout" if outcome.get("timeout") else "error"
        record_agent_result(name, time.perf_counter() - started, status)
        if outcome.get("ok") and outcome.get("value") is not None:
            results[name] = outcome["value"]
        elif not outcome.get("ok"):
            _log({"action": "agent_" + status, "agent": name,
                  "error": outcome.get("error"), "correlation_id": correlation_id})
    return results


def _aggregate(results: dict) -> str | None:
    """Aggregate multiple agent results using emoji-labeled sections."""
    parts = []
    for name, value in results.items():
        if isinstance(value, tuple):
            obs, _recs = value
            summary = getattr(obs, "summary", None) if obs else None
        else:
            summary = value if value else None
        if not summary:
            continue
        emoji = DOMAIN_EMOJI.get(name, "•")
        label = name.replace("_", " ").title()
        parts.append(f"{emoji} {label}: {summary}")
    return "\n".join(parts) if parts else None


def _do_skill_fallback(query: str, correlation_id: str) -> dict:
    """Zero-claim fallback: try skill executor for skill categories."""
    from classifier import classify_query
    from skill_executor import execute_skill

    category = classify_query(query)
    if category.startswith("skill:"):
        result = execute_skill(category, query)
        _log(
            {
                "action": "skill_fallback",
                "category": category,
                "success": result.get("success"),
                "correlation_id": correlation_id,
            }
        )
        return {
            "handled": result.get("success", False),
            "output": result.get("output"),
            "source": "skill_executor",
        }

    _log({"action": "llm_fallback", "category": category, "correlation_id": correlation_id})
    return {"handled": False, "output": None, "source": "llm_needed"}


def _responses_from_results(results: dict) -> dict:
    responses = {}
    for name, value in results.items():
        if isinstance(value, tuple):
            obs, _recs = value
            summary = getattr(obs, "summary", None) if obs else None
        else:
            summary = value
        if summary:
            responses[name] = summary
    return responses


def route(query: str) -> dict:
    """Route query to appropriate agent(s) and aggregate responses."""
    correlation_id = str(uuid.uuid4())[:8]
    _log({"action": "route_start", "correlation_id": correlation_id, "query": query[:80]})
    t0 = now()
    claimants = _get_claimants(query)

    if not claimants:
        strategy = "skill_fallback"
        result = _do_skill_fallback(query, correlation_id)
        elapsed = (now() - t0).total_seconds()
        payload = {
            "query": query,
            "correlation_id": correlation_id,
            "strategy": strategy,
            "agents": [],
            "responded": [],
            "responses": {},
            "aggregated": result.get("output"),
            "handled": result.get("handled", False),
            "source": result.get("source"),
        }
        _log(
            {
                "action": "route_done",
                "correlation_id": correlation_id,
                "strategy": strategy,
                "agents": [],
                "elapsed": elapsed,
            }
        )
        record_route_result(strategy, result.get("handled", False), result.get("source"))
        _save_coord_log({**payload, "ts": now().isoformat()})
        return payload

    try:
        if len(claimants) == 1:
            strategy = "first_wins"
            results = _run_single(claimants[0], query, correlation_id)
        else:
            strategy = "parallel"
            results = _run_parallel(claimants, query, correlation_id)
    except Exception as e:
        _log(
            {
                "action": "route_error",
                "correlation_id": correlation_id,
                "strategy": "unknown",
                "error": str(e)[:100],
            }
        )
        results = {}
        strategy = "first_wins" if len(claimants) == 1 else "parallel"

    if len(results) > 1:
        aggregated = _aggregate(results)
    elif results:
        aggregated = next(iter(_responses_from_results(results).values()), None)
    else:
        aggregated = None

    responses = _responses_from_results(results)
    elapsed = (now() - t0).total_seconds()
    responded = list(results.keys())
    payload = {
        "query": query,
        "correlation_id": correlation_id,
        "strategy": strategy,
        "agents": claimants,
        "responded": responded,
        "responses": responses,
        "aggregated": aggregated,
        "handled": bool(aggregated),
    }
    _log(
        {
            "action": "route_done",
            "correlation_id": correlation_id,
            "strategy": strategy,
            "agents": claimants,
            "responded": responded,
            "elapsed": elapsed,
        }
    )
    record_route_result(strategy, bool(aggregated))
    _save_coord_log({**payload, "ts": now().isoformat()})
    return payload


# ── Backward Compatibility ────────────────────────────────────────


def is_multi_domain(query: str) -> bool:
    """Check if query requires multiple agents. Delegates to _get_claimants()."""
    return len(identify_agents(query)) > 1


def identify_agents(query: str) -> list:
    """Identify which agent(s) should handle this query. Delegates to _get_claimants()."""
    claimants = _get_claimants(query)
    if not claimants:
        triggers = [
            "como esta todo",
            "como va todo",
            "estado general",
            "resumen completo",
            "que deberia saber",
            "que paso hoy",
            "update",
            "dame el status",
        ]
        if any(t in query.lower() for t in triggers):
            return ["infra", "personal", "finance"]
    return claimants


def list_agents() -> list:
    """List all agents with their status."""
    agents = []
    for name in AGENT_MODULES:
        mod = _load_agent(name)
        status = "healthy" if mod is not None else "unavailable"
        preferred_role = getattr(mod, "PREFERRED_ROLE", "fast") if mod else "unknown"
        agents.append(
            {
                "name": name,
                "module": AGENT_MODULES[name],
                "status": status,
                "preferred_role": preferred_role,
                "has_owns": mod is not None and hasattr(mod, "owns"),
                "has_handle": mod is not None and hasattr(mod, "handle"),
            }
        )
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
        if result.get("aggregated"):
            print(result["aggregated"])
        else:
            print("No agent handled this query.")
        print(f"\n--- strategy: {result.get('strategy')}, agents: {result.get('agents')} ---")
    elif args.agents:
        for a in list_agents():
            print(f"  [{a['status']}] {a['name']} (role: {a['preferred_role']})")
    else:
        p.print_help()
