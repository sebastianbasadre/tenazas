#!/usr/bin/env python3
"""
Predictive Engine — Infrastructure failure prediction for Tenazas runtime.

Reads patterns from patterns.json and event history from memory_log.json.
Uses simple heuristics: if an event happened N times at hour H, predict
it will happen again. Returns predictions with probability.

Stores predictions in predictions.json. Logs to logs/predictions.log.
"""

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from runtime_utils import load_json, save_json

BASE_DIR = Path(__file__).parent
MEMORY_FILE = BASE_DIR / "memory_log.json"
PATTERNS_FILE = BASE_DIR / "patterns.json"
PREDICTIONS_FILE = BASE_DIR / "predictions.json"
LOG_FILE = BASE_DIR / "logs" / "predictions.log"

# ── Config ────────────────────────────────────────────────────────

LOOKBACK_DAYS = 14
MIN_OCCURRENCES = 2
MAX_PREDICTIONS = 20
# Probability = min(occurrences / SATURATION_COUNT, MAX_PROBABILITY)
SATURATION_COUNT = 10
MAX_PROBABILITY = 0.90


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    """Append a JSON line to predictions.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _analyze_history() -> dict:
    """
    Analyze memory_log.json to find recurring event patterns.

    Returns:
        dict mapping (event_type, target, hour) -> occurrence count
    """
    memory = load_json(MEMORY_FILE, [])
    if not isinstance(memory, list):
        return {}

    cutoff = _now() - timedelta(days=LOOKBACK_DAYS)
    counts = defaultdict(int)

    for entry in memory:
        try:
            entry_dt = datetime.fromisoformat(entry["ts"])
        except (KeyError, ValueError, TypeError):
            continue
        if entry_dt < cutoff:
            continue

        for event in entry.get("events", []):
            etype = event.get("type", "")
            target = event.get("container", "")
            hour = entry_dt.hour
            counts[(etype, target, hour)] += 1

    return dict(counts)


def _analyze_patterns() -> list:
    """
    Read existing patterns.json for known recurring patterns.

    Returns:
        List of pattern dicts
    """
    data = load_json(PATTERNS_FILE, {"patterns": []})
    return data.get("patterns", [])


def generate_predictions() -> list:
    """
    Generate predictions for the next 24 hours based on history and patterns.

    Returns:
        List of prediction dicts with: event, target, hour, probability, basis
    """
    history_counts = _analyze_history()
    known_patterns = _analyze_patterns()
    predictions = []
    seen = set()

    # From history analysis
    for (etype, target, hour), count in history_counts.items():
        if count < MIN_OCCURRENCES:
            continue

        key = (etype, target, hour)
        if key in seen:
            continue
        seen.add(key)

        probability = min(count / SATURATION_COUNT, MAX_PROBABILITY)

        predictions.append({
            "event": etype,
            "target": target,
            "hour": hour,
            "probability": round(probability, 2),
            "occurrences": count,
            "basis": "history",
        })

    # From pattern engine (supplement with pattern data)
    for pattern in known_patterns:
        key = (pattern.get("event", ""), pattern.get("target", ""), pattern.get("hour", -1))
        if key in seen:
            # Update probability if pattern has more data
            for p in predictions:
                if (p["event"], p["target"], p["hour"]) == key:
                    occ = pattern.get("occurrences", 0)
                    new_prob = min(occ / SATURATION_COUNT, MAX_PROBABILITY)
                    if new_prob > p["probability"]:
                        p["probability"] = round(new_prob, 2)
                        p["occurrences"] = occ
                        p["basis"] = "history+pattern"
                    break
            continue

        occ = pattern.get("occurrences", 0)
        if occ < MIN_OCCURRENCES:
            continue

        seen.add(key)
        predictions.append({
            "event": pattern.get("event", ""),
            "target": pattern.get("target", ""),
            "hour": pattern.get("hour", -1),
            "probability": round(min(occ / SATURATION_COUNT, MAX_PROBABILITY), 2),
            "occurrences": occ,
            "basis": "pattern",
        })

    # Sort by probability desc, limit
    predictions.sort(key=lambda p: p["probability"], reverse=True)
    predictions = predictions[:MAX_PREDICTIONS]

    # Save and log
    result = {
        "ts": _now().isoformat(),
        "lookback_days": LOOKBACK_DAYS,
        "predictions": predictions,
    }
    save_json(PREDICTIONS_FILE, result)

    _log({
        "ts": _now().isoformat(),
        "action": "generate_predictions",
        "count": len(predictions),
    })

    return predictions


def get_predictions_for_hour(hour: int) -> list:
    """
    Get predictions for a specific hour.

    Args:
        hour: Hour of day (0-23)

    Returns:
        List of prediction dicts matching that hour
    """
    data = load_json(PREDICTIONS_FILE, {"predictions": []})
    return [p for p in data.get("predictions", []) if p.get("hour") == hour]


def get_high_risk_predictions(threshold: float = 0.5) -> list:
    """
    Get predictions above a probability threshold.

    Args:
        threshold: Minimum probability (0.0-1.0)

    Returns:
        List of prediction dicts above threshold
    """
    data = load_json(PREDICTIONS_FILE, {"predictions": []})
    return [p for p in data.get("predictions", []) if p.get("probability", 0) >= threshold]


def predict_next_hours(hours_ahead: int = 6) -> list:
    """
    Get predictions for the next N hours from now.

    Args:
        hours_ahead: How many hours to look ahead

    Returns:
        List of prediction dicts for the upcoming hours
    """
    current_hour = _now().hour
    target_hours = [(current_hour + h) % 24 for h in range(1, hours_ahead + 1)]

    data = load_json(PREDICTIONS_FILE, {"predictions": []})
    return [p for p in data.get("predictions", []) if p.get("hour") in target_hours]
