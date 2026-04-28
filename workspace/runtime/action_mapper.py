#!/usr/bin/env python3
"""
Action Mapper — Maps detected problems to Tenazas skill actions.

Takes diagnostic results or RAG context and produces structured
action suggestions that map to existing skills.

SAFETY: Never executes actions. Only suggests them.
All actions require explicit approval from Sebas.
"""

import json
import re
from datetime import datetime
from pathlib import Path
from runtime_utils import now

DIAGNOSTICS_LOG = Path(__file__).parent / "logs" / "diagnostics.log"

# ── Action Registry ──────────────────────────────────────────────
# Maps problem patterns to skill actions.
# Each entry: pattern → {action, skill, description, priority, safe}

ACTION_RULES = [
    # VPN / Gluetun
    {
        "patterns": ["vpn_down", "vpn_leak", "gluetun_down", "gluetun failure",
                      "vpn disconnected", "ip argentina", "country.*AR"],
        "action": "docker.restart",
        "target": "gluetun",
        "skill": "container-restart",
        "description": "Reiniciar gluetun (VPN gateway)",
        "priority": 1,  # VPN is critical
        "safe": True,
    },
    # Arr-stack containers
    {
        "patterns": ["sonarr.*down", "sonarr.*crash", "sonarr.*exited",
                      "container_down.*sonarr"],
        "action": "docker.restart",
        "target": "sonarr",
        "skill": "container-restart",
        "description": "Reiniciar Sonarr",
        "priority": 2,
        "safe": True,
    },
    {
        "patterns": ["radarr.*down", "radarr.*crash", "radarr.*exited",
                      "container_down.*radarr"],
        "action": "docker.restart",
        "target": "radarr",
        "skill": "container-restart",
        "description": "Reiniciar Radarr",
        "priority": 2,
        "safe": True,
    },
    {
        "patterns": ["qbittorrent.*down", "qbittorrent.*crash",
                      "container_down.*qbittorrent"],
        "action": "docker.restart",
        "target": "qbittorrent",
        "skill": "container-restart",
        "description": "Reiniciar qBittorrent",
        "priority": 2,
        "safe": True,
    },
    # Generic arr-stack
    {
        "patterns": ["container_down", "container.*crash", "container.*exited"],
        "action": "docker.restart",
        "target": "{container}",
        "skill": "container-restart",
        "description": "Reiniciar container caído",
        "priority": 3,
        "safe": True,
    },
    # Network
    {
        "patterns": ["internet.*slow", "latency.*high", "ping.*fail",
                      "network.*problem", "internet_lost", "dns.*fail"],
        "action": "run_diagnostic",
        "target": "network",
        "skill": "internet-health",
        "description": "Ejecutar diagnóstico de red completo",
        "priority": 2,
        "safe": True,
    },
    {
        "patterns": ["network.*scan", "device.*count", "unknown.*device",
                      "new.*device"],
        "action": "run_scan",
        "target": "network",
        "skill": "network_scan",
        "description": "Escanear red LAN con nmap",
        "priority": 3,
        "safe": True,
    },
    # Ollama / LLM
    {
        "patterns": ["ollama.*down", "ollama.*offline", "notebook.*offline",
                      "model.*timeout", "inference.*fail",
                      "ollama_notebook_down"],
        "action": "check_status",
        "target": "ollama",
        "skill": "model-manager",
        "description": "Verificar estado de Ollama y modelos",
        "priority": 2,
        "safe": True,
    },
    # Infrastructure
    {
        "patterns": ["infra.*status", "health.*check", "system.*status",
                      "docker.*status"],
        "action": "run_diagnostic",
        "target": "infra",
        "skill": "infra-monitor",
        "description": "Ejecutar check de infraestructura completo",
        "priority": 3,
        "safe": True,
    },
    # Smart Home
    {
        "patterns": ["luz.*apag", "luz.*encend", "hue.*off", "hue.*on",
                      "luces"],
        "action": "hue_control",
        "target": "hue",
        "skill": "smart-home",
        "description": "Control de luces Hue",
        "priority": 4,
        "safe": True,
    },
    # Backup
    {
        "patterns": ["backup.*fail", "backup.*status", "hyper.*backup"],
        "action": "check_status",
        "target": "backup",
        "skill": "backup-monitor",
        "description": "Verificar estado de backups",
        "priority": 3,
        "safe": True,
    },
]


# ── Helpers ──────────────────────────────────────────────────────



def _log_action(entry: dict):
    DIAGNOSTICS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(DIAGNOSTICS_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Core API ─────────────────────────────────────────────────────


def match_actions(text: str, context: dict | None = None) -> list:
    """Match problem text against action rules.

    Args:
        text: problem description, query, or diagnostic output
        context: optional dict with extra info (e.g., {"container": "sonarr"})

    Returns: list of matched actions, sorted by priority.
    """
    text_lower = text.lower()
    matched = []
    seen_targets = set()

    for rule in ACTION_RULES:
        for pattern in rule["patterns"]:
            if re.search(pattern, text_lower):
                target = rule["target"]
                # Replace {container} placeholder from context
                if "{container}" in target and context:
                    target = context.get("container", target)

                # Deduplicate by target
                if target in seen_targets:
                    break
                seen_targets.add(target)

                matched.append({
                    "action": rule["action"],
                    "target": target,
                    "skill": rule["skill"],
                    "description": rule["description"],
                    "priority": rule["priority"],
                    "safe": rule["safe"],
                    "matched_pattern": pattern,
                })
                break  # One match per rule is enough

    matched.sort(key=lambda a: a["priority"])
    return matched


def map_events_to_actions(events: list) -> list:
    """Map runtime events to suggested actions.

    Args:
        events: list of event dicts from runtime (e.g., container_down, vpn_leak)

    Returns: deduplicated action suggestions.
    """
    all_actions = []
    seen = set()

    for event in events:
        etype = event.get("type", "")
        container = event.get("container", "")
        text = f"{etype} {container}".strip()

        actions = match_actions(text, {"container": container})
        for a in actions:
            key = (a["action"], a["target"])
            if key not in seen:
                seen.add(key)
                a["source_event"] = etype
                all_actions.append(a)

    all_actions.sort(key=lambda a: a["priority"])

    _log_action({
        "type": "action_mapping",
        "events_count": len(events),
        "actions_suggested": len(all_actions),
        "actions": [{"action": a["action"], "target": a["target"]} for a in all_actions],
    })

    return all_actions


def map_diagnostics_to_actions(diagnostics: list) -> list:
    """Map diagnostic results to suggested actions.

    Args:
        diagnostics: list of diagnostic dicts from infra_diagnostics

    Returns: action suggestions with diagnostic context.
    """
    all_actions = []
    seen = set()

    for diag in diagnostics:
        problem = diag.get("problem", "")
        cause = diag.get("cause", "")
        text = f"{problem} {cause}"

        actions = match_actions(text)
        for a in actions:
            key = (a["action"], a["target"])
            if key not in seen:
                seen.add(key)
                a["diagnostic_problem"] = problem
                a["diagnostic_cause"] = cause
                all_actions.append(a)

    all_actions.sort(key=lambda a: a["priority"])
    return all_actions


def format_suggestions(actions: list) -> str:
    """Format action suggestions for Telegram/LLM output.

    Returns human-readable text. Never auto-executes.
    """
    if not actions:
        return ""

    lines = ["\nAcciones sugeridas:"]
    for i, a in enumerate(actions, 1):
        safe_tag = "" if a["safe"] else " [requiere revisión]"
        lines.append(f"  {i}. {a['description']} (skill: {a['skill']}){safe_tag}")

    lines.append("\n⚠ Ninguna acción se ejecuta automáticamente. Aprobá explícitamente.")
    return "\n".join(lines)
