#!/usr/bin/env python3
"""
Prompt Optimizer — Analyzes prompt efficiency and suggests improvements.

Detects: hallucination patterns, excessive verbosity, low confidence,
high token usage. Suggests but NEVER auto-deploys prompt changes.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
TELEMETRY_LOG = BASE_DIR / "logs" / "prompt_optimizer.log"
SUGGESTIONS_FILE = BASE_DIR / "prompt_suggestions.json"
DECISIONS_LOG = BASE_DIR / "logs" / "decisions.jsonl"



def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_decisions(limit: int = 200) -> list:
    if not DECISIONS_LOG.exists():
        return []
    try:
        lines = DECISIONS_LOG.read_text().strip().split("\n")
        entries = []
        for line in lines[-limit:]:
            try:
                entries.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return entries
    except OSError:
        return []


def analyze_confidence_patterns() -> dict:
    """Find query types with consistently low confidence."""
    decisions = _load_decisions()
    by_type = {}
    for d in decisions:
        qt = d.get("classification", "unknown")
        if qt not in by_type:
            by_type[qt] = []
        by_type[qt].append(d.get("confidence", 0))

    low_confidence = {}
    for qt, scores in by_type.items():
        if len(scores) >= 3:
            avg = sum(scores) / len(scores)
            if avg < 0.65:
                low_confidence[qt] = {"avg_confidence": round(avg, 4), "count": len(scores)}

    return low_confidence


def analyze_token_efficiency() -> dict:
    """Find pipelines with high token usage relative to quality."""
    decisions = _load_decisions()
    by_pipeline = {}
    for d in decisions:
        p = d.get("pipeline", "unknown")
        if p not in by_pipeline:
            by_pipeline[p] = {"tokens": [], "confidences": []}
        by_pipeline[p]["tokens"].append(d.get("context_tokens", 0))
        by_pipeline[p]["confidences"].append(d.get("confidence", 0))

    inefficient = {}
    for p, data in by_pipeline.items():
        if len(data["tokens"]) >= 3:
            avg_tokens = sum(data["tokens"]) / len(data["tokens"])
            avg_conf = sum(data["confidences"]) / len(data["confidences"])
            if avg_tokens > 2000 and avg_conf < 0.7:
                inefficient[p] = {
                    "avg_tokens": round(avg_tokens),
                    "avg_confidence": round(avg_conf, 4),
                    "suggestion": "Reduce context size or improve retrieval quality",
                }

    return inefficient


def analyze_escalation_patterns() -> dict:
    """Find query types that frequently escalate."""
    decisions = _load_decisions()
    by_type = {}
    for d in decisions:
        qt = d.get("classification", "unknown")
        if qt not in by_type:
            by_type[qt] = {"total": 0, "escalated": 0}
        by_type[qt]["total"] += 1
        if d.get("escalated"):
            by_type[qt]["escalated"] += 1

    high_escalation = {}
    for qt, data in by_type.items():
        if data["total"] >= 3:
            rate = data["escalated"] / data["total"]
            if rate > 0.3:
                high_escalation[qt] = {
                    "escalation_rate": round(rate, 4),
                    "suggestion": "Consider routing directly to multi-agent for this type",
                }

    return high_escalation


def generate_suggestions() -> list:
    """Analyze all patterns and generate improvement suggestions.

    NEVER auto-deploys changes. Suggestions only.
    """
    suggestions = []

    low_conf = analyze_confidence_patterns()
    for qt, data in low_conf.items():
        suggestions.append({
            "type": "low_confidence",
            "target": qt,
            "suggestion": f"Query type '{qt}' has avg confidence {data['avg_confidence']}. Consider improving RAG coverage or adding relevant SKILL.md docs.",
            "requires_approval": True,
        })

    inefficient = analyze_token_efficiency()
    for p, data in inefficient.items():
        suggestions.append({
            "type": "token_inefficiency",
            "target": p,
            "suggestion": data["suggestion"],
            "requires_approval": True,
        })

    high_esc = analyze_escalation_patterns()
    for qt, data in high_esc.items():
        suggestions.append({
            "type": "high_escalation",
            "target": qt,
            "suggestion": data["suggestion"],
            "requires_approval": True,
        })

    # Save
    SUGGESTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(SUGGESTIONS_FILE, "w") as f:
        json.dump({"suggestions": suggestions, "ts": now().isoformat()},
                  f, indent=2, default=str)

    _log({"type": "prompt_analysis", "suggestions": len(suggestions)})
    return suggestions
