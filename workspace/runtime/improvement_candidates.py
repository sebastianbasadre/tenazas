#!/usr/bin/env python3
"""
Improvement Candidates — Transform mined errors into actionable proposals.

Takes error_mining output and generates typed improvement proposals:
- prompt_tweak: repeated dubious classifications
- router_rule: tool with repeated failures
- new_runbook: incident repeated 3+ times
- new_test: failure not covered by existing tests
- new_skill: query pattern with no skill assigned
- guardrail_policy: repeated false alerts

Output: state/improvement_candidates.json
Proposals NEVER auto-promote to production — everything stays in staging.

Usage:
    python3 improvement_candidates.py --generate    # from today's errors
    python3 improvement_candidates.py --list        # show all candidates
    python3 improvement_candidates.py --approve ID  # approve a candidate
    python3 improvement_candidates.py --reject ID   # reject a candidate
    python3 improvement_candidates.py --stage ID    # move to staging
"""

import json
import sys
from collections import Counter
from datetime import datetime
from hashlib import sha256
from pathlib import Path

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
CANDIDATES_FILE = STATE_DIR / "improvement_candidates.json"
ERROR_MINING_DIR = STATE_DIR / "error_mining"
STAGING_DIR = BASE_DIR.parent / "staging" if Path("/home/tenazas").exists() else BASE_DIR.parent / "staging"
LOG_FILE = BASE_DIR / "logs" / "improvement_candidates.log"


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def _load_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _save_candidates(candidates: list):
    CANDIDATES_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CANDIDATES_FILE, "w") as f:
        json.dump(candidates, f, indent=2, default=str)


def _candidate_id(ctype: str, title: str) -> str:
    return sha256(f"{ctype}:{title}".encode()).hexdigest()[:12]


# ── Generators ──────────────────────────────────────────────────


def _gen_prompt_tweaks(errors: list) -> list:
    """Generate prompt_tweak proposals from dubious classifications."""
    dubious = [e for e in errors if e["type"] == "dubious_classification"]
    if len(dubious) < 2:
        return []

    # Group by classification target
    by_target = Counter()
    examples = {}
    for e in dubious:
        detail = e.get("detail", "")
        # Extract "classified as X"
        if "classified as" in detail:
            target = detail.split("classified as")[1].split(" via")[0].strip()
            by_target[target] += 1
            if target not in examples:
                examples[target] = detail[:120]

    proposals = []
    for target, count in by_target.items():
        if count >= 2:
            proposals.append({
                "type": "prompt_tweak",
                "title": f"Mejorar clasificacion para '{target}'",
                "description": f"{count} queries clasificadas como '{target}' cayeron a keyword/regex "
                               f"en vez de modelo. Considerar agregar ejemplos al dataset o ajustar "
                               f"los patterns del orchestrator.",
                "expected_gain": f"Reducir {count} clasificaciones dudosas/dia",
                "risk": "low",
                "evidence": [examples.get(target, "")],
            })

    return proposals


def _gen_router_rules(errors: list) -> list:
    """Generate router_rule proposals from tool failures."""
    failures = [e for e in errors if e["type"] == "tool_failure"]
    if not failures:
        return []

    by_subtype = Counter()
    for e in failures:
        by_subtype[e.get("subtype", "unknown")] += 1

    proposals = []
    for subtype, count in by_subtype.items():
        if count >= 2:
            proposals.append({
                "type": "router_rule",
                "title": f"Agregar regla de router para '{subtype}'",
                "description": f"{count} fallos de tipo '{subtype}' detectados. "
                               f"Considerar agregar fallback rule o ajustar routing logic.",
                "expected_gain": f"Reducir {count} fallos de routing/dia",
                "risk": "medium",
                "evidence": [e["detail"][:120] for e in failures if e.get("subtype") == subtype][:3],
            })

    return proposals


def _gen_runbooks(errors: list) -> list:
    """Generate new_runbook proposals from repeated incidents."""
    repeated = [e for e in errors if e["type"] == "repeated_incident"]

    proposals = []
    for e in repeated:
        count = e.get("count", 3)
        proposals.append({
            "type": "new_runbook",
            "title": f"Runbook para incidente recurrente ({count}x)",
            "description": f"Incidente repetido {count} veces: {e['detail'][:150]}. "
                           f"Crear runbook con pasos de diagnostico y remediacion.",
            "expected_gain": "Reducir tiempo de resolucion en incidentes futuros",
            "risk": "low",
            "evidence": [e["detail"][:200]],
        })

    return proposals


def _gen_tests(errors: list) -> list:
    """Generate new_test proposals from uncovered failures."""
    chain_failures = [e for e in errors if e["type"] == "chain_failure"]
    timeouts = [e for e in errors if e["type"] == "timeout"]

    proposals = []
    if chain_failures:
        proposals.append({
            "type": "new_test",
            "title": f"Tests para {len(chain_failures)} chain failures",
            "description": "Skill chain failures sin cobertura de tests. "
                           "Agregar test cases que simulen estas fallas.",
            "expected_gain": "Detectar chain failures antes de produccion",
            "risk": "low",
            "evidence": [e["detail"][:120] for e in chain_failures[:3]],
        })

    if len(timeouts) >= 3:
        proposals.append({
            "type": "new_test",
            "title": f"Tests de timeout ({len(timeouts)} eventos)",
            "description": "Multiples timeouts detectados. Agregar tests de latencia "
                           "con umbrales para detectar regresiones.",
            "expected_gain": "Detectar regresiones de latencia en CI",
            "risk": "low",
            "evidence": [e["detail"][:120] for e in timeouts[:3]],
        })

    return proposals


def _gen_guardrail_policies(errors: list) -> list:
    """Generate guardrail_policy proposals from false alerts."""
    false_alerts = [e for e in errors if e["type"] == "false_alert"]
    if len(false_alerts) < 2:
        return []

    proposals = [{
        "type": "guardrail_policy",
        "title": f"Ajustar umbrales de alerta ({len(false_alerts)} falsas)",
        "description": f"{len(false_alerts)} alertas se auto-resolvieron en < 5 min. "
                       f"Considerar agregar cooldown o subir umbral de severidad.",
        "expected_gain": f"Eliminar ~{len(false_alerts)} falsas alertas/dia",
        "risk": "medium",
        "evidence": [e["detail"][:120] for e in false_alerts[:3]],
    }]

    return proposals


# ── Main API ────────────────────────────────────────────────────


def generate(date: str = None) -> list:
    """Generate improvement candidates from mined errors.

    Args:
        date: YYYY-MM-DD to process (default: today)

    Returns: list of new candidates generated
    """
    if not date:
        date = _now().strftime("%Y-%m-%d")

    # Load errors
    error_file = ERROR_MINING_DIR / f"daily_{date}.json"
    report = _load_json(error_file, {})
    errors = report.get("errors", [])

    if not errors:
        _log({"action": "generate", "date": date, "errors": 0, "candidates": 0})
        return []

    # Run all generators
    generators = [
        _gen_prompt_tweaks,
        _gen_router_rules,
        _gen_runbooks,
        _gen_tests,
        _gen_guardrail_policies,
    ]

    new_candidates = []
    for gen in generators:
        try:
            proposals = gen(errors)
            new_candidates.extend(proposals)
        except Exception as e:
            _log({"action": "generator_error", "generator": gen.__name__, "error": str(e)[:200]})

    # Load existing candidates
    existing = _load_json(CANDIDATES_FILE, [])
    if not isinstance(existing, list):
        existing = []
    existing_ids = {c.get("id") for c in existing}

    # Add metadata and dedup
    added = 0
    for c in new_candidates:
        c["id"] = _candidate_id(c["type"], c["title"])
        c["status"] = "proposed"
        c["created_at"] = _now().isoformat()
        c["source_date"] = date

        if c["id"] not in existing_ids:
            existing.append(c)
            existing_ids.add(c["id"])
            added += 1

    # Cap at 100 candidates
    existing = existing[-100:]
    _save_candidates(existing)

    _log({"action": "generate", "date": date, "errors": len(errors),
          "candidates_new": added, "candidates_total": len(existing)})

    return [c for c in new_candidates if c["id"] not in existing_ids or added > 0]


def list_candidates(status: str = None) -> list:
    """List candidates, optionally filtered by status."""
    candidates = _load_json(CANDIDATES_FILE, [])
    if not isinstance(candidates, list):
        return []
    if status:
        return [c for c in candidates if c.get("status") == status]
    return candidates


def update_status(candidate_id: str, new_status: str, note: str = "") -> bool:
    """Update candidate status: proposed → staged → approved | rejected."""
    valid_transitions = {
        "proposed": ["staged", "rejected"],
        "staged": ["approved", "rejected"],
    }

    candidates = _load_json(CANDIDATES_FILE, [])
    if not isinstance(candidates, list):
        return False

    for c in candidates:
        if c.get("id") == candidate_id:
            current = c.get("status", "proposed")
            if new_status not in valid_transitions.get(current, []):
                return False
            c["status"] = new_status
            c[f"{new_status}_at"] = _now().isoformat()
            if note:
                c["note"] = note
            _save_candidates(candidates)
            _log({"action": "status_change", "id": candidate_id,
                  "from": current, "to": new_status})
            return True

    return False


def stage_candidate(candidate_id: str) -> bool:
    """Move candidate to staging directory."""
    candidates = _load_json(CANDIDATES_FILE, [])
    if not isinstance(candidates, list):
        return False

    for c in candidates:
        if c.get("id") == candidate_id and c.get("status") == "proposed":
            # Write to staging
            staging_type = c["type"]
            staging_subdir = {
                "prompt_tweak": "prompts",
                "router_rule": "router_rules",
                "new_runbook": "runbooks",
                "new_test": "tests",
                "new_skill": "datasets",
                "guardrail_policy": "router_rules",
            }.get(staging_type, "prompts")

            staging_path = STAGING_DIR / staging_subdir / f"{candidate_id}.json"
            staging_path.parent.mkdir(parents=True, exist_ok=True)
            with open(staging_path, "w") as f:
                json.dump(c, f, indent=2, default=str)

            return update_status(candidate_id, "staged")

    return False


# ── CLI ─────────────────────────────────────────────────────────


def format_candidates(candidates: list) -> str:
    if not candidates:
        return "No improvement candidates."

    lines = [f"Improvement Candidates ({len(candidates)}):\n"]
    status_icons = {"proposed": "?", "staged": "S", "approved": "OK", "rejected": "X"}
    risk_icons = {"low": "-", "medium": "*", "high": "!"}

    for c in candidates:
        icon = status_icons.get(c.get("status", "?"), "?")
        risk = risk_icons.get(c.get("risk", ""), "?")
        lines.append(f"  [{icon}] {c.get('id', '?')} [{c['type']}] {risk} {c['title']}")
        if c.get("expected_gain"):
            lines.append(f"      gain: {c['expected_gain']}")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Improvement Candidates")
    p.add_argument("--generate", action="store_true", help="Generate from today's errors")
    p.add_argument("--list", action="store_true", help="List all candidates")
    p.add_argument("--status", type=str, help="Filter by status")
    p.add_argument("--approve", type=str, metavar="ID", help="Approve a candidate")
    p.add_argument("--reject", type=str, metavar="ID", help="Reject a candidate")
    p.add_argument("--stage", type=str, metavar="ID", help="Move candidate to staging")
    args = p.parse_args()

    if args.generate:
        candidates = generate()
        print(f"Generated {len(candidates)} new candidates")
        print(format_candidates(candidates))
    elif args.list:
        candidates = list_candidates(status=args.status)
        print(format_candidates(candidates))
    elif args.approve:
        ok = update_status(args.approve, "approved")
        print("Approved" if ok else "Failed (invalid ID or transition)")
    elif args.reject:
        ok = update_status(args.reject, "rejected")
        print("Rejected" if ok else "Failed (invalid ID or transition)")
    elif args.stage:
        ok = stage_candidate(args.stage)
        print("Staged" if ok else "Failed (invalid ID or transition)")
    else:
        p.print_help()
