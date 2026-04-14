#!/usr/bin/env python3
"""
action_registry.py
Single source of truth for all actions Tenazas can execute.

EXTENDS (does not duplicate):
  - docker_wrapper.py — imports ALLOWED_ACTIONS, BLOCKED_CONTAINERS
  - safety_guard.py — imports PROTECTED_CONTAINERS, ARR_CONTAINERS

Each action defines: name, category, risk_level, requires_approval,
cooldown_seconds, max_retries, validator_name, rollback action.

Risk levels:
  SAFE   — auto-execute (read-only, no side effects)
  MEDIUM — auto-execute only with cooldown + validation
  LOW    — auto-execute (restarts of arr-stack, well-tested)
  HIGH   — always requires Sebas approval

Usage:
  from action_registry import get_action, list_actions, can_execute
"""

import json
from datetime import datetime
from pathlib import Path

# Import existing security rules — single source of truth
from docker_wrapper import ALLOWED_ACTIONS as DOCKER_ALLOWED, BLOCKED_CONTAINERS
from safety_guard import PROTECTED_CONTAINERS, ARR_CONTAINERS

LOG_PATH = Path(__file__).parent / "logs" / "action_registry.log"

RISK_SAFE = "safe"
RISK_LOW = "low"
RISK_MEDIUM = "medium"
RISK_HIGH = "high"

# ── Action definitions ──────────────────────────────────────────
# Each action is a dict. The registry is a flat list for simplicity.
# validator_name maps to action_validator.py functions.

ACTIONS = [
    # ── Docker operations (sourced from docker_wrapper whitelist) ──
    {
        "name": "restart_container",
        "category": "docker",
        "description": "Restart a Docker container",
        "risk_level": RISK_LOW,
        "requires_approval": False,
        "cooldown_seconds": 1200,   # 20 min (matches infra_self_heal)
        "max_retries": 2,
        "validator_name": "validate_restart_container",
        "rollback": {"action": "restart_container", "description": "Restart again if unhealthy"},
        "allowed_targets": list(ARR_CONTAINERS),
        "blocked_targets": list(PROTECTED_CONTAINERS | BLOCKED_CONTAINERS),
    },
    {
        "name": "start_container",
        "category": "docker",
        "description": "Start a stopped Docker container",
        "risk_level": RISK_LOW,
        "requires_approval": False,
        "cooldown_seconds": 600,
        "max_retries": 2,
        "validator_name": "validate_restart_container",
        "rollback": {"action": "stop_container", "description": "Stop container if start caused issues"},
        "allowed_targets": list(ARR_CONTAINERS),
        "blocked_targets": list(PROTECTED_CONTAINERS | BLOCKED_CONTAINERS),
    },
    {
        "name": "stop_container",
        "category": "docker",
        "description": "Stop a running Docker container",
        "risk_level": RISK_MEDIUM,
        "requires_approval": True,
        "cooldown_seconds": 600,
        "max_retries": 1,
        "validator_name": None,
        "rollback": {"action": "start_container", "description": "Restart the stopped container"},
        "allowed_targets": list(ARR_CONTAINERS),
        "blocked_targets": list(PROTECTED_CONTAINERS | BLOCKED_CONTAINERS),
    },

    # ── Ollama / Model operations ──
    {
        "name": "reload_model",
        "category": "ollama",
        "description": "Reload an Ollama model into VRAM",
        "risk_level": RISK_LOW,
        "requires_approval": False,
        "cooldown_seconds": 300,
        "max_retries": 2,
        "validator_name": "validate_reload_model",
        "rollback": None,
        "allowed_targets": ["gemma3:12b", "qwen3:8b", "qwen2.5:3b",
                            "nomic-embed-text", "deepseek-r1:14b",
                            "qwen2.5:7b", "qwen2.5:32b", "qwen2.5-coder:14b"],
        "blocked_targets": [],
    },

    # ── Network ──
    {
        "name": "restart_vpn",
        "category": "network",
        "description": "Restart gluetun VPN gateway",
        "risk_level": RISK_LOW,
        "requires_approval": False,
        "cooldown_seconds": 1200,
        "max_retries": 2,
        "validator_name": "validate_restart_container",
        "rollback": {"action": "restart_vpn", "description": "Restart gluetun again"},
        "allowed_targets": ["gluetun"],
        "blocked_targets": [],
    },
    {
        "name": "network_scan",
        "category": "diagnostic",
        "description": "Scan LAN with nmap",
        "risk_level": RISK_SAFE,
        "requires_approval": False,
        "cooldown_seconds": 1800,  # 30 min (matches nmap cache)
        "max_retries": 1,
        "validator_name": None,
        "rollback": None,
        "allowed_targets": ["192.168.2.0/24"],
        "blocked_targets": [],
    },

    # ── Diagnostics (read-only) ──
    {
        "name": "health_check",
        "category": "diagnostic",
        "description": "Run infrastructure health check",
        "risk_level": RISK_SAFE,
        "requires_approval": False,
        "cooldown_seconds": 60,
        "max_retries": 1,
        "validator_name": None,
        "rollback": None,
        "allowed_targets": [],
        "blocked_targets": [],
    },
    {
        "name": "check_ollama",
        "category": "diagnostic",
        "description": "Check Ollama status and loaded models",
        "risk_level": RISK_SAFE,
        "requires_approval": False,
        "cooldown_seconds": 60,
        "max_retries": 1,
        "validator_name": None,
        "rollback": None,
        "allowed_targets": [],
        "blocked_targets": [],
    },

    # ── Disk ──
    {
        "name": "cleanup_disk",
        "category": "maintenance",
        "description": "Clean old logs and temp files",
        "risk_level": RISK_MEDIUM,
        "requires_approval": True,
        "cooldown_seconds": 3600,
        "max_retries": 1,
        "validator_name": "validate_cleanup_disk",
        "rollback": None,
        "allowed_targets": [],
        "blocked_targets": [],
    },

    # ── Notifications (safe, no side effects on infra) ──
    {
        "name": "send_telegram",
        "category": "notification",
        "description": "Send a Telegram notification",
        "risk_level": RISK_SAFE,
        "requires_approval": False,
        "cooldown_seconds": 10,
        "max_retries": 2,
        "validator_name": None,
        "rollback": None,
        "allowed_targets": [],
        "blocked_targets": [],
    },
]

# Build lookup index
_ACTION_INDEX = {a["name"]: a for a in ACTIONS}


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Public API ──────────────────────────────────────────────────

def get_action(name: str) -> dict | None:
    """Get action definition by name. Returns None if unknown."""
    return _ACTION_INDEX.get(name)


def list_actions(category: str | None = None, risk_level: str | None = None) -> list[dict]:
    """List actions, optionally filtered by category or risk level."""
    result = ACTIONS
    if category:
        result = [a for a in result if a["category"] == category]
    if risk_level:
        result = [a for a in result if a["risk_level"] == risk_level]
    return result


def can_execute(action_name: str, target: str = "") -> dict:
    """Check if an action can be executed against a target.

    Returns: {"allowed": bool, "reason": str, "requires_approval": bool, "risk_level": str}

    Delegates container validation to existing safety_guard rules.
    """
    action = get_action(action_name)
    if not action:
        _log({"check": "can_execute", "action": action_name, "target": target,
              "allowed": False, "reason": "unknown_action"})
        return {"allowed": False, "reason": f"Unknown action: {action_name}",
                "requires_approval": True, "risk_level": RISK_HIGH}

    # Check blocked targets (inherits from docker_wrapper + safety_guard)
    if target and target.lower() in {t.lower() for t in action.get("blocked_targets", [])}:
        reason = f"Target '{target}' is blocked for action '{action_name}'"
        _log({"check": "can_execute", "action": action_name, "target": target,
              "allowed": False, "reason": "blocked_target"})
        return {"allowed": False, "reason": reason,
                "requires_approval": True, "risk_level": RISK_HIGH}

    # Check allowed targets if list is non-empty (whitelist mode)
    allowed_targets = action.get("allowed_targets", [])
    if allowed_targets and target:
        if target.lower() not in {t.lower() for t in allowed_targets}:
            reason = f"Target '{target}' not in allowed list for '{action_name}'"
            _log({"check": "can_execute", "action": action_name, "target": target,
                  "allowed": False, "reason": "not_in_whitelist"})
            return {"allowed": False, "reason": reason,
                    "requires_approval": True, "risk_level": action["risk_level"]}

    _log({"check": "can_execute", "action": action_name, "target": target,
          "allowed": True, "risk_level": action["risk_level"]})
    return {
        "allowed": True,
        "reason": "Action permitted",
        "requires_approval": action["requires_approval"],
        "risk_level": action["risk_level"],
    }


def get_cooldown(action_name: str) -> int:
    """Get cooldown in seconds for an action."""
    action = get_action(action_name)
    return action["cooldown_seconds"] if action else 0


def get_rollback(action_name: str) -> dict | None:
    """Get rollback definition for an action."""
    action = get_action(action_name)
    return action.get("rollback") if action else None
