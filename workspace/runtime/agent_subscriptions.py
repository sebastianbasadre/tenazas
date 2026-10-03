#!/usr/bin/env python3
"""Subscriptions Agent -- recurring bill and subscription monitoring."""

import json
from collections import defaultdict
from pathlib import Path

from agent_protocol import Observation, Recommendation
from runtime_utils import load_json, now, save_json

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
STATUS_FILE = AGENT_DIR / "subscriptions_status.json"
LOG_FILE = BASE_DIR / "logs" / "agent_subscriptions.log"
TX_FILE = BASE_DIR / "finance" / "transactions.json"

OWNERSHIP = {"subscription", "suscripcion", "suscripción", "recurring", "recurrente",
             "bill", "bills", "factura", "servicio", "servicios", "renewal", "renovacion"}
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


def _load_transactions() -> list:
    data = load_json(str(TX_FILE), [])
    return data if isinstance(data, list) else []


def assess() -> dict:
    txns = _load_transactions()
    buckets = defaultdict(list)
    keywords = ("netflix", "spotify", "flow", "internet", "personal", "claro",
                "movistar", "github", "openai", "google", "streaming")
    for tx in txns:
        text = f"{tx.get('description', '')} {tx.get('raw', '')}".lower()
        if tx.get("category") == "servicios" or any(k in text for k in keywords):
            name = next((k for k in keywords if k in text), tx.get("description", "servicio"))
            buckets[name].append(tx)
    recurring = []
    for name, items in buckets.items():
        if len(items) >= 2:
            total = sum(float(i.get("amount", 0) or 0) for i in items[-3:])
            recurring.append({"name": name, "count": len(items), "recent_total": round(total, 2)})
    status = {"agent": "subscriptions", "ts": now().isoformat(), "recurring": recurring}
    save_json(str(STATUS_FILE), status)
    _log({"action": "assess", "recurring": len(recurring)})
    return status


def handle(query: str, correlation_id: str = None):
    status = assess()
    recurring = status.get("recurring", [])
    if recurring:
        names = ", ".join(r["name"] for r in recurring[:5])
        summary = f"Subscriptions: {len(recurring)} recurrentes detectadas ({names})"
    else:
        summary = "Subscriptions: sin recurrentes detectadas"
    recs = [Recommendation("review", "recurring_bills", "low", False,
                           "Revisar cargos recurrentes detectados")] if recurring else []
    obs = Observation("subscriptions", "finance", summary, 0.7, status)
    _log({"action": "handle", "query": query[:80], "correlation_id": correlation_id})
    return obs, recs

