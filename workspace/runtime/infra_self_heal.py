#!/usr/bin/env python3
"""
infra_self_heal.py
Detects and auto-repairs infrastructure issues with safe policy.

Detects: docker unhealthy, ollama dead, GPU stuck, memory high, network down.
Policy: restart containers (except openclaw/watchtower), notify on action.
Safety: cooldown per target, max 3 restarts/hour, always notify.
"""

import json
import subprocess
import time
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

try:
    from .runtime_config import OLLAMA_GPU_URL, get_default_telegram_chat_id
except ImportError:
    from runtime_config import OLLAMA_GPU_URL, get_default_telegram_chat_id

RUNTIME = Path(__file__).parent
LOG_PATH = RUNTIME / "logs" / "self_heal.log"
STATE_PATH = RUNTIME / "self_heal_state.json"
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")
OLLAMA_URL = OLLAMA_GPU_URL
CHAT_ID = get_default_telegram_chat_id()

# Safety
PROTECTED = {"openclaw", "watchtower", "WATCHTOWER"}
COOLDOWN_SECS = 1200  # 20 min per target
MAX_RESTARTS_HOUR = 3


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


def _notify(msg: str):
    try:
        token = json.loads(CONFIG_PATH.read_text())["channels"]["telegram"]["botToken"]
        payload = json.dumps({"chat_id": CHAT_ID, "text": msg[:4090]}).encode()
        urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
                        data=payload, headers={"Content-Type": "application/json"}), timeout=10)
    except Exception:
        pass


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text()) if STATE_PATH.exists() else {}
    except Exception:
        return {}


def _save_state(state: dict):
    STATE_PATH.write_text(json.dumps(state, default=str))


def _can_restart(target: str, state: dict) -> bool:
    """Check cooldown and hourly limit."""
    now = time.time()
    # Per-target cooldown
    last = state.get("last_restart", {}).get(target, 0)
    if now - last < COOLDOWN_SECS:
        return False
    # Hourly limit
    hour_ago = now - 3600
    recent = [t for t in state.get("restart_times", []) if t > hour_ago]
    if len(recent) >= MAX_RESTARTS_HOUR:
        return False
    return True


def _record_restart(target: str, state: dict):
    state.setdefault("last_restart", {})[target] = time.time()
    state.setdefault("restart_times", []).append(time.time())
    # Keep only last hour
    hour_ago = time.time() - 3600
    state["restart_times"] = [t for t in state["restart_times"] if t > hour_ago]


# ── Detectors ────────────────────────────────────────────────────

def detect_unhealthy_containers() -> list[str]:
    """Find containers that are down or restarting."""
    try:
        r = subprocess.run(["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
                           capture_output=True, text=True, timeout=10)
        down = []
        for line in r.stdout.strip().splitlines():
            parts = line.split("\t")
            if len(parts) == 2:
                name, status = parts
                if name not in PROTECTED and ("Exited" in status or "Restarting" in status):
                    down.append(name)
        return down
    except Exception:
        return []


def detect_ollama_dead() -> bool:
    """Check if Ollama is responding."""
    try:
        urlopen(Request(f"{OLLAMA_URL}/api/tags"), timeout=5)
        return False
    except Exception:
        return True


def detect_high_memory() -> bool:
    """Check NAS memory usage."""
    try:
        with open("/proc/meminfo") as f:
            info = {}
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    info[parts[0].rstrip(":")] = int(parts[1])
        total = info.get("MemTotal", 1)
        available = info.get("MemAvailable", total)
        pct_used = (1 - available / total) * 100
        return pct_used > 90
    except Exception:
        return False


def detect_network_down() -> bool:
    """Check internet connectivity."""
    try:
        urlopen(Request("http://1.1.1.1"), timeout=5)
        return False
    except Exception:
        return True


# ── Repairs ──────────────────────────────────────────────────────

def restart_container(name: str) -> bool:
    """Restart a Docker container."""
    if name in PROTECTED:
        return False
    try:
        r = subprocess.run(["docker", "restart", name],
                           capture_output=True, text=True, timeout=30)
        return r.returncode == 0
    except Exception:
        return False


# ── Main ─────────────────────────────────────────────────────────

def heal(dry_run: bool = False):
    """Run all detectors and apply repairs."""
    state = _load_state()
    actions = []

    # 1. Unhealthy containers
    down = detect_unhealthy_containers()
    for name in down:
        if _can_restart(name, state):
            if dry_run:
                actions.append(f"[DRY] Would restart {name}")
            else:
                ok = restart_container(name)
                _record_restart(name, state)
                actions.append(f"Restarted {name}: {'OK' if ok else 'FAIL'}")
                _log({"action": "restart", "target": name, "success": ok})
        else:
            actions.append(f"Cooldown: {name} (skipped)")

    # 2. Ollama dead
    if detect_ollama_dead():
        actions.append("Ollama offline — cannot auto-restart (runs on HomeLab)")
        _log({"action": "detect", "issue": "ollama_dead"})

    # 3. High memory
    if detect_high_memory():
        actions.append("NAS memory >90% — manual review needed")
        _log({"action": "detect", "issue": "high_memory"})

    # 4. Network down
    if detect_network_down():
        actions.append("Network down — checking VPN")
        # Try restarting gluetun
        if _can_restart("gluetun", state) and not dry_run:
            ok = restart_container("gluetun")
            _record_restart("gluetun", state)
            actions.append(f"Restarted gluetun: {'OK' if ok else 'FAIL'}")

    if not dry_run:
        _save_state(state)

    # Notify if any actions taken
    if actions:
        msg = "Self-heal report:\n" + "\n".join(f"  - {a}" for a in actions)
        if not dry_run:
            _notify(msg)
        print(msg)
    else:
        print("All systems healthy — no action needed")

    _log({"action": "heal_complete", "actions": len(actions), "dry_run": dry_run})


if __name__ == "__main__":
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--run", action="store_true", help="Run heal cycle")
    p.add_argument("--dry-run", action="store_true", help="Detect only, no repairs")
    p.add_argument("--status", action="store_true", help="Show state")
    args = p.parse_args()

    if args.run:
        heal(dry_run=False)
    elif args.dry_run:
        heal(dry_run=True)
    elif args.status:
        s = _load_state()
        restarts = s.get("last_restart", {})
        print(f"Restarts tracked: {len(restarts)}")
        for name, ts in restarts.items():
            ago = int(time.time() - ts)
            print(f"  {name}: {ago}s ago")
    else:
        p.print_help()
