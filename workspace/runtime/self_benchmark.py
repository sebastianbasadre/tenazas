#!/usr/bin/env python3
"""
Self-Benchmark — Measures system performance across multiple dimensions.

Benchmarks:
- Model response quality (from confidence scores)
- Pipeline latency
- Diagnostic accuracy (from follow-up events)

Reads from metrics/ directory. Never modifies infrastructure.

Storage: workspace/runtime/benchmark_results.json
Log: workspace/runtime/logs/benchmark.log
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
METRICS_DIR = BASE_DIR / "metrics"
MEMORY_FILE = BASE_DIR / "memory_log.json"
BENCHMARK_FILE = BASE_DIR / "benchmark_results.json"
TELEMETRY_LOG = BASE_DIR / "logs" / "benchmark.log"

# Config
BENCHMARK_WINDOW_DAYS = 7
MIN_SAMPLES = 3


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


def _load_metrics(days: int = BENCHMARK_WINDOW_DAYS) -> list:
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


# ── Benchmark Functions ─────────────────────────────────────────


def _benchmark_model_quality(entries: list) -> dict:
    """Benchmark model response quality from confidence scores."""
    by_model = defaultdict(list)

    for e in entries:
        if e.get("type") != "query" or "confidence" not in e:
            continue
        model = e.get("model", "unknown")
        by_model[model].append(e["confidence"])

    results = {}
    for model, scores in by_model.items():
        if len(scores) < MIN_SAMPLES:
            continue

        scores_sorted = sorted(scores)
        results[model] = {
            "sample_count": len(scores),
            "avg_confidence": round(sum(scores) / len(scores), 4),
            "min_confidence": round(min(scores), 4),
            "max_confidence": round(max(scores), 4),
            "median_confidence": round(
                scores_sorted[len(scores_sorted) // 2], 4
            ),
            "low_confidence_rate": round(
                sum(1 for s in scores if s < 0.6) / len(scores), 4
            ),
        }

    return {
        "models_benchmarked": len(results),
        "results": results,
    }


def _benchmark_pipeline_latency(entries: list) -> dict:
    """Benchmark pipeline latency from query metrics."""
    by_pipeline = defaultdict(list)

    for e in entries:
        if e.get("type") != "query" or "latency_ms" not in e:
            continue
        pipeline = e.get("pipeline", "unknown")
        by_pipeline[pipeline].append(e["latency_ms"])

    results = {}
    for pipeline, latencies in by_pipeline.items():
        if len(latencies) < MIN_SAMPLES:
            continue

        latencies_sorted = sorted(latencies)
        p95_idx = int(len(latencies_sorted) * 0.95)

        results[pipeline] = {
            "sample_count": len(latencies),
            "avg_ms": round(sum(latencies) / len(latencies), 1),
            "min_ms": round(min(latencies), 1),
            "max_ms": round(max(latencies), 1),
            "median_ms": round(latencies_sorted[len(latencies_sorted) // 2], 1),
            "p95_ms": round(
                latencies_sorted[min(p95_idx, len(latencies_sorted) - 1)], 1
            ),
        }

    return {
        "pipelines_benchmarked": len(results),
        "results": results,
    }


def _benchmark_diagnostic_accuracy(entries: list) -> dict:
    """Benchmark diagnostic accuracy by checking follow-up events.

    If diagnostics detected an issue and the next run shows it resolved,
    the diagnostic was accurate. If it persists after action, less accurate.
    """
    memory = _load_json(MEMORY_FILE, [])
    if not isinstance(memory, list):
        memory = []

    # Count: issues that led to actions vs issues that recurred
    action_count = 0
    recurrence_count = 0
    total_issues = 0

    cutoff = _now() - timedelta(days=BENCHMARK_WINDOW_DAYS)

    for i, entry in enumerate(memory):
        try:
            ts = datetime.fromisoformat(entry["ts"])
        except (KeyError, ValueError):
            continue
        if ts < cutoff:
            continue

        events = entry.get("events", [])
        results = entry.get("results", [])

        if events:
            total_issues += len(events)

        if results:
            action_count += len(results)

        # Check if same event type appears in next entry (recurrence)
        if i + 1 < len(memory) and events:
            next_events = memory[i + 1].get("events", [])
            next_types = {e.get("type") for e in next_events}
            current_types = {e.get("type") for e in events}
            recurring = current_types & next_types
            recurrence_count += len(recurring)

    # Accuracy = issues that didn't recur / total issues
    resolved = total_issues - recurrence_count
    accuracy = round(resolved / max(total_issues, 1), 4)

    return {
        "total_issues_detected": total_issues,
        "actions_taken": action_count,
        "recurrences": recurrence_count,
        "resolved_without_recurrence": resolved,
        "accuracy": accuracy,
    }


def _benchmark_escalation_effectiveness(entries: list) -> dict:
    """Benchmark escalation effectiveness."""
    queries = [e for e in entries if e.get("type") == "query"]
    if not queries:
        return {"total_queries": 0, "escalated": 0, "escalation_rate": 0.0}

    escalated = [q for q in queries if q.get("escalated")]

    # Compare confidence of escalated vs non-escalated
    esc_conf = [q.get("confidence", 0) for q in escalated if "confidence" in q]
    non_esc_conf = [
        q.get("confidence", 0) for q in queries
        if not q.get("escalated") and "confidence" in q
    ]

    return {
        "total_queries": len(queries),
        "escalated": len(escalated),
        "escalation_rate": round(len(escalated) / len(queries), 4),
        "avg_confidence_escalated": round(
            sum(esc_conf) / len(esc_conf), 4
        ) if esc_conf else 0.0,
        "avg_confidence_normal": round(
            sum(non_esc_conf) / len(non_esc_conf), 4
        ) if non_esc_conf else 0.0,
    }


# ── Main API ────────────────────────────────────────────────────


def run_benchmark() -> dict:
    """Run all benchmarks and produce a comprehensive report.

    Returns a report dict with all benchmark results.
    """
    entries = _load_metrics()

    report = {
        "benchmark_date": _now().isoformat(),
        "window_days": BENCHMARK_WINDOW_DAYS,
        "total_metrics_entries": len(entries),
        "model_quality": _benchmark_model_quality(entries),
        "pipeline_latency": _benchmark_pipeline_latency(entries),
        "diagnostic_accuracy": _benchmark_diagnostic_accuracy(entries),
        "escalation_effectiveness": _benchmark_escalation_effectiveness(entries),
    }

    _save_json(BENCHMARK_FILE, report)

    _log({
        "type": "benchmark_run",
        "entries_analyzed": len(entries),
        "models_benchmarked": report["model_quality"]["models_benchmarked"],
        "pipelines_benchmarked": report["pipeline_latency"]["pipelines_benchmarked"],
        "diagnostic_accuracy": report["diagnostic_accuracy"]["accuracy"],
    })

    return report


def get_latest_results() -> dict:
    """Load the most recent benchmark results."""
    return _load_json(BENCHMARK_FILE, {})


def compare_benchmarks(current: dict, previous: dict) -> dict:
    """Compare two benchmark reports and highlight changes.

    Returns dict with improvements and regressions.
    """
    changes = {"improvements": [], "regressions": [], "unchanged": []}

    # Compare diagnostic accuracy
    curr_acc = current.get("diagnostic_accuracy", {}).get("accuracy", 0)
    prev_acc = previous.get("diagnostic_accuracy", {}).get("accuracy", 0)
    if curr_acc > prev_acc + 0.05:
        changes["improvements"].append(
            f"Diagnostic accuracy improved: {prev_acc:.2f} -> {curr_acc:.2f}"
        )
    elif curr_acc < prev_acc - 0.05:
        changes["regressions"].append(
            f"Diagnostic accuracy declined: {prev_acc:.2f} -> {curr_acc:.2f}"
        )

    # Compare escalation rate
    curr_esc = current.get("escalation_effectiveness", {}).get("escalation_rate", 0)
    prev_esc = previous.get("escalation_effectiveness", {}).get("escalation_rate", 0)
    if curr_esc < prev_esc - 0.05:
        changes["improvements"].append(
            f"Escalation rate decreased: {prev_esc:.2f} -> {curr_esc:.2f}"
        )
    elif curr_esc > prev_esc + 0.05:
        changes["regressions"].append(
            f"Escalation rate increased: {prev_esc:.2f} -> {curr_esc:.2f}"
        )

    return changes
