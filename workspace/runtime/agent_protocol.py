"""
agent_protocol.py -- Typed contracts for the multi-agent system.

Defines the shared data types that all domain agents and the coordinator
use to communicate. Zero runtime imports to avoid circular dependencies.

Exports:
    Observation          - What an agent observed (domain status, findings)
    Recommendation       - What an agent suggests doing (action + risk)
    validate_agent_module - Duck-type check for agent module interface
    VALID_ROLES          - Allowed model routing roles
    VALID_RISK_LEVELS    - Allowed risk classifications
"""

from dataclasses import dataclass, field, asdict
from datetime import datetime


# ──────────────────────────────────────────────
# Constants
# ──────────────────────────────────────────────

VALID_ROLES = ("fast", "reason", "code", "heavy", "vision", "embed")

VALID_RISK_LEVELS = ("low", "medium", "high", "critical")


# ──────────────────────────────────────────────
# Observation — what an agent found
# ──────────────────────────────────────────────

@dataclass
class Observation:
    agent_name: str
    domain: str
    summary: str
    confidence: float = 0.0
    details: dict = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().astimezone().isoformat()
        # Clamp confidence to 0.0-1.0
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def to_dict(self) -> dict:
        return asdict(self)


# ──────────────────────────────────────────────
# Recommendation — what an agent suggests
# ──────────────────────────────────────────────

@dataclass
class Recommendation:
    action: str
    target: str
    risk_level: str = "low"
    requires_approval: bool = True
    rationale: str = ""

    def __post_init__(self):
        if self.risk_level not in VALID_RISK_LEVELS:
            self.risk_level = "low"

    def to_dict(self) -> dict:
        return asdict(self)


# ──────────────────────────────────────────────
# Module validation — duck-type interface check
# ──────────────────────────────────────────────

def validate_agent_module(module) -> tuple:
    """Validate that a module conforms to the agent interface.

    Returns (ok: bool, errors: list of str).
    Checks for: owns(topic), handle(query), PREFERRED_ROLE constant.
    """
    errors = []

    if not (hasattr(module, "owns") and callable(module.owns)):
        errors.append("missing owns(topic) function")

    if not (hasattr(module, "handle") and callable(module.handle)):
        errors.append("missing handle(query) function")

    if not hasattr(module, "PREFERRED_ROLE"):
        errors.append("missing PREFERRED_ROLE constant")
    elif getattr(module, "PREFERRED_ROLE") not in VALID_ROLES:
        errors.append(f"invalid PREFERRED_ROLE: {getattr(module, 'PREFERRED_ROLE')}")

    return (len(errors) == 0, errors)
