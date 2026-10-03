#!/usr/bin/env python3
"""Agent Security - Specialized in local security posture.

Ownership: exposed services, admin panels, SSH, cameras, network ports,
credentials, and security drift. Read-only by design: it observes and
recommends, but does not execute changes directly.
"""

import json
from pathlib import Path

from agent_protocol import Observation, Recommendation
from runtime_utils import load_json, now, save_json

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "security_memory.json"
STATUS_FILE = AGENT_DIR / "security_status.json"
LOG_FILE = BASE_DIR / "logs" / "agent_security.log"
RUNTIME_STATE_FILE = BASE_DIR / "state.json"
DEVICE_INVENTORY_FILE = BASE_DIR / "device_inventory.json"

OWNERSHIP = {
    "security", "seguridad", "audit", "auditoria", "auditoría", "ssh",
    "firewall", "puerto", "port", "ports", "exposed", "expuesto",
    "credencial", "credential", "password", "token", "secret", "camara",
    "camera", "nvr", "admin", "portainer", "grafana", "n8n",
}
PREFERRED_ROLE = "reason"

ADMIN_PORTS = {
    22: "ssh",
    23: "telnet",
    80: "http",
    443: "https",
    3000: "grafana",
    5000: "synology",
    5001: "synology-https",
    5678: "n8n",
    8000: "gluetun-api",
    8080: "http-alt",
    8100: "chromadb",
    8443: "https-alt",
    8888: "proxy",
    8890: "searxng",
    9000: "portainer",
    9090: "prometheus",
    11434: "ollama",
    18789: "openclaw",
}
HIGH_RISK_PORTS = {23, 3389, 5900}
ADMIN_CONTAINERS = {"grafana", "n8n", "portainer", "prometheus", "searxng", "chromadb"}


def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(e, default=str) + "\n")
    except OSError:
        pass


def owns(topic: str) -> bool:
    text = topic.lower()
    return any(k in text for k in OWNERSHIP)


def _device_name(device: dict) -> str:
    return device.get("name") or device.get("ip") or device.get("id") or "unknown"


def _audit_devices() -> dict:
    devices = load_json(str(DEVICE_INVENTORY_FILE), [])
    if not isinstance(devices, list):
        devices = []

    findings = []
    admin_surfaces = []
    high_risk = []
    unknown_with_ports = []

    for device in devices:
        ports = device.get("ports", []) or []
        if not isinstance(ports, list):
            continue
        name = _device_name(device)
        category = str(device.get("category", "")).lower()
        ip = device.get("ip", "")

        for port in ports:
            try:
                port_num = int(port)
            except (TypeError, ValueError):
                continue

            if port_num in ADMIN_PORTS:
                item = {
                    "device": name,
                    "ip": ip,
                    "port": port_num,
                    "service": ADMIN_PORTS[port_num],
                    "category": category,
                }
                admin_surfaces.append(item)
                severity = "high" if port_num in HIGH_RISK_PORTS else "medium"
                findings.append({
                    "type": "admin_surface",
                    "severity": severity,
                    **item,
                })

            if port_num in HIGH_RISK_PORTS:
                high_risk.append({"device": name, "ip": ip, "port": port_num})

        if ports and not device.get("name"):
            unknown_with_ports.append({"ip": ip, "ports": ports[:10]})

    return {
        "devices_total": len(devices),
        "findings": findings,
        "admin_surfaces": admin_surfaces,
        "high_risk": high_risk,
        "unknown_with_ports": unknown_with_ports,
    }


def _audit_containers() -> dict:
    state = load_json(str(RUNTIME_STATE_FILE), {})
    containers = state.get("containers", []) if isinstance(state, dict) else []
    if not isinstance(containers, list):
        containers = []

    admin_running = []
    stopped_admin = []
    for container in containers:
        name = str(container.get("name", "")).lower()
        if name not in ADMIN_CONTAINERS:
            continue
        item = {
            "name": container.get("name", name),
            "status": container.get("status", ""),
            "up": bool(container.get("up", False)),
        }
        if item["up"]:
            admin_running.append(item)
        else:
            stopped_admin.append(item)

    return {
        "containers_total": len(containers),
        "admin_running": admin_running,
        "stopped_admin": stopped_admin,
    }


def assess() -> dict:
    devices = _audit_devices()
    containers = _audit_containers()
    findings = list(devices.get("findings", []))

    for item in containers.get("stopped_admin", []):
        findings.append({
            "type": "admin_container_down",
            "severity": "medium",
            "container": item["name"],
            "status": item["status"],
        })

    high = sum(1 for f in findings if f.get("severity") == "high")
    medium = sum(1 for f in findings if f.get("severity") == "medium")
    health = "ok"
    if high:
        health = "review"
    elif medium:
        health = "watch"

    status = {
        "agent": "security",
        "ts": now().isoformat(),
        "health": health,
        "findings": findings,
        "devices": devices,
        "containers": containers,
        "summary": f"Security: {len(findings)} findings ({high} high, {medium} medium)",
    }
    save_json(str(STATUS_FILE), status)
    _log({"action": "assess", "health": health, "findings": len(findings)})
    return status


def _observe_ports() -> dict:
    status = assess()
    surfaces = status.get("devices", {}).get("admin_surfaces", [])
    high_risk = status.get("devices", {}).get("high_risk", [])
    if high_risk:
        summary = f"Security ports: {len(high_risk)} high-risk ports detected"
        confidence = 0.85
    elif surfaces:
        summary = f"Security ports: {len(surfaces)} admin surfaces visible on LAN"
        confidence = 0.8
    else:
        summary = "Security ports: no risky ports found in inventory"
        confidence = 0.75
    return {
        "summary": summary,
        "confidence": confidence,
        "sub": "ports",
        "issues": status.get("findings", []),
        "raw": status,
    }


def _observe_admin_services() -> dict:
    status = assess()
    running = status.get("containers", {}).get("admin_running", [])
    stopped = status.get("containers", {}).get("stopped_admin", [])
    parts = [item["name"] for item in running[:6]]
    summary = "Security admin: "
    summary += ", ".join(parts) if parts else "no admin containers found"
    if stopped:
        summary += f"; {len(stopped)} admin container stopped"
    return {
        "summary": summary,
        "confidence": 0.8,
        "sub": "admin_services",
        "issues": status.get("findings", []),
        "raw": status,
    }


def _observe_full() -> dict:
    status = assess()
    findings = status.get("findings", [])
    if findings:
        top = findings[0]
        label = top.get("service") or top.get("type") or top.get("container", "finding")
        summary = f"{status['summary']} - top: {label}"
    else:
        summary = "Security: no findings from current inventory"
    return {
        "summary": summary,
        "confidence": 0.8 if findings else 0.7,
        "sub": "full",
        "issues": findings,
        "raw": status,
    }


def _build_recommendations(data: dict) -> list[Recommendation]:
    recs = []
    seen = set()
    for issue in data.get("issues", [])[:5]:
        issue_type = issue.get("type", "security_review")
        service = issue.get("service") or issue.get("container") or issue.get("device") or "security"
        key = (issue_type, service)
        if key in seen:
            continue
        seen.add(key)
        risk = "high" if issue.get("severity") == "high" else "medium"
        recs.append(Recommendation(
            action="review_security_posture",
            target=str(service),
            risk_level=risk,
            requires_approval=True,
            rationale=f"{issue_type}: {service}",
        ))
    return recs


def handle(query: str, correlation_id: str = None):
    """Returns (Observation, list[Recommendation])."""
    q = query.lower()
    if any(k in q for k in ("puerto", "port", "exposed", "expuesto", "firewall")):
        data = _observe_ports()
    elif any(k in q for k in ("admin", "grafana", "portainer", "n8n", "prometheus")):
        data = _observe_admin_services()
    else:
        data = _observe_full()

    obs = Observation(
        agent_name="security",
        domain="security",
        summary=data.get("summary", "Security: sin datos"),
        confidence=data.get("confidence", 0.7),
        details=data,
    )
    recs = _build_recommendations(data)
    try:
        state = load_json(str(STATUS_FILE), {})
        state.update(data)
        state["observation"] = obs.to_dict()
        save_json(str(STATUS_FILE), state)
    except Exception:
        pass
    _log({
        "action": "handle",
        "query": query[:80],
        "sub": data.get("sub", "full"),
        "confidence": obs.confidence,
        "recommendations": len(recs),
        "correlation_id": correlation_id,
    })
    return (obs, recs)


def handle_query(query: str) -> str:
    """Backward-compat wrapper. Returns string summary."""
    obs, _recs = handle(query)
    return obs.summary


def get_memory() -> dict:
    return load_json(str(MEMORY_FILE), {"events": []})


def save_memory_event(event: dict):
    mem = get_memory()
    mem["events"].append({**event, "ts": now().isoformat()})
    mem["events"] = mem["events"][-100:]
    save_json(str(MEMORY_FILE), mem)
