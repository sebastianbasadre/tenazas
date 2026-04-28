#!/usr/bin/env python3
"""
Retrieval Quality Scoring — Evaluates RAG retrieval quality.

Metrics:
  - Context overlap (redundancy between results)
  - Document diversity (variety of sources)
  - Recency (freshness of retrieved docs)
  - Semantic relevance (confidence scores)

If quality < threshold → recommend search expansion.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
TELEMETRY_LOG = BASE_DIR / "logs" / "retrieval_quality.log"

QUALITY_THRESHOLD = 0.6



def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _overlap_score(results: list) -> float:
    """Measure redundancy between results. Low overlap = high diversity = good."""
    if len(results) <= 1:
        return 1.0  # No overlap possible

    word_sets = []
    for r in results:
        words = set(r.get("text", "").lower().split())
        word_sets.append(words)

    overlaps = []
    for i in range(len(word_sets)):
        for j in range(i + 1, len(word_sets)):
            if word_sets[i] and word_sets[j]:
                intersection = len(word_sets[i] & word_sets[j])
                smaller = min(len(word_sets[i]), len(word_sets[j]))
                overlaps.append(intersection / smaller if smaller else 0)

    avg_overlap = sum(overlaps) / len(overlaps) if overlaps else 0
    return round(1.0 - avg_overlap, 4)  # Invert: low overlap = high score


def _diversity_score(results: list) -> float:
    """Measure source variety. More diverse sources = better."""
    if not results:
        return 0.0

    sources = set()
    families = set()
    for r in results:
        source = r.get("source", "")
        sources.add(source)
        parts = source.split("/")
        families.add(parts[0] if parts else source)

    # Normalize: 3+ families = 1.0, 2 = 0.7, 1 = 0.4
    n = len(families)
    if n >= 4:
        return 1.0
    if n >= 3:
        return 0.85
    if n >= 2:
        return 0.7
    return 0.4


def _recency_score(results: list) -> float:
    """Score freshness. Skills and core docs count as always fresh."""
    if not results:
        return 0.0

    scores = []
    for r in results:
        source = r.get("source", "")
        if "SKILL.md" in source or source in ("SOUL.md", "AGENTS.md"):
            scores.append(1.0)
        elif source.startswith("memory/"):
            match = re.match(r"memory/(\d{4}-\d{2}-\d{2})", source)
            if match:
                try:
                    days = (datetime.now() - datetime.strptime(match.group(1), "%Y-%m-%d")).days
                    scores.append(max(1.0 - (days / 30), 0.2))
                except ValueError:
                    scores.append(0.5)
            else:
                scores.append(0.5)
        else:
            scores.append(0.7)

    return round(sum(scores) / len(scores), 4)


def _relevance_score(results: list) -> float:
    """Average confidence/score of results."""
    if not results:
        return 0.0
    scores = [r.get("score", r.get("confidence", 0.5)) for r in results]
    return round(sum(scores) / len(scores), 4)


def evaluate(results: list) -> dict:
    """Evaluate retrieval quality across all dimensions.

    Returns:
        {
            "quality_score": float 0-1,
            "overlap": float,
            "diversity": float,
            "recency": float,
            "relevance": float,
            "should_expand": bool,
            "result_count": int,
        }
    """
    overlap = _overlap_score(results)
    diversity = _diversity_score(results)
    recency = _recency_score(results)
    relevance = _relevance_score(results)

    quality = round(
        overlap * 0.20 + diversity * 0.25 + recency * 0.20 + relevance * 0.35,
        4,
    )

    result = {
        "quality_score": quality,
        "overlap": overlap,
        "diversity": diversity,
        "recency": recency,
        "relevance": relevance,
        "should_expand": quality < QUALITY_THRESHOLD,
        "result_count": len(results),
    }

    _log({"type": "retrieval_quality", "score": quality, "expand": result["should_expand"]})
    return result
