#!/usr/bin/env python3
"""
policy_engine.py
Risk-aware execution policy — wraps safety_guard.py with risk classification.

EXTENDS (does not duplicate):
  - safety_guard.validate_action() — used as inner validator
  - action_registry.can_execute() — used for risk levels
  - docker_wrapper — never calls Docker directly

Risk policy:
  SAFE   → execute immediately, log only
  LOW    → execute with cooldown check, log + validate post-exec
  MEDIUM → requires approval OR auto-execute if confidence > 0.9
  HIGH   → ALWAYS requires Sebas approval, no exceptions

Invariants:
  - openclaw and watchtower are NEVER touched (inherited from safety_guard)
  - Unknown actions default to HIGH risk
  - All decisions are logged with full context

Usage:
  from policy_engine import evaluate, PolicyDecision
"""

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from action_registry import (
    RISK_HIGH, RISK_LOW, RISK_MEDIUM, RISK_SAFE,
    can_execute, get_action, get_cooldown,
)
from safety_guard import validate_action

RUNTIME = Path(__file__).parent
LOG_PATH = RUNTIME / "logs" / "policy_engine.log"
COOLDOWN_STATE_PATH = RUNTIME / "policy_cooldown_state.json"


@dataclass
class PolicyDecision:
    """Result of policy evaluation."""
    action_name: str
    target: str
    allowed: bool
    execute_now: bool           # True = go ahead, False = needs approval or blocked
    requires_approval: bool
    risk_level: str
    reason: str
    cooldown_remaining: int = 0  # seconds until cooldown expires
    confidence: float = 0.0


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_cooldowns() -> dict:
    try:
        return json.loads(COOLDOWN_STATE_PATH.read_text()) if COOLDOWN_STATE_PATH.exists() else {}
    except Exception:
        return {}


def _save_cooldowns(state: dict):
    COOLDOWN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    COOLDOWN_STATE_PATH.write_text(json.dumps(state, default=str))


def _check_cooldown(action_name: str, target: str) -> int:
    """Check cooldown for action+target pair. Returns seconds remaining (0 = clear)."""
    state = _load_cooldowns()
    key = f"{action_name}:{target}" if target else action_name
    last_exec = state.get(key, 0)
    cooldown = get_cooldown(action_name)
    elapsed = time.time() - last_exec
    remaining = max(0, int(cooldown - elapsed))
    return remaining


def record_execution(action_name: str, target: str = ""):
    """Record that an action was executed (for cooldown tracking)."""
    state = _load_cooldowns()
    key = f"{action_name}:{target}" if target else action_name
    state[key] = time.time()
    # Prune old entries (> 24h)
    cutoff = time.time() - 86400
    state = {k: v for k, v in state.items() if v > cutoff}
    _save_cooldowns(state)


# ── Core evaluation ─────────────────────────────────────────────

def evaluate(action_name: str, target: str = "",
             confidence: float = 0.0, context: str = "") -> PolicyDecision:
    """Evaluate whether an action should execute, need approval, or be blocked.

    Args:
        action_name: registered action name (from action_registry)
        target: target of the action (container name, model, etc.)
        confidence: detection confidence (0-1), affects MEDIUM risk decisions
        context: human-readable context for logging

    Returns:
        PolicyDecision with execute_now=True if safe to proceed immediately.
    """
    # Step 1: Check action_registry (inherits docker_wrapper + safety_guard whitelists)
    registry_check = can_execute(action_name, target)

    if not registry_check["allowed"]:
        decision = PolicyDecision(
            action_name=action_name, target=target,
            allowed=False, execute_now=False,
            requires_approval=False,
            risk_level=registry_check["risk_level"],
            reason=registry_check["reason"],
            confidence=confidence,
        )
        _log_decision(decision, context)
        return decision

    # Step 2: Map docker actions for safety_guard validation
    sg_action = _map_to_safety_guard_action(action_name)
    if sg_action:
        sg_result = validate_action(sg_action, target, context)
        if not sg_result["allowed"]:
            decision = PolicyDecision(
                action_name=action_name, target=target,
                allowed=False, execute_now=False,
                requires_approval=False,
                risk_level=RISK_HIGH,
                reason=f"safety_guard blocked: {sg_result['reason']}",
                confidence=confidence,
            )
            _log_decision(decision, context)
            return decision

    # Step 3: Get risk level from registry
    action_def = get_action(action_name)
    risk = action_def["risk_level"] if action_def else RISK_HIGH
    requires_approval = action_def["requires_approval"] if action_def else True

    # Step 4: Check cooldown
    cooldown_remaining = _check_cooldown(action_name, target)

    # Step 5: Apply risk policy
    if risk == RISK_SAFE:
        execute_now = True
        reason = "SAFE action — auto-execute"
    elif risk == RISK_LOW:
        if cooldown_remaining > 0:
            execute_now = False
            reason = f"Cooldown active: {cooldown_remaining}s remaining"
        else:
            execute_now = True
            reason = "LOW risk — auto-execute (cooldown clear)"
    elif risk == RISK_MEDIUM:
        if requires_approval:
            execute_now = False
            reason = "MEDIUM risk — requires approval"
        elif confidence >= 0.9 and cooldown_remaining == 0:
            execute_now = True
            reason = f"MEDIUM risk — auto-execute (confidence={confidence:.2f})"
        else:
            execute_now = False
            requires_approval = True
            reason = f"MEDIUM risk — approval needed (confidence={confidence:.2f})"
    else:  # HIGH or unknown
        execute_now = False
        requires_approval = True
        reason = "HIGH risk — always requires approval"

    decision = PolicyDecision(
        action_name=action_name, target=target,
        allowed=True, execute_now=execute_now,
        requires_approval=requires_approval,
        risk_level=risk, reason=reason,
        cooldown_remaining=cooldown_remaining,
        confidence=confidence,
    )
    _log_decision(decision, context)
    return decision


def _map_to_safety_guard_action(action_name: str) -> str | None:
    """Map registry action name to safety_guard action string."""
    mapping = {
        "restart_container": "docker restart",
        "start_container": "docker start",
        "stop_container": "docker stop",
        "restart_vpn": "docker restart",
    }
    return mapping.get(action_name)


def _log_decision(decision: PolicyDecision, context: str):
    _log({
        "decision": asdict(decision),
        "context": context[:200] if context else "",
    })


# ── Batch evaluation ────────────────────────────────────────────

def evaluate_incident(incident: dict) -> PolicyDecision:
    """Evaluate an incident (conforming to incident_schema) and decide action.

    Extracts action_name and target from incident.recommended_action.
    """
    rec = incident.get("recommended_action", {})
    action_name = rec.get("action_name", "")
    target = rec.get("target", "")
    confidence = incident.get("confidence", 0.0)
    context = incident.get("title", "")

    if not action_name:
        return PolicyDecision(
            action_name="", target=target,
            allowed=False, execute_now=False,
            requires_approval=False,
            risk_level=RISK_HIGH,
            reason="No recommended action in incident",
            confidence=confidence,
        )

    return evaluate(action_name, target, confidence, context)
