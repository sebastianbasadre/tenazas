#!/usr/bin/env python3
"""Agent Finance — Specialized in financial monitoring.

Ownership: Binance, IOL, market alerts.
"""

import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now, load_json, save_json
from agent_protocol import Observation, Recommendation

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "finance_memory.json"
LOG_FILE = BASE_DIR / "logs" / "agent_finance.log"

OWNERSHIP = {"finance", "binance", "iol", "crypto", "btc", "portfolio",
             "market", "trading", "investment", "dolar", "pnl"}
BTC_ALERT_THRESHOLD = 0.05  # 5%
IOL_ALERT_THRESHOLD = 0.03  # 3%
PREFERRED_ROLE = "fast"


def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass

def owns(topic: str) -> bool:
    return any(k in topic.lower() for k in OWNERSHIP)


def assess() -> dict:
    status = {"agent": "finance", "ts": now().isoformat(), "alerts": [], "summary": ""}
    try:
        from finance_daily import get_binance_pnl
        pnl = get_binance_pnl()
        if pnl and abs(pnl.get("change_pct", 0)) > BTC_ALERT_THRESHOLD * 100:
            status["alerts"].append(f"BTC: {pnl['change_pct']:+.1f}%")
    except Exception: pass
    try:
        from finance_daily import get_iol_portfolio
        iol = get_iol_portfolio()
        if iol:
            status["summary"] = f"IOL: ${iol.get('total', 0):,.0f}"
    except Exception: pass
    _log({"action": "assess", "alerts": len(status["alerts"])})
    return status


def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    status = assess()
    alerts = status.get("alerts", [])
    obs = Observation(
        agent_name="finance",
        domain="finance",
        summary=status.get("summary", "Finance: sin alertas") if not alerts else f"Finance: {', '.join(alerts[:3])}",
        confidence=0.85,
        details=status,
    )
    recs = []
    for alert in alerts:
        recs.append(Recommendation(
            action="alert",
            target="telegram",
            risk_level="medium",
            requires_approval=False,
            rationale=alert,
        ))
    return (obs, recs)


def handle_query(query: str) -> str:
    """Backward-compat wrapper. Returns string summary."""
    obs, recs = handle(query)
    return obs.summary


def get_memory() -> dict: return load_json(str(MEMORY_FILE), {"events": []})
def save_memory_event(event: dict):
    mem = get_memory()
    mem["events"].append({**event, "ts": now().isoformat()})
    mem["events"] = mem["events"][-100:]
    save_json(str(MEMORY_FILE), mem)
