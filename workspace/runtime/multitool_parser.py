#!/usr/bin/env python3
"""
multitool_parser.py — XML parser + registry + policy for multitool inference.

Parses strict XML tool calls from the finetuned model, validates against
a closed allowlist, and maps tool names to orchestrator skill categories.

API:
    parse_multitool_response(text) -> {valid, tools, raw, error}
    should_use_multitool(query)    -> bool
    get_skill_category(tool_name)  -> str | None
"""

import re
import xml.etree.ElementTree as ET


# ── Tool Registry ─────────────────────────────────────────────────
# Maps XML tool name → orchestrator execution info.
# "skill_category" maps directly to SKILL_MAP keys in orchestrator.py.
# "handler"/"action" entries are for parameterized skills (smart-home).

TOOL_REGISTRY = {
    "weather":          {"skill_category": "skill:weather"},
    "email":            {"skill_category": "skill:email"},
    "calendar":         {"skill_category": "skill:calendar"},
    "infra-monitor":    {"skill_category": "skill:infra-monitor"},
    "network_scan":     {"skill_category": "skill:network-scanner"},
    "network-scanner":  {"skill_category": "skill:network-scanner"},
    "security-monitor": {"skill_category": "skill:security-monitor"},
    "arr-monitor":      {"skill_category": "skill:arr-monitor"},
    "self-evolve":      {"skill_category": "skill:self-evolve"},
    "reminders":        {"skill_category": "skill:reminders"},
    "smart-home-on":    {"skill_category": "skill:smart-home", "action": "on"},
    "smart-home-off":   {"skill_category": "skill:smart-home", "action": "off"},
    "smart-home-cinema": {"skill_category": "skill:smart-home", "action": "cinema"},
    "smart-home-relax": {"skill_category": "skill:smart-home", "action": "relax"},
    "smart-home-work":  {"skill_category": "skill:smart-home", "action": "work"},
    "tts":              {"skill_category": "skill:tts"},
}

ALLOWED_TOOLS = frozenset(TOOL_REGISTRY.keys())
MAX_TOOLS = 3


def get_skill_category(tool_name: str) -> str | None:
    """Return the orchestrator skill_category for a tool name, or None."""
    entry = TOOL_REGISTRY.get(tool_name)
    return entry["skill_category"] if entry else None


def get_tool_action(tool_name: str) -> str | None:
    """Return the action override for parameterized tools (smart-home), or None."""
    entry = TOOL_REGISTRY.get(tool_name)
    return entry.get("action") if entry else None


# ── XML Parser ────────────────────────────────────────────────────


def parse_multitool_response(text: str) -> dict:
    """Parse model output into validated tool list.

    Expects strict XML: <tools><tool>name</tool>...</tools>
    Rejects anything outside that format.

    Returns:
        {
            "valid": bool,
            "tools": list[str],   # deduplicated, ordered, max 3
            "raw": str,           # original text
            "error": str | None,
        }
    """
    text = text.strip()
    result = {"valid": False, "tools": [], "raw": text, "error": None}

    if not text:
        result["error"] = "empty_response"
        return result

    # Extract <tools>...</tools> block — allow whitespace/newlines around it
    match = re.search(r"<tools>(.*?)</tools>", text, re.DOTALL)
    if not match:
        result["error"] = "no_tools_block"
        return result

    # Verify there's no significant text outside the <tools> block
    before = text[:match.start()].strip()
    after = text[match.end():].strip()
    if before or after:
        result["error"] = "text_outside_xml"
        return result

    xml_content = match.group(0)

    # Parse with ElementTree (safe XML parsing)
    try:
        root = ET.fromstring(xml_content)
    except ET.ParseError as e:
        result["error"] = f"xml_parse_error: {e}"
        return result

    if root.tag != "tools":
        result["error"] = f"unexpected_root: {root.tag}"
        return result

    # Extract tool names
    tools = []
    seen = set()
    for child in root:
        if child.tag != "tool":
            result["error"] = f"unexpected_element: {child.tag}"
            return result
        name = (child.text or "").strip().lower()
        if not name:
            continue
        if name not in ALLOWED_TOOLS:
            result["error"] = f"unknown_tool: {name}"
            return result
        # Deduplicate preserving order
        if name not in seen:
            tools.append(name)
            seen.add(name)

    # Enforce max
    if len(tools) > MAX_TOOLS:
        result["error"] = f"too_many_tools: {len(tools)}"
        return result

    result["valid"] = True
    result["tools"] = tools
    return result


# ── Multitool Policy ──────────────────────────────────────────────
# Conservative: only use multitool path when the query clearly asks
# for multiple actions across different domains.

# Connectors that signal multiple requests
_MULTI_CONNECTORS = re.compile(
    r"\b(y\s+(?:tambi[eé]n|de\s+paso|despu[eé]s|adem[aá]s)|"
    r"adem[aá]s|tambi[eé]n|de\s+paso|aparte)\b",
    re.IGNORECASE,
)

# Domain keyword sets — if query mentions 2+ different domains, it's multi
_DOMAIN_KEYWORDS = {
    "weather": {"clima", "temperatura", "tiempo", "lluvia", "weather", "frio", "calor"},
    "email": {"email", "emails", "correo", "correos", "mail", "mails", "inbox"},
    "calendar": {"agenda", "calendario", "eventos", "evento", "reunion", "reunión"},
    "infra": {"containers", "container", "docker", "infra", "infraestructura"},
    "network": {"red", "nmap", "dispositivos", "scanner", "escanear"},
    "security": {"seguridad", "security", "puertos", "expuestos", "firewall", "credenciales", "ssh"},
    "arr": {"sonarr", "radarr", "lidarr", "torrent", "descargas", "arr"},
    "lights": {"luces", "luz", "hue", "cinema", "cine", "relax", "prender", "apagar", "encender"},
    "tts": {"audio", "voz", "tts", "decí", "deci", "hablá", "habla"},
    "self-evolve": {"propuestas", "mejoras", "evolve"},
    "reminders": {"recordatorio", "recordatorios", "reminder", "avisame", "avísame"},
}


def should_use_multitool(query: str) -> bool:
    """Decide if a query should be routed through multitool inference.

    Returns True only when there are clear signals of multiple actions:
    - A multi-action connector ("y además", "de paso", "también") AND
      mention of 2+ different domains
    - OR mention of 3+ different domains (even without connector)

    Conservative: when in doubt, returns False (use normal single-skill path).
    """
    query_lower = query.lower()
    words = set(re.findall(r"\w+", query_lower))

    # Count how many different domains are mentioned
    matched_domains = set()
    for domain, keywords in _DOMAIN_KEYWORDS.items():
        if words & keywords:
            matched_domains.add(domain)

    # 3+ domains → always multitool
    if len(matched_domains) >= 3:
        return True

    # 2 domains + connector → multitool
    if len(matched_domains) >= 2 and _MULTI_CONNECTORS.search(query_lower):
        return True

    # 2 domains + simple "y" between them (e.g. "clima y agenda")
    if len(matched_domains) >= 2 and re.search(r"\by\b", query_lower):
        return True

    return False
