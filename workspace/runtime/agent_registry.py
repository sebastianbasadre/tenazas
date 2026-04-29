"""
agent_registry.py -- Centralized agent lifecycle management.

Provides register/unregister/dispatch/list for domain agents.
Validates modules at registration via agent_protocol.validate_agent_module().
Error isolation: one agent failing does not affect others.

Exports:
    register(name, module_path) -> bool
    unregister(name) -> bool
    dispatch(query) -> list of (name, observation, recommendations)
    list_agents() -> list of dicts
    get_agent(name) -> dict or None
    init_default_agents() -> None
    DEFAULT_AGENTS -- dict of known agent name -> module path
"""

import json
import importlib
from pathlib import Path

from runtime_utils import load_json, save_json, now
from agent_protocol import validate_agent_module, Observation


# ──────────────────────────────────────────────
# Paths (same pattern as agent_coordinator.py)
# ──────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
REGISTRY_FILE = AGENT_DIR / "agents_registry.json"
LOG_FILE = BASE_DIR / "logs" / "agent_registry.log"


# ──────────────────────────────────────────────
# Logging (JSONL, same 4-line pattern)
# ──────────────────────────────────────────────

def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(e, default=str) + "\n")
    except OSError:
        pass


# ──────────────────────────────────────────────
# Module-level state
# ──────────────────────────────────────────────

_registry = {}

# Known agents matching agent_coordinator.AGENT_MODULES
DEFAULT_AGENTS = {
    "infra": "agent_infra",
    "personal": "agent_personal",
    "finance": "agent_finance",
    "research": "agent_research",
    "self_improve": "agent_self_improve",
}


# ──────────────────────────────────────────────
# Registry operations
# ──────────────────────────────────────────────

def register(name: str, module_path: str) -> bool:
    """Register an agent by importing its module and validating the interface.

    Returns True if registration succeeded, False on import or validation error.
    Failed modules are stored with error status for diagnostics.
    """
    # Try to import the module
    try:
        module = importlib.import_module(module_path)
    except Exception as e:
        _registry[name] = {
            "module": None,
            "preferred_role": None,
            "status": "import_error",
            "failure_count": 0,
            "error": str(e)[:200],
        }
        _log({"action": "register_import_error", "agent": name, "path": module_path,
              "error": str(e)[:200]})
        _persist_snapshot()
        return False

    # Validate duck-type interface
    ok, errors = validate_agent_module(module)
    if not ok:
        _registry[name] = {
            "module": None,
            "preferred_role": None,
            "status": "invalid",
            "failure_count": 0,
            "error": "; ".join(errors),
        }
        _log({"action": "register_invalid", "agent": name, "path": module_path,
              "errors": errors})
        _persist_snapshot()
        return False

    # Success
    _registry[name] = {
        "module": module,
        "preferred_role": getattr(module, "PREFERRED_ROLE", None),
        "status": "healthy",
        "failure_count": 0,
        "error": None,
    }
    _log({"action": "register_ok", "agent": name, "path": module_path,
          "preferred_role": _registry[name]["preferred_role"]})
    _persist_snapshot()
    return True


def unregister(name: str) -> bool:
    """Remove an agent from the registry. Returns True if it existed."""
    if name in _registry:
        _registry.pop(name)
        _log({"action": "unregister", "agent": name})
        _persist_snapshot()
        return True
    return False


def dispatch(query: str) -> list:
    """Route a query to all healthy agents that claim ownership.

    Returns list of (name, observation_or_None, recommendations_list).
    Exceptions in agent.handle() are caught -- broken agents return (name, None, []).
    """
    results = []
    for name, entry in _registry.items():
        if entry["status"] != "healthy" or entry["module"] is None:
            continue

        # Check ownership
        try:
            if not entry["module"].owns(query):
                continue
        except Exception:
            continue

        # Execute handler with error isolation
        try:
            obs, recs = entry["module"].handle(query)
            results.append((name, obs, recs))
        except Exception as e:
            entry["failure_count"] += 1
            _log({"action": "handle_error", "agent": name,
                  "error": str(e)[:200], "failures": entry["failure_count"]})
            results.append((name, None, []))

    return results


def list_agents() -> list:
    """Return list of agent status dicts (no module objects)."""
    return [
        {
            "name": name,
            "status": entry["status"],
            "preferred_role": entry.get("preferred_role"),
            "failure_count": entry["failure_count"],
        }
        for name, entry in _registry.items()
    ]


def get_agent(name: str):
    """Return agent entry dict (without module object) or None."""
    entry = _registry.get(name)
    if entry is None:
        return None
    return {
        "name": name,
        "status": entry["status"],
        "preferred_role": entry.get("preferred_role"),
        "failure_count": entry["failure_count"],
        "error": entry.get("error"),
    }


# ──────────────────────────────────────────────
# Snapshot persistence
# ──────────────────────────────────────────────

def _get_registry_snapshot() -> dict:
    """Serializable snapshot (excludes module objects)."""
    return {
        name: {
            "status": entry["status"],
            "preferred_role": entry.get("preferred_role"),
            "failure_count": entry["failure_count"],
            "error": entry.get("error"),
        }
        for name, entry in _registry.items()
    }


def _persist_snapshot():
    """Save registry snapshot to disk."""
    try:
        AGENT_DIR.mkdir(parents=True, exist_ok=True)
        save_json(str(REGISTRY_FILE), _get_registry_snapshot())
    except Exception:
        pass


# ──────────────────────────────────────────────
# Default agent initialization
# ──────────────────────────────────────────────

def init_default_agents():
    """Register all default agents. Called lazily or explicitly by coordinator."""
    registered = 0
    failed = 0
    for name, path in DEFAULT_AGENTS.items():
        if register(name, path):
            registered += 1
        else:
            failed += 1
    _log({"action": "init_default_agents", "registered": registered, "failed": failed})
