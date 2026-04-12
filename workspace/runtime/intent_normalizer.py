"""
intent_normalizer.py
Convierte el output del finetuned_router en un NormalizedCall validado.
Usa los skill names exactos del orchestrator de Tenazas.

Flujo:
  finetuned_router.route()  →  normalize()  →  NormalizedCall
                                                     ↓
                                             orchestrator construye tool call
"""

from dataclasses import dataclass, field
from typing import Optional

# ──────────────────────────────────────────────
# NormalizedCall — resultado final
# ──────────────────────────────────────────────

@dataclass
class NormalizedCall:
    intent:        str
    skill:         str           # nombre exacto del skill: "skill:smart-home", etc.
    action:        str           # acción dentro del skill
    args:          dict
    confidence:    str           # "high" | "medium" | "low"
    source:        str           # origen del parseo
    missing_slots: list = field(default_factory=list)
    valid:         bool = True
    raw_message:   str = ""

    def to_dict(self) -> dict:
        return {
            "intent":        self.intent,
            "skill":         self.skill,
            "action":        self.action,
            "args":          self.args,
            "confidence":    self.confidence,
            "source":        self.source,
            "missing_slots": self.missing_slots,
            "valid":         self.valid,
        }

# ──────────────────────────────────────────────
# Schemas — skill names exactos del orchestrator
# ──────────────────────────────────────────────

INTENT_SCHEMAS = {

    "lights_on": {
        "skill":    "skill:smart-home",
        "action":   "on",
        "required": [],
        "optional": ["room", "brightness", "color"],
        "defaults": {"brightness": 100, "room": "all"},
    },

    "lights_off": {
        "skill":    "skill:smart-home",
        "action":   "off",
        "required": [],
        "optional": ["room"],
        "defaults": {"room": "all"},
    },

    "cinema_mode": {
        "skill":    "skill:smart-home",
        "action":   "cinema",
        "required": [],
        "optional": ["brightness"],
        "defaults": {"brightness": 10, "room": "living"},
    },

    "weather": {
        "skill":    "skill:weather",
        "action":   "current",
        "required": [],
        "optional": ["location", "days"],
        "defaults": {"location": "Buenos Aires", "days": 1},
    },

    "media_search": {
        "skill":    "skill:arr-monitor",
        "action":   "search",
        "required": [],
        "optional": ["title", "type", "season", "episode"],
        "defaults": {"type": "any"},
    },

    "infra_check": {
        "skill":    "skill:infra-monitor",
        "action":   "check",
        "required": [],
        "optional": ["target", "severity"],
        "defaults": {"target": "all", "severity": "any"},
    },

    "email": {
        "skill":    "skill:email",
        "action":   "digest",
        "required": [],
        "optional": ["count", "filter"],
        "defaults": {"count": 10},
    },

    "calendar": {
        "skill":    "skill:calendar-agenda",
        "action":   "agenda",
        "required": [],
        "optional": ["days", "filter"],
        "defaults": {"days": 2},
    },

    "self_evolve": {
        "skill":    "skill:self-evolve",
        "action":   "run",
        "required": [],
        "optional": [],
        "defaults": {},
    },

    "chat": {
        "skill":    "chat",
        "action":   "respond",
        "required": [],
        "optional": [],
        "defaults": {},
    },
}

# ──────────────────────────────────────────────
# Confianza según origen del parseo
# ──────────────────────────────────────────────

CONFIDENCE_MAP = {
    "model_json":           "high",
    "model_json_fenced":    "high",
    "model_json_extracted": "medium",
    "keyword_fallback":     "low",
    "no_match":             "low",
    "ollama_error":         "low",
}

# ──────────────────────────────────────────────
# Función principal
# ──────────────────────────────────────────────

def normalize(router_output: dict, original_message: str = "") -> Optional[NormalizedCall]:
    """
    Convierte el output de finetuned_router.route() en un NormalizedCall validado.

    Retorna None si no hay intent detectable.
    Nunca lanza excepción.
    """
    intent = router_output.get("intent")
    if not intent:
        return None

    schema = INTENT_SCHEMAS.get(intent)
    if not schema:
        return NormalizedCall(
            intent=intent,
            skill="chat",
            action="respond",
            args={},
            confidence="low",
            source=router_output.get("_parsed_by", "unknown"),
            valid=True,
            raw_message=original_message,
        )

    # Construir args: defaults base + args del modelo (solo slots permitidos)
    args = {**schema["defaults"]}
    allowed = set(schema["required"] + schema["optional"])
    model_args = router_output.get("args") or {}

    for k, v in model_args.items():
        if k in allowed and v is not None and v != "":
            args[k] = v

    # Detectar slots requeridos faltantes
    missing = [s for s in schema["required"] if s not in args]

    # Determinar confianza
    parsed_by = router_output.get("_parsed_by", "unknown")
    confidence = CONFIDENCE_MAP.get(parsed_by, "low")

    return NormalizedCall(
        intent=intent,
        skill=schema["skill"],
        action=schema["action"],
        args=args,
        confidence=confidence,
        source=parsed_by,
        missing_slots=missing,
        valid=len(missing) == 0,
        raw_message=original_message,
    )


def normalize_or_chat(router_output: dict, original_message: str = "") -> NormalizedCall:
    """
    Como normalize() pero nunca retorna None.
    Si no hay intent, retorna un NormalizedCall de chat.
    """
    result = normalize(router_output, original_message)
    if result is None:
        return NormalizedCall(
            intent="chat",
            skill="chat",
            action="respond",
            args={},
            confidence="low",
            source="no_intent",
            valid=True,
            raw_message=original_message,
        )
    return result
