#!/usr/bin/env python3
"""
Experiment Runner — Small measured experiments with strict guardrails.

Allowed experiments:
- A/B system prompts (10% traffic)
- Confidence thresholds comparison
- Skill chain ordering
- Briefing template comparison

Guardrails:
- Max 10% of traffic in experiment
- Auto-rollback if metric drops > 5%
- Never experiment on medium/high risk actions
- Auto-expire after 48h
- Results persisted in state/experiments/

Storage: state/experiments/
Log: logs/experiment_runner.log

Usage:
    python3 experiment_runner.py --create NAME --type prompt_ab --config '{...}'
    python3 experiment_runner.py --list
    python3 experiment_runner.py --check ID           # check metrics + auto-rollback
    python3 experiment_runner.py --results ID          # show results
    python3 experiment_runner.py --stop ID             # stop experiment
"""

import json
import random
import time
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
EXPERIMENTS_DIR = STATE_DIR / "experiments"
LOG_FILE = BASE_DIR / "logs" / "experiment_runner.log"

# Guardrails
MAX_TRAFFIC_PERCENT = 10
MAX_METRIC_DROP_PERCENT = 5
MAX_DURATION_HOURS = 48
BLOCKED_RISK_LEVELS = {"medium", "high"}

# Experiment types
VALID_TYPES = {
    "prompt_ab",              # A/B test system prompts
    "confidence_threshold",   # compare confidence thresholds
    "chain_order",            # compare skill chain ordering
    "briefing_template",      # compare daily briefing templates
}



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def _experiment_path(experiment_id: str) -> Path:
    return EXPERIMENTS_DIR / f"{experiment_id}.json"


def _load_experiment(experiment_id: str) -> dict:
    try:
        with open(_experiment_path(experiment_id)) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_experiment(experiment: dict):
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    with open(_experiment_path(experiment["id"]), "w") as f:
        json.dump(experiment, f, indent=2, default=str)


# ── Core API ────────────────────────────────────────────────────


def create(name: str, exp_type: str, config: dict,
           traffic_percent: int = 10) -> dict:
    """Create a new experiment.

    Args:
        name: human-readable name
        exp_type: one of VALID_TYPES
        config: experiment-specific config (variants, baseline, etc.)
        traffic_percent: % of traffic to route to experiment

    Returns: experiment dict or error dict
    """
    # Validate type
    if exp_type not in VALID_TYPES:
        return {"error": f"Invalid type '{exp_type}'. Valid: {VALID_TYPES}"}

    # Enforce traffic guardrail
    if traffic_percent > MAX_TRAFFIC_PERCENT:
        traffic_percent = MAX_TRAFFIC_PERCENT

    # Check no conflicting active experiment
    active = list_active()
    if len(active) >= 3:
        return {"error": "Max 3 concurrent experiments. Stop one first."}

    experiment_id = sha256(f"{name}:{now().isoformat()}".encode()).hexdigest()[:12]
    expires_at = (now() + timedelta(hours=MAX_DURATION_HOURS)).isoformat()

    experiment = {
        "id": experiment_id,
        "name": name,
        "type": exp_type,
        "config": config,
        "traffic_percent": traffic_percent,
        "status": "active",
        "created_at": now().isoformat(),
        "expires_at": expires_at,
        "baseline_metric": None,
        "results": {
            "control": {"count": 0, "metric_sum": 0},
            "variant": {"count": 0, "metric_sum": 0},
        },
    }

    _save_experiment(experiment)
    _log({"action": "create", "id": experiment_id, "name": name,
          "type": exp_type, "traffic": traffic_percent})
    return experiment


def should_use_variant(experiment_id: str) -> bool:
    """Decide if this request should use the experiment variant.

    Returns True for variant, False for control.
    Respects traffic_percent guardrail.
    """
    exp = _load_experiment(experiment_id)
    if not exp or exp.get("status") != "active":
        return False

    # Check expiry
    try:
        expires = datetime.fromisoformat(exp["expires_at"])
        if now() > expires:
            stop(experiment_id, reason="expired")
            return False
    except (ValueError, KeyError):
        pass

    traffic = exp.get("traffic_percent", MAX_TRAFFIC_PERCENT)
    return random.randint(1, 100) <= traffic


def record_result(experiment_id: str, is_variant: bool, metric_value: float):
    """Record a single result for an experiment.

    Args:
        experiment_id: experiment to record for
        is_variant: True if this was the variant, False for control
        metric_value: the metric being measured (e.g. confidence, latency)
    """
    exp = _load_experiment(experiment_id)
    if not exp or exp.get("status") != "active":
        return

    group = "variant" if is_variant else "control"
    exp["results"][group]["count"] += 1
    exp["results"][group]["metric_sum"] += metric_value

    # Set baseline from first control results
    if exp["baseline_metric"] is None and exp["results"]["control"]["count"] >= 5:
        exp["baseline_metric"] = (
            exp["results"]["control"]["metric_sum"] /
            exp["results"]["control"]["count"]
        )

    _save_experiment(exp)


def check_guardrails(experiment_id: str) -> dict:
    """Check if experiment should be rolled back.

    Returns: {"ok": bool, "reason": str, "metrics": dict}
    """
    exp = _load_experiment(experiment_id)
    if not exp:
        return {"ok": False, "reason": "experiment not found"}

    if exp.get("status") != "active":
        return {"ok": True, "reason": "experiment not active"}

    # Check expiry
    try:
        expires = datetime.fromisoformat(exp["expires_at"])
        if now() > expires:
            stop(experiment_id, reason="expired")
            return {"ok": False, "reason": "expired", "action": "stopped"}
    except (ValueError, KeyError):
        pass

    control = exp["results"]["control"]
    variant = exp["results"]["variant"]

    # Need minimum samples
    if control["count"] < 5 or variant["count"] < 3:
        return {"ok": True, "reason": "insufficient samples",
                "control_n": control["count"], "variant_n": variant["count"]}

    control_avg = control["metric_sum"] / control["count"]
    variant_avg = variant["metric_sum"] / variant["count"]

    # Check if variant dropped metric by > 5%
    if control_avg > 0:
        drop_percent = ((control_avg - variant_avg) / control_avg) * 100
    else:
        drop_percent = 0

    metrics = {
        "control_avg": round(control_avg, 4),
        "variant_avg": round(variant_avg, 4),
        "drop_percent": round(drop_percent, 2),
        "control_n": control["count"],
        "variant_n": variant["count"],
    }

    if drop_percent > MAX_METRIC_DROP_PERCENT:
        stop(experiment_id, reason=f"metric dropped {drop_percent:.1f}%")
        _log({"action": "auto_rollback", "id": experiment_id,
              "drop": drop_percent, "metrics": metrics})
        return {"ok": False, "reason": f"auto-rollback: metric dropped {drop_percent:.1f}%",
                "action": "stopped", "metrics": metrics}

    return {"ok": True, "reason": "within guardrails", "metrics": metrics}


def stop(experiment_id: str, reason: str = "manual") -> bool:
    """Stop an experiment."""
    exp = _load_experiment(experiment_id)
    if not exp:
        return False

    exp["status"] = "stopped"
    exp["stopped_at"] = now().isoformat()
    exp["stop_reason"] = reason
    _save_experiment(exp)
    _log({"action": "stop", "id": experiment_id, "reason": reason})
    return True


def get_results(experiment_id: str) -> dict:
    """Get experiment results with analysis."""
    exp = _load_experiment(experiment_id)
    if not exp:
        return {}

    control = exp["results"]["control"]
    variant = exp["results"]["variant"]

    analysis = {
        "experiment": exp["name"],
        "type": exp["type"],
        "status": exp["status"],
        "control_avg": round(control["metric_sum"] / max(control["count"], 1), 4),
        "variant_avg": round(variant["metric_sum"] / max(variant["count"], 1), 4),
        "control_n": control["count"],
        "variant_n": variant["count"],
    }

    if control["count"] > 0 and variant["count"] > 0:
        ctrl_avg = analysis["control_avg"]
        var_avg = analysis["variant_avg"]
        if ctrl_avg > 0:
            analysis["change_percent"] = round(
                ((var_avg - ctrl_avg) / ctrl_avg) * 100, 2)
        analysis["winner"] = "variant" if var_avg > ctrl_avg else "control"

    return analysis


def list_active() -> list:
    """List all active experiments."""
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    active = []
    for f in EXPERIMENTS_DIR.glob("*.json"):
        try:
            exp = json.loads(f.read_text())
            if exp.get("status") == "active":
                # Check expiry
                try:
                    expires = datetime.fromisoformat(exp["expires_at"])
                    if now() > expires:
                        stop(exp["id"], reason="expired")
                        continue
                except (ValueError, KeyError):
                    pass
                active.append(exp)
        except (json.JSONDecodeError, KeyError):
            continue
    return active


def list_all() -> list:
    """List all experiments."""
    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    experiments = []
    for f in EXPERIMENTS_DIR.glob("*.json"):
        try:
            experiments.append(json.loads(f.read_text()))
        except (json.JSONDecodeError, KeyError):
            continue
    return sorted(experiments, key=lambda x: x.get("created_at", ""), reverse=True)


def is_risk_allowed(risk_level: str) -> bool:
    """Check if an action's risk level allows experimentation."""
    return risk_level.lower() not in BLOCKED_RISK_LEVELS


# ── CLI ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Experiment Runner")
    p.add_argument("--create", type=str, metavar="NAME", help="Create experiment")
    p.add_argument("--type", type=str, help="Experiment type")
    p.add_argument("--config", type=str, default="{}", help="Config JSON")
    p.add_argument("--list", action="store_true", help="List active experiments")
    p.add_argument("--check", type=str, metavar="ID", help="Check guardrails")
    p.add_argument("--results", type=str, metavar="ID", help="Show results")
    p.add_argument("--stop", type=str, metavar="ID", help="Stop experiment")
    args = p.parse_args()

    if args.create:
        config = json.loads(args.config)
        exp = create(args.create, args.type or "prompt_ab", config)
        if "error" in exp:
            print(f"Error: {exp['error']}")
        else:
            print(f"Created: {exp['id']} ({exp['name']})")
    elif args.list:
        for exp in list_all():
            status = exp.get("status", "?")
            print(f"  [{status}] {exp['id']} {exp['name']} ({exp['type']})")
    elif args.check:
        result = check_guardrails(args.check)
        print(json.dumps(result, indent=2, default=str))
    elif args.results:
        result = get_results(args.results)
        print(json.dumps(result, indent=2, default=str))
    elif args.stop:
        ok = stop(args.stop)
        print("Stopped" if ok else "Not found")
    else:
        p.print_help()
