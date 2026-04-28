#!/usr/bin/env python3
"""
Knowledge Graph — Structured graph of Tenazas infrastructure.

Nodes: containers, devices, services, skills, models
Edges: depends_on, controls, monitors, connects_to

Enables deeper reasoning about infrastructure relationships.
"""

import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
GRAPH_FILE = BASE_DIR / "knowledge_graph.json"
TELEMETRY_LOG = BASE_DIR / "logs" / "knowledge_graph.log"



def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Static Graph Definition ──────────────────────────────────────

NODES = {
    # Containers
    "openclaw": {"type": "container", "role": "assistant", "critical": True},
    "gluetun": {"type": "container", "role": "vpn_gateway", "critical": True},
    "qbittorrent": {"type": "container", "role": "torrent_client"},
    "sonarr": {"type": "container", "role": "tv_manager"},
    "radarr": {"type": "container", "role": "movie_manager"},
    "lidarr": {"type": "container", "role": "music_manager"},
    "prowlarr": {"type": "container", "role": "indexer"},
    "seerr": {"type": "container", "role": "request_manager"},
    "requestrr": {"type": "container", "role": "request_bot"},
    "tautulli": {"type": "container", "role": "plex_monitor"},
    "sabnzbd": {"type": "container", "role": "usenet_client"},
    "chromadb": {"type": "container", "role": "vector_db"},
    "piper": {"type": "container", "role": "tts_engine"},
    "portainer": {"type": "container", "role": "docker_ui"},
    "watchtower": {"type": "container", "role": "auto_updater", "critical": True},
    # Devices
    "nas": {"type": "device", "ip": "192.168.2.2", "role": "host"},
    "notebook": {"type": "device", "ip": "192.168.2.6", "role": "gpu_inference"},
    "nas_backup": {"type": "device", "ip": "192.168.2.3", "role": "backup"},
    "router": {"type": "device", "ip": "192.168.2.1", "role": "gateway"},
    "hue_bridge": {"type": "device", "ip": "192.168.2.11", "role": "smart_home"},
    "samsung_m8": {"type": "device", "ip": "192.168.2.46", "role": "display"},
    # Services
    "ollama_gpu": {"type": "service", "url": "http://192.168.2.6:11434", "role": "inference"},
    "ollama_cpu": {"type": "service", "url": "http://127.0.0.1:11434", "role": "inference_fallback"},
    "gemini": {"type": "service", "role": "cloud_inference"},
    "telegram": {"type": "service", "role": "communication"},
}

EDGES = [
    # Arr-stack depends on gluetun
    {"from": "qbittorrent", "to": "gluetun", "type": "depends_on"},
    {"from": "sonarr", "to": "gluetun", "type": "depends_on"},
    {"from": "radarr", "to": "gluetun", "type": "depends_on"},
    {"from": "lidarr", "to": "gluetun", "type": "depends_on"},
    {"from": "prowlarr", "to": "gluetun", "type": "depends_on"},
    {"from": "sabnzbd", "to": "gluetun", "type": "depends_on"},
    {"from": "seerr", "to": "gluetun", "type": "depends_on"},
    {"from": "requestrr", "to": "gluetun", "type": "depends_on"},
    # Sonarr/Radarr use indexers and download clients
    {"from": "sonarr", "to": "prowlarr", "type": "depends_on"},
    {"from": "radarr", "to": "prowlarr", "type": "depends_on"},
    {"from": "sonarr", "to": "qbittorrent", "type": "depends_on"},
    {"from": "radarr", "to": "qbittorrent", "type": "depends_on"},
    # OpenClaw controls/monitors
    {"from": "openclaw", "to": "chromadb", "type": "depends_on"},
    {"from": "openclaw", "to": "ollama_gpu", "type": "depends_on"},
    {"from": "openclaw", "to": "telegram", "type": "connects_to"},
    {"from": "openclaw", "to": "hue_bridge", "type": "controls"},
    {"from": "openclaw", "to": "samsung_m8", "type": "controls"},
    # Device hosting
    {"from": "ollama_gpu", "to": "notebook", "type": "runs_on"},
    {"from": "ollama_cpu", "to": "nas", "type": "runs_on"},
    {"from": "chromadb", "to": "nas", "type": "runs_on"},
    # Monitoring
    {"from": "openclaw", "to": "gluetun", "type": "monitors"},
    {"from": "openclaw", "to": "sonarr", "type": "monitors"},
    {"from": "openclaw", "to": "radarr", "type": "monitors"},
    {"from": "tautulli", "to": "nas", "type": "monitors"},
    # Backup
    {"from": "nas", "to": "nas_backup", "type": "backs_up_to"},
]


# ── API ──────────────────────────────────────────────────────────


def get_node(name: str) -> dict | None:
    return NODES.get(name)


def get_nodes_by_type(node_type: str) -> dict:
    return {k: v for k, v in NODES.items() if v.get("type") == node_type}


def get_dependencies(name: str) -> list:
    """Get what this node depends on."""
    return [e["to"] for e in EDGES if e["from"] == name and e["type"] == "depends_on"]


def get_dependents(name: str) -> list:
    """Get what depends on this node."""
    return [e["from"] for e in EDGES if e["to"] == name and e["type"] == "depends_on"]


def get_impact(name: str) -> dict:
    """Assess impact of a node failure. Returns affected nodes."""
    dependents = get_dependents(name)
    cascade = set(dependents)
    # One level of cascade
    for d in dependents:
        cascade.update(get_dependents(d))
    cascade.discard(name)

    return {
        "node": name,
        "direct_dependents": dependents,
        "cascade_affected": sorted(cascade),
        "is_critical": NODES.get(name, {}).get("critical", False),
    }


def build_graph() -> dict:
    """Build and save the full knowledge graph."""
    graph = {
        "nodes": NODES,
        "edges": EDGES,
        "node_count": len(NODES),
        "edge_count": len(EDGES),
        "built_at": now().isoformat(),
    }

    GRAPH_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(GRAPH_FILE, "w") as f:
        json.dump(graph, f, indent=2, default=str)

    _log({"type": "graph_built", "nodes": len(NODES), "edges": len(EDGES)})
    return graph
