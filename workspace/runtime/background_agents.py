#!/usr/bin/env python3
"""
Background Agents — Registry for persistent sequential background tasks.

Each agent has: name, interval_seconds, last_run, function_ref.
Agents run sequentially (not in parallel) when check_and_run() is called
by the runtime loop.

Agents:
  - infra_watcher: reads state.json, flags issues
  - pattern_detector: runs infra_memory.update_memory()
  - metrics_collector: aggregates daily metrics

Log: workspace/runtime/logs/background_agents.log
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from runtime_utils import now
try:
    from .runtime_utils import load_json, save_json
except ImportError:
    from runtime_utils import load_json, save_json

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
AGENTS_STATE_FILE = BASE_DIR / "agents_state.json"
TELEMETRY_LOG = BASE_DIR / "logs" / "background_agents.log"



def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Agent Functions ─────────────────────────────────────────────


def _infra_watcher() -> dict:
    """Read state.json and flag issues. Read-only."""
    state = load_json(STATE_FILE, {})
    flags = []

    # Check containers
    for c in state.get("containers", []):
        if not c.get("up", True) and c.get("name", "").lower() not in ("openclaw", "watchtower"):
            flags.append({"issue": "container_down", "target": c["name"]})

    # Check internet
    internet = state.get("internet", {})
    if not internet.get("ok"):
        flags.append({"issue": "internet_down"})
    elif internet.get("latency_ms") and internet["latency_ms"] > 200:
        flags.append({"issue": "high_latency", "latency_ms": internet["latency_ms"]})

    # Check VPN
    vpn = state.get("vpn", {})
    if not vpn.get("ok"):
        flags.append({"issue": "vpn_down"})
    elif vpn.get("country") == "AR":
        flags.append({"issue": "vpn_leak", "country": "AR"})

    # Check Ollama
    ollama = state.get("ollama_notebook", {})
    if not ollama.get("ok"):
        flags.append({"issue": "ollama_notebook_offline"})

    return {"flags": flags, "state_ts": state.get("ts", "")}


def _pattern_detector() -> dict:
    """Run infra_memory pattern detection."""
    try:
        from runtime import infra_memory
        patterns = infra_memory.update_memory()
        return {
            "patterns_found": len(patterns) if patterns else 0,
            "high_confidence": sum(
                1 for p in (patterns or []) if p.get("confidence", 0) >= 0.8
            ),
        }
    except ImportError:
        return {"error": "infra_memory not available"}
    except Exception as e:
        return {"error": str(e)}


def _metrics_collector() -> dict:
    """Aggregate daily metrics summary."""
    try:
        from runtime import metrics_engine
        return metrics_engine.summary(days=1)
    except ImportError:
        return {"error": "metrics_engine not available"}
    except Exception as e:
        return {"error": str(e)}


def _security_watcher() -> dict:
    """Run the read-only security posture assessment."""
    try:
        try:
            from runtime import agent_security
        except ImportError:
            import agent_security
        status = agent_security.assess()
        findings = status.get("findings", [])
        return {
            "health": status.get("health", "unknown"),
            "findings": len(findings),
            "high": sum(1 for f in findings if f.get("severity") == "high"),
            "medium": sum(1 for f in findings if f.get("severity") == "medium"),
        }
    except Exception as e:
        return {"error": str(e)}


# ── Agent Registry ──────────────────────────────────────────────


AGENTS = [
    {
        "name": "infra_watcher",
        "interval_seconds": 300,       # 5 minutes
        "function": _infra_watcher,
        "description": "Reads state.json, flags infrastructure issues",
    },
    {
        "name": "pattern_detector",
        "interval_seconds": 3600,      # 1 hour
        "function": _pattern_detector,
        "description": "Runs infra_memory pattern detection",
    },
    {
        "name": "metrics_collector",
        "interval_seconds": 1800,      # 30 minutes
        "function": _metrics_collector,
        "description": "Aggregates daily metrics summary",
    },
    {
        "name": "security_watcher",
        "interval_seconds": 900,       # 15 minutes
        "function": _security_watcher,
        "description": "Checks local security posture and exposed admin surfaces",
    },
]


# ── Core API ────────────────────────────────────────────────────


def _load_agents_state() -> dict:
    """Load last_run timestamps for each agent."""
    return load_json(AGENTS_STATE_FILE, {})


def _save_agents_state(state: dict):
    save_json(AGENTS_STATE_FILE, state)


def _should_run(agent_name: str, interval_seconds: int,
                agents_state: dict) -> bool:
    """Check if an agent is due to run based on its interval."""
    last_run = agents_state.get(agent_name, {}).get("last_run", "")
    if not last_run:
        return True
    try:
        last_dt = datetime.fromisoformat(last_run)
        return (now() - last_dt).total_seconds() >= interval_seconds
    except (ValueError, TypeError):
        return True


def check_and_run() -> list:
    """Check all agents and run those that are due. Sequential execution.

    Returns list of results from agents that ran.
    """
    agents_state = _load_agents_state()
    results = []

    for agent in AGENTS:
        name = agent["name"]
        interval = agent["interval_seconds"]

        if not _should_run(name, interval, agents_state):
            continue

        _log({"type": "agent_start", "agent": name})

        try:
            result = agent["function"]()
            ok = "error" not in result
        except Exception as e:
            result = {"error": str(e)}
            ok = False

        agents_state[name] = {
            "last_run": now().isoformat(),
            "ok": ok,
            "result_summary": str(result)[:200],
        }

        results.append({
            "agent": name,
            "ok": ok,
            "result": result,
        })

        _log({
            "type": "agent_complete",
            "agent": name,
            "ok": ok,
            "result_keys": list(result.keys()) if isinstance(result, dict) else [],
        })

    _save_agents_state(agents_state)
    return results


def get_agents_status() -> list:
    """Get status of all registered agents."""
    agents_state = _load_agents_state()
    status = []
    for agent in AGENTS:
        name = agent["name"]
        state = agents_state.get(name, {})
        status.append({
            "name": name,
            "description": agent["description"],
            "interval_seconds": agent["interval_seconds"],
            "last_run": state.get("last_run", "never"),
            "ok": state.get("ok"),
        })
    return status
