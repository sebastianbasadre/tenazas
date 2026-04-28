#!/usr/bin/env python3
"""
Skill Generator — Detects repeated workflows and proposes new skills.

Analyzes event patterns and task history from memory_log.json to identify
recurring manual workflows. Generates skill proposals as structured dicts.
NEVER auto-creates skills — only proposes. Requires approval.

Stores proposals in skill_proposals.json. Logs to logs/skill_generator.log.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from runtime_utils import load_json, save_json

BASE_DIR = Path(__file__).parent
MEMORY_FILE = BASE_DIR / "memory_log.json"
PROPOSALS_FILE = BASE_DIR / "skill_proposals.json"
LOG_FILE = BASE_DIR / "logs" / "skill_generator.log"

# ── Config ────────────────────────────────────────────────────────

LOOKBACK_DAYS = 30
MIN_RECURRENCE = 3        # Minimum times a workflow must repeat to propose
MAX_PROPOSALS = 20


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    """Append a JSON line to skill_generator.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _analyze_workflows() -> list:
    """
    Analyze memory_log.json to find recurring event+action combinations.

    Returns:
        List of (workflow_key, count, details) tuples
    """
    memory = load_json(MEMORY_FILE, [])
    if not isinstance(memory, list):
        return []

    cutoff = _now() - timedelta(days=LOOKBACK_DAYS)
    workflow_counts = defaultdict(lambda: {"count": 0, "events": [], "actions": []})

    for entry in memory:
        try:
            entry_dt = datetime.fromisoformat(entry["ts"])
        except (KeyError, ValueError, TypeError):
            continue
        if entry_dt < cutoff:
            continue

        events = entry.get("events", [])
        results = entry.get("results", [])

        if not events and not results:
            continue

        # Build a workflow signature from event types + action types
        event_types = tuple(sorted(set(e.get("type", "") for e in events)))
        action_types = tuple(sorted(set(r.get("action", "") for r in results if r.get("ok"))))

        if not event_types:
            continue

        key = (event_types, action_types)
        workflow_counts[key]["count"] += 1
        workflow_counts[key]["events"] = list(event_types)
        workflow_counts[key]["actions"] = list(action_types)

    return [(k, v["count"], v) for k, v in workflow_counts.items()
            if v["count"] >= MIN_RECURRENCE]


def _create_proposal(events: list, actions: list, count: int) -> dict:
    """
    Create a skill proposal from a recurring workflow.

    Returns:
        Proposal dict with: name, description, trigger, steps, requires_approval, recurrence
    """
    # Generate a descriptive name
    primary_event = events[0] if events else "unknown"
    name_parts = primary_event.replace("_", "-")
    if actions:
        name_parts += "-handler"
    else:
        name_parts += "-monitor"

    # Build trigger description
    trigger = f"When events [{', '.join(events)}] are detected"

    # Build steps from the actions that were taken
    steps = []
    for action in actions:
        steps.append({
            "action": action,
            "description": f"Execute {action}",
            "requires_approval": "restart" in action or "stop" in action,
        })

    if not steps:
        steps.append({
            "action": "notify",
            "description": f"Notify about {primary_event}",
            "requires_approval": False,
        })

    return {
        "name": name_parts,
        "description": f"Auto-proposed skill for handling: {', '.join(events)}",
        "trigger": trigger,
        "steps": steps,
        "requires_approval": True,  # ALWAYS requires approval
        "recurrence": count,
        "proposed_at": _now().isoformat(),
        "status": "pending",
    }


def generate_proposals() -> list:
    """
    Analyze workflows and generate skill proposals for recurring patterns.

    Returns:
        List of new proposal dicts
    """
    workflows = _analyze_workflows()
    existing = load_proposals()
    existing_names = {p.get("name") for p in existing}

    new_proposals = []
    for _key, count, details in workflows:
        proposal = _create_proposal(details["events"], details["actions"], count)

        # Skip if a similar proposal already exists
        if proposal["name"] in existing_names:
            continue

        new_proposals.append(proposal)

    # Add new proposals to the store
    if new_proposals:
        all_proposals = existing + new_proposals
        # Cap total proposals
        if len(all_proposals) > MAX_PROPOSALS:
            # Keep most recent
            all_proposals = all_proposals[-MAX_PROPOSALS:]
        _save_proposals(all_proposals)

    _log({
        "ts": _now().isoformat(),
        "action": "generate_proposals",
        "workflows_found": len(workflows),
        "new_proposals": len(new_proposals),
    })

    return new_proposals


def load_proposals() -> list:
    """Load all skill proposals from disk."""
    data = load_json(PROPOSALS_FILE, {"proposals": []})
    return data.get("proposals", [])


def _save_proposals(proposals: list):
    """Save proposals to disk."""
    save_json(PROPOSALS_FILE, {
        "proposals": proposals,
        "last_updated": _now().isoformat(),
    })


def get_pending_proposals() -> list:
    """Get proposals that are still pending approval."""
    return [p for p in load_proposals() if p.get("status") == "pending"]


def approve_proposal(name: str) -> bool:
    """
    Mark a proposal as approved. Does NOT create the skill — only marks it.

    Args:
        name: The proposal name to approve

    Returns:
        True if found and approved
    """
    proposals = load_proposals()
    for p in proposals:
        if p.get("name") == name and p.get("status") == "pending":
            p["status"] = "approved"
            p["approved_at"] = _now().isoformat()
            _save_proposals(proposals)
            _log({"ts": _now().isoformat(), "action": "approve_proposal", "name": name})
            return True
    return False


def reject_proposal(name: str) -> bool:
    """
    Mark a proposal as rejected.

    Args:
        name: The proposal name to reject

    Returns:
        True if found and rejected
    """
    proposals = load_proposals()
    for p in proposals:
        if p.get("name") == name and p.get("status") == "pending":
            p["status"] = "rejected"
            p["rejected_at"] = _now().isoformat()
            _save_proposals(proposals)
            _log({"ts": _now().isoformat(), "action": "reject_proposal", "name": name})
            return True
    return False
