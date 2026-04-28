#!/usr/bin/env python3
"""
Runbook Generator — Creates reusable repair runbooks for recurring issues.

When Tenazas detects a recurring infrastructure problem, generates a
structured repair runbook with step-by-step instructions.

Storage: workspace/runtime/runbooks/
Safety: Runbooks are suggestions only. Never auto-executed.
"""

import json
import hashlib
from datetime import datetime
from pathlib import Path
from runtime_utils import now

try:
    from runtime_config import GLUETUN_PROXY, HOMELAB_IP, LAN_SUBNET, OLLAMA_GPU_TAGS_URL, ROUTER_IP
except ImportError:
    from workspace.runtime.runtime_config import GLUETUN_PROXY, HOMELAB_IP, LAN_SUBNET, OLLAMA_GPU_TAGS_URL, ROUTER_IP

BASE_DIR = Path(__file__).parent
RUNBOOKS_DIR = BASE_DIR / "runbooks"
TELEMETRY_LOG = BASE_DIR / "logs" / "runbooks.log"



def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Runbook Templates ────────────────────────────────────────────
# Each template: problem_type → steps + commands

RUNBOOK_TEMPLATES = {
    "container_crash_loop": {
        "title": "Container en crash loop",
        "severity": "critical",
        "steps": [
            {"action": "Verificar logs del container", "command": "docker logs {target} --tail 50"},
            {"action": "Verificar uso de disco", "command": "df -h /volume1"},
            {"action": "Verificar estado de servicios dependientes", "command": "docker ps --filter 'status=running' --format '{{{{.Names}}}}'"},
            {"action": "Reiniciar el container", "command": "docker restart {target}", "requires_approval": True},
            {"action": "Verificar que arranco correctamente", "command": "docker ps --filter 'name={target}'"},
            {"action": "Si persiste, reiniciar containers dependientes", "command": "docker restart gluetun", "requires_approval": True},
        ],
    },
    "vpn_failure": {
        "title": "VPN desconectada o leak detectado",
        "severity": "critical",
        "steps": [
            {"action": "Verificar estado de gluetun", "command": "docker logs gluetun --tail 30"},
            {"action": "Verificar IP publica via proxy", "command": f"curl -sf -x {GLUETUN_PROXY} https://ifconfig.me"},
            {"action": "Verificar pais de la IP", "command": "curl -sf http://ip-api.com/json/$(curl -sf -x {GLUETUN_PROXY} https://ifconfig.me)?fields=countryCode"},
            {"action": "Reiniciar gluetun", "command": "docker restart gluetun", "requires_approval": True},
            {"action": "Esperar 30s y reverificar VPN", "command": f"sleep 30 && curl -sf -x {GLUETUN_PROXY} https://ifconfig.me"},
            {"action": "Si persiste, verificar config de NordVPN", "command": "docker exec gluetun cat /gluetun/servers.json | head -5"},
        ],
    },
    "network_failure": {
        "title": "Problema de red / Internet caido",
        "severity": "critical",
        "steps": [
            {"action": "Verificar conectividad basica", "command": "ping -c 3 8.8.8.8"},
            {"action": "Verificar DNS", "command": "nslookup google.com"},
            {"action": "Verificar gateway", "command": f"ping -c 1 {ROUTER_IP}"},
            {"action": "Verificar interfaces de red", "command": "ip addr show | grep 'inet '"},
            {"action": "Si el router responde pero no hay internet, contactar ISP"},
        ],
    },
    "ollama_offline": {
        "title": "Ollama offline (notebook o NAS)",
        "severity": "warning",
        "steps": [
            {"action": "Verificar notebook GPU", "command": f"curl -sf --max-time 5 {OLLAMA_GPU_TAGS_URL}"},
            {"action": "Verificar NAS CPU", "command": "curl -sf --max-time 5 http://127.0.0.1:11434/api/tags"},
            {"action": "Verificar que la notebook esta encendida y accesible", "command": f"ping -c 1 -W 3 {HOMELAB_IP}"},
            {"action": "Si la notebook responde pero Ollama no, reiniciar servicio Ollama en la notebook"},
            {"action": "Verificar modelos disponibles despues del fix", "command": f"curl -sf {OLLAMA_GPU_TAGS_URL} | python3 -c 'import sys,json; print(json.load(sys.stdin))'"},
        ],
    },
    "container_down": {
        "title": "Container caido",
        "severity": "warning",
        "steps": [
            {"action": "Verificar estado actual", "command": "docker ps -a --filter 'name={target}'"},
            {"action": "Verificar logs", "command": "docker logs {target} --tail 20"},
            {"action": "Reiniciar container", "command": "docker restart {target}", "requires_approval": True},
            {"action": "Verificar que arranco", "command": "docker ps --filter 'name={target}'"},
        ],
    },
    "high_latency": {
        "title": "Latencia alta de red",
        "severity": "info",
        "steps": [
            {"action": "Medir latencia a multiples destinos", "command": "ping -c 5 8.8.8.8"},
            {"action": "Verificar carga de red", "command": f"nmap -sn {LAN_SUBNET} | grep 'Host is up' | wc -l"},
            {"action": "Verificar si VPN afecta velocidad", "command": f"curl -sf --max-time 10 -x {GLUETUN_PROXY} https://ifconfig.me"},
            {"action": "Si es persistente, considerar cambiar servidor VPN"},
        ],
    },
}


# ── Core API ─────────────────────────────────────────────────────


def _classify_problem(problem: str, target: str = "",
                      patterns: list | None = None) -> str:
    """Classify a problem into a runbook template type."""
    problem_lower = problem.lower()

    if "crash loop" in problem_lower or (
        patterns and any(p.get("event") == "restart_executed" for p in patterns)
    ):
        return "container_crash_loop"
    if "vpn" in problem_lower or "leak" in problem_lower or "gluetun" in target:
        return "vpn_failure"
    if "internet" in problem_lower or "network" in problem_lower or "latencia" in problem_lower:
        if "latencia" in problem_lower or "laten" in problem_lower:
            return "high_latency"
        return "network_failure"
    if "ollama" in problem_lower or "notebook" in problem_lower:
        return "ollama_offline"
    if "container" in problem_lower or "caido" in problem_lower or "down" in problem_lower:
        return "container_down"

    return "container_down"  # Default


def generate_runbook(
    problem: str,
    target: str = "",
    diagnostic_data: dict | None = None,
    patterns: list | None = None,
) -> dict:
    """Generate a repair runbook for a detected issue.

    Args:
        problem: problem description
        target: affected container/service name
        diagnostic_data: output from infra_diagnostics
        patterns: relevant patterns from infra_memory

    Returns: structured runbook dict
    """
    problem_type = _classify_problem(problem, target, patterns)
    template = RUNBOOK_TEMPLATES.get(problem_type, RUNBOOK_TEMPLATES["container_down"])

    # Fill in target placeholders
    steps = []
    for step in template["steps"]:
        filled = {**step}
        if "command" in filled:
            filled["command"] = filled["command"].format(target=target or "unknown")
        steps.append(filled)

    # Generate unique ID for this runbook
    rb_id = hashlib.md5(f"{problem_type}:{target}".encode()).hexdigest()[:8]

    runbook = {
        "id": rb_id,
        "problem_type": problem_type,
        "title": template["title"],
        "severity": template["severity"],
        "target": target,
        "problem": problem,
        "steps": steps,
        "generated_at": now().isoformat(),
        "pattern_context": [
            {"pattern": p["pattern"], "confidence": p["confidence"]}
            for p in (patterns or [])
        ],
        "reusable": True,
    }

    # Save to runbooks directory
    _save_runbook(runbook)

    _log({
        "type": "runbook_generated",
        "id": rb_id,
        "problem_type": problem_type,
        "target": target,
    })

    return runbook


def _save_runbook(runbook: dict):
    """Save runbook to disk for reuse."""
    RUNBOOKS_DIR.mkdir(parents=True, exist_ok=True)
    path = RUNBOOKS_DIR / f"{runbook['id']}.json"
    with open(path, "w") as f:
        json.dump(runbook, f, indent=2, default=str)


def get_runbook(problem_type: str, target: str = "") -> dict | None:
    """Retrieve an existing runbook by problem type and target."""
    rb_id = hashlib.md5(f"{problem_type}:{target}".encode()).hexdigest()[:8]
    path = RUNBOOKS_DIR / f"{rb_id}.json"
    if path.exists():
        try:
            with open(path) as f:
                return json.load(f)
        except json.JSONDecodeError:
            pass
    return None


def list_runbooks() -> list:
    """List all saved runbooks."""
    if not RUNBOOKS_DIR.exists():
        return []
    runbooks = []
    for path in sorted(RUNBOOKS_DIR.glob("*.json")):
        try:
            with open(path) as f:
                rb = json.load(f)
            runbooks.append({
                "id": rb["id"],
                "title": rb["title"],
                "target": rb["target"],
                "severity": rb["severity"],
                "generated_at": rb["generated_at"],
            })
        except (json.JSONDecodeError, KeyError):
            continue
    return runbooks


def format_runbook(runbook: dict) -> str:
    """Format a runbook for Telegram/LLM output."""
    lines = [
        f"Runbook: {runbook['title']}",
        f"Target: {runbook.get('target', 'N/A')}",
        f"Severidad: {runbook['severity']}",
        "",
    ]

    for i, step in enumerate(runbook["steps"], 1):
        approval = " [requiere aprobacion]" if step.get("requires_approval") else ""
        lines.append(f"  {i}. {step['action']}{approval}")
        if "command" in step:
            lines.append(f"     $ {step['command']}")

    lines.append("")
    lines.append("Ninguna accion se ejecuta automaticamente.")
    return "\n".join(lines)
