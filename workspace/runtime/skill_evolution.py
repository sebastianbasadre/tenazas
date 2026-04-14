#!/usr/bin/env python3
"""
Skill Evolution — Progressive skill adoption: proposal → shadow → 10% → full.

Phase 1: Proposed (skill_gap_detector) → staging/skills/
Phase 2: Shadow — receives queries but doesn't respond to user
Phase 3: 10% traffic — real responses to 10% of queries
Phase 4: Full adoption (requires approval)

Every phase transition requires Sebas's approval.

Usage:
    python3 skill_evolution.py --status         # show skills per phase
    python3 skill_evolution.py --approve ID     # advance to next phase
    python3 skill_evolution.py --archive ID     # archive failed skill
    python3 skill_evolution.py --evaluate ID    # show evaluation report
"""

import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
EVOLUTION_FILE = STATE_DIR / "skill_evolution.json"
LOG_FILE = BASE_DIR / "logs" / "skill_evolution.log"

PHASES = ["proposed", "shadow", "canary_10pct", "active", "archived"]
SHADOW_EVAL_THRESHOLD = 50     # queries before evaluation
CANARY_EVAL_THRESHOLD = 100    # queries before full promotion
ACCURACY_THRESHOLD = 0.80      # 80% accuracy to advance


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


def _load_skills() -> list:
    try:
        with open(EVOLUTION_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_skills(skills: list):
    EVOLUTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EVOLUTION_FILE, "w") as f:
        json.dump(skills, f, indent=2, default=str)


def _get_skill(skill_id: str) -> dict | None:
    for s in _load_skills():
        if s.get("id") == skill_id:
            return s
    return None


# ── Core API ────────────────────────────────────────────────────


def register(skill_id: str, name: str, description: str, source: str = "skill_gap_detector") -> dict:
    """Register a new skill in phase 'proposed'."""
    skills = _load_skills()
    if any(s["id"] == skill_id for s in skills):
        return {"error": "skill already registered"}

    skill = {
        "id": skill_id,
        "name": name,
        "description": description,
        "phase": "proposed",
        "source": source,
        "registered_at": _now().isoformat(),
        "shadow_queries": 0,
        "shadow_correct": 0,
        "canary_queries": 0,
        "canary_correct": 0,
        "phase_history": [{"phase": "proposed", "ts": _now().isoformat()}],
    }
    skills.append(skill)
    _save_skills(skills)
    _log({"action": "register", "id": skill_id, "name": name})
    return skill


def advance_phase(skill_id: str) -> dict:
    """Advance skill to next phase. Requires sufficient evaluation.

    Returns: {success, skill, reason}
    """
    skills = _load_skills()
    skill = None
    for s in skills:
        if s["id"] == skill_id:
            skill = s
            break

    if not skill:
        return {"success": False, "reason": "skill not found"}

    current = skill["phase"]
    current_idx = PHASES.index(current) if current in PHASES else -1

    if current_idx < 0 or current_idx >= len(PHASES) - 2:  # can't advance past active
        return {"success": False, "reason": f"cannot advance from {current}"}

    # Check evaluation requirements
    if current == "shadow":
        if skill["shadow_queries"] < SHADOW_EVAL_THRESHOLD:
            return {"success": False,
                    "reason": f"need {SHADOW_EVAL_THRESHOLD} shadow queries, "
                              f"have {skill['shadow_queries']}"}
        accuracy = skill["shadow_correct"] / max(skill["shadow_queries"], 1)
        if accuracy < ACCURACY_THRESHOLD:
            return {"success": False,
                    "reason": f"shadow accuracy {accuracy:.0%} < {ACCURACY_THRESHOLD:.0%}"}

    elif current == "canary_10pct":
        if skill["canary_queries"] < CANARY_EVAL_THRESHOLD:
            return {"success": False,
                    "reason": f"need {CANARY_EVAL_THRESHOLD} canary queries, "
                              f"have {skill['canary_queries']}"}
        accuracy = skill["canary_correct"] / max(skill["canary_queries"], 1)
        if accuracy < ACCURACY_THRESHOLD:
            return {"success": False,
                    "reason": f"canary accuracy {accuracy:.0%} < {ACCURACY_THRESHOLD:.0%}"}

    # Advance
    next_phase = PHASES[current_idx + 1]
    skill["phase"] = next_phase
    skill["phase_history"].append({"phase": next_phase, "ts": _now().isoformat()})
    _save_skills(skills)
    _log({"action": "advance", "id": skill_id, "from": current, "to": next_phase})

    return {"success": True, "skill": skill, "reason": f"advanced to {next_phase}"}


def record_shadow_query(skill_id: str, correct: bool):
    """Record a shadow evaluation query result."""
    skills = _load_skills()
    for s in skills:
        if s["id"] == skill_id and s["phase"] == "shadow":
            s["shadow_queries"] += 1
            if correct:
                s["shadow_correct"] += 1
            _save_skills(skills)
            return True
    return False


def record_canary_query(skill_id: str, correct: bool):
    """Record a canary (10% traffic) query result."""
    skills = _load_skills()
    for s in skills:
        if s["id"] == skill_id and s["phase"] == "canary_10pct":
            s["canary_queries"] += 1
            if correct:
                s["canary_correct"] += 1
            _save_skills(skills)
            return True
    return False


def archive(skill_id: str, reason: str = "manual") -> bool:
    """Archive a failed or unwanted skill."""
    skills = _load_skills()
    for s in skills:
        if s["id"] == skill_id:
            s["phase"] = "archived"
            s["archived_reason"] = reason
            s["phase_history"].append({"phase": "archived", "ts": _now().isoformat()})
            _save_skills(skills)
            _log({"action": "archive", "id": skill_id, "reason": reason})
            return True
    return False


def get_evaluation(skill_id: str) -> dict:
    """Get evaluation report for a skill."""
    skill = _get_skill(skill_id)
    if not skill:
        return {"error": "not found"}

    report = {
        "id": skill["id"],
        "name": skill["name"],
        "phase": skill["phase"],
    }

    if skill["shadow_queries"] > 0:
        report["shadow_accuracy"] = round(
            skill["shadow_correct"] / skill["shadow_queries"], 4)
        report["shadow_queries"] = skill["shadow_queries"]

    if skill["canary_queries"] > 0:
        report["canary_accuracy"] = round(
            skill["canary_correct"] / skill["canary_queries"], 4)
        report["canary_queries"] = skill["canary_queries"]

    report["ready_for_next"] = _check_ready(skill)
    return report


def _check_ready(skill: dict) -> bool:
    """Check if skill is ready for next phase."""
    phase = skill["phase"]
    if phase == "proposed":
        return True  # always ready to start shadow
    if phase == "shadow":
        if skill["shadow_queries"] < SHADOW_EVAL_THRESHOLD:
            return False
        return (skill["shadow_correct"] / max(skill["shadow_queries"], 1)) >= ACCURACY_THRESHOLD
    if phase == "canary_10pct":
        if skill["canary_queries"] < CANARY_EVAL_THRESHOLD:
            return False
        return (skill["canary_correct"] / max(skill["canary_queries"], 1)) >= ACCURACY_THRESHOLD
    return False


def is_shadow(skill_id: str) -> bool:
    """Check if skill is in shadow phase (receives queries but doesn't respond)."""
    skill = _get_skill(skill_id)
    return skill is not None and skill.get("phase") == "shadow"


def should_canary(skill_id: str) -> bool:
    """Check if skill is in canary phase (10% real traffic)."""
    skill = _get_skill(skill_id)
    return skill is not None and skill.get("phase") == "canary_10pct"


def list_by_phase(phase: str = None) -> list:
    """List skills, optionally filtered by phase."""
    skills = _load_skills()
    if phase:
        return [s for s in skills if s.get("phase") == phase]
    return skills


def get_status() -> dict:
    """Summary of skills per phase."""
    skills = _load_skills()
    by_phase = {}
    for s in skills:
        p = s.get("phase", "unknown")
        by_phase[p] = by_phase.get(p, 0) + 1
    return {"total": len(skills), "by_phase": by_phase}


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Skill Evolution")
    p.add_argument("--status", action="store_true", help="Show status")
    p.add_argument("--approve", type=str, metavar="ID", help="Advance phase")
    p.add_argument("--archive", type=str, metavar="ID", help="Archive skill")
    p.add_argument("--evaluate", type=str, metavar="ID", help="Show evaluation")
    args = p.parse_args()

    if args.status:
        print(json.dumps(get_status(), indent=2))
        for s in list_by_phase():
            print(f"  [{s['phase']}] {s['id']} — {s['name']}")
    elif args.approve:
        result = advance_phase(args.approve)
        print(json.dumps(result, indent=2, default=str))
    elif args.archive:
        print("Archived" if archive(args.archive) else "Not found")
    elif args.evaluate:
        print(json.dumps(get_evaluation(args.evaluate), indent=2))
    else:
        p.print_help()
