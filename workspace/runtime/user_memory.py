"""
user_memory.py — Minimal useful memory for Tenazas.

Two layers:
1. user_profile.json — static, long-term (name, location, projects, preferences)
2. recent_context.json — short-term, TTL-based (last 5 interactions summarized)

Injection is selective: only when it improves the response.
"""

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path

RUNTIME_DIR = Path(__file__).parent
PROFILE_PATH = RUNTIME_DIR / "user_profile.json"
CONTEXT_PATH = RUNTIME_DIR / "recent_context.json"

# Short-term memory: max entries and TTL
CONTEXT_MAX_ENTRIES = 5
CONTEXT_TTL_SECONDS = 3600  # 1 hour


# ── Profile (long-term) ─────────────────────────────────────────

_DEFAULT_PROFILE = {
    "name": "Sebas",
    "full_name": "Sebastián Basadre",
    "country": "Argentina",
    "city": "Buenos Aires",
    "timezone": "America/Argentina/Buenos_Aires",
    "language": "es-AR",
    "tone": "directo, rioplatense, cálido",
    "projects": ["Tenazas", "OpenClaw", "Homelab"],
    "interests": ["IA", "producto", "inversión", "música", "homelab"],
    "job_search": True,
    "roles_target": ["Product Manager", "TAM", "DevOps"],
    "broker": "IOL (InvertirOnline)",
    "smoking": True,
    "tools": ["Docker", "WSL2", "Ollama", "Synology NAS", "NVIDIA Workbench"],
    "notes": [
        "Prefiere respuestas concretas y cortas",
        "Usa Tenazas por Telegram",
    ],
}

# Topics worth saving to short-term memory
_IMPORTANT_TOPICS = [
    "sprint", "tenazas", "openclaw", "homelab", "dataset", "workbench",
    "docker", "nas", "entrenamiento", "training", "modelo", "router",
    "pipeline", "deploy", "grafana", "prometheus", "eval",
]


def get_profile() -> dict:
    """Load user profile. Creates default if missing."""
    try:
        return json.loads(PROFILE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        save_profile(_DEFAULT_PROFILE)
        return _DEFAULT_PROFILE.copy()


def save_profile(profile: dict) -> None:
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False))


def update_profile(key: str, value) -> None:
    """Update a single profile field."""
    p = get_profile()
    p[key] = value
    save_profile(p)


# ── Recent Context (short-term) ─────────────────────────────────

def get_recent_context() -> list[dict]:
    """Load recent context, filtering expired entries."""
    try:
        entries = json.loads(CONTEXT_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return []

    now = time.time()
    active = [e for e in entries if now - e.get("ts", 0) < CONTEXT_TTL_SECONDS]

    # Trim to max
    active = active[-CONTEXT_MAX_ENTRIES:]
    return active


def is_worth_remembering(query: str, skill: str | None) -> bool:
    """Decide if this interaction is worth saving to short-term memory.

    Only save important/continuable topics, not trivial queries.
    """
    q = query.lower()
    # Chat libre about important topics
    if skill is None and any(t in q for t in _IMPORTANT_TOPICS):
        return True
    # Skills that indicate ongoing work (not weather/calendar/email)
    if skill and skill not in (
        "skill:weather", "skill:email", "skill:calendar",
        "skill:calendar-agenda", "skill:smoking",
    ):
        return True
    return False


def add_context(query: str, skill: str | None, summary: str) -> None:
    """Add a summarized interaction to short-term memory."""
    entries = get_recent_context()
    entries.append({
        "ts": time.time(),
        "query": query[:100],
        "skill": skill,
        "summary": summary[:200],
    })
    # Keep only last N
    entries = entries[-CONTEXT_MAX_ENTRIES:]
    CONTEXT_PATH.write_text(json.dumps(entries, indent=2, ensure_ascii=False))
    _log_memory_event("memory_write", query)


def clear_context() -> None:
    """Clear short-term memory."""
    CONTEXT_PATH.write_text("[]")


# ── Injection (selective) ────────────────────────────────────────

def should_inject_memory(query: str) -> bool:
    """Decide if memory injection would improve the response.

    Returns True for vague/contextual queries that benefit from knowing
    who the user is or what they were doing recently.
    """
    q = query.lower().strip()

    # Vague follow-ups that need context
    vague_patterns = [
        "cómo veníamos", "como veniamos", "en qué estábamos",
        "en que estabamos", "seguimos", "continuamos",
        "y eso", "y lo otro", "qué más", "que mas",
        "qué me conviene", "que me conviene",
        "qué debería", "que deberia",
        "qué hago", "que hago",
        "cómo vengo", "como vengo",
        "qué onda", "que onda",
        "dame un resumen", "poneme al día", "poneme al dia",
    ]
    if any(p in q for p in vague_patterns):
        return True

    # Personal questions that benefit from profile
    personal_patterns = [
        "mi nombre", "quién soy", "quien soy",
        "mis proyectos", "en qué trabajo", "en que trabajo",
        "qué puedo hacer", "que puedo hacer",
        "recomendame", "sugerime", "aconse",
    ]
    if any(p in q for p in personal_patterns):
        return True

    return False


def build_memory_context(query: str) -> str:
    """Build a memory context string to inject before the query.

    Only called when should_inject_memory() returns True.
    Returns a concise block, max 8-10 lines.
    """
    parts = []

    # Profile summary
    profile = get_profile()
    name = profile.get("name", "usuario")
    projects = ", ".join(profile.get("projects", [])[:4])
    parts.append(f"- Nombre: {name}")
    if profile.get("timezone"):
        parts.append(f"- Zona horaria: {profile['timezone']}")
    if projects:
        parts.append(f"- Proyectos activos: {projects}")
    if profile.get("tone_preference"):
        parts.append(f"- Tono preferido: {profile['tone_preference']}")
    if profile.get("notes"):
        for note in profile["notes"][:2]:
            parts.append(f"- {note}")
    if profile.get("job_search"):
        roles = ", ".join(profile.get("roles_target", []))
        parts.append(f"- Buscando trabajo: {roles}")

    # Recent context (if any)
    recent = get_recent_context()
    if recent:
        parts.append("- Contexto reciente:")
        for entry in recent[-3:]:  # last 3 max
            parts.append(f"  - {entry['summary']}")

    _log_memory_event("memory_read", query, used_recent=len(recent))

    if not parts:
        return ""

    return "Contexto útil del usuario:\n" + "\n".join(parts)


# ── Memory Event Logging ────────────────────────────────────────

MEMORY_EVENTS_LOG = RUNTIME_DIR / "logs" / "memory_events.jsonl"


def _log_memory_event(event: str, query: str = "",
                      used_recent: int = 0) -> None:
    """Log memory read/write/prune events."""
    try:
        MEMORY_EVENTS_LOG.parent.mkdir(parents=True, exist_ok=True)
        entry = json.dumps({
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": event,
            "query": query[:200],
            "used_recent_items": used_recent,
        }, ensure_ascii=False)
        with open(MEMORY_EVENTS_LOG, "a", encoding="utf-8") as f:
            f.write(entry + "\n")
    except Exception:
        pass
