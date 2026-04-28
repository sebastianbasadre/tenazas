#!/usr/bin/env python3
"""
Cognitive Core — Structured reasoning layer for Tenazas.

Sits between orchestrator and execution pipelines. Transforms Tenazas
from a reactive assistant into a reasoning system that thinks before acting.

Flow: user input → orchestrator → cognitive_core → planning → pipeline → execution

OODA loop: Observe → Orient → Decide → Act

Components:
  1. Intent Interpreter — classify what the user wants
  2. Task Decomposer — break complex problems into sub-questions
  3. Reasoning Depth Controller — FAST / STANDARD / DEEP
  4. Strategy Selector — choose reasoning approach
  5. Cognitive Loop — OODA cycle with confidence feedback
  6. Multi-agent coordination — escalate when needed

Safety: NEVER executes infrastructure changes. Only recommends actions.
"""

import json
import re
import time
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
TELEMETRY_LOG = BASE_DIR / "logs" / "cognitive_core.log"

# ── Reasoning Depths ─────────────────────────────────────────────

FAST = "fast"
STANDARD = "standard"
DEEP = "deep"

# ── Intent Categories ────────────────────────────────────────────

INTENTS = {
    "infrastructure_issue",
    "knowledge_question",
    "system_diagnostics",
    "automation_request",
    "monitoring_request",
    "conversational",
}

# ── Intent Classification Rules ──────────────────────────────────

_INTENT_RULES = [
    (
        "system_diagnostics",
        [r"diagn[oó]stic", r"health\s*check", r"status.*sistema",
         r"est[aá]\s*todo", r"reporte.*infra", r"chequeo"],
        {"diagnostico", "status", "health", "reporte", "chequeo"},
    ),
    (
        "infrastructure_issue",
        [r"(no|sin)\s*(funciona|anda|responde)", r"ca[iy]do", r"lento",
         r"error", r"crash", r"timeout", r"leak", r"offline",
         r"por\s*qu[eé].*ca", r"por\s*qu[eé].*no\s*funciona"],
        {"caido", "roto", "lento", "error", "crash", "offline",
         "timeout", "leak", "fallo"},
    ),
    (
        "monitoring_request",
        [r"monitorea", r"vigil[aá]", r"alert.*cuando",
         r"avis[aá].*si", r"notific[aá]"],
        {"monitorear", "vigilar", "alertar", "avisar", "notificar"},
    ),
    (
        "automation_request",
        [r"reinici[aá]", r"restart", r"ejecut[aá]", r"corr[eé]",
         r"prend[eé].*lu[cz]", r"apag[aá].*lu[cz]", r"scan",
         r"backup"],
        {"reiniciar", "restart", "ejecutar", "prender", "apagar",
         "scan", "backup"},
    ),
    (
        "knowledge_question",
        [r"(c[oó]mo|qu[eé]|cu[aá]l|d[oó]nde|por\s*qu[eé])\s",
         r"explic[aá]", r"qu[eé]\s+es", r"cu[aá]nto"],
        {"como", "que", "cual", "donde", "porque", "explicar",
         "configurar"},
    ),
]

# ── Decomposition Templates ──────────────────────────────────────

_DECOMPOSITION_TEMPLATES = {
    "infrastructure_issue": [
        "Verificar estado actual del componente afectado",
        "Revisar eventos recientes relacionados",
        "Consultar patrones historicos en infra_memory",
        "Analizar posibles causas raiz",
        "Evaluar impacto en servicios dependientes",
        "Proponer acciones correctivas",
    ],
    "system_diagnostics": [
        "Recolectar estado del sistema (containers, red, VPN, Ollama)",
        "Ejecutar diagnosticos rule-based",
        "Comparar con patrones conocidos",
        "Generar reporte de salud",
    ],
    "knowledge_question": [
        "Buscar en RAG pipeline",
        "Evaluar calidad del retrieval",
        "Si confianza baja, expandir busqueda",
        "Sintetizar respuesta",
    ],
    "automation_request": [
        "Validar accion contra safety_guard",
        "Identificar target y skill correspondiente",
        "Generar plan de ejecucion",
        "Presentar plan para aprobacion",
    ],
    "monitoring_request": [
        "Identificar metrica o componente a monitorear",
        "Verificar estado actual",
        "Configurar alerta si aplica",
    ],
    "conversational": [
        "Responder directamente",
    ],
}

# ── Strategy Definitions ─────────────────────────────────────────

STRATEGIES = {
    "direct_answer": {
        "depth": FAST,
        "pipelines": [],
        "description": "Respuesta directa sin pipeline",
    },
    "rag_retrieval": {
        "depth": STANDARD,
        "pipelines": ["rag_pipeline"],
        "description": "Busqueda en knowledge base",
    },
    "infra_diagnostics": {
        "depth": STANDARD,
        "pipelines": ["infra_diagnostics", "action_mapper"],
        "description": "Diagnostico de infraestructura",
    },
    "hybrid_reasoning": {
        "depth": STANDARD,
        "pipelines": ["rag_pipeline", "infra_diagnostics"],
        "description": "RAG + diagnosticos combinados",
    },
    "multi_agent_reasoning": {
        "depth": DEEP,
        "pipelines": ["multi_agent_engine", "rag_pipeline"],
        "description": "Razonamiento cooperativo multi-agente",
    },
}


# ── Helpers ──────────────────────────────────────────────────────



def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── 1. Intent Interpreter ────────────────────────────────────────


def interpret_intent(query: str, context: dict | None = None) -> dict:
    """Classify query intent using keyword heuristics.

    Returns: {intent, confidence, signals}
    """
    query_lower = query.lower().strip()
    words = set(re.findall(r"\w+", query_lower))

    best_intent = "conversational"
    best_score = 0
    signals = []

    for intent, patterns, keywords in _INTENT_RULES:
        score = 0

        for pattern in patterns:
            if re.search(pattern, query_lower):
                score += 3
                signals.append(f"pattern:{pattern[:20]}")
                break

        kw_matches = words & keywords
        score += len(kw_matches)
        if kw_matches:
            signals.append(f"keywords:{','.join(list(kw_matches)[:3])}")

        # Context boost
        if context and context.get("recent_events"):
            for event in context["recent_events"]:
                etype = event.get("type", "")
                if intent == "infrastructure_issue" and etype in (
                    "container_down", "vpn_down", "vpn_leak", "internet_lost"
                ):
                    score += 2
                    signals.append(f"context:{etype}")

        if score > best_score:
            best_score = score
            best_intent = intent

    if best_score < 2:
        best_intent = "conversational"

    # Confidence based on score strength
    confidence = min(best_score / 6.0, 1.0)

    return {
        "intent": best_intent,
        "confidence": round(confidence, 4),
        "signals": signals,
    }


# ── 2. Task Decomposer ──────────────────────────────────────────


def decompose_task(query: str, intent: str) -> list:
    """Break a complex query into reasoning sub-steps.

    Returns: list of step dicts [{step, description, requires_data}]
    """
    template = _DECOMPOSITION_TEMPLATES.get(intent, ["Analizar y responder"])

    steps = []
    for i, desc in enumerate(template, 1):
        step = {
            "step": i,
            "description": desc,
            "status": "pending",
        }

        # Tag steps that need data
        if any(kw in desc.lower() for kw in ["verificar", "recolectar", "revisar", "buscar"]):
            step["requires_data"] = True
        else:
            step["requires_data"] = False

        steps.append(step)

    # For complex queries (long, multi-clause), add correlation step
    if len(query.split()) > 10 and intent != "conversational":
        steps.append({
            "step": len(steps) + 1,
            "description": "Correlacionar hallazgos y sintetizar respuesta",
            "status": "pending",
            "requires_data": False,
        })

    return steps


# ── 3. Reasoning Depth Controller ───────────────────────────────


def _estimate_complexity(query: str, intent: str,
                         context: dict | None = None) -> float:
    """Estimate query complexity (0-1).

    Factors: query length, multi-clause, intent type, context richness.
    """
    score = 0.0

    # Length factor
    word_count = len(query.split())
    if word_count > 15:
        score += 0.3
    elif word_count > 8:
        score += 0.15

    # Multi-clause (questions with "y", "pero", "ademas")
    if re.search(r"\b(y|pero|adem[aá]s|tambi[eé]n|por\s+otro\s+lado)\b", query.lower()):
        score += 0.2

    # Intent complexity
    intent_weights = {
        "conversational": 0.0,
        "knowledge_question": 0.1,
        "monitoring_request": 0.1,
        "automation_request": 0.2,
        "system_diagnostics": 0.3,
        "infrastructure_issue": 0.4,
    }
    score += intent_weights.get(intent, 0.1)

    # Context: recent events add complexity
    if context and context.get("recent_events"):
        score += min(len(context["recent_events"]) * 0.05, 0.2)

    return round(min(score, 1.0), 4)


def select_reasoning_depth(query: str, intent: str,
                           context: dict | None = None) -> dict:
    """Select reasoning depth based on complexity.

    Returns: {depth, complexity, rationale}
    """
    complexity = _estimate_complexity(query, intent, context)

    if complexity < 0.25:
        depth = FAST
        rationale = "Query simple, respuesta directa"
    elif complexity < 0.55:
        depth = STANDARD
        rationale = "Complejidad moderada, pipeline standard"
    else:
        depth = DEEP
        rationale = "Query compleja, razonamiento profundo"

    return {
        "depth": depth,
        "complexity": complexity,
        "rationale": rationale,
    }


# ── 4. Strategy Selector ────────────────────────────────────────


def select_reasoning_strategy(intent: str, depth: str) -> dict:
    """Choose the best reasoning strategy.

    Returns: strategy dict with pipelines and description.
    """
    # Strategy matrix: intent × depth
    matrix = {
        ("conversational", FAST): "direct_answer",
        ("conversational", STANDARD): "direct_answer",
        ("conversational", DEEP): "rag_retrieval",

        ("knowledge_question", FAST): "rag_retrieval",
        ("knowledge_question", STANDARD): "rag_retrieval",
        ("knowledge_question", DEEP): "multi_agent_reasoning",

        ("infrastructure_issue", FAST): "infra_diagnostics",
        ("infrastructure_issue", STANDARD): "hybrid_reasoning",
        ("infrastructure_issue", DEEP): "multi_agent_reasoning",

        ("system_diagnostics", FAST): "infra_diagnostics",
        ("system_diagnostics", STANDARD): "infra_diagnostics",
        ("system_diagnostics", DEEP): "hybrid_reasoning",

        ("automation_request", FAST): "direct_answer",
        ("automation_request", STANDARD): "infra_diagnostics",
        ("automation_request", DEEP): "hybrid_reasoning",

        ("monitoring_request", FAST): "direct_answer",
        ("monitoring_request", STANDARD): "infra_diagnostics",
        ("monitoring_request", DEEP): "hybrid_reasoning",
    }

    strategy_name = matrix.get((intent, depth), "rag_retrieval")
    strategy = STRATEGIES[strategy_name]

    return {
        "name": strategy_name,
        "depth": strategy["depth"],
        "pipelines": strategy["pipelines"],
        "description": strategy["description"],
    }


# ── 5. Cognitive Loop (OODA) ────────────────────────────────────


def _observe(query: str, context: dict | None = None) -> dict:
    """Observe: collect all available context."""
    observation = {
        "query": query,
        "query_length": len(query.split()),
        "has_context": context is not None,
        "recent_events_count": len(context.get("recent_events", [])) if context else 0,
        "has_working_memory": bool(context.get("working_memory")) if context else False,
    }

    # Check infra memory for relevant patterns
    try:
        from runtime.infra_memory import get_active_patterns
        active = get_active_patterns()
        observation["active_patterns"] = len(active)
        observation["patterns"] = [p["pattern"] for p in active[:3]]
    except ImportError:
        observation["active_patterns"] = 0
        observation["patterns"] = []

    return observation


def _orient(query: str, observation: dict,
            context: dict | None = None) -> dict:
    """Orient: interpret the situation."""
    intent_result = interpret_intent(query, context)
    depth_result = select_reasoning_depth(
        query, intent_result["intent"], context
    )

    return {
        "intent": intent_result,
        "depth": depth_result,
        "has_active_patterns": observation.get("active_patterns", 0) > 0,
    }


def _decide(query: str, orientation: dict) -> dict:
    """Decide: choose strategy and plan."""
    intent = orientation["intent"]["intent"]
    depth = orientation["depth"]["depth"]

    strategy = select_reasoning_strategy(intent, depth)
    steps = decompose_task(query, intent)

    return {
        "strategy": strategy,
        "steps": steps,
        "step_count": len(steps),
    }


def _act(decision: dict) -> dict:
    """Act: prepare execution plan (does NOT execute).

    Returns the execution plan for the caller to run.
    """
    return {
        "pipelines": decision["strategy"]["pipelines"],
        "strategy_name": decision["strategy"]["name"],
        "depth": decision["strategy"]["depth"],
        "steps": decision["steps"],
        "ready": True,
    }


# ── 6. Confidence Feedback ──────────────────────────────────────


def evaluate_confidence(results: list, strategy: dict) -> dict:
    """Evaluate confidence and decide if escalation is needed.

    Integrates with confidence_engine and retrieval_quality.
    """
    # Compute confidence from results
    try:
        from runtime.confidence_engine import compute_confidence
        confidence = compute_confidence(results)
    except ImportError:
        scores = [r.get("score", 0) for r in results]
        avg = sum(scores) / max(len(scores), 1)
        confidence = {
            "score": round(avg, 4),
            "level": "high" if avg >= 0.8 else "medium" if avg >= 0.6 else "low",
        }

    # Evaluate retrieval quality if applicable
    retrieval_quality = None
    if strategy.get("name") in ("rag_retrieval", "hybrid_reasoning"):
        try:
            from runtime.retrieval_quality import evaluate
            retrieval_quality = evaluate(results)
        except ImportError:
            pass

    # Decide escalation
    should_escalate = False
    escalation_reason = None

    if confidence["level"] == "low":
        should_escalate = True
        escalation_reason = "low_confidence"
    elif retrieval_quality and retrieval_quality.get("should_expand"):
        should_escalate = True
        escalation_reason = "low_retrieval_quality"

    return {
        "confidence": confidence,
        "retrieval_quality": retrieval_quality,
        "should_escalate": should_escalate,
        "escalation_reason": escalation_reason,
    }


def escalate_depth(current_depth: str) -> str:
    """Escalate to deeper reasoning."""
    escalation = {FAST: STANDARD, STANDARD: DEEP, DEEP: DEEP}
    return escalation[current_depth]


# ── Main API ─────────────────────────────────────────────────────


def reason(query: str, context: dict | None = None) -> dict:
    """Run the full cognitive loop.

    OODA: Observe → Orient → Decide → Act

    Returns:
        {
            "observation": dict,
            "orientation": dict,
            "decision": dict,
            "execution_plan": dict,
            "trace": dict,
        }
    """
    start = time.monotonic()

    # O — Observe
    observation = _observe(query, context)

    # O — Orient
    orientation = _orient(query, observation, context)

    # D — Decide
    decision = _decide(query, orientation)

    # A — Act (prepare plan, don't execute)
    execution_plan = _act(decision)

    latency_ms = round((time.monotonic() - start) * 1000, 1)

    result = {
        "observation": observation,
        "orientation": {
            "intent": orientation["intent"]["intent"],
            "intent_confidence": orientation["intent"]["confidence"],
            "depth": orientation["depth"]["depth"],
            "complexity": orientation["depth"]["complexity"],
        },
        "decision": {
            "strategy": decision["strategy"]["name"],
            "pipelines": decision["strategy"]["pipelines"],
            "steps": decision["steps"],
        },
        "execution_plan": execution_plan,
        "latency_ms": latency_ms,
    }

    # Log decision trace
    _log({
        "type": "cognitive_loop",
        "query": query[:100],
        "intent": orientation["intent"]["intent"],
        "depth": orientation["depth"]["depth"],
        "strategy": decision["strategy"]["name"],
        "complexity": orientation["depth"]["complexity"],
        "latency_ms": latency_ms,
    })

    # Integration with decision_trace if available
    try:
        from runtime.decision_trace import trace
        trace(
            query=query,
            classification=orientation["intent"]["intent"],
            pipeline=decision["strategy"]["name"],
            confidence=orientation["intent"]["confidence"],
            latency_ms=latency_ms,
        )
    except ImportError:
        pass

    return result


def reason_with_feedback(
    query: str,
    results: list,
    context: dict | None = None,
) -> dict:
    """Run cognitive loop with confidence feedback. Escalates if needed.

    Args:
        query: user query
        results: retrieval/pipeline results to evaluate
        context: optional context dict

    Returns: cognitive result + confidence evaluation + possible re-plan.
    """
    # Initial reasoning
    cognitive = reason(query, context)

    # Evaluate confidence
    feedback = evaluate_confidence(
        results, cognitive["decision"]
    )

    cognitive["feedback"] = {
        "confidence": feedback["confidence"],
        "should_escalate": feedback["should_escalate"],
        "escalation_reason": feedback.get("escalation_reason"),
    }

    # Auto-escalate if needed
    if feedback["should_escalate"]:
        current_depth = cognitive["orientation"]["depth"]
        new_depth = escalate_depth(current_depth)

        if new_depth != current_depth:
            intent = cognitive["orientation"]["intent"]
            new_strategy = select_reasoning_strategy(intent, new_depth)
            new_steps = decompose_task(query, intent)

            cognitive["escalation"] = {
                "from_depth": current_depth,
                "to_depth": new_depth,
                "new_strategy": new_strategy["name"],
                "new_pipelines": new_strategy["pipelines"],
                "new_steps": new_steps,
                "reason": feedback.get("escalation_reason", "low_confidence"),
            }

            _log({
                "type": "cognitive_escalation",
                "query": query[:100],
                "from": current_depth,
                "to": new_depth,
                "reason": feedback.get("escalation_reason"),
            })

    return cognitive
