#!/usr/bin/env python3
"""
Infrastructure Diagnostics — Rule-based system health analysis for Tenazas.

Analyzes:
- Container status (crash loops, unexpected exits)
- Network connectivity (latency, failures)
- VPN status (leaks, disconnections)
- Ollama availability
- Recent events for pattern correlation

Each rule produces: {problem, cause, severity, suggested_action}

SAFETY: Read-only analysis. Never modifies infrastructure.
"""

import json
from datetime import datetime, timedelta
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
MEMORY_FILE = BASE_DIR / "memory_log.json"
EVENTS_FILE = BASE_DIR / "events.json"
DIAGNOSTICS_LOG = BASE_DIR / "logs" / "diagnostics.log"

# Thresholds
CRASH_LOOP_THRESHOLD = 3      # restarts in lookback period
CRASH_LOOP_LOOKBACK_HOURS = 2
LATENCY_HIGH_MS = 200
VPN_EXPECTED_COUNTRIES = {"ES"}  # Spain via NordVPN
OLLAMA_REQUIRED = False          # Don't alert if notebook is simply off

# Severity levels
CRITICAL = "critical"
WARNING = "warning"
INFO = "info"


# ── Helpers ──────────────────────────────────────────────────────


def _now():
    return datetime.now().astimezone()


def _load_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _log_diagnostic(entry: dict):
    DIAGNOSTICS_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(DIAGNOSTICS_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Rule Engine ──────────────────────────────────────────────────


def _check_containers(state: dict, memory: list) -> list:
    """Detect container issues: down, crash loops."""
    issues = []
    containers = state.get("containers", [])

    for c in containers:
        name = c.get("name", "")
        status = c.get("status", "")
        up = c.get("up", True)

        # Skip protected containers
        if name.lower() in ("openclaw", "watchtower"):
            continue

        # Container down
        if not up:
            issues.append({
                "problem": f"Container '{name}' está caído",
                "cause": f"Status: {status}",
                "severity": WARNING,
                "suggested_action": f"Reiniciar {name}",
                "target": name,
                "rule": "container_down",
            })

    # Crash loop detection: count restart events in recent memory
    cutoff = _now() - timedelta(hours=CRASH_LOOP_LOOKBACK_HOURS)
    restart_counts = {}

    for entry in memory if isinstance(memory, list) else []:
        try:
            entry_dt = datetime.fromisoformat(entry["ts"])
        except (KeyError, ValueError):
            continue
        if entry_dt < cutoff:
            continue

        for result in entry.get("results", []):
            if result.get("action") == "docker.restart" and result.get("ok"):
                target = result.get("target", "")
                restart_counts[target] = restart_counts.get(target, 0) + 1

    for target, count in restart_counts.items():
        if count >= CRASH_LOOP_THRESHOLD:
            issues.append({
                "problem": f"Container '{target}' en crash loop ({count} restarts en {CRASH_LOOP_LOOKBACK_HOURS}h)",
                "cause": "Restarts recurrentes indican un problema subyacente",
                "severity": CRITICAL,
                "suggested_action": f"Revisar logs de {target}: docker logs {target} --tail 50",
                "target": target,
                "rule": "crash_loop",
            })

    return issues


def _check_network(state: dict) -> list:
    """Detect network issues: connectivity, latency."""
    issues = []
    internet = state.get("internet", {})

    if not internet.get("ok"):
        issues.append({
            "problem": "Sin conexión a internet",
            "cause": "Ping a 8.8.8.8 falló",
            "severity": CRITICAL,
            "suggested_action": "Verificar conexión ISP y router (192.168.2.1)",
            "target": "network",
            "rule": "internet_down",
        })
    elif internet.get("latency_ms") and internet["latency_ms"] > LATENCY_HIGH_MS:
        issues.append({
            "problem": f"Latencia alta: {internet['latency_ms']:.0f}ms",
            "cause": "Congestión de red o problema con ISP",
            "severity": WARNING,
            "suggested_action": "Ejecutar diagnóstico de red completo",
            "target": "network",
            "rule": "high_latency",
        })

    return issues


def _check_vpn(state: dict) -> list:
    """Detect VPN issues: down, leaks, wrong country."""
    issues = []
    vpn = state.get("vpn", {})

    if not vpn.get("ok"):
        issues.append({
            "problem": "VPN desconectada",
            "cause": "No se pudo obtener IP via proxy gluetun",
            "severity": CRITICAL,
            "suggested_action": "Reiniciar gluetun",
            "target": "gluetun",
            "rule": "vpn_down",
        })
        return issues

    country = vpn.get("country", "")
    if country == "AR":
        issues.append({
            "problem": f"VPN leak detectado — IP argentina ({vpn.get('ip', '?')})",
            "cause": "gluetun no está enrutando tráfico por NordVPN",
            "severity": CRITICAL,
            "suggested_action": "Reiniciar gluetun inmediatamente",
            "target": "gluetun",
            "rule": "vpn_leak",
        })
    elif country and country not in VPN_EXPECTED_COUNTRIES and country != "??":
        issues.append({
            "problem": f"VPN en país inesperado: {country} (esperado: ES)",
            "cause": "NordVPN puede haber cambiado de servidor",
            "severity": WARNING,
            "suggested_action": "Verificar configuración de gluetun",
            "target": "gluetun",
            "rule": "vpn_wrong_country",
        })

    return issues


def _check_ollama(state: dict) -> list:
    """Detect Ollama issues."""
    issues = []

    notebook = state.get("ollama_notebook", {})
    nas = state.get("ollama_nas", {})

    if not notebook.get("ok") and not nas.get("ok"):
        issues.append({
            "problem": "Todos los backends Ollama offline",
            "cause": "Ni notebook (GPU) ni NAS (CPU) responden",
            "severity": CRITICAL,
            "suggested_action": "Verificar Ollama en notebook (192.168.2.6) y NAS",
            "target": "ollama",
            "rule": "ollama_all_down",
        })
    elif not notebook.get("ok"):
        issues.append({
            "problem": "Ollama notebook offline — usando fallback",
            "cause": "Notebook (192.168.2.6) no responde",
            "severity": INFO,
            "suggested_action": "Verificar que la notebook esté encendida con Ollama corriendo",
            "target": "ollama",
            "rule": "ollama_notebook_down",
        })

    return issues


def _check_device_count(state: dict, prev_state: dict) -> list:
    """Detect unusual changes in network device count."""
    issues = []
    curr_count = state.get("network_devices", {}).get("count", 0)
    prev_count = prev_state.get("network_devices", {}).get("count", 0)

    if prev_count > 0 and curr_count == 0:
        issues.append({
            "problem": "No se detectaron dispositivos en la red",
            "cause": "nmap scan falló o red desconectada",
            "severity": WARNING,
            "suggested_action": "Ejecutar scan de red manual",
            "target": "network",
            "rule": "no_devices",
        })
    elif abs(curr_count - prev_count) > 5:
        issues.append({
            "problem": f"Cambio abrupto en dispositivos: {prev_count} → {curr_count}",
            "cause": "Posible problema de red o dispositivos nuevos/caídos",
            "severity": INFO,
            "suggested_action": "Revisar scan de red para identificar cambios",
            "target": "network",
            "rule": "device_count_anomaly",
        })

    return issues


# ── Main API ─────────────────────────────────────────────────────


def run_diagnostics(state: dict | None = None) -> list:
    """Run all diagnostic rules against current system state.

    Args:
        state: system state dict. If None, reads from state.json.

    Returns: list of issue dicts sorted by severity.
    """
    if state is None:
        state = _load_json(STATE_FILE, {})

    memory = _load_json(MEMORY_FILE, [])
    prev_state = {}  # Could be loaded from a previous snapshot if needed

    issues = []
    issues.extend(_check_containers(state, memory))
    issues.extend(_check_network(state))
    issues.extend(_check_vpn(state))
    issues.extend(_check_ollama(state))
    issues.extend(_check_device_count(state, prev_state))

    # Sort by severity
    severity_order = {CRITICAL: 0, WARNING: 1, INFO: 2}
    issues.sort(key=lambda i: severity_order.get(i["severity"], 9))

    _log_diagnostic({
        "type": "diagnostics",
        "issues_count": len(issues),
        "critical": sum(1 for i in issues if i["severity"] == CRITICAL),
        "warning": sum(1 for i in issues if i["severity"] == WARNING),
        "info": sum(1 for i in issues if i["severity"] == INFO),
        "issues": [{"problem": i["problem"], "severity": i["severity"]} for i in issues],
    })

    return issues


def format_diagnostic_report(issues: list) -> str:
    """Format diagnostics for Telegram/LLM output."""
    if not issues:
        return "Sistema saludable — sin problemas detectados."

    severity_icons = {CRITICAL: "CRIT", WARNING: "WARN", INFO: "INFO"}
    lines = ["Diagnóstico de infraestructura:\n"]

    for i, issue in enumerate(issues, 1):
        icon = severity_icons.get(issue["severity"], "?")
        lines.append(f"  [{icon}] {issue['problem']}")
        lines.append(f"         Causa: {issue['cause']}")
        lines.append(f"         Acción: {issue['suggested_action']}")
        lines.append("")

    return "\n".join(lines)


def quick_health_check() -> dict:
    """Fast health summary for embedding in other outputs.

    Returns: {healthy: bool, summary: str, critical_count: int, issues: list}
    """
    issues = run_diagnostics()
    critical = [i for i in issues if i["severity"] == CRITICAL]

    return {
        "healthy": len(critical) == 0,
        "summary": "OK" if not issues else f"{len(issues)} issues ({len(critical)} critical)",
        "critical_count": len(critical),
        "issues": issues,
    }
