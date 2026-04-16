#!/usr/bin/env python3
"""
Telegram Deterministic Router — Intercepts skill queries before the LLM.

Runs as a standalone Telegram polling bot. For messages that match a known
skill (email, clima, luces, infra, etc.), executes the skill directly via
orchestrator.route_and_execute() and responds with real data. For everything
else, forwards to Ollama with SOUL.md context so the LLM handles it.

This eliminates hallucinated responses for deterministic skills because the
LLM never sees those queries.

Usage:
    python3 telegram_router.py           # normal mode
    python3 telegram_router.py --test    # run classification tests
    python3 telegram_router.py --dry-run # classify stdin messages without sending
"""

import json
import logging
import os
import re
import sys
import time
from collections import deque
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

# ── Paths ──────────────────────────────────────────────────────────

RUNTIME_DIR = Path(__file__).parent
LOG_DIR = RUNTIME_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)

CONFIG_FILE = Path("/home/tenazas/.openclaw/openclaw.json")
SOUL_FILE = Path("/home/tenazas/.openclaw/workspace/SOUL.md")
LOG_FILE = LOG_DIR / "telegram_router.log"

# ── Logging ────────────────────────────────────────────────────────

log = logging.getLogger("telegram_router")
log.setLevel(logging.INFO)
_handler = RotatingFileHandler(LOG_FILE, maxBytes=2_000_000, backupCount=3)
_handler.setFormatter(logging.Formatter("%(message)s"))
log.addHandler(_handler)


def _log(entry: dict):
    entry["ts"] = datetime.now().astimezone().isoformat()
    log.info(json.dumps(entry, ensure_ascii=False))


# ── Config ─────────────────────────────────────────────────────────

def _load_config() -> dict:
    with open(CONFIG_FILE) as f:
        return json.load(f)


def _get_bot_token() -> str:
    return _load_config()["channels"]["telegram"]["botToken"]


def _get_allowed_chat_ids() -> set:
    cfg = _load_config()
    return set(str(x) for x in cfg["channels"]["telegram"].get("allowFrom", []))


CHAT_ID = "1565794551"
TELEGRAM_API = "https://api.telegram.org/bot{token}/{method}"
POLL_TIMEOUT = 30  # long-polling timeout in seconds

# ── Conversation History ──────────────────────────────────────────
# Per-chat sliding window so the LLM has context of recent exchanges.
# Also allows skill outputs to be referenced in follow-up messages.

HISTORY_MAX_TURNS = 10  # max user+assistant pairs kept
HISTORY_MAX_AGE = 600   # seconds — discard entries older than 10 min

# {chat_id: deque([ {"role": "user"|"assistant", "content": str, "ts": float}, ... ])}
_chat_history: dict[str, deque] = {}

# ── Self-evolve conversational continuity ─────────────────────────
# Tracks last skill per chat_id so vague follow-ups can be routed back.
# Only used for self-evolve. TTL: 5 minutes.
_SELF_EVOLVE_FOLLOWUP_TTL = 300  # 5 min
# {chat_id: {"skill": str, "ts": float}}
_last_skill: dict[str, dict] = {}

# Patterns that indicate a vague follow-up (not a new topic)
_FOLLOWUP_PATTERNS = re.compile(
    r"^(y\??|qu[eé]\s+m[aá]s\??|cont[aá]me\s+m[aá]s|detalles?|sobre\s+eso|"
    r"y\s+sobre\s+eso\??|ampli[aá]|explic[aá]|m[aá]s\s+info|"
    r"m[aá]s\s+detalles?|y\s+eso\??)$",
    re.IGNORECASE,
)


def _record_last_skill(chat_id: str, skill: str):
    """Record the last intercepted skill for a chat."""
    _last_skill[chat_id] = {"skill": skill, "ts": time.time()}


def _check_self_evolve_followup(chat_id: str, text: str) -> bool:
    """Check if this is a vague follow-up to a self-evolve interaction.

    Returns True only if:
    1. The last skill for this chat was skill:self-evolve
    2. It was within the TTL window
    3. The current message matches a vague follow-up pattern
    """
    last = _last_skill.get(chat_id)
    if not last:
        return False
    if last["skill"] != "skill:self-evolve":
        return False
    if time.time() - last["ts"] > _SELF_EVOLVE_FOLLOWUP_TTL:
        return False
    return bool(_FOLLOWUP_PATTERNS.match(text.strip()))


def _get_history(chat_id: str) -> deque:
    if chat_id not in _chat_history:
        _chat_history[chat_id] = deque(maxlen=HISTORY_MAX_TURNS * 2)
    return _chat_history[chat_id]


def _append_history(chat_id: str, role: str, content: str):
    """Append a message to the chat history, trimming old entries."""
    hist = _get_history(chat_id)
    hist.append({"role": role, "content": content, "ts": time.time()})


def _build_ollama_messages(chat_id: str, system: str) -> list[dict]:
    """Build messages array for Ollama /api/chat from history.

    Drops entries older than HISTORY_MAX_AGE and caps assistant content
    to 200 chars to prevent raw skill data from polluting chat context.
    """
    now = time.time()
    msgs = [{"role": "system", "content": system}]
    for entry in _get_history(chat_id):
        if now - entry["ts"] > HISTORY_MAX_AGE:
            continue
        content = entry["content"]
        # Cap assistant messages aggressively — raw skill output (tables,
        # JSON, long lists) confuses the model into mixing topics.
        if entry["role"] == "assistant" and len(content) > 200:
            content = content[:197] + "..."
        msgs.append({"role": entry["role"], "content": content})
    return msgs


# ── Telegram API helpers ───────────────────────────────────────────

def tg_request(token: str, method: str, data: dict | None = None, timeout: int = 35):
    """Call Telegram Bot API. Returns parsed JSON or None on error."""
    url = TELEGRAM_API.format(token=token, method=method)
    if data:
        payload = json.dumps(data).encode()
        req = Request(url, data=payload,
                      headers={"Content-Type": "application/json"})
    else:
        req = Request(url)
    try:
        resp = urlopen(req, timeout=timeout)
        return json.loads(resp.read())
    except (HTTPError, URLError, OSError) as e:
        _log({"action": "tg_api_error", "method": method, "error": str(e)})
        return None


def send_message(token: str, chat_id: str, text: str):
    """Send a Telegram message. Truncates to 4096 chars.

    Tries Markdown first; falls back to plain text if Telegram rejects it
    (common with special chars in skill output like email tables).
    """
    if len(text) > 4096:
        text = text[:4090] + "\n..."
    result = tg_request(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
        "parse_mode": "Markdown",
    })
    if result and result.get("ok"):
        return result
    # Markdown failed — retry without parse_mode
    return tg_request(token, "sendMessage", {
        "chat_id": chat_id,
        "text": text,
    })


def send_typing(token: str, chat_id: str):
    """Send typing indicator."""
    tg_request(token, "sendChatAction", {
        "chat_id": chat_id,
        "action": "typing",
    }, timeout=5)


# ── Deterministic Classification ───────────────────────────────────
#
# Uses orchestrator.classify_query() which is already tested and proven.
# We import it at runtime to avoid import errors during testing.

# Skills that should be intercepted deterministically
INTERCEPTED_SKILLS = {
    "skill:email",
    "skill:weather",
    "skill:smart-home",
    "skill:network-scanner",
    "skill:arr-monitor",
    "skill:infra-monitor",
    "skill:calendar",
    "skill:calendar-agenda",
    "skill:morning-greeting",
    "skill:self-evolve",
    "skill:reminders",
    "skill:smoking-tracker",
    "skill:tts",
    "iol_broker",
    "skill:model-trainer",
    "skill:expense",
}

# Additional keyword patterns for email that the orchestrator might miss.
# These serve as a safety net — if orchestrator says "chat" but we detect
# email keywords, we override to skill:email.
EMAIL_OVERRIDE_PATTERNS = [
    r"\b(emails?|e-?mails?|correos?|mails?|inbox)\b",
    r"\b(leeme|mostrame|revisar?|cheque[aá]r?|abr[ií])\b.{0,15}\b(correo|mail|email|inbox)",
    r"\b(cu[aá]ntos|hay)\b.{0,10}\b(correo|mail|email)",
    r"\bhay\s+algo\s+de\b",  # "hay algo de telus?"
]
_email_override_re = [re.compile(p, re.IGNORECASE) for p in EMAIL_OVERRIDE_PATTERNS]


def classify_intent(text: str) -> str:
    """Classify a message using Gemma router → orchestrator regex fallback.

    Returns a category string. If the category is in INTERCEPTED_SKILLS,
    the message should be handled without the LLM.
    """
    sys.path.insert(0, str(RUNTIME_DIR)) if str(RUNTIME_DIR) not in sys.path else None

    # Tier 1: Use finetuned_router (Gemma 12B → finetuned v8 → keyword)
    try:
        from finetuned_router import route as _ft_route
        result = _ft_route(text)
        tool = result.get("tool")
        tier = result.get("_parsed_by", "?")
        if tool and tool != "chat":
            # Map tool name to skill category
            _TOOL_TO_SKILL = {
                "smart-home-on": "skill:smart-home",
                "smart-home-off": "skill:smart-home",
                "smart-home-cinema": "skill:smart-home",
                "weather": "skill:weather",
                "email": "skill:email",
                "calendar": "skill:calendar-agenda",
                "arr-monitor": "skill:arr-monitor",
                "infra-monitor": "skill:infra-monitor",
                "self-evolve": "skill:self-evolve",
                "tts": "skill:tts",
                "reminders": "skill:reminders",
                "smoking-tracker": "skill:smoking-tracker",
            }
            category = _TOOL_TO_SKILL.get(tool, f"skill:{tool}")
            _log({"action": "classified", "category": category,
                  "text": text[:80], "tier": tier})
            return category
    except Exception as e:
        _log({"action": "router_classify_error", "error": str(e)})

    # Tier 2: Orchestrator regex fallback
    try:
        from orchestrator import classify_query
        category = classify_query(text)
    except Exception as e:
        _log({"action": "classify_error", "error": str(e), "text": text[:80]})
        category = "chat"
        return category

    _log({"action": "classified", "category": category, "text": text[:80],
          "tier": "orchestrator"})

    # Safety net: if orchestrator missed email, check our override patterns
    if category == "chat":
        text_lower = text.lower()
        for pat in _email_override_re:
            if pat.search(text_lower):
                category = "skill:email"
                _log({"action": "email_override", "original": "chat",
                      "text": text[:80]})
                break

    return category


def should_intercept(text: str) -> tuple[bool, str]:
    """Returns (should_intercept, category)."""
    category = classify_intent(text)
    return category in INTERCEPTED_SKILLS, category


# ── Skill Execution ────────────────────────────────────────────────

def _execute_iol(text: str) -> str:
    """Execute IOL broker query via iol_handler_cli."""
    try:
        import subprocess
        result = subprocess.run(
            ["python3", "/home/tenazas/.openclaw/workspace/skills/iol_broker/iol_handler_cli.py", text],
            capture_output=True, text=True, timeout=30,
        )
        output = result.stdout.strip()
        if result.returncode == 0 and output:
            return output
        return result.stderr.strip() or "No pude obtener datos de IOL."
    except subprocess.TimeoutExpired:
        return "Timeout conectando a IOL."
    except Exception as e:
        return f"Error IOL: {e}"


def execute_intercepted_skill(text: str, category: str = None) -> tuple[str, str]:
    """Execute a skill via orchestrator and return (formatted_output, raw_output).

    If category is provided, skips re-classification (avoids double-classify bug).
    Returns tuple: (display_text, raw_data) so callers can choose composer vs humanize.
    """
    if category is None:
        category = classify_intent(text)

    # IOL broker has its own handler (not in orchestrator SKILL_MAP)
    if category == "iol_broker":
        _log({"action": "iol_execute", "text": text[:80]})
        result = _execute_iol(text)
        return result, result

    sys.path.insert(0, str(RUNTIME_DIR)) if str(RUNTIME_DIR) not in sys.path else None
    from orchestrator import execute_skill

    try:
        skill_result = execute_skill(category, text)
    except Exception as e:
        _log({"action": "execute_error", "error": str(e), "text": text[:80]})
        msg = f"Error ejecutando skill: {e}"
        return msg, ""

    if skill_result:
        raw = skill_result.get("raw", "")
        if skill_result["success"]:
            output = skill_result["output"] or ""
            return _format_skill_output(output, category), raw
        else:
            error = skill_result.get("error", "unknown")
            output = skill_result.get("output", "")
            msg = f"No pude ejecutar: {output or error}"
            return msg, raw

    return f"Skill no ejecutado (clasificación: {category})", ""


def _format_skill_output(output: str, classification: str) -> str:
    """Clean and format skill output for Telegram."""
    if not output or not output.strip():
        if "email" in classification:
            return "No hay emails en la bandeja."
        return "Sin datos."

    # For email: parse himalaya table into readable list
    if "email" in classification:
        return _format_email_output(output)

    return output.strip()


def _format_email_output(output: str) -> str:
    """Parse himalaya pipe-delimited table into a readable Telegram format."""
    lines = output.strip().split("\n")

    # Find data rows (skip header, separator, empty)
    rows = []
    for line in lines:
        if "|" not in line:
            continue
        parts = [p.strip() for p in line.split("|")]
        # Filter out header/separator rows
        if not parts or all(c in "-| " for c in line):
            continue
        # parts[0] is empty (before first |), parts[-1] is empty (after last |)
        cells = [p for p in parts if p]
        if not cells or cells[0] in ("ID", "id"):
            continue
        rows.append(cells)

    if not rows:
        return output.strip()

    # Format each email as a compact block
    formatted = []
    for cells in rows:
        # Columns: ID, FLAGS, SUBJECT, FROM, DATE
        if len(cells) >= 4:
            flags = cells[1].strip() if len(cells) > 1 else ""
            subject = cells[2].strip() if len(cells) > 2 else ""
            sender = cells[3].strip() if len(cells) > 3 else ""
            # Truncate long subjects
            if len(subject) > 70:
                subject = subject[:67] + "..."
            # Clean sender name (remove "via ..." suffixes)
            if " vía " in sender:
                sender = sender.split(" vía ")[0]
            if " via " in sender:
                sender = sender.split(" via ")[0]
            unread = "*" if flags and "*" in flags else ""
            marker = ">> " if unread else "   "
            formatted.append(f"{marker}{subject}\n     {sender}")

    count = len(formatted)
    unread = sum(1 for r in rows if len(r) > 1 and "*" in r[1])
    header = f"{count} emails recientes"
    if unread:
        header += f" ({unread} sin leer)"

    return header + "\n\n" + "\n\n".join(formatted)


# ── Humanize skill output ─────────────────────────────────────────

# Skills that should be humanized (raw output → natural response)
# Some skills already return good output (smoking-tracker, smart-home)
_HUMANIZE_SKILLS = {
    "skill:calendar", "skill:calendar-agenda",
    "skill:infra-monitor",
    "skill:arr-monitor",
    "skill:weather",
    "skill:email",
    "skill:self-evolve",
    "skill:network-scanner",
}

_HUMANIZE_PROMPT = (
    "Sos Tenazas, asistente personal de Sebas. "
    "Hablás en español rioplatense, directo y cálido. "
    "NUNCA empezás con 'Che'. Variá los inicios. "
    "Nunca decís 'como asistente' ni 'según los datos'. "
    "Respondés como un amigo por WhatsApp.\n\n"
    "Sebas preguntó: \"{query}\"\n"
    "Datos del sistema:\n{data}\n\n"
    "REGLAS:\n"
    "- Solo respondé lo que Sebas preguntó. NADA más.\n"
    "- NO agregues información de otros temas (clima, emails, infra) "
    "si no los pidió.\n"
    "- Si los datos del sistema están vacíos o no tienen lo que pidió, "
    "decí que no tenés esa info.\n"
    "- NUNCA inventes datos que no están en 'Datos del sistema'.\n"
    "- Máximo 3-4 líneas."
)


def _humanize_response(query: str, raw_output: str, category: str) -> str:
    """Pass skill output through LLM for natural language response."""
    if category not in _HUMANIZE_SKILLS:
        return raw_output
    if not raw_output or len(raw_output) < 10:
        return raw_output

    prompt = _HUMANIZE_PROMPT.format(query=query[:100], data=raw_output[:1500])

    try:
        result = _call_ollama_chat(OLLAMA_GPU_URL, [
            {"role": "system", "content": prompt},
            {"role": "user", "content": query},
        ])
        if result and len(result) > 5:
            return result
    except Exception:
        pass

    # Fallback: return raw
    return raw_output


# ── LLM Fallback (non-skill messages) ─────────────────────────────

# Ollama endpoint for non-skill messages
OLLAMA_GPU_URL = os.environ.get("OLLAMA_GPU_URL", "http://192.168.2.6:11434")
OLLAMA_NAS_URL = "http://127.0.0.1:11434"
LLM_MODEL = "qwen3:8b"
LLM_TIMEOUT = 60


def _load_soul() -> str:
    """Load SOUL.md as system prompt."""
    try:
        return SOUL_FILE.read_text(encoding="utf-8")
    except FileNotFoundError:
        return "Sos Tenazas, asistente personal de Sebas. Español rioplatense, directo."


# System prompt for free chat (non-skill queries).
# SOUL.md is designed for the gateway (tool execution). For Telegram chat,
# the LLM can NOT execute commands — it only generates text. This prompt
# prevents hallucination and off-topic responses.
_CHAT_SYSTEM_PROMPT = (
    "Sos Tenazas, asistente personal de Sebas. Hablás en español rioplatense, "
    "directo y cálido. Esto es un chat de Telegram.\n\n"
    "REGLAS ESTRICTAS:\n"
    "1. NUNCA inventes datos que no tenés (precios, nombres, valores, fechas). "
    "Si no sabés algo, decí 'No tengo esa info' o 'No puedo verificar eso'.\n"
    "2. Solo respondé lo que te preguntaron. NO metas clima, emails, infra ni "
    "ningún otro tema si no te lo pidieron explícitamente.\n"
    "3. No repitas información de mensajes anteriores salvo que te la pidan.\n"
    "4. Si el usuario pregunta algo fuera de tus capacidades (precios de nafta, "
    "noticias, datos en tiempo real que no tenés), decilo claramente.\n"
    "5. Máximo 3-4 líneas. Sin emojis excesivos. Sin frases como "
    "'como asistente' o 'según los datos'.\n"
    "6. Tus capacidades reales son: clima (Open-Meteo), emails (Gmail), "
    "calendario (Google Calendar), luces (Hue), infra (Docker/NAS), "
    "red (nmap), modelos (Ollama), finanzas (IOL/Binance), cigarrillos. "
    "Cualquier otra cosa, NO la tenés.\n"
    "7. NUNCA generes datos de ejemplo ni respuestas inventadas para parecer útil."
)


def _call_ollama(url: str, prompt: str, system: str, model: str | None = None) -> str | None:
    """Call Ollama generate API (single-turn). Returns response text or None."""
    payload = json.dumps({
        "model": model or LLM_MODEL,
        "prompt": prompt,
        "system": system,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 1024},
    }).encode()
    req = Request(f"{url}/api/generate", data=payload,
                  headers={"Content-Type": "application/json"})
    try:
        resp = urlopen(req, timeout=LLM_TIMEOUT)
        data = json.loads(resp.read())
        return data.get("response", "").strip()
    except Exception as e:
        _log({"action": "ollama_error", "url": url, "error": str(e)})
        return None


def _call_ollama_chat(url: str, messages: list[dict], model: str | None = None) -> str | None:
    """Call Ollama chat API (multi-turn). Returns response text or None."""
    payload = json.dumps({
        "model": model or LLM_MODEL,
        "messages": messages,
        "stream": False,
        "options": {"temperature": 0.7, "num_predict": 1024},
    }).encode()
    req = Request(f"{url}/api/chat", data=payload,
                  headers={"Content-Type": "application/json"})
    try:
        resp = urlopen(req, timeout=LLM_TIMEOUT)
        data = json.loads(resp.read())
        return data.get("message", {}).get("content", "").strip()
    except Exception as e:
        _log({"action": "ollama_chat_error", "url": url, "error": str(e)})
        return None


def _select_model_for_query(text: str) -> tuple[str, str]:
    """Use model_router V2 to select the best model for a query.

    Returns (model_name, ollama_url) or falls back to defaults.
    """
    try:
        sys.path.insert(0, str(RUNTIME_DIR)) if str(RUNTIME_DIR) not in sys.path else None
        from model_router import route_by_role
        result = route_by_role(text)
        if result and result.get("model") and result.get("url"):
            return result["model"], result["url"]
    except Exception as e:
        _log({"action": "router_fallback", "error": str(e)})
    return LLM_MODEL, OLLAMA_GPU_URL


def _gather_analysis_context(text: str) -> str:
    """Gather relevant log data for analytical queries.

    If the query mentions fallback, routing, guardrails, etc., inject
    recent log entries so the LLM has real data to analyze.
    """
    text_lower = text.lower()
    context_parts = []

    # Fallback / routing analysis
    if any(k in text_lower for k in ("fallback", "routing", "rout", "modelo", "model")):
        log_file = RUNTIME_DIR / "logs" / "model_router.log"
        try:
            lines = log_file.read_text().strip().split("\n")
            # Last 50 entries
            recent = lines[-50:]
            context_parts.append(f"=== model_router.log (últimas {len(recent)} entradas) ===")
            context_parts.extend(recent)
        except (FileNotFoundError, OSError):
            pass

    # Guardrail / degradation analysis
    if any(k in text_lower for k in ("guardrail", "degradaci", "cooldown", "vram")):
        try:
            data = json.loads(GUARDRAIL_STATE_FILE.read_text()) if GUARDRAIL_STATE_FILE.exists() else {}
            context_parts.append("=== guardrail_state.json ===")
            context_parts.append(json.dumps(data, indent=2))
        except (json.JSONDecodeError, OSError):
            pass

    # Orchestrator / cloud fallback analysis
    if any(k in text_lower for k in ("fallback", "cloud", "orquestador", "orchestrat")):
        orch_log = RUNTIME_DIR / "logs" / "orchestrator.log"
        try:
            lines = orch_log.read_text().strip().split("\n")
            # Filter cloud_fallback entries
            fallbacks = [l for l in lines if "cloud_fallback" in l][-20:]
            if fallbacks:
                context_parts.append(f"=== orchestrator.log cloud_fallbacks ({len(fallbacks)}) ===")
                context_parts.extend(fallbacks)
            # Also last 20 routing decisions
            routings = [l for l in lines if '"routing"' in l][-20:]
            if routings:
                context_parts.append(f"=== orchestrator.log routing ({len(routings)}) ===")
                context_parts.extend(routings)
        except (FileNotFoundError, OSError):
            pass

    # Container / infra analysis
    if any(k in text_lower for k in ("container", "infra", "docker", "restart")):
        docker_log = RUNTIME_DIR / "logs" / "docker_actions.log"
        try:
            raw_lines = docker_log.read_text().strip().split("\n")[-50:]
            # Filter out intentional security blocks — only show real actions
            lines = []
            for l in raw_lines:
                try:
                    entry = json.loads(l)
                    err = str(entry.get("error", "")).lower()
                    if "not allowed" in err or "not in the" in err:
                        continue  # intentional block, skip
                    lines.append(l)
                except (json.JSONDecodeError, ValueError):
                    lines.append(l)
            lines = lines[-30:]
            if lines:
                context_parts.append(f"=== docker_actions.log (últimas {len(lines)}) ===")
                context_parts.extend(lines)
        except (FileNotFoundError, OSError):
            pass

    if not context_parts:
        return ""

    return "\n\nDatos reales del sistema para tu análisis:\n" + "\n".join(context_parts)


GUARDRAIL_STATE_FILE = RUNTIME_DIR / "guardrail_state.json"


# ── Live Data Guardrail ──────────────────────────────────────────
# Intercepts queries about real-time data we can't provide (fuel prices,
# exchange rates, news, sports). Returns a fixed response without hitting
# the LLM — prevents hallucination and saves tokens.

_LIVE_DATA_UNSUPPORTED_PATTERNS = [
    r"\bprecio\b.*\bnafta\b",
    r"\bnafta\b.*\bprecio\b",
    r"\bcu[aá]nto\s+(est[aá]|sale|cuesta)\b.*\b(d[oó]lar|nafta|btc|bitcoin|eth|ethereum)\b",
    r"\bcotiza(ci[oó]n)?\b",
    r"\bnoticias\b.*\b(hoy|del d[ií]a|actuales)\b",
    r"\bresultado\b.*\bpartido\b",
    r"\bqui[eé]n\s+gan[oó]\b",
    r"\bc[uú]anto\s+vale\b.*\b(d[oó]lar|nafta|btc|bitcoin|eth|ethereum)\b",
    r"\bprecio\b.*\b(d[oó]lar|gasolina|combustible|super|premium)\b",
]


def _is_unsupported_live_data(text: str) -> bool:
    t = text.strip().lower()
    return any(re.search(p, t) for p in _LIVE_DATA_UNSUPPORTED_PATTERNS)


def _unsupported_live_data_reply() -> str:
    return (
        "No tengo una fuente confiable en tiempo real para eso, "
        "así que prefiero no inventártelo."
    )


def handle_non_skill(text: str, chat_id: str = CHAT_ID) -> str:
    """Forward non-skill message to Ollama LLM with conversation history."""

    # Guardrail: intercept live data queries we can't answer
    if _is_unsupported_live_data(text):
        log.info("guardrail_hit kind=unsupported_live_data text=%r", text[:200])
        try:
            from obs import log_router_decision
            log_router_decision(
                query=text, route="guardrail", path="early_return",
                skill=None, guardrail_hit=True, used_llm=False,
                latency_ms=0, success=True, chat_id=chat_id)
        except Exception:
            pass
        return _unsupported_live_data_reply()

    system = _CHAT_SYSTEM_PROMPT
    model, primary_url = _select_model_for_query(text)

    # Enrich analytical queries with real data
    context = _gather_analysis_context(text)
    if context:
        text = text + context

    # Build multi-turn messages from history
    messages = _build_ollama_messages(chat_id, system)

    # If we have conversation history (>1 = system + at least one prior exchange),
    # use chat API for context-aware responses
    if len(messages) > 1:
        response = _call_ollama_chat(primary_url, messages, model=model)
        if response:
            return response
        # Fallback: default model on GPU
        if model != LLM_MODEL or primary_url != OLLAMA_GPU_URL:
            response = _call_ollama_chat(OLLAMA_GPU_URL, messages)
            if response:
                return response
        # Last resort: NAS CPU
        response = _call_ollama_chat(OLLAMA_NAS_URL, messages)
        if response:
            return response

    # No history or chat API failed — single-turn fallback
    prompt = text
    response = _call_ollama(primary_url, prompt, system, model=model)
    if response:
        return response

    if model != LLM_MODEL or primary_url != OLLAMA_GPU_URL:
        response = _call_ollama(OLLAMA_GPU_URL, prompt, system)
        if response:
            return response

    response = _call_ollama(OLLAMA_NAS_URL, prompt, system)
    if response:
        return response

    return "No pude conectar con el modelo. Ollama offline?"


# ── Main Polling Loop ──────────────────────────────────────────────

def poll_loop():
    """Main Telegram long-polling loop."""
    token = _get_bot_token()
    allowed = _get_allowed_chat_ids()
    offset = 0

    _log({"action": "startup", "allowed_chats": list(allowed)})
    print(f"[telegram_router] Started. Polling Telegram. Allowed: {allowed}")

    while True:
        try:
            result = tg_request(token, "getUpdates", {
                "offset": offset,
                "timeout": POLL_TIMEOUT,
                "allowed_updates": ["message", "callback_query"],
            }, timeout=POLL_TIMEOUT + 5)

            if not result or not result.get("ok"):
                time.sleep(2)
                continue

            updates = result.get("result", [])
            for update in updates:
                offset = update["update_id"] + 1
                _process_update(token, update, allowed)

        except KeyboardInterrupt:
            _log({"action": "shutdown", "reason": "keyboard"})
            break
        except Exception as e:
            _log({"action": "poll_error", "error": str(e)})
            time.sleep(5)


def _handle_callback(token: str, callback_query: dict):
    """Handle an inline keyboard callback (approval_queue, exec approval, or feedback)."""
    data = callback_query.get("data", "")

    # Route feedback callbacks
    if data.startswith("fb_"):
        try:
            from skill_feedback import handle_feedback_callback
            result = handle_feedback_callback(callback_query, token=token)
            _log({"action": "feedback_processed", "feedback": result.get("feedback", "?")})
            return
        except Exception as e:
            _log({"action": "feedback_error", "error": str(e)})

    # Route approval_queue callbacks (Sprint 1 approval flow)
    if data.startswith("aq_"):
        try:
            from approval_queue import handle_callback as aq_handle
            result = aq_handle(callback_query)
            _log({"action": "aq_callback_processed", "result": result.get("action", "unknown"),
                  "id": result.get("id", "")})
            return
        except Exception as e:
            _log({"action": "aq_callback_error", "error": str(e)})
            cb_id = callback_query.get("id", "")
            if cb_id:
                tg_request(token, "answerCallbackQuery", {
                    "callback_query_id": cb_id,
                    "text": "Error procesando aprobacion",
                }, timeout=5)
            return

    # Route exec approval callbacks (legacy)
    try:
        from exec_approvals import handle_callback_query
        result = handle_callback_query(callback_query, token=token)
        _log({"action": "callback_processed", "result": result.get("action", "unknown"),
              "approval_id": result.get("approval_id", "")})
    except Exception as e:
        _log({"action": "callback_error", "error": str(e)})
        # Answer the callback to remove loading indicator
        cb_id = callback_query.get("id", "")
        if cb_id:
            tg_request(token, "answerCallbackQuery", {
                "callback_query_id": cb_id,
                "text": "Error procesando la accion",
            }, timeout=5)


def _process_update(token: str, update: dict, allowed: set):
    """Process a single Telegram update."""
    # Handle inline keyboard button taps (exec approvals)
    callback = update.get("callback_query")
    if callback:
        cb_chat_id = str(callback.get("message", {}).get("chat", {}).get("id", ""))
        if cb_chat_id in allowed:
            _handle_callback(token, callback)
        return

    msg = update.get("message")
    if not msg:
        return

    chat_id = str(msg.get("chat", {}).get("id", ""))
    text = msg.get("text", "").strip()

    if not text or chat_id not in allowed:
        return

    _log({"action": "incoming", "chat_id": chat_id, "text": text[:120]})

    # Record user message in history
    _append_history(chat_id, "user", text)

    # ── Text-based approval/status commands ──
    text_lower = text.lower().strip()
    if text_lower.startswith("aprobar ") or text_lower.startswith("approve "):
        target = text[len(text_lower.split()[0]) + 1:].strip()
        response = None
        # Try exec approval_queue first
        try:
            from approval_queue import approve_by_text
            result = approve_by_text(target)
            if result:
                response = f"Aprobado: {target} (ID: {result})"
        except Exception:
            pass
        # Try improvement_candidates if not found in exec queue
        if not response:
            try:
                from improvement_candidates import update_status, stage_candidate, list_candidates
                # If target looks like a candidate ID (hex), stage then approve
                candidates = list_candidates()
                match = [c for c in candidates if c.get("id", "").startswith(target)]
                if match:
                    c = match[0]
                    if c["status"] == "proposed":
                        stage_candidate(c["id"])
                    update_status(c["id"], "approved", note="approved via Telegram")
                    response = f"Mejora aprobada: [{c['type']}] {c['title']}"
            except Exception:
                pass
        if not response:
            response = f"No encontre aprobacion pendiente para '{target}'"
        send_message(token, chat_id, response)
        _append_history(chat_id, "assistant", response)
        return

    if text_lower.startswith("rechazar ") or text_lower.startswith("reject "):
        target = text[len(text_lower.split()[0]) + 1:].strip()
        response = None
        # Try exec approval_queue first
        try:
            from approval_queue import _load_queue, _expire_old, _save_queue
            queue = _load_queue()
            _expire_old(queue)
            for entry in queue:
                if entry.get("status") != "pending":
                    continue
                if (target.lower() in entry.get("action_name", "").lower()
                        or target.lower() in entry.get("target", "").lower()):
                    entry["status"] = "rejected"
                    entry["resolved_at"] = time.time()
                    _save_queue(queue)
                    response = f"Rechazado: {entry['action_name']} -> {entry.get('target', '')}"
                    break
        except Exception:
            pass
        # Try improvement_candidates if not found
        if not response:
            try:
                from improvement_candidates import update_status, list_candidates
                candidates = list_candidates()
                match = [c for c in candidates if c.get("id", "").startswith(target)]
                if match:
                    c = match[0]
                    update_status(c["id"], "rejected", note="rejected via Telegram")
                    response = f"Mejora rechazada: [{c['type']}] {c['title']}"
            except Exception:
                pass
        if not response:
            response = f"No encontre aprobacion pendiente para '{target}'"
        send_message(token, chat_id, response)
        _append_history(chat_id, "assistant", response)
        return

    if text_lower in ("estado homelab", "homelab status", "status homelab", "estado"):
        send_typing(token, chat_id)
        try:
            from homelab_snapshot import snapshot, format_telegram
            snap = snapshot()
            response = format_telegram(snap)
        except Exception as e:
            response = f"Error generando snapshot: {e}"
        send_message(token, chat_id, response)
        _append_history(chat_id, "assistant", response)
        return

    if text_lower in ("estado tenazas", "dashboard", "tenazas status", "tenazas dashboard"):
        send_typing(token, chat_id)
        try:
            from homelab_dashboard_payload import format_telegram as fmt_dashboard
            response = fmt_dashboard()
        except Exception as e:
            response = f"Error generando dashboard: {e}"
        send_message(token, chat_id, response)
        _append_history(chat_id, "assistant", response)
        return

    # Check email send follow-up before classifying
    try:
        from orchestrator import complete_email_send, _load_email_pending
        if _load_email_pending():
            email_result = complete_email_send(text)
            if email_result:
                response = email_result.get("output", "")
                _log({"action": "email_send_complete", "success": email_result.get("success")})
                send_message(token, chat_id, response)
                return
    except Exception:
        pass

    # Check self-evolve conversational follow-up before classifying
    if _check_self_evolve_followup(chat_id, text):
        _log({"action": "self_evolve_followup", "text": text[:80]})
        intercept, category = True, "skill:self-evolve"
    else:
        # Classify
        intercept, category = should_intercept(text)

    import time as _time
    _t0 = _time.perf_counter()

    if intercept:
        _log({"action": "intercepted", "category": category, "text": text[:80]})
        send_typing(token, chat_id)

        # Check implicit feedback (repeat query within 2 min)
        try:
            from skill_feedback import check_repeat
            check_repeat(chat_id, text, category)
        except Exception:
            pass

        formatted_response, raw_data = execute_intercepted_skill(text, category=category)

        # Try structured composer first (no LLM needed)
        composed = None
        _source_map = {
            "skill:weather": "weather",
            "skill:email": "gmail",
            "skill:calendar": "calendar",
            "skill:calendar-agenda": "calendar",
        }
        composer_source = _source_map.get(category)
        result_count = None
        if composer_source and raw_data:
            try:
                from response_composer import compose_skill_response
                composed = compose_skill_response(composer_source, raw_data, text)
            except Exception as e:
                log.warning("composer failed source=%s: %s", composer_source, e)
                try:
                    from obs import log_failure
                    log_failure(query=text, route=category, path="composer",
                                reason=f"composer_exception: {e}",
                                severity="medium")
                except Exception:
                    pass

        if composed:
            response = composed
            route_method = "composer"
        else:
            # Fallback: humanize through LLM
            response = _humanize_response(text, formatted_response, category)
            route_method = "humanize" if response != formatted_response else "raw"
            # Log failure if we expected composer but fell back
            if composer_source:
                try:
                    from obs import log_failure
                    sev = "high" if route_method == "raw" else "medium"
                    log_failure(query=text, route=category, path=route_method,
                                reason="composer_fallback",
                                response_preview=response, severity=sev)
                except Exception:
                    pass

        _latency = int((_time.perf_counter() - _t0) * 1000)

        # Track last skill for conversational continuity
        _record_last_skill(chat_id, category)

        _log({"action": "skill_response", "category": category,
              "response_len": len(response), "route": route_method,
              "success": True})

        # Observability: structured logs
        try:
            from obs import log_router_decision, log_skill_run
            log_router_decision(
                query=text, route=category, path=route_method,
                skill=category, guardrail_hit=False,
                used_llm=(route_method == "humanize"),
                latency_ms=_latency, success=True, chat_id=chat_id)
            log_skill_run(
                skill=category, query=text, ok=True,
                route=route_method, latency_ms=_latency)
        except Exception:
            pass

        # Send with feedback buttons for skills
        try:
            from skill_feedback import send_with_feedback
            send_with_feedback(token, chat_id, response, text, category)
        except Exception:
            send_message(token, chat_id, response)
    else:
        _log({"action": "delegating_to_llm", "category": category, "text": text[:80]})
        send_typing(token, chat_id)

        response = handle_non_skill(text, chat_id=chat_id)
        _latency = int((_time.perf_counter() - _t0) * 1000)

        _log({"action": "llm_response", "category": category,
              "response_len": len(response)})

        # Observability: structured log
        try:
            from obs import log_router_decision
            log_router_decision(
                query=text, route="chat_free", path="llm_chat",
                skill=None, guardrail_hit=False, used_llm=True,
                latency_ms=_latency, success=bool(response), chat_id=chat_id)
        except Exception:
            pass

        send_message(token, chat_id, response)

    # Record assistant response in history
    _append_history(chat_id, "assistant", response)


# ── Test Mode ──────────────────────────────────────────────────────

def run_tests():
    """Run classification tests."""
    test_cases = [
        # (input, expected_intercept, expected_category_contains)
        ("cuántos emails tengo", True, "email"),
        ("mostrame mis correos", True, "email"),
        ("revisá el inbox", True, "email"),
        ("hay algo de telus?", True, "email"),
        ("leeme los mails", True, "email"),
        ("emails", True, "email"),
        ("mis mails", True, "email"),
        ("cómo está el clima?", True, "weather"),
        ("qué temperatura hace?", True, "weather"),
        ("prendé las luces", True, "smart-home"),
        ("estado de los containers", True, "infra"),
        ("escaneá la red", True, "network"),
        ("qué tengo en la agenda?", True, "calendar"),
        ("buenos días", True, "morning"),
        ("explicame qué es kubernetes", False, None),
        ("hola cómo andás", False, None),
        ("cuánto gasté este mes", False, None),
        ("postulé a TIVIT", False, None),
    ]

    passed = 0
    failed = 0

    for text, expected_intercept, expected_cat in test_cases:
        intercept, category = should_intercept(text)
        cat_ok = (expected_cat is None) or (expected_cat in category)
        ok = (intercept == expected_intercept) and cat_ok

        status = "✓" if ok else "✗"
        if ok:
            passed += 1
        else:
            failed += 1

        print(f"  {status} \"{text}\"")
        print(f"    intercept={intercept} (expected {expected_intercept})")
        print(f"    category={category}" +
              (f" (expected *{expected_cat}*)" if expected_cat else ""))

    print(f"\n{passed} passed, {failed} failed out of {len(test_cases)}")
    return failed == 0


def run_dry():
    """Read messages from stdin and classify without sending."""
    print("Dry-run mode. Type messages (Ctrl+D to exit):")
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        intercept, category = should_intercept(text)
        action = "INTERCEPT" if intercept else "LLM"
        print(f"  [{action}] {category}: {text}")


# ── Entry point ────────────────────────────────────────────────────

if __name__ == "__main__":
    if "--test" in sys.argv:
        sys.exit(0 if run_tests() else 1)
    elif "--dry-run" in sys.argv:
        run_dry()
    else:
        poll_loop()
