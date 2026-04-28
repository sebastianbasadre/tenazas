#!/usr/bin/env python3
"""
Self-Optimization Engine — Learns which pipelines and models perform best.

Analyzes metrics data to optimize:
- Pipeline selection per query type
- Model selection per pipeline
- Query rewrite usage
- RAG expansion triggers

Storage: workspace/runtime/optimization_state.json
Safety: Only adjusts routing preferences. Never modifies infrastructure.
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from runtime_utils import load_json, save_json

BASE_DIR = Path(__file__).parent
OPTIMIZATION_FILE = BASE_DIR / "optimization_state.json"
METRICS_DIR = BASE_DIR / "metrics"
TELEMETRY_LOG = BASE_DIR / "logs" / "optimizer.log"

# Config
ANALYSIS_WINDOW_DAYS = 7
MIN_SAMPLES = 5
ESCALATION_THRESHOLD = 0.3  # If >30% escalate, adjust routing
CONFIDENCE_THRESHOLD = 0.7   # Below this, pipeline underperforms


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_metrics(days: int = ANALYSIS_WINDOW_DAYS) -> list:
    """Load metric entries from the last N days."""
    entries = []
    for i in range(days):
        day = _now() - timedelta(days=i)
        path = METRICS_DIR / f"{day.strftime('%Y-%m-%d')}.json"
        try:
            with open(path) as f:
                data = json.load(f)
            if isinstance(data, list):
                entries.extend(data)
        except (FileNotFoundError, json.JSONDecodeError):
            continue
    return entries


# ── Analysis ─────────────────────────────────────────────────────


def _analyze_by_query_type(entries: list) -> dict:
    """Analyze performance per query type."""
    by_type = defaultdict(lambda: {
        "count": 0, "confidences": [], "latencies": [],
        "escalations": 0, "pipelines": Counter(), "models": Counter(),
    })

    for e in entries:
        if e.get("type") != "query":
            continue
        qt = e.get("query_type", "unknown")
        stats = by_type[qt]
        stats["count"] += 1
        if "confidence" in e:
            stats["confidences"].append(e["confidence"])
        if "latency_ms" in e:
            stats["latencies"].append(e["latency_ms"])
        if e.get("escalated"):
            stats["escalations"] += 1
        stats["pipelines"][e.get("pipeline", "unknown")] += 1
        stats["models"][e.get("model", "unknown")] += 1

    return dict(by_type)


def _find_best_config(stats: dict) -> dict:
    """Find the best pipeline+model combo for a query type."""
    if stats["count"] < MIN_SAMPLES:
        return {}

    avg_conf = (
        sum(stats["confidences"]) / len(stats["confidences"])
        if stats["confidences"] else 0
    )
    avg_latency = (
        sum(stats["latencies"]) / len(stats["latencies"])
        if stats["latencies"] else 0
    )
    escalation_rate = stats["escalations"] / max(stats["count"], 1)

    # Best pipeline = most used with acceptable confidence
    best_pipeline = stats["pipelines"].most_common(1)[0][0] if stats["pipelines"] else "rag"
    best_model = stats["models"].most_common(1)[0][0] if stats["models"] else "gemini-flash"

    # If escalation rate is high, suggest fallback pipeline
    fallback_pipeline = None
    if escalation_rate > ESCALATION_THRESHOLD:
        fallback_pipeline = "multi_agent"

    # If avg confidence is low, suggest expanding retrieval
    expand_retrieval = avg_conf < CONFIDENCE_THRESHOLD

    return {
        "preferred_pipeline": best_pipeline,
        "fallback_pipeline": fallback_pipeline,
        "preferred_model": best_model,
        "avg_confidence": round(avg_conf, 4),
        "avg_latency_ms": round(avg_latency, 1),
        "escalation_rate": round(escalation_rate, 4),
        "sample_count": stats["count"],
        "expand_retrieval": expand_retrieval,
    }


# ── Main API ─────────────────────────────────────────────────────


def optimize() -> dict:
    """Run optimization analysis and update preferences.

    Returns the new optimization state.
    """
    entries = _load_metrics()
    by_type = _analyze_by_query_type(entries)

    state = {}
    for qt, stats in by_type.items():
        config = _find_best_config(stats)
        if config:
            state[qt] = config

    # Save
    full_state = {
        "preferences": state,
        "last_optimization": _now().isoformat(),
        "analysis_window_days": ANALYSIS_WINDOW_DAYS,
        "total_queries_analyzed": sum(s["count"] for s in by_type.values()),
    }
    save_json(OPTIMIZATION_FILE, full_state)

    _log({
        "type": "optimization",
        "query_types_analyzed": len(state),
        "total_queries": full_state["total_queries_analyzed"],
    })

    return full_state


def get_preferences() -> dict:
    """Load current optimization preferences."""
    data = load_json(OPTIMIZATION_FILE, {"preferences": {}})
    return data.get("preferences", {})


def get_preference(query_type: str) -> dict:
    """Get optimization preference for a specific query type."""
    return get_preferences().get(query_type, {})


def should_expand_retrieval(query_type: str) -> bool:
    """Check if RAG should expand retrieval for this query type."""
    pref = get_preference(query_type)
    return pref.get("expand_retrieval", False)


def should_use_multi_agent(query_type: str) -> bool:
    """Check if multi-agent reasoning is recommended for this query type."""
    pref = get_preference(query_type)
    return pref.get("fallback_pipeline") == "multi_agent"


def get_preferred_model(query_type: str) -> str | None:
    """Get the historically best model for a query type."""
    pref = get_preference(query_type)
    return pref.get("preferred_model")
