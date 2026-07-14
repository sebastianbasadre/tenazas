#!/usr/bin/env python3
"""
homelab_goals.py
Persistent homelab goals — evaluated against real system state.

Goals persist in state/homelab_goals.json. Each goal has:
  id, desc, priority, status (ok/warn/fail), last_checked, evidence.

Evaluators check real state (Docker, Ollama, RAM, VPN, router metrics)
and update status accordingly. proactive_planner.py imports this to
prioritize suggestions based on failing goals.

Usage:
  python3 homelab_goals.py --status     # show all goals
  python3 homelab_goals.py --evaluate   # evaluate + update
"""

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from runtime_config import OLLAMA_GPU_URL
except ImportError:
    from workspace.runtime.runtime_config import OLLAMA_GPU_URL

RUNTIME = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state")
GOALS_PATH = STATE_DIR / "homelab_goals.json"
LOG_PATH = RUNTIME / "logs" / "homelab_goals.log"
OLLAMA_URL = OLLAMA_GPU_URL


DEFAULT_GOALS = [
    {"id": "ollama_available", "desc": "Ollama siempre disponible", "priority": 1},
    {"id": "gemma_warm", "desc": "Gemma 12B caliente en VRAM", "priority": 2},
    {"id": "ram_healthy", "desc": "RAM NAS < 85%", "priority": 3},
    {"id": "vpn_healthy", "desc": "VPN activa y en ES", "priority": 4},
    {"id": "containers_healthy", "desc": "0 containers unhealthy", "priority": 5},
    {"id": "router_accuracy", "desc": "Router accuracy > 85%", "priority": 6},
]
IGNORED_CONTAINER_HEALTH = {
    "hireswarm-agent-server",
    "hireswarm-inngest",
}


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str, ensure_ascii=False) + "\n")


def _load_goals() -> list:
    """Load goals from state file, initializing with defaults if missing."""
    try:
        if GOALS_PATH.exists():
            goals = json.loads(GOALS_PATH.read_text())
            if isinstance(goals, list) and goals:
                return goals
    except Exception:
        pass

    # Initialize with defaults
    goals = []
    for g in DEFAULT_GOALS:
        goals.append({
            **g,
            "status": "unknown",
            "last_checked": None,
            "evidence": "",
        })
    _save_goals(goals)
    return goals


def _save_goals(goals: list):
    GOALS_PATH.parent.mkdir(parents=True, exist_ok=True)
    GOALS_PATH.write_text(json.dumps(goals, indent=2, ensure_ascii=False, default=str))


# ── Evaluators ─────────────────────────────────────────────────

def _eval_ollama_available() -> tuple[str, str]:
    """Check if Ollama endpoint responds."""
    try:
        resp = urlopen(Request(f"{OLLAMA_URL}/api/tags"), timeout=5)
        data = json.loads(resp.read())
        n = len(data.get("models", []))
        return "ok", f"Ollama online, {n} models available"
    except Exception as e:
        return "fail", f"Ollama offline: {e}"


def _eval_gemma_warm() -> tuple[str, str]:
    """Check if gemma3:12b is loaded in VRAM."""
    try:
        resp = urlopen(Request(f"{OLLAMA_URL}/api/ps"), timeout=5)
        ps = json.loads(resp.read())
        models = ps.get("models", [])
        names = [m.get("name", "") for m in models]
        for model in models:
            name = model.get("name", "")
            if "gemma3:12b" not in name:
                continue
            size_vram = int(model.get("size_vram") or 0)
            if size_vram > 0:
                size_gb = round(size_vram / 1e9, 1)
                return "ok", f"gemma3:12b loaded in VRAM ({size_gb} GB)"
            return "warn", "gemma3:12b loaded but not in VRAM"
        return "warn", f"gemma3:12b not in VRAM (loaded: {', '.join(names) or 'none'})"
    except Exception as e:
        return "fail", f"Cannot check: {e}"


def _eval_ram_healthy() -> tuple[str, str]:
    """Check NAS RAM < 85%."""
    try:
        r = subprocess.run(["cat", "/proc/meminfo"], capture_output=True, text=True, timeout=5)
        meminfo = {}
        for line in r.stdout.splitlines():
            parts = line.split(":")
            if len(parts) == 2:
                meminfo[parts[0].strip()] = int(parts[1].strip().split()[0])
        total = meminfo.get("MemTotal", 1)
        avail = meminfo.get("MemAvailable", 0)
        pct = round((total - avail) / total * 100)
        if pct < 85:
            return "ok", f"RAM at {pct}%"
        return "fail", f"RAM at {pct}% (threshold: 85%)"
    except FileNotFoundError:
        return "unknown", "Skipped: /proc/meminfo unavailable on this host"
    except Exception as e:
        return "warn", f"Cannot check RAM: {e}"


def _eval_vpn_healthy() -> tuple[str, str]:
    """Check VPN is up and country is ES."""
    try:
        r = subprocess.run(
            ["docker", "ps", "--filter", "name=gluetun", "--format", "{{.Status}}"],
            capture_output=True, text=True, timeout=5,
        )
        if "Up" not in r.stdout:
            return "fail", "gluetun container not running"

        try:
            resp = urlopen(Request("http://localhost:8000/v1/publicip/country"), timeout=5)
            data = json.loads(resp.read())
            country = data.get("country", "")
            if country.upper() in ("ES", "SPAIN", "ESPANA"):
                return "ok", f"VPN active, country: {country}"
            return "warn", f"VPN active but country is {country} (expected ES)"
        except Exception:
            return "warn", "VPN up but cannot verify country"
    except FileNotFoundError:
        return "unknown", "Skipped: docker unavailable on this host"
    except Exception as e:
        return "fail", f"VPN check failed: {e}"


def _eval_containers_healthy() -> tuple[str, str]:
    """Check 0 unhealthy containers."""
    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        lines = r.stdout.strip().splitlines()
        unhealthy = []
        ignored = []
        for line in lines:
            if "\t" not in line:
                continue
            name = line.split("\t", 1)[0]
            is_unhealthy = "unhealthy" in line.lower() or "Up" not in line
            if not is_unhealthy:
                continue
            if name.lower() in IGNORED_CONTAINER_HEALTH:
                ignored.append(name)
                continue
            unhealthy.append(name)
        if not unhealthy:
            suffix = f" ({len(ignored)} ignored)" if ignored else ""
            return "ok", f"All {len(lines)} containers healthy{suffix}"
        suffix = f"; ignored: {', '.join(ignored[:5])}" if ignored else ""
        return "fail", f"{len(unhealthy)} unhealthy: {', '.join(unhealthy[:5])}{suffix}"
    except FileNotFoundError:
        return "unknown", "Skipped: docker unavailable on this host"
    except Exception as e:
        return "fail", f"Cannot check containers: {e}"


def _eval_router_accuracy() -> tuple[str, str]:
    """Check router classification accuracy from metrics or logs."""
    # Try metrics endpoint first
    try:
        resp = urlopen(Request("http://localhost:9105/metrics"), timeout=5)
        text = resp.read().decode()
        for line in text.splitlines():
            if line.startswith("router_accuracy"):
                val = float(line.split()[-1])
                if val > 85:
                    return "ok", f"Router accuracy: {val:.1f}%"
                return "warn", f"Router accuracy: {val:.1f}% (target: >85%)"
    except Exception:
        pass

    # Fallback: estimate from router logs
    log_file = RUNTIME / "logs" / "telegram_router.log"
    try:
        lines = log_file.read_text().strip().splitlines()[-200:]
        intercepted = sum(1 for l in lines if '"intercepted"' in l)
        delegated = sum(1 for l in lines if '"delegating_to_llm"' in l)
        total = intercepted + delegated
        if total > 10:
            rate = round(intercepted / total * 100, 1)
            status = "ok" if rate > 85 else "warn"
            return status, f"Intercept rate: {rate}% ({intercepted}/{total})"
    except FileNotFoundError:
        pass

    return "warn", "No router metrics available"


_EVALUATORS = {
    "ollama_available": _eval_ollama_available,
    "gemma_warm": _eval_gemma_warm,
    "ram_healthy": _eval_ram_healthy,
    "vpn_healthy": _eval_vpn_healthy,
    "containers_healthy": _eval_containers_healthy,
    "router_accuracy": _eval_router_accuracy,
}


# ── Public API ─────────────────────────────────────────────────

def evaluate_all() -> list:
    """Evaluate all goals and update state. Returns updated goals list."""
    goals = _load_goals()
    now = datetime.now().astimezone().isoformat()

    for goal in goals:
        evaluator = _EVALUATORS.get(goal["id"])
        if not evaluator:
            continue
        try:
            status, evidence = evaluator()
            goal["status"] = status
            goal["evidence"] = evidence
            goal["last_checked"] = now
        except Exception as e:
            goal["status"] = "warn"
            goal["evidence"] = f"Evaluator error: {e}"
            goal["last_checked"] = now
            _log({"action": "eval_error", "goal": goal["id"], "error": str(e)})

    _save_goals(goals)
    _log({"action": "evaluate_all",
          "ok": sum(1 for g in goals if g["status"] == "ok"),
          "warn": sum(1 for g in goals if g["status"] == "warn"),
          "fail": sum(1 for g in goals if g["status"] == "fail")})
    return goals


def get_failing_goals() -> list:
    """Get goals with status warn or fail, sorted by priority."""
    goals = _load_goals()
    failing = [g for g in goals if g.get("status") in ("fail", "warn")]
    failing.sort(key=lambda g: g.get("priority", 99))
    return failing


def get_all_goals() -> list:
    """Get all goals with current status."""
    return _load_goals()


def format_status(goals: list) -> str:
    """Format goals as human-readable text."""
    icons = {"ok": "[OK]", "warn": "[!!]", "fail": "[XX]", "unknown": "[??]"}
    lines = ["HomeLab Goals"]
    for g in goals:
        icon = icons.get(g.get("status", "unknown"), "[??]")
        lines.append(f"  {icon} P{g['priority']} {g['desc']}")
        if g.get("evidence"):
            lines.append(f"       {g['evidence']}")
    ok = sum(1 for g in goals if g.get("status") == "ok")
    lines.append(f"\n{ok}/{len(goals)} goals met")
    return "\n".join(lines)


# ── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser(description="HomeLab goals")
    p.add_argument("--status", action="store_true", help="Show goals status")
    p.add_argument("--evaluate", action="store_true", help="Evaluate + update goals")
    args = p.parse_args()

    if args.evaluate:
        goals = evaluate_all()
        print(format_status(goals))
    elif args.status:
        goals = get_all_goals()
        print(format_status(goals))
    else:
        p.print_help()
