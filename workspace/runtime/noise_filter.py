#!/usr/bin/env python3
"""
noise_filter.py — Sprint 5: Governance & Stability

Prevents Tenazas from becoming annoying.

Rules:
  - Same alert type within 30 min → suppress (already notified)
  - Proposal of same type rejected before → suppress permanently (blacklist)
  - Metric fluctuation < 5% → not worth notifying
  - Backend offline during known maintenance window → suppress
  - If no action would change based on the info → do not notify

Fail-open: if noise_filter crashes, the notification goes through.

Integrates additively with:
  - proactive_planner.py — filter suggestions before sending
  - notification_poller.py — filter calendar/email alerts
  - infra_self_heal.py — filter repair notifications

Usage:
  python3 noise_filter.py --status     # show filter state
  python3 noise_filter.py --clear      # clear all TTL entries
  python3 noise_filter.py --stats      # suppression stats
"""

import json
import logging
import time
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

# ── Paths ─────────────────────────────────────────────────────

BASE = Path(__file__).parent
STATE_PATH = BASE.parent.parent / "state" / "noise_filter_state.json"
LOG_PATH = BASE / "logs" / "noise_filter.log"

# ── Config ────────────────────────────────────────────────────

SUPPRESSION_TTL = 1800       # 30 minutes in seconds
FLUCTUATION_THRESHOLD = 0.05  # 5% change required to notify
MAINTENANCE_WINDOWS = {
    # hour ranges (24h) where backend_offline is expected
    "backup": (2, 4),   # HyperBackup 2-3AM
}

# ── Logging ───────────────────────────────────────────────────

LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
logger = logging.getLogger("noise_filter")
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
        "recent_notifications": {},  # type → unix_ts
        "suppressed_count": 0,
        "allowed_count": 0,
        "metric_baselines": {},      # metric_name → last_value
    }


def _save_state(state: dict):
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(
        json.dumps(state, indent=2, default=str, ensure_ascii=False))


def _get_blacklist() -> list:
    """Read blacklist from preference_learning (additive integration)."""
    try:
        from runtime.preference_learning import get_blacklist
        return get_blacklist()
    except Exception:
        return []


def _cleanup_expired(state: dict) -> dict:
    """Remove expired TTL entries."""
    now = time.time()
    recent = state.get("recent_notifications", {})
    state["recent_notifications"] = {
        k: v for k, v in recent.items()
        if now - v < SUPPRESSION_TTL
    }
    return state


# ── Core filter ───────────────────────────────────────────────

def should_send(notification_type: str, content: str = "",
                severity: str = "info",
                metric_value: float | None = None,
                metric_name: str | None = None,
                _now: float | None = None) -> bool:
    """Decide if a notification should be sent.

    Args:
        notification_type: category of notification (e.g. "container_down",
                          "proposal_routing", "vpn_leak", etc.)
        content: notification text (for logging)
        severity: "critical", "warning", "info"
        metric_value: if this is a metric notification, the current value
        metric_name: name of the metric being reported
        _now: override current time (for testing)

    Returns:
        True if notification should go through.
        Always True on internal error (fail-open).
    """
    try:
        return _should_send_inner(
            notification_type, content, severity,
            metric_value, metric_name, _now)
    except Exception as e:
        # Fail open — if filter crashes, let the notification through
        _log({"action": "error", "error": str(e), "type": notification_type,
              "result": "fail_open"})
        return True


def _should_send_inner(notification_type: str, content: str,
                       severity: str, metric_value: float | None,
                       metric_name: str | None,
                       _now: float | None) -> bool:
    now = _now if _now is not None else time.time()
    state = _load_state()
    state = _cleanup_expired(state)

    # Rule 0: Critical always passes
    if severity == "critical":
        state["allowed_count"] = state.get("allowed_count", 0) + 1
        state["recent_notifications"][notification_type] = now
        _save_state(state)
        _log({"action": "allow", "type": notification_type,
              "reason": "critical_always_passes"})
        return True

    # Rule 1: Same alert type within TTL → suppress
    last_sent = state.get("recent_notifications", {}).get(notification_type)
    if last_sent is not None and (now - last_sent) < SUPPRESSION_TTL:
        state["suppressed_count"] = state.get("suppressed_count", 0) + 1
        _save_state(state)
        _log({"action": "suppress", "type": notification_type,
              "reason": "ttl_active",
              "seconds_remaining": int(SUPPRESSION_TTL - (now - last_sent))})
        return False

    # Rule 2: Blacklisted type → suppress permanently
    blacklist = _get_blacklist()
    if notification_type in blacklist:
        state["suppressed_count"] = state.get("suppressed_count", 0) + 1
        _save_state(state)
        _log({"action": "suppress", "type": notification_type,
              "reason": "blacklisted"})
        return False

    # Rule 3: Metric fluctuation < threshold → not worth notifying
    if metric_value is not None and metric_name:
        baselines = state.get("metric_baselines", {})
        prev = baselines.get(metric_name)
        if prev is not None and prev != 0:
            change = abs(metric_value - prev) / abs(prev)
            if change < FLUCTUATION_THRESHOLD:
                state["suppressed_count"] = state.get("suppressed_count", 0) + 1
                baselines[metric_name] = metric_value
                state["metric_baselines"] = baselines
                _save_state(state)
                _log({"action": "suppress", "type": notification_type,
                      "reason": "low_fluctuation",
                      "change_pct": round(change * 100, 2)})
                return False
        # Update baseline
        baselines[metric_name] = metric_value
        state["metric_baselines"] = baselines

    # Rule 4: Backend offline during maintenance window → suppress
    if notification_type in ("backend_offline", "ollama_offline", "backup_warning"):
        current_hour = datetime.now().hour
        for window_name, (start, end) in MAINTENANCE_WINDOWS.items():
            if start <= current_hour < end:
                state["suppressed_count"] = state.get("suppressed_count", 0) + 1
                _save_state(state)
                _log({"action": "suppress", "type": notification_type,
                      "reason": f"maintenance_window_{window_name}"})
                return False

    # Rule 5: No actionable info → suppress info-level non-events
    if severity == "info" and notification_type.endswith("_stable"):
        state["suppressed_count"] = state.get("suppressed_count", 0) + 1
        _save_state(state)
        _log({"action": "suppress", "type": notification_type,
              "reason": "stable_non_actionable"})
        return False

    # Passed all filters — allow
    state["recent_notifications"][notification_type] = now
    state["allowed_count"] = state.get("allowed_count", 0) + 1
    _save_state(state)
    _log({"action": "allow", "type": notification_type, "severity": severity})
    return True


# ── Management ────────────────────────────────────────────────

def clear_state():
    """Reset all filter state."""
    _save_state({
        "recent_notifications": {},
        "suppressed_count": 0,
        "allowed_count": 0,
        "metric_baselines": {},
    })
    _log({"action": "state_cleared"})


def get_stats() -> dict:
    """Get suppression statistics."""
    state = _load_state()
    state = _cleanup_expired(state)
    return {
        "suppressed_count": state.get("suppressed_count", 0),
        "allowed_count": state.get("allowed_count", 0),
        "active_ttls": len(state.get("recent_notifications", {})),
        "tracked_metrics": len(state.get("metric_baselines", {})),
    }


# ── CLI ───────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Noise filter")
    p.add_argument("--status", action="store_true", help="Show filter state")
    p.add_argument("--clear", action="store_true", help="Clear all TTL entries")
    p.add_argument("--stats", action="store_true", help="Suppression stats")
    args = p.parse_args()

    if args.clear:
        clear_state()
        print("Filter state cleared.")
    elif args.stats or args.status:
        s = get_stats()
        print(f"Noise filter stats:")
        print(f"  Suppressed: {s['suppressed_count']}")
        print(f"  Allowed:    {s['allowed_count']}")
        print(f"  Active TTLs: {s['active_ttls']}")
        print(f"  Tracked metrics: {s['tracked_metrics']}")
    else:
        p.print_help()
