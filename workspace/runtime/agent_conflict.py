#!/usr/bin/env python3
"""
Agent Conflict Detector — Detects disagreement between multi-agent outputs.

When Qwen and Phi disagree (e.g., Qwen says "network issue", Phi says
"container crash"), computes a disagreement score and may trigger escalation.

Disagreement signals:
  - Keyword divergence (different problem categories mentioned)
  - Confidence delta between agents
  - Contradictory conclusions
"""

import json
import re
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
TELEMETRY_LOG = BASE_DIR / "logs" / "agent_conflict.log"

# Problem category keywords for semantic comparison
CATEGORY_KEYWORDS = {
    "network": {"network", "internet", "latency", "ping", "dns", "connectivity", "red", "latencia"},
    "vpn": {"vpn", "gluetun", "leak", "nordvpn", "proxy", "country", "argentina"},
    "container": {"container", "docker", "restart", "crash", "exited", "caido", "loop"},
    "ollama": {"ollama", "model", "inference", "gpu", "notebook", "timeout"},
    "storage": {"disk", "space", "storage", "volume", "disco", "espacio"},
    "config": {"config", "setting", "parameter", "configuracion"},
}

ESCALATION_THRESHOLD = 0.6



def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _extract_categories(text: str) -> set:
    """Extract problem categories mentioned in text."""
    if not text:
        return set()
    text_lower = text.lower()
    found = set()
    for cat, keywords in CATEGORY_KEYWORDS.items():
        if any(kw in text_lower for kw in keywords):
            found.add(cat)
    return found


def compute_disagreement(
    analysis: str,
    verification: str,
    analysis_confidence: float = 0.0,
    verification_confidence: float = 0.0,
) -> dict:
    """Compute disagreement score between two agent outputs.

    Returns:
        {
            "score": float 0-1 (0=agree, 1=fully disagree),
            "category_divergence": float,
            "confidence_delta": float,
            "analysis_categories": list,
            "verification_categories": list,
            "should_escalate": bool,
        }
    """
    # Category divergence
    a_cats = _extract_categories(analysis)
    v_cats = _extract_categories(verification)

    if a_cats and v_cats:
        intersection = a_cats & v_cats
        union = a_cats | v_cats
        category_similarity = len(intersection) / len(union) if union else 1.0
        category_divergence = 1.0 - category_similarity
    elif not a_cats and not v_cats:
        category_divergence = 0.0
    else:
        category_divergence = 0.5  # One has categories, other doesn't

    # Confidence delta
    confidence_delta = abs(analysis_confidence - verification_confidence)

    # Contradiction detection (simple keyword check)
    contradiction_score = 0.0
    negation_pairs = [
        ("correcto", "incorrecto"), ("ok", "fail"), ("up", "down"),
        ("normal", "anormal"), ("estable", "inestable"),
    ]
    a_lower = (analysis or "").lower()
    v_lower = (verification or "").lower()
    for pos, neg in negation_pairs:
        if (pos in a_lower and neg in v_lower) or (neg in a_lower and pos in v_lower):
            contradiction_score = 0.3
            break

    # Combined score
    score = round(
        category_divergence * 0.5 + confidence_delta * 0.3 + contradiction_score * 0.2,
        4,
    )

    result = {
        "score": min(score, 1.0),
        "category_divergence": round(category_divergence, 4),
        "confidence_delta": round(confidence_delta, 4),
        "contradiction_detected": contradiction_score > 0,
        "analysis_categories": sorted(a_cats),
        "verification_categories": sorted(v_cats),
        "should_escalate": score >= ESCALATION_THRESHOLD,
    }

    _log({"type": "conflict_check", "score": result["score"],
           "escalate": result["should_escalate"]})

    return result


def check_multi_agent_result(reasoning_result: dict) -> dict:
    """Check a multi_agent_engine.reason() result for conflicts.

    Args:
        reasoning_result: output from multi_agent_engine.reason()

    Returns: disagreement analysis dict.
    """
    analysis = reasoning_result.get("analysis", "")
    verification = reasoning_result.get("verification", "")

    return compute_disagreement(analysis, verification)
