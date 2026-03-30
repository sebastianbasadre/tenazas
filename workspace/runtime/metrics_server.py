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
    # Runtime
    G_RUNS = Gauge("runtime_runs_total", "Total runtime cycles completed")
    G_DURATION = Gauge("runtime_last_duration_seconds", "Last cycle duration")
    G_FAILURES = Gauge("runtime_failures_total", "Total runtime failures")
    G_HEALTH = Gauge("runtime_health_score", "Runtime health score 0-100")

    # Infrastructure
    G_RESTARTS = Gauge("container_restarts_total", "Container restarts executed")
    G_VPN = Gauge("vpn_disconnects_total", "VPN disconnect events")
    G_OLLAMA = Gauge("ollama_failures_total", "Ollama unavailable events")
    G_FREEZES = Gauge("runtime_freeze_detected", "Freeze events detected")
    G_WATCHDOGS = Gauge("runtime_watchdog_triggered", "Watchdog timeout events")

    # RAG
    G_RAG_QUERIES = Gauge("rag_queries_total", "Total RAG queries executed")
    G_RAG_LOW_CONF = Gauge("rag_low_confidence_total", "RAG low confidence results")

    # Steps (top-level gauges for each step)
    G_STEP_UPDATE_STATE = Gauge("runtime_step_update_state_seconds", "update_state step duration")
    G_STEP_DETECT_EVENTS = Gauge("runtime_step_detect_events_seconds", "detect_events step duration")
    G_STEP_RUN_TASKS = Gauge("runtime_step_run_tasks_seconds", "run_tasks step duration")
    G_STEP_NMAP = Gauge("runtime_step_nmap_seconds", "network scan step duration")

    # Model failover
    G_FAILOVER_ACTIVE = Gauge("model_failover_active", "1 if using fallback model, 0 if primary")


def update_from_file():
    """Read metrics_state.json and update Prometheus Gauges."""
    try:
        with open(METRICS_STATE_FILE) as f:
            state = json.load(f)

        # Runtime
        G_RUNS.set(state.get("runs", 0))
        G_DURATION.set(state.get("last_duration", 0))
        G_FAILURES.set(state.get("failures", 0))
        G_HEALTH.set(state.get("health_score", 0))

        # Infrastructure
        G_RESTARTS.set(state.get("restarts", 0))
        G_VPN.set(state.get("vpn_disconnects", 0))
        G_OLLAMA.set(state.get("ollama_failures", 0))
        G_FREEZES.set(state.get("freezes", 0))
        G_WATCHDOGS.set(state.get("watchdogs", 0))

        # RAG
        G_RAG_QUERIES.set(state.get("rag_queries", 0))
        G_RAG_LOW_CONF.set(state.get("rag_low_confidence", 0))

        # Steps
        steps = state.get("steps", {})
        G_STEP_UPDATE_STATE.set(steps.get("update_state", 0))
        G_STEP_DETECT_EVENTS.set(steps.get("detect_events", 0))
        G_STEP_RUN_TASKS.set(steps.get("run_tasks", 0))
        G_STEP_NMAP.set(steps.get("nmap", 0))

        # Failover
        G_FAILOVER_ACTIVE.set(state.get("failover_active", 0))

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
