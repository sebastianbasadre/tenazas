"""
response_composer.py
Composes natural language responses from structured skill data.

Separates DATA (what the skill returns) from PRESENTATION (what the user sees).
Each skill has a composer function that formats structured data into text.
No LLM needed — deterministic, fast, consistent.
"""

import json
import logging
import re
from datetime import datetime

log = logging.getLogger("response_composer")

# WMO weather interpretation codes
_WMO_CODES = {
    0: "despejado", 1: "mayormente despejado",
    2: "parcialmente nublado", 3: "nublado",
    45: "niebla", 48: "niebla con escarcha",
    51: "llovizna leve", 53: "llovizna", 55: "llovizna intensa",
    61: "lluvia leve", 63: "lluvia", 65: "lluvia intensa",
    71: "nieve leve", 73: "nieve", 75: "nieve intensa",
    80: "chubascos leves", 81: "chubascos", 82: "chubascos intensos",
    95: "tormenta", 96: "tormenta con granizo",
}

# Codes that mean "bring umbrella"
_RAIN_CODES = {51, 53, 55, 61, 63, 65, 80, 81, 82, 95, 96}
_COLD_THRESHOLD = 15  # below this → suggest jacket


# ── Weather ──────────────────────────────────────────────────────

def parse_weather_structured(raw: str) -> dict:
    """Parse Open-Meteo JSON into structured data.

    Returns:
        {
            "ok": True/False,
            "source": "weather",
            "data": {
                "temp_c": float,
                "condition": str,
                "condition_code": int,
                "wind_kmh": float,
                "is_raining": bool,
                "is_cold": bool,
            },
            "error": str or None,
        }
    """
    try:
        api = json.loads(raw)
        cw = api.get("current_weather", {})
        code = cw.get("weathercode", -1)
        temp = cw.get("temperature", 0)
        return {
            "ok": True,
            "source": "weather",
            "data": {
                "temp_c": temp,
                "condition": _WMO_CODES.get(code, f"codigo {code}"),
                "condition_code": code,
                "wind_kmh": cw.get("windspeed", 0),
                "is_raining": code in _RAIN_CODES,
                "is_cold": temp < _COLD_THRESHOLD,
            },
            "error": None,
        }
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        return {
            "ok": False,
            "source": "weather",
            "data": {},
            "error": str(e),
        }


def compose_weather(result: dict, query: str = "") -> str:
    """Compose weather response from structured data."""
    if not result.get("ok"):
        return "No pude obtener el clima ahora. Intentá en un rato."

    d = result["data"]
    temp = d["temp_c"]
    condition = d["condition"]
    wind = d["wind_kmh"]

    # Base response
    parts = [f"{temp}°C, {condition}, viento {wind} km/h."]

    # Actionable advice only when relevant
    if d["is_raining"]:
        parts.append("Llevate paraguas.")
    if d["is_cold"]:
        parts.append("Abrigate.")

    return " ".join(parts)


# ── Gmail ────────────────────────────────────────────────────────

def _classify_email_query(query: str) -> str:
    """Classify what type of email query this is."""
    q = query.lower()
    # Search by contact/sender
    contact_patterns = [
        r"(mail|email|correo)\w*\s+(de|from)\s+\w+",
        r"(ten[eé]s|hay|lleg[oó])\s+.*(mail|email|correo)\w*\s+de\s+",
        r"de\s+\w+.*(mail|email|correo)",
        r"(escribi[oó]|mand[oó]|envi[oó])\s+\w+",
    ]
    for p in contact_patterns:
        if re.search(p, q):
            return "search_contact"
    # Count
    if re.search(r"cu[aá]nto|cuantos|how many", q):
        return "count"
    # Latest
    if re.search(r"[uú]ltimo|latest|reciente|nuevo", q):
        return "latest"
    # Important
    if re.search(r"importante|urgente|important", q):
        return "recent_important"
    return "recent_important"


def _extract_contact_name(query: str) -> str | None:
    """Extract contact name from query like 'mail de Carolina'."""
    m = re.search(
        r"(?:mail|email|correo|mails|emails)\w*\s+de\s+(\w+)", query, re.IGNORECASE
    )
    if m:
        return m.group(1)
    m = re.search(
        r"de\s+(\w+).*(?:mail|email|correo)", query, re.IGNORECASE
    )
    if m:
        return m.group(1)
    return None


def parse_gmail_structured(raw: str) -> dict:
    """Parse himalaya pipe-delimited table into structured data.

    Returns:
        {
            "ok": True/False,
            "source": "gmail",
            "data": {
                "emails": [{"id", "flags", "subject", "from", "date", "unread"}],
                "total": int,
                "unread": int,
            },
            "error": str or None,
        }
    """
    if not raw or not raw.strip():
        return {
            "ok": True, "source": "gmail",
            "data": {"emails": [], "total": 0, "unread": 0},
            "error": None,
        }

    lines = raw.strip().split("\n")

    # Find column positions from header separator line (|--------|------|...)
    col_ranges = []
    for line in lines:
        if "|" in line and all(c in "-| " for c in line):
            pos = 0
            for segment in line.split("|"):
                if segment:
                    start = pos
                    end = pos + len(segment)
                    col_ranges.append((start, end))
                pos += len(segment) + 1  # +1 for the |
            break

    emails = []
    for line in lines:
        if "|" not in line:
            continue
        # Skip header and separator
        if all(c in "-| " for c in line):
            continue

        if col_ranges:
            # Use column positions for precise extraction
            cells = []
            parts = line.split("|")
            # parts[0] is before first |, parts[-1] is after last |
            inner = parts[1:-1] if len(parts) > 2 else parts
            cells = [p.strip() for p in inner]
        else:
            # Fallback: simple split
            parts = [p.strip() for p in line.split("|")]
            cells = [p for p in parts if p]

        if not cells or cells[0] in ("ID", "id"):
            continue

        if len(cells) >= 5:
            eid = cells[0]
            flags = cells[1]
            subject = cells[2]
            sender = cells[3]
            date = cells[4]
        elif len(cells) >= 4:
            eid = cells[0]
            flags = cells[1]
            subject = cells[2]
            sender = cells[3]
            date = ""
        else:
            continue

        # Clean sender
        for sep in [" vía ", " via "]:
            if sep in sender:
                sender = sender.split(sep)[0]

        emails.append({
            "id": eid,
            "flags": flags,
            "subject": subject[:100],
            "from": sender,
            "date": date,
            "unread": "*" in flags,
        })

    return {
        "ok": True,
        "source": "gmail",
        "data": {
            "emails": emails,
            "total": len(emails),
            "unread": sum(1 for e in emails if e["unread"]),
        },
        "error": None,
    }


def compose_gmail(result: dict, query: str = "") -> str:
    """Compose gmail response from structured data."""
    if not result.get("ok"):
        return "No pude consultar los emails ahora."

    data = result["data"]
    emails = data["emails"]
    total = data["total"]
    unread = data["unread"]
    query_type = _classify_email_query(query)

    if not emails:
        return "No hay emails en la bandeja."

    # Search by contact
    if query_type == "search_contact":
        contact = _extract_contact_name(query)
        if contact:
            matches = [e for e in emails
                       if contact.lower() in e["from"].lower()
                       or contact.lower() in e["subject"].lower()]
            if not matches:
                return f"No veo mails recientes de {contact}."
            if len(matches) == 1:
                e = matches[0]
                return f"Sí, 1 mail de {e['from']}: \"{e['subject']}\""
            lines = [f"{len(matches)} mails de {contact}:"]
            for e in matches[:3]:
                marker = ">> " if e["unread"] else "   "
                lines.append(f"{marker}\"{e['subject']}\" — {e['from']}")
            return "\n".join(lines)
        # No contact extracted, fall through to recent
        query_type = "recent_important"

    # Count
    if query_type == "count":
        parts = [f"{total} emails recientes"]
        if unread:
            parts[0] += f" ({unread} sin leer)"
        return parts[0] + "."

    # Latest
    if query_type == "latest":
        e = emails[0]
        status = " (sin leer)" if e["unread"] else ""
        return f"Último mail: \"{e['subject']}\" de {e['from']}{status}"

    # Recent important (default): show unread first, max 5
    show = sorted(emails, key=lambda e: (not e["unread"],))[:5]
    header = f"{total} emails recientes"
    if unread:
        header += f" ({unread} sin leer)"

    lines = [header]
    for e in show:
        marker = ">> " if e["unread"] else "   "
        lines.append(f"{marker}{e['subject']}")
        lines.append(f"     {e['from']}")
    return "\n".join(lines)


# ── Calendar ─────────────────────────────────────────────────────

def _classify_calendar_query(query: str) -> str:
    """Classify calendar query type."""
    q = query.lower()
    if re.search(r"\bma[nñ]ana\b", q):
        return "tomorrow"
    if re.search(r"\bpr[oó]ximo\b|\bsiguiente\b|\bnext\b", q):
        return "next_event"
    if re.search(r"\bcu[aá]nto|\bcuantos\b|\bhow many\b", q):
        if re.search(r"\bma[nñ]ana\b", q):
            return "count_tomorrow"
        return "count_today"
    if re.search(r"\ba las\s+\d", q):
        return "time_lookup"
    # Default: today
    return "today"


def parse_calendar_structured(raw: str) -> dict:
    """Parse gcal.py output (timestamp | title) into structured data.

    Returns:
        {
            "ok": True/False,
            "source": "calendar",
            "data": {
                "events": [{"title", "start", "date", "time", "day_label"}],
                "total": int,
            },
            "error": str or None,
        }
    """
    if not raw or not raw.strip():
        return {
            "ok": True, "source": "calendar",
            "data": {"events": [], "total": 0},
            "error": None,
        }

    # "No hay eventos" messages from gcal.py
    if "no hay eventos" in raw.lower():
        return {
            "ok": True, "source": "calendar",
            "data": {"events": [], "total": 0},
            "error": None,
        }

    today = datetime.now().date()
    events = []
    for line in raw.strip().splitlines():
        if "|" not in line:
            continue
        ts_str, title = line.split("|", 1)
        ts_str = ts_str.strip()
        title = title.strip()
        try:
            dt = datetime.fromisoformat(ts_str)
            d = dt.date()
            delta = (d - today).days
            if delta == 0:
                day_label = "Hoy"
            elif delta == 1:
                day_label = "Mañana"
            elif delta == -1:
                day_label = "Ayer"
            else:
                day_label = d.strftime("%a %d/%m")
            events.append({
                "title": title,
                "start": ts_str,
                "date": d.isoformat(),
                "time": dt.strftime("%H:%M"),
                "day_label": day_label,
            })
        except (ValueError, AttributeError):
            events.append({
                "title": title or line.strip(),
                "start": ts_str,
                "date": "",
                "time": "",
                "day_label": "",
            })

    return {
        "ok": True,
        "source": "calendar",
        "data": {"events": events, "total": len(events)},
        "error": None,
    }


def compose_calendar(result: dict, query: str = "") -> str:
    """Compose calendar response from structured data."""
    if not result.get("ok"):
        return "No pude consultar el calendario ahora."

    events = result["data"]["events"]
    query_type = _classify_calendar_query(query)

    if not events:
        if query_type == "tomorrow" or query_type == "count_tomorrow":
            return "Mañana no veo eventos en calendario."
        if query_type == "today" or query_type == "count_today":
            return "Hoy no tenés eventos."
        return "No hay eventos próximos."

    # Filter by day
    today_events = [e for e in events if e["day_label"] == "Hoy"]
    tomorrow_events = [e for e in events if e["day_label"] == "Mañana"]

    if query_type == "tomorrow":
        show = tomorrow_events
        if not show:
            return "Mañana no veo eventos en calendario."
        label = "Mañana"
    elif query_type == "next_event":
        e = events[0]
        return f"Tu próximo evento es {e['title']} {e['day_label']} a las {e['time']}."
    elif query_type == "count_today":
        n = len(today_events)
        if n == 0:
            return "Hoy no tenés eventos."
        return f"Hoy tenés {n} evento{'s' if n > 1 else ''}."
    elif query_type == "count_tomorrow":
        n = len(tomorrow_events)
        if n == 0:
            return "Mañana no tenés eventos."
        return f"Mañana tenés {n} evento{'s' if n > 1 else ''}."
    elif query_type == "time_lookup":
        # Extract hour from query
        m = re.search(r"a las\s+(\d{1,2})", query.lower())
        if m:
            hour = int(m.group(1))
            matches = [e for e in events
                       if e["time"] and int(e["time"].split(":")[0]) == hour]
            if matches:
                e = matches[0]
                return f"Sí, tenés {e['title']} a las {e['time']} ({e['day_label']})."
            return f"No veo nada agendado a las {hour}:00."
        show = today_events or events
        label = "Hoy"
    else:
        # today (default)
        show = today_events if today_events else events
        label = "Hoy" if today_events else "Próximos"

    # Format event list
    if len(show) == 1:
        e = show[0]
        return f"{label} tenés {e['title']} a las {e['time']}."

    lines = [f"{label} tenés {len(show)} eventos:"]
    for e in show[:5]:
        lines.append(f"  {e['time']} — {e['title']}")
    return "\n".join(lines)


# ── Dispatcher ───────────────────────────────────────────────────

# Maps source → (parser, composer)
_COMPOSERS = {
    "weather": (parse_weather_structured, compose_weather),
    "gmail": (parse_gmail_structured, compose_gmail),
    "calendar": (parse_calendar_structured, compose_calendar),
}


def compose_skill_response(source: str, raw: str, query: str = "") -> str | None:
    """Main entry point. Returns composed text or None if source not supported.

    Usage:
        text = compose_skill_response("weather", raw_json, "como esta el clima")
        if text is not None:
            # use composed response
        else:
            # fallback to legacy humanize
    """
    entry = _COMPOSERS.get(source)
    if not entry:
        return None

    parser, composer = entry
    structured = parser(raw)

    log.info("compose source=%s ok=%s query=%r",
             source, structured.get("ok"), query[:80] if query else "")

    return composer(structured, query)
