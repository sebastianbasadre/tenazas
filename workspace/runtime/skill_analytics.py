#!/usr/bin/env python3
"""
Skill Analytics — Analyze skill usage patterns and suggest optimizations.

Tracks: most used skills, slow skills, failing skills, redundant skills.
Sources: metrics_engine data, decision traces, cron job history.
"""

import json
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
METRICS_DIR = BASE_DIR / "metrics"
TELEMETRY_LOG = BASE_DIR / "logs" / "skill_analytics.log"
ANALYTICS_FILE = BASE_DIR / "skill_analytics.json"

SKILLS_DIR = "/home/tenazas/.openclaw/workspace/skills"


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def list_skills() -> list:
    """List available skills from the skills directory."""
    skills_path = Path(SKILLS_DIR)
    if not skills_path.exists():
        return []
    return sorted([d.name for d in skills_path.iterdir()
                   if d.is_dir() and (d / "SKILL.md").exists()])


def _load_metrics(days: int = 7) -> list:
    entries = []
    for i in range(days):
        day = _now() - timedelta(days=i)
        path = METRICS_DIR / f"{day.strftime('%Y-%m-%d')}.json"
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, list):
                entries.extend(data)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return entries


def analyze_usage(days: int = 7) -> dict:
    """Analyze skill usage from metrics and pipeline data."""
    entries = _load_metrics(days)

    # Count pipeline usage as proxy for skill usage
    pipeline_counts = Counter()
    action_targets = Counter()

    for e in entries:
        if e.get("type") == "query":
            pipeline_counts[e.get("pipeline", "unknown")] += 1
        if e.get("type") == "diagnostic":
            action_targets["infra-monitor"] += 1

    # Map pipelines to skills
    skill_usage = {
        "rag": pipeline_counts.get("rag", 0),
        "infra-monitor": pipeline_counts.get("diagnostics", 0) + action_targets.get("infra-monitor", 0),
        "container-restart": action_targets.get("container-restart", 0),
    }

    return skill_usage


def generate_report(days: int = 7) -> dict:
    """Generate skill analytics report."""
    available = list_skills()
    usage = analyze_usage(days)

    used = {k for k, v in usage.items() if v > 0}
    unused = [s for s in available if s not in used]

    report = {
        "period_days": days,
        "total_skills": len(available),
        "skills_used": len(used),
        "skills_unused": len(unused),
        "usage": usage,
        "unused_skills": unused[:10],
        "suggestions": [],
        "generated_at": _now().isoformat(),
    }

    if unused:
        report["suggestions"].append({
            "type": "unused_skills",
            "message": f"{len(unused)} skills sin uso en {days} dias: {', '.join(unused[:5])}",
        })

    # Save
    ANALYTICS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(ANALYTICS_FILE, "w") as f:
        json.dump(report, f, indent=2, default=str)

    _log({"type": "skill_analysis", "total": len(available), "used": len(used)})
    return report
