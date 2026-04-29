#!/usr/bin/env python3
"""Agent Finance — Specialized in financial monitoring.

Ownership: Binance, IOL, market alerts.
"""

import json
from pathlib import Path
from runtime_utils import now, load_json, save_json
from agent_protocol import Observation, Recommendation

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "finance_memory.json"
STATUS_FILE = AGENT_DIR / "finance_status.json"
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


def _observe_binance():
    """Focused observation of Binance/BTC status."""
    try:
        from finance_daily import get_binance_pnl
        pnl = get_binance_pnl()
        if not pnl:
            return {"summary": "Binance: sin datos", "confidence": 0.5, "sub": "binance", "issues": [], "raw": {}}
        change = pnl.get("change_pct", 0)
        price = pnl.get("price", 0)
        issues = []
        if abs(change) > BTC_ALERT_THRESHOLD * 100:
            issues.append(f"BTC: {change:+.1f}% cambio significativo")
        summary = f"Binance: BTC ${price:,.0f}" if price else "Binance: BTC"
        if change:
            summary += f" ({change:+.1f}%)"
        return {"summary": summary, "confidence": 0.8, "sub": "binance", "issues": issues, "raw": pnl}
    except Exception as e:
        return {"summary": f"Binance: error ({str(e)[:80]})", "confidence": 0.3, "sub": "binance", "issues": [str(e)[:100]], "raw": {}}


def _observe_iol():
    """Focused observation of IOL portfolio."""
    try:
        from finance_daily import get_iol_portfolio
        iol = get_iol_portfolio()
        if not iol:
            return {"summary": "IOL: sin datos", "confidence": 0.5, "sub": "iol", "issues": [], "raw": {}}
        total = iol.get("total", 0)
        pnl = iol.get("pnl_day", iol.get("pnl", 0))
        issues = []
        if total > 0 and abs(pnl) > IOL_ALERT_THRESHOLD * total:
            issues.append(f"IOL P&L dia: ${pnl:,.0f}")
        summary = f"IOL: ${total:,.0f} total"
        return {"summary": summary, "confidence": 0.8, "sub": "iol", "issues": issues, "raw": iol}
    except Exception as e:
        return {"summary": f"IOL: error ({str(e)[:80]})", "confidence": 0.3, "sub": "iol", "issues": [str(e)[:100]], "raw": {}}


def _observe_expenses():
    """Focused observation of recent expenses."""
    try:
        from expenses import weekly_summary
        week = weekly_summary()
        total = week.get("total", 0)
        count = week.get("count", 0)
        top_cat = week.get("top_category", "")
        issues = []
        summary = f"Gastos: ${total:,.0f} esta semana ({count} movimientos)"
        if top_cat:
            summary += f" - top: {top_cat}"
        return {"summary": summary, "confidence": 0.8, "sub": "expenses", "issues": issues, "raw": week}
    except ImportError:
        try:
            from finance import summary as finance_summary
            s = finance_summary("week")
            total = s.get("total_expense", s.get("expenses", 0))
            summary = f"Gastos: ${total:,.0f} esta semana"
            return {"summary": summary, "confidence": 0.7, "sub": "expenses", "issues": [], "raw": s}
        except Exception as e2:
            return {"summary": f"Gastos: error ({str(e2)[:80]})", "confidence": 0.3, "sub": "expenses", "issues": [str(e2)[:100]], "raw": {}}
    except Exception as e:
        return {"summary": f"Gastos: error ({str(e)[:80]})", "confidence": 0.3, "sub": "expenses", "issues": [str(e)[:100]], "raw": {}}


def _observe_dolar():
    """Best-effort observation of USD rate."""
    try:
        from finance_daily import get_dolar_rate
        rate = get_dolar_rate()
        if not rate:
            return {"summary": "Dolar: sin datos", "confidence": 0.3, "sub": "dolar", "issues": ["dolar data not available"], "raw": {}}
        blue = rate.get("blue", rate.get("rate", 0))
        summary = f"Dolar: ${blue:,.0f}" if blue else "Dolar: sin datos"
        return {"summary": summary, "confidence": 0.7, "sub": "dolar", "issues": [], "raw": rate}
    except Exception:
        return {"summary": "Dolar: sin datos", "confidence": 0.3, "sub": "dolar", "issues": ["dolar data not available"], "raw": {}}


def _observe_full():
    """Full assessment via existing assess(), reformatted as observer dict."""
    status = assess()
    alerts = status.get("alerts", [])
    summary = status.get("summary", "Finance: sin datos")
    if alerts:
        summary = f"Finance: {', '.join(alerts[:3])}"
    return {
        "summary": summary,
        "confidence": 0.85,
        "sub": "full",
        "issues": alerts,
        "raw": status,
    }


def _build_finance_recs(data):
    """Build typed Recommendations from finance observer data."""
    recs = []
    for issue in data.get("issues", []):
        recs.append(Recommendation(
            action="alert",
            target="telegram",
            risk_level="medium",
            requires_approval=False,
            rationale=issue,
        ))
    return recs


def handle(query: str, correlation_id: str = None):
    """Returns (Observation, list[Recommendation])."""
    q = query.lower()
    if any(k in q for k in ("binance", "btc", "crypto", "bitcoin")):
        data = _observe_binance()
    elif any(k in q for k in ("iol", "portafolio", "portfolio", "acciones", "cedears", "invertido")):
        data = _observe_iol()
    elif any(k in q for k in ("gasto", "gasté", "expense", "cuánto", "cuanto")):
        data = _observe_expenses()
    elif any(k in q for k in ("dolar", "dólar", "usd", "blue", "mep")):
        data = _observe_dolar()
    else:
        data = _observe_full()
    obs = Observation(
        agent_name="finance",
        domain="finance",
        summary=data.get("summary", "Finance: sin datos"),
        confidence=data.get("confidence", 0.7),
        details=data,
    )
    recs = _build_finance_recs(data)
    # Save observation to per-agent state file for cross-agent reads (D-07)
    try:
        state = load_json(str(STATUS_FILE), {})
        state.update(data)
        state["observation"] = obs.to_dict()
        save_json(str(STATUS_FILE), state)
    except Exception:
        pass  # Never crash on observation caching
    _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"), "confidence": obs.confidence, "correlation_id": correlation_id})
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
