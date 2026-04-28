#!/usr/bin/env python3
"""
Goal Engine — Goal tracking and evaluation for Tenazas runtime.

3 permanent goals:
  1. maintain_infrastructure_stability (priority 1)
  2. minimize_downtime (priority 2)
  3. maximize_response_accuracy (priority 3)

Goals have priority, status (active/achieved/blocked), and metrics targets.
evaluate_goals() checks current system state against targets.

Stores state in goals_state.json. Logs to logs/goals.log.
"""

import json
from datetime import datetime
from pathlib import Path
try:
    from .runtime_utils import load_json, save_json
except ImportError:
    from runtime_utils import load_json, save_json

BASE_DIR = Path(__file__).parent
GOALS_FILE = BASE_DIR / "goals_state.json"
STATE_FILE = BASE_DIR / "state.json"
MEMORY_FILE = BASE_DIR / "memory_log.json"
LOG_FILE = BASE_DIR / "logs" / "goals.log"

# ── Goal Definitions ─────────────────────────────────────────────

PERMANENT_GOALS = [
    {
        "id": "maintain_infrastructure_stability",
        "description": "Keep all infrastructure containers running and healthy",
        "priority": 1,
        "targets": {
            "containers_up_pct": 95.0,   # >= 95% containers running
            "internet_ok": True,
            "vpn_ok": True,
        },
    },
    {
        "id": "minimize_downtime",
        "description": "Minimize container and service downtime through proactive monitoring",
        "priority": 2,
        "targets": {
            "max_events_per_day": 5,      # No more than 5 incidents/day
            "max_restarts_per_day": 3,     # No more than 3 restarts/day
        },
    },
    {
        "id": "maximize_response_accuracy",
        "description": "Ensure LLM backends are available for accurate responses",
        "priority": 3,
        "targets": {
            "ollama_notebook_ok": True,    # GPU backend preferred
            "ollama_any_ok": True,         # At least one backend
        },
    },
]


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    """Append a JSON line to goals.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _count_today_events() -> int:
    """Count events from memory_log.json that happened today."""
    memory = load_json(MEMORY_FILE, [])
    if not isinstance(memory, list):
        return 0

    today = _now().date().isoformat()
    count = 0
    for entry in memory:
        ts = entry.get("ts", "")
        if ts.startswith(today):
            count += len(entry.get("events", []))
    return count


def _count_today_restarts() -> int:
    """Count restart actions from memory_log.json that happened today."""
    memory = load_json(MEMORY_FILE, [])
    if not isinstance(memory, list):
        return 0

    today = _now().date().isoformat()
    count = 0
    for entry in memory:
        ts = entry.get("ts", "")
        if ts.startswith(today):
            for result in entry.get("results", []):
                if result.get("action") == "docker.restart" and result.get("ok"):
                    count += 1
    return count


def load_goals() -> list:
    """Load goal states from disk, initializing if needed."""
    data = load_json(GOALS_FILE, {"goals": []})
    goals = data.get("goals", [])

    # Ensure all permanent goals exist
    existing_ids = {g["id"] for g in goals}
    for pg in PERMANENT_GOALS:
        if pg["id"] not in existing_ids:
            goals.append({
                "id": pg["id"],
                "description": pg["description"],
                "priority": pg["priority"],
                "status": "active",
                "targets": pg["targets"],
                "last_evaluation": "",
                "metrics": {},
            })

    return goals


def save_goals(goals: list):
    """Save goal states to disk."""
    save_json(GOALS_FILE, {"goals": goals, "last_updated": _now().isoformat()})


def evaluate_goals() -> list:
    """
    Evaluate all goals against current system state.

    Returns:
        List of goal dicts with updated status and metrics
    """
    state = load_json(STATE_FILE, {})
    goals = load_goals()

    containers = state.get("containers", [])
    total_containers = len(containers)
    up_containers = sum(1 for c in containers if c.get("up"))
    up_pct = (up_containers / total_containers * 100) if total_containers > 0 else 0.0

    internet_ok = state.get("internet", {}).get("ok", False)
    vpn_ok = state.get("vpn", {}).get("ok", False)
    ollama_notebook_ok = state.get("ollama_notebook", {}).get("ok", False)
    ollama_nas_ok = state.get("ollama_nas", {}).get("ok", False)

    today_events = _count_today_events()
    today_restarts = _count_today_restarts()

    for goal in goals:
        gid = goal["id"]
        targets = goal.get("targets", {})

        if gid == "maintain_infrastructure_stability":
            metrics = {
                "containers_up_pct": round(up_pct, 1),
                "internet_ok": internet_ok,
                "vpn_ok": vpn_ok,
            }
            met = (
                up_pct >= targets.get("containers_up_pct", 95)
                and internet_ok == targets.get("internet_ok", True)
                and vpn_ok == targets.get("vpn_ok", True)
            )

        elif gid == "minimize_downtime":
            metrics = {
                "events_today": today_events,
                "restarts_today": today_restarts,
            }
            met = (
                today_events <= targets.get("max_events_per_day", 5)
                and today_restarts <= targets.get("max_restarts_per_day", 3)
            )

        elif gid == "maximize_response_accuracy":
            ollama_any = ollama_notebook_ok or ollama_nas_ok
            metrics = {
                "ollama_notebook_ok": ollama_notebook_ok,
                "ollama_nas_ok": ollama_nas_ok,
                "ollama_any_ok": ollama_any,
            }
            met = ollama_any  # At minimum, one backend must be up

        else:
            metrics = {}
            met = False

        goal["metrics"] = metrics
        goal["status"] = "achieved" if met else "active"
        goal["last_evaluation"] = _now().isoformat()

    save_goals(goals)

    _log({
        "ts": _now().isoformat(),
        "action": "evaluate_goals",
        "results": {g["id"]: g["status"] for g in goals},
    })

    return goals


def get_goal(goal_id: str) -> dict:
    """Get a specific goal by ID."""
    goals = load_goals()
    for g in goals:
        if g["id"] == goal_id:
            return g
    return {}


def get_blocked_goals() -> list:
    """Return goals that are not met (status != achieved)."""
    goals = load_goals()
    return [g for g in goals if g.get("status") != "achieved"]
