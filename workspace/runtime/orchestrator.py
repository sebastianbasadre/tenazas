#!/usr/bin/env python3
"""
Orchestrator — Central AI routing layer for Tenazas.

Classifies user queries and routes them to the optimal pipeline + model.

Categories (pipeline selection):
  chat               → direct LLM
  knowledge_query    → RAG pipeline
  infrastructure_issue → infra diagnostics + RAG
  automation_command  → skill execution
  diagnostic_request  → infra diagnostics engine
  complex_reasoning   → multi-model chain

Model selection delegates to Router V2 (model_router.route_by_role) when
USE_ROUTER_V2=True, with automatic fallback to V1 on any error.
"""

import json
import re
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
ORCHESTRATOR_LOG = BASE_DIR / "logs" / "orchestrator.log"

# Feature flag: use Router V2 for model selection
# Set to False to revert to V1 behavior without code changes
USE_ROUTER_V2 = True

# ── Routing metrics (in-memory, reset on process restart) ────────

_routing_metrics = defaultdict(int)


def get_routing_metrics() -> dict:
    """Return current routing metrics snapshot. Resets on process restart."""
    return dict(_routing_metrics)


def reset_routing_metrics():
    """Reset all routing metrics (for testing)."""
    _routing_metrics.clear()


# ── V1 Model tiers (used as fallback when V2 is off or fails) ───

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

# ── Category → Role mapping (orchestrator intent → model_router role) ──

CATEGORY_TO_ROLE = {
    "chat": "fast",
    "knowledge_query": "fast",
    "infrastructure_issue": "fast",
    "automation_command": "fast",
    "diagnostic_request": "fast",
    "complex_reasoning": "reason",
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

# ── IOL Broker keywords ─────────────────────────────────────────

IOL_KEYWORDS = [
    "portafolio", "iol", "invertironline", "acciones", "cedears",
    "cotización", "cotizacion", "comprar", "vender", "saldo invertir",
    "mis acciones", "cuánto tengo invertido",
]

CLASSIFICATION_RULES = [
    # IOL Broker (most specific — check first)
    (
        "iol_broker",
        [r"portafolio", r"portfolio", r"cotizaci[oó]n\s+\w+", r"comprar\s+\d+",
         r"vender\s+\d+", r"mis\s+acciones", r"cedears?",
         r"invertironline", r"\biol\b",
         r"cu[aá]nto\s+tengo\s+invertido", r"tengo\s+invertido",
         r"saldo\s+(?:de\s+)?(?:mi\s+)?(?:cuenta\s+)?invert",
         r"mis\s+inversiones", r"mi\s+cartera", r"\binvertido\b"],
        {"portafolio", "portfolio", "iol", "invertironline", "acciones", "cedears",
         "cotización", "cotizacion", "comprar", "vender",
         "invertido", "inversiones", "broker"},
    ),
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


def _select_model_v2(
    category: str,
    query: str = "",
    flags: dict | None = None,
) -> dict | None:
    """Try Router V2 model selection. Returns model dict or None on failure.

    Translates orchestrator category + prompt into a route_by_role() call.
    Returns a dict compatible with V1 format: {id, alias, tier, strengths}.
    """
    try:
        from runtime import model_router as mr

        # The prompt-based classification can override the category default
        # e.g. a "chat" query with code in it → code role
        merged_flags = dict(flags or {})
        if "role" not in merged_flags:
            # Only set category default if no explicit role in flags
            category_role = CATEGORY_TO_ROLE.get(category, "fast")
            # Let classify_task analyze the prompt first; if it returns
            # something more specific than fast, use that instead
            prompt_role = mr.classify_task(query, merged_flags)
            if prompt_role != "fast":
                merged_flags["role"] = prompt_role
            else:
                merged_flags["role"] = category_role

        result = mr.route_by_role(query, flags=merged_flags)
        if not result:
            return None

        # Convert to orchestrator model format
        model_name = result["model"]
        tier = "local_gpu" if result["backend"] == "notebook_gpu" else "local_cpu"

        return {
            "id": f"ollama/{model_name}",
            "alias": result["role"],
            "tier": tier,
            "strengths": mr.MODEL_CATALOG.get(model_name, {}).get("capabilities", []),
            "_v2_result": result,  # preserve full V2 result for logging
        }
    except Exception:
        return None


def select_model(
    category: str,
    available_backends: dict | None = None,
    query: str = "",
    flags: dict | None = None,
) -> dict:
    """Select the best model for a given category.

    When USE_ROUTER_V2 is True, delegates to model_router.route_by_role()
    for smarter role-based selection. Falls back to V1 on any error.

    Args:
        category: query classification
        available_backends: {tier: bool} e.g. {"local_gpu": True, "cloud": True}
        query: original prompt text (for V2 classification)
        flags: optional hints for V2 (role, has_image, critical, etc.)

    Returns: model dict with at minimum {id, alias, tier}.
    """
    # V2 path: delegate to model_router
    if USE_ROUTER_V2 and available_backends is None:
        t0 = time.monotonic()
        v2_result = _select_model_v2(category, query, flags)
        decision_latency = time.monotonic() - t0
        if v2_result:
            _routing_metrics["v2_calls"] += 1
            _routing_metrics[f"v2_role_{v2_result['alias']}"] += 1
            v2_full = v2_result.get("_v2_result", {})
            if v2_full.get("vram_swap"):
                _routing_metrics["v2_vram_swaps"] += 1
            if v2_full.get("fallback_used"):
                _routing_metrics["v2_fallbacks"] += 1
            _record_metrics(
                role=v2_result["alias"],
                model=v2_full.get("model", "unknown"),
                backend=v2_full.get("backend", "unknown"),
                fallback_used=v2_full.get("fallback_used", False),
                vram_swap=v2_full.get("vram_swap", False),
                decision_latency=decision_latency,
                v2_used=True,
            )
            return v2_result
        # V2 failed (backends offline or import error) — fall through to V1
        _routing_metrics["v1_fallback_from_v2"] += 1
        _record_v1_fallback()

    # V1 path: static model selection
    _routing_metrics["v1_calls"] += 1

    if available_backends is None:
        available_backends = {"local_gpu": True, "local_cpu": True, "cloud": True}

    pipeline = PIPELINES.get(category, PIPELINES["chat"])
    default = pipeline["default_model"]

    # Try default model first
    model = MODELS.get(default)
    if model and available_backends.get(model["tier"], False):
        _record_metrics(
            role=CATEGORY_TO_ROLE.get(category, "fast"),
            model=model["id"], backend=model["tier"],
            v2_used=False,
        )
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
            is_cloud = model["tier"] == "cloud"
            _record_metrics(
                role=CATEGORY_TO_ROLE.get(category, "fast"),
                model=model["id"], backend=model["tier"],
                v2_used=False, cloud_fallback=is_cloud,
            )
            if is_cloud:
                _log({
                    "type": "cloud_fallback",
                    "reason": "local backends unavailable in V1 chain",
                    "category": category,
                    "model": model["id"],
                })
            return model

    # Last resort: any available model
    for model in MODELS.values():
        if available_backends.get(model["tier"], False):
            return model

    # Nothing available — CLOUD FALLBACK (explicit policy)
    _log({
        "type": "cloud_fallback",
        "reason": "all backends offline — last resort to Gemini cloud",
        "category": category,
        "model": MODELS["gemini_flash"]["id"],
    })
    _record_metrics(
        role=CATEGORY_TO_ROLE.get(category, "fast"),
        model=MODELS["gemini_flash"]["id"], backend="cloud",
        v2_used=False, cloud_fallback=True,
    )
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
    # 1. Classify intent (pipeline selection)
    classification = classify_query(query, context)

    # 2. Build flags from context
    flags = _build_flags_from_context(context)

    # 3. Select model (V2 delegates to model_router, V1 as fallback)
    model = select_model(classification, available_backends,
                         query=query, flags=flags)

    # 4. Assemble pipeline
    pipeline = get_pipeline(classification)

    # 5. Set parameters based on category
    params = _get_parameters(classification)

    # 6. Extract context signals
    signals = _extract_signals(context)

    # 7. Enrich with V2 routing info if available
    v2_info = model.pop("_v2_result", None)

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

    if v2_info:
        result["router_v2"] = {
            "role": v2_info.get("role"),
            "classified_as": v2_info.get("classified_as"),
            "model": v2_info.get("model"),
            "fallback_used": v2_info.get("fallback_used"),
            "degraded": v2_info.get("degraded", False),
            "degrade_reason": v2_info.get("degrade_reason", ""),
            "vram_swap": v2_info.get("vram_swap"),
            "signals": v2_info.get("signals", []),
        }
        # Record guardrail degradation to Prometheus
        if v2_info.get("degraded"):
            reason_key = v2_info.get("degrade_reason", "")
            # Extract the guardrail type (first word before ":")
            gr_type = reason_key.split(":")[0] if ":" in reason_key else "unknown"
            try:
                from runtime.metrics_exporter import record_guardrail_degradation
                record_guardrail_degradation(gr_type)
            except Exception:
                pass

    log_entry = {
        "type": "routing",
        "query": query[:100],
        "classification": classification,
        "pipeline": pipeline["name"],
        "model": model["id"],
    }
    if v2_info:
        log_entry["router_v2_role"] = v2_info.get("role")
        log_entry["router_v2_model"] = v2_info.get("model")
        if v2_info.get("degraded"):
            log_entry["guardrail_degraded"] = True
            log_entry["guardrail_reason"] = v2_info.get("degrade_reason")
    _log(log_entry)

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


def report_inference(model_id: str, latency: float, route_result: dict | None = None):
    """Report actual inference latency after LLM call completes.

    Call this after the LLM responds to close the guardrail feedback loop.
    The latency is used for future guardrail decisions and Prometheus metrics.

    Args:
        model_id: model ID from route result (e.g. "ollama/qwen2.5:7b")
        latency: actual inference time in seconds
        route_result: the full route_query() result (for extracting V2 info)
    """
    try:
        from runtime.model_router import report_inference_complete
        # Strip "ollama/" prefix if present
        model = model_id.replace("ollama/", "")
        vram_swap = False
        if route_result and "router_v2" in route_result:
            vram_swap = route_result["router_v2"].get("vram_swap", False)
        report_inference_complete(model, latency, vram_swap)
    except Exception:
        pass

    try:
        from runtime.metrics_exporter import record_model_latency
        model = model_id.replace("ollama/", "")
        role = "fast"
        if route_result and "router_v2" in route_result:
            role = route_result["router_v2"].get("role", "fast")
        record_model_latency(model, role, latency)
    except Exception:
        pass


def _record_metrics(**kwargs):
    """Record routing decision to Prometheus (best-effort, never raises)."""
    try:
        from runtime.metrics_exporter import record_routing_decision
        record_routing_decision(**kwargs)
    except Exception:
        pass


def _record_v1_fallback():
    """Record V1 fallback event to Prometheus (best-effort)."""
    try:
        from runtime.metrics_exporter import record_v1_fallback_from_v2
        record_v1_fallback_from_v2()
    except Exception:
        pass


def _build_flags_from_context(context: dict | None) -> dict:
    """Extract Router V2 flags from orchestrator context."""
    if not context:
        return {}

    flags = {}
    if context.get("has_image"):
        flags["has_image"] = True
    if context.get("has_attachment"):
        flags["has_attachment"] = True
    if context.get("critical"):
        flags["critical"] = True
    if context.get("embed"):
        flags["embed"] = True
    if context.get("role"):
        flags["role"] = context["role"]

    return flags
