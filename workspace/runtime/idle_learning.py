#!/usr/bin/env python3
"""
Idle Learning Engine — Multi-model collaborative analysis of routing decisions.

Uses a Generator→Critic→Synthesizer→Governor pipeline to analyze past routing
decisions and generate improvement proposals WITHOUT auto-applying them.

Pipeline:
  1. Generator: load_recent_decisions() reads model_router.log + guardrail_state.json
  2. Critic: deepseek-r1:14b analyzes decisions for problems
  3. Synthesizer: qwen2.5:32b generates structured proposals from critic output
  4. Governor: pure Python — validates, deduplicates, saves proposals

Security: NEVER modifies router config, existing files, or applies rules automatically.
All output goes to runtime/learning/ only.

Usage:
  docker exec openclaw python3 /home/tenazas/runtime/idle_learning.py

Future integration:
  - Connect to Prometheus via HTTP API for richer metrics context
  - Feed approved proposals into adaptive_router.py for learned preferences
  - Add auto-approval for high-confidence, low-risk proposals (requires explicit opt-in)
  - Schedule via cron during idle hours (2-6AM) when HomeLab is underutilized
"""

import hashlib
import json
import re
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

# ── Config ──────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
LOG_DIR = BASE_DIR / "logs"
LEARNING_DIR = BASE_DIR / "learning"
PROPOSALS_FILE = LEARNING_DIR / "proposals.json"
REPORT_FILE = LEARNING_DIR / "learning_report.md"
TELEMETRY_LOG = LOG_DIR / "idle_learning_v2.log"

ROUTER_LOG = LOG_DIR / "model_router.log"
GUARDRAIL_STATE = BASE_DIR / "guardrail_state.json"

OLLAMA_URL = "http://192.168.2.6:11434"
CRITIC_MODEL = "deepseek-r1:14b"
SYNTHESIZER_MODEL = "qwen2.5:32b"

# Limits
MAX_DECISIONS = 50
MAX_ISSUES = 10
MAX_PROPOSALS = 10
LLM_TIMEOUT = 120  # seconds


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    """Append a JSON line to idle_learning_v2.log."""
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Phase 1: Generator — Load Recent Decisions ─��───────────────


def load_recent_decisions(limit: int = MAX_DECISIONS) -> list:
    """Load recent routing decisions from model_router.log.

    Filters for route_v2 entries that have interesting signals:
    fallbacks, guardrail degradations, VRAM swaps, heavy model usage.

    Returns:
        List of decision dicts, most recent first, up to `limit` entries.
    """
    decisions = []

    # Read routing decisions
    try:
        with open(ROUTER_LOG) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("action") != "route_v2":
                    continue
                decisions.append(entry)
    except FileNotFoundError:
        _log({"action": "load_decisions", "error": "router log not found"})
        return []

    # Most recent first
    decisions = decisions[-limit * 3:]  # overfetch, then filter
    decisions.reverse()

    # Enrich with guardrail context
    guardrail_stats = {}
    try:
        with open(GUARDRAIL_STATE) as f:
            guardrail_stats = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    # Filter to interesting decisions (not all plain fast→7b are useful)
    interesting = []
    for d in decisions:
        dominated = (
            d.get("fallback_used")
            or d.get("degraded")
            or d.get("vram_swap")
            or d.get("role") in ("heavy", "code", "reason")
            or d.get("result") == "no_backend_available"
        )
        if dominated:
            interesting.append(d)

    # If not enough interesting ones, pad with recent non-interesting
    if len(interesting) < limit:
        for d in decisions:
            if d not in interesting:
                interesting.append(d)
            if len(interesting) >= limit:
                break

    result = interesting[:limit]

    _log({
        "action": "load_decisions",
        "total_in_log": len(decisions),
        "interesting": len([d for d in result if d.get("fallback_used") or d.get("degraded")]),
        "returned": len(result),
        "guardrail_degradations": guardrail_stats.get("degradations", {}),
    })

    return result


# ── Phase 2: Critic — deepseek-r1:14b analyzes decisions ───────


def _call_ollama(model: str, prompt: str, timeout: int = LLM_TIMEOUT) -> str:
    """Call Ollama generate API. Returns response text or empty string on error."""
    payload = json.dumps({
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {"temperature": 0.3, "num_predict": 2048},
    }).encode()

    req = Request(
        f"{OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )

    try:
        resp = urlopen(req, timeout=timeout)
        data = json.loads(resp.read())
        return data.get("response", "")
    except (URLError, json.JSONDecodeError, OSError) as e:
        _log({"action": "ollama_call", "model": model, "error": str(e)})
        return ""


def _extract_json(text: str) -> list | dict | None:
    """Extract JSON from LLM response. Handles markdown code blocks."""
    # Try direct parse
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Try extracting from markdown code block
    match = re.search(r"```(?:json)?\s*\n?([\s\S]*?)```", text)
    if match:
        try:
            return json.loads(match.group(1).strip())
        except json.JSONDecodeError:
            pass

    # Try finding array or object boundaries
    for start, end in [("[", "]"), ("{", "}")]:
        idx_start = text.find(start)
        idx_end = text.rfind(end)
        if idx_start >= 0 and idx_end > idx_start:
            try:
                return json.loads(text[idx_start:idx_end + 1])
            except json.JSONDecodeError:
                pass

    return None


def run_critic(decisions: list) -> list:
    """Analyze routing decisions using deepseek-r1:14b.

    Args:
        decisions: list of routing decision dicts from load_recent_decisions()

    Returns:
        List of issue dicts: [{issue, pattern, impact, confidence}, ...]
    """
    if not decisions:
        _log({"action": "critic", "result": "no_decisions"})
        return []

    # Build summary for the critic
    summary_lines = []
    stats = {
        "total": len(decisions),
        "fallbacks": 0, "degraded": 0, "vram_swaps": 0,
        "heavy": 0, "code": 0, "reason": 0, "fast": 0,
    }

    for d in decisions:
        role = d.get("role", "?")
        model = d.get("model_chosen", "?")
        fallback = d.get("fallback_used", False)
        degraded = d.get("degraded", False)
        vram = d.get("vram_swap", False)
        degrade_reason = d.get("degrade_reason", "")
        preview = d.get("prompt_preview", "")[:60]

        stats["fallbacks"] += int(fallback)
        stats["degraded"] += int(degraded)
        stats["vram_swaps"] += int(vram)
        stats[role] = stats.get(role, 0) + 1

        flags = []
        if fallback:
            flags.append("FALLBACK")
        if degraded:
            flags.append(f"DEGRADED({degrade_reason})")
        if vram:
            flags.append("VRAM_SWAP")

        flag_str = " " + " ".join(flags) if flags else ""
        summary_lines.append(f"- role={role} model={model}{flag_str} prompt=\"{preview}\"")

    summary = "\n".join(summary_lines[:30])  # Cap for context window
    stats_str = json.dumps(stats, indent=2)

    prompt = f"""Eres un analista de sistemas de routing de modelos LLM.

Analiza estas {stats['total']} decisiones recientes de routing y detecta problemas.

ESTADÍSTICAS:
{stats_str}

DECISIONES RECIENTES:
{summary}

Responde SOLO con un array JSON de problemas detectados. Cada problema:
{{"issue": "descripción corta", "pattern": "condición que lo causa", "impact": "efecto negativo", "confidence": 0.0-1.0}}

Si no hay problemas, responde con un array vacío [].
Responde SOLO el JSON, sin explicación adicional."""

    _log({"action": "critic_start", "decisions": len(decisions), "model": CRITIC_MODEL})

    response = _call_ollama(CRITIC_MODEL, prompt)
    if not response:
        _log({"action": "critic", "result": "no_response"})
        return []

    issues = _extract_json(response)
    if issues is None:
        _log({"action": "critic", "result": "json_parse_failed", "raw_len": len(response)})
        return []

    if isinstance(issues, dict):
        issues = [issues]
    if not isinstance(issues, list):
        return []

    # Validate and cap
    valid = []
    for issue in issues[:MAX_ISSUES]:
        if not isinstance(issue, dict):
            continue
        if "issue" not in issue:
            continue
        valid.append({
            "issue": str(issue.get("issue", "")),
            "pattern": str(issue.get("pattern", "")),
            "impact": str(issue.get("impact", "")),
            "confidence": min(1.0, max(0.0, float(issue.get("confidence", 0.5)))),
        })

    _log({"action": "critic_done", "issues_found": len(valid)})
    return valid


# ── Phase 3: Synthesizer — qwen2.5:32b generates proposals ────


def run_synthesizer(issues: list) -> list:
    """Generate structured improvement proposals from critic issues.

    Args:
        issues: list of issue dicts from run_critic()

    Returns:
        List of proposal dicts with type, description, condition, action, etc.
    """
    if not issues:
        _log({"action": "synthesizer", "result": "no_issues"})
        return []

    issues_str = json.dumps(issues, indent=2, ensure_ascii=False)

    prompt = f"""Eres un ingeniero de sistemas que diseña reglas de routing para modelos LLM.

Dado estos problemas detectados en el sistema de routing:
{issues_str}

Genera propuestas de mejora ESTRUCTURADAS. Cada propuesta debe ser un objeto JSON con:
- "type": uno de ["routing_adjustment", "guardrail_tuning", "threshold_change", "classification_fix"]
- "description": qué cambiar y por qué
- "condition": objeto con las condiciones que disparan la regla
- "action": objeto con la acción propuesta
- "confidence": 0.0-1.0
- "expected_benefit": beneficio esperado
- "risk": riesgo de implementar

Responde SOLO con un array JSON de propuestas. Sin explicación adicional.
Si no hay propuestas viables, responde con []."""

    _log({"action": "synthesizer_start", "issues": len(issues), "model": SYNTHESIZER_MODEL})

    response = _call_ollama(SYNTHESIZER_MODEL, prompt, timeout=180)
    if not response:
        _log({"action": "synthesizer", "result": "no_response"})
        return []

    proposals = _extract_json(response)
    if proposals is None:
        _log({"action": "synthesizer", "result": "json_parse_failed", "raw_len": len(response)})
        return []

    if isinstance(proposals, dict):
        proposals = [proposals]
    if not isinstance(proposals, list):
        return []

    # Validate schema
    valid_types = {"routing_adjustment", "guardrail_tuning", "threshold_change", "classification_fix"}
    valid = []
    for p in proposals[:MAX_PROPOSALS]:
        if not isinstance(p, dict):
            continue
        if "description" not in p:
            continue
        valid.append({
            "type": p.get("type", "routing_adjustment") if p.get("type") in valid_types else "routing_adjustment",
            "description": str(p.get("description", "")),
            "condition": p.get("condition", {}),
            "action": p.get("action", {}),
            "confidence": min(1.0, max(0.0, float(p.get("confidence", 0.5)))),
            "expected_benefit": str(p.get("expected_benefit", "")),
            "risk": str(p.get("risk", "unknown")),
            "status": "proposed",
            "created_at": _now().isoformat(),
            "source_issues": [i.get("issue", "") for i in issues],
        })

    _log({"action": "synthesizer_done", "proposals_generated": len(valid)})
    return valid


# ── Phase 4: Governor — Python validation + storage + review ────

# Valid status transitions:
#   proposed → reviewed → accepted | rejected
#   (no going back from accepted/rejected)
VALID_STATUSES = {"proposed", "reviewed", "accepted", "rejected"}
VALID_TRANSITIONS = {
    "proposed": {"reviewed"},
    "reviewed": {"accepted", "rejected"},
    "accepted": set(),   # terminal
    "rejected": set(),   # terminal
}


def _generate_proposal_id(proposal: dict) -> str:
    """Generate a stable short ID from proposal content."""
    key = f"{proposal.get('type', '')}:{proposal.get('description', '')}:{json.dumps(proposal.get('condition', {}), sort_keys=True)}"
    return hashlib.sha256(key.encode()).hexdigest()[:12]


def _load_proposals() -> list:
    """Load existing proposals from proposals.json."""
    try:
        with open(PROPOSALS_FILE) as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_proposals_file(proposals: list):
    """Write proposals list to proposals.json."""
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    with open(PROPOSALS_FILE, "w") as f:
        json.dump(proposals, f, indent=2, ensure_ascii=False, default=str)


def _is_duplicate(new: dict, existing: list) -> bool:
    """Check if a proposal is a duplicate of an existing one."""
    new_id = new.get("id", "")
    for e in existing:
        # Exact ID match
        if new_id and e.get("id") == new_id:
            return True
        # Same type + description
        if (e.get("type") == new.get("type")
                and e.get("description") == new.get("description")):
            return True
        # Fuzzy: same condition + action
        if (e.get("condition") == new.get("condition")
                and e.get("action") == new.get("action")
                and e.get("condition")):
            return True
    return False


def save_proposals(proposals: list) -> int:
    """Validate, deduplicate, and save proposals to proposals.json.

    Each proposal gets a stable ID for future reference in reviews.

    Args:
        proposals: list of proposal dicts from run_synthesizer()

    Returns:
        Number of new proposals saved.
    """
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    existing = _load_proposals()
    added = 0

    for p in proposals:
        # Schema validation
        if not isinstance(p, dict):
            continue
        if not p.get("description"):
            continue
        if p.get("status") != "proposed":
            continue

        # Assign stable ID if missing
        if not p.get("id"):
            p["id"] = _generate_proposal_id(p)

        # Dedup
        if _is_duplicate(p, existing):
            continue

        existing.append(p)
        added += 1

    _save_proposals_file(existing)

    _log({"action": "save_proposals", "new": added, "total": len(existing)})
    return added


# ── Review Workflow ─────────────────────────────────────────────


def review_proposal(
    proposal_id: str,
    decision: str,
    reviewer_note: str = "",
) -> bool:
    """Transition a proposal through the review workflow.

    Status transitions:
      proposed → reviewed    (mark as reviewed, add note)
      reviewed → accepted    (approve the proposal)
      reviewed → rejected    (reject the proposal)

    Args:
        proposal_id: the proposal's ID (12-char hex)
        decision: target status — "reviewed", "accepted", or "rejected"
        reviewer_note: optional note explaining the decision

    Returns:
        True if transition succeeded, False otherwise.
    """
    if decision not in VALID_STATUSES:
        _log({"action": "review", "id": proposal_id, "error": f"invalid status: {decision}"})
        return False

    proposals = _load_proposals()
    found = False

    for p in proposals:
        if p.get("id") != proposal_id:
            continue
        found = True

        current = p.get("status", "proposed")
        allowed = VALID_TRANSITIONS.get(current, set())

        if decision not in allowed:
            _log({
                "action": "review", "id": proposal_id,
                "error": f"invalid transition: {current} → {decision}",
            })
            return False

        p["status"] = decision
        p["reviewed_at"] = _now().isoformat()
        p["decision"] = decision
        if reviewer_note:
            p["reviewer_note"] = reviewer_note

        _log({
            "action": "review", "id": proposal_id,
            "from": current, "to": decision,
            "note": reviewer_note[:100] if reviewer_note else "",
        })
        break

    if not found:
        _log({"action": "review", "id": proposal_id, "error": "not found"})
        return False

    _save_proposals_file(proposals)
    return True


def list_proposals(status: str | None = None) -> list:
    """List proposals, optionally filtered by status.

    Args:
        status: filter by status (proposed/reviewed/accepted/rejected) or None for all

    Returns:
        List of proposal dicts.
    """
    proposals = _load_proposals()
    if status is not None:
        proposals = [p for p in proposals if p.get("status") == status]
    return proposals


def get_proposal(proposal_id: str) -> dict | None:
    """Get a single proposal by ID."""
    for p in _load_proposals():
        if p.get("id") == proposal_id:
            return p
    return None


def get_review_summary() -> dict:
    """Get counts by status for dashboard/reporting."""
    proposals = _load_proposals()
    summary = {"total": len(proposals)}
    for status in VALID_STATUSES:
        summary[status] = sum(1 for p in proposals if p.get("status") == status)
    return summary


# ── Phase 5: Report Generation ──────────────────────────────────


def generate_report(issues: list, proposals: list, decisions: list | None = None):
    """Generate a human-readable learning report.

    Includes new proposals from this run AND a summary of all known proposals
    by status (proposed/reviewed/accepted/rejected).

    Args:
        issues: critic output
        proposals: synthesizer output (new this run)
        decisions: original decisions (for stats)
    """
    LEARNING_DIR.mkdir(parents=True, exist_ok=True)
    now = _now()
    review = get_review_summary()

    lines = [
        f"# Idle Learning Report — {now.strftime('%Y-%m-%d %H:%M')} ART",
        "",
        "## Resumen ejecutivo",
        "",
    ]

    if not issues:
        lines.append("No se detectaron problemas en las decisiones recientes.")
    else:
        lines.append(f"Se detectaron **{len(issues)} problemas** y se generaron "
                      f"**{len(proposals)} propuestas** de mejora.")

    # Proposal inventory
    lines.extend([
        "",
        f"**Inventario de propuestas:** {review['total']} total — "
        f"{review.get('proposed', 0)} nuevas, "
        f"{review.get('reviewed', 0)} en revision, "
        f"{review.get('accepted', 0)} aceptadas, "
        f"{review.get('rejected', 0)} rechazadas",
        "",
    ])

    # Decision stats
    if decisions:
        stats = {"total": len(decisions)}
        for d in decisions:
            role = d.get("role", "unknown")
            stats[role] = stats.get(role, 0) + 1
            if d.get("fallback_used"):
                stats["fallbacks"] = stats.get("fallbacks", 0) + 1
            if d.get("degraded"):
                stats["degraded"] = stats.get("degraded", 0) + 1
            if d.get("vram_swap"):
                stats["vram_swaps"] = stats.get("vram_swaps", 0) + 1

        lines.extend([
            "## Decisiones analizadas",
            "",
            f"- Total: {stats['total']}",
            f"- Fallbacks: {stats.get('fallbacks', 0)}",
            f"- Degradaciones por guardrails: {stats.get('degraded', 0)}",
            f"- VRAM swaps: {stats.get('vram_swaps', 0)}",
            "",
            "### Distribucion por rol",
            "",
        ])
        for role in ("fast", "reason", "heavy", "code", "vision", "embed"):
            count = stats.get(role, 0)
            if count:
                lines.append(f"- {role}: {count}")
        lines.append("")

    # Issues
    if issues:
        lines.extend([
            "## Problemas detectados",
            "",
        ])
        for i, issue in enumerate(issues, 1):
            conf = issue.get("confidence", 0)
            lines.extend([
                f"### {i}. {issue.get('issue', 'Sin descripcion')}",
                "",
                f"- **Patron:** {issue.get('pattern', 'N/A')}",
                f"- **Impacto:** {issue.get('impact', 'N/A')}",
                f"- **Confianza:** {conf:.0%}",
                "",
            ])

    # New proposals (this run)
    if proposals:
        lines.extend([
            "## Propuestas nuevas (esta ejecucion)",
            "",
        ])
        for i, p in enumerate(proposals, 1):
            pid = p.get("id", "?")
            lines.extend([
                f"### {i}. [{pid}] {p.get('description', 'Sin descripcion')}",
                "",
                f"- **ID:** `{pid}`",
                f"- **Tipo:** {p.get('type', 'N/A')}",
                f"- **Condicion:** `{json.dumps(p.get('condition', {}))}`",
                f"- **Accion:** `{json.dumps(p.get('action', {}))}`",
                f"- **Beneficio esperado:** {p.get('expected_benefit', 'N/A')}",
                f"- **Riesgo:** {p.get('risk', 'N/A')}",
                f"- **Confianza:** {p.get('confidence', 0):.0%}",
                f"- **Estado:** {p.get('status', 'proposed')}",
                "",
            ])

    # Known proposals (all statuses)
    all_proposals = _load_proposals()
    reviewed = [p for p in all_proposals if p.get("status") in ("reviewed", "accepted", "rejected")]
    if reviewed:
        lines.extend([
            "## Propuestas ya revisadas",
            "",
        ])
        for p in reviewed:
            pid = p.get("id", "?")
            status = p.get("status", "?")
            icon = {"reviewed": "🔍", "accepted": "✅", "rejected": "❌"}.get(status, "?")
            note = p.get("reviewer_note", "")
            lines.extend([
                f"- {icon} **[{pid}]** {p.get('description', '?')} — **{status}**"
                + (f" — _{note}_" if note else ""),
            ])
        lines.append("")

    pending = [p for p in all_proposals if p.get("status") == "proposed"]
    if pending:
        lines.extend([
            "## Propuestas pendientes de revision",
            "",
        ])
        for p in pending:
            pid = p.get("id", "?")
            lines.append(f"- **[{pid}]** {p.get('description', '?')} (confianza: {p.get('confidence', 0):.0%})")
        lines.append("")

    # Footer
    lines.extend([
        "---",
        "",
        f"Generado: {now.isoformat()}",
        f"Critic: {CRITIC_MODEL}",
        f"Synthesizer: {SYNTHESIZER_MODEL}",
        "",
        "**Este reporte es informativo. Ninguna propuesta se aplica automaticamente.**",
    ])

    with open(REPORT_FILE, "w") as f:
        f.write("\n".join(lines))

    _log({"action": "report_generated", "issues": len(issues), "proposals": len(proposals),
           "review_summary": review})


# ── Main ────────────────────────────────────────────────────────


def run_idle_learning() -> dict:
    """Run the full idle learning pipeline.

    Returns:
        Summary dict with counts and status.
    """
    _log({"action": "pipeline_start"})

    # Phase 1: Load decisions
    decisions = load_recent_decisions()
    if not decisions:
        _log({"action": "pipeline_end", "result": "no_decisions"})
        generate_report([], [], [])
        return {"decisions": 0, "issues": 0, "proposals": 0, "status": "no_data"}

    # Phase 2: Critic
    issues = run_critic(decisions)

    # Phase 3: Synthesizer (only if critic found issues)
    proposals = []
    if issues:
        proposals = run_synthesizer(issues)

    # Phase 4: Governor
    new_saved = save_proposals(proposals)

    # Phase 5: Report
    generate_report(issues, proposals, decisions)

    summary = {
        "decisions": len(decisions),
        "issues": len(issues),
        "proposals": len(proposals),
        "new_saved": new_saved,
        "status": "completed",
    }

    _log({"action": "pipeline_end", **summary})
    return summary


if __name__ == "__main__":
    print("Idle Learning Engine — Tenazas")
    print(f"Reading from: {ROUTER_LOG}")
    print(f"Critic: {CRITIC_MODEL}")
    print(f"Synthesizer: {SYNTHESIZER_MODEL}")
    print(f"Output: {LEARNING_DIR}/")
    print()

    result = run_idle_learning()

    print(f"Decisions analyzed: {result['decisions']}")
    print(f"Issues found: {result['issues']}")
    print(f"Proposals generated: {result['proposals']}")
    print(f"New proposals saved: {result.get('new_saved', 0)}")
    print(f"Status: {result['status']}")
    print()
    print(f"Report: {REPORT_FILE}")
    print(f"Proposals: {PROPOSALS_FILE}")
