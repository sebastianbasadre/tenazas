#!/usr/bin/env python3
"""
Contextual Awareness — Decide when to interrupt Sebas and when not to.

Sources: Google Calendar, temporal_guard, Telegram activity logs, incidents.

Rules:
- Active calendar event → no interrupt (except critical)
- Event in < 15 min → only send event reminder
- No Telegram activity > 4h → assume absent, accumulate
- No activity > 8h → send accumulated summary on return
- Critical incident → ALWAYS interrupt

Usage:
    python3 contextual_awareness.py --status    # show current context
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
from runtime_utils import now
try:
    from .runtime_utils import load_json, save_json
except ImportError:
    from runtime_utils import load_json, save_json

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AWARENESS_FILE = STATE_DIR / "contextual_awareness.json"
ACCUMULATED_FILE = STATE_DIR / "accumulated_notifications.json"
LOG_FILE = BASE_DIR / "logs" / "contextual_awareness.log"
TELEGRAM_LOG = BASE_DIR / "logs" / "telegram_handler.log"

ABSENT_THRESHOLD_HOURS = 4
LONG_ABSENT_THRESHOLD_HOURS = 8



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


# ── Context sources ─────────────────────────────────────────────


def _check_calendar_busy() -> dict:
    """Check if there's an active event or one starting soon."""
    try:
        from notification_poller import check_calendar
        events = check_calendar()
        if not events:
            return {"busy": False, "upcoming_minutes": None}

        _now = now()
        for event in events:
            start_str = event.get("start", event.get("time", ""))
            try:
                # Parse time (HH:MM format expected from check_calendar)
                if len(start_str) <= 5:
                    h, m = start_str.split(":")
                    start = _now.replace(hour=int(h), minute=int(m), second=0)
                else:
                    start = datetime.fromisoformat(start_str)

                diff = (start - _now).total_seconds() / 60
                if -60 <= diff <= 0:
                    return {"busy": True, "event": event.get("title", "?"),
                            "upcoming_minutes": 0}
                if 0 < diff <= 15:
                    return {"busy": False, "upcoming_minutes": round(diff),
                            "event": event.get("title", "?")}
            except (ValueError, TypeError):
                continue

        return {"busy": False, "upcoming_minutes": None}
    except Exception:
        return {"busy": False, "upcoming_minutes": None}


def _get_last_telegram_activity() -> float:
    """Get timestamp of last Telegram message from Sebas."""
    try:
        # Read last lines of telegram handler log
        with open(TELEGRAM_LOG) as f:
            lines = f.readlines()
        for line in reversed(lines[-100:]):
            try:
                entry = json.loads(line.strip())
                if entry.get("action") in ("intercepted", "delegating_to_llm",
                                           "skill_response", "llm_response"):
                    return datetime.fromisoformat(entry["ts"]).timestamp()
            except (json.JSONDecodeError, ValueError, KeyError):
                continue
    except FileNotFoundError:
        pass
    return 0


def _check_active_critical() -> bool:
    """Check if there's an active critical incident."""
    try:
        from infra_diagnostics import quick_health_check
        health = quick_health_check()
        return health.get("critical_count", 0) > 0
    except Exception:
        return False


# ── Main API ────────────────────────────────────────────────────


def get_context() -> dict:
    """Build current awareness context."""
    _now = now()

    # Calendar
    cal = _check_calendar_busy()

    # Temporal guard
    try:
        from temporal_guard import get_context as tg_context
        time_ctx = tg_context()
    except Exception:
        time_ctx = {"window": "unknown", "hour": _now.hour}

    # Last activity
    last_ts = _get_last_telegram_activity()
    hours_since = (time.time() - last_ts) / 3600 if last_ts > 0 else 999

    # Critical incidents
    has_critical = _check_active_critical()

    context = {
        "ts": _now.isoformat(),
        "calendar_busy": cal.get("busy", False),
        "event_upcoming_min": cal.get("upcoming_minutes"),
        "event_name": cal.get("event"),
        "time_window": time_ctx.get("window", "unknown"),
        "hour": _now.hour,
        "hours_since_activity": round(hours_since, 1),
        "is_absent": hours_since >= ABSENT_THRESHOLD_HOURS,
        "is_long_absent": hours_since >= LONG_ABSENT_THRESHOLD_HOURS,
        "has_critical": has_critical,
    }

    return context


def can_interrupt(severity: str = "info") -> bool:
    """Decide if Tenazas should interrupt Sebas right now.

    Args:
        severity: "critical", "warning", "info"

    Returns: True if interruption is allowed
    """
    # Critical ALWAYS interrupts
    if severity == "critical":
        _log({"action": "can_interrupt", "severity": severity, "result": True,
              "reason": "critical always passes"})
        return True

    ctx = get_context()

    # Calendar event active → don't interrupt
    if ctx["calendar_busy"]:
        _log({"action": "can_interrupt", "severity": severity, "result": False,
              "reason": f"calendar busy: {ctx.get('event_name', '?')}"})
        return False

    # Event upcoming in < 15 min → only critical
    if ctx["event_upcoming_min"] is not None and ctx["event_upcoming_min"] <= 15:
        _log({"action": "can_interrupt", "severity": severity, "result": False,
              "reason": f"event in {ctx['event_upcoming_min']}min"})
        return False

    # Long absent → accumulate, don't interrupt
    if ctx["is_absent"]:
        _accumulate_notification(severity)
        _log({"action": "can_interrupt", "severity": severity, "result": False,
              "reason": f"absent {ctx['hours_since_activity']:.1f}h"})
        return False

    # Night window → only warning+ (from temporal_guard)
    try:
        from temporal_guard import should_notify
        if not should_notify(severity, "contextual_check"):
            _log({"action": "can_interrupt", "severity": severity, "result": False,
                  "reason": f"temporal_guard blocked in {ctx['time_window']}"})
            return False
    except Exception:
        pass

    _log({"action": "can_interrupt", "severity": severity, "result": True,
          "reason": "all checks passed"})
    return True


def _accumulate_notification(severity: str):
    """Store notification for later delivery."""
    accumulated = load_json(ACCUMULATED_FILE, {"notifications": []})
    accumulated["notifications"].append({
        "severity": severity,
        "ts": now().isoformat(),
    })
    # Cap at 50
    accumulated["notifications"] = accumulated["notifications"][-50:]
    save_json(ACCUMULATED_FILE, accumulated)


def get_accumulated() -> list:
    """Get accumulated notifications (for when Sebas returns)."""
    data = load_json(ACCUMULATED_FILE, {"notifications": []})
    return data.get("notifications", [])


def clear_accumulated():
    """Clear accumulated notifications after delivery."""
    save_json(ACCUMULATED_FILE, {"notifications": [], "cleared_at": now().isoformat()})


def format_status() -> str:
    ctx = get_context()
    lines = ["Contextual Awareness"]
    lines.append(f"  Window: {ctx['time_window']} ({ctx['hour']}:00)")
    lines.append(f"  Calendar: {'busy' if ctx['calendar_busy'] else 'free'}")
    if ctx['event_upcoming_min'] is not None:
        lines.append(f"  Event in: {ctx['event_upcoming_min']}min ({ctx.get('event_name', '?')})")
    lines.append(f"  Last activity: {ctx['hours_since_activity']:.1f}h ago")
    lines.append(f"  Status: {'absent' if ctx['is_absent'] else 'available'}")
    accumulated = get_accumulated()
    if accumulated:
        lines.append(f"  Accumulated: {len(accumulated)} pending")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Contextual Awareness")
    p.add_argument("--status", action="store_true")
    p.add_argument("--can-interrupt", type=str, metavar="SEVERITY")
    args = p.parse_args()

    if args.status:
        print(format_status())
    elif args.can_interrupt:
        result = can_interrupt(args.can_interrupt)
        print(f"Can interrupt ({args.can_interrupt}): {result}")
    else:
        p.print_help()
