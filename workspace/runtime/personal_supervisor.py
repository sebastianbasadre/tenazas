#!/usr/bin/env python3
"""
Personal Supervisor — Unified coordination layer for Tenazas personal domains.

Routes user requests to the right module(s), combines multi-domain responses,
and maintains cross-domain awareness. Does NOT duplicate module logic.

Domains: finance, expense, jobs, improvement, overview
Each domain maps to a handler that calls the actual module API.

Usage:
  docker exec openclaw python3 /home/tenazas/runtime/personal_supervisor.py --ask "cómo vengo este mes"
  docker exec openclaw python3 /home/tenazas/runtime/personal_supervisor.py --ask "qué debería priorizar hoy"
  docker exec openclaw python3 /home/tenazas/runtime/personal_supervisor.py --status

Security: read-only from modules. Never modifies data. Writing goes through module APIs directly.
"""

import json
import re
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "logs" / "personal_supervisor.log"
IMPROVEMENT_FILE = BASE_DIR / "learning" / "daily_improvement.json"

# ── Domain definitions ──────────────────────────────────────────

DOMAINS = {
    "finance":     "Estado financiero mensual (ingresos, gastos, balance)",
    "expense":     "Gastos diarios y semanales, alertas de threshold",
    "jobs":        "Pipeline laboral, postulaciones, oportunidades",
    "improvement": "Mejora diaria del sistema, propuestas",
    "overview":    "Vision global, mezcla de dominios",
}

# ── Intent classification (keyword-based) ───────────────────────

_INTENT_PATTERNS = [
    # Finance
    ("finance", re.compile(
        r"(balance|ingreso|sueldo|cobr[eé]|finanzas|mes\s+actual|"
        r"cu[aá]nto\s+(tengo|gane|cobr[eé])|estado\s+financiero)", re.I)),
    # Expense
    ("expense", re.compile(
        r"(gast[oéa]|plata|super|nafta|almuerzo|cuánto\s+llevo|"
        r"en\s+qu[eé]\s+se\s+me|se\s+me\s+est[aá]\s+yendo|"
        r"resumen\s+de\s+hoy|gastos?\s+de\s+hoy|"
        r"esta\s+semana.*gast|categor[ií]a)", re.I)),
    # Jobs
    ("jobs", re.compile(
        r"(postul|trabajo|empleo|empresa|laboral|entrevista|"
        r"pipeline|oportunidad|screening|offer|"
        r"qu[eé]\s+empresas|busqueda|recruit)", re.I)),
    # Improvement
    ("improvement", re.compile(
        r"(mejora|propuesta|optimiz|sistema|daily\s+improvement|"
        r"qu[eé]\s+mejorar|sugerencia)", re.I)),
    # Overview (broad questions)
    ("overview", re.compile(
        r"(c[oó]mo\s+vengo|c[oó]mo\s+estoy|qu[eé]\s+deber[ií]a\s+priorizar|"
        r"resumen\s+general|estado\s+general|panorama|"
        r"brief|qu[eé]\s+tengo\s+pendiente|prioridad)", re.I)),
]


def _now():
    return datetime.now().astimezone()


def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = _now().isoformat()
    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Intent Classification ───────────────────────────────────────


def classify_intent(text: str) -> list:
    """Classify user text into one or more domains.

    Returns:
        List of domain strings, ordered by relevance. At least one.
    """
    text = text.strip()
    matched = []

    for domain, pattern in _INTENT_PATTERNS:
        if pattern.search(text):
            if domain not in matched:
                matched.append(domain)

    # If overview matched, ensure it includes sub-domains
    if "overview" in matched:
        for d in ("finance", "expense", "jobs", "improvement"):
            if d not in matched:
                matched.append(d)

    # Default to overview if nothing matched
    if not matched:
        matched = ["overview", "finance", "expense", "jobs", "improvement"]

    _log({"action": "classify", "text": text[:80], "domains": matched})
    return matched


# ── Domain Handlers ─────────────────────────────────────────────
# Each handler returns (data_dict_or_none, formatted_text_or_none)
# Handlers never raise — they return None on failure.


def _handle_finance() -> tuple:
    try:
        from runtime.finance import summary, format_summary
        s = summary("month")
        if s.get("transaction_count", 0) == 0:
            return None, None
        return s, format_summary(s)
    except Exception:
        return None, None


def _handle_expense() -> tuple:
    try:
        from runtime.expenses import daily_summary, weekly_summary, format_daily, format_weekly, check_threshold_alerts, format_alerts
        daily = daily_summary()
        weekly = weekly_summary()
        alerts = check_threshold_alerts()

        parts = []
        if daily.get("count", 0) > 0:
            parts.append(format_daily(daily))
        if weekly.get("count", 0) > 0:
            parts.append(format_weekly(weekly))
        if alerts:
            parts.append(format_alerts(alerts))

        if not parts:
            return None, None

        data = {"daily": daily, "weekly": weekly, "alerts": alerts}
        return data, "\n\n".join(parts)
    except Exception:
        return None, None


def _handle_jobs() -> tuple:
    try:
        from runtime.jobs import pipeline_summary, format_pipeline
        ps = pipeline_summary()
        if ps.get("total", 0) == 0:
            return None, None
        return ps, format_pipeline(ps)
    except Exception:
        return None, None


def _handle_improvement() -> tuple:
    try:
        with open(IMPROVEMENT_FILE) as f:
            history = json.load(f)
        if not history:
            return None, None
        latest = history[-1]
        text = (f"Mejora [{latest.get('area', '?')}]: {latest.get('proposal', 'N/A')}\n"
                f"  Impacto: {latest.get('impact', '?')} | Riesgo: {latest.get('risk', '?')}")
        return latest, text
    except (FileNotFoundError, json.JSONDecodeError, IndexError):
        return None, None


HANDLERS = {
    "finance": _handle_finance,
    "expense": _handle_expense,
    "jobs": _handle_jobs,
    "improvement": _handle_improvement,
}


# ── Core API ────────────────────────────────────────────────────


def handle_request(text: str) -> dict:
    """Process a user request through the supervisor.

    1. Classify intent into domains
    2. Call relevant handlers
    3. Combine results

    Args:
        text: user message text

    Returns:
        dict with: query, domains, results (per domain), response (combined text),
        missing (domains with no data)
    """
    domains = classify_intent(text)

    results = {}
    formatted = {}
    missing = []

    for domain in domains:
        handler = HANDLERS.get(domain)
        if not handler:
            continue
        data, text_out = handler()
        if data is not None:
            results[domain] = data
            formatted[domain] = text_out
        else:
            missing.append(domain)

    # Build combined response
    response_parts = []
    for domain in domains:
        if domain in formatted and formatted[domain]:
            response_parts.append(formatted[domain])

    combined = "\n\n---\n\n".join(response_parts) if response_parts else "Sin datos disponibles para tu consulta."

    if missing and response_parts:
        combined += f"\n\n(sin datos: {', '.join(missing)})"

    result = {
        "query": text[:200],
        "domains": domains,
        "results": results,
        "response": combined,
        "domains_with_data": list(results.keys()),
        "missing": missing,
    }

    _log({"action": "handle_request", "query": text[:80],
          "domains": domains, "with_data": list(results.keys()),
          "missing": missing})

    return result


def global_status() -> dict:
    """Get a global status across all personal domains.

    Returns:
        dict with per-domain status summaries.
    """
    status = {}

    for domain, handler in HANDLERS.items():
        data, text = handler()
        if data is not None:
            status[domain] = {"available": True, "summary": text[:100] if text else ""}
        else:
            status[domain] = {"available": False, "summary": "sin datos"}

    _log({"action": "global_status",
          "available": [d for d, s in status.items() if s["available"]]})

    return status


def format_status(status: dict) -> str:
    """Format global status for Telegram."""
    lines = ["Estado de dominios personales:"]
    icons = {"finance": "💰", "expense": "🧾", "jobs": "💼", "improvement": "🔧"}
    for domain, info in status.items():
        icon = icons.get(domain, "·")
        state = "OK" if info["available"] else "sin datos"
        lines.append(f"  {icon} {domain}: {state}")
        if info["available"] and info["summary"]:
            # First line of summary only
            first_line = info["summary"].split("\n")[0][:60]
            lines.append(f"     {first_line}")
    return "\n".join(lines)


# ── CLI ─────────────────────────────────────────────────────────


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Tenazas Personal Supervisor")
    parser.add_argument("--ask", type=str, help="Ask a question")
    parser.add_argument("--status", action="store_true", help="Global domain status")
    parser.add_argument("--classify", type=str, help="Classify intent only")
    parser.add_argument("--json", action="store_true", help="Output as JSON")

    args = parser.parse_args()

    if args.ask:
        result = handle_request(args.ask)
        if args.json:
            # Don't dump full results (too large), just metadata + response
            output = {k: v for k, v in result.items() if k != "results"}
            print(json.dumps(output, ensure_ascii=False, indent=2, default=str))
        else:
            print(result["response"])

    elif args.status:
        s = global_status()
        if args.json:
            print(json.dumps(s, ensure_ascii=False, indent=2, default=str))
        else:
            print(format_status(s))

    elif args.classify:
        domains = classify_intent(args.classify)
        print(f"Dominios: {', '.join(domains)}")

    else:
        parser.print_help()
