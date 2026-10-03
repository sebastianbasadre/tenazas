#!/usr/bin/env python3
"""Home Reliability Agent -- smart-home and LAN health."""

import json
from pathlib import Path

from agent_protocol import Observation, Recommendation
from runtime_utils import load_json, now, save_json

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
STATUS_FILE = AGENT_DIR / "home_reliability_status.json"
LOG_FILE = BASE_DIR / "logs" / "agent_home_reliability.log"
INVENTORY_FILE = BASE_DIR / "device_inventory.json"

OWNERSHIP = {"home", "casa", "smart home", "smarthome", "device", "devices",
             "hue", "samsung", "vacuum", "aspiradora", "wifi", "lan", "doorbell"}
PREFERRED_ROLE = "fast"


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def owns(topic: str) -> bool:
    q = topic.lower()
    return any(k in q for k in OWNERSHIP)


def assess() -> dict:
    inventory = load_json(str(INVENTORY_FILE), {})
    devices = inventory.get("devices", []) if isinstance(inventory, dict) else []
    offline = [d for d in devices if str(d.get("status", d.get("state", ""))).lower() in {"offline", "down"}]
    status = {"agent": "home_reliability", "ts": now().isoformat(), "devices": len(devices),
              "offline": offline[:10]}
    save_json(str(STATUS_FILE), status)
    _log({"action": "assess", "devices": len(devices), "offline": len(offline)})
    return status


def handle(query: str, correlation_id: str = None):
    status = assess()
    if status["devices"]:
        summary = f"Home: {status['devices']} devices tracked"
        if status["offline"]:
            summary += f", {len(status['offline'])} offline"
    else:
        summary = "Home: sin inventario reciente"
    recs = [Recommendation("investigate", "offline_devices", "medium", False,
                           "Hay dispositivos smart-home offline")] if status["offline"] else []
    obs = Observation("home_reliability", "home", summary, 0.7, status)
    _log({"action": "handle", "query": query[:80], "correlation_id": correlation_id})
    return obs, recs

