"""
routes_contract.py — Fuente de verdad para labels de routing en Tenazas.

REGLA: Cualquier módulo que necesite labels de routing debe importar de acá.
NO duplicar VALID_ROUTES en otros archivos.

Ver: /home/tenazas/runtime/INTERFACE_CONTRACT.md
"""

from __future__ import annotations

# ─────────────────────────────────────────
# Labels oficiales
# ─────────────────────────────────────────

VALID_ROUTES: set[str] = {
    "weather",
    "calendar",
    "weather_calendar",
    "lights",
    "network_scan",
    "infra",
    "tts",
    "arr_monitor",
    "none",
}

# ─────────────────────────────────────────
# Aliases deprecados → label oficial
# ─────────────────────────────────────────

DEPRECATED_ALIASES: dict[str, str] = {
    "network-scanner": "network_scan",
    "network_scanner": "network_scan",
    "smart-home-on": "lights",
    "smart-home-off": "lights",
    "smart-home": "lights",
    "infra-monitor": "infra",
    "infra_monitor": "infra",
    "arr-monitor": "arr_monitor",
    "chat": "none",
}

# ─────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────


def is_valid_route(route: str) -> bool:
    """Devuelve True si el route es un label oficial."""
    return route in VALID_ROUTES


def normalize_route(route: str) -> str:
    """
    Normaliza un route a su label oficial.
    - Si es válido, lo devuelve tal cual.
    - Si es un alias deprecado, devuelve el label oficial.
    - Si es desconocido, devuelve 'none'.
    """
    if not route or not isinstance(route, str):
        return "none"

    route = route.strip().lower()

    if route in VALID_ROUTES:
        return route

    if route in DEPRECATED_ALIASES:
        import logging
        logging.getLogger("tenazas.routes").warning(
            "Label deprecado '%s' → usando '%s'",
            route, DEPRECATED_ALIASES[route],
        )
        return DEPRECATED_ALIASES[route]

    return "none"


def assert_valid_route(route: str) -> str:
    """
    Como normalize_route pero lanza ValueError si el route
    no es válido ni tiene alias. Para usar en tests y validaciones estrictas.
    """
    normalized = normalize_route(route)
    if normalized == "none" and route != "none":
        raise ValueError(
            f"Route desconocido: '{route}'. "
            f"Válidos: {sorted(VALID_ROUTES)}"
        )
    return normalized
