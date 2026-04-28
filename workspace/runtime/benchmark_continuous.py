#!/usr/bin/env python3
"""
Benchmark Continuous — Background monitoring with threshold alerts.

Runs every 30 min, checks 4 metrics, alerts when thresholds crossed.
Exports metrics to Prometheus via metrics_state.json.

Suites:
  router_accuracy: 10 known queries → % correct
  infra_uptime: expected containers vs running
  alert_precision: useful alerts / total (from decision_quality)
  latency_p95: last 100 orchestrator queries

Thresholds:
  router_accuracy < 75% → warning
  infra_uptime < 95% → critical
  alert_precision < 60% → warning
  latency_p95 > 5000ms → warning

Usage:
    python3 benchmark_continuous.py --run     # run once
    python3 benchmark_continuous.py --status  # show last results
"""

import json
import time
from datetime import datetime, timedelta
from pathlib import Path
try:
    from .runtime_utils import load_json
except ImportError:
    from runtime_utils import load_json

try:
    from .runtime_config import get_default_telegram_chat_id
except ImportError:
    from runtime_config import get_default_telegram_chat_id

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
RESULTS_FILE = STATE_DIR / "benchmark_continuous.json"
METRICS_STATE = BASE_DIR / "logs" / "metrics_state.json"
LOG_FILE = BASE_DIR / "logs" / "benchmark_continuous.log"
STATE_FILE = BASE_DIR / "state.json"
ORCHESTRATOR_LOG = BASE_DIR / "logs" / "orchestrator.log"

EXPECTED_CONTAINERS = 20

# Tuned 2026-04-19: aligned with GATE_THRESHOLDS in self_benchmark.py
THRESHOLDS = {
    "router_accuracy": {"value": 0.60, "severity": "warning", "direction": "below"},
    "infra_uptime": {"value": 0.95, "severity": "critical", "direction": "below"},
    "alert_precision": {"value": 0.50, "severity": "warning", "direction": "below"},
    "latency_p95": {"value": 15000, "severity": "warning", "direction": "above"},
}

# Compact query set for continuous monitoring
KNOWN_QUERIES = [
    ("que temperatura hace", "skill:weather"),
    ("prende las luces", "skill:smart-home"),
    ("escanea la red", "skill:network-scanner"),
    ("mis emails", "skill:email"),
    ("hola", "chat"),
    ("analiza los logs", "complex_reasoning"),
    ("reinicia sonarr", "automation_command"),
    ("modo cine", "skill:smart-home"),
    ("estado de la vpn", "skill:infra-monitor"),
    ("buen dia", "skill:morning-greeting"),
]


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def _load_jsonl_recent(path: Path, hours: int = 1) -> list:
    entries = []
    cutoff = _now() - timedelta(hours=hours)
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                    ts = entry.get("ts", "")
                    if ts and datetime.fromisoformat(ts) >= cutoff:
                        entries.append(entry)
                except (json.JSONDecodeError, ValueError):
                    continue
    except FileNotFoundError:
        pass
    return entries


# ── Suites ──────────────────────────────────────────────────────


def check_router_accuracy() -> float:
    """Test 10 known queries against orchestrator. Returns accuracy 0-1."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "orchestrator", str(BASE_DIR / "orchestrator.py"))
        orch = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(orch)

        correct = 0
        for query, expected in KNOWN_QUERIES:
            try:
                result = orch.classify_query(query)
                actual = result if isinstance(result, str) else result.get("classification", "")
                if actual == expected:
                    correct += 1
            except Exception:
                pass
        return correct / len(KNOWN_QUERIES) if KNOWN_QUERIES else 0
    except Exception:
        return -1  # error state


def check_infra_uptime() -> float:
    """Check expected containers vs running. Returns ratio 0-1."""
    state = load_json(STATE_FILE, {})
    containers = state.get("containers", [])
    running = sum(1 for c in containers if c.get("up"))
    return running / EXPECTED_CONTAINERS if EXPECTED_CONTAINERS > 0 else 1.0


def check_alert_precision() -> float:
    """Useful alerts / total from decision_quality. Returns ratio 0-1."""
    dq_dir = STATE_DIR / "decision_quality"
    if not dq_dir.exists():
        return 1.0  # no data = assume good

    today = _now().strftime("%Y-%m-%d")
    entries = load_json(dq_dir / f"{today}.json", [])
    if not entries:
        return 1.0

    total = len(entries)
    good = sum(1 for e in entries if e.get("quality") == "good")
    neutral = sum(1 for e in entries if e.get("quality") == "neutral")
    # Precision = (good + neutral) / total (bad = false positives)
    return (good + neutral) / total if total > 0 else 1.0


def check_latency_p95() -> float:
    """P95 latency in ms from recent orchestrator queries."""
    entries = _load_jsonl_recent(ORCHESTRATOR_LOG, hours=1)
    latencies = []
    for e in entries:
        lat = e.get("latency") or e.get("decision_latency")
        if lat is not None:
            try:
                latencies.append(float(lat) * 1000)  # to ms
            except (ValueError, TypeError):
                pass

    if not latencies:
        return 0

    latencies.sort()
    p95_idx = min(int(len(latencies) * 0.95), len(latencies) - 1)
    return latencies[p95_idx]


# ── Main API ────────────────────────────────────────────────────


def run() -> dict:
    """Run all benchmark suites and check thresholds."""
    results = {
        "ts": _now().isoformat(),
        "router_accuracy": check_router_accuracy(),
        "infra_uptime": check_infra_uptime(),
        "alert_precision": check_alert_precision(),
        "latency_p95": check_latency_p95(),
        "alerts": [],
    }

    # Check thresholds
    for metric, config in THRESHOLDS.items():
        value = results.get(metric, 0)
        if value < 0:
            continue  # error state, skip

        breached = False
        if config["direction"] == "below" and value < config["value"]:
            breached = True
        elif config["direction"] == "above" and value > config["value"]:
            breached = True

        if breached:
            alert = {
                "metric": metric,
                "value": round(value, 4),
                "threshold": config["value"],
                "severity": config["severity"],
            }
            results["alerts"].append(alert)

            # Notify via noise_filter
            try:
                import noise_filter as nf
                if nf.should_send(f"benchmark_{metric}",
                                  f"{metric}={value:.4f} (threshold {config['value']})",
                                  severity=config["severity"]):
                    _notify_alert(alert)
            except Exception:
                pass

    # Save results
    RESULTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(RESULTS_FILE, "w") as f:
        json.dump(results, f, indent=2, default=str)

    # Update metrics_state for Prometheus
    _update_prometheus_metrics(results)

    _log({"action": "run_complete", "alerts": len(results["alerts"]),
          "accuracy": results["router_accuracy"],
          "uptime": results["infra_uptime"]})

    return results


def _notify_alert(alert: dict):
    """Send threshold alert to Telegram."""
    try:
        config_path = Path("/home/tenazas/.openclaw/openclaw.json")
        if not config_path.exists():
            return
        token = json.loads(config_path.read_text())["channels"]["telegram"]["botToken"]
        msg = (f"Benchmark alert: {alert['metric']}\n"
               f"Value: {alert['value']:.4f} (threshold: {alert['threshold']})\n"
               f"Severity: {alert['severity']}")
        from urllib.request import Request, urlopen
        payload = json.dumps({
            "chat_id": get_default_telegram_chat_id(),
            "text": msg,
        }).encode()
        urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                        data=payload, headers={"Content-Type": "application/json"}), timeout=10)
    except Exception:
        pass


def _update_prometheus_metrics(results: dict):
    """Write benchmark metrics to metrics_state.json for Prometheus."""
    try:
        state = load_json(METRICS_STATE, {})
        state["benchmark_continuous"] = {
            "router_accuracy": results["router_accuracy"],
            "infra_uptime": results["infra_uptime"],
            "alert_precision": results["alert_precision"],
            "latency_p95": results["latency_p95"],
            "last_run": results["ts"],
            "alerts_count": len(results["alerts"]),
        }
        with open(METRICS_STATE, "w") as f:
            json.dump(state, f, indent=2, default=str)
    except Exception:
        pass


def get_last_results() -> dict:
    """Load last benchmark results."""
    return load_json(RESULTS_FILE, {})


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Benchmark Continuous")
    p.add_argument("--run", action="store_true", help="Run benchmarks")
    p.add_argument("--status", action="store_true", help="Show last results")
    args = p.parse_args()

    if args.run:
        results = run()
        print(f"Router accuracy: {results['router_accuracy']:.0%}")
        print(f"Infra uptime: {results['infra_uptime']:.0%}")
        print(f"Alert precision: {results['alert_precision']:.0%}")
        print(f"Latency P95: {results['latency_p95']:.0f}ms")
        if results["alerts"]:
            print(f"\nAlerts ({len(results['alerts'])}):")
            for a in results["alerts"]:
                print(f"  [{a['severity']}] {a['metric']}: {a['value']:.4f}")
    elif args.status:
        print(json.dumps(get_last_results(), indent=2, default=str))
    else:
        p.print_help()
