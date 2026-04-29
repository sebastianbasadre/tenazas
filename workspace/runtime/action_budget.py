#!/usr/bin/env python3
"""
action_budget.py — Sprint 5: Governance & Stability

Prevents over-automation via rolling-window rate limits.

Limits:
  - max 10 actions per hour across all types
  - max 3 actions per type per hour
  - max 1 critical action per 6 hours
  - max 5 notifications to Telegram per 30 minutes

Fail-open: if budget module crashes, allow the action.

Integrates additively with:
  - policy_engine.py — check budget before allowing execution
  - approval_queue.py — check notification budget before sending to Telegram

Usage:
  python3 action_budget.py --status    # show current budget usage
  python3 action_budget.py --reset     # reset all counters
"""

import json
import logging
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

try:
    from .runtime_config import (
        get_default_telegram_chat_id,
        get_telegram_bot_token,
        load_config,
    )
except ImportError:
    from runtime_config import (
        get_default_telegram_chat_id,
        get_telegram_bot_token,
        load_config,
    )

# ── Paths ─────────────────────────────────────────────────────

BASE = Path(__file__).parent
STATE_PATH = BASE.parent.parent / "state" / "action_budget.json"
LOG_PATH = BASE / "logs" / "action_budget.log"

# ── Limits ────────────────────────────────────────────────────

MAX_ACTIONS_PER_HOUR = 10
MAX_PER_TYPE_PER_HOUR = 3
MAX_CRITICAL_PER_6H = 1
MAX_NOTIFICATIONS_PER_30M = 5

WINDOW_1H = 3600
WINDOW_6H = 21600
WINDOW_30M = 1800

# ── Logging ───────────────────────────────────────────────────

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("action_budget")
logger.setLevel(logging.INFO)
if not logger.handlers:
    h = RotatingFileHandler(LOG_PATH, maxBytes=5_000_000, backupCount=3)
    h.setFormatter(logging.Formatter("%(message)s"))
    logger.addHandler(h)


def _log(entry: dict):
    entry["ts"] = datetime.now().astimezone().isoformat()
    logger.info(json.dumps(entry, default=str, ensure_ascii=False))


# ── State ─────────────────────────────────────────────────────

def _load_state() -> dict:
    if STATE_PATH.exists():
        try:
            return json.loads(STATE_PATH.read_text())
        except Exception:
            pass
    return {
        "actions": [],           # [{ts, type, severity}]
        "notifications": [],     # [{ts}]
        "budget_exceeded_notified": {},  # type → ts (avoid spam about budget)
    }


def _save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, indent=2, default=str, ensure_ascii=False))


def _cleanup(state: dict, now: float) -> dict:
    """Remove entries outside all windows."""
    cutoff_6h = now - WINDOW_6H
    state["actions"] = [
        a for a in state.get("actions", [])
        if a.get("ts", 0) > cutoff_6h
    ]
    cutoff_30m = now - WINDOW_30M
    state["notifications"] = [
        n for n in state.get("notifications", [])
        if n.get("ts", 0) > cutoff_30m
    ]
    # Clean up old exceeded notifications (>1h old)
    cutoff_1h = now - WINDOW_1H
    exceeded = state.get("budget_exceeded_notified", {})
    state["budget_exceeded_notified"] = {
        k: v for k, v in exceeded.items() if v > cutoff_1h
    }
    return state


# ── Core ──────────────────────────────────────────────────────

def can_execute(action_type: str, severity: str = "info",
                _now: float | None = None) -> bool:
    """Check if an action is within budget.

    Args:
        action_type: type of action (e.g. "restart_container", "restart_vpn")
        severity: "critical", "warning", "info"

    Returns:
        True if action is allowed within budget.
        Always True on internal error (fail-open).
    """
    try:
        return _can_execute_inner(action_type, severity, _now)
    except Exception as e:
        _log({"action": "error", "error": str(e), "type": action_type,
              "result": "fail_open"})
        return True


def _can_execute_inner(action_type: str, severity: str,
                       _now: float | None) -> bool:
    now = _now if _now is not None else time.time()
    state = _load_state()
    state = _cleanup(state, now)

    actions = state.get("actions", [])
    cutoff_1h = now - WINDOW_1H
    cutoff_6h = now - WINDOW_6H

    # Check 1: Global hourly limit
    recent_1h = [a for a in actions if a["ts"] > cutoff_1h]
    if len(recent_1h) >= MAX_ACTIONS_PER_HOUR:
        _handle_exceeded(state, "global_hourly", action_type, now)
        return False

    # Check 2: Per-type hourly limit
    type_1h = [a for a in recent_1h if a.get("type") == action_type]
    if len(type_1h) >= MAX_PER_TYPE_PER_HOUR:
        _handle_exceeded(state, f"type_{action_type}", action_type, now)
        return False

    # Check 3: Critical action limit (6h window)
    if severity == "critical":
        critical_6h = [a for a in actions
                       if a["ts"] > cutoff_6h
                       and a.get("severity") == "critical"]
        if len(critical_6h) >= MAX_CRITICAL_PER_6H:
            _handle_exceeded(state, "critical_6h", action_type, now)
            return False

    # Budget OK — record the action
    state["actions"].append({
        "ts": now,
        "type": action_type,
        "severity": severity,
    })
    _save_state(state)
    _log({"action": "allowed", "type": action_type, "severity": severity,
          "total_1h": len(recent_1h) + 1})
    return True


def can_notify(_now: float | None = None) -> bool:
    """Check if a Telegram notification is within budget.

    Returns:
        True if notification is allowed.
        Always True on internal error (fail-open).
    """
    try:
        return _can_notify_inner(_now)
    except Exception as e:
        _log({"action": "error", "error": str(e), "result": "fail_open"})
        return True


def _can_notify_inner(_now: float | None) -> bool:
    now = _now if _now is not None else time.time()
    state = _load_state()
    state = _cleanup(state, now)

    notifications = state.get("notifications", [])
    cutoff_30m = now - WINDOW_30M
    recent_30m = [n for n in notifications if n["ts"] > cutoff_30m]

    if len(recent_30m) >= MAX_NOTIFICATIONS_PER_30M:
        _handle_exceeded(state, "notifications_30m", "telegram_notify", now)
        return False

    state["notifications"].append({"ts": now})
    _save_state(state)
    _log({"action": "notify_allowed", "total_30m": len(recent_30m) + 1})
    return True


def _handle_exceeded(state: dict, budget_type: str,
                     action_type: str, now: float):
    """Log budget exceeded. Notify Sebas ONCE per window, not per action."""
    _log({"action": "budget_exceeded", "budget_type": budget_type,
          "action_type": action_type})

    # Check if we already notified for ANY budget type this window
    notified = state.get("budget_exceeded_notified", {})
    already_notified_any = len(notified) > 0

    if budget_type not in notified:
        notified[budget_type] = now
        state["budget_exceeded_notified"] = notified

    _save_state(state)

    # Only send ONE Telegram message the first time any budget is exceeded
    # All subsequent exceeded events in the same window → log only
    if not already_notified_any:
        _notify_exceeded_once()


def _notify_exceeded_once():
    """Send a single friendly notification about paused automation."""
    # Use noise_filter if available for extra protection
    try:
        from runtime.noise_filter import should_send
        if not should_send("budget_exceeded", severity="warning"):
            return
    except Exception:
        pass  # noise_filter unavailable — proceed

    try:
        import json as _json
        from urllib.request import urlopen, Request

        config = load_config()
        token = get_telegram_bot_token(config)
        chat_id = get_default_telegram_chat_id(config)
        if not token or not chat_id:
            return
        text = ("\u23f8 Paus\u00e9 algunas acciones autom\u00e1ticas temporalmente "
                "\u2014 demasiada actividad en los \u00faltimos 30 min. "
                "Retomo en breve.")
        payload = _json.dumps({
            "chat_id": chat_id,
            "text": text,
        }).encode()
        urlopen(Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload,
            headers={"Content-Type": "application/json"},
        ), timeout=10)
    except Exception:
        pass  # Best-effort notification


# ── Management ────────────────────────────────────────────────

def reset():
    """Reset all budget counters."""
    _save_state({
        "actions": [],
        "notifications": [],
        "budget_exceeded_notified": {},
    })
    _log({"action": "reset"})


def get_status(_now: float | None = None) -> dict:
    """Get current budget usage."""
    now = _now if _now is not None else time.time()
    state = _load_state()
    state = _cleanup(state, now)

    cutoff_1h = now - WINDOW_1H
    cutoff_6h = now - WINDOW_6H
    cutoff_30m = now - WINDOW_30M

    actions = state.get("actions", [])
    notifications = state.get("notifications", [])

    actions_1h = [a for a in actions if a["ts"] > cutoff_1h]
    critical_6h = [a for a in actions
                   if a["ts"] > cutoff_6h
                   and a.get("severity") == "critical"]
    notifs_30m = [n for n in notifications if n["ts"] > cutoff_30m]

    # Per-type breakdown
    type_counts = {}
    for a in actions_1h:
        t = a.get("type", "unknown")
        type_counts[t] = type_counts.get(t, 0) + 1

    return {
        "actions_1h": len(actions_1h),
        "actions_1h_limit": MAX_ACTIONS_PER_HOUR,
        "critical_6h": len(critical_6h),
        "critical_6h_limit": MAX_CRITICAL_PER_6H,
        "notifications_30m": len(notifs_30m),
        "notifications_30m_limit": MAX_NOTIFICATIONS_PER_30M,
        "per_type_1h": type_counts,
        "per_type_limit": MAX_PER_TYPE_PER_HOUR,
    }


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Action budget")
    p.add_argument("--status", action="store_true", help="Show budget usage")
    p.add_argument("--reset", action="store_true", help="Reset all counters")
    args = p.parse_args()

    if args.reset:
        reset()
        print("Budget counters reset.")
    elif args.status:
        s = get_status()
        print(f"Action budget:")
        print(f"  Actions (1h):       {s['actions_1h']}/{s['actions_1h_limit']}")
        print(f"  Critical (6h):      {s['critical_6h']}/{s['critical_6h_limit']}")
        print(f"  Notifications (30m): {s['notifications_30m']}/{s['notifications_30m_limit']}")
        if s["per_type_1h"]:
            print(f"  Per type (1h):")
            for t, c in sorted(s["per_type_1h"].items()):
                print(f"    {t}: {c}/{s['per_type_limit']}")
    else:
        p.print_help()
