#!/usr/bin/env python3
"""Persist coordinator and per-agent telemetry for metrics_server polling."""

from __future__ import annotations

from pathlib import Path

from runtime_utils import load_json, locked_write_json

BASE_DIR = Path(__file__).parent
METRICS_STATE_FILE = BASE_DIR / "logs" / "agent_metrics_state.json"
VALID_OUTCOMES = {"ok", "error", "timeout"}


def _default_state() -> dict:
    return {
        "per_agent": {},
        "coordinator": {
            "routes": {},
            "fallbacks": {},
            "handled": {"true": 0, "false": 0},
        },
    }


def _load_state() -> dict:
    state = load_json(str(METRICS_STATE_FILE), _default_state())
    if not isinstance(state, dict):
        return _default_state()
    state.setdefault("per_agent", {})
    coord = state.setdefault("coordinator", {})
    coord.setdefault("routes", {})
    coord.setdefault("fallbacks", {})
    coord.setdefault("handled", {"true": 0, "false": 0})
    return state


def _save_state(state: dict) -> None:
    try:
        locked_write_json(str(METRICS_STATE_FILE), state)
    except Exception:
        pass


def record_agent_result(agent: str, elapsed_seconds: float, outcome: str) -> None:
    """Record aggregate metrics for a single agent execution."""
    if not agent:
        return
    outcome = outcome if outcome in VALID_OUTCOMES else "error"
    elapsed = max(float(elapsed_seconds), 0.0)
    state = _load_state()
    per_agent = state["per_agent"].setdefault(
        agent,
        {
            "queries_total": 0,
            "latency_sum": 0.0,
            "latency_last": 0.0,
            "latency_avg": 0.0,
            "outcomes": {"ok": 0, "error": 0, "timeout": 0},
        },
    )
    per_agent["queries_total"] += 1
    per_agent["latency_sum"] += elapsed
    per_agent["latency_last"] = elapsed
    per_agent["latency_avg"] = per_agent["latency_sum"] / per_agent["queries_total"]
    per_agent["outcomes"].setdefault(outcome, 0)
    per_agent["outcomes"][outcome] += 1
    _save_state(state)


def record_route_result(strategy: str, handled: bool, source: str | None = None) -> None:
    """Record aggregate metrics for a coordinator route."""
    if not strategy:
        return
    state = _load_state()
    coord = state["coordinator"]
    coord["routes"][strategy] = coord["routes"].get(strategy, 0) + 1
    key = "true" if handled else "false"
    coord["handled"][key] = coord["handled"].get(key, 0) + 1
    if source:
        coord["fallbacks"][source] = coord["fallbacks"].get(source, 0) + 1
    _save_state(state)
