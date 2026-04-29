#!/usr/bin/env python3
"""
temporal_guard.py
Time-based filter for proposals, actions, and notifications.

Windows (America/Argentina/Buenos_Aires):
  night   (00:00-07:00): only critical incidents pass, no suggestions
  morning (07:00-12:00): prioritize calendar+finance, infra only if problem
  work    (12:00-20:00): everything enabled, active window
  evening (20:00-00:00): no service restarts, informational only

Extra rule: if Google Calendar event in next 30 min → suppress non-critical
notifications to avoid distraction.

Main method: temporal_guard.should_notify(severity, category) → bool

Usage:
  python3 temporal_guard.py --status    # show current window + rules
  python3 temporal_guard.py --test      # test all windows
"""

import json
import subprocess
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")
GCAL_SCRIPT = "/home/tenazas/.openclaw/workspace/skills/calendar-agenda/gcal.py"
LOG_PATH = Path(__file__).parent / "logs" / "temporal_guard.log"


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")


# ── Time windows ───────────────────────────────────────────────

def current_window(now: datetime | None = None) -> str:
    """Get current time window."""
    if now is None:
        now = datetime.now(TZ)
    hour = now.hour
    if 0 <= hour < 7:
        return "night"
    elif 7 <= hour < 12:
        return "morning"
    elif 12 <= hour < 20:
        return "work"
    else:  # 20-24
        return "evening"


def _has_upcoming_event(minutes: int = 30) -> bool:
    """Check if there's a Google Calendar event in the next N minutes."""
    try:
        r = subprocess.run(
            ["python3", GCAL_SCRIPT, "1"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode != 0 or not r.stdout.strip():
            return False

        now = datetime.now().astimezone()
        cutoff = now + timedelta(minutes=minutes)

        for line in r.stdout.strip().splitlines():
            if "|" not in line:
                continue
            ts_str = line.split("|", 1)[0].strip()
            try:
                event_dt = datetime.fromisoformat(ts_str)
                if now <= event_dt <= cutoff:
                    return True
            except Exception:
                continue
    except Exception:
        pass
    return False


# ── Main filter ────────────────────────────────────────────────

def should_notify(severity: str, category: str,
                  now: datetime | None = None,
                  skip_calendar_check: bool = False) -> bool:
    """Decide if a notification should be sent based on time context.

    Args:
        severity: "critical", "warning", "info"
        category: "infra", "calendar", "finance", "notification",
                  "suggestion", "goal", "approval", etc.
        now: override current time (for testing)
        skip_calendar_check: skip Google Calendar proximity check (for testing)

    Returns:
        True if the notification should be sent.
    """
    window = current_window(now)

    # Critical always passes in all windows
    if severity == "critical":
        _log({"action": "allow", "severity": severity, "category": category,
              "window": window, "reason": "critical_always_passes"})
        return True

    # Approvals always pass (user needs to decide)
    if category == "approval":
        return True

    # Night: only critical passes (already handled above)
    if window == "night":
        _log({"action": "block", "severity": severity, "category": category,
              "window": window, "reason": "night_non_critical"})
        return False

    # Morning: calendar + finance + critical only; infra only if warning+
    if window == "morning":
        if category in ("calendar", "finance"):
            return True
        if category == "infra" and severity == "warning":
            return True
        if severity == "info" or category == "suggestion":
            _log({"action": "block", "severity": severity, "category": category,
                  "window": window, "reason": "morning_info_blocked"})
            return False
        return severity in ("critical", "warning")

    # Work: everything enabled
    if window == "work":
        # Extra rule: upcoming calendar event → suppress non-critical
        if not skip_calendar_check and severity != "critical":
            try:
                if _has_upcoming_event(30):
                    if severity == "info" or category == "suggestion":
                        _log({"action": "block", "severity": severity, "category": category,
                              "window": window, "reason": "upcoming_event"})
                        return False
            except Exception:
                pass
        return True

    # Evening: no service restarts, informational only
    if window == "evening":
        if category == "infra" and severity != "critical":
            _log({"action": "block", "severity": severity, "category": category,
                  "window": window, "reason": "evening_no_infra"})
            return False
        if category == "suggestion":
            return severity in ("critical", "warning")
        return severity in ("critical", "warning")

    return True


def should_allow_action(action_name: str, severity: str = "info",
                        now: datetime | None = None) -> bool:
    """Check if an action should be allowed based on time.

    Specifically blocks service restarts during evening window.
    """
    window = current_window(now)

    if severity == "critical":
        return True

    # Evening: no restarts
    if window == "evening" and "restart" in action_name:
        _log({"action": "block_action", "action_name": action_name,
              "window": window, "reason": "evening_no_restarts"})
        return False

    # Night: only critical actions
    if window == "night" and severity != "critical":
        _log({"action": "block_action", "action_name": action_name,
              "window": window, "reason": "night_non_critical"})
        return False

    return True


def get_context(now: datetime | None = None) -> dict:
    """Get current temporal context for enriching decisions."""
    if now is None:
        now = datetime.now(TZ)
    return {
        "window": current_window(now),
        "hour": now.hour,
        "day_name": ["Lunes", "Martes", "Miercoles", "Jueves",
                     "Viernes", "Sabado", "Domingo"][now.weekday()],
        "is_weekend": now.weekday() >= 5,
    }


# ── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Temporal guard")
    p.add_argument("--status", action="store_true", help="Show current window")
    p.add_argument("--test", action="store_true", help="Test all windows")
    args = p.parse_args()

    if args.status:
        ctx = get_context()
        print(f"Window: {ctx['window']} ({ctx['day_name']} {ctx['hour']}:00)")
        print(f"Weekend: {ctx['is_weekend']}")
        print("\nRules for {window}:".format(**ctx))
        window = ctx["window"]
        if window == "night":
            print("  - Only critical incidents pass")
        elif window == "morning":
            print("  - Calendar + finance priority")
            print("  - Infra only if warning+")
            print("  - No suggestions/info")
        elif window == "work":
            print("  - Everything enabled")
            print("  - Upcoming events suppress non-critical")
        elif window == "evening":
            print("  - No service restarts")
            print("  - Informational suggestions only if warning+")

    elif args.test:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo("America/Argentina/Buenos_Aires")
        test_cases = [
            (3, "critical", "infra", True),
            (3, "warning", "infra", False),
            (3, "info", "calendar", False),
            (8, "info", "calendar", True),
            (8, "info", "finance", True),
            (8, "info", "suggestion", False),
            (8, "warning", "infra", True),
            (14, "info", "suggestion", True),
            (14, "warning", "infra", True),
            (21, "warning", "infra", False),
            (21, "critical", "infra", True),
            (21, "info", "suggestion", False),
        ]
        passed = 0
        for hour, sev, cat, expected in test_cases:
            now = datetime(2026, 4, 14, hour, 0, tzinfo=tz)
            result = should_notify(sev, cat, now=now, skip_calendar_check=True)
            ok = result == expected
            status = "OK" if ok else "FAIL"
            if ok:
                passed += 1
            print(f"  [{status}] {hour:02d}:00 {sev:8s} {cat:12s} -> {result} (expected {expected})")
        print(f"\n{passed}/{len(test_cases)} passed")
    else:
        p.print_help()
