#!/usr/bin/env python3
"""
Decision Trace — Structured log of every reasoning decision made by Tenazas.

Records the full decision chain: classification → pipeline → model → confidence
→ actions → escalation. Enables post-hoc analysis and debugging.

Storage: runtime/logs/decisions.jsonl (append-only, one JSON per line)
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
DECISIONS_LOG = BASE_DIR / "logs" / "decisions.jsonl"
MAX_LOG_SIZE_MB = 10


def _now():
    return datetime.now().astimezone()


def _ensure_log():
    DECISIONS_LOG.parent.mkdir(parents=True, exist_ok=True)
    # Rotate if too large
    if DECISIONS_LOG.exists() and DECISIONS_LOG.stat().st_size > MAX_LOG_SIZE_MB * 1024 * 1024:
        rotated = DECISIONS_LOG.with_suffix(".jsonl.old")
        if rotated.exists():
            rotated.unlink()
        DECISIONS_LOG.rename(rotated)


def trace(
    query: str,
    classification: str = "",
    pipeline: str = "",
    model: str = "",
    confidence: float = 0.0,
    actions: list | None = None,
    latency_ms: float = 0.0,
    escalated: bool = False,
    escalation_reason: str = "",
    context_tokens: int = 0,
    metadata: dict | None = None,
) -> dict:
    """Record a complete decision trace."""
    _ensure_log()

    entry = {
        "ts": _now().isoformat(),
        "query": query[:200],
        "classification": classification,
        "pipeline": pipeline,
        "model": model,
        "confidence": round(confidence, 4),
        "actions_proposed": len(actions) if actions else 0,
        "actions": [a.get("action", "") if isinstance(a, dict) else str(a)
                    for a in (actions or [])][:5],
        "latency_ms": round(latency_ms, 1),
        "escalated": escalated,
        "escalation_reason": escalation_reason,
        "context_tokens": context_tokens,
    }
    if metadata:
        entry["metadata"] = metadata

    with open(DECISIONS_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")

    return entry


def get_recent(limit: int = 50) -> list:
    """Get the most recent decision traces."""
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


def get_by_classification(classification: str, limit: int = 20) -> list:
    """Filter traces by classification type."""
    return [t for t in get_recent(200) if t.get("classification") == classification][:limit]


def get_escalations(limit: int = 20) -> list:
    """Get traces where escalation occurred."""
    return [t for t in get_recent(200) if t.get("escalated")][:limit]


def summary(hours: int = 24) -> dict:
    """Summarize decision traces from the last N hours."""
    cutoff = _now() - timedelta(hours=hours)
    recent = get_recent(500)
    filtered = []
    for t in recent:
        try:
            ts = datetime.fromisoformat(t["ts"])
            if ts >= cutoff:
                filtered.append(t)
        except (KeyError, ValueError):
            continue

    if not filtered:
        return {"total": 0, "period_hours": hours}

    confidences = [t["confidence"] for t in filtered if t.get("confidence")]
    latencies = [t["latency_ms"] for t in filtered if t.get("latency_ms")]

    return {
        "total": len(filtered),
        "period_hours": hours,
        "avg_confidence": round(sum(confidences) / len(confidences), 4) if confidences else 0,
        "avg_latency_ms": round(sum(latencies) / len(latencies), 1) if latencies else 0,
        "escalations": sum(1 for t in filtered if t.get("escalated")),
        "classifications": {c: sum(1 for t in filtered if t.get("classification") == c)
                           for c in set(t.get("classification", "") for t in filtered)},
    }
