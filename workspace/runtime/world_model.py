#!/usr/bin/env python3
"""
World Model — Infrastructure representation for Tenazas runtime.

Represents the known state of: containers, devices, network topology,
services, skills, and LLM models. Loads live data from state.json
and supplements with hardcoded known devices/services.

Logs to logs/world_model.log.
"""

import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "state.json"
LOG_FILE = BASE_DIR / "logs" / "world_model.log"

SUBNET = "192.168.2.0/24"

# ── Known devices (hardcoded from CLAUDE.md) ─────────────────────

KNOWN_DEVICES = {
    "192.168.2.1": {"name": "Router", "type": "network"},
    "192.168.2.2": {"name": "NAS DS923+", "type": "nas"},
    "192.168.2.3": {"name": "NAS Backup", "type": "nas"},
    "192.168.2.11": {"name": "Hue Bridge", "type": "smart_home"},
    "192.168.2.46": {"name": "Samsung M8", "type": "display"},
    "192.168.2.6": {"name": "HomeLab", "type": "compute"},
}

# ── Known services ───────────────────────────────────────────────

KNOWN_SERVICES = {
    "ollama_notebook": {
        "url": "http://192.168.2.6:11434",
        "type": "llm_inference",
        "host": "192.168.2.6",
        "description": "Ollama GPU (RTX 3090)",
    },
    "ollama_nas": {
        "url": "http://127.0.0.1:11434",
        "type": "llm_inference",
        "host": "192.168.2.2",
        "description": "Ollama CPU (Ryzen, fallback)",
    },
    "chromadb": {
        "url": "http://192.168.2.2:8100",
        "type": "vector_db",
        "host": "192.168.2.2",
        "description": "ChromaDB for RAG",
    },
    "hue_bridge": {
        "url": "http://192.168.2.11/api",
        "type": "smart_home",
        "host": "192.168.2.11",
        "description": "Philips Hue (8 lights)",
    },
    "gluetun_proxy": {
        "url": "http://192.168.2.2:8888",
        "type": "vpn_proxy",
        "host": "192.168.2.2",
        "description": "NordVPN via gluetun (Spain)",
    },
    "samsung_m8": {
        "url": "ws://192.168.2.46:8001",
        "type": "display",
        "host": "192.168.2.46",
        "description": "Samsung M8 WebSocket control",
    },
}

# ── Known Ollama models ──────────────────────────────────────────

KNOWN_MODELS = {
    "qwen2.5:3b": {"params": "3B", "use": "system, logs, JSON", "backend": "notebook_gpu"},
    "qwen2.5:7b": {"params": "7B", "use": "reasoning, analysis", "backend": "notebook_gpu"},
    "qwen3:8b": {"params": "8B", "use": "alternative", "backend": "notebook_gpu"},
    "phi4-mini": {"params": "~4B", "use": "fallback lightweight", "backend": "nas_cpu_or_notebook"},
    "nomic-embed-text": {"params": "-", "use": "embeddings RAG", "backend": "notebook_gpu"},
}

SKILL_COUNT = 22


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    """Append a JSON line to world_model.log."""
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_state() -> dict:
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def get_entity(entity_type: str, name: str = "") -> dict | list:
    """
    Get an entity or list of entities from the world model.

    Args:
        entity_type: One of 'container', 'device', 'service', 'model', 'skill'
        name: Optional specific entity name/key

    Returns:
        A dict for a specific entity, or a list of all entities of that type
    """
    state = _load_state()

    if entity_type == "container":
        containers = state.get("containers", [])
        if name:
            for c in containers:
                if c.get("name") == name:
                    return c
            return {}
        return containers

    elif entity_type == "device":
        if name:
            for ip, info in KNOWN_DEVICES.items():
                if ip == name or info["name"].lower() == name.lower():
                    return {"ip": ip, **info}
            return {}
        return [{"ip": ip, **info} for ip, info in KNOWN_DEVICES.items()]

    elif entity_type == "service":
        if name:
            return KNOWN_SERVICES.get(name, {})
        return dict(KNOWN_SERVICES)

    elif entity_type == "model":
        if name:
            return KNOWN_MODELS.get(name, {})
        return dict(KNOWN_MODELS)

    elif entity_type == "skill":
        return {"count": SKILL_COUNT}

    _log({"ts": _now().isoformat(), "action": "get_entity",
          "type": entity_type, "name": name, "error": "unknown entity type"})
    return {}


def get_status() -> dict:
    """
    Get a complete status snapshot of the world model.

    Returns:
        dict with containers, internet, vpn, ollama, devices, services, models
    """
    state = _load_state()

    containers = state.get("containers", [])
    up_count = sum(1 for c in containers if c.get("up"))
    down_count = len(containers) - up_count

    status = {
        "ts": _now().isoformat(),
        "containers": {
            "total": len(containers),
            "up": up_count,
            "down": down_count,
            "down_names": [c["name"] for c in containers if not c.get("up")],
        },
        "internet": state.get("internet", {}),
        "vpn": state.get("vpn", {}),
        "ollama_notebook": state.get("ollama_notebook", {}),
        "ollama_nas": state.get("ollama_nas", {}),
        "network_devices": state.get("network_devices", {}),
        "known_devices": len(KNOWN_DEVICES),
        "known_services": len(KNOWN_SERVICES),
        "known_models": len(KNOWN_MODELS),
        "skills": SKILL_COUNT,
    }

    _log({"ts": _now().isoformat(), "action": "get_status"})
    return status


def get_topology() -> dict:
    """
    Get the network topology representation.

    Returns:
        dict describing the subnet, devices, and their roles
    """
    topology = {
        "subnet": SUBNET,
        "gateway": "192.168.2.1",
        "primary_nas": "192.168.2.2",
        "backup_nas": "192.168.2.3",
        "compute_node": "192.168.2.6",
        "devices": KNOWN_DEVICES,
        "services": {name: {"url": svc["url"], "host": svc["host"]}
                     for name, svc in KNOWN_SERVICES.items()},
    }

    _log({"ts": _now().isoformat(), "action": "get_topology"})
    return topology
