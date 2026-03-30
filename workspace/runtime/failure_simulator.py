#!/usr/bin/env python3
"""
Failure Simulator — Generates targeted failure scenarios to validate diagnostics.

More granular than simulation_lab.py — focuses on specific failure modes:
  container crash loop, VPN disconnect, DNS failure, network outage,
  Ollama unavailable, disk full, high latency.

Uses knowledge_graph to determine cascade impact.
NEVER touches real infrastructure.
"""

import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
TELEMETRY_LOG = BASE_DIR / "logs" / "failure_simulator.log"


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _base_state() -> dict:
    return {
        "ts": _now().isoformat(),
        "containers": [
            {"name": n, "status": "Up 3 days", "up": True}
            for n in ["gluetun", "qbittorrent", "sonarr", "radarr", "lidarr",
                       "prowlarr", "seerr", "requestrr", "tautulli", "sabnzbd",
                       "chromadb", "piper", "portainer", "watchtower", "openclaw"]
        ],
        "internet": {"ok": True, "latency_ms": 12},
        "vpn": {"ok": True, "ip": "82.140.1.2", "country": "ES"},
        "public_ip": {"ip": "206.223.50.10"},
        "ollama_notebook": {"ok": True},
        "ollama_nas": {"ok": True},
        "network_devices": {"count": 38},
    }


def simulate_crash_loop(target: str = "sonarr", restarts: int = 5) -> dict:
    """Simulate a container in crash loop."""
    state = _base_state()
    for c in state["containers"]:
        if c["name"] == target:
            c["status"] = f"Restarting ({restarts}) 10 seconds ago"
            c["up"] = False
    memory = [
        {"ts": _now().isoformat(), "events": [], "results": [
            {"action": "docker.restart", "target": target, "ok": True}
        ]} for _ in range(restarts)
    ]
    return {"state": state, "memory": memory, "scenario": f"crash_loop:{target}"}


def simulate_vpn_disconnect() -> dict:
    state = _base_state()
    state["vpn"] = {"ok": False, "ip": None}
    for c in state["containers"]:
        if c["name"] == "gluetun":
            c["status"] = "Up 1h (unhealthy)"
    return {"state": state, "memory": [], "scenario": "vpn_disconnect"}


def simulate_dns_failure() -> dict:
    state = _base_state()
    state["internet"] = {"ok": True, "latency_ms": 15}
    # Internet works but DNS doesn't — VPN check fails
    state["vpn"] = {"ok": False, "ip": None}
    return {"state": state, "memory": [], "scenario": "dns_failure"}


def simulate_network_outage() -> dict:
    state = _base_state()
    state["internet"] = {"ok": False, "latency_ms": None}
    state["vpn"] = {"ok": False, "ip": None}
    state["ollama_notebook"] = {"ok": False}
    state["network_devices"] = {"count": 2}
    return {"state": state, "memory": [], "scenario": "network_outage"}


def simulate_ollama_unavailable() -> dict:
    state = _base_state()
    state["ollama_notebook"] = {"ok": False}
    state["ollama_nas"] = {"ok": False}
    return {"state": state, "memory": [], "scenario": "ollama_unavailable"}


def simulate_high_latency() -> dict:
    state = _base_state()
    state["internet"] = {"ok": True, "latency_ms": 500}
    return {"state": state, "memory": [], "scenario": "high_latency"}


def run_scenario(scenario: str) -> dict:
    """Run a named scenario and return diagnostics results.

    NEVER touches real infrastructure.
    """
    generators = {
        "crash_loop": simulate_crash_loop,
        "vpn_disconnect": simulate_vpn_disconnect,
        "dns_failure": simulate_dns_failure,
        "network_outage": simulate_network_outage,
        "ollama_unavailable": simulate_ollama_unavailable,
        "high_latency": simulate_high_latency,
    }

    gen = generators.get(scenario)
    if not gen:
        return {"error": f"Unknown scenario: {scenario}", "available": list(generators.keys())}

    sim = gen()

    # Run diagnostics against fake state
    try:
        from runtime import infra_diagnostics as diag
        issues = diag.run_diagnostics(sim["state"])
    except ImportError:
        issues = []

    result = {
        "scenario": sim["scenario"],
        "issues": [{"problem": i["problem"], "severity": i["severity"]} for i in issues],
        "issues_count": len(issues),
        "critical": sum(1 for i in issues if i.get("severity") == "critical"),
    }

    _log({"type": "simulation", "scenario": scenario, "issues": result["issues_count"]})
    return result


def list_scenarios() -> list:
    return ["crash_loop", "vpn_disconnect", "dns_failure",
            "network_outage", "ollama_unavailable", "high_latency"]
