#!/usr/bin/env python3
"""
ROI Tracker — measures the real value Tenazas provides.

Scans existing logs (telegram_router.log) to compute:
- Skill executions vs LLM chats (automation rate)
- Estimated hours saved per skill
- Errors avoided (container restarts, VPN fixes, etc.)
- Usage breakdown by skill and by day

Persists snapshot to logs/roi_state.json for Prometheus + Grafana.

Usage:
    python3 roi_tracker.py              # compute + save + print summary
    python3 roi_tracker.py --json       # raw JSON output
    python3 roi_tracker.py --days 30    # custom period
"""

import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
TELEGRAM_LOG = LOGS_DIR / "telegram_router.log"
METRICS_STATE = LOGS_DIR / "metrics_state.json"
ROI_STATE = LOGS_DIR / "roi_state.json"

# Minutes saved per skill execution (estimated)
MINUTES_SAVED = {
    "skill:email":            3.0,
    "skill:weather":          1.0,
    "skill:calendar-agenda":  2.0,
    "skill:calendar":         2.0,
    "skill:smart-home":       1.5,
    "skill:smoking-tracker":  0.5,
    "skill:infra-monitor":    5.0,
    "skill:network-scanner":  4.0,
    "skill:arr-monitor":      3.0,
    "skill:morning-greeting": 1.0,
    "skill:tts":              0.5,
    "skill:reminders":        1.0,
    "skill:self-evolve":      0.0,
    "skill:model-trainer":    0.0,
    "iol_broker":             5.0,
    "knowledge_query":        2.0,
    "complex_reasoning":      3.0,
    "automation_command":      2.0,
    "chat":                   0.0,
}


def _load_telegram_log(days=7):
    """Parse telegram_router.log JSONL, filter by date range."""
    if not TELEGRAM_LOG.exists():
        return []
    cutoff = (datetime.now() - timedelta(days=days)).isoformat()
    entries = []
    try:
        with open(TELEGRAM_LOG, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    if entry.get("ts", "") >= cutoff:
                        entries.append(entry)
                except (json.JSONDecodeError, TypeError):
                    continue
    except Exception:
        pass
    return entries


def compute_skill_metrics(days=7):
    """Count skill executions and LLM chats from telegram_router.log."""
    entries = _load_telegram_log(days)

    by_skill = {}
    by_day = {}
    skill_executions = 0
    llm_chats = 0

    for e in entries:
        action = e.get("action", "")
        category = e.get("category", "")

        # Count completed skill responses
        if action == "skill_response" and category:
            skill_executions += 1
            if category not in by_skill:
                by_skill[category] = {"count": 0, "success": 0, "fail": 0}
            by_skill[category]["count"] += 1
            if e.get("success", False):
                by_skill[category]["success"] += 1
            else:
                by_skill[category]["fail"] += 1

            # Daily breakdown
            day = e.get("ts", "")[:10]
            if day:
                if day not in by_day:
                    by_day[day] = {"skills": 0, "chats": 0}
                by_day[day]["skills"] += 1

        # Count LLM chat responses
        elif action == "llm_response" and category:
            llm_chats += 1
            if category not in by_skill:
                by_skill[category] = {"count": 0, "success": 0, "fail": 0}
            by_skill[category]["count"] += 1
            by_skill[category]["success"] += 1

            day = e.get("ts", "")[:10]
            if day:
                if day not in by_day:
                    by_day[day] = {"skills": 0, "chats": 0}
                by_day[day]["chats"] += 1

    # Compute success rates
    for info in by_skill.values():
        total = info["count"]
        info["success_rate"] = round(info["success"] / total, 2) if total > 0 else 0.0

    total_interactions = skill_executions + llm_chats
    automation_rate = round(skill_executions / total_interactions, 2) if total_interactions > 0 else 0.0

    # Top skills sorted by count
    top_skills = sorted(by_skill.items(), key=lambda x: x[1]["count"], reverse=True)

    return {
        "by_skill": by_skill,
        "by_day": dict(sorted(by_day.items())),
        "totals": {
            "skill_executions": skill_executions,
            "llm_chats": llm_chats,
            "total_interactions": total_interactions,
            "automation_rate": automation_rate,
        },
        "top_skills": [[k, v["count"]] for k, v in top_skills[:10]],
    }


def compute_hours_saved(skill_metrics):
    """Estimate hours saved based on skill execution counts."""
    by_skill = skill_metrics.get("by_skill", {})
    hours_detail = {}
    total_minutes = 0.0

    for skill, info in by_skill.items():
        mins_per = MINUTES_SAVED.get(skill, 0.0)
        saved = info["count"] * mins_per
        if saved > 0:
            hours_detail[skill] = round(saved / 60, 1)
        total_minutes += saved

    return {
        "by_skill": hours_detail,
        "total_hours": round(total_minutes / 60, 1),
        "total_minutes": round(total_minutes),
    }


def compute_errors_avoided():
    """Count errors avoided from metrics_state.json (cumulative)."""
    result = {
        "container_restarts": 0,
        "vpn_fixes": 0,
        "ollama_recoveries": 0,
        "total": 0,
    }
    if not METRICS_STATE.exists():
        return result
    try:
        data = json.loads(METRICS_STATE.read_text())
        result["container_restarts"] = data.get("restarts", 0)
        result["vpn_fixes"] = data.get("vpn_disconnects", 0)
        result["ollama_recoveries"] = data.get("ollama_failures", 0)
        result["total"] = sum([
            result["container_restarts"],
            result["vpn_fixes"],
            result["ollama_recoveries"],
        ])
    except Exception:
        pass
    return result


def generate_roi_snapshot(days=7):
    """Generate complete ROI snapshot and persist to roi_state.json."""
    skill_metrics = compute_skill_metrics(days)
    hours_saved = compute_hours_saved(skill_metrics)
    errors_avoided = compute_errors_avoided()

    # Load previous snapshot for delta calculation
    previous_totals = {}
    if ROI_STATE.exists():
        try:
            prev = json.loads(ROI_STATE.read_text())
            previous_totals = prev.get("errors_avoided_detail", {})
        except Exception:
            pass

    snapshot = {
        "generated_at": datetime.now().isoformat(),
        "period_days": days,
        "skills": skill_metrics["by_skill"],
        "totals": {
            **skill_metrics["totals"],
            "hours_saved": hours_saved["total_hours"],
            "minutes_saved": hours_saved["total_minutes"],
            "errors_avoided": errors_avoided["total"],
        },
        "top_skills": skill_metrics["top_skills"],
        "hours_saved_detail": hours_saved["by_skill"],
        "errors_avoided_detail": errors_avoided,
        "by_day": skill_metrics["by_day"],
        "previous_totals": previous_totals,
    }

    # Persist
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    ROI_STATE.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False))
    return snapshot


def print_summary(snapshot):
    """Print human-readable summary."""
    t = snapshot["totals"]
    print(f"ROI Snapshot ({snapshot['period_days']}d)")
    print(f"  Skills: {t['skill_executions']}  |  LLM chats: {t['llm_chats']}  |  Automation: {int(t['automation_rate']*100)}%")
    print(f"  Hours saved: ~{t['hours_saved']}h ({t['minutes_saved']} min)")
    print(f"  Errors avoided: {t['errors_avoided']}")
    print()
    print("Top skills:")
    for name, count in snapshot["top_skills"][:5]:
        info = snapshot["skills"].get(name, {})
        sr = int(info.get("success_rate", 0) * 100)
        print(f"  {name}: {count} ({sr}% ok)")


if __name__ == "__main__":
    days = 7
    as_json = False
    for i, arg in enumerate(sys.argv[1:], 1):
        if arg == "--json":
            as_json = True
        elif arg == "--days" and i < len(sys.argv) - 1:
            days = int(sys.argv[i + 1])

    snapshot = generate_roi_snapshot(days)
    if as_json:
        print(json.dumps(snapshot, indent=2, ensure_ascii=False))
    else:
        print_summary(snapshot)
