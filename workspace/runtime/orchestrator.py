#!/usr/bin/env python3
"""
Orchestrator — Central AI routing layer for Tenazas.

Classifies user queries and routes them to the optimal pipeline + model.

Categories:
  chat               → direct LLM
  knowledge_query    → RAG pipeline
  infrastructure_issue → infra diagnostics + RAG
  automation_command  → skill execution
  diagnostic_request  → infra diagnostics engine
  complex_reasoning   → multi-model chain

Model selection based on task complexity and availability.
"""

import json
import re
from datetime import datetime
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
ORCHESTRATOR_LOG = BASE_DIR / "logs" / "orchestrator.log"

# Model tiers (ordered by preference)
MODELS = {
    "gemini_flash": {
        "id": "google/gemini-2.5-flash",
        "alias": "fast",
        "tier": "cloud",
        "strengths": ["chat", "knowledge", "fast_response"],
    },
    "gemini_pro": {
        "id": "google/gemini-2.5-pro",
        "alias": "reasoning",
        "tier": "cloud",
        "strengths": ["complex_reasoning", "analysis"],
    },
    "qwen_7b": {
        "id": "ollama/qwen2.5:7b",
        "alias": "local-heavy",
        "tier": "local_gpu",
        "strengths": ["reasoning", "diagnostics", "analysis"],
    },
    "qwen_3b": {
        "id": "ollama/qwen2.5:7b",
        "alias": "local-fast",
        "tier": "local_gpu",
        "strengths": ["system", "logs", "json", "fast_response"],
    },
    "phi4": {
        "id": "ollama/phi4-mini",
        "alias": "lightweight",
        "tier": "local_cpu",
        "strengths": ["lightweight", "simple_chat"],
    },
}

# Pipeline definitions
PIPELINES = {
    "chat": {
        "description": "Direct LLM response for casual conversation",
        "modules": [],
        "default_model": "qwen_7b",
    },
    "knowledge_query": {
        "description": "RAG pipeline for factual questions about the system",
        "modules": ["rag_pipeline"],
        "default_model": "qwen_7b",
    },
    "infrastructure_issue": {
        "description": "Infra diagnostics + RAG for infrastructure problems",
        "modules": ["infra_diagnostics", "rag_pipeline", "action_mapper"],
        "default_model": "qwen_7b",
    },
    "automation_command": {
        "description": "Skill execution for automation requests",
        "modules": ["action_mapper"],
        "default_model": "qwen_3b",
    },
    "diagnostic_request": {
        "description": "Infrastructure diagnostics engine",
        "modules": ["infra_diagnostics"],
        "default_model": "qwen_7b",
    },
    "complex_reasoning": {
        "description": "Multi-model reasoning for complex analysis",
        "modules": ["rag_pipeline", "infra_diagnostics"],
        "default_model": "qwen_7b",
    },
}

# ── Classification Rules ─────────────────────────────────────────
# Ordered by specificity (most specific first).
# Each rule: (category, patterns, keywords)

CLASSIFICATION_RULES = [
    # Diagnostic requests
    (
        "diagnostic_request",
        [r"diagn[oó]stic", r"health\s*check", r"status\s*(del|de|completo)",
         r"est[aá]\s*(todo|el sistema)", r"reporte\s*de\s*infra"],
        {"diagnostico", "status", "health", "reporte", "chequeo"},
    ),
    # Infrastructure issues
    (
        "infrastructure_issue",
        [r"(no|sin)\s*(funciona|anda|responde|conecta)", r"ca[iy]do",
         r"lento", r"error", r"fail", r"crash", r"reinici[aá]",
         r"vpn.*(leak|down|caido)", r"internet.*(lento|caido|sin)",
         r"container.*(down|caido|error)", r"ollama.*(offline|down|timeout)"],
        {"caido", "roto", "lento", "error", "fallo", "reiniciar",
         "offline", "timeout", "crash", "leak"},
    ),
    # Automation commands
    (
        "automation_command",
        [r"reinici[aá]", r"restart", r"ejecut[aá]", r"corr[eé]",
         r"prend[eé].*lu[cz]", r"apag[aá].*lu[cz]", r"scan",
         r"escane[aá]", r"backup"],
        {"reiniciar", "restart", "ejecutar", "correr", "prender",
         "apagar", "scan", "escanear", "backup"},
    ),
    # Knowledge queries
    (
        "knowledge_query",
        [r"(c[oó]mo|qu[eé]|cu[aá]l|d[oó]nde|por\s*qu[eé])",
         r"explic[aá]", r"config", r"ip\s+de", r"puerto",
         r"skill", r"modelo", r"container"],
        {"como", "que", "cual", "donde", "porque", "explicar",
         "configurar", "ip", "puerto", "skill", "modelo"},
    ),
    # Complex reasoning
    (
        "complex_reasoning",
        [r"anali[zs][aá]", r"compar[aá]", r"evalu[aá]",
         r"estrategia", r"plan", r"optimiz",
         r"por\s*qu[eé]\s+.{20,}"],  # Long "why" questions
        {"analizar", "comparar", "evaluar", "estrategia",
         "planificar", "optimizar"},
    ),
]

# Chat is the default fallback — no explicit rules needed.

# ── Helpers ──────────────────────────────────────────────────────


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    ORCHESTRATOR_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(ORCHESTRATOR_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Classification ───────────────────────────────────────────────


def classify_query(query: str, context: dict | None = None) -> str:
    """Classify a query into a category using keyword + regex rules.

    Fast, no LLM needed. Returns category string.
    """
    query_lower = query.lower().strip()
    words = set(re.findall(r"\w+", query_lower))

    # Check context for recent infra events (boosts infra classification)
    context_boost = set()
    if context:
        recent_events = context.get("recent_events", [])
        for event in recent_events:
            etype = event.get("type", "")
            if etype in ("container_down", "vpn_down", "vpn_leak",
                         "internet_lost", "ollama_notebook_down"):
                context_boost.add("infrastructure_issue")

    # Score each category
    best_category = "chat"
    best_score = 0

    for category, patterns, keywords in CLASSIFICATION_RULES:
        score = 0

        # Pattern matching (strong signal)
        for pattern in patterns:
            if re.search(pattern, query_lower):
                score += 3
                break

        # Keyword matching (weaker signal)
        keyword_matches = words & keywords
        score += len(keyword_matches)

        # Context boost
        if category in context_boost:
            score += 2

        if score > best_score:
            best_score = score
            best_category = category

    # Minimum threshold to override chat
    if best_score < 2:
        best_category = "chat"

    return best_category


# ── Model Selection ──────────────────────────────────────────────


def select_model(
    category: str,
    available_backends: dict | None = None,
) -> dict:
    """Select the best model for a given category.

    Args:
        category: query classification
        available_backends: {tier: bool} e.g. {"local_gpu": True, "cloud": True}

    Returns: model dict from MODELS.
    """
    if available_backends is None:
        available_backends = {"local_gpu": True, "local_cpu": True, "cloud": True}

    pipeline = PIPELINES.get(category, PIPELINES["chat"])
    default = pipeline["default_model"]

    # Try default model first
    model = MODELS.get(default)
    if model and available_backends.get(model["tier"], False):
        return model

    # Fallback chain by category
    fallback_chains = {
        "chat": ["gemini_flash", "qwen_3b", "phi4"],
        "knowledge_query": ["gemini_flash", "qwen_7b", "qwen_3b"],
        "infrastructure_issue": ["qwen_7b", "qwen_3b", "gemini_flash"],
        "automation_command": ["qwen_3b", "qwen_7b", "gemini_flash"],
        "diagnostic_request": ["qwen_7b", "qwen_3b", "gemini_flash"],
        "complex_reasoning": ["gemini_pro", "qwen_7b", "gemini_flash"],
    }

    for model_key in fallback_chains.get(category, ["gemini_flash"]):
        model = MODELS.get(model_key)
        if model and available_backends.get(model["tier"], False):
            return model

    # Last resort: any available model
    for model in MODELS.values():
        if available_backends.get(model["tier"], False):
            return model

    # Nothing available — return gemini flash (cloud should always be up)
    return MODELS["gemini_flash"]


# ── Pipeline Assembly ────────────────────────────────────────────


def get_pipeline(category: str) -> dict:
    """Get pipeline configuration for a category.

    Returns: {name, description, modules, default_model}
    """
    pipeline = PIPELINES.get(category, PIPELINES["chat"])
    return {
        "name": category,
        "description": pipeline["description"],
        "modules": pipeline["modules"],
        "default_model": pipeline["default_model"],
    }


# ── Main API ─────────────────────────────────────────────────────


def route_query(
    query: str,
    context: dict | None = None,
    available_backends: dict | None = None,
) -> dict:
    """Central routing: classify → select model → assemble pipeline.

    Args:
        query: user input
        context: optional dict with recent_events, working_memory, etc.
        available_backends: {tier: bool} for model availability

    Returns:
        {
            "query": str,
            "classification": str,
            "pipeline": {name, description, modules},
            "model": {id, alias, tier},
            "parameters": {temperature, ...},
            "context_signals": list,
        }
    """
    # 1. Classify
    classification = classify_query(query, context)

    # 2. Select model
    model = select_model(classification, available_backends)

    # 3. Assemble pipeline
    pipeline = get_pipeline(classification)

    # 4. Set parameters based on category
    params = _get_parameters(classification)

    # 5. Extract context signals
    signals = _extract_signals(context)

    result = {
        "query": query,
        "classification": classification,
        "pipeline": pipeline,
        "model": {
            "id": model["id"],
            "alias": model["alias"],
            "tier": model["tier"],
        },
        "parameters": params,
        "context_signals": signals,
    }

    _log({
        "type": "routing",
        "query": query[:100],
        "classification": classification,
        "pipeline": pipeline["name"],
        "model": model["id"],
    })

    return result


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

    model = select_model(escalated, available_backends)
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


# ── Internal Helpers ─────────────────────────────────────────────


def _get_parameters(category: str) -> dict:
    """Get LLM parameters tuned for the category."""
    params_map = {
        "chat": {"temperature": 0.7, "max_tokens": 512},
        "knowledge_query": {"temperature": 0.3, "max_tokens": 1024},
        "infrastructure_issue": {"temperature": 0.2, "max_tokens": 1024},
        "automation_command": {"temperature": 0.1, "max_tokens": 512},
        "diagnostic_request": {"temperature": 0.2, "max_tokens": 1024},
        "complex_reasoning": {"temperature": 0.3, "max_tokens": 2048},
    }
    return params_map.get(category, {"temperature": 0.5, "max_tokens": 512})


def _extract_signals(context: dict | None) -> list:
    """Extract relevant context signals for routing hints."""
    if not context:
        return []

    signals = []
    for event in context.get("recent_events", []):
        etype = event.get("type", "")
        if etype:
            signals.append(f"recent_event:{etype}")

    if context.get("working_memory"):
        signals.append("has_working_memory")

    if context.get("ollama_available") is False:
        signals.append("ollama_offline")

    if context.get("previous_classification"):
        signals.append(f"prev:{context['previous_classification']}")

    return signals
