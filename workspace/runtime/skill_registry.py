"""Compatibility helpers around the current skill registry.

The large `skill_executor` module remains the source of truth for now. New code
should depend on this module when it only needs registry metadata, which gives
the executor room to be split without changing callers again.
"""

from __future__ import annotations

try:
    from . import skill_executor
except ImportError:
    import skill_executor  # type: ignore


def list_skill_names() -> list[str]:
    return sorted(skill_executor.SKILL_MAP.keys())


def get_skill_definition(name: str) -> dict:
    return dict(skill_executor.SKILL_MAP.get(name, {}))


def is_destructive(name: str) -> bool:
    return bool(skill_executor.SKILL_MAP.get(name, {}).get("destructive", False))
