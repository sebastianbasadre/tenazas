#!/usr/bin/env python3
"""Agent Infra — Specialized in homelab infrastructure.

Ownership: containers, GPU, RAM, disk, network, VPN.
Reads snapshot, uses predictor + root_cause_engine, proposes via action_registry.
"""

import json
from pathlib import Path
from runtime_utils import now, load_json, save_json
from agent_protocol import Observation, Recommendation

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "infra_memory.json"
STATUS_FILE = AGENT_DIR / "infra_status.json"
LOG_FILE = BASE_DIR / "logs" / "agent_infra.log"

OWNERSHIP = {"container", "containers", "gpu", "ram", "disk", "network", "vpn",
             "ollama", "docker", "gluetun", "infra", "homelab", "backend_offline"}
BLOCKED_TARGETS = {"openclaw", "watchtower"}
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
    """Assess current infra state. Returns status + proposals."""
    status = {"agent": "infra", "ts": now().isoformat(), "health": "ok", "proposals": [], "issues": []}
    try:
        from homelab_snapshot import snapshot
        snap = snapshot()
        c = snap.get("containers", {})
        if c.get("stopped", 0) > 0 or c.get("unhealthy", 0) > 0:
            status["health"] = "degraded"
            status["issues"].append(f"{c.get('stopped',0)} stopped, {c.get('unhealthy',0)} unhealthy")
        o = snap.get("ollama", {})
        if o.get("status") != "online":
            status["health"] = "degraded"
            status["issues"].append("ollama offline")
    except Exception as e:
        status["issues"].append(f"snapshot error: {str(e)[:100]}")

    try:
        from infra_predictor import predict_all
        preds = predict_all()
        for p in preds:
            status["proposals"].append({
                "action": p.get("recommended_action", "investigate"),
                "reason": f"prediction: {p['type']} (p={p['probability']:.0%})",
                "severity": p.get("severity", "info"),
            })
    except Exception: pass

    # Filter out protected targets
    status["proposals"] = [p for p in status["proposals"]
                           if not any(t in p.get("action", "").lower() for t in BLOCKED_TARGETS)]

    save_json(str(STATUS_FILE), status)
    _log({"action": "assess", "health": status["health"], "proposals": len(status["proposals"])})
    return status


def _observe_containers(cached_snap=None):
    """Focused observation of container status."""
    try:
        if cached_snap is None:
            from homelab_snapshot import snapshot
            cached_snap = snapshot()
        snap = cached_snap
        c = snap.get("containers", {})
        total = c.get("total", 0)
        running = c.get("running", 0)
        stopped = c.get("stopped", 0)
        unhealthy = c.get("unhealthy", 0)
        issues = []
        if stopped > 0:
            issues.append(f"{stopped} stopped")
        if unhealthy > 0:
            issues.append(f"{unhealthy} unhealthy")
        summary = f"Containers: {running}/{total} running"
        if issues:
            summary += f" ({', '.join(issues)})"
        confidence = 0.9 if not issues else 0.6
        return {"summary": summary, "confidence": confidence, "sub": "containers", "issues": issues, "raw": c}
    except Exception as e:
        return {"summary": f"Containers: error ({str(e)[:80]})", "confidence": 0.3, "sub": "containers", "issues": [str(e)[:100]], "raw": {}}


def _observe_ollama(cached_snap=None):
    """Focused observation of Ollama/GPU status."""
    try:
        if cached_snap is None:
            from homelab_snapshot import snapshot
            cached_snap = snapshot()
        snap = cached_snap
        o = snap.get("ollama", {})
        status = o.get("status", "unknown")
        models = o.get("models", [])
        issues = []
        if status != "online":
            issues.append(f"ollama {status}")
        summary = f"Ollama: {status}"
        if models:
            summary += f" ({len(models)} models)"
        confidence = 0.9 if status == "online" else 0.5
        return {"summary": summary, "confidence": confidence, "sub": "ollama", "issues": issues, "raw": o}
    except Exception as e:
        return {"summary": f"Ollama: error ({str(e)[:80]})", "confidence": 0.3, "sub": "ollama", "issues": [str(e)[:100]], "raw": {}}


def _observe_vpn(cached_snap=None):
    """Focused observation of VPN/gluetun status."""
    try:
        if cached_snap is None:
            from homelab_snapshot import snapshot
            cached_snap = snapshot()
        snap = cached_snap
        vpn = snap.get("vpn", snap.get("gluetun", {}))
        status = vpn.get("status", "unknown")
        country = vpn.get("country", "")
        issues = []
        if status != "connected" and status != "ok":
            issues.append(f"VPN {status}")
        if country and country.upper() == "AR":
            issues.append("VPN leak detected (AR)")
        summary = f"VPN: {status}"
        if country:
            summary += f" ({country})"
        confidence = 0.9 if not issues else 0.5
        return {"summary": summary, "confidence": confidence, "sub": "vpn", "issues": issues, "raw": vpn}
    except Exception as e:
        return {"summary": f"VPN: error ({str(e)[:80]})", "confidence": 0.3, "sub": "vpn", "issues": [str(e)[:100]], "raw": {}}


def _observe_disk(cached_snap=None):
    """Focused observation of disk usage."""
    try:
        if cached_snap is None:
            from homelab_snapshot import snapshot
            cached_snap = snapshot()
        snap = cached_snap
        disk = snap.get("disk", snap.get("storage", {}))
        used_pct = disk.get("used_pct", disk.get("percent", 0))
        issues = []
        if used_pct > 90:
            issues.append(f"disk usage critical: {used_pct}%")
        elif used_pct > 80:
            issues.append(f"disk usage high: {used_pct}%")
        summary = f"Disk: {used_pct}% used"
        confidence = 0.9 if not issues else 0.6
        return {"summary": summary, "confidence": confidence, "sub": "disk", "issues": issues, "raw": disk}
    except Exception as e:
        return {"summary": f"Disk: error ({str(e)[:80]})", "confidence": 0.3, "sub": "disk", "issues": [str(e)[:100]], "raw": {}}


def _assess_full():
    """Full assessment via existing assess(), reformatted as observer dict."""
    status = assess()
    return {
        "summary": f"Infra: {status['health']}" + (f" - {', '.join(status['issues'][:3])}" if status.get("issues") else ""),
        "confidence": 0.9 if status["health"] == "ok" else 0.6,
        "sub": "full",
        "issues": status.get("issues", []),
        "raw": status,
    }


def _build_recommendations(data):
    """Build typed Recommendations from observer data issues."""
    recs = []
    for issue in data.get("issues", []):
        issue_lower = issue.lower()
        # Determine action based on issue type
        if "stopped" in issue_lower or "unhealthy" in issue_lower:
            action = "restart"
            risk_level = "medium"
            requires_approval = True
            target = "container"
        elif "ollama" in issue_lower and ("offline" in issue_lower or "error" in issue_lower):
            action = "investigate"
            risk_level = "low"
            requires_approval = False
            target = "ollama"
        elif "vpn" in issue_lower or "leak" in issue_lower:
            action = "restart"
            risk_level = "medium"
            requires_approval = True
            target = "gluetun"
        elif "disk" in issue_lower:
            action = "investigate"
            risk_level = "low"
            requires_approval = False
            target = "storage"
        else:
            action = "investigate"
            risk_level = "low"
            requires_approval = False
            target = "infra"
        # Filter BLOCKED_TARGETS
        if target.lower() in BLOCKED_TARGETS:
            continue
        recs.append(Recommendation(
            action=action,
            target=target,
            risk_level=risk_level,
            requires_approval=requires_approval,
            rationale=issue,
        ))
    return recs


def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    # Cache snapshot once to avoid redundant I/O across sub-observers
    try:
        from homelab_snapshot import snapshot
        snap = snapshot()
    except Exception:
        snap = None
    q = query.lower()
    if any(k in q for k in ("container", "docker", "stopped", "unhealthy", "restart")):
        data = _observe_containers(cached_snap=snap)
    elif any(k in q for k in ("gpu", "vram", "ollama", "modelo", "model")):
        data = _observe_ollama(cached_snap=snap)
    elif any(k in q for k in ("vpn", "gluetun", "leak", "ip")):
        data = _observe_vpn(cached_snap=snap)
    elif any(k in q for k in ("disk", "disco", "espacio", "storage")):
        data = _observe_disk(cached_snap=snap)
    else:
        data = _assess_full()
    obs = Observation(
        agent_name="infra",
        domain="infrastructure",
        summary=data.get("summary", "Infra: sin datos"),
        confidence=data.get("confidence", 0.5),
        details=data,
    )
    recs = _build_recommendations(data)
    # Save observation to per-agent state file for cross-agent reads (D-07)
    try:
        state = load_json(str(STATUS_FILE), {})
        state.update(data)
        state["observation"] = obs.to_dict()
        save_json(str(STATUS_FILE), state)
    except Exception:
        pass  # Never crash on observation caching
    _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"), "confidence": obs.confidence})
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
