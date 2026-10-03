#!/usr/bin/env python3
"""Orchestrator -- Central AI routing facade for Tenazas.
Submodules: classifier.py, skill_executor.py, escalation.py, routing_metrics.py."""

import json, os, time  # noqa: E401
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
ORCHESTRATOR_LOG = BASE_DIR / "logs" / "orchestrator.log"
USE_ROUTER_V2 = True  # Set False to revert to V1 model selection

# Re-exports from submodules (try/except for container vs test import paths)
try:
    from routing_metrics import (_routing_metrics, get_routing_metrics, reset_routing_metrics,
                                 report_inference, _record_metrics, _record_v1_fallback)
except ImportError:
    from workspace.runtime.routing_metrics import (_routing_metrics, get_routing_metrics,
        reset_routing_metrics, report_inference, _record_metrics, _record_v1_fallback)
try:
    from skill_executor import (execute_skill, SKILL_MAP, SKILL_TIMEOUT, complete_email_send,
        _load_email_pending, _save_email_pending, _clear_email_pending,
        _SKILL_HANDLERS, _SKILL_PARSERS, _call_multitool_model, MULTITOOL_MODEL,
        MULTITOOL_SYSTEM_PROMPT, MULTITOOL_OLLAMA_URL)
except ImportError:
    from workspace.runtime.skill_executor import (execute_skill, SKILL_MAP, SKILL_TIMEOUT,
        complete_email_send, _load_email_pending, _save_email_pending, _clear_email_pending,
        _SKILL_HANDLERS, _SKILL_PARSERS, _call_multitool_model, MULTITOOL_MODEL,
        MULTITOOL_SYSTEM_PROMPT, MULTITOOL_OLLAMA_URL)
try:
    from escalation import escalate
except ImportError:
    from workspace.runtime.escalation import escalate
try:
    from classifier import classify_query, CLASSIFICATION_RULES, IOL_KEYWORDS
except ImportError:
    from workspace.runtime.classifier import classify_query, CLASSIFICATION_RULES, IOL_KEYWORDS
_CLASSIFICATION_RULES = CLASSIFICATION_RULES  # Legacy alias

MODELS = {
    "qwen_7b": {"id": "ollama/qwen3:8b", "alias": "local-heavy", "tier": "local_gpu",
                "strengths": ["reasoning", "diagnostics", "analysis"]},
    "qwen_3b": {"id": "ollama/qwen2.5:3b", "alias": "local-fast", "tier": "local_gpu",
                "strengths": ["system", "logs", "json", "fast_response"]},
    "phi4": {"id": "ollama/phi4-mini", "alias": "lightweight", "tier": "local_cpu",
             "strengths": ["lightweight", "simple_chat"]},
    "openrouter_kimi": {"id": "openrouter/moonshotai/kimi-k2", "alias": "cloud-openrouter",
                        "tier": "cloud_openrouter",
                        "strengths": ["general", "reasoning", "analysis", "complex", "code"]},
    "gemini_flash": {"id": "google/gemini-2.5-flash", "alias": "fast", "tier": "cloud_legacy",
                     "strengths": ["chat", "knowledge", "fast_response"]},
    "gemini_pro": {"id": "google/gemini-2.5-pro", "alias": "reasoning", "tier": "cloud_legacy",
                   "strengths": ["complex_reasoning", "analysis"]},
}

CATEGORY_TO_ROLE = {
    "chat": "fast", "knowledge_query": "fast", "infrastructure_issue": "fast",
    "automation_command": "fast", "diagnostic_request": "fast",
    "complex_reasoning": "reason", "openrouter_usage": "fast",
}

PIPELINES = {
    "chat": {"description": "Direct LLM response for casual conversation",
             "modules": [], "default_model": "qwen_7b"},
    "knowledge_query": {"description": "RAG pipeline for factual questions about the system",
                        "modules": ["rag_pipeline"], "default_model": "qwen_7b"},
    "infrastructure_issue": {"description": "Infra diagnostics + RAG for infrastructure problems",
                             "modules": ["infra_diagnostics", "rag_pipeline", "action_mapper"],
                             "default_model": "qwen_7b"},
    "automation_command": {"description": "Skill execution for automation requests",
                          "modules": ["action_mapper"], "default_model": "qwen_3b"},
    "diagnostic_request": {"description": "Infrastructure diagnostics engine",
                           "modules": ["infra_diagnostics"], "default_model": "qwen_7b"},
    "complex_reasoning": {"description": "Multi-model reasoning for complex analysis",
                          "modules": ["rag_pipeline", "infra_diagnostics"], "default_model": "qwen_7b"},
    "openrouter_usage": {"description": "OpenRouter balance and usage query",
                         "modules": ["openrouter_usage"], "default_model": "qwen_3b"},
}

def _log(entry: dict):
    ORCHESTRATOR_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(ORCHESTRATOR_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")

def _is_openrouter_available() -> bool:
    return (os.environ.get("TENAZAS_ALLOW_CLOUD", "true").lower() == "true"
            and bool(os.environ.get("OPENROUTER_API_KEY", "")))


def _is_gemini_available() -> bool:
    return (os.environ.get("TENAZAS_ALLOW_CLOUD", "true").lower() == "true"
            and bool(os.environ.get("GOOGLE_API_KEY", "")))

_PARAMS = {"chat": {"temperature": 0.7, "max_tokens": 512},
           "knowledge_query": {"temperature": 0.3, "max_tokens": 1024},
           "infrastructure_issue": {"temperature": 0.2, "max_tokens": 1024},
           "automation_command": {"temperature": 0.1, "max_tokens": 512},
           "diagnostic_request": {"temperature": 0.2, "max_tokens": 1024},
           "complex_reasoning": {"temperature": 0.3, "max_tokens": 2048}}

def _get_parameters(category: str) -> dict:
    return _PARAMS.get(category, {"temperature": 0.5, "max_tokens": 512})

def _extract_signals(context: dict | None) -> list:
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

def _build_flags_from_context(context: dict | None) -> dict:
    if not context:
        return {}
    flags = {}
    for key in ("has_image", "has_attachment", "critical", "embed"):
        if context.get(key):
            flags[key] = True
    if context.get("role"):
        flags["role"] = context["role"]
    return flags

# ── Model Selection ─────────────────────────────────────────────

def _select_model_v2(category: str, query: str = "", flags: dict | None = None) -> dict | None:
    """Try Router V2 model selection. Returns model dict or None on failure."""
    try:
        from runtime import model_router as mr
        merged_flags = dict(flags or {})
        if "role" not in merged_flags:
            category_role = CATEGORY_TO_ROLE.get(category, "fast")
            prompt_role = mr.classify_task(query, merged_flags)
            merged_flags["role"] = prompt_role if prompt_role != "fast" else category_role
        result = mr.route_by_role(query, flags=merged_flags)
        if not result:
            return None
        model_name = result["model"]
        tier = "local_gpu" if result["backend"] == "notebook_gpu" else "local_cpu"
        return {
            "id": f"ollama/{model_name}", "alias": result["role"], "tier": tier,
            "strengths": mr.MODEL_CATALOG.get(model_name, {}).get("capabilities", []),
            "_v2_result": result,
        }
    except Exception:
        return None


def select_model(category: str, available_backends: dict | None = None,
                 query: str = "", flags: dict | None = None) -> dict:
    """Select the best model for a given category.

    V2 delegates to model_router.route_by_role(); falls back to V1 on error.
    """
    if category.startswith("skill:"):
        category = "automation_command"

    # V2 path
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
                role=v2_result["alias"], model=v2_full.get("model", "unknown"),
                backend=v2_full.get("backend", "unknown"),
                fallback_used=v2_full.get("fallback_used", False),
                vram_swap=v2_full.get("vram_swap", False),
                decision_latency=decision_latency, v2_used=True,
            )
            return v2_result
        _routing_metrics["v1_fallback_from_v2"] += 1
        _record_v1_fallback()

    # V1 path
    _routing_metrics["v1_calls"] += 1
    if available_backends is None:
        available_backends = {
            "local_gpu": True, "local_cpu": True,
            "cloud_openrouter": _is_openrouter_available(),
            "cloud_legacy": _is_gemini_available(),
        }

    pipeline = PIPELINES.get(category, PIPELINES["chat"])
    default = pipeline["default_model"]
    model = MODELS.get(default)
    if model and available_backends.get(model["tier"], False):
        _record_metrics(role=CATEGORY_TO_ROLE.get(category, "fast"),
                        model=model["id"], backend=model["tier"], v2_used=False)
        return model

    fallback_chains = {
        "chat": ["openrouter_kimi", "qwen_3b", "phi4", "gemini_flash"],
        "knowledge_query": ["openrouter_kimi", "qwen_7b", "qwen_3b", "gemini_flash"],
        "infrastructure_issue": ["qwen_7b", "qwen_3b", "openrouter_kimi", "gemini_flash"],
        "automation_command": ["qwen_3b", "qwen_7b", "openrouter_kimi", "gemini_flash"],
        "diagnostic_request": ["qwen_7b", "qwen_3b", "openrouter_kimi", "gemini_flash"],
        "complex_reasoning": ["openrouter_kimi", "qwen_7b", "gemini_pro", "gemini_flash"],
    }
    for model_key in fallback_chains.get(category, ["gemini_flash"]):
        model = MODELS.get(model_key)
        if model and available_backends.get(model["tier"], False):
            is_cloud = model["tier"] in {"cloud_openrouter", "cloud_legacy"}
            _record_metrics(role=CATEGORY_TO_ROLE.get(category, "fast"),
                            model=model["id"], backend=model["tier"],
                            v2_used=False, cloud_fallback=is_cloud)
            if is_cloud:
                _log({"type": "cloud_fallback", "reason": "local backends unavailable in V1 chain",
                      "category": category, "model": model["id"]})
            return model

    for model in MODELS.values():
        if available_backends.get(model["tier"], False):
            return model

    if _is_openrouter_available():
        _log({"type": "cloud_fallback", "reason": "all local backends offline -- OpenRouter last resort",
              "category": category, "model": MODELS["openrouter_kimi"]["id"]})
        _record_metrics(role=CATEGORY_TO_ROLE.get(category, "fast"),
                        model=MODELS["openrouter_kimi"]["id"], backend="cloud_openrouter",
                        v2_used=False, cloud_fallback=True)
        return MODELS["openrouter_kimi"]

    if _is_gemini_available():
        _log({"type": "cloud_fallback", "reason": "all preferred backends offline -- last resort to Gemini cloud",
              "category": category, "model": MODELS["gemini_flash"]["id"]})
        _record_metrics(role=CATEGORY_TO_ROLE.get(category, "fast"),
                        model=MODELS["gemini_flash"]["id"], backend="cloud_legacy",
                        v2_used=False, cloud_fallback=True)
        return MODELS["gemini_flash"]

    return MODELS["phi4"]


def get_pipeline(category: str) -> dict:
    """Get pipeline config. Skill categories map to automation_command."""
    lookup = "automation_command" if category.startswith("skill:") else category
    pipeline = PIPELINES.get(lookup, PIPELINES["chat"])
    return {"name": category, "description": pipeline["description"],
            "modules": pipeline["modules"], "default_model": pipeline["default_model"]}


# ── Main API ────────────────────────────────────────────────────


def route_query(query: str, context: dict | None = None,
                available_backends: dict | None = None) -> dict:
    """Central routing: classify -> select model -> assemble pipeline."""
    classification = classify_query(query, context)
    flags = _build_flags_from_context(context)
    model = select_model(classification, available_backends, query=query, flags=flags)
    pipeline = get_pipeline(classification)
    params = _get_parameters(classification)
    signals = _extract_signals(context)
    v2_info = model.pop("_v2_result", None)

    result = {
        "query": query, "classification": classification, "pipeline": pipeline,
        "model": {"id": model["id"], "alias": model["alias"], "tier": model["tier"]},
        "parameters": params, "context_signals": signals,
    }

    if v2_info:
        result["router_v2"] = {
            "role": v2_info.get("role"), "classified_as": v2_info.get("classified_as"),
            "model": v2_info.get("model"), "fallback_used": v2_info.get("fallback_used"),
            "degraded": v2_info.get("degraded", False),
            "degrade_reason": v2_info.get("degrade_reason", ""),
            "vram_swap": v2_info.get("vram_swap"), "signals": v2_info.get("signals", []),
        }
        if v2_info.get("degraded"):
            gr_type = v2_info.get("degrade_reason", "").split(":")[0] if ":" in v2_info.get("degrade_reason", "") else "unknown"
            try:
                from runtime.metrics_exporter import record_guardrail_degradation
                record_guardrail_degradation(gr_type)
            except Exception:
                pass

    log_entry = {"type": "routing", "query": query[:100], "classification": classification,
                 "pipeline": pipeline["name"], "model": model["id"]}
    if v2_info:
        log_entry["router_v2_role"] = v2_info.get("role")
        log_entry["router_v2_model"] = v2_info.get("model")
        if v2_info.get("degraded"):
            log_entry["guardrail_degraded"] = True
            log_entry["guardrail_reason"] = v2_info.get("degrade_reason")
    _log(log_entry)
    return result


def route_and_execute(query: str, context: dict | None = None,
                      available_backends: dict | None = None) -> dict:
    """Classify, and if it's a skill, execute it directly."""
    classification = classify_query(query, context)

    if classification.startswith("skill:"):
        skill_result = execute_skill(classification, query)
        route = route_query(query, context, available_backends)
        route["classification"] = classification
        route["executed"] = True
        route["skill_result"] = skill_result
        _log({"type": "skill_routed", "skill": classification,
              "success": skill_result["success"], "executed": True})
        return route

    if classification == "complex_reasoning":
        try:
            from multi_agent_engine import reason as _ma_reason
            ctx = json.dumps(context or {})[:2000]
            ma_result = _ma_reason(query, ctx)
            if ma_result.get("success") and ma_result.get("conclusion"):
                route = route_query(query, context, available_backends)
                route["classification"] = classification
                route["executed"] = True
                route["skill_result"] = {
                    "skill": "multi_agent", "success": True,
                    "output": ma_result["conclusion"],
                    "raw": json.dumps({"analysis": ma_result.get("analysis", ""),
                                       "verification": ma_result.get("verification", ""),
                                       "agents_used": ma_result.get("agents_used", [])}),
                    "destructive": False, "error": None,
                }
                _log({"type": "multi_agent_reasoning",
                      "agents": ma_result.get("agents_used", []),
                      "success": True, "query": query[:100]})
                return route
        except Exception:
            pass

    route = route_query(query, context, available_backends)
    route["executed"] = False
    route["skill_result"] = None
    return route


def route_and_execute_multitool(query: str, context: dict | None = None) -> dict:
    """Execute tools from a single query via hybrid router (rules + model)."""
    try:
        from runtime.hybrid_router import route as hybrid_route
    except ImportError:
        from hybrid_router import route as hybrid_route
    try:
        from runtime.multitool_parser import get_skill_category, get_tool_action
    except ImportError:
        from multitool_parser import get_skill_category, get_tool_action

    t0 = time.monotonic()
    _routing_metrics["multitool_requests_total"] += 1

    try:
        tools = hybrid_route(query)
    except Exception as e:
        _routing_metrics["multitool_fallback_total"] += 1
        _log({"type": "hybrid_router_error", "error": str(e), "query": query[:100]})
        return route_and_execute(query, context)

    if not tools:
        _log({"type": "multitool_empty", "query": query[:100]})
        return route_and_execute(query, context)

    results = []
    output_parts = []
    action_queries = {"on": "prende las luces", "off": "apaga las luces",
                      "cinema": "modo cinema", "relax": "modo relax", "work": "modo trabajo"}

    for tool_name in tools:
        skill_cat = get_skill_category(tool_name)
        if not skill_cat:
            results.append({"tool": tool_name, "success": False, "error": "no_skill_mapping"})
            continue
        action = get_tool_action(tool_name)
        exec_query = action_queries.get(action, query) if action else query
        skill_result = execute_skill(skill_cat, exec_query)
        results.append({"tool": tool_name, "skill": skill_cat,
                        "success": skill_result["success"],
                        "output": skill_result["output"],
                        "error": skill_result.get("error")})
        _routing_metrics["multitool_tools_executed_total"] += 1
        if skill_result["success"] and skill_result["output"]:
            output_parts.append(skill_result["output"])

    duration = time.monotonic() - t0
    aggregated = "\n\n".join(output_parts) if output_parts else "Sin resultados"

    _log({"type": "multitool_execution", "query": query[:100], "tools": tools,
          "results_ok": sum(1 for r in results if r.get("success")),
          "results_fail": sum(1 for r in results if not r.get("success")),
          "duration_s": round(duration, 2)})

    return {
        "executed": True, "multitool": True, "tools_used": tools,
        "results": results, "aggregated_text": aggregated,
        "fallback_used": False, "classification": "multitool", "skill_result": None,
    }
