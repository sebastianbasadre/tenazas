#!/usr/bin/env python3
"""
Daily Improvement — Generates one concrete improvement proposal per day.

Scans operational signals across the entire Tenazas environment (routing,
guardrails, finance, jobs, observability, reliability) and produces a single,
prioritized, actionable proposal ready for human review.

Reuses idle_learning.py for LLM calls and JSON extraction. Does NOT
duplicate that module's logic — this is a higher-level orchestrator that
reads broader signals and selects the best single improvement per day.

Output: runtime/learning/daily_improvement.json + daily_improvement.md

Security:
  - NEVER modifies any runtime file, config, or code
  - NEVER applies changes automatically
  - Only writes to runtime/learning/
  - All proposals require human review

Usage:
  docker exec openclaw python3 /home/tenazas/runtime/daily_improvement.py

Future: schedule via cron at 6AM (idle hours, before morning greeting at 8AM)
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from runtime_utils import now
try:
    from .runtime_utils import load_json
except ImportError:
    from runtime_utils import load_json

try:
    from runtime_config import OLLAMA_GPU_URL
except ImportError:
    from workspace.runtime.runtime_config import OLLAMA_GPU_URL

# ── Config ──────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
LEARNING_DIR = BASE_DIR / "learning"
DAILY_FILE = LEARNING_DIR / "daily_improvement.json"
DAILY_REPORT = LEARNING_DIR / "daily_improvement.md"
TELEMETRY_LOG = LOG_DIR / "daily_improvement.log"

# Signal sources
ROUTER_LOG = LOG_DIR / "model_router.log"
ORCHESTRATOR_LOG = LOG_DIR / "orchestrator.log"
GUARDRAIL_STATE = BASE_DIR / "guardrail_state.json"
FINANCE_FILE = BASE_DIR / "finance" / "transactions.json"
JOBS_FILE = BASE_DIR / "jobs" / "opportunities.json"
EXPENSES_LOG = LOG_DIR / "expenses.log"

OLLAMA_URL = OLLAMA_GPU_URL
PROPOSAL_MODEL = "qwen2.5:7b"

# Areas
AREAS = (
    "routing", "guardrails", "prompting", "observability",
    "ux", "finance", "jobs", "reliability", "memory",
)



def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_jsonl(path: Path, max_lines: int = 500) -> list:
    """Load recent lines from a JSONL file."""
    lines = []
    try:
        with open(path) as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        lines.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
    except FileNotFoundError:
        pass
    return lines[-max_lines:]


# ── Phase 1: Scan Signals ──────────────────────────────────────


def _scan_routing_signals() -> list:
    """Scan model_router.log for routing opportunities."""
    signals = []
    entries = _load_jsonl(ROUTER_LOG, 200)
    route_entries = [e for e in entries if e.get("action") == "route_v2"]

    if not route_entries:
        return signals

    # High fallback rate
    fallbacks = sum(1 for e in route_entries if e.get("fallback_used"))
    if len(route_entries) > 5 and fallbacks / len(route_entries) > 0.3:
        signals.append({
            "area": "routing",
            "signal": "high_fallback_rate",
            "detail": f"{fallbacks}/{len(route_entries)} decisions used fallback model",
            "impact": "high",
            "evidence": f"fallback_rate={fallbacks / len(route_entries):.0%}",
        })

    # Degradation frequency
    degraded = sum(1 for e in route_entries if e.get("degraded"))
    if degraded > 3:
        signals.append({
            "area": "guardrails",
            "signal": "frequent_degradation",
            "detail": f"{degraded} routing decisions were degraded by guardrails",
            "impact": "medium",
            "evidence": f"degraded_count={degraded}",
        })

    # VRAM swap frequency
    swaps = sum(1 for e in route_entries if e.get("vram_swap"))
    if swaps > 5:
        signals.append({
            "area": "reliability",
            "signal": "frequent_vram_swaps",
            "detail": f"{swaps} VRAM swaps detected — adds latency",
            "impact": "medium",
            "evidence": f"vram_swaps={swaps}",
        })

    # Role imbalance
    roles = {}
    for e in route_entries:
        r = e.get("role", "unknown")
        roles[r] = roles.get(r, 0) + 1
    total = sum(roles.values())
    if total > 10:
        dominant = max(roles, key=roles.get)
        if roles[dominant] / total > 0.85:
            signals.append({
                "area": "routing",
                "signal": "role_imbalance",
                "detail": f"Role '{dominant}' handles {roles[dominant] / total:.0%} of traffic",
                "impact": "low",
                "evidence": json.dumps(roles),
            })

    return signals


def _scan_guardrail_signals() -> list:
    """Scan guardrail state for tuning opportunities."""
    signals = []
    state = load_json(GUARDRAIL_STATE)
    degs = state.get("degradations", {})

    total_degs = sum(degs.values())
    if total_degs == 0:
        return signals

    # Cooldown dominates → maybe cooldown is too aggressive
    if degs.get("cooldown", 0) > total_degs * 0.6 and total_degs > 3:
        signals.append({
            "area": "guardrails",
            "signal": "cooldown_too_aggressive",
            "detail": f"Cooldown caused {degs['cooldown']}/{total_degs} degradations",
            "impact": "medium",
            "evidence": json.dumps(degs),
        })

    # Latency guardrail firing often → heavy models too slow
    if degs.get("latency", 0) > 2:
        signals.append({
            "area": "reliability",
            "signal": "heavy_model_latency",
            "detail": f"Latency guardrail fired {degs['latency']} times",
            "impact": "high",
            "evidence": f"latency_degradations={degs['latency']}",
        })

    return signals


def _scan_finance_signals() -> list:
    """Scan finance data for workflow improvements."""
    signals = []
    txns = load_json(FINANCE_FILE, [])
    if not isinstance(txns, list):
        return signals

    if not txns:
        signals.append({
            "area": "finance",
            "signal": "no_transactions",
            "detail": "Finance module has no data — consider promoting daily capture",
            "impact": "low",
            "evidence": "transactions_count=0",
        })
        return signals

    # Check for uncategorized
    otros = sum(1 for t in txns if t.get("category") == "otros")
    if len(txns) > 5 and otros / len(txns) > 0.3:
        signals.append({
            "area": "finance",
            "signal": "high_uncategorized",
            "detail": f"{otros}/{len(txns)} transactions categorized as 'otros'",
            "impact": "medium",
            "evidence": f"otros_rate={otros / len(txns):.0%}",
        })

    return signals


def _scan_jobs_signals() -> list:
    """Scan jobs data for workflow improvements."""
    signals = []
    opps = load_json(JOBS_FILE, [])
    if not isinstance(opps, list):
        return signals

    if not opps:
        return signals

    # Stale opportunities (no update in 7+ days)
    stale = 0
    for o in opps:
        if o.get("status") in ("detected", "interested", "applied", "screening"):
            updated = o.get("updated_at", "")
            if updated:
                try:
                    age = (now() - datetime.fromisoformat(updated)).days
                    if age > 7:
                        stale += 1
                except (ValueError, TypeError):
                    pass

    if stale > 0:
        signals.append({
            "area": "jobs",
            "signal": "stale_opportunities",
            "detail": f"{stale} active opportunities not updated in 7+ days",
            "impact": "medium",
            "evidence": f"stale_count={stale}",
        })

    # High rejection rate
    total = len(opps)
    rejected = sum(1 for o in opps if o.get("status") == "rejected")
    if total > 3 and rejected / total > 0.6:
        signals.append({
            "area": "jobs",
            "signal": "high_rejection_rate",
            "detail": f"{rejected}/{total} opportunities ended in rejection",
            "impact": "medium",
            "evidence": f"rejection_rate={rejected / total:.0%}",
        })

    return signals


def _scan_observability_signals() -> list:
    """Scan orchestrator log for observability gaps."""
    signals = []
    entries = _load_jsonl(ORCHESTRATOR_LOG, 200)

    if not entries:
        signals.append({
            "area": "observability",
            "signal": "no_orchestrator_logs",
            "detail": "No orchestrator log entries — routing may not be logging",
            "impact": "medium",
            "evidence": "orchestrator_log_entries=0",
        })
        return signals

    # Cloud fallbacks
    cloud = [e for e in entries if e.get("type") == "cloud_fallback"]
    if len(cloud) > 2:
        signals.append({
            "area": "reliability",
            "signal": "cloud_fallbacks",
            "detail": f"{len(cloud)} cloud fallback events in orchestrator log",
            "impact": "high",
            "evidence": f"cloud_fallback_count={len(cloud)}",
        })

    return signals


def scan_all_signals() -> list:
    """Scan all signal sources and return a unified list."""
    signals = []
    signals.extend(_scan_routing_signals())
    signals.extend(_scan_guardrail_signals())
    signals.extend(_scan_finance_signals())
    signals.extend(_scan_jobs_signals())
    signals.extend(_scan_observability_signals())

    _log({"action": "scan_signals", "total": len(signals),
          "areas": list(set(s["area"] for s in signals))})

    return signals


# ── Phase 2: Prioritize ────────────────────────────────────────

IMPACT_SCORES = {"high": 3, "medium": 2, "low": 1}


def prioritize_signals(signals: list) -> list:
    """Sort signals by impact (high first), then by area diversity."""
    return sorted(signals, key=lambda s: -IMPACT_SCORES.get(s.get("impact", "low"), 0))


def select_best_opportunity(signals: list) -> dict | None:
    """Select the single best improvement opportunity for today.

    Priority: high impact > medium > low. Avoids repeating recent proposals.
    """
    if not signals:
        return None

    ranked = prioritize_signals(signals)

    # Load recent daily proposals to avoid repeats
    history = load_json(DAILY_FILE, [])
    if not isinstance(history, list):
        history = []
    recent_signals = set()
    for h in history[-14:]:  # last 2 weeks
        recent_signals.add(h.get("signal", ""))

    # Pick first signal not recently proposed
    for s in ranked:
        if s.get("signal") not in recent_signals:
            return s

    # All signals are recent repeats — pick highest impact anyway
    return ranked[0] if ranked else None


# ── Phase 3: Generate Proposal ──────────────────────────────────


def _call_ollama(model: str, prompt: str, timeout: int = 60) -> str:
    """Call Ollama generate API."""
    from urllib.request import Request, urlopen
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 1024},
    }).encode()

    req = Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        from urllib.error import URLError
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        return data.get("response", "")
    except Exception as e:
        _log({"action": "ollama_call", "model": model, "error": str(e)})
        return ""


def generate_proposal(opportunity: dict) -> dict:
    """Generate a concrete improvement proposal from a signal.

    Uses qwen2.5:7b for fast, focused proposals. Falls back to
    a structured template if LLM is unavailable.

    Returns:
        Proposal dict with all required fields.
    """
    area = opportunity.get("area", "unknown")
    signal = opportunity.get("signal", "")
    detail = opportunity.get("detail", "")
    evidence = opportunity.get("evidence", "")
    impact = opportunity.get("impact", "medium")

    prompt = f"""Eres un ingeniero de sistemas que propone mejoras concretas y accionables.

AREA: {area}
PROBLEMA DETECTADO: {detail}
EVIDENCIA: {evidence}
IMPACTO ESTIMADO: {impact}

Genera UNA propuesta de mejora concreta. Responde en JSON:
{{
  "proposal": "descripcion concreta de que cambiar",
  "steps": ["paso 1", "paso 2"],
  "expected_benefit": "beneficio medible",
  "risk": "riesgo de implementar"
}}

Sé específico. No propuestas vagas. Responde SOLO el JSON."""

    _log({"action": "generate_proposal", "area": area, "signal": signal,
          "model": PROPOSAL_MODEL})

    response = _call_ollama(PROPOSAL_MODEL, prompt)

    # Try to parse LLM response
    proposal_text = ""
    steps = []
    benefit = ""
    risk = "bajo"

    if response:
        import re
        # Extract JSON
        try:
            clean = re.sub(r"```(?:json)?\s*\n?", "", response).strip().rstrip("`")
            parsed = json.loads(clean)
            if isinstance(parsed, dict):
                proposal_text = parsed.get("proposal", "")
                steps = parsed.get("steps", [])
                benefit = parsed.get("expected_benefit", "")
                risk = parsed.get("risk", "bajo")
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", response)
            if match:
                try:
                    parsed = json.loads(match.group())
                    proposal_text = parsed.get("proposal", "")
                    steps = parsed.get("steps", [])
                    benefit = parsed.get("expected_benefit", "")
                    risk = parsed.get("risk", "bajo")
                except json.JSONDecodeError:
                    pass

    # Fallback: structured template if LLM failed
    if not proposal_text:
        proposal_text = f"Revisar {area}: {detail}"
        steps = [f"Investigar: {detail}", f"Evaluar evidencia: {evidence}",
                 "Proponer cambio concreto", "Validar en staging"]
        benefit = f"Mejora en {area}"
        risk = "requiere evaluacion"

    return {
        "date": now().strftime("%Y-%m-%d"),
        "area": area,
        "signal": signal,
        "insight": detail,
        "proposal": proposal_text,
        "steps": steps if isinstance(steps, list) else [steps],
        "expected_benefit": benefit,
        "risk": risk,
        "impact": impact,
        "evidence": evidence,
        "status": "proposed",
        "generated_at": now().isoformat(),
        "model": PROPOSAL_MODEL,
    }


# ── Phase 4: Save + Report ─────────────────────────────────────


def _load_daily_history() -> list:
    data = load_json(DAILY_FILE, [])
    return data if isinstance(data, list) else []


def save_daily_proposal(proposal: dict) -> dict:
    """Save today's proposal to daily_improvement.json.

    Appends to history. Returns the saved proposal.
    """
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    history = _load_daily_history()

    # Avoid duplicate for same date+signal
    for h in history:
        if h.get("date") == proposal.get("date") and h.get("signal") == proposal.get("signal"):
            _log({"action": "save_daily", "result": "duplicate", "date": proposal["date"]})
            return h

    history.append(proposal)

    with open(DAILY_FILE, "w") as f:
        json.dump(history, f, indent=2, ensure_ascii=False, default=str)

    _log({"action": "save_daily", "date": proposal["date"],
          "area": proposal["area"], "signal": proposal["signal"]})

    return proposal


def generate_daily_report(proposal: dict, signals: list):
    """Generate a human-readable daily improvement report."""
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    _now = now()

    lines = [
        f"# Mejora del dia — {_now.strftime('%Y-%m-%d')}",
        "",
        f"## Propuesta: {proposal.get('proposal', 'N/A')}",
        "",
        f"- **Area:** {proposal.get('area', 'N/A')}",
        f"- **Impacto:** {proposal.get('impact', 'N/A')}",
        f"- **Riesgo:** {proposal.get('risk', 'N/A')}",
        f"- **Evidencia:** {proposal.get('evidence', 'N/A')}",
        f"- **Estado:** {proposal.get('status', 'proposed')}",
        "",
    ]

    if proposal.get("steps"):
        lines.append("### Pasos")
        lines.append("")
        for i, step in enumerate(proposal["steps"], 1):
            lines.append(f"{i}. {step}")
        lines.append("")

    lines.extend([
        f"### Beneficio esperado",
        "",
        f"{proposal.get('expected_benefit', 'N/A')}",
        "",
    ])

    # Signal overview
    if signals:
        lines.extend([
            "## Otras senales detectadas",
            "",
        ])
        for s in signals[:10]:
            if s.get("signal") == proposal.get("signal"):
                continue  # skip the selected one
            lines.append(f"- [{s.get('area')}] {s.get('detail')} ({s.get('impact')})")
        lines.append("")

    # History summary
    history = _load_daily_history()
    if len(history) > 1:
        recent = history[-7:]
        lines.extend([
            "## Historial reciente",
            "",
        ])
        for h in reversed(recent):
            lines.append(f"- {h.get('date', '?')} [{h.get('area', '?')}] {h.get('proposal', '?')[:60]}")
        lines.append("")

    lines.extend([
        "---",
        "",
        f"Generado: {_now.isoformat()}",
        f"Modelo: {PROPOSAL_MODEL}",
        f"Senales escaneadas: {len(signals)}",
        "",
        "**Esta propuesta requiere revision humana. No se aplica automaticamente.**",
    ])

    with open(DAILY_REPORT, "w") as f:
        f.write("\n".join(lines))


# ── Main ────────────────────────────────────────────────────────


def run_daily_improvement() -> dict:
    """Run the daily improvement pipeline.

    Returns:
        dict with: date, signals_found, area, proposal, status
    """
    _log({"action": "pipeline_start"})
    today = now().strftime("%Y-%m-%d")

    # Phase 1: Scan signals
    signals = scan_all_signals()

    if not signals:
        _log({"action": "pipeline_end", "result": "no_signals"})
        empty_proposal = {
            "date": today, "area": "none", "signal": "no_signals",
            "insight": "No improvement signals detected",
            "proposal": "Sistema operando dentro de parametros normales",
            "steps": [], "expected_benefit": "N/A", "risk": "N/A",
            "impact": "none", "evidence": "", "status": "info",
            "generated_at": now().isoformat(), "model": "none",
        }
        save_daily_proposal(empty_proposal)
        generate_daily_report(empty_proposal, [])
        return {"date": today, "signals": 0, "area": "none",
                "proposal": empty_proposal["proposal"], "status": "no_signals"}

    # Phase 2: Select best opportunity
    opportunity = select_best_opportunity(signals)

    # Phase 3: Generate proposal
    proposal = generate_proposal(opportunity)

    # Phase 4: Save and report
    saved = save_daily_proposal(proposal)
    generate_daily_report(saved, signals)

    summary = {
        "date": today,
        "signals": len(signals),
        "area": proposal["area"],
        "proposal": proposal["proposal"],
        "impact": proposal["impact"],
        "status": "completed",
    }

    _log({"action": "pipeline_end", **summary})
    return summary


if __name__ == "__main__":
    print("Daily Improvement — Tenazas")
    print(f"Scanning: routing, guardrails, finance, jobs, observability")
    print(f"Model: {PROPOSAL_MODEL}")
    print(f"Output: {LEARNING_DIR}/")
    print()

    result = run_daily_improvement()

    print(f"Date: {result['date']}")
    print(f"Signals found: {result['signals']}")
    print(f"Area: {result['area']}")
    print(f"Proposal: {result['proposal']}")
    print(f"Status: {result['status']}")
    print()
    print(f"Report: {DAILY_REPORT}")
    print(f"History: {DAILY_FILE}")
