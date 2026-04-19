#!/usr/bin/env python3
"""
autofix_tiers.py — Tier classification and regression detection.

Reads health_sweep SQLite to classify probe failures into tiers.
"""

import json
import sqlite3
import time
from pathlib import Path
from urllib.request import urlopen

HOMELAB_OLLAMA = "http://192.168.2.6:11434"

_IN_CONTAINER = Path("/home/tenazas").exists()
STATE_DIR = Path("/home/tenazas/.openclaw/state") if _IN_CONTAINER else Path(__file__).parent.parent / "state"
SWEEP_DB = STATE_DIR / "health_sweeps.sqlite"


# ── Regression detection ────────────────────────────────────────

def get_probe_history(probe: str, n: int = 20) -> list[dict]:
    """Get last N sweep results for a probe from SQLite."""
    try:
        conn = sqlite3.connect(str(SWEEP_DB))
        rows = conn.execute(
            "SELECT ts, status, latency_ms, detail_json FROM sweeps "
            "WHERE probe = ? ORDER BY id DESC LIMIT ?",
            (probe, n)).fetchall()
        conn.close()
        return [{"ts": r[0], "status": r[1], "latency_ms": r[2],
                 "detail": json.loads(r[3]) if r[3] else {}} for r in rows]
    except Exception:
        return []


def get_sweep_count() -> int:
    """Get total number of sweeps in history."""
    try:
        conn = sqlite3.connect(str(SWEEP_DB))
        row = conn.execute("SELECT COUNT(DISTINCT ts) FROM sweeps").fetchone()
        conn.close()
        return row[0] if row else 0
    except Exception:
        return 0


def get_latest_score() -> int | None:
    """Get latest overall health score."""
    try:
        conn = sqlite3.connect(str(SWEEP_DB))
        row = conn.execute("SELECT overall FROM scores ORDER BY id DESC LIMIT 1").fetchone()
        conn.close()
        return row[0] if row else None
    except Exception:
        return None


def classify_regression(probe: str) -> str:
    """Classify probe failure type.

    Returns:
        'new'         — first time failing
        'regression'  — was ok, now failing
        'chronic'     — failing > 5 consecutive sweeps
        'flapping'    — alternating ok/fail
        'gradual'     — latency increasing (performance degradation)
        'unknown'     — not enough data
    """
    history = get_probe_history(probe, 20)
    if not history:
        return "unknown"

    statuses = [h["status"] for h in history]

    if statuses[0] == "ok":
        return "unknown"  # currently ok

    # Count consecutive failures
    consecutive = 0
    for s in statuses:
        if s != "ok":
            consecutive += 1
        else:
            break

    if consecutive >= 5:
        return "chronic"

    # Check if ever ok
    if "ok" not in statuses:
        return "new"

    # Flapping detection
    if consecutive == 1:
        transitions = sum(1 for i in range(1, len(statuses))
                          if statuses[i] != statuses[i - 1])
        if transitions > len(statuses) * 0.5:
            return "flapping"

    # Gradual degradation (latency trend)
    latencies = [h.get("latency_ms", 0) for h in history if h.get("latency_ms") and h["latency_ms"] > 0]
    if len(latencies) >= 5:
        recent = sum(latencies[:3]) / 3
        older = sum(latencies[-3:]) / 3
        if older > 0 and recent / older > 2.0:
            return "gradual"

    return "regression"


# ── Expected degradation filter ─────────────────────────────────

def check_condition(condition: str) -> bool:
    """Evaluate a condition string."""
    if condition == "homelab_ollama_alive":
        try:
            resp = urlopen(f"{HOMELAB_OLLAMA}/api/tags", timeout=3)
            return resp.status == 200
        except Exception:
            return False
    return False


def should_skip(probe: str, status: str, config: dict) -> tuple[bool, str]:
    """Check if probe should be skipped.

    Returns (skip, reason).
    """
    if status == "ok":
        return True, "ok"

    # Expected degradations
    for deg in config.get("expected_degradations", []):
        if deg["probe"] == probe:
            cond = deg.get("condition", "")
            if not cond or check_condition(cond):
                return True, f"expected: {deg.get('reason', 'configured')}"

    # Never existed
    if probe in config.get("never_existed", []):
        return True, "never_existed"

    return False, ""


def classify_tier(probe: str, detail: dict, config: dict) -> dict:
    """Classify a failing probe into a tier.

    Returns:
        {tier, reason, regression, action_hint}
    """
    regression = classify_regression(probe)

    # Chronic → Tier 3 (probably config, not code)
    if regression == "chronic":
        return {"tier": 3, "reason": "chronic failure (>5 sweeps)",
                "regression": regression, "action_hint": "human review needed"}

    # Flapping → skip (timing/network, not a bug)
    if regression == "flapping":
        return {"tier": 0, "reason": "flapping — intermittent, not actionable",
                "regression": regression, "action_hint": "skip"}

    # Gradual → Tier 3 (performance, needs investigation)
    if regression == "gradual":
        return {"tier": 3, "reason": "gradual degradation (latency trend)",
                "regression": regression, "action_hint": "performance investigation"}

    # Check if Tier 1 can handle it
    tier1_probes = {
        "chromadb", "prometheus", "grafana", "piper", "vpn",
        "containers", "ollama_gpu", "ollama_vram", "metrics_exporter",
        "grafana_cache", "chromadb_frozen",
    }
    if probe in tier1_probes:
        return {"tier": 1, "reason": f"ops action available for {probe}",
                "regression": regression, "action_hint": "restart or pull"}

    # Has stacktrace → Tier 2 candidate
    msg = detail.get("message", "")
    if any(k in msg for k in ["Traceback", "Error", "Exception", "error"]):
        return {"tier": 2, "reason": "error with potential code fix",
                "regression": regression, "action_hint": "aider fix"}

    # Default → Tier 3
    return {"tier": 3, "reason": "no automated fix available",
            "regression": regression, "action_hint": "human review"}


def get_latest_failures() -> list[dict]:
    """Get failing probes from most recent sweep."""
    try:
        conn = sqlite3.connect(str(SWEEP_DB))
        row = conn.execute(
            "SELECT ts FROM sweeps ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return []
        ts = row[0]
        rows = conn.execute(
            "SELECT probe, status, latency_ms, detail_json FROM sweeps "
            "WHERE ts = ? AND status != 'ok'", (ts,)).fetchall()
        conn.close()
        return [{"probe": r[0], "status": r[1], "latency_ms": r[2],
                 "detail": json.loads(r[3]) if r[3] else {}} for r in rows]
    except Exception:
        return []
