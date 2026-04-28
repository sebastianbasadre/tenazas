#!/usr/bin/env python3
"""
Personal Optimizer — Learn Sebas's patterns for better experience.

Patterns detected:
- active_hours: when Sebas responds to Telegram
- decision_patterns: what he approves fast vs ignores
- skill_usage_rhythm: which skills at which hours
- interruption_tolerance: how many notifs/hour before ignoring

All actions are informative, never executive.
Self-feeding — no explicit input needed.
Minimum 7 days of data before making suggestions.

Usage:
    python3 personal_optimizer.py --report     # pattern report
    python3 personal_optimizer.py --ingest     # ingest new data
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
try:
    from .runtime_utils import load_json, save_json
except ImportError:
    from runtime_utils import load_json, save_json

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
PATTERNS_FILE = STATE_DIR / "personal_patterns.json"
LOG_FILE = BASE_DIR / "logs" / "personal_optimizer.log"
PREFS_FILE = STATE_DIR / "preferences.json"
TELEGRAM_LOG = BASE_DIR / "logs" / "telegram_handler.log"

MIN_DAYS_FOR_SUGGESTIONS = 7
MIN_SAMPLES = 20


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


def _load_telegram_activity(days: int = 14) -> list:
    """Load Telegram interaction timestamps from logs."""
    entries = []
    cutoff = _now() - timedelta(days=days)
    try:
        with open(TELEGRAM_LOG) as f:
            for line in f:
                try:
                    entry = json.loads(line.strip())
                    ts = entry.get("ts", "")
                    if ts and datetime.fromisoformat(ts) >= cutoff:
                        if entry.get("action") in ("intercepted", "delegating_to_llm"):
                            entries.append(entry)
                except (json.JSONDecodeError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return entries


# ── Pattern detectors ───────────────────────────────────────────


def detect_active_hours(entries: list = None) -> dict:
    """Detect hours when Sebas is most active on Telegram."""
    if entries is None:
        entries = _load_telegram_activity()

    hour_counts = Counter()
    days_seen = set()

    for e in entries:
        try:
            dt = datetime.fromisoformat(e["ts"])
            hour_counts[dt.hour] += 1
            days_seen.add(dt.date())
        except (ValueError, KeyError):
            continue

    total_days = len(days_seen)
    if total_days == 0:
        return {"hours": {}, "peak_hours": [], "total_days": 0, "sufficient": False}

    # Normalize by days
    avg_per_hour = {h: round(c / total_days, 2) for h, c in hour_counts.items()}

    # Peak hours: above average
    overall_avg = sum(avg_per_hour.values()) / max(len(avg_per_hour), 1)
    peak_hours = sorted([h for h, avg in avg_per_hour.items() if avg > overall_avg])

    return {
        "hours": dict(sorted(avg_per_hour.items())),
        "peak_hours": peak_hours,
        "total_days": total_days,
        "sufficient": total_days >= MIN_DAYS_FOR_SUGGESTIONS,
    }


def detect_decision_patterns(entries: list = None) -> dict:
    """Detect what types of proposals Sebas approves fast vs ignores."""
    prefs = load_json(PREFS_FILE, {})
    signals = prefs.get("signals", [])

    by_category = defaultdict(lambda: {"approved": 0, "rejected": 0, "total": 0})
    for s in signals:
        cat = s.get("category", "unknown")
        by_category[cat]["total"] += 1
        if s.get("type") == "approval":
            by_category[cat]["approved"] += 1
        elif s.get("type") == "rejection":
            by_category[cat]["rejected"] += 1

    fast_approve = []
    often_ignored = []
    for cat, counts in by_category.items():
        if counts["total"] < 3:
            continue
        rate = counts["approved"] / counts["total"]
        if rate > 0.8:
            fast_approve.append(cat)
        elif rate < 0.2:
            often_ignored.append(cat)

    return {
        "by_category": dict(by_category),
        "fast_approve": fast_approve,
        "often_ignored": often_ignored,
    }


def detect_skill_rhythm(entries: list = None) -> dict:
    """Detect which skills are used at which hours."""
    if entries is None:
        entries = _load_telegram_activity()

    rhythm = defaultdict(Counter)
    for e in entries:
        try:
            dt = datetime.fromisoformat(e["ts"])
            cat = e.get("category", "")
            if cat.startswith("skill:"):
                period = "morning" if dt.hour < 12 else "afternoon" if dt.hour < 18 else "evening"
                rhythm[period][cat] += 1
        except (ValueError, KeyError):
            continue

    return {period: dict(counter.most_common(3))
            for period, counter in rhythm.items()}


def detect_interruption_tolerance(entries: list = None) -> dict:
    """Detect how many notifications per hour before Sebas stops responding."""
    if entries is None:
        entries = _load_telegram_activity()

    # Group by hour windows
    by_hour = defaultdict(list)
    for e in entries:
        try:
            dt = datetime.fromisoformat(e["ts"])
            key = dt.strftime("%Y-%m-%d-%H")
            by_hour[key].append(dt)
        except (ValueError, KeyError):
            continue

    if not by_hour:
        return {"avg_per_hour": 0, "max_per_hour": 0, "tolerance_estimate": 5}

    counts = [len(v) for v in by_hour.values()]
    avg = sum(counts) / len(counts)
    max_count = max(counts)

    # Tolerance = average + 1 std (rough estimate)
    variance = sum((c - avg) ** 2 for c in counts) / len(counts)
    tolerance = round(avg + variance ** 0.5, 1)

    return {
        "avg_per_hour": round(avg, 1),
        "max_per_hour": max_count,
        "tolerance_estimate": max(tolerance, 2),  # minimum 2
    }


# ── Main API ────────────────────────────────────────────────────


def analyze() -> dict:
    """Run full pattern analysis."""
    entries = _load_telegram_activity()

    report = {
        "analyzed_at": _now().isoformat(),
        "active_hours": detect_active_hours(entries),
        "decision_patterns": detect_decision_patterns(),
        "skill_rhythm": detect_skill_rhythm(entries),
        "interruption_tolerance": detect_interruption_tolerance(entries),
        "suggestions": [],
    }

    # Generate suggestions only with sufficient data
    if report["active_hours"]["sufficient"]:
        peak = report["active_hours"]["peak_hours"]
        if peak:
            report["suggestions"].append(
                f"Ventana activa: {min(peak)}:00–{max(peak)+1}:00")

        tol = report["interruption_tolerance"]
        if tol["avg_per_hour"] > 0:
            report["suggestions"].append(
                f"Tolerancia estimada: {tol['tolerance_estimate']:.0f} notif/hora")

        rhythm = report["skill_rhythm"]
        for period, skills in rhythm.items():
            if skills:
                top = list(skills.keys())[0]
                report["suggestions"].append(
                    f"Skill frecuente {period}: {top}")

    save_json(PATTERNS_FILE, report)
    _log({"action": "analyze_complete",
          "days": report["active_hours"]["total_days"],
          "suggestions": len(report["suggestions"])})

    return report


def has_sufficient_data() -> bool:
    """Check if there's enough data for suggestions."""
    entries = _load_telegram_activity()
    days_seen = set()
    for e in entries:
        try:
            days_seen.add(datetime.fromisoformat(e["ts"]).date())
        except (ValueError, KeyError):
            continue
    return len(days_seen) >= MIN_DAYS_FOR_SUGGESTIONS


def get_report() -> dict:
    """Load last analysis report."""
    return load_json(PATTERNS_FILE, {})


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Personal Optimizer")
    p.add_argument("--report", action="store_true", help="Show pattern report")
    p.add_argument("--ingest", action="store_true", help="Ingest and analyze")
    args = p.parse_args()

    if args.report or args.ingest:
        report = analyze()
        print(json.dumps(report, indent=2, default=str))
    else:
        p.print_help()
