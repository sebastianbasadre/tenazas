#!/usr/bin/env python3
"""
Adaptive Router — Learns optimal routing over time using metrics history.

Tracks: query_type → pipeline → success_rate and recommends the best
pipeline for each query type based on historical performance.

Updates recommendations using metrics from metrics_engine.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
METRICS_DIR = BASE_DIR / "metrics"
ROUTER_STATE = BASE_DIR / "adaptive_router_state.json"
TELEMETRY_LOG = BASE_DIR / "logs" / "adaptive_router.log"

MIN_SAMPLES = 3
ANALYSIS_DAYS = 7


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_metrics(days: int = ANALYSIS_DAYS) -> list:
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


def analyze_routes() -> dict:
    """Analyze routing performance from metrics.

    Returns: {query_type: {pipeline: {success_rate, avg_confidence, avg_latency, count}}}
    """
    entries = _load_metrics()
    queries = [e for e in entries if e.get("type") == "query"]

    stats = defaultdict(lambda: defaultdict(lambda: {
        "count": 0, "confidences": [], "latencies": [], "escalations": 0,
    }))

    for q in queries:
        qt = q.get("query_type", "unknown")
        pipeline = q.get("pipeline", "unknown")
        s = stats[qt][pipeline]
        s["count"] += 1
        if "confidence" in q:
            s["confidences"].append(q["confidence"])
        if "latency_ms" in q:
            s["latencies"].append(q["latency_ms"])
        if q.get("escalated"):
            s["escalations"] += 1

    result = {}
    for qt, pipelines in stats.items():
        result[qt] = {}
        for pipeline, s in pipelines.items():
            if s["count"] < MIN_SAMPLES:
                continue
            avg_conf = sum(s["confidences"]) / len(s["confidences"]) if s["confidences"] else 0
            avg_lat = sum(s["latencies"]) / len(s["latencies"]) if s["latencies"] else 0
            esc_rate = s["escalations"] / s["count"]
            # Success = high confidence + low escalation
            success_rate = avg_conf * (1 - esc_rate)

            result[qt][pipeline] = {
                "count": s["count"],
                "success_rate": round(success_rate, 4),
                "avg_confidence": round(avg_conf, 4),
                "avg_latency_ms": round(avg_lat, 1),
                "escalation_rate": round(esc_rate, 4),
            }

    return result


def get_best_pipeline(query_type: str) -> str | None:
    """Get the historically best pipeline for a query type."""
    routes = analyze_routes()
    pipelines = routes.get(query_type, {})
    if not pipelines:
        return None

    best = max(pipelines.items(), key=lambda x: x[1]["success_rate"])
    return best[0]


def update_recommendations() -> dict:
    """Run analysis and save recommendations."""
    routes = analyze_routes()

    recommendations = {}
    for qt, pipelines in routes.items():
        if not pipelines:
            continue
        sorted_pipes = sorted(pipelines.items(), key=lambda x: x[1]["success_rate"], reverse=True)
        recommendations[qt] = {
            "best_pipeline": sorted_pipes[0][0],
            "best_success_rate": sorted_pipes[0][1]["success_rate"],
            "alternatives": [p[0] for p in sorted_pipes[1:3]],
            "data": pipelines,
        }

    state = {
        "recommendations": recommendations,
        "last_update": _now().isoformat(),
        "analysis_days": ANALYSIS_DAYS,
    }

    ROUTER_STATE.parent.mkdir(parents=True, exist_ok=True)
    with open(ROUTER_STATE, "w") as f:
        json.dump(state, f, indent=2, default=str)

    _log({"type": "router_update", "query_types": len(recommendations)})
    return state


def get_recommendations() -> dict:
    """Load saved recommendations."""
    try:
        with open(ROUTER_STATE) as f:
            return json.load(f).get("recommendations", {})
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
