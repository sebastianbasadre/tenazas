#!/usr/bin/env python3
"""
Skill Executor — Executes skills and returns real output.

Extracted from orchestrator.py to separate skill execution concerns
from orchestration logic. The orchestrator facade re-exports all public
names for backward compatibility.

Skill categories map to executable command templates. Skills with
custom handlers (smart-home, TTS, calendar, etc.) use dedicated
functions. Parsers post-process raw command output.
"""

import json
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path

try:
    from runtime_utils import now
except ImportError:
    from workspace.runtime.runtime_utils import now

try:
    from runtime_config import HUE_BRIDGE_IP, OLLAMA_GPU_URL
except ImportError:
    from workspace.runtime.runtime_config import HUE_BRIDGE_IP, OLLAMA_GPU_URL

# ── Logging ─────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
ORCHESTRATOR_LOG = BASE_DIR / "logs" / "orchestrator.log"


def _log(entry: dict):
    ORCHESTRATOR_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(ORCHESTRATOR_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Multitool Constants ─────────────────────────────────────────

MULTITOOL_MODEL = "tenazas-multitool"
MULTITOOL_OLLAMA_URL = OLLAMA_GPU_URL
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

# ── Email Pending State ─────────────────────────────────────────

_EMAIL_PENDING_FILE = Path(__file__).parent / "email_pending.json"

# ── Skill Map ───────────────────────────────────────────────────

# Timeout per skill (seconds)
SKILL_TIMEOUT = 30

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
        "command": "",
        "destructive": False,
        "description": "Eventos del calendario",
        "handler": "_handle_calendar",
    },
    "skill:calendar-agenda": {
        "command": "",
        "destructive": False,
        "description": "Eventos del calendario",
        "handler": "_handle_calendar",
    },
    "skill:smart-home": {
        # Default: list light states. Actual action extracted from query.
        "command": (
            f'curl -sf http://{HUE_BRIDGE_IP}/api/'
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
        "command": "python3 /home/tenazas/runtime/device_discovery.py --quick",
        "destructive": False,
        "description": "Scan de red LAN + inventario de dispositivos",
        "handler": "_handle_network_scanner",
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
    "skill:smoking-tracker": {
        "command": "",
        "destructive": False,
        "description": "Registro de cigarrillos fumados",
        "handler": "_handle_smoking",
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


# ── Skill Parsers ───────────────────────────────────────────────


def _parse_calendar(raw: str) -> str:
    """Parse gcal.py output into readable format."""
    if not raw or not raw.strip():
        return "No hay eventos proximos."
    lines = raw.strip().splitlines()
    from datetime import datetime as _dt, date as _date
    today = _date.today()
    parts = ["Proximos eventos:"]
    for line in lines:
        if "|" not in line:
            continue
        ts_str, title = line.split("|", 1)
        ts_str = ts_str.strip()
        title = title.strip()
        try:
            dt = _dt.fromisoformat(ts_str)
            d = dt.date()
            time_str = dt.strftime("%H:%M")
            delta = (d - today).days
            if delta == 0:
                day_label = "Hoy"
            elif delta == 1:
                day_label = "Manana"
            elif delta == -1:
                day_label = "Ayer"
            else:
                day_label = d.strftime("%a %d/%m")
            parts.append(f"  {day_label} {time_str} — {title}")
        except Exception:
            parts.append(f"  {line.strip()}")
    return "\n".join(parts) if len(parts) > 1 else "No hay eventos proximos."


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
    """Handle smart home commands — delegates to devices/ dispatcher.

    Supports: Hue lights, Marantz AVR, Hue Sync Box, Samsung TVs,
    Google Cast, Xiaomi Vacuum, NVIDIA SHIELD, EZVIZ cameras,
    Nest Doorbell.
    """
    try:
        from devices import dispatch, dispatch_composite

        # Check for composite "cinema mode" that spans multiple devices
        query_lower = query.lower()
        _cinema_re = (
            r"cine|cinema|pel[ií]cula|movie"
            r"|modo\s*cine|modo\s*pel[ií]cula"
        )
        # Cinema composite: only when no specific device is mentioned
        # "modo cine" → composite, but "marantz modo cine" → marantz only
        _device_specific = (
            r"marantz|sync\s*box|samsung|shield|tv|parlante|aspiradora"
        )
        if (re.search(_cinema_re, query_lower)
                and not re.search(_device_specific, query_lower)
                and not re.search(r"lu[cz]|light|brillo", query_lower)):
            return dispatch_composite("cinema", query, skill_cat)

        return dispatch(query, skill_cat)

    except ImportError:
        # Fallback: devices package not available (shouldn't happen)
        return {
            "skill": skill_cat, "success": False,
            "output": "Error: modulo devices no disponible.",
            "raw": "", "destructive": False, "error": "import_error",
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


def _handle_calendar(skill_cat: str, query: str, skill: dict) -> dict:
    """Smart calendar handler — filters events based on query."""
    from datetime import date, timedelta

    q = query.lower()
    today = date.today()

    # Detect if it's a date question (not agenda)
    is_date_question = any(w in q for w in [
        "que dia es", "que fecha es", "qué día es", "qué fecha es",
    ])
    if is_date_question:
        days = {"lunes": 0, "martes": 1, "miercoles": 2, "miércoles": 2,
                "jueves": 3, "viernes": 4, "sabado": 5, "sábado": 5, "domingo": 6}
        if "mañana" in q:
            d = today + timedelta(days=1)
        elif "pasado" in q:
            d = today + timedelta(days=2)
        else:
            d = today
        day_names = ["Lunes", "Martes", "Miercoles", "Jueves",
                     "Viernes", "Sabado", "Domingo"]
        return {
            "skill": skill_cat, "success": True,
            "output": f"{day_names[d.weekday()]} {d.strftime('%d/%m/%Y')}",
            "raw": "", "destructive": False, "error": None,
        }

    # Get calendar events
    try:
        days_ahead = "7" if any(w in q for w in ["semana", "week"]) else "2"
        r = subprocess.run(
            ["python3", "/home/tenazas/.openclaw/workspace/skills/"
             "calendar-agenda/gcal.py", days_ahead],
            capture_output=True, text=True, timeout=15,
        )
        raw = r.stdout.strip() if r.returncode == 0 else ""
    except Exception as e:
        return {
            "skill": skill_cat, "success": False,
            "output": f"Error consultando calendario: {e}",
            "raw": "", "destructive": False, "error": "gcal_error",
        }

    if not raw:
        return {
            "skill": skill_cat, "success": True,
            "output": "No hay eventos proximos.",
            "raw": "", "destructive": False, "error": None,
        }

    # Parse and filter
    parsed = _parse_calendar(raw)
    lines = parsed.split("\n")

    # Filter by day name if mentioned
    day_filters = {
        "lunes": "Lun", "martes": "Mar", "miercoles": "Mie", "miércoles": "Mie",
        "jueves": "Jue", "viernes": "Vie", "sabado": "Sab", "sábado": "Sab",
        "domingo": "Dom", "mañana": "Manana", "hoy": "Hoy",
    }
    active_filter = None
    for word, label in day_filters.items():
        if word in q:
            active_filter = label
            break

    # Filter by keyword (doctor, reunión, etc)
    keyword_filter = None
    # Map common words to calendar search terms
    kw_aliases = {"doctor": ["doctor", "medic", "oftalm", "clinic", "turno"],
                  "reunion": ["reunion", "meeting", "call"]}
    for alias_key, alias_list in kw_aliases.items():
        if alias_key in q:
            keyword_filter = "|".join(alias_list)
            break
    if not keyword_filter:
        for kw in ["oftalm", "hackathon", "conservatorio", "psic", "ucema"]:
            if kw in q:
                keyword_filter = kw
                break

    if active_filter or keyword_filter:
        kw_terms = keyword_filter.split("|") if keyword_filter else []
        filtered = [lines[0]]  # Keep header
        for line in lines[1:]:
            low_line = line.lower()
            if active_filter and active_filter.lower() in low_line:
                filtered.append(line)
            elif kw_terms and any(t in low_line for t in kw_terms):
                filtered.append(line)
        if len(filtered) > 1:
            parsed = "\n".join(filtered)
        else:
            parsed = f"No hay eventos que matcheen '{active_filter or keyword_filter}'."

    return {
        "skill": skill_cat, "success": True,
        "output": parsed, "raw": raw, "destructive": False, "error": None,
    }


def _handle_morning_greeting(skill_cat: str, query: str, skill: dict) -> dict:
    """Aggregate morning greeting from multiple sources."""
    parts = []
    now_dt = datetime.now()
    parts.append(f"Buenos días Sebas — {now_dt.strftime('%A %d/%m')}")

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


def _load_email_pending() -> dict:
    try:
        if _EMAIL_PENDING_FILE.exists():
            return json.loads(_EMAIL_PENDING_FILE.read_text())
    except Exception:
        pass
    return {}


def _save_email_pending(data: dict):
    try:
        _EMAIL_PENDING_FILE.write_text(json.dumps(data))
    except Exception:
        pass


def _clear_email_pending():
    try:
        _EMAIL_PENDING_FILE.unlink(missing_ok=True)
    except Exception:
        pass


def _handle_email(skill_cat: str, query: str, skill: dict) -> dict:
    """Handle email: list, read, or send via himalaya CLI."""
    q = query.lower()

    # Detect send intent
    is_send = bool(re.search(
        r"(mand[aá]|envi[aá]|escrib[ií])\w*\s+(un\s+)?"
        r"(mail|email|correo|e-?mail)", q
    ))

    if is_send:
        return _handle_email_send_start(skill_cat, query)

    # Check if this is a follow-up to a pending send
    # (handled in telegram_router via conversational state)

    # Default: list emails
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


def _handle_email_send_start(skill_cat: str, query: str) -> dict:
    """Start email send flow — extract recipient, ask for details."""
    # Try to extract recipient from query
    # "mandá mail a Viviana González" → "Viviana González"
    match = re.search(
        r"(?:mail|email|correo)\s+a\s+(.+?)(?:\s+(?:con|sobre|diciendo|que)\s|$)",
        query, re.IGNORECASE,
    )
    to_name = match.group(1).strip() if match else ""

    # Try to extract subject if present
    # "Asunto: Te amo, desde Tenazas"
    subj_match = re.search(r"asunto:\s*(.+?)(?:\.|$)", query, re.IGNORECASE)
    subject = subj_match.group(1).strip() if subj_match else ""

    # Try to extract body
    body_match = re.search(
        r"(?:diciendo|que\s+diga|con\s+el\s+mensaje)\s+(.+)",
        query, re.IGNORECASE,
    )
    body = body_match.group(1).strip() if body_match else ""

    # Validate email address
    if to_name and "@" not in to_name:
        _save_email_pending({"to": "", "to_name": to_name, "subject": subject, "body": body})
        return {
            "skill": skill_cat, "success": True,
            "output": f"No tengo el mail de {to_name}. Pasame la dirección de email.",
            "raw": "", "destructive": False, "error": None,
        }

    if to_name and subject and body:
        _clear_email_pending()
        return _send_email(skill_cat, to_name, subject, body)

    _save_email_pending({"to": to_name, "subject": subject, "body": body})

    missing = []
    if not to_name:
        missing.append("destinatario (email)")
    if not subject:
        missing.append("asunto")
    if not body:
        missing.append("mensaje")

    prompt = f"Para mandar el mail necesito: {', '.join(missing)}."
    if to_name:
        prompt = f"Mail para {to_name}. " + prompt

    return {
        "skill": skill_cat, "success": True,
        "output": prompt, "raw": "", "destructive": False, "error": None,
    }


def complete_email_send(query: str) -> dict | None:
    """Complete a pending email send with follow-up info. Returns result or None."""
    pending = _load_email_pending()
    if not pending:
        return None

    q = query.strip()

    # Check if user provided an email address
    email_match = re.search(r"[\w.+-]+@[\w.-]+\.\w+", q)
    if email_match and not pending.get("to"):
        pending["to"] = email_match.group(0)
        # If we now have everything, send
        if pending["to"] and pending.get("subject") and pending.get("body"):
            result = _send_email("skill:email", pending["to"], pending["subject"], pending["body"])
            _clear_email_pending()
            return result
        # Still missing subject/body
        missing = []
        if not pending.get("subject"):
            missing.append("asunto")
        if not pending.get("body"):
            missing.append("mensaje")
        _save_email_pending(pending)
        name = pending.get("to_name", pending["to"])
        return {
            "skill": "skill:email", "success": True,
            "output": f"Mail para {name} ({pending['to']}). Necesito: {', '.join(missing)}.",
            "raw": "", "destructive": False, "error": None,
        }

    # Parse "Asunto: X, Mensaje: Y" or just free text
    subj_match = re.search(r"asunto:\s*(.+?)(?:,|\.|$)", q, re.IGNORECASE)
    body_match = re.search(r"(?:mensaje|cuerpo|body):\s*(.+)", q, re.IGNORECASE)

    if subj_match:
        pending["subject"] = subj_match.group(1).strip()
    if body_match:
        pending["body"] = body_match.group(1).strip()

    # If only one thing was missing and user just typed it
    if not pending["subject"] and not pending["body"]:
        # Assume it's "Asunto: <first line>, Body: <rest>"
        lines = q.split("\n", 1)
        pending["subject"] = lines[0].strip()
        if len(lines) > 1:
            pending["body"] = lines[1].strip()
        else:
            pending["body"] = lines[0].strip()
    elif not pending["subject"]:
        pending["subject"] = q
    elif not pending["body"]:
        pending["body"] = q

    # Check if we have everything
    if pending["to"] and pending["subject"] and pending["body"]:
        result = _send_email("skill:email", pending["to"], pending["subject"], pending["body"])
        _clear_email_pending()
        return result

    return None


def _send_email(skill_cat: str, to: str, subject: str, body: str) -> dict:
    """Actually send an email via himalaya message send."""
    # Build raw RFC822 message
    msg = (
        f"From: sebastian.basadre@gmail.com\r\n"
        f"To: {to}\r\n"
        f"Subject: {subject}\r\n"
        f"Content-Type: text/plain; charset=utf-8\r\n"
        f"\r\n"
        f"{body}"
    )

    try:
        result = subprocess.run(
            ["himalaya", "message", "send"],
            input=msg,
            capture_output=True, text=True, timeout=30,
        )
        if result.returncode == 0:
            return {
                "skill": skill_cat, "success": True,
                "output": f"Mail enviado a {to}: \"{subject}\"",
                "raw": result.stdout, "destructive": True, "error": None,
            }
        return {
            "skill": skill_cat, "success": False,
            "output": f"Error enviando mail: {result.stderr[:100]}",
            "raw": result.stderr, "destructive": False, "error": "send_failed",
        }
    except Exception as e:
        return {
            "skill": skill_cat, "success": False,
            "output": f"Error enviando mail: {e}",
            "raw": str(e), "destructive": False, "error": "exception",
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


def _handle_smoking(skill_category, query, skill):
    """Handle smoking tracker — log or stats."""
    try:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "smoking", str(Path(__file__).parent / "smoking.py"))
        smoking_mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(smoking_mod)

        q = query.lower()
        is_stats = any(w in q for w in [
            "cuánto", "cuanto", "estadística", "estadistica",
            "stats", "cuántos", "cuantos", "historial",
        ])
        action = "stats" if is_stats else "log"
        output = smoking_mod.format_response(action)
    except Exception as e:
        output = f"Error en smoking tracker: {e}"

    return {
        "skill": skill_category, "success": True,
        "output": output, "raw": "", "destructive": False, "error": None,
    }


def _handle_network_scanner(skill_category, query, skill):
    """Handle network scan — quick or deep based on query."""
    q = query.lower()
    is_deep = any(w in q for w in [
        "profundo", "deep", "completo", "fingerprint", "puertos", "ports",
        "servicios", "services", "detallado",
    ])
    mode = "--deep" if is_deep else "--quick"
    try:
        result = subprocess.run(
            ["python3", "/home/tenazas/runtime/device_discovery.py", mode],
            capture_output=True, text=True, timeout=SKILL_TIMEOUT if not is_deep else 600,
        )
        if result.returncode == 0:
            return {
                "skill": skill_category, "success": True,
                "output": result.stdout[:3000],
                "raw": result.stdout, "destructive": False, "error": None,
            }
        return {
            "skill": skill_category, "success": False,
            "output": f"Error en scan: {result.stderr[:200]}",
            "raw": result.stderr, "destructive": False, "error": "scan_error",
        }
    except subprocess.TimeoutExpired:
        return {
            "skill": skill_category, "success": False,
            "output": "Timeout en network scan",
            "raw": "", "destructive": False, "error": "timeout",
        }


# ── Handler and Parser Registries ───────────────────────────────

_SKILL_HANDLERS = {
    "_handle_smart_home": _handle_smart_home,
    "_handle_tts": _handle_tts,
    "_handle_morning_greeting": _handle_morning_greeting,
    "_handle_reminders": _handle_reminders,
    "_handle_email": _handle_email,
    "_handle_model_trainer": _handle_model_trainer,
    "_handle_expense": _handle_expense,
    "_handle_smoking": _handle_smoking,
    "_handle_calendar": _handle_calendar,
    "_handle_network_scanner": _handle_network_scanner,
}

_SKILL_PARSERS = {
    "_parse_weather": _parse_weather,
    "_parse_proposals": _parse_proposals,
    "_parse_infra": _parse_infra,
    "_parse_calendar": _parse_calendar,
}


# ── Main Execution Function ─────────────────────────────────────


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


# ── Multitool Model Call ────────────────────────────────────────


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
