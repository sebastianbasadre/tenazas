#!/usr/bin/env python3
"""
Escalation — Escalates queries to more capable pipelines.

Extracted from orchestrator.py to separate escalation logic from
the main routing facade. Uses lazy imports from orchestrator to
avoid circular dependencies (orchestrator re-exports escalate).

Escalation paths:
  knowledge_query → complex_reasoning
  infrastructure_issue → complex_reasoning
  diagnostic_request → infrastructure_issue
  chat → knowledge_query
  automation_command → infrastructure_issue
"""

import json
from pathlib import Path

try:
    from runtime_utils import now
except ImportError:
    from workspace.runtime.runtime_utils import now

# ── Logging ─────────────────────────────────────────────────────

# Default log path (same directory as orchestrator).
# Tests may redirect orchestrator.ORCHESTRATOR_LOG; _log() reads it
# lazily so the redirect is respected.
_LOG_PATH = Path(__file__).parent / "logs" / "orchestrator.log"


def _log(entry: dict):
    # Use orchestrator's ORCHESTRATOR_LOG to respect test redirects.
    # The orchestrator module may be loaded as 'runtime.orchestrator' or
    # 'orchestrator' depending on sys.path; check sys.modules for both.
    import sys
    _lp = _LOG_PATH
    for mod_name in ("runtime.orchestrator", "orchestrator"):
        mod = sys.modules.get(mod_name)
        if mod and hasattr(mod, "ORCHESTRATOR_LOG"):
            _lp = mod.ORCHESTRATOR_LOG
            break
    _lp.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(_lp, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Escalation ──────────────────────────────────────────────────


def escalate(
    original_route: dict,
    reason: str,
    available_backends: dict | None = None,
) -> dict:
    """Escalate to a more capable pipeline after failure or low confidence.

    Escalation paths:
      knowledge_query → complex_reasoning
      infrastructure_issue → complex_reasoning
      diagnostic_request → infrastructure_issue
      chat → knowledge_query
      automation_command → infrastructure_issue
    """
    # Lazy imports to avoid circular dependency with orchestrator
    from orchestrator import select_model, get_pipeline, _get_parameters

    escalation_map = {
        "chat": "knowledge_query",
        "knowledge_query": "complex_reasoning",
        "infrastructure_issue": "complex_reasoning",
        "automation_command": "infrastructure_issue",
        "diagnostic_request": "infrastructure_issue",
        "complex_reasoning": "complex_reasoning",  # No further escalation
    }

    current = original_route["classification"]
    escalated = escalation_map.get(current, "complex_reasoning")

    model = select_model(escalated, available_backends,
                         query=original_route.get("query", ""))
    pipeline = get_pipeline(escalated)
    params = _get_parameters(escalated)

    result = {
        "query": original_route["query"],
        "classification": escalated,
        "pipeline": pipeline,
        "model": {
            "id": model["id"],
            "alias": model["alias"],
            "tier": model["tier"],
        },
        "parameters": params,
        "context_signals": original_route.get("context_signals", []),
        "escalated_from": current,
        "escalation_reason": reason,
    }

    _log({
        "type": "escalation",
        "query": original_route["query"][:100],
        "from": current,
        "to": escalated,
        "reason": reason,
        "model": model["id"],
    })

    return result
