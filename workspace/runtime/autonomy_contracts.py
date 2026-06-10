#!/usr/bin/env python3
"""
autonomy_contracts.py
Per-agent autonomy contracts for Tenazas/OpenClaw.

This module answers one question before policy_engine makes a final risk
decision: is this specific agent allowed to attempt this action right now?

The checks are intentionally conservative:
  - unknown agents are shadow/proposal-only
  - unknown actions are blocked by the action registry later, but denied here
    when an agent contract is present
  - confidence, daily budgets, failure streaks, and explicit scopes are enforced
  - state is persisted as JSON so cron workers share the same limits
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


RUNTIME = Path(__file__).parent
STATE_PATH = RUNTIME / "autonomy_state.json"
LOG_PATH = RUNTIME / "logs" / "autonomy_contracts.log"
KILL_SWITCH_DIR = Path("/home/tenazas/.openclaw/state")


@dataclass
class AutonomyDecision:
    agent: str
    action_name: str
    target: str
    allowed: bool
    execute_now: bool
    requires_approval: bool
    reason: str
    shadow_mode: bool = False
    circuit_open: bool = False
    budget_remaining: int | None = None
    confidence: float = 0.0


CONTRACTS: dict[str, dict[str, Any]] = {
    "infra_watcher": {
        "mode": "auto",
        "allowed_actions": {
            "restart_container": [
                "qbittorrent", "sonarr", "radarr", "lidarr", "prowlarr",
                "seerr", "requestrr", "tautulli", "sabnzbd",
            ],
            "restart_vpn": ["gluetun"],
            "health_check": ["*"],
            "send_telegram": ["*"],
        },
        "min_confidence": {
            "restart_container": 0.82,
            "restart_vpn": 0.85,
            "health_check": 0.0,
            "send_telegram": 0.0,
        },
        "daily_action_budget": 6,
        "daily_target_budget": 2,
        "failure_streak_limit": 3,
        "kill_switch": "AUTONOMY_INFRA_WATCHER_DISABLED",
    },
    "agents_worker": {
        "mode": "shadow",
        "allowed_actions": {
            "health_check": ["*"],
            "check_ollama": ["*"],
            "send_telegram": ["*"],
        },
        "min_confidence": {},
        "daily_action_budget": 10,
        "daily_target_budget": 10,
        "failure_streak_limit": 3,
        "kill_switch": "AUTONOMY_AGENTS_WORKER_DISABLED",
    },
    "learning_worker": {
        "mode": "shadow",
        "allowed_actions": {
            "health_check": ["*"],
            "check_ollama": ["*"],
            "send_telegram": ["*"],
        },
        "min_confidence": {},
        "daily_action_budget": 10,
        "daily_target_budget": 10,
        "failure_streak_limit": 3,
        "kill_switch": "AUTONOMY_LEARNING_WORKER_DISABLED",
    },
    "model_failover": {
        "mode": "auto",
        "allowed_actions": {
            "reload_model": ["*"],
            "check_ollama": ["*"],
            "send_telegram": ["*"],
        },
        "min_confidence": {
            "reload_model": 0.80,
            "check_ollama": 0.0,
            "send_telegram": 0.0,
        },
        "daily_action_budget": 8,
        "daily_target_budget": 4,
        "failure_streak_limit": 3,
        "kill_switch": "AUTONOMY_MODEL_FAILOVER_DISABLED",
    },
}


def _today() -> str:
    return datetime.now().astimezone().date().isoformat()


def _log(entry: dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_state() -> dict[str, Any]:
    try:
        if STATE_PATH.exists():
            data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception:
        pass
    return {"day": _today(), "agents": {}}


def _save_state(state: dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    tmp.replace(STATE_PATH)


def _normalize_state(state: dict[str, Any]) -> dict[str, Any]:
    today = _today()
    if state.get("day") != today:
        return {"day": today, "agents": {}}
    state.setdefault("agents", {})
    return state


def _agent_state(state: dict[str, Any], agent: str) -> dict[str, Any]:
    agents = state.setdefault("agents", {})
    current = agents.setdefault(agent, {})
    current.setdefault("actions_today", 0)
    current.setdefault("targets_today", {})
    current.setdefault("failure_streak", 0)
    current.setdefault("circuit_opened_at", None)
    current.setdefault("last_action_at", None)
    return current


def _target_key(action_name: str, target: str) -> str:
    return f"{action_name}:{target}" if target else action_name


def _kill_switch_active(contract: dict[str, Any]) -> bool:
    name = contract.get("kill_switch")
    if not name:
        return False
    return (RUNTIME / name).exists() or (KILL_SWITCH_DIR / name).exists()


def _target_allowed(allowed_targets: list[str], target: str) -> bool:
    if "*" in allowed_targets:
        return True
    return target.lower() in {t.lower() for t in allowed_targets}


def evaluate(
    agent: str,
    action_name: str,
    target: str = "",
    confidence: float = 0.0,
    context: str = "",
) -> AutonomyDecision:
    """Evaluate an action against the calling agent's autonomy contract."""
    agent = (agent or "").strip()
    action_name = (action_name or "").strip()
    target = (target or "").strip()

    if not agent:
        decision = AutonomyDecision(
            agent="",
            action_name=action_name,
            target=target,
            allowed=True,
            execute_now=False,
            requires_approval=False,
            reason="No agent supplied; autonomy contract skipped",
            confidence=confidence,
        )
        _log({"decision": asdict(decision), "context": context[:200]})
        return decision

    contract = CONTRACTS.get(agent)
    if not contract:
        decision = AutonomyDecision(
            agent=agent,
            action_name=action_name,
            target=target,
            allowed=True,
            execute_now=False,
            requires_approval=True,
            reason="Unknown agent; proposal/approval required",
            shadow_mode=True,
            confidence=confidence,
        )
        _log({"decision": asdict(decision), "context": context[:200]})
        return decision

    if _kill_switch_active(contract):
        decision = AutonomyDecision(
            agent=agent,
            action_name=action_name,
            target=target,
            allowed=False,
            execute_now=False,
            requires_approval=False,
            reason="Agent autonomy kill switch active",
            confidence=confidence,
        )
        _log({"decision": asdict(decision), "context": context[:200]})
        return decision

    allowed_actions = contract.get("allowed_actions", {})
    if action_name not in allowed_actions:
        decision = AutonomyDecision(
            agent=agent,
            action_name=action_name,
            target=target,
            allowed=False,
            execute_now=False,
            requires_approval=True,
            reason=f"Action '{action_name}' is outside agent autonomy contract",
            confidence=confidence,
        )
        _log({"decision": asdict(decision), "context": context[:200]})
        return decision

    if not _target_allowed(allowed_actions[action_name], target):
        decision = AutonomyDecision(
            agent=agent,
            action_name=action_name,
            target=target,
            allowed=False,
            execute_now=False,
            requires_approval=True,
            reason=f"Target '{target}' is outside agent autonomy contract",
            confidence=confidence,
        )
        _log({"decision": asdict(decision), "context": context[:200]})
        return decision

    min_confidence = contract.get("min_confidence", {}).get(action_name, 0.0)
    if confidence < min_confidence:
        decision = AutonomyDecision(
            agent=agent,
            action_name=action_name,
            target=target,
            allowed=True,
            execute_now=False,
            requires_approval=True,
            reason=f"Confidence below autonomy threshold ({confidence:.2f} < {min_confidence:.2f})",
            confidence=confidence,
        )
        _log({"decision": asdict(decision), "context": context[:200]})
        return decision

    state = _normalize_state(_load_state())
    current = _agent_state(state, agent)
    failure_limit = int(contract.get("failure_streak_limit", 3))
    if current.get("failure_streak", 0) >= failure_limit:
        decision = AutonomyDecision(
            agent=agent,
            action_name=action_name,
            target=target,
            allowed=False,
            execute_now=False,
            requires_approval=True,
            reason=f"Circuit breaker open after {current.get('failure_streak', 0)} failures",
            circuit_open=True,
            confidence=confidence,
        )
        _log({"decision": asdict(decision), "context": context[:200]})
        return decision

    daily_budget = int(contract.get("daily_action_budget", 0))
    actions_today = int(current.get("actions_today", 0))
    if daily_budget and actions_today >= daily_budget:
        decision = AutonomyDecision(
            agent=agent,
            action_name=action_name,
            target=target,
            allowed=True,
            execute_now=False,
            requires_approval=True,
            reason="Daily autonomy budget exhausted",
            budget_remaining=0,
            confidence=confidence,
        )
        _log({"decision": asdict(decision), "context": context[:200]})
        return decision

    target_budget = int(contract.get("daily_target_budget", 0))
    targets_today = current.setdefault("targets_today", {})
    key = _target_key(action_name, target)
    target_count = int(targets_today.get(key, 0))
    if target_budget and target_count >= target_budget:
        decision = AutonomyDecision(
            agent=agent,
            action_name=action_name,
            target=target,
            allowed=True,
            execute_now=False,
            requires_approval=True,
            reason="Daily target autonomy budget exhausted",
            budget_remaining=max(0, daily_budget - actions_today),
            confidence=confidence,
        )
        _log({"decision": asdict(decision), "context": context[:200]})
        return decision

    shadow = contract.get("mode") == "shadow"
    budget_remaining = max(0, daily_budget - actions_today) if daily_budget else None
    decision = AutonomyDecision(
        agent=agent,
        action_name=action_name,
        target=target,
        allowed=True,
        execute_now=not shadow,
        requires_approval=shadow,
        reason="Autonomy contract permits action" if not shadow else "Agent is in shadow mode",
        shadow_mode=shadow,
        budget_remaining=budget_remaining,
        confidence=confidence,
    )
    _log({"decision": asdict(decision), "context": context[:200]})
    return decision


def record_action(agent: str, action_name: str, target: str = "") -> None:
    """Record that an autonomous action actually executed."""
    if not agent:
        return
    state = _normalize_state(_load_state())
    current = _agent_state(state, agent)
    key = _target_key(action_name, target)
    current["actions_today"] = int(current.get("actions_today", 0)) + 1
    current.setdefault("targets_today", {})
    current["targets_today"][key] = int(current["targets_today"].get(key, 0)) + 1
    current["last_action_at"] = time.time()
    _save_state(state)
    _log({"action": "record_action", "agent": agent, "action_name": action_name, "target": target})


def record_outcome(
    agent: str,
    action_name: str,
    target: str = "",
    ok: bool = True,
    reason: str = "",
) -> None:
    """Record post-execution verification result and update circuit breaker state."""
    if not agent:
        return
    state = _normalize_state(_load_state())
    current = _agent_state(state, agent)
    if ok:
        current["failure_streak"] = 0
        current["circuit_opened_at"] = None
    else:
        current["failure_streak"] = int(current.get("failure_streak", 0)) + 1
        contract = CONTRACTS.get(agent, {})
        limit = int(contract.get("failure_streak_limit", 3))
        if current["failure_streak"] >= limit:
            current["circuit_opened_at"] = time.time()
    _save_state(state)
    _log({
        "action": "record_outcome",
        "agent": agent,
        "action_name": action_name,
        "target": target,
        "ok": ok,
        "reason": reason[:200],
        "failure_streak": current.get("failure_streak", 0),
    })


def reset_agent(agent: str) -> None:
    """Reset one agent's autonomy counters and circuit breaker."""
    state = _normalize_state(_load_state())
    state.setdefault("agents", {}).pop(agent, None)
    _save_state(state)


def status() -> dict[str, Any]:
    """Return current autonomy state plus contract names for dashboards/status."""
    state = _normalize_state(_load_state())
    return {
        "day": state.get("day"),
        "contracts": sorted(CONTRACTS.keys()),
        "agents": state.get("agents", {}),
    }
