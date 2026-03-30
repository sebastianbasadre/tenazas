#!/usr/bin/env python3
"""
Metrics Engine — Operational metrics collection and aggregation for Tenazas.

Records: routing decisions, confidence scores, pipeline usage, model stats,
diagnostics events, suggested actions, query latency, reasoning outcomes.

Storage: workspace/runtime/metrics/ (JSON, one file per day)
"""

import json
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
METRICS_DIR = BASE_DIR / "metrics"
METRICS_LOG = BASE_DIR / "logs" / "metrics.log"


def _now():
    return datetime.now().astimezone()


def _today_file() -> Path:
    METRICS_DIR.mkdir(parents=True, exist_ok=True)
    return METRICS_DIR / f"{_now().strftime('%Y-%m-%d')}.json"


def _load_day(path: Path) -> list:
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_day(path: Path, entries: list):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(entries, f, indent=None, default=str)


def _log(entry: dict):
    METRICS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(METRICS_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Recording ────────────────────────────────────────────────────


def record(event_type: str, data: dict):
    """Record a metric event.

    Args:
        event_type: routing, confidence, pipeline, model, diagnostic,
                    action, latency, reasoning, query
        data: event-specific payload
    """
    entry = {
        "ts": _now().isoformat(),
        "type": event_type,
        **data,
    }

    path = _today_file()
    entries = _load_day(path)
    entries.append(entry)
    _save_day(path, entries)


def record_query(query: str, classification: str, pipeline: str,
                 model: str, confidence: float, latency_ms: float,
                 actions: list | None = None, escalated: bool = False):
    """Convenience: record a full query lifecycle."""
    record("query", {
        "query_type": classification,
        "pipeline": pipeline,
        "model": model,
        "confidence": round(confidence, 4),
        "latency_ms": round(latency_ms, 1),
        "actions_suggested": len(actions) if actions else 0,
        "escalated": escalated,
    })


def record_diagnostic(issues_count: int, critical: int, actions: list):
    record("diagnostic", {
        "issues_count": issues_count,
        "critical": critical,
        "actions_suggested": len(actions),
    })


def record_reasoning(agents_used: list, outcome: str, confidence: float):
    record("reasoning", {
        "agents": agents_used,
        "outcome": outcome,
        "confidence": round(confidence, 4),
    })


# ── Timer Context Manager ───────────────────────────────────────


class Timer:
    """Context manager to measure latency.

    Usage:
        with Timer() as t:
            do_work()
        print(t.ms)
    """

    def __enter__(self):
        self.start = time.monotonic()
        self.ms = 0
        return self

    def __exit__(self, *args):
        self.ms = round((time.monotonic() - self.start) * 1000, 1)


# ── Aggregation ──────────────────────────────────────────────────


def _load_range(days: int = 7) -> list:
    """Load metrics from the last N days."""
    entries = []
    for i in range(days):
        day = _now() - timedelta(days=i)
        path = METRICS_DIR / f"{day.strftime('%Y-%m-%d')}.json"
        entries.extend(_load_day(path))
    return entries


def average_confidence(days: int = 7) -> float:
    """Average confidence score across queries."""
    entries = _load_range(days)
    scores = [e["confidence"] for e in entries if "confidence" in e]
    return round(sum(scores) / len(scores), 4) if scores else 0.0


def pipeline_distribution(days: int = 7) -> dict:
    """Count of queries per pipeline."""
    entries = _load_range(days)
    counter = Counter(e.get("pipeline", "unknown")
                      for e in entries if e.get("type") == "query")
    return dict(counter.most_common())


def model_distribution(days: int = 7) -> dict:
    """Count of queries per model."""
    entries = _load_range(days)
    counter = Counter(e.get("model", "unknown")
                      for e in entries if e.get("type") == "query")
    return dict(counter.most_common())


def diagnostics_frequency(days: int = 7) -> dict:
    """Diagnostic event counts."""
    entries = _load_range(days)
    diags = [e for e in entries if e.get("type") == "diagnostic"]
    return {
        "total": len(diags),
        "critical_total": sum(e.get("critical", 0) for e in diags),
        "avg_issues": round(
            sum(e.get("issues_count", 0) for e in diags) / max(len(diags), 1), 1
        ),
    }


def latency_stats(days: int = 7) -> dict:
    """Response latency statistics (ms)."""
    entries = _load_range(days)
    latencies = [e["latency_ms"] for e in entries
                 if e.get("type") == "query" and "latency_ms" in e]
    if not latencies:
        return {"min": 0, "max": 0, "avg": 0, "p95": 0, "count": 0}

    latencies.sort()
    p95_idx = int(len(latencies) * 0.95)
    return {
        "min": round(min(latencies), 1),
        "max": round(max(latencies), 1),
        "avg": round(sum(latencies) / len(latencies), 1),
        "p95": round(latencies[min(p95_idx, len(latencies) - 1)], 1),
        "count": len(latencies),
    }


def escalation_rate(days: int = 7) -> float:
    """Percentage of queries that required escalation."""
    entries = _load_range(days)
    queries = [e for e in entries if e.get("type") == "query"]
    if not queries:
        return 0.0
    escalated = sum(1 for e in queries if e.get("escalated"))
    return round(escalated / len(queries), 4)


def summary(days: int = 7) -> dict:
    """Full metrics summary."""
    return {
        "period_days": days,
        "avg_confidence": average_confidence(days),
        "pipeline_distribution": pipeline_distribution(days),
        "model_distribution": model_distribution(days),
        "diagnostics": diagnostics_frequency(days),
        "latency": latency_stats(days),
        "escalation_rate": escalation_rate(days),
    }
