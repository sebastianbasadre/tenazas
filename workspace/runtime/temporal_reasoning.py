#!/usr/bin/env python3
"""
temporal_reasoning.py
Time-aware filtering for the proactive planner.

Rules:
  - Night (23-07): only critical infra alerts, no suggestions
  - Morning (07-10): calendar priority, finance daily
  - Work (10-18): all categories active
  - Evening (18-23): reduced priority, no infra noise

Integrates with proactive_planner to filter suggestions by time context.
"""

from datetime import datetime
from zoneinfo import ZoneInfo

TZ = ZoneInfo("America/Argentina/Buenos_Aires")

# Time windows
WINDOWS = {
    "night":   (23, 7),   # sleep — critical only
    "morning": (7, 10),   # wake up — calendar + finance
    "work":    (10, 18),  # active — everything
    "evening": (18, 23),  # wind down — reduced
}


def current_window() -> str:
    """Get current time window."""
    hour = datetime.now(TZ).hour
    if 7 <= hour < 10:
        return "morning"
    elif 10 <= hour < 18:
        return "work"
    elif 18 <= hour < 23:
        return "evening"
    else:
        return "night"


def should_notify(suggestion: dict) -> bool:
    """Decide if a suggestion should be sent to Telegram based on time."""
    window = current_window()
    priority = suggestion.get("priority", "low")
    stype = suggestion.get("type", "")

    if window == "night":
        # Only critical infra (container down, network down)
        return priority == "high" and stype in ("infra", "gpu")

    if window == "morning":
        # Calendar + finance + critical
        return stype in ("calendar", "finance") or priority == "high"

    if window == "work":
        # Everything except low priority
        return priority in ("high", "medium")

    if window == "evening":
        # Only high priority
        return priority == "high"

    return False


def get_time_context() -> dict:
    """Get current time context for enriching responses."""
    now = datetime.now(TZ)
    return {
        "window": current_window(),
        "hour": now.hour,
        "day_name": ["Lunes", "Martes", "Miercoles", "Jueves",
                     "Viernes", "Sabado", "Domingo"][now.weekday()],
        "is_weekend": now.weekday() >= 5,
        "greeting": _greeting(now.hour),
    }


def _greeting(hour: int) -> str:
    if 5 <= hour < 12:
        return "Buen dia"
    elif 12 <= hour < 19:
        return "Buenas tardes"
    else:
        return "Buenas noches"


def filter_suggestions(suggestions: list[dict]) -> list[dict]:
    """Filter suggestions based on current time window."""
    return [s for s in suggestions if should_notify(s)]


if __name__ == "__main__":
    ctx = get_time_context()
    print(f"Window: {ctx['window']} ({ctx['day_name']} {ctx['hour']}:00)")
    print(f"Greeting: {ctx['greeting']}")
    print(f"Weekend: {ctx['is_weekend']}")

    # Test filtering
    test = [
        {"type": "calendar", "priority": "medium", "detail": "Evento en 1h"},
        {"type": "infra", "priority": "high", "detail": "Container caido"},
        {"type": "feedback", "priority": "low", "detail": "3 negativos"},
    ]
    filtered = filter_suggestions(test)
    print(f"\nFiltered {len(test)} → {len(filtered)} for {ctx['window']}:")
    for s in filtered:
        print(f"  [{s['priority']}] {s['detail']}")
