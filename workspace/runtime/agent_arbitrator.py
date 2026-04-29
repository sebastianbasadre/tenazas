#!/usr/bin/env python3
"""Agent Arbitrator — Resolves conflicts between agents.

Priority order:
1. critical safety (policy_engine) — always wins
2. agent_infra in critical incident
3. agent_personal in active Sebas event
4. agent_finance in extreme variation (>10%)
5. agent_research / agent_self_improve — lowest

Conflict resolution: detect → log → apply priority → confidence tiebreak
→ Telegram escalation on true tie or contradiction → execute winner.
"""

import json
import time
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from runtime_utils import now, load_json, save_json
from agent_protocol import Observation, Recommendation

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
CONFLICTS_FILE = STATE_DIR / "agents" / "conflicts.json"
LOG_FILE = BASE_DIR / "logs" / "agent_arbitrator.log"

PRIORITY = {
    "safety": 0,
    "infra_critical": 1,
    "personal_active": 2,
    "finance_extreme": 3,
    "infra": 4,
    "personal": 5,
    "finance": 6,
    "research": 7,
    "self_improve": 8,
}

OBSERVATION_TTL_MINUTES = 10
AGENT_STATE_DIR = STATE_DIR / "agents"
AGENT_STATE_FILES = {
    "infra": "infra_status.json",
    "personal": "personal_status.json",
    "finance": "finance_status.json",
    "research": "research_status.json",
    "self_improve": "self_improve_status.json",
}
OPPOSITE_ACTIONS = {
    "restart": "stop", "stop": "restart",
    "start": "stop", "investigate": None,
}


def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass


def _get_effective_priority(agent: str, context: dict = None) -> int:
    """Get priority considering context (critical incidents, active events, etc.)."""
    ctx = context or {}

    if agent == "infra" and ctx.get("severity") == "critical":
        return PRIORITY["infra_critical"]
    if agent == "personal" and ctx.get("calendar_active"):
        return PRIORITY["personal_active"]
    if agent == "finance" and ctx.get("variation_pct", 0) > 10:
        return PRIORITY["finance_extreme"]

    return PRIORITY.get(agent, 9)


def _conflict_id(agent_a: str, agent_b: str, resource: str) -> str:
    key = f"{min(agent_a, agent_b)}:{max(agent_a, agent_b)}:{resource}"
    return sha256(key.encode()).hexdigest()[:12]


def _get_confidence(claim: dict) -> float:
    """Extract confidence from claim (Observation object or dict)."""
    obs = claim.get("observation")
    if obs is None:
        return 0.0
    if hasattr(obs, "confidence"):
        return float(obs.confidence)
    if isinstance(obs, dict):
        return float(obs.get("confidence", 0.0))
    return 0.0


def _are_contradictory(recs_a: list, recs_b: list) -> bool:
    """Check if two recommendation sets contain opposite actions on same target."""
    for ra in recs_a:
        for rb in recs_b:
            t_a = ra.target if hasattr(ra, "target") else ra.get("target", "")
            t_b = rb.target if hasattr(rb, "target") else rb.get("target", "")
            a_a = ra.action if hasattr(ra, "action") else ra.get("action", "")
            a_b = rb.action if hasattr(rb, "action") else rb.get("action", "")
            if t_a and t_a == t_b:
                opp = OPPOSITE_ACTIONS.get(a_a)
                if opp and a_b == opp:
                    return True
                opp_b = OPPOSITE_ACTIONS.get(a_b)
                if opp_b and a_a == opp_b:
                    return True
    return False


def _static_priority_winner(a: dict, b: dict) -> str:
    """Return agent name with lower static PRIORITY number (per D-06)."""
    pa = PRIORITY.get(a["agent"], 9)
    pb = PRIORITY.get(b["agent"], 9)
    return a["agent"] if pa <= pb else b["agent"]


def _default_winner(a: dict, b: dict, reason: str) -> dict:
    """Fallback: pick winner by static priority."""
    winner_name = _static_priority_winner(a, b)
    loser_name = b["agent"] if winner_name == a["agent"] else a["agent"]
    winner = a if winner_name == a["agent"] else b
    return {
        "winner": winner_name, "loser": loser_name,
        "reason": f"default priority ({reason})",
        "action": winner.get("action"), "escalated": False,
    }


def _escalate_to_telegram(claim_a: dict, claim_b: dict) -> dict:
    """Escalate conflict to Telegram for Sebas to decide."""
    try:
        from exec_approvals import send_approval_request
    except ImportError:
        _log({"action": "escalate_import_fail"})
        # Default to static priority winner
        return _default_winner(claim_a, claim_b, "exec_approvals unavailable")

    obs_a = claim_a.get("observation")
    obs_b = claim_b.get("observation")
    sum_a = getattr(obs_a, "summary", "") if obs_a else claim_a.get("action", "N/A")
    sum_b = getattr(obs_b, "summary", "") if obs_b else claim_b.get("action", "N/A")
    conf_a = _get_confidence(claim_a)
    conf_b = _get_confidence(claim_b)

    summary = f"Conflicto: {claim_a['agent']} vs {claim_b['agent']}"
    details = (
        f"Agente: {claim_a['agent']}\n"
        f"  Observacion: {sum_a}\n"
        f"  Confianza: {conf_a:.2f}\n\n"
        f"Agente: {claim_b['agent']}\n"
        f"  Observacion: {sum_b}\n"
        f"  Confianza: {conf_b:.2f}"
    )

    try:
        approval_id = send_approval_request(summary, details, "arbitrator")
    except Exception as e:
        _log({"action": "escalate_send_fail", "error": str(e)[:100]})
        return _default_winner(claim_a, claim_b, f"Telegram send failed: {str(e)[:50]}")

    # Determine default winner by static priority (lower number wins) per D-06
    default = _static_priority_winner(claim_a, claim_b)

    _log({"action": "escalate_telegram", "agents": [claim_a["agent"], claim_b["agent"]],
          "approval_id": approval_id, "default_winner": default})

    return {
        "winner": default,
        "loser": claim_b["agent"] if default == claim_a["agent"] else claim_a["agent"],
        "reason": f"escalated to Telegram (default: {default} after 5min timeout)",
        "action": claim_a.get("action") if default == claim_a["agent"] else claim_b.get("action"),
        "escalated": True,
        "approval_id": approval_id,
        "agents": [claim_a["agent"], claim_b["agent"]],
    }


# ── Main API ────────────────────────────────────────────────────


def resolve(proposals: list) -> dict:
    """Resolve conflicts between agent proposals on the same resource.

    Args:
        proposals: list of dicts. Each dict must have 'agent' and 'action' keys.
            Optional keys: 'resource', 'target', 'severity', 'context',
            'observation' (Observation instance or dict),
            'recommendations' (list of Recommendation instances or dicts).

    Returns:
        {winner, loser, reason, action, escalated}
    """
    if len(proposals) < 2:
        if proposals:
            return {"winner": proposals[0]["agent"], "loser": None,
                    "reason": "no conflict", "action": proposals[0]["action"],
                    "escalated": False}
        return {"winner": None, "loser": None, "reason": "no proposals",
                "action": None, "escalated": False}

    # Check safety first -- only for container-related actions
    filtered = []
    for p in proposals:
        target = p.get("target", p.get("resource", ""))
        action = p.get("action", "").lower()
        if "restart" in action or "stop" in action or "exec" in action:
            try:
                import safety_guard as sg
                if not sg.is_safe_container(target):
                    _log({"action": "safety_block", "agent": p["agent"],
                          "proposal": action, "target": target})
                    continue
            except Exception:
                pass
        filtered.append(p)
    proposals = filtered

    if len(proposals) < 2:
        winner = proposals[0] if proposals else None
        return {"winner": winner["agent"] if winner else None,
                "loser": None, "reason": "safety filtered",
                "action": winner["action"] if winner else None,
                "escalated": False}

    # Sort by priority (lower number = higher priority)
    sorted_props = sorted(proposals,
                          key=lambda p: _get_effective_priority(p["agent"], p.get("context", {})))

    winner = sorted_props[0]
    loser = sorted_props[1]

    # Check if same priority
    w_prio = _get_effective_priority(winner["agent"], winner.get("context", {}))
    l_prio = _get_effective_priority(loser["agent"], loser.get("context", {}))

    if w_prio == l_prio:
        # Confidence tiebreak (per D-02)
        conf_w = _get_confidence(winner)
        conf_l = _get_confidence(loser)

        # Check for contradictory recommendations (per D-04)
        recs_w = winner.get("recommendations", [])
        recs_l = loser.get("recommendations", [])
        contradictory = _are_contradictory(recs_w, recs_l) if (recs_w and recs_l) else False

        if contradictory:
            # Contradictory actions on same target -- must escalate
            _log({"action": "contradiction_detected",
                  "agents": [winner["agent"], loser["agent"]]})

            # Track in conflict history for observability
            resource = winner.get("resource", winner.get("target", ""))
            cid = _conflict_id(winner["agent"], loser["agent"], resource)
            conflicts = load_json(str(CONFLICTS_FILE), {"history": {}})
            history = conflicts.get("history", {}).get(cid, {"count": 0, "first": now().isoformat()})
            history["count"] += 1
            history["last"] = now().isoformat()
            conflicts.setdefault("history", {})[cid] = history
            save_json(str(CONFLICTS_FILE), conflicts)

            return _escalate_to_telegram(winner, loser)

        if abs(conf_w - conf_l) > 0.01:
            # Confidence differs -- higher confidence wins
            if conf_l > conf_w:
                winner, loser = loser, winner
                conf_w, conf_l = conf_l, conf_w

            result = {
                "winner": winner["agent"],
                "loser": loser["agent"],
                "reason": f"confidence tiebreak {winner['agent']}({conf_w:.2f}) > {loser['agent']}({conf_l:.2f})",
                "action": winner["action"],
                "escalated": False,
            }
            _log({"action": "resolve_confidence", **result})
            return result

        # True tie: same priority AND same confidence -- escalate to Telegram
        _log({"action": "true_tie",
              "agents": [winner["agent"], loser["agent"]],
              "confidence": conf_w})

        # Track in conflict history for observability
        resource = winner.get("resource", winner.get("target", ""))
        cid = _conflict_id(winner["agent"], loser["agent"], resource)
        conflicts = load_json(str(CONFLICTS_FILE), {"history": {}})
        history = conflicts.get("history", {}).get(cid, {"count": 0, "first": now().isoformat()})
        history["count"] += 1
        history["last"] = now().isoformat()
        conflicts.setdefault("history", {})[cid] = history
        save_json(str(CONFLICTS_FILE), conflicts)

        return _escalate_to_telegram(winner, loser)

    result = {
        "winner": winner["agent"],
        "loser": loser["agent"],
        "reason": f"priority {winner['agent']}({w_prio}) > {loser['agent']}({l_prio})",
        "action": winner["action"],
        "escalated": False,
    }

    _log({"action": "resolve", **result})
    return result


def read_cached_observation(agent_name: str) -> dict:
    """Read cached observation from agent's state file. Returns None if stale or missing."""
    fname = AGENT_STATE_FILES.get(agent_name)
    if not fname:
        return None
    state_file = AGENT_STATE_DIR / fname
    state = load_json(str(state_file), {})
    obs_dict = state.get("observation")
    if not obs_dict:
        return None
    ts_str = obs_dict.get("timestamp", "")
    if ts_str:
        try:
            obs_time = datetime.fromisoformat(ts_str)
            if now().astimezone() - obs_time > timedelta(minutes=OBSERVATION_TTL_MINUTES):
                return None
        except (ValueError, TypeError):
            return None
    else:
        return None
    return obs_dict


def get_conflict_history() -> dict:
    return load_json(str(CONFLICTS_FILE), {"history": {}}).get("history", {})


def clear_conflicts():
    save_json(str(CONFLICTS_FILE), {"history": {}})
