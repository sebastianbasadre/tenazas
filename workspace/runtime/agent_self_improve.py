#!/usr/bin/env python3
"""Agent Self-Improve — Specialized in system self-improvement.

Ownership: error mining, candidates, experiments, benchmarks.
NEVER promotes to runtime direct — always staging.
"""

import json
from pathlib import Path
from runtime_utils import now, load_json, save_json
from agent_protocol import Observation, Recommendation

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "self_improve_memory.json"
LOG_FILE = BASE_DIR / "logs" / "agent_self_improve.log"

OWNERSHIP = {"improve", "benchmark", "error_mining", "experiment", "staging",
             "candidate", "self-evolve", "calibration", "learning"}
PREFERRED_ROLE = "reason"


def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass

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


def _observe_errors():
    """Focused observation of error mining results."""
    try:
        from error_mining import mine_all
        report = mine_all()
        total = report.get("total_errors", 0)
        categories = report.get("categories", {})
        issues = []
        if total > 0:
            top_cats = sorted(categories.items(), key=lambda x: x[1], reverse=True)[:3]
            for cat, count in top_cats:
                issues.append(f"{cat}: {count} errors")
        summary = f"Errors: {total} total"
        if issues:
            summary += f" ({', '.join(issues[:2])})"
        confidence = 0.7 if total > 0 else 0.8
        return {"summary": summary, "confidence": confidence, "sub": "errors", "issues": issues, "raw": report}
    except Exception as e:
        return {"summary": f"Errors: error ({str(e)[:80]})", "confidence": 0.3, "sub": "errors", "issues": [str(e)[:100]], "raw": {}}


def _observe_proposals():
    """Focused observation of improvement proposals."""
    try:
        from improvement_candidates import list_candidates
        cands = list_candidates(status="proposed")
        pending = len(cands)
        summary = f"Proposals: {pending} pending"
        return {"summary": summary, "confidence": 0.8, "sub": "proposals", "issues": [], "raw": {"pending": pending, "candidates": cands[:5]}}
    except Exception as e:
        return {"summary": f"Proposals: error ({str(e)[:80]})", "confidence": 0.3, "sub": "proposals", "issues": [str(e)[:100]], "raw": {}}


def _observe_experiments():
    """Focused observation of active experiments."""
    try:
        from experiment_runner import list_active
        exps = list_active()
        active = len(exps)
        summary = f"Experiments: {active} active"
        return {"summary": summary, "confidence": 0.8, "sub": "experiments", "issues": [], "raw": {"active": active, "experiments": exps[:5]}}
    except Exception as e:
        return {"summary": f"Experiments: error ({str(e)[:80]})", "confidence": 0.3, "sub": "experiments", "issues": [str(e)[:100]], "raw": {}}


def _observe_benchmark():
    """Focused observation of benchmark status."""
    try:
        from self_benchmark import get_status
        status = get_status()
        score = status.get("score", 0)
        gate = status.get("gate", "unknown")
        summary = f"Benchmark: score={score}, gate={gate}"
        issues = []
        if gate == "fail":
            issues.append(f"benchmark gate failed (score={score})")
        return {"summary": summary, "confidence": 0.8, "sub": "benchmark", "issues": issues, "raw": status}
    except Exception as e:
        return {"summary": f"Benchmark: error ({str(e)[:80]})", "confidence": 0.3, "sub": "benchmark", "issues": [str(e)[:100]], "raw": {}}


def _assess_full():
    """Full assessment via existing assess(), reformatted as observer dict."""
    status = assess()
    pending = status.get("pending", 0)
    experiments = status.get("experiments", 0)
    summary = f"Self-improve: {pending} pendientes, {experiments} experimentos" if pending or experiments else "Self-improve: sin propuestas"
    return {
        "summary": summary,
        "confidence": 0.75,
        "sub": "full",
        "issues": [],
        "raw": status,
    }


def _build_improve_recs(data):
    """Build typed Recommendations from self-improve observer data."""
    recs = []
    sub = data.get("sub", "")
    raw = data.get("raw", {})
    # Check for errors to review
    if sub == "errors":
        total = raw.get("total_errors", 0)
        if total > 0:
            recs.append(Recommendation(
                action="review_errors",
                target="error_mining",
                risk_level="low",
                requires_approval=False,
                rationale=f"{total} errors found",
            ))
    # Check for pending proposals
    if sub == "proposals":
        pending = raw.get("pending", 0)
        if pending > 0:
            recs.append(Recommendation(
                action="review_proposals",
                target="improvement_candidates",
                risk_level="low",
                requires_approval=False,
                rationale=f"{pending} proposals pending",
            ))
    # Full assessment: check both
    if sub == "full":
        pending = raw.get("pending", 0)
        if pending > 0:
            recs.append(Recommendation(
                action="review_proposals",
                target="improvement_candidates",
                risk_level="low",
                requires_approval=False,
                rationale=f"{pending} proposals pending",
            ))
    return recs


def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    q = query.lower()
    if any(k in q for k in ("error", "falla", "failure", "mining")):
        data = _observe_errors()
    elif any(k in q for k in ("proposal", "propuesta", "candidate", "pendiente")):
        data = _observe_proposals()
    elif any(k in q for k in ("experiment", "a/b", "test")):
        data = _observe_experiments()
    elif any(k in q for k in ("benchmark", "gate", "score")):
        data = _observe_benchmark()
    else:
        data = _assess_full()
    obs = Observation(
        agent_name="self_improve",
        domain="self_improvement",
        summary=data.get("summary", "Self-improve: sin datos"),
        confidence=data.get("confidence", 0.5),
        details=data,
    )
    recs = _build_improve_recs(data)
    _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"), "confidence": obs.confidence})
    return (obs, recs)


def handle_query(query: str) -> str:
    """Backward-compat wrapper. Returns string summary."""
    obs, recs = handle(query)
    return obs.summary


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


def get_memory() -> dict: return load_json(str(MEMORY_FILE), {"events": []})
def save_memory_event(event: dict):
    mem = get_memory()
    mem["events"].append({**event, "ts": now().isoformat()})
    mem["events"] = mem["events"][-100:]
    save_json(str(MEMORY_FILE), mem)
