#!/usr/bin/env python3
"""Portfolio Risk Agent -- allocation and concentration monitoring."""

import json
from pathlib import Path

from agent_protocol import Observation, Recommendation
from runtime_utils import load_json, now, save_json

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
STATUS_FILE = AGENT_DIR / "portfolio_risk_status.json"
LOG_FILE = BASE_DIR / "logs" / "agent_portfolio_risk.log"

OWNERSHIP = {
    "risk", "riesgo", "allocation", "allocacion", "asignacion", "drawdown",
    "concentration", "concentracion", "exposure", "exposicion", "portfolio risk",
}
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


def _load_positions() -> list:
    try:
        from finance_exporter import fetch_binance, fetch_iol_live, fetch_usd_ars_rate
        from finance_positions import build_unified_positions

        rate = fetch_usd_ars_rate() or 0
        return build_unified_positions(fetch_binance(), fetch_iol_live(rate), rate)
    except Exception:
        return []


def assess() -> dict:
    positions = _load_positions()
    total = sum(float(p.get("value_usd", 0) or 0) for p in positions)
    top = sorted(positions, key=lambda p: float(p.get("value_usd", 0) or 0), reverse=True)[:5]
    issues = []
    if total > 0 and top:
        top_alloc = float(top[0].get("value_usd", 0) or 0) / total * 100
        if top_alloc > 60:
            issues.append(f"Concentration high: {top[0].get('symbol', '?')} {top_alloc:.1f}%")
    status = {"agent": "portfolio_risk", "ts": now().isoformat(), "total_usd": round(total, 2),
              "top": top, "issues": issues}
    save_json(str(STATUS_FILE), status)
    _log({"action": "assess", "positions": len(positions), "issues": len(issues)})
    return status


def handle(query: str, correlation_id: str = None):
    status = assess()
    top = status.get("top", [])
    if status["total_usd"] > 0 and top:
        lead = top[0]
        alloc = float(lead.get("value_usd", 0) or 0) / status["total_usd"] * 100
        summary = f"Portfolio risk: ${status['total_usd']:,.0f} total, top {lead.get('symbol', '?')} {alloc:.1f}%"
    else:
        summary = "Portfolio risk: sin datos suficientes"
    recs = [
        Recommendation("review", "portfolio_allocation", "medium", False, issue)
        for issue in status.get("issues", [])
    ]
    obs = Observation("portfolio_risk", "finance", summary, 0.75, status)
    _log({"action": "handle", "query": query[:80], "correlation_id": correlation_id})
    return obs, recs

