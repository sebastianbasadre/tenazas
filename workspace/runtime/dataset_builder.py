#!/usr/bin/env python3
"""
Dataset Builder — Creates datasets from operational data.

Sources: diagnostics logs, agent reasoning logs, metrics.
Builds structured entries: {input, expected_output, source, quality_score}.
Stores in datasets/ directory (one JSON per dataset type).

Safety: Read-only. Only reads existing logs and metrics.

Log: workspace/runtime/logs/dataset.log
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
DATASETS_DIR = BASE_DIR / "datasets"
LOGS_DIR = BASE_DIR / "logs"
METRICS_DIR = BASE_DIR / "metrics"
TELEMETRY_LOG = LOGS_DIR / "dataset.log"

# Config
MAX_ENTRIES_PER_DATASET = 500
MIN_QUALITY_SCORE = 0.3


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


def _load_log_lines(log_path: Path, max_lines: int = 1000) -> list:
    """Load entries from a JSON-line log file."""
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


def _load_metrics(days: int = 7) -> list:
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


# ── Quality Scoring ─────────────────────────────────────────────


def _score_diagnostic_entry(entry: dict) -> float:
    """Score quality of a diagnostic log entry for training."""
    score = 0.5  # Base score

    # Has structured issues with severity
    issues = entry.get("issues", [])
    if issues and all("severity" in i and "problem" in i for i in issues):
        score += 0.2

    # Has action context
    if entry.get("type") == "action_mapping":
        score += 0.1

    # Has clear event counts
    if "issues_count" in entry and "critical" in entry:
        score += 0.1

    # Penalize entries with no useful content
    if not issues and entry.get("issues_count", 0) == 0:
        score -= 0.2

    return round(min(max(score, 0.0), 1.0), 4)


def _score_reasoning_entry(entry: dict) -> float:
    """Score quality of a reasoning log entry for training."""
    score = 0.4  # Base score

    # Successful reasoning
    if entry.get("status") == "complete":
        score += 0.3

    # Multiple agents used
    agents = entry.get("agents_used", [])
    if len(agents) >= 2:
        score += 0.1
    if len(agents) >= 3:
        score += 0.1

    # No fallback = cleaner data
    if not entry.get("fallback_used"):
        score += 0.1

    return round(min(max(score, 0.0), 1.0), 4)


# ── Dataset Builders ────────────────────────────────────────────


def build_diagnostic_dataset() -> dict:
    """Build a dataset from diagnostic logs.

    Each entry: {input, expected_output, source, quality_score}
    Input = system state snapshot, Output = diagnostic issues found.

    Returns: {entries: list, count: int, avg_quality: float}
    """
    diag_log = LOGS_DIR / "diagnostics.log"
    lines = _load_log_lines(diag_log)

    entries = []
    for entry in lines:
        if entry.get("type") != "diagnostics":
            continue

        quality = _score_diagnostic_entry(entry)
        if quality < MIN_QUALITY_SCORE:
            continue

        issues = entry.get("issues", [])
        issues_text = "; ".join(
            f"[{i.get('severity', '?')}] {i.get('problem', '')}"
            for i in issues
        ) if issues else "No issues detected"

        dataset_entry = {
            "input": json.dumps({
                "issues_count": entry.get("issues_count", 0),
                "critical": entry.get("critical", 0),
                "warning": entry.get("warning", 0),
                "info": entry.get("info", 0),
            }),
            "expected_output": issues_text[:500],
            "source": "diagnostics.log",
            "quality_score": quality,
            "ts": entry.get("ts", ""),
        }
        entries.append(dataset_entry)

    entries = entries[-MAX_ENTRIES_PER_DATASET:]

    avg_quality = (
        sum(e["quality_score"] for e in entries) / len(entries) if entries else 0.0
    )

    result = {
        "dataset_type": "diagnostic",
        "entries": entries,
        "count": len(entries),
        "avg_quality": round(avg_quality, 4),
        "built_at": _now().isoformat(),
    }

    # Save to datasets directory
    _save_json(DATASETS_DIR / "diagnostic_dataset.json", result)

    _log({
        "type": "dataset_built",
        "dataset": "diagnostic",
        "entries": len(entries),
        "avg_quality": round(avg_quality, 4),
    })

    return result


def build_reasoning_dataset() -> dict:
    """Build a dataset from multi-agent reasoning logs.

    Each entry: {input, expected_output, source, quality_score}
    Input = query/problem, Output = reasoning conclusion.

    Returns: {entries: list, count: int, avg_quality: float}
    """
    reasoning_log = LOGS_DIR / "multi_agent.log"
    lines = _load_log_lines(reasoning_log)

    entries = []
    for entry in lines:
        if entry.get("type") != "reasoning":
            continue

        quality = _score_reasoning_entry(entry)
        if quality < MIN_QUALITY_SCORE:
            continue

        query = entry.get("query", "")
        if not query:
            continue

        dataset_entry = {
            "input": query[:300],
            "expected_output": json.dumps({
                "status": entry.get("status", ""),
                "agents_used": entry.get("agents_used", []),
                "fallback_used": entry.get("fallback_used", False),
            }),
            "source": "multi_agent.log",
            "quality_score": quality,
            "ts": entry.get("ts", ""),
        }
        entries.append(dataset_entry)

    entries = entries[-MAX_ENTRIES_PER_DATASET:]

    avg_quality = (
        sum(e["quality_score"] for e in entries) / len(entries) if entries else 0.0
    )

    result = {
        "dataset_type": "reasoning",
        "entries": entries,
        "count": len(entries),
        "avg_quality": round(avg_quality, 4),
        "built_at": _now().isoformat(),
    }

    _save_json(DATASETS_DIR / "reasoning_dataset.json", result)

    _log({
        "type": "dataset_built",
        "dataset": "reasoning",
        "entries": len(entries),
        "avg_quality": round(avg_quality, 4),
    })

    return result


def build_metrics_dataset() -> dict:
    """Build a dataset from operational metrics.

    Each entry: {input, expected_output, source, quality_score}
    Input = query metrics, Output = outcome summary.

    Returns: {entries: list, count: int, avg_quality: float}
    """
    metrics = _load_metrics(days=14)

    entries = []
    for m in metrics:
        if m.get("type") != "query":
            continue

        quality = 0.5
        if "confidence" in m and "latency_ms" in m:
            quality = 0.7
        if m.get("escalated"):
            quality += 0.1  # Escalation data is valuable

        if quality < MIN_QUALITY_SCORE:
            continue

        dataset_entry = {
            "input": json.dumps({
                "query_type": m.get("query_type", ""),
                "pipeline": m.get("pipeline", ""),
                "model": m.get("model", ""),
            }),
            "expected_output": json.dumps({
                "confidence": m.get("confidence", 0),
                "latency_ms": m.get("latency_ms", 0),
                "escalated": m.get("escalated", False),
                "actions_suggested": m.get("actions_suggested", 0),
            }),
            "source": "metrics",
            "quality_score": round(quality, 4),
            "ts": m.get("ts", ""),
        }
        entries.append(dataset_entry)

    entries = entries[-MAX_ENTRIES_PER_DATASET:]

    avg_quality = (
        sum(e["quality_score"] for e in entries) / len(entries) if entries else 0.0
    )

    result = {
        "dataset_type": "metrics",
        "entries": entries,
        "count": len(entries),
        "avg_quality": round(avg_quality, 4),
        "built_at": _now().isoformat(),
    }

    _save_json(DATASETS_DIR / "metrics_dataset.json", result)

    _log({
        "type": "dataset_built",
        "dataset": "metrics",
        "entries": len(entries),
        "avg_quality": round(avg_quality, 4),
    })

    return result


# ── Main API ────────────────────────────────────────────────────


def build_all_datasets() -> dict:
    """Build all dataset types.

    Returns summary of all datasets built.
    """
    results = {
        "diagnostic": build_diagnostic_dataset(),
        "reasoning": build_reasoning_dataset(),
        "metrics": build_metrics_dataset(),
    }

    total = sum(r["count"] for r in results.values())

    _log({
        "type": "build_all",
        "total_entries": total,
        "datasets": {k: v["count"] for k, v in results.items()},
    })

    return {
        "datasets": {k: {"count": v["count"], "avg_quality": v["avg_quality"]}
                     for k, v in results.items()},
        "total_entries": total,
        "built_at": _now().isoformat(),
    }


def list_datasets() -> list:
    """List available datasets in the datasets/ directory."""
    if not DATASETS_DIR.exists():
        return []

    datasets = []
    for path in sorted(DATASETS_DIR.glob("*_dataset.json")):
        try:
            data = _load_json(path, {})
            datasets.append({
                "name": path.stem,
                "type": data.get("dataset_type", "unknown"),
                "count": data.get("count", 0),
                "avg_quality": data.get("avg_quality", 0),
                "built_at": data.get("built_at", ""),
            })
        except Exception:
            continue

    return datasets
