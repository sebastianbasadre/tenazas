#!/usr/bin/env python3
"""
Standalone Prometheus metrics server for Tenazas.

Runs as a persistent background process (started from startup.sh).
Polls metrics_state.json (written by runtime.py each cycle) and
updates Prometheus Gauges so they can be scraped at :9105/metrics.

Usage: python3 metrics_server.py &
"""

import json
import time
from pathlib import Path

METRICS_STATE_FILE = Path(__file__).parent / "logs" / "metrics_state.json"
POLL_INTERVAL = 5  # seconds

try:
    from prometheus_client import Gauge, start_http_server
    PROM_AVAILABLE = True
except ImportError:
    PROM_AVAILABLE = False

if PROM_AVAILABLE:
    # Use Gauges (not Counters) because we SET values from file
    G_RUNS = Gauge("runtime_runs_total", "Total runtime cycles completed")
    G_DURATION = Gauge("runtime_last_duration_seconds", "Last cycle duration")
    G_FAILURES = Gauge("runtime_failures_total", "Total runtime failures")
    G_RESTARTS = Gauge("container_restarts_total", "Container restarts executed")
    G_VPN = Gauge("vpn_disconnects_total", "VPN disconnect events")
    G_OLLAMA = Gauge("ollama_failures_total", "Ollama unavailable events")
    G_FREEZES = Gauge("runtime_freeze_detected", "Freeze events detected")
    G_WATCHDOGS = Gauge("runtime_watchdog_triggered", "Watchdog timeout events")
    G_HEALTH = Gauge("runtime_health_score", "Runtime health score 0-100")


def update_from_file():
    """Read metrics_state.json and update Prometheus Gauges."""
    try:
        with open(METRICS_STATE_FILE) as f:
            state = json.load(f)
        G_RUNS.set(state.get("runs", 0))
        G_DURATION.set(state.get("last_duration", 0))
        G_FAILURES.set(state.get("failures", 0))
        G_RESTARTS.set(state.get("restarts", 0))
        G_VPN.set(state.get("vpn_disconnects", 0))
        G_OLLAMA.set(state.get("ollama_failures", 0))
        G_FREEZES.set(state.get("freezes", 0))
        G_WATCHDOGS.set(state.get("watchdogs", 0))
        G_HEALTH.set(state.get("health_score", 0))
    except (FileNotFoundError, json.JSONDecodeError):
        pass


if __name__ == "__main__":
    if not PROM_AVAILABLE:
        print("prometheus_client not available")
        exit(1)

    start_http_server(9105)
    print("Metrics server on :9105 (polling metrics_state.json)")

    while True:
        update_from_file()
        time.sleep(POLL_INTERVAL)
