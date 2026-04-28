#!/usr/bin/env python3
"""Agent Self-Improve — Specialized in system self-improvement.

Ownership: error mining, candidates, experiments, benchmarks.
NEVER promotes to runtime direct — always staging.
"""

import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "self_improve_memory.json"
LOG_FILE = BASE_DIR / "logs" / "agent_self_improve.log"

OWNERSHIP = {"improve", "benchmark", "error_mining", "experiment", "staging",
             "candidate", "self-evolve", "calibration", "learning"}


def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass
def _load(p, d=None):
    try:
        with open(p) as f: return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError): return d if d is not None else {}
def _save(p, data):
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w") as f: json.dump(data, f, indent=2, default=str)


def owns(topic: str) -> bool:
    return any(k in topic.lower() for k in OWNERSHIP)


def assess() -> dict:
    status = {"agent": "self_improve", "ts": now().isoformat(), "pending": 0, "experiments": 0}
    try:
        from improvement_candidates import list_candidates
        cands = list_candidates(status="proposed")
        status["pending"] = len(cands)
    except Exception: pass
    try:
        from experiment_runner import list_active
        status["experiments"] = len(list_active())
    except Exception: pass
    _log({"action": "assess", "pending": status["pending"], "experiments": status["experiments"]})
    return status


def handle_query(query: str) -> str:
    status = assess()
    parts = ["Self-Improve:"]
    parts.append(f"  Candidates pendientes: {status['pending']}")
    parts.append(f"  Experimentos activos: {status['experiments']}")
    return "\n".join(parts)


def run_daily():
    """Daily self-improvement cycle. Returns results dict."""
    results = {"mined": 0, "candidates": 0, "ts": now().isoformat()}
    try:
        from error_mining import mine_all
        report = mine_all()
        results["mined"] = report.get("total_errors", 0)
    except Exception: pass
    try:
        from improvement_candidates import generate
        cands = generate()
        results["candidates"] = len(cands)
    except Exception: pass
    save_memory_event(results)
    _log({"action": "daily_run", **results})
    return results


def get_memory() -> dict: return _load(MEMORY_FILE, {"events": []})
def save_memory_event(event: dict):
    mem = get_memory()
    mem["events"].append({**event, "ts": now().isoformat()})
    mem["events"] = mem["events"][-100:]
    _save(MEMORY_FILE, mem)
