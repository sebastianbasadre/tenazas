"""
Classifier -- Query classification rules and classify_query() for Tenazas orchestrator.

Extracted from orchestrator.py for independent importability.
"""

import json
import re
from pathlib import Path

try:
    from runtime_utils import now
except ImportError:
    from workspace.runtime.runtime_utils import now

# ── Config ───────────────────────────────────────────────────────

_LOG_PATH = Path(__file__).parent / "logs" / "orchestrator.log"


def _log(entry: dict):
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(_LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


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
        "skill:smoking-tracker",
        [r"estoy\s+fumando", r"me\s+fumo\s+uno", r"prend[ií]\s+uno",
         r"fum[eé]\s+uno", r"me\s+prendo\s+uno", r"\bpucho\b",
         r"cu[aá]nto\s+fum[eé]", r"estad[ií]sticas?\s+tabaco",
         r"mis\s+cigarrillos", r"cu[aá]ntos\s+cigarrillos"],
        {"cigarrillo", "cigarrillos", "pucho", "puchos", "fumando",
         "tabaco", "fumar", "fumé", "cigarro"},
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
         "reuniones", "horario", "cita"},
    ),
    # Smart home — all IoT devices (lights, TV, AVR, vacuum, speakers, etc.)
    # The devices/ dispatcher detects which device from the query text.
    (
        "skill:smart-home",
        [# Lights
         r"(prend|encend|apag|cambi)\w*\s*(las?\s+)?(lu[cz]|l[aá]mpara|alumbrad)",
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
         r"(quier|vamos?\s+a)\s+(ver\s+)?(una?\s+)?(peli|pel[ií]cula|movie|serie)",
         # TV / Samsung
         r"(prend|encend|apag)\w*\s*(el|la|los|las)?\s*(tv|tele(visor|visi[oó]n)?|pantalla|samsung|monitor)",
         r"(estado|status)\s+(de\s+)?(el|la)?\s*(tv|tele(visor|visi[oó]n)?|samsung)",
         # Marantz / AVR
         r"(prend|encend|apag)\w*\s*(el\s+)?(marantz|receiver|amplificador|avr)",
         r"(subi|baj)\w*\s*(el\s+)?vol",
         r"(estado|status)\s+(del?\s+)?(marantz|receiver|amplificador)",
         r"vol(umen)?\s*(a|en|al?)?\s*\d+",
         r"(pon[eé]|cambi[aá]|pas[aá])\w*\s*(a\s+)?hdmi\s*\d",
         r"\bmute\b|\bsilenci",
         # Sync Box
         r"sync\s*box",
         r"hue\s+sync",
         r"sincroniz\w+\s+lu[cz]",
         # SHIELD
         r"(prend|encend|apag)\w*\s*(el\s+)?(shield|nvidia)",
         r"(estado|status)\s+(del?\s+)?shield",
         r"(abr[ií]|lanz[aá])\s*(netflix|youtube|disney|prime|hbo|plex|spotify|kodi)",
         # Vacuum
         r"(prend|encend|activ)\w*\s*(la\s+)?aspiradora",
         r"(pasar|poner)\s*(la\s+)?aspiradora",
         r"aspir|barrer|limpiar\s*(el\s+)?piso",
         r"(estado|status)\s+(de\s+)?(la\s+)?aspiradora",
         r"(volver|dock|base)\s*(la\s+)?aspiradora",
         # Google Cast
         r"(volumen|play|pausa|pause|stop)\s*(del?\s+)?(parlante|speaker|nest|google\s*home)",
         r"(prend|encend|apag)\w*\s*(el\s+)?(parlante|speaker|nest|google)",
         # Cameras
         r"(estado|status)\s+(de\s+)?(las\s+)?c[aá]maras?",
         r"c[aá]maras?\s+(de\s+)?(seguridad|vigilancia)",
         # Doorbell
         r"timbre|doorbell"],
        # Keywords: device names + light-specific + entertainment.
        # Generic verbs like "encender" now safe because patterns are device-specific.
        {"cinema", "relax", "escena",
         "cine", "película", "netflix", "hbo", "disney", "serie",
         "luces", "luz", "brillo", "lámpara", "alumbrado",
         "marantz", "receiver", "amplificador", "volumen",
         "tv", "televisor", "samsung", "pantalla",
         "shield", "nvidia",
         "aspiradora", "vacuum", "barrer",
         "parlante", "speaker", "chromecast",
         "cámara", "camaras", "doorbell", "timbre",
         "sync"},
    ),
    # TTS — only trigger when user wants Tenazas to GENERATE audio,
    # not when mentioning "audio" in conversation (e.g. "te mando un audio")
    (
        "skill:tts",
        [r"(dec[ií]|habl[aá]|le[eé])\s+en\s+voz", r"dec[ií]me\s+",
         r"hablame\s+", r"(mandam|enviame|mand[aá]me)\s+(un\s+)?audio",
         r"text.to.speech", r"voz\s+alta",
         r"(que\s+me\s+)?habl[eé]s", r"(respond|contest)[aáeé]\w*\s+(en|por)\s+voz",
         r"(salud|respond)[aáeé]\w*\s+(en|por)\s+voz"],
        {"decí", "hablá", "hables", "voz", "deci", "tts",
         "hablame", "decime"},
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
    # Security monitor (read-only homelab security posture)
    (
        "skill:security-monitor",
        [r"(seguridad|security)\s+(del?\s+)?(homelab|nas|sistema|infra)",
         r"(revis[aá]|cheque[aá]|audit[aá])\w*\s+(la\s+)?seguridad",
         r"(puertos?|servicios?)\s+expuestos?",
         r"superficie\s+de\s+ataque",
         r"paneles?\s+admin",
         r"(grafana|portainer|n8n).{0,30}(seguridad|expuesto|abierto)",
         r"(firewall|credenciales?|ssh).{0,30}(seguridad|expuesto|abierto|riesgo)"],
        {"seguridad", "security", "puertos", "expuestos", "firewall",
         "credenciales", "ssh", "portainer", "grafana", "n8n"},
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
        [r"(c[oó]mo|qu[eé]|cu[aá]l|d[oó]nde|por\s*qu[eé])\s+(es|son|fue|funciona|significa|hace|hago|configur|se\s+usa)",
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


# ── Classification ───────────────────────────────────────────────


def classify_query(query: str, context: dict | None = None) -> str:
    """Classify a query into a category using keyword + regex rules.

    Fast, no LLM needed. Returns category string.
    """
    # Normalize: collapse spaces, fix common typos
    query_lower = re.sub(r"\s+", " ", query.lower()).strip()
    _TYPO_MAP = {
        "apga": "apaga", "prende": "prendé", "encende": "encendé",
        "infr a": "infra", "clim a": "clima", "contaner": "container",
        "containr": "container", "tempratura": "temperatura",
        "descarag": "descarga", "cigarrilo": "cigarrillo",
    }
    for typo, fix in _TYPO_MAP.items():
        if typo in query_lower:
            query_lower = query_lower.replace(typo, fix)

    # ── Router V3: finetuned_router + intent_normalizer ──────────
    # Feature flag: "use_finetuned_router": true in openclaw.json
    # If active and router gives >= medium confidence, use that result.
    # If it fails or gives low confidence, fall through to deterministic scoring.
    try:
        _tcfg_path = Path("/home/tenazas/.openclaw/tenazas.json")
        if _tcfg_path.exists():
            _tcfg = json.loads(_tcfg_path.read_text())
        else:
            _tcfg = {}
        if _tcfg.get("use_finetuned_router"):
            from finetuned_router import route as _ft_route
            from intent_normalizer import normalize_or_chat as _ft_normalize
            _router_out = _ft_route(query)
            _call = _ft_normalize(_router_out, query)
            if (_call and _call.confidence in ("high", "medium")
                    and _call.valid and _call.skill != "chat"):
                _log({"type": "router_v3", "intent": _call.intent,
                      "skill": _call.skill, "confidence": _call.confidence,
                      "source": _call.source, "query": query[:100]})
                return _call.skill
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

