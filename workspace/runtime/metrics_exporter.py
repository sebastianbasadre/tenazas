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

    # Router V2
    ROUTER_V2_CALLS = Counter("router_v2_decisions_total", "Router V2 routing decisions", ["role"])
    ROUTER_V1_CALLS = Counter("router_v1_decisions_total", "Router V1 fallback decisions")
    ROUTER_V1_FALLBACK_FROM_V2 = Counter(
        "router_v1_fallback_from_v2_total",
        "Times V2 failed and V1 took over (backends offline, error, etc.)",
    )
    ROUTER_V2_INTERNAL_FALLBACKS = Counter(
        "router_v2_internal_fallbacks_total",
        "Times V2 used a non-primary model within the role chain",
        ["role"],
    )
    ROUTER_VRAM_SWAPS = Counter(
        "router_vram_swaps_total",
        "Times a model exceeding GPU VRAM was selected (Ollama offloads to CPU)",
        ["role", "model"],
    )
    ROUTER_CLOUD_FALLBACKS = Counter(
        "router_cloud_fallbacks_total",
        "Times routing fell through to Gemini cloud (all local backends offline)",
    )
    ROUTER_LATENCY = Histogram(
        "router_decision_latency_seconds",
        "Time to make a routing decision (classify + select)",
        ["role"],
        buckets=[0.001, 0.005, 0.01, 0.05, 0.1, 0.5, 1, 5],
    )
    ROUTER_MODEL_LATENCY = Histogram(
        "router_model_latency_seconds",
        "LLM inference latency by model and role (when available)",
        ["model", "role"],
        buckets=[0.5, 1, 2, 5, 10, 30, 60, 120],
    )

    # Guardrails
    GUARDRAIL_DEGRADATIONS = Counter(
        "guardrail_degradations_total",
        "Times a heavy role was degraded by guardrails",
        ["reason"],
    )


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


# ── Router V2 Recording ─────────────────────────────────────────


def record_routing_decision(
    role: str,
    model: str,
    backend: str,
    fallback_used: bool = False,
    vram_swap: bool = False,
    decision_latency: float = 0.0,
    v2_used: bool = True,
    cloud_fallback: bool = False,
):
    """Record a routing decision for Prometheus + state file.

    Args:
        role: V2 role (fast, reason, heavy, code, vision, embed)
        model: model name selected (e.g. "qwen2.5:7b")
        backend: backend used (notebook_gpu, nas_cpu, cloud)
        fallback_used: True if V2 used a non-primary model in chain
        vram_swap: True if model exceeds GPU VRAM
        decision_latency: seconds to make the routing decision
        v2_used: True if V2 path was taken, False if V1
        cloud_fallback: True if fell through to Gemini cloud
    """
    if PROM_AVAILABLE:
        if v2_used:
            ROUTER_V2_CALLS.labels(role=role).inc()
            if fallback_used:
                ROUTER_V2_INTERNAL_FALLBACKS.labels(role=role).inc()
            if vram_swap:
                ROUTER_VRAM_SWAPS.labels(role=role, model=model).inc()
            ROUTER_LATENCY.labels(role=role).observe(decision_latency)
        else:
            ROUTER_V1_CALLS.inc()
        if cloud_fallback:
            ROUTER_CLOUD_FALLBACKS.inc()

    _log_metric("routing_decision", decision_latency, {
        "role": role, "model": model, "backend": backend,
        "fallback_used": fallback_used, "vram_swap": vram_swap,
        "v2_used": v2_used, "cloud_fallback": cloud_fallback,
    })


def record_v1_fallback_from_v2():
    """Record that V2 failed and V1 took over."""
    if PROM_AVAILABLE:
        ROUTER_V1_FALLBACK_FROM_V2.inc()
    _log_metric("router_v1_fallback_from_v2")


def record_model_latency(model: str, role: str, latency: float):
    """Record LLM inference latency by model and role."""
    if PROM_AVAILABLE:
        ROUTER_MODEL_LATENCY.labels(model=model, role=role).observe(latency)
    _log_metric("model_latency", latency, {"model": model, "role": role})


def record_guardrail_degradation(reason: str):
    """Record a guardrail-triggered degradation."""
    if PROM_AVAILABLE:
        GUARDRAIL_DEGRADATIONS.labels(reason=reason).inc()
    _log_metric("guardrail_degradation", 1, {"reason": reason})


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


# ── Cross-process metrics sync ──────────────────────────────────
# runtime.py runs as a cron subprocess, metrics_server.py is persistent.
# They don't share memory. This file bridges them via JSON.

METRICS_STATE_FILE = BASE_DIR / "logs" / "metrics_state.json"


def write_metrics_snapshot():
    """Write cumulative metrics to file for metrics_server.py to read.

    Called at the end of each runtime cycle. metrics_server.py polls
    this file and updates Prometheus Gauges so Prometheus can scrape them.
    """
    state = _load_metrics_state()

    # Accumulate this cycle's values into persistent state
    # (each runtime.py process starts counters from 0, so we add)
    METRICS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_STATE_FILE, "w") as f:
        json.dump(state, f)


def _load_metrics_state() -> dict:
    """Load existing metrics state, return defaults if missing."""
    try:
        with open(METRICS_STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {
            "runs": 0, "failures": 0, "restarts": 0,
            "vpn_disconnects": 0, "ollama_failures": 0,
            "freezes": 0, "watchdogs": 0, "health_score": 0,
            "last_duration": 0,
        }


def increment_state(key: str, value=1):
    """Increment a cumulative counter in the persistent state file."""
    state = _load_metrics_state()
    state[key] = state.get(key, 0) + value
    METRICS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_STATE_FILE, "w") as f:
        json.dump(state, f)


def set_state(key: str, value):
    """Set a gauge value in the persistent state file."""
    state = _load_metrics_state()
    state[key] = value
    METRICS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_STATE_FILE, "w") as f:
        json.dump(state, f)


# Patch record functions to also persist to state file
_orig_record_run = record_run
_orig_record_failure = record_failure
_orig_record_container_restart = record_container_restart
_orig_record_vpn_disconnect = record_vpn_disconnect
_orig_record_ollama_failure = record_ollama_failure
_orig_record_freeze = record_freeze
_orig_record_watchdog = record_watchdog
_orig_set_health_score = set_health_score


def record_run(duration: float):
    _orig_record_run(duration)
    increment_state("runs")
    set_state("last_duration", round(duration, 2))


def record_failure():
    _orig_record_failure()
    increment_state("failures")


def record_container_restart():
    _orig_record_container_restart()
    increment_state("restarts")


def record_vpn_disconnect():
    _orig_record_vpn_disconnect()
    increment_state("vpn_disconnects")


def record_ollama_failure():
    _orig_record_ollama_failure()
    increment_state("ollama_failures")


def record_freeze():
    _orig_record_freeze()
    increment_state("freezes")


def record_watchdog():
    _orig_record_watchdog()
    increment_state("watchdogs")


def set_health_score(score: float):
    _orig_set_health_score(score)
    set_state("health_score", round(score, 1))


_orig_record_step = record_step
_orig_record_rag_query = record_rag_query


def record_step(step: str, duration: float):
    _orig_record_step(step, duration)
    # Persist step durations as nested dict
    state = _load_metrics_state()
    steps = state.get("steps", {})
    steps[step] = round(duration, 3)
    state["steps"] = steps
    METRICS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_STATE_FILE, "w") as f:
        json.dump(state, f)


def record_rag_query(latency: float, confidence: float):
    _orig_record_rag_query(latency, confidence)
    increment_state("rag_queries")
    if confidence < 0.6:
        increment_state("rag_low_confidence")


# Patch Router V2 recording to also persist to state file
_orig_record_routing_decision = record_routing_decision
_orig_record_v1_fallback = record_v1_fallback_from_v2


def record_routing_decision(
    role: str, model: str, backend: str,
    fallback_used: bool = False, vram_swap: bool = False,
    decision_latency: float = 0.0, v2_used: bool = True,
    cloud_fallback: bool = False,
):
    _orig_record_routing_decision(
        role, model, backend, fallback_used, vram_swap,
        decision_latency, v2_used, cloud_fallback,
    )
    # Persist aggregates to state file for metrics_server.py
    state = _load_metrics_state()
    rv2 = state.get("router_v2", {})
    if v2_used:
        rv2["v2_calls"] = rv2.get("v2_calls", 0) + 1
        rv2[f"role_{role}"] = rv2.get(f"role_{role}", 0) + 1
        if fallback_used:
            rv2["internal_fallbacks"] = rv2.get("internal_fallbacks", 0) + 1
        if vram_swap:
            rv2["vram_swaps"] = rv2.get("vram_swaps", 0) + 1
    else:
        rv2["v1_calls"] = rv2.get("v1_calls", 0) + 1
    if cloud_fallback:
        rv2["cloud_fallbacks"] = rv2.get("cloud_fallbacks", 0) + 1
    rv2["last_role"] = role
    rv2["last_model"] = model
    rv2["last_latency"] = round(decision_latency, 4)
    state["router_v2"] = rv2
    METRICS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_STATE_FILE, "w") as f:
        json.dump(state, f)


def record_v1_fallback_from_v2():
    _orig_record_v1_fallback()
    state = _load_metrics_state()
    rv2 = state.get("router_v2", {})
    rv2["v1_fallback_from_v2"] = rv2.get("v1_fallback_from_v2", 0) + 1
    state["router_v2"] = rv2
    METRICS_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(METRICS_STATE_FILE, "w") as f:
        json.dump(state, f)


# ── Server startup ──────────────────────────────────────────────
# DO NOT auto-start here. metrics_server.py owns the :9105 port.
# runtime.py writes to metrics_state.json, metrics_server.py reads it.
