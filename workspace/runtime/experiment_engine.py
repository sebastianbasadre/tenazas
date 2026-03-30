#!/usr/bin/env python3
"""
Experiment Engine — Run controlled A/B experiments on pipelines and models.

Examples: Pipeline A vs B, Prompt v1 vs v2, Model comparison.
Records: accuracy, latency, confidence, token usage.
Powers self_optimizer with empirical data.
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
EXPERIMENTS_DIR = BASE_DIR / "experiments"
TELEMETRY_LOG = BASE_DIR / "logs" / "experiments.log"


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def create_experiment(
    name: str,
    variants: list,
    metric: str = "confidence",
    description: str = "",
) -> dict:
    """Create a new experiment definition.

    Args:
        name: experiment name (e.g., "rag_vs_multiagent")
        variants: list of variant dicts [{name, config}]
        metric: primary metric to compare
        description: what we're testing

    Returns: experiment definition dict.
    """
    exp_id = hashlib.md5(f"{name}:{_now().isoformat()}".encode()).hexdigest()[:8]

    experiment = {
        "id": exp_id,
        "name": name,
        "description": description,
        "variants": variants,
        "metric": metric,
        "status": "created",
        "results": [],
        "created_at": _now().isoformat(),
    }

    EXPERIMENTS_DIR.mkdir(parents=True, exist_ok=True)
    path = EXPERIMENTS_DIR / f"{exp_id}.json"
    with open(path, "w") as f:
        json.dump(experiment, f, indent=2, default=str)

    _log({"type": "experiment_created", "id": exp_id, "name": name})
    return experiment


def record_result(
    experiment_id: str,
    variant: str,
    metrics: dict,
) -> bool:
    """Record a result for an experiment variant.

    Args:
        experiment_id: experiment ID
        variant: variant name
        metrics: {confidence, latency_ms, token_usage, etc.}
    """
    path = EXPERIMENTS_DIR / f"{experiment_id}.json"
    if not path.exists():
        return False

    try:
        with open(path) as f:
            experiment = json.load(f)
    except json.JSONDecodeError:
        return False

    experiment["results"].append({
        "variant": variant,
        "metrics": metrics,
        "ts": _now().isoformat(),
    })
    experiment["status"] = "running"

    with open(path, "w") as f:
        json.dump(experiment, f, indent=2, default=str)

    return True


def analyze_experiment(experiment_id: str) -> dict | None:
    """Analyze results of an experiment.

    Returns: {winner, results_by_variant, conclusion}
    """
    path = EXPERIMENTS_DIR / f"{experiment_id}.json"
    if not path.exists():
        return None

    try:
        with open(path) as f:
            experiment = json.load(f)
    except json.JSONDecodeError:
        return None

    metric = experiment.get("metric", "confidence")
    by_variant = {}

    for r in experiment.get("results", []):
        v = r["variant"]
        if v not in by_variant:
            by_variant[v] = []
        by_variant[v].append(r["metrics"].get(metric, 0))

    averages = {}
    for v, values in by_variant.items():
        averages[v] = round(sum(values) / len(values), 4) if values else 0

    winner = max(averages, key=averages.get) if averages else None

    analysis = {
        "experiment_id": experiment_id,
        "name": experiment.get("name"),
        "metric": metric,
        "averages": averages,
        "winner": winner,
        "sample_sizes": {v: len(vals) for v, vals in by_variant.items()},
        "conclusion": f"Variant '{winner}' performed best on {metric}" if winner else "Insufficient data",
    }

    # Save analysis back
    experiment["analysis"] = analysis
    experiment["status"] = "analyzed"
    with open(path, "w") as f:
        json.dump(experiment, f, indent=2, default=str)

    _log({"type": "experiment_analyzed", "id": experiment_id, "winner": winner})
    return analysis


def list_experiments() -> list:
    """List all experiments."""
    if not EXPERIMENTS_DIR.exists():
        return []
    experiments = []
    for path in sorted(EXPERIMENTS_DIR.glob("*.json")):
        try:
            with open(path) as f:
                exp = json.load(f)
            experiments.append({
                "id": exp["id"],
                "name": exp["name"],
                "status": exp["status"],
                "results_count": len(exp.get("results", [])),
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return experiments
