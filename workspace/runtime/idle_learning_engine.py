#!/usr/bin/env python3
"""
Idle Learning Engine — Analyzes operational data during idle periods.

Reads recent metrics, log patterns, and confidence trends to produce
optimization suggestions. Never modifies anything automatically.

Storage: workspace/runtime/idle_learnings.json
Log: workspace/runtime/logs/idle_learning.log
"""

import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta
from pathlib import Path
try:
    from .runtime_utils import load_json, save_json
except ImportError:
    from runtime_utils import load_json, save_json

BASE_DIR = Path(__file__).parent
METRICS_DIR = BASE_DIR / "metrics"
LOGS_DIR = BASE_DIR / "logs"
LEARNINGS_FILE = BASE_DIR / "idle_learnings.json"
TELEMETRY_LOG = LOGS_DIR / "idle_learning.log"

# Config
ANALYSIS_WINDOW_DAYS = 7
MIN_SAMPLES_FOR_SUGGESTION = 3
MAX_SUGGESTIONS = 20


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


def _load_log_lines(log_path: Path, max_lines: int = 500) -> list:
    """Load recent lines from a JSON-line log file."""
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


# ── Analysis Functions ──────────────────────────────────────────


def _analyze_metrics(entries: list) -> list:
    """Analyze metrics for optimization opportunities."""
    suggestions = []

    # Check confidence trends
    confidences = [e.get("confidence", 0) for e in entries if "confidence" in e]
    if len(confidences) >= MIN_SAMPLES_FOR_SUGGESTION:
        avg = sum(confidences) / len(confidences)
        if avg < 0.6:
            suggestions.append({
                "type": "confidence_low",
                "description": f"Confianza promedio baja ({avg:.2f}) en los ultimos {ANALYSIS_WINDOW_DAYS} dias",
                "recommendation": "Considerar re-indexar RAG o expandir retrieval",
                "metric": round(avg, 4),
                "priority": "high",
            })

    # Check escalation frequency
    queries = [e for e in entries if e.get("type") == "query"]
    if len(queries) >= MIN_SAMPLES_FOR_SUGGESTION:
        escalated = sum(1 for q in queries if q.get("escalated"))
        rate = escalated / len(queries)
        if rate > 0.3:
            suggestions.append({
                "type": "high_escalation",
                "description": f"Tasa de escalacion alta ({rate:.0%}) — {escalated}/{len(queries)} queries",
                "recommendation": "Revisar pipeline routing y umbrales de confianza",
                "metric": round(rate, 4),
                "priority": "medium",
            })

    # Check latency outliers
    latencies = [e.get("latency_ms", 0) for e in entries
                 if e.get("type") == "query" and "latency_ms" in e]
    if len(latencies) >= MIN_SAMPLES_FOR_SUGGESTION:
        avg_lat = sum(latencies) / len(latencies)
        slow = sum(1 for l in latencies if l > avg_lat * 2)
        if slow > len(latencies) * 0.2:
            suggestions.append({
                "type": "latency_outliers",
                "description": f"{slow} queries con latencia >2x promedio ({avg_lat:.0f}ms)",
                "recommendation": "Investigar queries lentas — posible timeout de modelo",
                "metric": round(avg_lat, 1),
                "priority": "medium",
            })

    # Check model distribution imbalance
    models = Counter(e.get("model", "unknown") for e in entries if e.get("type") == "query")
    if models and len(models) > 1:
        total = sum(models.values())
        most_common_name, most_common_count = models.most_common(1)[0]
        if most_common_count / total > 0.9:
            suggestions.append({
                "type": "model_imbalance",
                "description": f"Modelo {most_common_name} maneja {most_common_count / total:.0%} del trafico",
                "recommendation": "Verificar si otros modelos estan disponibles y se estan usando",
                "metric": round(most_common_count / total, 4),
                "priority": "low",
            })

    return suggestions


def _analyze_log_patterns() -> list:
    """Analyze log files for recurring error patterns."""
    suggestions = []

    # Check executor log for repeated failures
    executor_log = LOGS_DIR / "executor.log"
    lines = _load_log_lines(executor_log)
    if lines:
        failures = [l for l in lines if not l.get("ok", True)]
        if len(failures) >= MIN_SAMPLES_FOR_SUGGESTION:
            # Group by action+target
            fail_groups = Counter(
                (l.get("action", ""), l.get("target", "")) for l in failures
            )
            for (action, target), count in fail_groups.most_common(3):
                if count >= MIN_SAMPLES_FOR_SUGGESTION:
                    suggestions.append({
                        "type": "recurring_failure",
                        "description": f"Fallo recurrente: {action} en {target} ({count} veces)",
                        "recommendation": f"Revisar logs de {target} y considerar runbook dedicado",
                        "metric": count,
                        "priority": "high",
                    })

    # Check diagnostics log for repeated issues
    diag_log = LOGS_DIR / "diagnostics.log"
    diag_lines = _load_log_lines(diag_log)
    if diag_lines:
        critical_counts = Counter()
        for entry in diag_lines:
            for issue in entry.get("issues", []):
                if issue.get("severity") == "critical":
                    critical_counts[issue.get("problem", "unknown")] += 1
        for problem, count in critical_counts.most_common(3):
            if count >= MIN_SAMPLES_FOR_SUGGESTION:
                suggestions.append({
                    "type": "recurring_critical",
                    "description": f"Problema critico recurrente: {problem} ({count} veces)",
                    "recommendation": "Investigar causa raiz y crear runbook si no existe",
                    "metric": count,
                    "priority": "high",
                })

    return suggestions


def _analyze_confidence_trends(entries: list) -> list:
    """Detect confidence score trends over time."""
    suggestions = []

    # Group by day
    by_day = defaultdict(list)
    for e in entries:
        if "confidence" not in e or "ts" not in e:
            continue
        try:
            day = datetime.fromisoformat(e["ts"]).strftime("%Y-%m-%d")
            by_day[day].append(e["confidence"])
        except (ValueError, TypeError):
            continue

    if len(by_day) < 3:
        return suggestions

    # Calculate daily averages sorted by date
    daily_avgs = sorted(
        [(day, sum(scores) / len(scores)) for day, scores in by_day.items()],
        key=lambda x: x[0],
    )

    # Check for declining trend (last 3 days lower than first 3)
    if len(daily_avgs) >= 6:
        first_half = sum(a[1] for a in daily_avgs[:3]) / 3
        last_half = sum(a[1] for a in daily_avgs[-3:]) / 3
        decline = first_half - last_half
        if decline > 0.1:
            suggestions.append({
                "type": "confidence_declining",
                "description": f"Confianza en declive: {first_half:.2f} -> {last_half:.2f} (delta: {decline:.2f})",
                "recommendation": "Re-indexar RAG, verificar calidad de embeddings",
                "metric": round(decline, 4),
                "priority": "high",
            })

    return suggestions


# ── Main API ─────────────────────────────────────────────────────


def analyze_idle() -> dict:
    """Run idle-time analysis on metrics, logs, and confidence trends.

    Returns analysis result with suggestions list.
    """
    entries = _load_metrics()
    suggestions = []

    suggestions.extend(_analyze_metrics(entries))
    suggestions.extend(_analyze_log_patterns())
    suggestions.extend(_analyze_confidence_trends(entries))

    # Deduplicate by type
    seen = set()
    unique = []
    for s in suggestions:
        key = (s["type"], s.get("description", ""))
        if key not in seen:
            seen.add(key)
            unique.append(s)

    unique.sort(key=lambda s: {"high": 0, "medium": 1, "low": 2}.get(s["priority"], 9))
    unique = unique[:MAX_SUGGESTIONS]

    result = {
        "analyzed_at": _now().isoformat(),
        "metrics_entries_analyzed": len(entries),
        "suggestions": unique,
        "analysis_window_days": ANALYSIS_WINDOW_DAYS,
    }

    save_json(LEARNINGS_FILE, result)

    _log({
        "type": "idle_analysis",
        "metrics_analyzed": len(entries),
        "suggestions_produced": len(unique),
        "high_priority": sum(1 for s in unique if s["priority"] == "high"),
    })

    # REM dreaming — memory consolidation in idle
    try:
        from dreaming import run_rem_session
        rem = run_rem_session()
        if rem.get("new_truths", 0) > 0:
            _log({"type": "rem_dreaming", "new_truths": rem["new_truths"]})
    except Exception as e:
        _log({"type": "rem_dreaming_skip", "error": str(e)})

    return result


def get_suggestions() -> list:
    """Load saved suggestions from idle_learnings.json."""
    data = load_json(LEARNINGS_FILE, {"suggestions": []})
    return data.get("suggestions", [])
