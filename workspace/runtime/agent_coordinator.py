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
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FutureTimeout, as_completed
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
}

DOMAIN_EMOJI = {
    "infra": "📡",
    "personal": "👤",
    "finance": "💰",
    "research": "🔍",
    "self_improve": "🔧",
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


# ── Core Routing ─────────────────────────────────────────────────


def _get_claimants(query: str) -> list:
    """Run all owns() calls in parallel."""
    claimants = []
    agents = {name: _load_agent(name) for name in AGENT_MODULES}
    loaded = {name: mod for name, mod in agents.items() if mod is not None and hasattr(mod, "owns")}
    if not loaded:
        return claimants

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {pool.submit(mod.owns, query): name for name, mod in loaded.items()}
        try:
            for future in as_completed(futures, timeout=2):
                name = futures[future]
                try:
                    if future.result():
                        claimants.append(name)
                except Exception:
                    pass
        except FutureTimeout:
            pass
    return claimants


def _run_single(name: str, query: str, correlation_id: str) -> dict:
    """Direct call to one agent. No threading overhead."""
    started = time.perf_counter()
    try:
        agent = _load_agent(name)
        if agent is None or not hasattr(agent, "handle"):
            return {}
        obs, recs = agent.handle(query, correlation_id=correlation_id)
        record_agent_result(name, time.perf_counter() - started, "ok")
        return {name: (obs, recs)}
    except Exception as e:
        record_agent_result(name, time.perf_counter() - started, "error")
        _log(
            {
                "action": "agent_error",
                "agent": name,
                "error": str(e)[:100],
                "correlation_id": correlation_id,
            }
        )
        return {}


def _run_parallel(claimants: list, query: str, correlation_id: str) -> dict:
    """Call handle() on all claimants in parallel."""
    results = {}
    loaded = {name: _load_agent(name) for name in claimants}
    callable_agents = {name: mod for name, mod in loaded.items() if mod is not None and hasattr(mod, "handle")}
    if not callable_agents:
        return results

    with ThreadPoolExecutor(max_workers=len(callable_agents)) as pool:
        futures = {
            pool.submit(mod.handle, query, correlation_id=correlation_id): name
            for name, mod in callable_agents.items()
        }
        started = {future: time.perf_counter() for future in futures}
        try:
            for future in as_completed(futures, timeout=31):
                name = futures[future]
                try:
                    obs, recs = future.result(timeout=30)
                    record_agent_result(name, time.perf_counter() - started[future], "ok")
                    results[name] = (obs, recs)
                except FutureTimeout:
                    record_agent_result(name, time.perf_counter() - started[future], "timeout")
                    _log({"action": "agent_timeout", "agent": name, "correlation_id": correlation_id})
                except Exception as e:
                    record_agent_result(name, time.perf_counter() - started[future], "error")
                    _log(
                        {
                            "action": "agent_error",
                            "agent": name,
                            "error": str(e)[:100],
                            "correlation_id": correlation_id,
                        }
                    )
        except FutureTimeout:
            for future, name in futures.items():
                if future.done():
                    continue
                record_agent_result(name, time.perf_counter() - started[future], "timeout")
                _log({"action": "agent_timeout", "agent": name, "correlation_id": correlation_id})
            _log(
                {
                    "action": "parallel_timeout",
                    "correlation_id": correlation_id,
                    "partial_results": list(results.keys()),
                }
            )
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
