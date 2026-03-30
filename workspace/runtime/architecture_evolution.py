#!/usr/bin/env python3
"""
Architecture Evolution — Detects architectural limitations and suggests improvements.

Analyzes module health, test coverage, integration gaps, and performance
bottlenecks to propose structural improvements to Tenazas.

Safety: Proposal-only. Never auto-modifies architecture.
"""

import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
TELEMETRY_LOG = BASE_DIR / "logs" / "architecture.log"
EVOLUTION_FILE = BASE_DIR / "evolution_proposals.json"

# Known modules and their dependencies
MODULE_REGISTRY = {
    "orchestrator": {"deps": ["confidence_engine", "action_mapper", "infra_diagnostics", "self_optimizer"]},
    "runtime": {"deps": ["executors", "docker_wrapper"]},
    "executors": {"deps": ["docker_wrapper"]},
    "docker_wrapper": {"deps": []},
    "confidence_engine": {"deps": []},
    "action_mapper": {"deps": []},
    "infra_diagnostics": {"deps": []},
    "metrics_engine": {"deps": []},
    "infra_memory": {"deps": []},
    "runbook_generator": {"deps": []},
    "multi_agent_engine": {"deps": []},
    "self_optimizer": {"deps": ["metrics_engine"]},
    "agent_lab": {"deps": ["multi_agent_engine"]},
    "shared_memory": {"deps": []},
    "world_model": {"deps": []},
    "goal_engine": {"deps": ["metrics_engine"]},
    "planner": {"deps": ["action_mapper"]},
    "predictive_engine": {"deps": ["infra_memory"]},
    "model_router": {"deps": []},
    "skill_generator": {"deps": ["metrics_engine"]},
    "safety_guard": {"deps": []},
    "idle_learning_engine": {"deps": ["metrics_engine"]},
    "background_agents": {"deps": ["infra_memory", "metrics_engine"]},
    "research_engine": {"deps": ["infra_memory", "runbook_generator"]},
    "self_debugger": {"deps": []},
    "self_dev_engine": {"deps": ["self_debugger", "metrics_engine"]},
    "model_distillation": {"deps": ["multi_agent_engine"]},
    "dataset_builder": {"deps": ["metrics_engine"]},
    "self_benchmark": {"deps": ["metrics_engine"]},
    "simulation_lab": {"deps": ["infra_diagnostics", "action_mapper"]},
    "architecture_evolution": {"deps": []},
}


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def check_module_health() -> list:
    """Check which modules exist and are importable."""
    results = []
    for name in MODULE_REGISTRY:
        path = BASE_DIR / f"{name}.py"
        exists = path.exists()
        results.append({
            "module": name,
            "exists": exists,
            "path": str(path),
            "dependencies": MODULE_REGISTRY[name]["deps"],
        })
    return results


def detect_gaps() -> list:
    """Detect missing modules, broken dependencies, and integration gaps."""
    health = check_module_health()
    existing = {m["module"] for m in health if m["exists"]}
    gaps = []

    for m in health:
        if not m["exists"]:
            gaps.append({
                "type": "missing_module",
                "module": m["module"],
                "severity": "warning",
                "suggestion": f"Implement {m['module']}.py",
            })
            continue

        for dep in m["dependencies"]:
            if dep not in existing:
                gaps.append({
                    "type": "broken_dependency",
                    "module": m["module"],
                    "dependency": dep,
                    "severity": "critical",
                    "suggestion": f"{m['module']} depends on {dep} which is missing",
                })

    return gaps


def suggest_improvements() -> list:
    """Analyze architecture and suggest structural improvements."""
    proposals = []
    health = check_module_health()
    existing = [m for m in health if m["exists"]]

    # Check module count
    if len(existing) > 25:
        proposals.append({
            "type": "complexity",
            "title": "Considerar agrupar modulos en sub-paquetes",
            "description": f"{len(existing)} modulos en runtime/ — considerar crear sub-directorios (reasoning/, monitoring/, learning/)",
            "priority": "low",
            "requires_approval": True,
        })

    # Check for modules without tests
    test_dir = BASE_DIR.parent.parent / "tests"
    if test_dir.exists():
        test_files = {f.stem for f in test_dir.glob("test_*.py")}
        untested = [m["module"] for m in existing
                    if not any(m["module"] in t for t in test_files)]
        if untested:
            proposals.append({
                "type": "test_coverage",
                "title": "Modulos sin tests",
                "description": f"Modulos sin test suite dedicada: {', '.join(untested[:10])}",
                "priority": "medium",
                "requires_approval": True,
            })

    return proposals


def evolve() -> dict:
    """Run full architecture analysis. Returns report."""
    gaps = detect_gaps()
    proposals = suggest_improvements()

    report = {
        "ts": _now().isoformat(),
        "module_count": len([m for m in check_module_health() if m["exists"]]),
        "gaps": gaps,
        "proposals": proposals,
        "gaps_count": len(gaps),
        "proposals_count": len(proposals),
    }

    # Save
    EVOLUTION_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(EVOLUTION_FILE, "w") as f:
        json.dump(report, f, indent=2, default=str)

    _log({
        "type": "evolution_analysis",
        "modules": report["module_count"],
        "gaps": report["gaps_count"],
        "proposals": report["proposals_count"],
    })

    return report
