#!/usr/bin/env python3
"""
Confidence Engine — Evaluates retrieved context quality for Tenazas RAG.

Combines multiple signals into a normalized 0-1 confidence score:
- Vector similarity (how close are the embeddings)
- Cross-source agreement (do multiple sources agree)
- Recency (how fresh is the information)
- Source reliability (SKILL.md > SOUL.md > memory)

Thresholds:
  0.8-1.0  high    → answer normally
  0.6-0.79 medium  → answer with uncertainty caveat
  <0.6     low     → ask clarification or expand retrieval
"""

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from runtime_utils import now

# ── Config ───────────────────────────────────────────────────────

WEIGHTS = {
    "similarity": 0.35,
    "agreement": 0.25,
    "recency": 0.20,
    "source_quality": 0.20,
}

THRESHOLDS = {
    "high": 0.80,
    "medium": 0.60,
}

# Source reliability tiers (0-1)
SOURCE_RELIABILITY = {
    "skill": 1.0,       # skills/*/SKILL.md — curated, authoritative
    "core_doc": 0.95,   # SOUL.md, AGENTS.md — identity/config
    "workspace": 0.80,  # other workspace .md files
    "memory_recent": 0.70,  # memory from last 7 days
    "memory_old": 0.40,     # older memory
    "working": 0.90,    # current conversation context
    "unknown": 0.50,
}

DIAGNOSTICS_LOG = Path(__file__).parent / "logs" / "diagnostics.log"


# ── Helpers ──────────────────────────────────────────────────────



def _log_diagnostic(entry: dict):
    """Append JSON line to diagnostics log."""
    DIAGNOSTICS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(DIAGNOSTICS_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _classify_source(source: str) -> str:
    """Classify a source path into a reliability tier."""
    if source == "working_memory":
        return "working"
    if source.startswith("skills/") and source.endswith("SKILL.md"):
        return "skill"
    if source in ("SOUL.md", "AGENTS.md", "HEARTBEAT.md"):
        return "core_doc"
    if source.startswith("memory/"):
        match = re.match(r"memory/(\d{4}-\d{2}-\d{2})", source)
        if match:
            try:
                days = (now().date() - datetime.strptime(match.group(1), "%Y-%m-%d").date()).days
                return "memory_recent" if days <= 7 else "memory_old"
            except ValueError:
                pass
        return "memory_old"
    if "/" not in source and source.endswith(".md"):
        return "workspace"
    return "unknown"


# ── Scoring Components ───────────────────────────────────────────


def _similarity_score(results: list) -> float:
    """Average vector similarity of top results. Range 0-1."""
    if not results:
        return 0.0
    scores = [r.get("vector_score", r.get("score", 0)) for r in results]
    return min(sum(scores) / len(scores), 1.0)


def _agreement_score(results: list) -> float:
    """How many distinct sources agree on the topic. Range 0-1.

    If multiple independent sources cover similar content, confidence is higher.
    """
    if len(results) <= 1:
        return 0.3  # Single source = low agreement

    sources = [r.get("source", "") for r in results]
    # Group by source "family" (e.g., skills/X, memory/Y)
    families = set()
    for s in sources:
        parts = s.split("/")
        families.add(parts[0] if parts else s)

    # 2 families = 0.6, 3+ = 0.8, 4+ = 1.0
    n = len(families)
    if n >= 4:
        return 1.0
    if n >= 3:
        return 0.8
    if n >= 2:
        return 0.6
    return 0.3


def _recency_score(results: list) -> float:
    """How recent are the sources. Range 0-1.

    Skills and core docs are always "fresh" (they're maintained).
    Memory files are scored by date.
    """
    if not results:
        return 0.0

    scores = []
    for r in results:
        tier = _classify_source(r.get("source", ""))
        if tier in ("skill", "core_doc", "workspace", "working"):
            scores.append(1.0)
        elif tier == "memory_recent":
            scores.append(0.8)
        elif tier == "memory_old":
            scores.append(0.4)
        else:
            scores.append(0.5)

    return sum(scores) / len(scores)


def _source_quality_score(results: list) -> float:
    """Average source reliability. Range 0-1."""
    if not results:
        return 0.0

    scores = []
    for r in results:
        tier = _classify_source(r.get("source", ""))
        scores.append(SOURCE_RELIABILITY.get(tier, 0.5))

    return sum(scores) / len(scores)


# ── Main API ─────────────────────────────────────────────────────


def compute_confidence(results: list) -> dict:
    """Compute confidence score for a set of retrieval results.

    Returns:
        {
            "score": float (0-1),
            "level": "high" | "medium" | "low",
            "components": {similarity, agreement, recency, source_quality},
            "recommendation": str,
        }
    """
    components = {
        "similarity": _similarity_score(results),
        "agreement": _agreement_score(results),
        "recency": _recency_score(results),
        "source_quality": _source_quality_score(results),
    }

    score = sum(
        components[k] * WEIGHTS[k] for k in WEIGHTS
    )
    score = round(min(max(score, 0.0), 1.0), 4)

    if score >= THRESHOLDS["high"]:
        level = "high"
        recommendation = "answer_normally"
    elif score >= THRESHOLDS["medium"]:
        level = "medium"
        recommendation = "answer_with_caveat"
    else:
        level = "low"
        recommendation = "expand_or_clarify"

    result = {
        "score": score,
        "level": level,
        "components": components,
        "recommendation": recommendation,
    }

    _log_diagnostic({
        "type": "confidence",
        "score": score,
        "level": level,
        "components": components,
        "result_count": len(results),
    })

    return result


def self_check(query: str, results: list, confidence: dict) -> dict:
    """Decide answer strategy based on confidence.

    Returns:
        {
            "strategy": "answer" | "answer_with_caveat" | "expand" | "clarify",
            "caveat": str | None,
            "expand_query": str | None,
        }
    """
    level = confidence["level"]
    score = confidence["score"]

    if level == "high":
        return {"strategy": "answer", "caveat": None, "expand_query": None}

    if level == "medium":
        weak = min(confidence["components"], key=confidence["components"].get)
        caveat_map = {
            "similarity": "Los resultados no son un match exacto con tu pregunta.",
            "agreement": "Solo una fuente cubre este tema — verificá si es correcto.",
            "recency": "La información puede no estar actualizada.",
            "source_quality": "Las fuentes son de baja prioridad (logs/notas).",
        }
        return {
            "strategy": "answer_with_caveat",
            "caveat": caveat_map.get(weak, "Confianza media en los resultados."),
            "expand_query": None,
        }

    # Low confidence
    if not results:
        return {
            "strategy": "clarify",
            "caveat": "No encontré contexto relevante.",
            "expand_query": None,
        }

    return {
        "strategy": "expand",
        "caveat": "Confianza baja — expandiendo búsqueda.",
        "expand_query": f"{query} infraestructura tenazas",
    }
