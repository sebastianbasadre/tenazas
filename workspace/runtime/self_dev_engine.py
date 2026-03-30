#!/usr/bin/env python3
"""
Self-Development Engine — Autonomous improvement loop (proposal only).

Analyzes metrics + self_debugger output to generate improvement proposals.
NEVER auto-modifies code. All proposals require explicit approval.

Storage: workspace/runtime/dev_proposals.json
Log: workspace/runtime/logs/self_dev.log
"""

import json
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
PROPOSALS_FILE = BASE_DIR / "dev_proposals.json"
TELEMETRY_LOG = BASE_DIR / "logs" / "self_dev.log"

# Config
MAX_PROPOSALS = 30


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _load_json(path: Path, default=None):
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
        json.dump(data, f, indent=2, default=str)


# ── Analysis Strategies ─────────────────────────────────────────


def _analyze_debug_reports() -> list:
    """Generate proposals from self_debugger scan results."""
    proposals = []

    try:
        from runtime import self_debugger
        scan = self_debugger.scan_all_logs()
    except (ImportError, Exception):
        return proposals

    for filename, report in scan.get("reports", {}).items():
        errors = report.get("errors", [])
        summary = report.get("summary", {})
        error_rate = summary.get("error_rate", 0)

        if error_rate > 0.3:
            module_name = filename.replace(".log", "")
            proposals.append({
                "module": module_name,
                "change_description": f"Refactorizar manejo de errores en {module_name} "
                                      f"(tasa de error: {error_rate:.0%})",
                "expected_benefit": "Reducir tasa de errores y mejorar resiliencia",
                "risk": "low",
                "requires_approval": True,
                "source": "self_debugger",
                "evidence": {
                    "error_rate": error_rate,
                    "total_errors": summary.get("total_errors", 0),
                    "top_category": list(summary.get("categories", {}).keys())[:1],
                },
            })

        # Check for specific error categories
        categories = summary.get("categories", {})
        if categories.get("timeout", 0) >= 5:
            proposals.append({
                "module": filename.replace(".log", ""),
                "change_description": "Aumentar timeouts o implementar retry con backoff",
                "expected_benefit": "Reducir fallos por timeout en conexiones lentas",
                "risk": "low",
                "requires_approval": True,
                "source": "self_debugger",
                "evidence": {"timeout_count": categories["timeout"]},
            })

        if categories.get("module_error", 0) >= 1:
            proposals.append({
                "module": filename.replace(".log", ""),
                "change_description": "Verificar y agregar dependencias faltantes",
                "expected_benefit": "Eliminar errores de import",
                "risk": "low",
                "requires_approval": True,
                "source": "self_debugger",
                "evidence": {"module_errors": categories["module_error"]},
            })

    return proposals


def _analyze_metrics() -> list:
    """Generate proposals from metrics analysis."""
    proposals = []

    try:
        from runtime import metrics_engine
        stats = metrics_engine.summary(days=7)
    except (ImportError, Exception):
        return proposals

    # Low average confidence
    avg_conf = stats.get("avg_confidence", 0)
    if avg_conf > 0 and avg_conf < 0.6:
        proposals.append({
            "module": "confidence_engine",
            "change_description": f"Confianza promedio baja ({avg_conf:.2f}) — "
                                  "ajustar pesos o mejorar calidad de embeddings",
            "expected_benefit": "Mejorar precision de respuestas RAG",
            "risk": "medium",
            "requires_approval": True,
            "source": "metrics",
            "evidence": {"avg_confidence": avg_conf},
        })

    # High escalation rate
    escalation = stats.get("escalation_rate", 0)
    if escalation > 0.3:
        proposals.append({
            "module": "orchestrator",
            "change_description": f"Tasa de escalacion alta ({escalation:.0%}) — "
                                  "revisar umbrales de routing",
            "expected_benefit": "Reducir escalaciones innecesarias",
            "risk": "medium",
            "requires_approval": True,
            "source": "metrics",
            "evidence": {"escalation_rate": escalation},
        })

    # High p95 latency
    latency = stats.get("latency", {})
    p95 = latency.get("p95", 0)
    if p95 > 5000:
        proposals.append({
            "module": "multi_agent_engine",
            "change_description": f"Latencia p95 alta ({p95:.0f}ms) — "
                                  "optimizar pipeline o reducir llamadas a modelo",
            "expected_benefit": "Mejorar tiempo de respuesta",
            "risk": "medium",
            "requires_approval": True,
            "source": "metrics",
            "evidence": {"p95_latency_ms": p95},
        })

    # Diagnostics frequency
    diags = stats.get("diagnostics", {})
    critical_total = diags.get("critical_total", 0)
    if critical_total > 10:
        proposals.append({
            "module": "infra_diagnostics",
            "change_description": f"{critical_total} issues criticos en 7 dias — "
                                  "revisar reglas de diagnostico o umbrales",
            "expected_benefit": "Reducir falsos positivos o detectar problemas reales",
            "risk": "low",
            "requires_approval": True,
            "source": "metrics",
            "evidence": {"critical_issues_7d": critical_total},
        })

    return proposals


def _analyze_idle_learnings() -> list:
    """Generate proposals from idle learning suggestions."""
    proposals = []

    try:
        from runtime import idle_learning_engine
        suggestions = idle_learning_engine.get_suggestions()
    except (ImportError, Exception):
        return proposals

    for s in suggestions:
        if s.get("priority") in ("high", "medium"):
            proposals.append({
                "module": "runtime",
                "change_description": s.get("description", ""),
                "expected_benefit": s.get("recommendation", ""),
                "risk": "low" if s.get("priority") == "medium" else "medium",
                "requires_approval": True,
                "source": "idle_learning",
                "evidence": {"type": s.get("type", ""), "metric": s.get("metric")},
            })

    return proposals


# ── Main API ────────────────────────────────────────────────────


def generate_proposals() -> list:
    """Analyze all sources and generate improvement proposals.

    NEVER auto-modifies code. All proposals have requires_approval=True.

    Returns list of proposal dicts.
    """
    all_proposals = []

    all_proposals.extend(_analyze_debug_reports())
    all_proposals.extend(_analyze_metrics())
    all_proposals.extend(_analyze_idle_learnings())

    # Deduplicate by module + description prefix
    seen = set()
    unique = []
    for p in all_proposals:
        key = (p["module"], p["change_description"][:50])
        if key not in seen:
            seen.add(key)
            p["proposed_at"] = _now().isoformat()
            p["status"] = "pending"
            unique.append(p)

    unique = unique[:MAX_PROPOSALS]

    # Save proposals (merge with existing)
    existing = _load_json(PROPOSALS_FILE, {"proposals": []})
    existing_proposals = existing.get("proposals", [])

    # Keep approved/rejected from history, add new pending
    history = [p for p in existing_proposals if p.get("status") != "pending"]
    all_saved = history + unique

    # Cap total
    if len(all_saved) > MAX_PROPOSALS * 2:
        all_saved = all_saved[-(MAX_PROPOSALS * 2):]

    data = {
        "proposals": all_saved,
        "last_analysis": _now().isoformat(),
        "pending_count": len(unique),
    }
    _save_json(PROPOSALS_FILE, data)

    _log({
        "type": "proposals_generated",
        "new_proposals": len(unique),
        "total_stored": len(all_saved),
        "sources": list(set(p.get("source", "") for p in unique)),
    })

    return unique


def get_pending_proposals() -> list:
    """Get all pending (unapproved) proposals."""
    data = _load_json(PROPOSALS_FILE, {"proposals": []})
    return [p for p in data.get("proposals", []) if p.get("status") == "pending"]


def get_all_proposals() -> list:
    """Get all proposals regardless of status."""
    data = _load_json(PROPOSALS_FILE, {"proposals": []})
    return data.get("proposals", [])
