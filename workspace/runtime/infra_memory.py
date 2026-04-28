#!/usr/bin/env python3
"""
Infrastructure Memory — Long-term pattern detection for Tenazas.

Analyzes diagnostic logs, restart history, network events, VPN status,
and Ollama availability to detect recurring patterns.

Examples:
  - sonarr crashes nightly at 3AM
  - VPN disconnects every morning
  - network latency spikes at certain hours

Storage: workspace/runtime/infra_memory.json
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from runtime_utils import now
try:
    from .runtime_utils import load_json, save_json
except ImportError:
    from runtime_utils import load_json, save_json

BASE_DIR = Path(__file__).parent
MEMORY_FILE = BASE_DIR / "infra_memory.json"
MEMORY_LOG_FILE = BASE_DIR / "memory_log.json"
DIAGNOSTICS_LOG = BASE_DIR / "logs" / "diagnostics.log"
TELEMETRY_LOG = BASE_DIR / "logs" / "infra_memory.log"

# Config
MIN_OCCURRENCES = 3
LOOKBACK_DAYS = 14
CONFIDENCE_THRESHOLD = 0.5
MAX_PATTERNS = 50



def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Pattern Detection ────────────────────────────────────────────


def _analyze_memory_log() -> list:
    """Extract event patterns from runtime memory_log.json."""
    memory = load_json(MEMORY_LOG_FILE, [])
    if not isinstance(memory, list):
        return []

    cutoff = now() - timedelta(days=LOOKBACK_DAYS)
    # Strip timezone for comparison (fromisoformat may return naive or aware)
    cutoff_naive = cutoff.replace(tzinfo=None)

    # Count (event_type, target, hour_bucket) → occurrences + timestamps
    counts = defaultdict(lambda: {"count": 0, "timestamps": []})

    for entry in memory:
        try:
            ts = datetime.fromisoformat(entry["ts"])
            ts_naive = ts.replace(tzinfo=None)
        except (KeyError, ValueError):
            continue
        if ts_naive < cutoff_naive:
            continue

        for event in entry.get("events", []):
            etype = event.get("type", "")
            target = event.get("container", "")
            hour = ts.hour
            # Bucket hours into 3-hour windows for broader patterns
            hour_bucket = (hour // 3) * 3
            key = (etype, target, hour_bucket)
            counts[key]["count"] += 1
            counts[key]["timestamps"].append(ts.isoformat())

        for result in entry.get("results", []):
            if result.get("action") == "docker.restart" and result.get("ok"):
                target = result.get("target", "")
                hour = ts.hour
                hour_bucket = (hour // 3) * 3
                key = ("restart_executed", target, hour_bucket)
                counts[key]["count"] += 1
                counts[key]["timestamps"].append(ts.isoformat())

    return [
        {
            "event": k[0],
            "target": k[1],
            "hour_bucket": k[2],
            "count": v["count"],
            "first_seen": min(v["timestamps"]),
            "last_seen": max(v["timestamps"]),
        }
        for k, v in counts.items()
        if v["count"] >= MIN_OCCURRENCES
    ]


def _pattern_confidence(count: int, days: int = LOOKBACK_DAYS) -> float:
    """Calculate confidence based on frequency.

    Higher count relative to lookback period = higher confidence.
    """
    expected_per_day = count / max(days, 1)
    # Normalize: 1+ per day = high confidence
    conf = min(expected_per_day / 1.0, 1.0)
    return round(conf, 2)


def _recommend_action(event: str, target: str) -> str:
    """Generate recommended action for a pattern."""
    recommendations = {
        "container_down": f"check_logs_then_restart:{target}",
        "restart_executed": f"investigate_root_cause:{target}",
        "vpn_down": "check_gluetun_config_and_nordvpn",
        "vpn_leak": "restart_gluetun_check_country",
        "internet_lost": "check_isp_and_router",
        "ollama_notebook_down": "check_notebook_power_and_ollama_service",
    }
    return recommendations.get(event, f"investigate:{event}:{target}")


# ── Main API ─────────────────────────────────────────────────────


def detect_patterns() -> list:
    """Analyze logs and detect recurring infrastructure patterns.

    Returns list of pattern dicts, sorted by confidence.
    """
    raw = _analyze_memory_log()

    patterns = []
    for r in raw:
        conf = _pattern_confidence(r["count"])
        if conf < CONFIDENCE_THRESHOLD:
            continue

        pattern_id = f"{r['event']}_{r['target']}_{r['hour_bucket']}h"
        patterns.append({
            "pattern": pattern_id,
            "event": r["event"],
            "target": r["target"],
            "hour_bucket": r["hour_bucket"],
            "frequency": r["count"],
            "confidence": conf,
            "first_seen": r["first_seen"],
            "last_seen": r["last_seen"],
            "recommended_action": _recommend_action(r["event"], r["target"]),
        })

    patterns.sort(key=lambda p: p["confidence"], reverse=True)
    return patterns[:MAX_PATTERNS]


def update_memory():
    """Run pattern detection and save to infra_memory.json."""
    patterns = detect_patterns()
    existing = load_json(MEMORY_FILE, {"patterns": [], "last_analysis": ""})

    # Merge: keep patterns from existing that are still valid
    old_map = {p["pattern"]: p for p in existing.get("patterns", [])}
    for p in patterns:
        if p["pattern"] in old_map:
            # Preserve any manually added notes
            old = old_map[p["pattern"]]
            p["notes"] = old.get("notes", "")

    data = {
        "patterns": patterns,
        "last_analysis": now().isoformat(),
        "lookback_days": LOOKBACK_DAYS,
    }
    save_json(MEMORY_FILE, data)

    _log({
        "type": "pattern_detection",
        "patterns_found": len(patterns),
        "high_confidence": sum(1 for p in patterns if p["confidence"] >= 0.8),
    })

    return patterns


def get_patterns() -> list:
    """Load saved patterns from memory file."""
    data = load_json(MEMORY_FILE, {"patterns": []})
    return data.get("patterns", [])


def get_patterns_for_target(target: str) -> list:
    """Get patterns related to a specific target (container, service)."""
    return [p for p in get_patterns() if p["target"] == target]


def get_patterns_for_event(event: str) -> list:
    """Get patterns for a specific event type."""
    return [p for p in get_patterns() if p["event"] == event]


def get_active_patterns(min_confidence: float = 0.6) -> list:
    """Get high-confidence patterns currently active."""
    current_hour = now().hour
    current_bucket = (current_hour // 3) * 3
    return [
        p for p in get_patterns()
        if p["confidence"] >= min_confidence and p["hour_bucket"] == current_bucket
    ]
