#!/usr/bin/env python3
"""
Model Distillation — Knowledge transfer between models.

Records successful reasoning chains from multi_agent_engine results.
Extracts patterns: {problem_type, reasoning_template, model_used}.
Enables retrieval of best known approaches for recurring problem types.

Storage: workspace/runtime/distillation_data.json
Log: workspace/runtime/logs/distillation.log
"""

import json
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from runtime_utils import load_json, save_json

BASE_DIR = Path(__file__).parent
DISTILLATION_FILE = BASE_DIR / "distillation_data.json"
TELEMETRY_LOG = BASE_DIR / "logs" / "distillation.log"
MULTI_AGENT_LOG = BASE_DIR / "logs" / "multi_agent.log"

# Config
MAX_TEMPLATES = 100
MAX_CHAINS_PER_TYPE = 10
MIN_CONFIDENCE_FOR_RECORD = 0.6


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_log_lines(log_path: Path, max_lines: int = 500) -> list:
    """Load recent entries from a JSON-line log file."""
    lines = []
    try:
        with open(log_path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        pass
    return lines[-max_lines:]


# ── Problem Classification ──────────────────────────────────────


def _classify_problem(query: str) -> str:
    """Classify a query into a problem type for template matching."""
    query_lower = query.lower()

    classifications = [
        ("container_issue", ["container", "docker", "restart", "crash", "caido", "down"]),
        ("vpn_issue", ["vpn", "gluetun", "leak", "nordvpn", "proxy"]),
        ("network_issue", ["network", "internet", "latency", "dns", "ping", "connectivity"]),
        ("ollama_issue", ["ollama", "model", "inference", "gpu", "notebook"]),
        ("storage_issue", ["disk", "storage", "volume", "space"]),
        ("performance_issue", ["slow", "timeout", "latency", "performance"]),
        ("security_issue", ["security", "permission", "auth", "blocked"]),
    ]

    for problem_type, keywords in classifications:
        if any(kw in query_lower for kw in keywords):
            return problem_type

    return "general"


# ── Core API ────────────────────────────────────────────────────


def record_reasoning_chain(query: str, result: dict, confidence: float = 0.0):
    """Record a successful reasoning chain for future reference.

    Args:
        query: the original problem/question
        result: multi_agent_engine result dict with analysis, verification, conclusion
        confidence: confidence score for this reasoning (0-1)
    """
    if confidence < MIN_CONFIDENCE_FOR_RECORD:
        return

    if not result.get("success"):
        return

    problem_type = _classify_problem(query)

    chain = {
        "query": query[:300],
        "problem_type": problem_type,
        "analysis": (result.get("analysis") or "")[:500],
        "verification": (result.get("verification") or "")[:300],
        "conclusion": (result.get("conclusion") or "")[:500],
        "agents_used": result.get("agents_used", []),
        "confidence": round(confidence, 4),
        "recorded_at": _now().isoformat(),
    }

    # Load existing data
    data = load_json(DISTILLATION_FILE, {"templates": {}, "chains": []})
    chains = data.get("chains", [])
    chains.append(chain)

    # Cap total chains
    if len(chains) > MAX_TEMPLATES * MAX_CHAINS_PER_TYPE:
        chains = chains[-(MAX_TEMPLATES * MAX_CHAINS_PER_TYPE // 2):]

    data["chains"] = chains

    # Update templates (best chain per problem type)
    _update_templates(data)

    save_json(DISTILLATION_FILE, data)

    _log({
        "type": "chain_recorded",
        "problem_type": problem_type,
        "confidence": confidence,
        "agents": result.get("agents_used", []),
    })


def _update_templates(data: dict):
    """Update reasoning templates from recorded chains."""
    chains = data.get("chains", [])
    by_type = defaultdict(list)

    for chain in chains:
        pt = chain.get("problem_type", "general")
        by_type[pt].append(chain)

    templates = {}
    for pt, type_chains in by_type.items():
        # Keep top chains by confidence
        sorted_chains = sorted(
            type_chains,
            key=lambda c: c.get("confidence", 0),
            reverse=True,
        )[:MAX_CHAINS_PER_TYPE]

        if not sorted_chains:
            continue

        best = sorted_chains[0]
        # Determine most effective model combination
        model_counts = defaultdict(int)
        for c in sorted_chains:
            for agent in c.get("agents_used", []):
                model_counts[agent] += 1

        best_models = sorted(model_counts, key=model_counts.get, reverse=True)

        templates[pt] = {
            "problem_type": pt,
            "reasoning_template": best.get("conclusion", "")[:500],
            "model_used": best_models[0] if best_models else "unknown",
            "all_models": best_models,
            "sample_count": len(sorted_chains),
            "avg_confidence": round(
                sum(c.get("confidence", 0) for c in sorted_chains) / len(sorted_chains),
                4,
            ),
            "best_confidence": best.get("confidence", 0),
            "last_updated": _now().isoformat(),
        }

    data["templates"] = templates


def get_reasoning_template(problem_type: str) -> dict | None:
    """Get the best known reasoning template for a problem type.

    Args:
        problem_type: one of the classified problem types

    Returns:
        Template dict or None if no template exists.
    """
    data = load_json(DISTILLATION_FILE, {"templates": {}})
    template = data.get("templates", {}).get(problem_type)

    if template:
        _log({
            "type": "template_retrieved",
            "problem_type": problem_type,
            "confidence": template.get("avg_confidence", 0),
        })

    return template


def get_all_templates() -> dict:
    """Get all stored reasoning templates."""
    data = load_json(DISTILLATION_FILE, {"templates": {}})
    return data.get("templates", {})


def extract_patterns_from_logs() -> list:
    """Extract reasoning patterns from multi_agent_engine logs.

    Scans multi_agent.log for successful reasoning entries and
    records them if not already captured.

    Returns list of newly extracted patterns.
    """
    log_entries = _load_log_lines(MULTI_AGENT_LOG)
    new_patterns = []

    for entry in log_entries:
        if entry.get("type") != "reasoning":
            continue
        if entry.get("status") != "complete":
            continue

        query = entry.get("query", "")
        if not query:
            continue

        problem_type = _classify_problem(query)
        agents = entry.get("agents_used", [])

        new_patterns.append({
            "problem_type": problem_type,
            "agents_used": agents,
            "query_preview": query[:100],
        })

    _log({
        "type": "patterns_extracted",
        "count": len(new_patterns),
    })

    return new_patterns
