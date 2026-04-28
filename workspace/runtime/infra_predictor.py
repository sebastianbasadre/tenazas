#!/usr/bin/env python3
"""
Infra Predictor — Predict problems before they happen.

Sources: decision_quality history, Prometheus metrics, device_inventory.
Predictions: container_failure_risk, vram_saturation, ram_leak,
             ollama_degradation, disk_exhaustion.

Never executes — only predicts and suggests.

Usage:
    python3 infra_predictor.py --predict    # run all predictions
    python3 infra_predictor.py --json       # JSON output
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
try:
    from .runtime_utils import load_json
except ImportError:
    from runtime_utils import load_json
from urllib.error import URLError
from urllib.request import Request, urlopen
from runtime_utils import now

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
LOG_FILE = BASE_DIR / "logs" / "infra_predictor.log"
PROMETHEUS_URL = "http://localhost:9090"
PREDICTIONS_FILE = STATE_DIR / "predictions.json"

MIN_PROBABILITY = 0.7  # minimum to notify



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def _query_prometheus(query: str, timeout: int = 5) -> list:
    """Query Prometheus API. Returns list of result dicts."""
    try:
        url = f"{PROMETHEUS_URL}/api/v1/query?query={query}"
        resp = urlopen(Request(url), timeout=timeout)
        data = json.loads(resp.read())
        if data.get("status") == "success":
            return data.get("data", {}).get("result", [])
    except (URLError, json.JSONDecodeError, OSError):
        pass
    return []


def _query_prometheus_range(query: str, hours: int = 3, step: str = "5m") -> list:
    """Query Prometheus range API for trend analysis."""
    try:
        end = int(time.time())
        start = end - hours * 3600
        url = (f"{PROMETHEUS_URL}/api/v1/query_range?"
               f"query={query}&start={start}&end={end}&step={step}")
        resp = urlopen(Request(url), timeout=10)
        data = json.loads(resp.read())
        if data.get("status") == "success":
            return data.get("data", {}).get("result", [])
    except (URLError, json.JSONDecodeError, OSError):
        pass
    return []


def _linear_slope(values: list) -> float:
    """Simple linear regression slope from [(timestamp, value)] pairs."""
    if len(values) < 3:
        return 0.0
    n = len(values)
    xs = [v[0] for v in values]
    ys = [v[1] for v in values]
    x_mean = sum(xs) / n
    y_mean = sum(ys) / n
    num = sum((x - x_mean) * (y - y_mean) for x, y in zip(xs, ys))
    den = sum((x - x_mean) ** 2 for x in xs)
    if den == 0:
        return 0.0
    return num / den


# ── Predictors ──────────────────────────────────────────────────


def predict_container_failure_risk() -> dict | None:
    """Container with restarts > 3 in 24h → high risk."""
    results = _query_prometheus("container_restarts_total")
    if not results:
        # Fallback: check decision_quality for repeated restart actions
        dq_dir = STATE_DIR / "decision_quality"
        if dq_dir.exists():
            today = now().strftime("%Y-%m-%d")
            dq_file = dq_dir / f"{today}.json"
            data = load_json(dq_file, [])
            restart_counts = {}
            for d in data:
                if d.get("action") == "restart_container":
                    t = d.get("target", "unknown")
                    restart_counts[t] = restart_counts.get(t, 0) + 1
            for target, count in restart_counts.items():
                if count >= 3:
                    return {
                        "type": "container_failure_risk",
                        "probability": min(0.5 + count * 0.1, 0.95),
                        "eta_minutes": 60,
                        "evidence": [f"{target}: {count} restarts today"],
                        "recommended_action": f"investigate {target} root cause",
                        "severity": "warning",
                        "target": target,
                    }
        return None

    for r in results:
        value = float(r.get("value", [0, 0])[1])
        target = r.get("metric", {}).get("container", "unknown")
        if value >= 3:
            return {
                "type": "container_failure_risk",
                "probability": min(0.5 + value * 0.1, 0.95),
                "eta_minutes": 60,
                "evidence": [f"{target}: {int(value)} restarts in 24h"],
                "recommended_action": f"investigate {target} root cause",
                "severity": "warning",
                "target": target,
            }
    return None


def predict_vram_saturation() -> dict | None:
    """VRAM trending towards 100% based on slope."""
    results = _query_prometheus_range(
        "ollama_gpu_vram_used_bytes / ollama_gpu_vram_total_bytes * 100", hours=1)
    if not results:
        return None

    for r in results:
        values = [(float(v[0]), float(v[1])) for v in r.get("values", [])]
        if len(values) < 3:
            continue
        current = values[-1][1]
        slope_per_min = _linear_slope(values) * 60  # per minute
        if slope_per_min > 0 and current > 80:
            remaining = (100 - current) / slope_per_min if slope_per_min > 0 else 999
            if remaining < 30:
                return {
                    "type": "vram_saturation",
                    "probability": min(0.6 + (current / 100) * 0.3, 0.95),
                    "eta_minutes": round(remaining, 1),
                    "evidence": [f"current: {current:.1f}%", f"trend: +{slope_per_min:.2f}%/min"],
                    "recommended_action": "gpu_scheduler --optimize",
                    "severity": "warning",
                }
    return None


def predict_ram_leak() -> dict | None:
    """NAS RAM growing > 2% per hour for 3h straight."""
    results = _query_prometheus_range(
        "nas_ram_used_bytes / nas_ram_total_bytes * 100", hours=3)
    if not results:
        return None

    for r in results:
        values = [(float(v[0]), float(v[1])) for v in r.get("values", [])]
        if len(values) < 6:
            continue
        slope_per_hour = _linear_slope(values) * 3600
        current = values[-1][1]
        if slope_per_hour > 2.0 and current > 70:
            eta = (95 - current) / (slope_per_hour / 60) if slope_per_hour > 0 else 999
            return {
                "type": "ram_leak",
                "probability": min(0.6 + slope_per_hour * 0.05, 0.95),
                "eta_minutes": round(eta, 1),
                "evidence": [f"current: {current:.1f}%", f"growth: +{slope_per_hour:.1f}%/hr for 3h"],
                "recommended_action": "identify memory-heavy container and restart",
                "severity": "warning",
            }
    return None


def predict_ollama_degradation() -> dict | None:
    """Ollama latency increasing progressively."""
    results = _query_prometheus_range(
        "ollama_gpu_latency_seconds", hours=2)
    if not results:
        return None

    for r in results:
        values = [(float(v[0]), float(v[1])) for v in r.get("values", [])]
        if len(values) < 5:
            continue
        slope_per_min = _linear_slope(values) * 60
        current = values[-1][1]
        if slope_per_min > 0.01 and current > 1.0:  # latency > 1s and growing
            return {
                "type": "ollama_degradation",
                "probability": min(0.6 + current * 0.1, 0.95),
                "eta_minutes": round((5.0 - current) / slope_per_min, 1) if slope_per_min > 0 else 999,
                "evidence": [f"latency: {current:.2f}s", f"trend: +{slope_per_min:.3f}s/min"],
                "recommended_action": "check VRAM, restart Ollama if needed",
                "severity": "warning",
            }
    return None


def predict_disk_exhaustion() -> dict | None:
    """NAS disk filling, project time to 90%."""
    results = _query_prometheus_range(
        "nas_disk_used_bytes / nas_disk_total_bytes * 100", hours=24, step="1h")
    if not results:
        return None

    for r in results:
        values = [(float(v[0]), float(v[1])) for v in r.get("values", [])]
        if len(values) < 3:
            continue
        slope_per_hour = _linear_slope(values) * 3600
        current = values[-1][1]
        if slope_per_hour > 0.1 and current > 75:
            eta_hours = (90 - current) / slope_per_hour if slope_per_hour > 0 else 999
            if eta_hours < 48:
                return {
                    "type": "disk_exhaustion",
                    "probability": min(0.5 + current / 200, 0.95),
                    "eta_minutes": round(eta_hours * 60, 0),
                    "evidence": [f"current: {current:.1f}%", f"growth: +{slope_per_hour:.2f}%/hr"],
                    "recommended_action": "cleanup logs and old media",
                    "severity": "warning",
                }
    return None


# ── Main API ────────────────────────────────────────────────────


def predict_all() -> list:
    """Run all predictors. Returns list of predictions above threshold."""
    predictors = [
        predict_container_failure_risk,
        predict_vram_saturation,
        predict_ram_leak,
        predict_ollama_degradation,
        predict_disk_exhaustion,
    ]

    predictions = []
    for pred in predictors:
        try:
            result = pred()
            if result and result.get("probability", 0) >= MIN_PROBABILITY:
                result["predicted_at"] = now().isoformat()
                predictions.append(result)
        except Exception as e:
            _log({"action": "predictor_error", "predictor": pred.__name__,
                  "error": str(e)[:200]})

    # Save predictions
    PREDICTIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(PREDICTIONS_FILE, "w") as f:
        json.dump(predictions, f, indent=2, default=str)

    _log({"action": "predict_complete", "count": len(predictions)})
    return predictions


def get_predictions() -> list:
    """Load last predictions."""
    return load_json(PREDICTIONS_FILE, [])


def format_predictions(predictions: list) -> str:
    if not predictions:
        return "No predictions above threshold."
    lines = [f"Predictions ({len(predictions)}):"]
    for p in predictions:
        lines.append(f"  [{p['severity']}] {p['type']}: {p['probability']:.0%} "
                     f"(ETA {p['eta_minutes']}min)")
        lines.append(f"    → {p['recommended_action']}")
    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Infra Predictor")
    parser.add_argument("--predict", action="store_true", help="Run predictions")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    if args.predict or args.json:
        preds = predict_all()
        if args.json:
            print(json.dumps(preds, indent=2, default=str))
        else:
            print(format_predictions(preds))
    else:
        parser.print_help()
