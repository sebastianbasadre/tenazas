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
import os
import re
import subprocess
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
        "id": "ollama/qwen3:8b",
        "alias": "local-heavy",
        "tier": "local_gpu",
        "strengths": ["reasoning", "diagnostics", "analysis"],
    },
    "qwen_3b": {
        "id": "ollama/qwen2.5:3b",
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
    "openrouter_kimi": {
        "id": "openrouter/moonshotai/kimi-k2",
        "alias": "cloud-openrouter",
        "tier": "cloud_openrouter",
        "strengths": ["general", "reasoning", "analysis", "complex", "code"],
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
    "openrouter_usage": "fast",
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
    "openrouter_usage": {
        "description": "OpenRouter balance and usage query",
        "modules": ["openrouter_usage"],
        "default_model": "qwen_3b",
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
    # OpenRouter usage / balance (check before general keywords)
    (
        "openrouter_usage",
        [r"gastos?\s+openrouter", r"balance\s+openrouter", r"cr[eé]ditos?\s+openrouter",
         r"cu[aá]nto\s+llevo\s+gastado\s+.*openrouter", r"openrouter\s+balance",
         r"openrouter\s+usage", r"saldo\s+openrouter"],
        {"openrouter", "balance", "creditos", "créditos", "gastado"},
    ),
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
         "invertido", "inversiones", "broker",
         "dólar", "dolar", "dollar", "usd", "blue", "mep", "ccl"},
    ),
    # ── Skill-specific rules (before generic categories) ──────────
    # Finance / Expenses
    (
        "skill:expense",
        [r"gastos?\s+(r[aá]pidos?|de\s+hoy|del\s+d[ií]a|de\s+la\s+semana|semanales?)",
         r"(qu[eé]|cu[aá]nto)\s+gast[eé]", r"mis\s+gastos",
         r"resumen\s+(de\s+)?(gastos|finanzas|financiero)",
         r"(balance|resumen)\s+(del\s+)?mes",
         r"(cuánto|cuanto)\s+(llevo|gast[eé]|me\s+queda)",
         r"(agregar?|registrar?|anotar?)\s+(un\s+)?gasto"],
        {"gastos", "gasto", "gasté", "finanzas", "balance", "resumen"},
    ),
    # Weather
    (
        "skill:weather",
        [r"(qu[eé]\s+)?clima", r"(qu[eé]\s+)?temperatura",
         r"(qu[eé]\s+)?tiempo\s+(hace|hay|hará|va\s+a\s+hacer)",
         r"va\s+a\s+llover", r"lluvia", r"pron[oó]stico",
         r"hace\s+(fr[ií]o|calor)", r"weather",
         r"c[oó]mo\s+(est[aá]|va|anda)\s+(el\s+)?(clima|tiempo)",
         r"(llover|llueve|lloviendo)",
         r"(proyecci[oó]n|predicci[oó]n)\s+(del?\s+)?(clima|tiempo|semana)",
         r"(qu[eé]\s+onda|c[oó]mo\s+viene)\s+(el\s+)?(clima|tiempo)"],
        {"clima", "temperatura", "lluvia", "pronostico", "pronóstico",
         "weather", "frio", "frío", "calor", "llover", "llueve",
         "tiempo"},
    ),
    # Email
    (
        "skill:email",
        [r"(revis|cheque|ver|leer|mostr|abr[ií])\w*\s*(los\s+|mis\s+)?(e-?mails?|correos?|inbox|mails?)",
         r"e-?mails?\s+(sin\s+leer|nuevos|recientes)",
         r"(tengo|hay)\s+(e-?mails?|correos?|mails?)",
         r"mis\s+(e-?mails?|correos?|mails?)",
         r"(cu[aá]ntos|cuantos)\s+(e-?mails?|correos?|mails?)",
         r"inbox", r"(unread|correo\s+nuevo)",
         r"^e-?mails?$",
         r"(mand|envi)[aá]\w*\s+(un\s+)?(e-?mail|correo|mail)",
         r"(algo\s+)?(urgente|importante|nuevo)\s+en\s+(el\s+)?(mail|correo|inbox)",
         r"(resum[ií]|cheque[aá])\w*\s+(el\s+)?(mail|inbox|correo)",
         r"bandeja\s+de\s+entrada"],
        {"email", "emails", "correo", "correos", "inbox", "unread",
         "mail", "mails", "gmail", "bandeja"},
    ),
    # Calendar
    (
        "skill:calendar",
        [r"(qu[eé]\s+)?eventos?\s+(tengo|hay|de\s+hoy|de\s+mañana)",
         r"(mi\s+)?agenda(\s+de\s+hoy)?", r"(mi\s+)?calendario",
         r"(qu[eé]\s+tengo|tengo\s+algo)\s+(hoy|mañana|esta\s+semana)",
         r"reuni[oó]n", r"pr[oó]xim[oa]s?\s+eventos?",
         r"(qu[eé]\s+me\s+espera|qu[eé]\s+hay)\s+(hoy|mañana)",
         r"(libre|disponible|ocupad)\w*\s+(el\s+)?(lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)",
         r"horario\s+(de\s+)?(la\s+)?(semana|hoy|mañana)",
         r"(agreg[aá]|cre[aá]|pon[eé])\w*\s+(una?\s+)?(reuni[oó]n|evento|cita)",
         r"(sin\s+)?reservas?\s+(el\s+)?(lunes|martes|miércoles|miercoles|jueves|viernes|sábado|sabado|domingo)"],
        {"calendario", "agenda", "eventos", "evento", "reunión", "reunion",
         "reuniones", "mañana", "horario", "cita"},
    ),
    # Smart home — lights (action-oriented only, not informational)
    # Cinema patterns evaluated first in handler (_handle_smart_home)
    (
        "skill:smart-home",
        [r"(prend|encend|apag|cambi)\w*\s*(las?\s+)?(lu[cz]|l[aá]mpara|alumbrad)",
         r"modo\s+(cine|cinema|pel[ií]cula|relax|trabajo|work)",
         r"(pon[eé]|activ[aá])\s*(modo|escena|cine|cinema)",
         r"(subi|baj)\w*\s*(el\s+)?brillo",
         r"hue\s+(on|off)", r"escena\s+\w+",
         r"(estado|status)\s+(de\s+)?(las?\s+)?lu[cz]",
         r"\b(cine|cinema)\b",
         r"(prepar|pon[eé])\w*\s*(netflix|hbo|disney|serie|pel[ií]cula|movie)",
         r"(ver|mirar)\s+(una?\s+)?(peli|pel[ií]cula|serie|movie)",
         r"(netflix|hbo|disney)\s*(plus)?",
         r"(dejar|dej[aá])\s+(la\s+)?luz\s+(prendida|encendida|apagada)",
         r"lu[cz](es)?\s+(on|off)",
         r"(quier|vamos?\s+a)\s+(ver\s+)?(una?\s+)?(peli|pel[ií]cula|movie|serie)"],
        {"apagar", "encender", "prender", "cinema", "relax", "escena",
         "cine", "película", "netflix", "hbo", "disney", "serie",
         "luces", "luz", "brillo", "lámpara", "alumbrado"},
    ),
    # TTS
    (
        "skill:tts",
        [r"(dec[ií]|habl[aá]|le[eé])\s+en\s+voz", r"dec[ií]\s+",
         r"habl[aá]\s+", r"(mand|envi)[aá]\s+(un\s+)?audio",
         r"text.to.speech", r"voz\s+alta"],
        {"decí", "habla", "hablá", "audio", "voz", "deci", "tts"},
    ),
    # Network scanner
    (
        "skill:network-scanner",
        [r"(escan|scan)\w*\s*(la\s+)?red", r"dispositivos?\s+(en|de)\s+(la\s+)?red",
         r"qui[eé]n\s+est[aá]\s+conectad", r"nmap",
         r"(cu[aá]ntos|qu[eé])\s+dispositivos",
         r"network[\s-]?scan"],
        {"red", "dispositivos", "nmap", "conectado", "conectados",
         "scanner", "escanear", "scan", "network"},
    ),
    # Arr monitor (media stack) — only status/listing queries
    # Queries with "caido/error/no funciona" go to infrastructure_issue instead
    (
        "skill:arr-monitor",
        [r"(estado|status|c[oó]mo\s+(est[aá]n?|andan?))\s+(de\s+)?(los?\s+)?(sonarr|radarr|lidarr|prowlarr|arr|descargas|torrent)",
         r"descargas?\s+(activ|pendient|estado)",
         r"(arr|media)\s+stack",
         r"(estado|status)\s+(del?\s+)?(arr|media|torrent)",
         r"(qu[eé]\s+)?(est[aá]\s+)?(bajando|descargando)",
         r"cola\s+(de\s+)?(radarr|sonarr|descargas|torrent)",
         r"(c[oó]mo\s+(va|anda)\s+(el\s+)?(torrent|sonarr|radarr))",
         r"(estado|status|c[oó]mo)\s+(de\s+)?\b(sonarr|radarr|lidarr|prowlarr|qbittorrent|sabnzbd)\b"],
        {"descargas", "torrent", "bajando", "descargando"},
    ),
    # Infra monitor (containers, docker status)
    (
        "skill:infra-monitor",
        [r"(estado|status)\s+(de\s+)?(los\s+)?containers?",
         r"(estado|status)\s+(del?\s+)?docker",
         r"(estado|status)\s+(del?\s+)?(sistema|infra|nas)",
         r"(cu[aá]ntos|qu[eé])\s+containers?",
         r"docker\s+(ps|status|estado)",
         r"(est[aá]n?|andan?)\s+(todos\s+)?(los\s+)?containers?",
         r"^containers?$",
         r"^docker$",
         r"^infra$",
         r"(c[oó]mo\s+(est[aá]|anda)\s+(el\s+)?(nas|sistema|infra|docker))",
         r"salud\s+(de\s+)?(la\s+)?(infra|infraestructura)",
         r"(hay\s+algo\s+)?(roto|caído|caido)\s+(en\s+)?(el\s+)?(sistema|infra|docker)",
         r"espacio\s+(en\s+)?(el\s+)?disco"],
        {"containers", "container", "docker", "infra", "infraestructura",
         "nas", "disco"},
    ),
    # Morning greeting
    (
        "skill:morning-greeting",
        [r"buenos?\s+d[ií]as?", r"good\s+morning", r"buen\s+d[ií]a",
         r"saludo\s+(matutino|diario)"],
        {"buenos", "días", "dias", "morning"},
    ),
    # Self-evolve (proposals, improvements)
    (
        "skill:self-evolve",
        [r"(propuestas?|mejoras?)\s+(de\s+)?(mejora|sistema|tenazas)",
         r"self[\s-]evolve", r"auto[\s-]mejora",
         r"(qu[eé]\s+)?propuestas?\s+(hay|tengo|pendientes?)",
         r"cu[aá]l\s+es\s+la\s+propuesta",
         r"(mostr|cont)[aá]\w*\s+(las?\s+)?propuestas?",
         r"(cu[aá]les?\s+son\s+)?(tus?|las?|mis)\s+propuestas?",
         r"qu[eé]\s+propuso\s+self[\s-]?evolve",
         r"propuestas?\s+de\s+mejora",
         r"sobre\s+(el\s+)?self[\s-]?evolve",
         r"(c[oó]mo\s+(est[aá]s|ven[ií]s)\s+funcionando)",
         r"(qu[eé]\s+pod[eé]s|c[oó]mo\s+pod[eé]s)\s+mejorar",
         r"(hac[eé]|corr[eé])\s+(el\s+)?(review|an[aá]lisis)",
         r"(an[aá]lisis|revisi[oó]n)\s+(semanal|del\s+sistema)",
         r"revis[aá]\s+(c[oó]mo\s+)?(ven[ií]s|est[aá]s|funcion)"],
        {"propuestas", "mejoras", "propuesta", "mejora", "evolve",
         "self-evolve", "análisis", "analisis", "review"},
    ),
    # Reminders
    (
        "skill:reminders",
        [r"(record|acord|avis)[aá]\w*\s+(que|de|a\s+las)",
         r"(pon[eé]|cre[aá]|agreg[aá])\w*\s+(un\s+)?recordatorio",
         r"reminder", r"recordatorios?\s+(activ|pendient)",
         r"(mis|los)\s+recordatorios"],
        {"recordatorio", "recordatorios", "reminder", "reminders",
         "avisame", "avísame", "acordar", "recordar"},
    ),

    # Model trainer (fine-tuning pipeline)
    (
        "skill:model-trainer",
        [r"(entren[aá]|re-?entren[aá])\s+(el\s+)?modelo",
         r"(pipeline|corr[eé])\s+(de\s+)?training",
         r"fine[\s-]?tun(e|ing)",
         r"(actualiz[aá]|actualizá)\s+(el\s+)?modelo\s+(de\s+)?tools",
         r"(estado|[úu]ltimo)\s+(del?\s+)?training"],
        {"training", "entrenar", "finetune", "finetuning", "multitool"},
    ),

    # ── Generic categories ──────────────────────────────────────────
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
         r"scan", r"escane[aá]", r"backup"],
        {"reiniciar", "restart", "ejecutar", "correr",
         "scan", "escanear", "backup"},
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

    # ── Router V3: finetuned_router + intent_normalizer ──────────
    # Feature flag: "use_finetuned_router": true in openclaw.json
    # If active and router gives >= medium confidence, use that result.
    # If it fails or gives low confidence, fall through to deterministic scoring.
    try:
        _cfg_path = Path("/home/tenazas/.openclaw/openclaw.json")
        if _cfg_path.exists():
            _cfg = json.loads(_cfg_path.read_text())
        else:
            _cfg = {}
        if _cfg.get("tenazas", {}).get("use_finetuned_router"):
            from finetuned_router import route as _ft_route
            from intent_normalizer import normalize_or_chat as _ft_normalize
            _router_out = _ft_route(query)
            _call = _ft_normalize(_router_out, query)
            if _call and _call.confidence in ("high", "medium") and _call.valid:
                _log({"type": "router_v3", "intent": _call.intent,
                      "skill": _call.skill, "confidence": _call.confidence,
                      "source": _call.source, "query": query[:100]})
                return _call.skill if _call.skill != "chat" else "chat"
    except Exception:
        pass  # any failure → fall through to deterministic scoring
    # ── end Router V3 ────────────────────────────────────────────

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

    # Skill categories require score >= 3 (pattern match) to avoid false positives
    if best_category.startswith("skill:") and best_score < 3:
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
    # Normalize skill categories for model selection
    if category.startswith("skill:"):
        category = "automation_command"

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
        available_backends = {
            "local_gpu": True, "local_cpu": True,
            "cloud_openrouter": _is_openrouter_available(),
            "cloud": True,
        }

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
        "chat": ["openrouter_kimi", "gemini_flash", "qwen_3b", "phi4"],
        "knowledge_query": ["openrouter_kimi", "gemini_flash", "qwen_7b", "qwen_3b"],
        "infrastructure_issue": ["qwen_7b", "qwen_3b", "openrouter_kimi", "gemini_flash"],
        "automation_command": ["qwen_3b", "qwen_7b", "openrouter_kimi", "gemini_flash"],
        "diagnostic_request": ["qwen_7b", "qwen_3b", "openrouter_kimi", "gemini_flash"],
        "complex_reasoning": ["gemini_pro", "qwen_7b", "openrouter_kimi", "gemini_flash"],
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

    # Nothing available — try OpenRouter before Gemini last resort
    if _is_openrouter_available():
        _log({
            "type": "cloud_fallback",
            "reason": "all local backends offline — OpenRouter last resort",
            "category": category,
            "model": MODELS["openrouter_kimi"]["id"],
        })
        _record_metrics(
            role=CATEGORY_TO_ROLE.get(category, "fast"),
            model=MODELS["openrouter_kimi"]["id"], backend="cloud_openrouter",
            v2_used=False, cloud_fallback=True,
        )
        return MODELS["openrouter_kimi"]

    # Final fallback — Gemini cloud (explicit policy)
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

    Skill categories (skill:*) map to automation_command pipeline.
    Returns: {name, description, modules, default_model}
    """
    lookup = category
    if category.startswith("skill:"):
        lookup = "automation_command"
    pipeline = PIPELINES.get(lookup, PIPELINES["chat"])
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


# ── Skill Execution ─────────────────────────────────────────────

# Maps skill category → executable command template
# {command} is run inside the openclaw container via subprocess
# "destructive": True means the skill modifies state (requires confirmation)
# "parser": optional callable name to post-process output

SKILL_MAP = {
    "skill:weather": {
        "command": (
            'curl -sf "https://api.open-meteo.com/v1/forecast'
            '?latitude=-34.61&longitude=-58.38'
            '&current_weather=true&timezone=America/Buenos_Aires"'
        ),
        "destructive": False,
        "description": "Clima actual en Buenos Aires",
        "parser": "_parse_weather",
    },
    "skill:email": {
        "command": "himalaya envelope list --folder INBOX --page-size 20",
        "destructive": False,
        "description": "Emails recientes",
        "handler": "_handle_email",
    },
    "skill:calendar": {
        "command": (
            "python3 /home/tenazas/.openclaw/workspace/skills/"
            "calendar-agenda/gcal.py 2"
        ),
        "destructive": False,
        "description": "Eventos del calendario",
    },
    "skill:smart-home": {
        # Default: list light states. Actual action extracted from query.
        "command": (
            'curl -sf http://192.168.2.11/api/'
            '${HUE_API_KEY}/lights'
        ),
        "destructive": True,
        "description": "Control de luces Hue",
        "handler": "_handle_smart_home",
    },
    "skill:tts": {
        "command": 'bash /home/tenazas/.openclaw/workspace/skills/tts/tts.sh "{text}"',
        "destructive": True,
        "description": "Text-to-speech",
        "handler": "_handle_tts",
    },
    "skill:network-scanner": {
        "command": "nmap -sn 192.168.2.0/24",
        "destructive": False,
        "description": "Scan de red LAN",
    },
    "skill:arr-monitor": {
        "command": (
            "docker ps --filter name=gluetun --filter name=qbittorrent "
            "--filter name=sonarr --filter name=radarr --filter name=lidarr "
            "--filter name=prowlarr --filter name=seerr --filter name=requestrr "
            "--filter name=tautulli --filter name=sabnzbd "
            '--format "{{.Names}}\t{{.Status}}"'
        ),
        "destructive": False,
        "description": "Estado del arr-stack",
    },
    "skill:infra-monitor": {
        "command": 'docker ps -a --format "{{.Names}}\t{{.Status}}"',
        "destructive": False,
        "description": "Estado de containers Docker",
        "parser": "_parse_infra",
    },
    "skill:morning-greeting": {
        "command": (
            "python3 /home/tenazas/.openclaw/workspace/skills/"
            "calendar-agenda/gcal.py 1"
        ),
        "destructive": False,
        "description": "Saludo matutino",
        "handler": "_handle_morning_greeting",
    },
    "skill:self-evolve": {
        "command": "cat /home/tenazas/runtime/learning/proposals.json 2>/dev/null || echo '[]'",
        "destructive": False,
        "description": "Propuestas de mejora",
        "parser": "_parse_proposals",
    },
    "skill:reminders": {
        "command": (
            "cat /home/tenazas/.openclaw/workspace/memory/reminders.json "
            "2>/dev/null || echo '[]'"
        ),
        "destructive": True,
        "description": "Recordatorios",
        "handler": "_handle_reminders",
    },
    "skill:expense": {
        "command": "",
        "destructive": False,
        "description": "Gastos y finanzas personales",
        "handler": "_handle_expense",
    },
    "skill:model-trainer": {
        "command": (
            "python3 /home/tenazas/.openclaw/workspace/skills/"
            "model-trainer/trainer_orchestrator.py --action status"
        ),
        "destructive": False,
        "description": "Pipeline de training multitool",
        "handler": "_handle_model_trainer",
    },
}

# Timeout per skill (seconds)
SKILL_TIMEOUT = 30


def execute_skill(skill_category: str, query: str = "") -> dict:
    """Execute a skill and return real output.

    Args:
        skill_category: "skill:weather", "skill:email", etc.
        query: original user query (for parameterized skills)

    Returns:
        {
            "skill": str,
            "success": bool,
            "output": str,      # human-readable result
            "raw": str,         # raw command output
            "destructive": bool,
            "error": str|None,
        }
    """
    skill = SKILL_MAP.get(skill_category)
    if not skill:
        return {
            "skill": skill_category,
            "success": False,
            "output": f"Skill desconocido: {skill_category}",
            "raw": "",
            "destructive": False,
            "error": "unknown_skill",
        }

    # Check for custom handler
    handler_name = skill.get("handler")
    if handler_name:
        handler = _SKILL_HANDLERS.get(handler_name)
        if handler:
            try:
                return handler(skill_category, query, skill)
            except Exception as e:
                return {
                    "skill": skill_category,
                    "success": False,
                    "output": f"Error en handler: {e}",
                    "raw": str(e),
                    "destructive": skill.get("destructive", False),
                    "error": "handler_error",
                }

    # Standard execution: run command, capture output
    command = skill["command"]
    try:
        result = subprocess.run(
            ["bash", "-c", command],
            capture_output=True, text=True,
            timeout=SKILL_TIMEOUT,
        )
        raw_output = result.stdout.strip()
        if result.returncode != 0 and not raw_output:
            raw_output = result.stderr.strip()

        # Apply parser if defined
        parser_name = skill.get("parser")
        if parser_name and parser_name in _SKILL_PARSERS:
            parsed = _SKILL_PARSERS[parser_name](raw_output)
        else:
            parsed = raw_output

        success = result.returncode == 0 or bool(raw_output)

        _log({
            "type": "skill_execution",
            "skill": skill_category,
            "success": success,
            "returncode": result.returncode,
            "output_len": len(raw_output),
        })

        return {
            "skill": skill_category,
            "success": success,
            "output": parsed,
            "raw": raw_output,
            "destructive": skill.get("destructive", False),
            "error": None if success else "command_failed",
        }

    except subprocess.TimeoutExpired:
        _log({"type": "skill_timeout", "skill": skill_category})
        return {
            "skill": skill_category,
            "success": False,
            "output": f"Timeout ejecutando {skill['description']}",
            "raw": "",
            "destructive": skill.get("destructive", False),
            "error": "timeout",
        }
    except Exception as e:
        return {
            "skill": skill_category,
            "success": False,
            "output": f"Error: {e}",
            "raw": str(e),
            "destructive": skill.get("destructive", False),
            "error": "execution_error",
        }


# ── Skill Parsers ───────────────────────────────────────────────


def _parse_weather(raw: str) -> str:
    """Parse Open-Meteo JSON into human-readable weather."""
    try:
        data = json.loads(raw)
        cw = data.get("current_weather", {})
        temp = cw.get("temperature", "?")
        wind = cw.get("windspeed", "?")
        code = cw.get("weathercode", -1)
        # WMO weather codes → description
        weather_codes = {
            0: "despejado", 1: "mayormente despejado",
            2: "parcialmente nublado", 3: "nublado",
            45: "niebla", 48: "niebla con escarcha",
            51: "llovizna leve", 53: "llovizna", 55: "llovizna intensa",
            61: "lluvia leve", 63: "lluvia", 65: "lluvia intensa",
            71: "nieve leve", 73: "nieve", 75: "nieve intensa",
            80: "chubascos leves", 81: "chubascos", 82: "chubascos intensos",
            95: "tormenta", 96: "tormenta con granizo",
        }
        desc = weather_codes.get(code, f"código {code}")
        return f"{temp}°C, {desc}, viento {wind} km/h"
    except (json.JSONDecodeError, KeyError):
        return raw if raw else "No se pudo obtener el clima"


def _parse_infra(raw: str) -> str:
    """Parse docker ps output into a readable status summary."""
    if not raw or not raw.strip():
        return "No se pudo obtener el estado de los containers."
    lines = [l.strip() for l in raw.strip().splitlines() if l.strip()]
    up = [l for l in lines if "Up" in l or "running" in l.lower()]
    down = [l for l in lines if "Exited" in l or "Restarting" in l]
    total = len(lines)
    parts = [f"Containers: {total} total, {len(up)} activos, {len(down)} caídos."]
    if down:
        parts.append("Caídos:")
        for d in down:
            name = d.split("\t")[0].split()[0] if "\t" in d else d.split()[0]
            parts.append(f"  - {name}")
    if not down:
        parts.append("Todo funcionando correctamente.")
    return "\n".join(parts)


def _parse_proposals(raw: str) -> str:
    """Parse proposals.json into a detailed summary."""
    try:
        proposals = json.loads(raw)
        if not proposals:
            return "No hay propuestas de mejora registradas."

        # Group by status
        by_status = {}
        for p in proposals:
            s = p.get("status", "unknown")
            by_status.setdefault(s, []).append(p)

        pending = by_status.get("proposed", [])
        reviewed = by_status.get("reviewed", [])
        accepted = by_status.get("accepted", [])
        rejected = by_status.get("rejected", [])

        lines = [f"Propuestas de mejora ({len(proposals)} total):"]

        # Status summary
        status_parts = []
        if pending:
            status_parts.append(f"{len(pending)} pendientes")
        if reviewed:
            status_parts.append(f"{len(reviewed)} en revisión")
        if accepted:
            status_parts.append(f"{len(accepted)} aceptadas")
        if rejected:
            status_parts.append(f"{len(rejected)} rechazadas")
        if status_parts:
            lines.append("  " + ", ".join(status_parts))
        lines.append("")

        # Show pending proposals with detail
        show = pending or reviewed or accepted
        if show:
            label = "Pendientes" if pending else "Recientes"
            display = (pending or reviewed or accepted)[:5]
            lines.append(f"{label}:")
            for p in display:
                area = p.get("area", p.get("type", "general"))
                desc = p.get("description", p.get("proposal", "sin descripción"))
                status = p.get("status", "?")
                ts = p.get("created_at", p.get("ts", p.get("timestamp", "")))
                if ts:
                    # Show just date portion
                    date_str = str(ts)[:10]
                else:
                    date_str = ""

                if len(desc) > 120:
                    desc = desc[:117] + "..."

                header = f"  • [{area}] {desc}"
                meta_parts = []
                if date_str:
                    meta_parts.append(date_str)
                if status != "proposed":
                    meta_parts.append(f"estado: {status}")
                if meta_parts:
                    header += f"\n    ({', '.join(meta_parts)})"
                lines.append(header)

            remaining = len(show) - 5
            if remaining > 0:
                lines.append(f"  ... y {remaining} más")
        else:
            lines.append("No hay propuestas pendientes de revisión.")

        return "\n".join(lines)
    except (json.JSONDecodeError, TypeError):
        return raw if raw else "Error leyendo propuestas"


# ── Skill Handlers (for parameterized/destructive skills) ───────


def _handle_smart_home(skill_cat: str, query: str, skill: dict) -> dict:
    """Handle smart home commands by extracting action from query."""
    query_lower = query.lower()

    # Determine action
    hue_key = os.environ.get("HUE_API_KEY", "")
    if not hue_key:
        # Try reading from known config location
        try:
            config_path = "/home/tenazas/.openclaw/openclaw.json"
            with open(config_path) as f:
                config = json.load(f)
            hue_key = config.get("hue", {}).get("apiKey", "")
        except Exception:
            pass

    if not hue_key:
        return {
            "skill": skill_cat, "success": False,
            "output": "No se encontró la API key del Hue Bridge",
            "raw": "", "destructive": True, "error": "no_hue_key",
        }

    base_url = f"http://192.168.2.11/api/{hue_key}"

    # Detect action from query — cinema has priority over on/off
    # to resolve overlap (e.g. "prendé modo cine" → cinema, not on)
    _cinema_re = (
        r"cine|cinema|pel[ií]cula|movie|netflix|hbo|disney|serie"
        r"|modo\s*cine|modo\s*pel[ií]cula"
    )
    _lights_only_re = r"lu[cz]|brillo|l[aá]mpara"

    if re.search(_cinema_re, query_lower):
        # Cinema wins unless query is ONLY about lights with no cinema intent
        # e.g. "prendé las luces" with "cine" somewhere → cinema
        action = "cinema"
    elif re.search(r"apag[aá]|off|todo.off|all.off", query_lower):
        action = "all-off"
    elif re.search(r"prend[eé]|encend|on|todo.on|all.on", query_lower):
        action = "all-on"
    elif re.search(r"relax", query_lower):
        action = "relax"
    elif re.search(r"trabaj|work", query_lower):
        action = "work"
    else:
        # Default: show light status
        try:
            result = subprocess.run(
                ["curl", "-sf", f"{base_url}/lights"],
                capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                lights = json.loads(result.stdout)
                lines = []
                for lid, ldata in sorted(lights.items()):
                    name = ldata.get("name", f"Light {lid}")
                    on = ldata.get("state", {}).get("on", False)
                    lines.append(f"  {name}: {'encendida' if on else 'apagada'}")
                return {
                    "skill": skill_cat, "success": True,
                    "output": "Estado de luces:\n" + "\n".join(lines),
                    "raw": result.stdout, "destructive": False, "error": None,
                }
        except Exception as e:
            return {
                "skill": skill_cat, "success": False,
                "output": f"Error consultando luces: {e}",
                "raw": str(e), "destructive": False, "error": "hue_error",
            }
        return {
            "skill": skill_cat, "success": False,
            "output": "No entendí qué hacer con las luces. "
                      "Podés decir: apagar, encender, cinema, relax, work.",
            "raw": "", "destructive": False, "error": "unknown_action",
        }

    # Execute scene
    scenes = {
        "all-off": {"on": False},
        "all-on": {"on": True, "bri": 254},
        "cinema": {"on": True, "bri": 50, "ct": 400},
        "relax": {"on": True, "bri": 140, "ct": 370},
        "work": {"on": True, "bri": 254, "ct": 250},
    }
    state = scenes[action]
    light_ids = ["1", "3", "4", "6", "11", "12", "13", "15"]
    errors = []
    for lid in light_ids:
        try:
            subprocess.run(
                ["curl", "-sf", "-X", "PUT",
                 "-d", json.dumps(state),
                 f"{base_url}/lights/{lid}/state"],
                capture_output=True, text=True, timeout=5,
            )
        except Exception as e:
            errors.append(f"Light {lid}: {e}")

    if errors:
        return {
            "skill": skill_cat, "success": True,
            "output": f"Luces: {action} (con {len(errors)} errores)",
            "raw": "\n".join(errors), "destructive": True, "error": None,
        }
    return {
        "skill": skill_cat, "success": True,
        "output": f"Luces: {action} aplicado",
        "raw": "", "destructive": True, "error": None,
    }


def _handle_tts(skill_cat: str, query: str, skill: dict) -> dict:
    """Extract text from query and send to TTS."""
    # Remove trigger words to get the text to speak
    text = re.sub(
        r"^(dec[ií]|habl[aá]|le[eé]|mand[aá]\s+audio)\s*:?\s*",
        "", query, flags=re.IGNORECASE,
    ).strip()
    if not text:
        return {
            "skill": skill_cat, "success": False,
            "output": "¿Qué querés que diga?",
            "raw": "", "destructive": False, "error": "no_text",
        }
    # Sanitize for shell (single quotes)
    safe_text = text.replace("'", "'\\''")
    cmd = f"bash /home/tenazas/.openclaw/workspace/skills/tts/tts.sh '{safe_text}'"
    try:
        result = subprocess.run(
            ["bash", "-c", cmd],
            capture_output=True, text=True, timeout=SKILL_TIMEOUT,
        )
        if result.returncode == 0:
            return {
                "skill": skill_cat, "success": True,
                "output": f"Audio enviado: \"{text[:60]}\"",
                "raw": result.stdout, "destructive": True, "error": None,
            }
        return {
            "skill": skill_cat, "success": False,
            "output": f"Error TTS: {result.stderr[:100]}",
            "raw": result.stderr, "destructive": True, "error": "tts_error",
        }
    except subprocess.TimeoutExpired:
        return {
            "skill": skill_cat, "success": False,
            "output": "Timeout en TTS",
            "raw": "", "destructive": True, "error": "timeout",
        }


def _handle_morning_greeting(skill_cat: str, query: str, skill: dict) -> dict:
    """Aggregate morning greeting from multiple sources."""
    parts = []
    now = datetime.now()
    parts.append(f"Buenos días Sebas — {now.strftime('%A %d/%m')}")

    # Calendar
    try:
        r = subprocess.run(
            ["python3", "/home/tenazas/.openclaw/workspace/skills/"
             "calendar-agenda/gcal.py", "1"],
            capture_output=True, text=True, timeout=15,
        )
        if r.returncode == 0 and r.stdout.strip():
            parts.append(f"\nAgenda:\n{r.stdout.strip()}")
        else:
            parts.append("\nAgenda: sin eventos")
    except Exception:
        parts.append("\nAgenda: no disponible")

    # Weather
    try:
        w = subprocess.run(
            ["curl", "-sf",
             "https://api.open-meteo.com/v1/forecast"
             "?latitude=-34.61&longitude=-58.38"
             "&current_weather=true&timezone=America/Buenos_Aires"],
            capture_output=True, text=True, timeout=10,
        )
        if w.returncode == 0:
            parts.append(f"\nClima: {_parse_weather(w.stdout)}")
    except Exception:
        pass

    # Containers
    try:
        d = subprocess.run(
            ["docker", "ps", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        if d.returncode == 0:
            lines = d.stdout.strip().split("\n")
            parts.append(f"\nContainers: {len(lines)} corriendo")
    except Exception:
        pass

    return {
        "skill": skill_cat, "success": True,
        "output": "\n".join(parts),
        "raw": "", "destructive": False, "error": None,
    }


def _handle_email(skill_cat: str, query: str, skill: dict) -> dict:
    """Handle email: list recent emails via himalaya CLI."""
    try:
        result = subprocess.run(
            ["himalaya", "envelope", "list", "--folder", "INBOX",
             "--page-size", "10"],
            capture_output=True, text=True, timeout=15,
        )
        if result.returncode != 0:
            stderr = result.stderr.strip()
            return {
                "skill": skill_cat, "success": False,
                "output": f"Error ejecutando himalaya: {stderr or 'exit code ' + str(result.returncode)}",
                "raw": stderr, "destructive": False,
                "error": "command_failed",
            }
        raw = result.stdout.strip()
        if not raw:
            return {
                "skill": skill_cat, "success": True,
                "output": "No hay emails en la bandeja de entrada.",
                "raw": "", "destructive": False, "error": None,
            }
        return {
            "skill": skill_cat, "success": True,
            "output": f"Emails recientes:\n{raw}",
            "raw": raw, "destructive": False, "error": None,
        }
    except FileNotFoundError:
        return {
            "skill": skill_cat, "success": False,
            "output": "himalaya no está instalado en el container.",
            "raw": "", "destructive": False, "error": "not_installed",
        }
    except subprocess.TimeoutExpired:
        return {
            "skill": skill_cat, "success": False,
            "output": "Timeout conectando al email (himalaya).",
            "raw": "", "destructive": False, "error": "timeout",
        }


def _handle_reminders(skill_cat: str, query: str, skill: dict) -> dict:
    """Handle reminders: list or create."""
    query_lower = query.lower()
    reminders_file = "/home/tenazas/.openclaw/workspace/memory/reminders.json"

    # Check if creating a new reminder
    is_create = bool(re.search(
        r"(record|acord|avis)[aá]|pon[eé]\s+.*recordatorio|"
        r"cre[aá]\s+.*recordatorio|agreg[aá]",
        query_lower,
    ))

    if not is_create:
        # List existing reminders
        try:
            result = subprocess.run(
                ["cat", reminders_file],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0 or not result.stdout.strip():
                return {
                    "skill": skill_cat, "success": True,
                    "output": "No tenés recordatorios activos por ahora.",
                    "raw": "", "destructive": False, "error": None,
                }
            reminders = json.loads(result.stdout)
            if not reminders:
                return {
                    "skill": skill_cat, "success": True,
                    "output": "No tenés recordatorios activos por ahora.",
                    "raw": "", "destructive": False, "error": None,
                }
            lines = ["Recordatorios activos:"]
            for r in reminders[:10]:
                text = r.get("text", r.get("message", "?"))
                when = r.get("when", r.get("time", ""))
                lines.append(f"  • {text}" + (f" ({when})" if when else ""))
            return {
                "skill": skill_cat, "success": True,
                "output": "\n".join(lines),
                "raw": result.stdout, "destructive": False, "error": None,
            }
        except Exception as e:
            return {
                "skill": skill_cat, "success": False,
                "output": f"Error leyendo recordatorios: {e}",
                "raw": str(e), "destructive": False, "error": "read_error",
            }

    # Creating a reminder — return the query for the LLM to process
    # (reminder creation requires NLP parsing, delegate to LLM)
    return {
        "skill": skill_cat, "success": True,
        "output": f"__DELEGATE_TO_LLM__:Crear recordatorio desde: {query}",
        "raw": query, "destructive": True, "error": None,
    }


def _handle_model_trainer(skill_cat: str, query: str, skill: dict) -> dict:
    """Handle model-trainer queries: status, dataset, eval, or full pipeline."""
    query_lower = query.lower()

    if any(k in query_lower for k in ("estado", "último", "ultimo", "status", "cuándo", "cuando")):
        action = "status"
    elif "dataset" in query_lower:
        action = "dataset_only"
    elif "eval" in query_lower:
        action = "eval_only"
    else:
        action = "full_pipeline"

    script = "/home/tenazas/.openclaw/workspace/skills/model-trainer/trainer_orchestrator.py"

    if action == "status":
        try:
            result = subprocess.run(
                ["python3", script, "--action", "status"],
                capture_output=True, text=True, timeout=15,
            )
            output = result.stdout.strip() or "Sin trainings registrados"
        except Exception as e:
            output = f"Error consultando status: {e}"
        return {
            "skill": skill_cat, "success": True,
            "output": output, "raw": "", "destructive": False, "error": None,
        }

    # For full_pipeline/dataset/eval: launch in background
    try:
        subprocess.Popen(["python3", script, "--action", action])
        msg = {
            "full_pipeline": "Pipeline de training iniciado. Te notifico cuando termine cada etapa (~60 min).",
            "dataset_only": "Generando dataset en background.",
            "eval_only": "Evaluando modelo en background.",
        }.get(action, f"Acción {action} iniciada.")
    except Exception as e:
        msg = f"Error lanzando {action}: {e}"

    return {
        "skill": skill_cat, "success": True,
        "output": msg, "raw": "", "destructive": False, "error": None,
    }


def _handle_expense(skill_category, query, skill):
    """Handle expense/finance queries using expenses.py and finance.py."""
    try:
        import importlib.util
        # Try daily summary first
        spec = importlib.util.spec_from_file_location(
            "expenses", "/home/tenazas/runtime/expenses.py")
        expenses_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(expenses_mod)

        # Check if query asks for weekly
        is_weekly = any(w in query.lower() for w in ["semana", "semanal", "week"])

        if is_weekly:
            data, text = expenses_mod.weekly_summary()
        else:
            data, text = expenses_mod.daily_summary()

        if data and data.get("count", 0) > 0:
            output = text
        else:
            # Try monthly summary from finance.py
            spec2 = importlib.util.spec_from_file_location(
                "finance", "/home/tenazas/runtime/finance.py")
            finance_mod = importlib.util.module_from_spec(spec2)
            spec2.loader.exec_module(finance_mod)
            s = finance_mod.summary("month")
            if s and s.get("transaction_count", 0) > 0:
                output = finance_mod.format_summary(s)
            else:
                output = "No hay gastos registrados todavía."
    except Exception as e:
        output = f"Error al consultar gastos: {e}"

    return {
        "skill": skill_category, "success": True,
        "output": output, "raw": "", "destructive": False, "error": None,
    }


# Registry of handlers and parsers
_SKILL_HANDLERS = {
    "_handle_smart_home": _handle_smart_home,
    "_handle_tts": _handle_tts,
    "_handle_morning_greeting": _handle_morning_greeting,
    "_handle_reminders": _handle_reminders,
    "_handle_email": _handle_email,
    "_handle_model_trainer": _handle_model_trainer,
    "_handle_expense": _handle_expense,
}

_SKILL_PARSERS = {
    "_parse_weather": _parse_weather,
    "_parse_proposals": _parse_proposals,
    "_parse_infra": _parse_infra,
}


# ── Route + Execute (main entry point for skill execution) ──────


def route_and_execute(
    query: str,
    context: dict | None = None,
    available_backends: dict | None = None,
) -> dict:
    """Classify, and if it's a skill, execute it directly.

    For non-skill categories, returns the normal route_query() result
    with execute=False.

    For skill categories, executes the skill and returns the result
    with execute=True and the skill output.

    Returns:
        route_query() result + {
            "executed": bool,
            "skill_result": {...} | None,
        }
    """
    classification = classify_query(query, context)

    if classification.startswith("skill:"):
        # Execute skill directly
        skill_result = execute_skill(classification, query)

        # Still route for logging and model selection
        route = route_query(query, context, available_backends)
        route["classification"] = classification
        route["executed"] = True
        route["skill_result"] = skill_result

        _log({
            "type": "skill_routed",
            "skill": classification,
            "success": skill_result["success"],
            "executed": True,
        })

        return route

    # ── Multi-agent reasoning for complex queries ─────────────────
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
                    "skill": "multi_agent",
                    "success": True,
                    "output": ma_result["conclusion"],
                    "raw": json.dumps({
                        "analysis": ma_result.get("analysis", ""),
                        "verification": ma_result.get("verification", ""),
                        "agents_used": ma_result.get("agents_used", []),
                    }),
                    "destructive": False,
                    "error": None,
                }
                _log({
                    "type": "multi_agent_reasoning",
                    "agents": ma_result.get("agents_used", []),
                    "success": True,
                    "query": query[:100],
                })
                return route
        except Exception:
            pass  # fall through to normal routing
    # ── end multi-agent ─────────────────────────────────────────

    # Non-skill: normal routing, no execution
    route = route_query(query, context, available_backends)
    route["executed"] = False
    route["skill_result"] = None
    return route


# ── Multitool Execution ──────────────────────────────────────────
# Optional path: when the finetuned multitool model is available in Ollama,
# queries that request multiple actions can be routed through it.
# Falls back to route_and_execute() on any failure.

MULTITOOL_MODEL = "tenazas-multitool"
MULTITOOL_OLLAMA_URL = os.environ.get("OLLAMA_GPU_URL", "http://192.168.2.6:11434")
MULTITOOL_SYSTEM_PROMPT = (
    "You are a deterministic multi-tool caller. "
    "Respond only with valid XML. Allowed format:\n"
    "<tools>\n  <tool>TOOL_NAME</tool>\n</tools>\n"
    "Allowed tools: weather, email, calendar, infra-monitor, "
    "network_scan, arr-monitor, self-evolve, reminders, "
    "smart-home-on, smart-home-off, smart-home-cinema, "
    "smart-home-relax, smart-home-work, tts.\n"
    "No extra text. If no tools apply, respond with <tools></tools>."
)


def _call_multitool_model(query: str) -> str | None:
    """Call the finetuned multitool model. Returns raw text or None."""
    import urllib.request
    payload = json.dumps({
        "model": MULTITOOL_MODEL,
        "prompt": query,
        "system": MULTITOOL_SYSTEM_PROMPT,
        "stream": False,
        "options": {"temperature": 0, "top_p": 1, "num_predict": 128},
    }).encode()
    req = urllib.request.Request(
        f"{MULTITOOL_OLLAMA_URL}/api/generate",
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read())
            return data.get("response", "").strip()
    except Exception as e:
        _log({"type": "multitool_model_error", "error": str(e)})
        return None


def route_and_execute_multitool(
    query: str,
    context: dict | None = None,
) -> dict:
    """Execute tools from a single query via hybrid router (rules + model).

    Flow:
      1. hybrid_router.route() applies hard rules first (instant, no LLM)
      2. If no rule matches, calls tenazas-multitool model
      3. Returns parsed, validated tool list

    On any failure, falls back to the normal route_and_execute() path.

    Returns:
        {
            "executed": bool,
            "multitool": True,
            "tools_used": list[str],
            "results": list[dict],
            "aggregated_text": str,
            "fallback_used": bool,
        }
        OR the normal route_and_execute() result on fallback.
    """
    try:
        from runtime.hybrid_router import route as hybrid_route
    except ImportError:
        from hybrid_router import route as hybrid_route

    try:
        from runtime.multitool_parser import (
            get_skill_category, get_tool_action,
        )
    except ImportError:
        from multitool_parser import (
            get_skill_category, get_tool_action,
        )

    t0 = time.monotonic()

    _routing_metrics["multitool_requests_total"] += 1

    # Hybrid router: hard rules first, model only for ambiguous
    try:
        tools = hybrid_route(query)
    except Exception as e:
        _routing_metrics["multitool_fallback_total"] += 1
        _log({"type": "hybrid_router_error", "error": str(e),
              "query": query[:100]})
        return route_and_execute(query, context)

    if not tools:
        # Model said no tools needed — fallback to normal
        _log({"type": "multitool_empty", "query": query[:100]})
        return route_and_execute(query, context)

    # Execute each tool via existing execute_skill()
    results = []
    output_parts = []
    for tool_name in tools:
        skill_cat = get_skill_category(tool_name)
        if not skill_cat:
            results.append({"tool": tool_name, "success": False,
                            "error": "no_skill_mapping"})
            continue

        # For smart-home tools, inject the action into the query
        action = get_tool_action(tool_name)
        exec_query = query
        if action:
            # Build a synthetic query the handler understands
            action_queries = {
                "on": "prendé las luces",
                "off": "apagá las luces",
                "cinema": "modo cinema",
                "relax": "modo relax",
                "work": "modo trabajo",
            }
            exec_query = action_queries.get(action, query)

        skill_result = execute_skill(skill_cat, exec_query)
        results.append({
            "tool": tool_name,
            "skill": skill_cat,
            "success": skill_result["success"],
            "output": skill_result["output"],
            "error": skill_result.get("error"),
        })
        _routing_metrics["multitool_tools_executed_total"] += 1

        if skill_result["success"] and skill_result["output"]:
            output_parts.append(skill_result["output"])

    duration = time.monotonic() - t0
    aggregated = "\n\n".join(output_parts) if output_parts else "Sin resultados"

    _log({
        "type": "multitool_execution",
        "query": query[:100],
        "raw_model": raw_response[:200],
        "tools": tools,
        "results_ok": sum(1 for r in results if r.get("success")),
        "results_fail": sum(1 for r in results if not r.get("success")),
        "duration_s": round(duration, 2),
    })

    return {
        "executed": True,
        "multitool": True,
        "tools_used": tools,
        "results": results,
        "aggregated_text": aggregated,
        "fallback_used": False,
        "parse_result": parse_result,
        "classification": "multitool",
        "skill_result": None,
    }


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


def _is_openrouter_available() -> bool:
    """Check if OpenRouter API key is configured."""
    import os
    return bool(os.environ.get("OPENROUTER_API_KEY", ""))


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
