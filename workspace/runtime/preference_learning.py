#!/usr/bin/env python3
"""
Preference Learning — Learn how Sebas prefers to operate.

Tracks signals from approvals, rejections, Telegram activity, and skill usage
to build a preference profile that influences routing and planning.

Signals:
- approval: action category approved → increase confidence
- rejection: action category rejected → decrease + record reason
- telegram_activity: hours Sebas responds → update temporal windows
- skill_usage: skill executed → track frequency
- rejection_pattern: same type rejected 2+ times → blacklist

Storage: state/preferences.json
Log: logs/preference_learning.log

Usage:
    python3 preference_learning.py --show          # show current preferences
    python3 preference_learning.py --top-skills    # most used skills
    python3 preference_learning.py --blacklist     # never-propose types
"""

import json
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
PREFS_FILE = STATE_DIR / "preferences.json"
LOG_FILE = BASE_DIR / "logs" / "preference_learning.log"

# Confidence decay/growth per signal
APPROVAL_BOOST = 0.05
REJECTION_PENALTY = 0.10
BLACKLIST_THRESHOLD = 2  # rejections of same type → blacklist
DEFAULT_CONFIDENCE = 0.5
MIN_CONFIDENCE = 0.1
MAX_CONFIDENCE = 0.95


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def _load_prefs() -> dict:
    try:
        with open(PREFS_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "confidence": {},        # category → float 0-1
            "rejections": {},        # category → [{"reason": str, "ts": str}]
            "blacklist": [],         # types never to propose again
            "skill_usage": {},       # skill → count
            "telegram_hours": {},    # hour (str) → count
            "signals": [],           # last 200 raw signals
        }


def _save_prefs(prefs: dict):
    PREFS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PREFS_FILE, "w") as f:
        json.dump(prefs, f, indent=2, default=str)


# ── Core API ────────────────────────────────────────────────────


def record_signal(signal_type: str, category: str, reason: str = "", metadata: dict = None):
    """Record a preference signal.

    Args:
        signal_type: "approval", "rejection", "skill_usage", "telegram_activity"
        category: action/skill category (e.g. "restart_container", "skill:weather")
        reason: rejection reason if applicable
        metadata: extra context
    """
    prefs = _load_prefs()

    signal = {
        "type": signal_type,
        "category": category,
        "reason": reason,
        "ts": _now().isoformat(),
        "metadata": metadata or {},
    }

    # Keep last 200 signals
    prefs["signals"].append(signal)
    prefs["signals"] = prefs["signals"][-200:]

    if signal_type == "approval":
        conf = prefs["confidence"].get(category, DEFAULT_CONFIDENCE)
        conf = min(conf + APPROVAL_BOOST, MAX_CONFIDENCE)
        prefs["confidence"][category] = round(conf, 3)
        _log({"action": "approval", "category": category, "confidence": conf})

    elif signal_type == "rejection":
        conf = prefs["confidence"].get(category, DEFAULT_CONFIDENCE)
        conf = max(conf - REJECTION_PENALTY, MIN_CONFIDENCE)
        prefs["confidence"][category] = round(conf, 3)

        # Track rejection reason
        if category not in prefs["rejections"]:
            prefs["rejections"][category] = []
        prefs["rejections"][category].append({
            "reason": reason, "ts": _now().isoformat()
        })
        # Keep last 10 rejections per category
        prefs["rejections"][category] = prefs["rejections"][category][-10:]

        # Check blacklist threshold
        rejection_count = len(prefs["rejections"][category])
        if rejection_count >= BLACKLIST_THRESHOLD and category not in prefs["blacklist"]:
            prefs["blacklist"].append(category)
            _log({"action": "blacklisted", "category": category,
                  "rejections": rejection_count})

        _log({"action": "rejection", "category": category,
              "confidence": conf, "reason": reason})

    elif signal_type == "skill_usage":
        prefs["skill_usage"][category] = prefs["skill_usage"].get(category, 0) + 1
        _log({"action": "skill_usage", "skill": category,
              "total": prefs["skill_usage"][category]})

    elif signal_type == "telegram_activity":
        hour = str(_now().hour)
        prefs["telegram_hours"][hour] = prefs["telegram_hours"].get(hour, 0) + 1

    _save_prefs(prefs)


def get_preference(category: str) -> float:
    """Get confidence level for a category. 0-1, higher = more trusted."""
    prefs = _load_prefs()
    if category in prefs.get("blacklist", []):
        return 0.0
    return prefs.get("confidence", {}).get(category, DEFAULT_CONFIDENCE)


def is_blacklisted(category: str) -> bool:
    """Check if a category has been blacklisted (rejected 2+ times)."""
    prefs = _load_prefs()
    return category in prefs.get("blacklist", [])


def get_top_skills(n: int = 10) -> list:
    """Get top N most-used skills."""
    prefs = _load_prefs()
    usage = prefs.get("skill_usage", {})
    return sorted(usage.items(), key=lambda x: -x[1])[:n]


def get_active_hours() -> list:
    """Get hours when Sebas is most active on Telegram."""
    prefs = _load_prefs()
    hours = prefs.get("telegram_hours", {})
    return sorted(hours.items(), key=lambda x: -x[1])[:8]


def get_blacklist() -> list:
    """Get all blacklisted categories."""
    prefs = _load_prefs()
    return prefs.get("blacklist", [])


def remove_from_blacklist(category: str) -> bool:
    """Manually remove a category from blacklist."""
    prefs = _load_prefs()
    if category in prefs.get("blacklist", []):
        prefs["blacklist"].remove(category)
        _save_prefs(prefs)
        _log({"action": "unblacklist", "category": category})
        return True
    return False


# ── CLI ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Preference Learning")
    p.add_argument("--show", action="store_true", help="Show preferences")
    p.add_argument("--top-skills", action="store_true", help="Top skills by usage")
    p.add_argument("--blacklist", action="store_true", help="Show blacklisted types")
    p.add_argument("--hours", action="store_true", help="Active Telegram hours")
    args = p.parse_args()

    if args.show:
        prefs = _load_prefs()
        print(json.dumps(prefs, indent=2, default=str))
    elif args.top_skills:
        for skill, count in get_top_skills():
            print(f"  {skill}: {count}")
    elif args.blacklist:
        bl = get_blacklist()
        print(f"Blacklisted ({len(bl)}):", ", ".join(bl) if bl else "none")
    elif args.hours:
        for hour, count in get_active_hours():
            print(f"  {hour}:00 — {count} messages")
    else:
        p.print_help()
