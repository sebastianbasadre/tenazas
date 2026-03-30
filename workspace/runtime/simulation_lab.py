#!/usr/bin/env python3
"""
Simulation Lab — Simulates infrastructure scenarios for testing reasoning.

Scenarios: container_crash, network_outage, vpn_failure, multi_failure,
           ollama_down, gluetun_down.
Each scenario generates a fake state dict. Runs diagnostics + action_mapper
against the fake state. Returns results with reasoning quality evaluation.

NEVER touches real infrastructure. All state is synthetic.

Log: workspace/runtime/logs/simulation.log
"""

import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
TELEMETRY_LOG = BASE_DIR / "logs" / "simulation.log"

# Containers used in simulations (realistic set)
SIMULATED_CONTAINERS = [
    "gluetun", "qbittorrent", "sonarr", "radarr", "lidarr",
    "prowlarr", "seerr", "requestrr", "tautulli", "sabnzbd",
    "chromadb", "piper", "ollama", "portainer", "watchtower",
    "openclaw",
]


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Healthy Baseline State ──────────────────────────────────────


def _healthy_state() -> dict:
    """Generate a healthy baseline system state."""
    return {
        "ts": _now().isoformat(),
        "containers": [
            {"name": c, "status": "Up 3 days", "up": True}
            for c in SIMULATED_CONTAINERS
        ],
        "internet": {"ok": True, "latency_ms": 15.2},
        "vpn": {"ok": True, "ip": "82.140.100.42", "country": "ES"},
        "public_ip": {"ip": "206.223.50.10"},
        "ollama_notebook": {"ok": True},
        "ollama_nas": {"ok": True},
        "network_devices": {"count": 38},
    }


# ── Scenario Generators ────────────────────────────────────────


def _scenario_container_crash(target: str = "sonarr") -> dict:
    """Simulate a container crash."""
    state = _healthy_state()
    for c in state["containers"]:
        if c["name"] == target:
            c["status"] = "Exited (137) 2 minutes ago"
            c["up"] = False
    return state


def _scenario_network_outage() -> dict:
    """Simulate complete network outage."""
    state = _healthy_state()
    state["internet"] = {"ok": False, "latency_ms": None}
    state["vpn"] = {"ok": False, "ip": None}
    state["public_ip"] = {"ip": None}
    state["ollama_notebook"] = {"ok": False}
    state["network_devices"] = {"count": 1}  # Only NAS itself
    return state


def _scenario_vpn_failure() -> dict:
    """Simulate VPN failure with IP leak."""
    state = _healthy_state()
    state["vpn"] = {"ok": True, "ip": "206.223.50.10", "country": "AR"}
    return state


def _scenario_multi_failure() -> dict:
    """Simulate multiple simultaneous failures."""
    state = _healthy_state()

    # Two containers down
    for c in state["containers"]:
        if c["name"] in ("sonarr", "radarr"):
            c["status"] = "Exited (1) 5 minutes ago"
            c["up"] = False

    # VPN degraded
    state["vpn"] = {"ok": False, "ip": None}

    # Ollama notebook offline
    state["ollama_notebook"] = {"ok": False}

    # High latency
    state["internet"] = {"ok": True, "latency_ms": 350.0}

    return state


def _scenario_ollama_down() -> dict:
    """Simulate both Ollama backends offline."""
    state = _healthy_state()
    state["ollama_notebook"] = {"ok": False}
    state["ollama_nas"] = {"ok": False}
    return state


def _scenario_gluetun_down() -> dict:
    """Simulate gluetun container down (affects all arr-stack)."""
    state = _healthy_state()
    for c in state["containers"]:
        if c["name"] == "gluetun":
            c["status"] = "Restarting (1) 30 seconds ago"
            c["up"] = False
    state["vpn"] = {"ok": False, "ip": None}
    return state


# ── Scenario Registry ──────────────────────────────────────────

SCENARIOS = {
    "container_crash": {
        "generator": _scenario_container_crash,
        "description": "Single container crashes (sonarr)",
        "expected_issues": 1,
    },
    "network_outage": {
        "generator": _scenario_network_outage,
        "description": "Complete network outage — internet, VPN, Ollama all down",
        "expected_issues": 3,
    },
    "vpn_failure": {
        "generator": _scenario_vpn_failure,
        "description": "VPN IP leak — Argentine IP detected through VPN proxy",
        "expected_issues": 1,
    },
    "multi_failure": {
        "generator": _scenario_multi_failure,
        "description": "Multiple simultaneous failures: containers + VPN + Ollama",
        "expected_issues": 4,
    },
    "ollama_down": {
        "generator": _scenario_ollama_down,
        "description": "Both Ollama backends (notebook GPU + NAS CPU) offline",
        "expected_issues": 1,
    },
    "gluetun_down": {
        "generator": _scenario_gluetun_down,
        "description": "Gluetun container down — VPN gateway unavailable",
        "expected_issues": 2,
    },
}


# ── Evaluation ──────────────────────────────────────────────────


def _evaluate_reasoning(issues: list, actions: list,
                        expected_issues: int) -> dict:
    """Evaluate quality of diagnostic reasoning."""
    issue_count = len(issues)
    action_count = len(actions)

    # Did we detect the right number of issues?
    detection_accuracy = 1.0 - abs(issue_count - expected_issues) / max(expected_issues, 1)
    detection_accuracy = max(detection_accuracy, 0.0)

    # Did we suggest actions for critical issues?
    critical_issues = [i for i in issues if i.get("severity") == "critical"]
    actions_for_critical = min(action_count, len(critical_issues))
    action_coverage = (
        actions_for_critical / len(critical_issues)
        if critical_issues else 1.0
    )

    # Overall quality score
    quality = round(
        (detection_accuracy * 0.5) + (action_coverage * 0.5),
        4,
    )

    return {
        "detection_accuracy": round(detection_accuracy, 4),
        "action_coverage": round(action_coverage, 4),
        "quality_score": quality,
        "issues_detected": issue_count,
        "issues_expected": expected_issues,
        "actions_suggested": action_count,
        "critical_issues": len(critical_issues),
    }


# ── Main API ────────────────────────────────────────────────────


def run_simulation(scenario: str) -> dict:
    """Run a simulation scenario and evaluate diagnostic reasoning.

    Args:
        scenario: one of the registered scenario names

    Returns:
        {
            "scenario": str,
            "issues_detected": list,
            "actions_suggested": list,
            "reasoning_quality": dict,
        }

    NEVER touches real infrastructure. All analysis runs against fake state.
    """
    if scenario not in SCENARIOS:
        available = list(SCENARIOS.keys())
        return {
            "scenario": scenario,
            "error": f"Unknown scenario. Available: {available}",
            "issues_detected": [],
            "actions_suggested": [],
            "reasoning_quality": {},
        }

    scenario_def = SCENARIOS[scenario]
    fake_state = scenario_def["generator"]()

    # Run diagnostics against fake state
    issues = []
    try:
        from runtime import infra_diagnostics
        issues = infra_diagnostics.run_diagnostics(state=fake_state)
    except ImportError:
        _log({"type": "sim_error", "scenario": scenario,
              "error": "infra_diagnostics not available"})
    except Exception as e:
        _log({"type": "sim_error", "scenario": scenario, "error": str(e)})

    # Run action mapper against detected issues
    actions = []
    try:
        from runtime import action_mapper
        actions = action_mapper.map_diagnostics_to_actions(issues)
    except ImportError:
        _log({"type": "sim_error", "scenario": scenario,
              "error": "action_mapper not available"})
    except Exception as e:
        _log({"type": "sim_error", "scenario": scenario, "error": str(e)})

    # Evaluate reasoning quality
    reasoning = _evaluate_reasoning(
        issues, actions, scenario_def["expected_issues"]
    )

    result = {
        "scenario": scenario,
        "description": scenario_def["description"],
        "issues_detected": issues,
        "actions_suggested": actions,
        "reasoning_quality": reasoning,
    }

    _log({
        "type": "simulation_run",
        "scenario": scenario,
        "issues_detected": len(issues),
        "actions_suggested": len(actions),
        "quality_score": reasoning["quality_score"],
    })

    return result


def run_all_simulations() -> dict:
    """Run all available simulation scenarios.

    Returns summary with per-scenario results.
    """
    results = {}
    total_quality = 0.0

    for name in SCENARIOS:
        result = run_simulation(name)
        results[name] = result
        total_quality += result.get("reasoning_quality", {}).get(
            "quality_score", 0
        )

    avg_quality = round(total_quality / max(len(SCENARIOS), 1), 4)

    summary = {
        "scenarios_run": len(results),
        "avg_quality_score": avg_quality,
        "results": results,
        "run_at": _now().isoformat(),
    }

    _log({
        "type": "all_simulations",
        "scenarios": len(results),
        "avg_quality": avg_quality,
    })

    return summary


def list_scenarios() -> list:
    """List all available simulation scenarios."""
    return [
        {
            "name": name,
            "description": s["description"],
            "expected_issues": s["expected_issues"],
        }
        for name, s in SCENARIOS.items()
    ]


def get_scenario_state(scenario: str) -> dict | None:
    """Get the fake state for a scenario without running diagnostics."""
    s = SCENARIOS.get(scenario)
    if not s:
        return None
    return s["generator"]()
