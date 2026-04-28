#!/usr/bin/env python3
"""
Planner — Rule-based planning engine for Tenazas runtime.

Takes a goal or problem and generates a structured plan with steps.
Each step has: action, target, requires_approval (bool), estimated_duration.
Uses templates for common plans. No LLM needed — pure rule-based.

Logs to logs/planner.log.
"""

import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "planner.log"

# ── Plan Templates ───────────────────────────────────────────────

PLAN_TEMPLATES = {
    "restart_sequence": {
        "description": "Restart a container safely",
        "steps": [
            {"action": "docker ps", "target": "{container}", "requires_approval": False,
             "estimated_duration": "5s", "description": "Check current container status"},
            {"action": "docker restart", "target": "{container}", "requires_approval": True,
             "estimated_duration": "30s", "description": "Restart the container"},
            {"action": "docker ps", "target": "{container}", "requires_approval": False,
             "estimated_duration": "5s", "description": "Verify container is running"},
        ],
    },
    "diagnostic_sequence": {
        "description": "Run diagnostics on infrastructure",
        "steps": [
            {"action": "check_containers", "target": "all", "requires_approval": False,
             "estimated_duration": "10s", "description": "List all container states"},
            {"action": "check_internet", "target": "8.8.8.8", "requires_approval": False,
             "estimated_duration": "5s", "description": "Test internet connectivity"},
            {"action": "check_vpn", "target": "gluetun", "requires_approval": False,
             "estimated_duration": "10s", "description": "Verify VPN status and country"},
            {"action": "check_ollama", "target": "notebook", "requires_approval": False,
             "estimated_duration": "5s", "description": "Check Ollama GPU backend"},
            {"action": "check_network", "target": "192.168.2.0/24", "requires_approval": False,
             "estimated_duration": "15s", "description": "Scan LAN devices"},
        ],
    },
    "vpn_recovery": {
        "description": "Recover VPN connectivity",
        "steps": [
            {"action": "check_vpn", "target": "gluetun", "requires_approval": False,
             "estimated_duration": "10s", "description": "Check current VPN status"},
            {"action": "docker restart", "target": "gluetun", "requires_approval": True,
             "estimated_duration": "30s", "description": "Restart gluetun container"},
            {"action": "wait", "target": "15s", "requires_approval": False,
             "estimated_duration": "15s", "description": "Wait for VPN to reconnect"},
            {"action": "check_vpn", "target": "gluetun", "requires_approval": False,
             "estimated_duration": "10s", "description": "Verify VPN is back and not leaking"},
        ],
    },
    "arr_stack_recovery": {
        "description": "Recover arr-stack containers",
        "steps": [
            {"action": "check_vpn", "target": "gluetun", "requires_approval": False,
             "estimated_duration": "10s", "description": "Ensure VPN is up first"},
            {"action": "docker restart", "target": "gluetun", "requires_approval": True,
             "estimated_duration": "30s", "description": "Restart gluetun (VPN gateway)"},
            {"action": "wait", "target": "15s", "requires_approval": False,
             "estimated_duration": "15s", "description": "Wait for gluetun to stabilize"},
            {"action": "docker restart", "target": "{container}", "requires_approval": True,
             "estimated_duration": "30s", "description": "Restart the affected container"},
            {"action": "docker ps", "target": "{container}", "requires_approval": False,
             "estimated_duration": "5s", "description": "Verify recovery"},
        ],
    },
    "ollama_failover": {
        "description": "Handle Ollama backend failure",
        "steps": [
            {"action": "check_ollama", "target": "notebook", "requires_approval": False,
             "estimated_duration": "5s", "description": "Check notebook GPU backend"},
            {"action": "check_ollama", "target": "nas", "requires_approval": False,
             "estimated_duration": "5s", "description": "Check NAS CPU fallback"},
            {"action": "update_status_file", "target": "/tmp/ollama-notebook.status",
             "requires_approval": False, "estimated_duration": "1s",
             "description": "Update Ollama status for routing"},
            {"action": "notify", "target": "telegram", "requires_approval": False,
             "estimated_duration": "5s", "description": "Notify about backend change"},
        ],
    },
}

# ── Problem-to-template mapping ──────────────────────────────────

PROBLEM_MAP = {
    "container_down": "restart_sequence",
    "vpn_down": "vpn_recovery",
    "vpn_leak": "vpn_recovery",
    "internet_lost": "diagnostic_sequence",
    "ollama_notebook_down": "ollama_failover",
    "arr_container_down": "arr_stack_recovery",
    "general_diagnostic": "diagnostic_sequence",
}



def _log(entry: dict):
    """Append a JSON line to planner.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def generate_plan(problem: str, context: dict = None) -> dict:
    """
    Generate a structured plan for a given problem.

    Args:
        problem: Problem type (e.g. 'container_down', 'vpn_leak')
        context: Optional dict with extra info (e.g. {'container': 'sonarr'})

    Returns:
        dict with: problem, template, description, steps[], generated_at
    """
    context = context or {}
    template_name = PROBLEM_MAP.get(problem)

    if not template_name:
        plan = _generate_generic_plan(problem, context)
    else:
        template = PLAN_TEMPLATES[template_name]
        steps = []
        for step in template["steps"]:
            resolved = {}
            for key, value in step.items():
                if isinstance(value, str):
                    resolved[key] = value.format(**context) if context else value
                else:
                    resolved[key] = value
            steps.append(resolved)

        plan = {
            "problem": problem,
            "template": template_name,
            "description": template["description"],
            "steps": steps,
            "generated_at": now().isoformat(),
            "requires_any_approval": any(s.get("requires_approval") for s in steps),
        }

    _log({
        "ts": now().isoformat(),
        "action": "generate_plan",
        "problem": problem,
        "template": plan.get("template", "generic"),
        "steps_count": len(plan.get("steps", [])),
    })

    return plan


def _generate_generic_plan(problem: str, context: dict) -> dict:
    """Generate a minimal generic plan for unknown problems."""
    return {
        "problem": problem,
        "template": "generic",
        "description": f"Generic investigation plan for: {problem}",
        "steps": [
            {"action": "diagnose", "target": problem, "requires_approval": False,
             "estimated_duration": "30s", "description": f"Investigate {problem}"},
            {"action": "notify", "target": "telegram", "requires_approval": False,
             "estimated_duration": "5s", "description": "Report findings"},
        ],
        "generated_at": now().isoformat(),
        "requires_any_approval": False,
    }


def list_templates() -> dict:
    """Return all available plan templates with descriptions."""
    return {name: t["description"] for name, t in PLAN_TEMPLATES.items()}


def get_plan_for_event(event: dict) -> dict:
    """
    Convenience function: given a runtime event dict, generate the appropriate plan.

    Args:
        event: An event dict from the runtime (must have 'type' key)

    Returns:
        A plan dict
    """
    etype = event.get("type", "")
    context = {}

    if event.get("container"):
        context["container"] = event["container"]

    # Map specific event types to problems
    if etype == "container_down":
        container = event.get("container", "")
        from . import docker_wrapper
        arr = {"gluetun", "qbittorrent", "sonarr", "radarr", "lidarr",
               "prowlarr", "seerr", "requestrr", "tautulli", "sabnzbd"}
        problem = "arr_container_down" if container in arr else "container_down"
    elif etype in PROBLEM_MAP:
        problem = etype
    else:
        problem = "general_diagnostic"

    return generate_plan(problem, context)
