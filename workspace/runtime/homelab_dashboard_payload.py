#!/usr/bin/env python3
"""
Homelab Dashboard Payload — Unified status for "estado tenazas" command.

Consolidates: snapshot, goals, incidents, staging, experiments,
preferences, skill gaps, error mining into a compact Telegram message.

Trigger: "estado tenazas" or "dashboard" from Telegram.

Usage:
    python3 homelab_dashboard_payload.py            # print dashboard
    python3 homelab_dashboard_payload.py --json     # JSON payload
"""

import json
from datetime import datetime, timedelta
from pathlib import Path
from runtime_utils import now
try:
    from .runtime_utils import load_json
except ImportError:
    from runtime_utils import load_json

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
LOG_FILE = BASE_DIR / "logs" / "dashboard.log"



def _log(entry: dict):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except OSError:
        pass


def _safe_import(module_name: str, func_name: str, *args, **kwargs):
    """Safely import and call a function, return None on failure."""
    try:
        import importlib
        mod = importlib.import_module(module_name)
        fn = getattr(mod, func_name)
        return fn(*args, **kwargs)
    except Exception:
        return None


def build_payload() -> dict:
    """Build the complete dashboard payload."""
    payload = {"ts": now().isoformat()}

    # 1. Homelab snapshot
    payload["snapshot"] = _safe_import("homelab_snapshot", "snapshot") or {}

    # 2. Goals status
    goals = _safe_import("homelab_goals", "evaluate_all") or []
    payload["goals"] = {
        "total": len(goals),
        "failing": [g for g in goals if g.get("status") == "fail"],
        "warning": [g for g in goals if g.get("status") == "warn"],
    }

    # 3. Incidents last 24h (from error_mining)
    error_report = _safe_import("error_mining", "get_today") or {}
    payload["errors_24h"] = {
        "total": error_report.get("total_errors", 0),
        "by_type": error_report.get("by_type", {}),
    }

    # 4. Staging pending
    candidates = load_json(STATE_DIR / "improvement_candidates.json", [])
    if not isinstance(candidates, list):
        candidates = []
    pending = [c for c in candidates if c.get("status") in ("proposed", "staged")]
    payload["staging"] = {
        "pending": len(pending),
        "items": [{"id": c["id"], "type": c["type"], "title": c.get("title", "")[:50]}
                  for c in pending[:5]],
    }

    # 5. Active experiments
    experiments = _safe_import("experiment_runner", "list_active") or []
    payload["experiments"] = {
        "active": len(experiments),
        "items": [{"id": e["id"], "name": e["name"], "type": e["type"]}
                  for e in experiments[:3]],
    }

    # 6. Top preferences
    top_skills = _safe_import("preference_learning", "get_top_skills", 3) or []
    blacklist = _safe_import("preference_learning", "get_blacklist") or []
    payload["preferences"] = {
        "top_skills": top_skills,
        "blacklisted": len(blacklist),
    }

    # 7. Skill gaps
    gaps = load_json(STATE_DIR / "skill_gaps.json", [])
    if not isinstance(gaps, list):
        gaps = []
    payload["skill_gaps"] = len(gaps)

    # 8. Last error mining
    mining_dir = STATE_DIR / "error_mining"
    last_mining = None
    if mining_dir.exists():
        files = sorted(mining_dir.glob("daily_*.json"), reverse=True)
        if files:
            report = load_json(files[0], {})
            last_mining = {
                "date": report.get("date", "?"),
                "total": report.get("total_errors", 0),
            }
    payload["last_mining"] = last_mining

    _log({"action": "payload_built"})
    return payload


def format_telegram(payload: dict = None) -> str:
    """Format dashboard as compact Telegram message (~20 lines)."""
    if payload is None:
        payload = build_payload()

    now_str = now().strftime("%H:%M %d/%m")
    lines = [f"Tenazas Dashboard ({now_str})"]
    lines.append("")

    # Snapshot summary
    snap = payload.get("snapshot", {})
    c = snap.get("containers", {})
    running = c.get("running", 0)
    total = c.get("total", 0)
    stopped = c.get("stopped", 0)
    c_ok = "OK" if stopped == 0 else f"{stopped} stopped"
    lines.append(f"Docker: {running}/{total} — {c_ok}")

    o = snap.get("ollama", {})
    lines.append(f"Ollama: {o.get('status', '?')}")

    vpn = snap.get("vpn", {})
    vpn_ok = "OK" if vpn.get("country") == "ES" else vpn.get("country", "?")
    lines.append(f"VPN: {vpn_ok}")

    # Goals
    goals = payload.get("goals", {})
    failing = goals.get("failing", [])
    warning = goals.get("warning", [])
    if failing:
        lines.append(f"Goals: {len(failing)} FAIL, {len(warning)} warn")
        for g in failing[:2]:
            lines.append(f"  ! {g.get('id', '?')}: {g.get('evidence', '')[:40]}")
    elif warning:
        lines.append(f"Goals: {len(warning)} warn")
    else:
        lines.append(f"Goals: all OK ({goals.get('total', 0)})")

    # Errors 24h
    errors = payload.get("errors_24h", {})
    err_total = errors.get("total", 0)
    if err_total > 0:
        by_type = errors.get("by_type", {})
        top_types = sorted(by_type.items(), key=lambda x: -x[1])[:2]
        type_str = ", ".join(f"{t}:{c}" for t, c in top_types)
        lines.append(f"Errores 24h: {err_total} ({type_str})")
    else:
        lines.append("Errores 24h: 0")

    # Staging
    staging = payload.get("staging", {})
    pending = staging.get("pending", 0)
    if pending > 0:
        lines.append(f"Staging: {pending} pendientes")
        for item in staging.get("items", [])[:2]:
            lines.append(f"  [{item['type']}] {item['title'][:40]}")
    else:
        lines.append("Staging: limpio")

    # Experiments
    experiments = payload.get("experiments", {})
    active = experiments.get("active", 0)
    if active > 0:
        names = ", ".join(e["name"] for e in experiments.get("items", []))
        lines.append(f"Experimentos: {active} activos ({names})")
    else:
        lines.append("Experimentos: 0")

    # Preferences
    prefs = payload.get("preferences", {})
    top = prefs.get("top_skills", [])
    bl = prefs.get("blacklisted", 0)
    if top:
        skills_str = ", ".join(f"{s[0].replace('skill:','')}" for s in top[:3])
        lines.append(f"Top skills: {skills_str}")
    if bl > 0:
        lines.append(f"Blacklisted: {bl} tipos")

    # Skill gaps
    gaps = payload.get("skill_gaps", 0)
    if gaps > 0:
        lines.append(f"Skill gaps: {gaps} detectados")

    # Last mining
    mining = payload.get("last_mining")
    if mining:
        lines.append(f"Mining: {mining['date']} ({mining['total']} errores)")

    return "\n".join(lines)


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="Homelab Dashboard")
    p.add_argument("--json", action="store_true", help="JSON output")
    args = p.parse_args()

    if args.json:
        print(json.dumps(build_payload(), indent=2, default=str))
    else:
        print(format_telegram())
