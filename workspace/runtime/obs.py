"""
obs.py — Observability helpers for Tenazas.

Structured JSONL logging for router decisions, skill runs, and failures.
Append-only, rotation-safe, no dependencies beyond stdlib.
"""

import json
import os
from datetime import datetime, timezone

# ── Log paths (inside runtime/logs/ which is volume-mounted) ─────
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
ROUTER_LOG = os.path.join(LOG_DIR, "router_decisions.jsonl")
SKILL_LOG = os.path.join(LOG_DIR, "skill_runs.jsonl")
FAIL_LOG = os.path.join(LOG_DIR, "chat_failures.jsonl")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def append_jsonl(path: str, row: dict) -> None:
    """Append a single JSON line to a JSONL file. Creates dirs if needed."""
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
    except Exception:
        pass  # observability must never crash the main flow


def log_router_decision(*, query: str, route: str, path: str,
                        skill: str | None = None, guardrail_hit: bool = False,
                        used_llm: bool = False, latency_ms: int = 0,
                        success: bool = True, error: str | None = None,
                        chat_id: str = "") -> None:
    """Log a routing decision."""
    append_jsonl(ROUTER_LOG, {
        "ts": utc_now_iso(),
        "chat_id": chat_id,
        "query": query[:200],
        "route": route,
        "path": path,
        "skill": skill,
        "guardrail_hit": guardrail_hit,
        "used_llm": used_llm,
        "latency_ms": latency_ms,
        "success": success,
        "error": error,
    })


def log_skill_run(*, skill: str, query: str, ok: bool, route: str,
                  result_count: int | None = None,
                  latency_ms: int = 0) -> None:
    """Log a skill execution."""
    append_jsonl(SKILL_LOG, {
        "ts": utc_now_iso(),
        "skill": skill,
        "query": query[:200],
        "ok": ok,
        "route": route,
        "result_count": result_count,
        "latency_ms": latency_ms,
    })


def log_failure(*, query: str, route: str, path: str,
                reason: str, response_preview: str = "",
                severity: str = "medium") -> None:
    """Log a failure case for future dataset improvement."""
    append_jsonl(FAIL_LOG, {
        "ts": utc_now_iso(),
        "query": query[:200],
        "route": route,
        "path": path,
        "reason": reason,
        "response_preview": response_preview[:200],
        "severity": severity,
    })
