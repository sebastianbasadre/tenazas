#!/usr/bin/env python3
"""
experiment_sandbox.py
A/B testing for prompts and configs — staging only, never auto-deploys.

Policy: experiments run in isolation, results go to staging.
Sebas approves before any change goes to production.

Usage:
  python3 experiment_sandbox.py --propose "nuevo system prompt para weather"
  python3 experiment_sandbox.py --status
  python3 experiment_sandbox.py --approve <id>
"""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

EXPERIMENTS_PATH = Path(__file__).parent / "learning" / "experiments.json"
LOG_PATH = Path(__file__).parent / "logs" / "experiments.log"


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load() -> list:
    try:
        return json.loads(EXPERIMENTS_PATH.read_text()) if EXPERIMENTS_PATH.exists() else []
    except Exception:
        return []


def _save(experiments: list):
    EXPERIMENTS_PATH.parent.mkdir(parents=True, exist_ok=True)
    EXPERIMENTS_PATH.write_text(json.dumps(experiments, indent=2, ensure_ascii=False, default=str))


def propose(description: str, category: str = "prompt",
            current: str = "", proposed: str = "") -> str:
    """Create a new experiment proposal. Returns experiment ID.

    NEVER auto-applies. Goes to staging for Sebas to review.
    """
    experiments = _load()
    exp_id = hashlib.sha256(f"{description}{datetime.now()}".encode()).hexdigest()[:8]

    experiment = {
        "id": exp_id,
        "description": description,
        "category": category,  # prompt, config, pattern, model
        "current": current[:500],
        "proposed": proposed[:500],
        "status": "proposed",  # proposed → approved → applied | rejected
        "created_at": datetime.now().astimezone().isoformat(),
        "results": None,
    }

    experiments.append(experiment)
    _save(experiments[-50:])
    _log({"action": "propose", "id": exp_id, "description": description[:80]})
    return exp_id


def approve(exp_id: str) -> bool:
    """Approve an experiment — marks as ready to apply."""
    experiments = _load()
    for e in experiments:
        if e["id"] == exp_id and e["status"] == "proposed":
            e["status"] = "approved"
            e["approved_at"] = datetime.now().astimezone().isoformat()
            _save(experiments)
            _log({"action": "approve", "id": exp_id})
            return True
    return False


def reject(exp_id: str) -> bool:
    """Reject an experiment."""
    experiments = _load()
    for e in experiments:
        if e["id"] == exp_id and e["status"] in ("proposed", "approved"):
            e["status"] = "rejected"
            e["rejected_at"] = datetime.now().astimezone().isoformat()
            _save(experiments)
            _log({"action": "reject", "id": exp_id})
            return True
    return False


def get_pending() -> list[dict]:
    """Get experiments awaiting approval."""
    return [e for e in _load() if e["status"] == "proposed"]


def get_approved() -> list[dict]:
    """Get approved experiments ready to apply."""
    return [e for e in _load() if e["status"] == "approved"]


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--propose", help="Describe experiment")
    p.add_argument("--status", action="store_true")
    p.add_argument("--approve", help="Approve experiment by ID")
    p.add_argument("--reject", help="Reject experiment by ID")
    args = p.parse_args()

    if args.propose:
        eid = propose(args.propose)
        print(f"Experiment proposed: {eid}")
    elif args.approve:
        ok = approve(args.approve)
        print(f"Approved: {ok}")
    elif args.reject:
        ok = reject(args.reject)
        print(f"Rejected: {ok}")
    elif args.status:
        for e in _load():
            print(f"  [{e['status']:10s}] {e['id']} — {e['description'][:60]}")
    else:
        p.print_help()
