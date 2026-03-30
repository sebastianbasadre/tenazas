#!/usr/bin/env python3
"""
Metrics Exporter — Prometheus-compatible metrics for Tenazas runtime.

Exposes metrics at http://localhost:9105/metrics (if prometheus_client installed).
Gracefully degrades to JSON file logging if prometheus_client not available.

Metrics:
  runtime_runs_total, runtime_duration_seconds, runtime_failures_total
  rag_queries_total, rag_latency_seconds, rag_low_confidence_total
  container_restarts_total, vpn_disconnects_total, ollama_failures_total
  runtime_freeze_detected, runtime_watchdog_triggered
  runtime_health_score, runtime_step_duration_seconds
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
METRICS_LOG = BASE_DIR / "logs" / "runtime_metrics.log"
METRICS_PORT = 9105

# Try to import prometheus_client — optional dependency
try:
    from prometheus_client import Counter, Gauge, Histogram, Summary, start_http_server
    PROM_AVAILABLE = True
except ImportError:
    PROM_AVAILABLE = False


def _now():
    return datetime.now().astimezone()


# ── Prometheus Metrics (if available) ────────────────────────────

if PROM_AVAILABLE:
    # Runtime
    RUNTIME_RUNS = Counter("runtime_runs_total", "Total runtime cycles")
    RUNTIME_DURATION = Histogram(
        "runtime_duration_seconds", "Runtime cycle duration",
        buckets=[1, 5, 10, 30, 60, 120],
    )
    RUNTIME_FAILURES = Counter("runtime_failures_total", "Runtime failures")

    # Steps
    STEP_DURATION = Histogram(
        "runtime_step_duration_seconds", "Duration of each runtime step",
        ["step"],
        buckets=[0.1, 0.5, 1, 2, 5, 10, 30],
    )

    # RAG
    RAG_QUERIES = Counter("rag_queries_total", "Total RAG queries")
    RAG_LATENCY = Histogram(
        "rag_latency_seconds", "RAG query latency",
        buckets=[0.1, 0.5, 1, 2, 5, 10],
    )
    RAG_LOW_CONFIDENCE = Counter("rag_low_confidence_total", "RAG low confidence results")

    # Infrastructure
    CONTAINER_RESTARTS = Counter("container_restarts_total", "Container restarts executed")
    VPN_DISCONNECTS = Counter("vpn_disconnects_total", "VPN disconnect events")
    OLLAMA_FAILURES = Counter("ollama_failures_total", "Ollama unavailable events")

    # Health
    FREEZE_DETECTED = Counter("runtime_freeze_detected", "Freeze events detected")
    WATCHDOG_TRIGGERED = Counter("runtime_watchdog_triggered", "Watchdog timeout events")
    HEALTH_SCORE = Gauge("runtime_health_score", "Runtime health score 0-100")


# ── Singleton server ─────────────────────────────────────────────

_server_started = False
_server_lock = threading.Lock()


def start_server():
    """Start Prometheus HTTP server (singleton, once per container lifetime)."""
    global _server_started
    if not PROM_AVAILABLE:
        return False

    with _server_lock:
        if _server_started:
            return True
        try:
            start_http_server(METRICS_PORT)
            _server_started = True
            return True
        except OSError:
            # Port already in use (another instance running)
            _server_started = True
            return True


# ── Recording API (works with or without prometheus) ─────────────


def _log_metric(name: str, value=1, labels: dict | None = None):
    """Append metric to JSON log file (fallback when no prometheus)."""
    METRICS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry = {"ts": _now().isoformat(), "metric": name, "value": value}
    if labels:
        entry["labels"] = labels
    with open(METRICS_LOG, "a") as f:
        f.write(json.dumps(entry) + "\n")


def record_run(duration: float):
    """Record a completed runtime cycle."""
    if PROM_AVAILABLE:
        RUNTIME_RUNS.inc()
        RUNTIME_DURATION.observe(duration)
    _log_metric("runtime_run", duration)


def record_failure():
    if PROM_AVAILABLE:
        RUNTIME_FAILURES.inc()
    _log_metric("runtime_failure")


def record_step(step: str, duration: float):
    """Record duration of a runtime step."""
    if PROM_AVAILABLE:
        STEP_DURATION.labels(step=step).observe(duration)
    _log_metric("step_duration", duration, {"step": step})


def record_rag_query(latency: float, confidence: float):
    if PROM_AVAILABLE:
        RAG_QUERIES.inc()
        RAG_LATENCY.observe(latency)
        if confidence < 0.6:
            RAG_LOW_CONFIDENCE.inc()
    _log_metric("rag_query", latency, {"confidence": round(confidence, 4)})


def record_container_restart():
    if PROM_AVAILABLE:
        CONTAINER_RESTARTS.inc()
    _log_metric("container_restart")


def record_vpn_disconnect():
    if PROM_AVAILABLE:
        VPN_DISCONNECTS.inc()
    _log_metric("vpn_disconnect")


def record_ollama_failure():
    if PROM_AVAILABLE:
        OLLAMA_FAILURES.inc()
    _log_metric("ollama_failure")


def record_freeze():
    if PROM_AVAILABLE:
        FREEZE_DETECTED.inc()
    _log_metric("freeze_detected")


def record_watchdog():
    if PROM_AVAILABLE:
        WATCHDOG_TRIGGERED.inc()
    _log_metric("watchdog_triggered")


def set_health_score(score: float):
    if PROM_AVAILABLE:
        HEALTH_SCORE.set(score)
    _log_metric("health_score", score)


# ── Health Score Calculator ──────────────────────────────────────


def calculate_health(
    cycle_duration: float,
    step_durations: dict,
    freeze_count: int = 0,
    restart_count: int = 0,
) -> float:
    """Calculate runtime health score (0-100).

    Factors:
      - Cycle duration (fast = good)
      - Step balance (no single step dominating)
      - Freeze events (bad)
      - Restart loops (bad)
    """
    score = 100.0

    # Penalize slow cycles
    if cycle_duration > 60:
        score -= 30
    elif cycle_duration > 30:
        score -= 15
    elif cycle_duration > 10:
        score -= 5

    # Penalize individual slow steps
    for step, dur in step_durations.items():
        if dur > 20:
            score -= 15
        elif dur > 10:
            score -= 5

    # Penalize freezes heavily
    score -= freeze_count * 20

    # Penalize restart loops
    if restart_count > 3:
        score -= 20
    elif restart_count > 1:
        score -= 10

    return max(round(score, 1), 0.0)
