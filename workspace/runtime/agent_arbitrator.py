#!/usr/bin/env python3
"""Agent Arbitrator — Resolves conflicts between agents.

Priority order:
1. critical safety (policy_engine) — always wins
2. agent_infra in critical incident
3. agent_personal in active Sebas event
4. agent_finance in extreme variation (>10%)
5. agent_research / agent_self_improve — lowest

Conflict resolution: detect → log → apply priority → notify loser → execute winner.
If priority equal → postpone 5 min. 3 persistent → escalate to Sebas.
"""

import json
import time
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
CONFLICTS_FILE = STATE_DIR / "agents" / "conflicts.json"
LOG_FILE = BASE_DIR / "logs" / "agent_arbitrator.log"

PRIORITY = {
    "safety": 0,
    "infra_critical": 1,
    "personal_active": 2,
    "finance_extreme": 3,
    "infra": 4,
    "personal": 5,
    "finance": 6,
    "research": 7,
    "self_improve": 8,
}

POSTPONE_SECONDS = 300  # 5 min
MAX_POSTPONE_COUNT = 3


def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
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


def _get_effective_priority(agent: str, context: dict = None) -> int:
    """Get priority considering context (critical incidents, active events, etc.)."""
    ctx = context or {}

    if agent == "infra" and ctx.get("severity") == "critical":
        return PRIORITY["infra_critical"]
    if agent == "personal" and ctx.get("calendar_active"):
        return PRIORITY["personal_active"]
    if agent == "finance" and ctx.get("variation_pct", 0) > 10:
        return PRIORITY["finance_extreme"]

    return PRIORITY.get(agent, 9)


def _conflict_id(agent_a: str, agent_b: str, resource: str) -> str:
    key = f"{min(agent_a, agent_b)}:{max(agent_a, agent_b)}:{resource}"
    return sha256(key.encode()).hexdigest()[:12]


# ── Main API ────────────────────────────────────────────────────


def resolve(proposals: list) -> dict:
    """Resolve conflicts between agent proposals on the same resource.

    Args:
        proposals: list of {agent, action, resource, severity, context}

    Returns:
        {winner, loser, reason, action, escalated}
    """
    if len(proposals) < 2:
        if proposals:
            return {"winner": proposals[0]["agent"], "loser": None,
                    "reason": "no conflict", "action": proposals[0]["action"],
                    "escalated": False}
        return {"winner": None, "loser": None, "reason": "no proposals",
                "action": None, "escalated": False}

    # Check safety first — only for container-related actions
    filtered = []
    for p in proposals:
        target = p.get("target", p.get("resource", ""))
        action = p.get("action", "").lower()
        if "restart" in action or "stop" in action or "exec" in action:
            try:
                import safety_guard as sg
                if not sg.is_safe_container(target):
                    _log({"action": "safety_block", "agent": p["agent"],
                          "proposal": action, "target": target})
                    continue
            except Exception:
                pass
        filtered.append(p)
    proposals = filtered

    if len(proposals) < 2:
        winner = proposals[0] if proposals else None
        return {"winner": winner["agent"] if winner else None,
                "loser": None, "reason": "safety filtered",
                "action": winner["action"] if winner else None,
                "escalated": False}

    # Sort by priority (lower number = higher priority)
    sorted_props = sorted(proposals,
                          key=lambda p: _get_effective_priority(p["agent"], p.get("context", {})))

    winner = sorted_props[0]
    loser = sorted_props[1]

    # Check if same priority → postpone
    w_prio = _get_effective_priority(winner["agent"], winner.get("context", {}))
    l_prio = _get_effective_priority(loser["agent"], loser.get("context", {}))

    if w_prio == l_prio:
        return _handle_tie(winner, loser)

    result = {
        "winner": winner["agent"],
        "loser": loser["agent"],
        "reason": f"priority {winner['agent']}({w_prio}) > {loser['agent']}({l_prio})",
        "action": winner["action"],
        "escalated": False,
    }

    _log({"action": "resolve", **result})
    return result


def _handle_tie(a: dict, b: dict) -> dict:
    """Handle equal-priority conflict. Postpone and track."""
    resource = a.get("resource", a.get("target", ""))
    cid = _conflict_id(a["agent"], b["agent"], resource)

    conflicts = _load(CONFLICTS_FILE, {"history": {}})
    history = conflicts["history"].get(cid, {"count": 0, "first": now().isoformat()})
    history["count"] += 1
    history["last"] = now().isoformat()
    conflicts["history"][cid] = history
    _save(CONFLICTS_FILE, conflicts)

    if history["count"] >= MAX_POSTPONE_COUNT:
        _log({"action": "escalate", "agents": [a["agent"], b["agent"]],
              "resource": resource, "count": history["count"]})
        return {
            "winner": None, "loser": None,
            "reason": f"escalated to Sebas after {history['count']} ties",
            "action": None, "escalated": True,
            "agents": [a["agent"], b["agent"]],
        }

    _log({"action": "postpone", "agents": [a["agent"], b["agent"]],
          "resource": resource, "count": history["count"]})
    return {
        "winner": None, "loser": None,
        "reason": f"tie postponed ({history['count']}/{MAX_POSTPONE_COUNT})",
        "action": None, "escalated": False,
        "postponed": True,
    }


def get_conflict_history() -> dict:
    return _load(CONFLICTS_FILE, {"history": {}}).get("history", {})


def clear_conflicts():
    _save(CONFLICTS_FILE, {"history": {}})
