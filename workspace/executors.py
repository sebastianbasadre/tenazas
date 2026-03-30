#!/usr/bin/env python3
"""
Tenazas Executors — deterministic actions. Zero LLM calls.
Each function returns True on success, False on failure.
All actions are logged to runtime/logs/executor.log.
"""

import datetime
import json
import subprocess
import time
from pathlib import Path

LOGS_DIR     = Path(__file__).parent / "logs"
EXECUTOR_LOG = LOGS_DIR / "executor.log"
LOGS_DIR.mkdir(exist_ok=True)

# ── Philips Hue ───────────────────────────────────────────────────────────────
HUE_API   = "http://192.168.2.11/api/UvFQJAVDaw3FzcyGLoWwnMnz5c5RQoqXMD576kbn"
HUE_ALL   = [1, 3, 4, 6, 11, 12, 13, 15]
HUE_MAIN  = [4, 13]          # ceiling lamps
HUE_ACCENT= [1, 3, 6, 11, 12, 15]  # bloom, play, lightstrip

# ── Compose paths ─────────────────────────────────────────────────────────────
ARR_STACK = "/volume1/docker/arr-stack"


# ── Helpers ───────────────────────────────────────────────────────────────────
def _ts() -> str:
    return datetime.datetime.now().isoformat()

def _log(msg: str):
    with open(EXECUTOR_LOG, "a") as f:
        f.write(f"[{_ts()}] {msg}\n")

def _sh(cmd: str, timeout: int = 30) -> tuple[int, str]:
    try:
        r = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout + r.stderr).strip()
    except subprocess.TimeoutExpired:
        return 1, "timeout"
    except Exception as e:
        return 1, str(e)

def _hue(light_id: int, payload: dict) -> bool:
    body = json.dumps(payload)
    rc, _ = _sh(f"curl -sf -X PUT {HUE_API}/lights/{light_id}/state -d '{body}'", timeout=5)
    return rc == 0


# ══════════════════════════════════════════════════════════════════════════════
# DOCKER EXECUTORS
# ══════════════════════════════════════════════════════════════════════════════

def docker_restart(target: str) -> bool:
    """Restart a single container by name."""
    _log(f"docker.restart → {target}")
    rc, out = _sh(f"docker restart {target}", timeout=60)
    _log(f"  rc={rc} {out[:120]}")
    return rc == 0


def docker_start(target: str) -> bool:
    """
    Start a container or a compose stack.
    Special value 'arr-stack' waits for gluetun to be healthy before starting.
    """
    _log(f"docker.start → {target}")

    if target == "arr-stack":
        # Wait for gluetun to become healthy (up to 30s)
        for attempt in range(6):
            rc, out = _sh("docker inspect gluetun --format '{{.State.Health.Status}}'", timeout=5)
            if rc == 0 and out.strip() == "healthy":
                break
            _log(f"  gluetun not healthy yet (attempt {attempt + 1}/6) — waiting 5s")
            time.sleep(5)
        rc, out = _sh(f"cd {ARR_STACK} && docker-compose up -d", timeout=90)
    else:
        rc, out = _sh(f"docker start {target}", timeout=30)

    _log(f"  rc={rc} {out[:120]}")
    return rc == 0


def docker_stop(target: str) -> bool:
    """Stop a container by name."""
    _log(f"docker.stop → {target}")
    rc, out = _sh(f"docker stop {target}", timeout=30)
    _log(f"  rc={rc} {out[:120]}")
    return rc == 0


# ══════════════════════════════════════════════════════════════════════════════
# NETWORK EXECUTORS
# ══════════════════════════════════════════════════════════════════════════════

def network_scan(target: str = "") -> bool:
    """Quick ARP scan of 192.168.2.0/24 and log results."""
    _log("network.scan → 192.168.2.0/24")
    rc, out = _sh("nmap -sn 192.168.2.0/24 --host-timeout 3s", timeout=60)
    if rc == 0:
        _log(f"  devices_up={out.count('Host is up')}")
    else:
        _log(f"  scan failed: {out[:80]}")
    return rc == 0


# ══════════════════════════════════════════════════════════════════════════════
# HUE EXECUTORS
# ══════════════════════════════════════════════════════════════════════════════

def hue_scene(scene: str) -> bool:
    """
    Apply a named Hue lighting scene.

    Scenes:
      off     — all lights off
      on      — all lights full brightness
      work    — cool white, max brightness (productive)
      cinema  — main lamps off, accent lights dim blue
      relax   — main lamps warm and dim, accents off
    """
    _log(f"hue.scene → {scene}")
    success = True

    if scene in ("off",):
        for lid in HUE_ALL:
            success &= _hue(lid, {"on": False})

    elif scene in ("on", "full"):
        for lid in HUE_ALL:
            success &= _hue(lid, {"on": True, "bri": 254})

    elif scene == "work":
        # Cool white — 4000K equivalent (ct 250 mired)
        for lid in HUE_ALL:
            success &= _hue(lid, {"on": True, "bri": 254, "ct": 250})

    elif scene == "cinema":
        # Main lamps off, accent lights dim blue
        for lid in HUE_MAIN:
            success &= _hue(lid, {"on": False})
        for lid in HUE_ACCENT:
            success &= _hue(lid, {"on": True, "bri": 50, "hue": 46920, "sat": 254})

    elif scene == "relax":
        # Warm dim mains, accents off
        for lid in HUE_MAIN:
            success &= _hue(lid, {"on": True, "bri": 100, "ct": 400})
        for lid in HUE_ACCENT:
            success &= _hue(lid, {"on": False})

    else:
        _log(f"  unknown scene: {scene!r}")
        return False

    _log(f"  ok={success}")
    return success


# ══════════════════════════════════════════════════════════════════════════════
# DISPATCH TABLE
# ══════════════════════════════════════════════════════════════════════════════

_DISPATCH: dict[str, object] = {
    "docker.restart": docker_restart,
    "docker.start":   docker_start,
    "docker.stop":    docker_stop,
    "network.scan":   network_scan,
    "hue.scene":      hue_scene,
}


def execute(task: dict) -> bool:
    """
    Execute a task dict. Returns True on success.

    Expected task structure:
      {"task": "docker.restart", "target": "gluetun", "status": "pending"}
    """
    name   = task.get("task", "")
    target = task.get("target", "")
    fn     = _DISPATCH.get(name)
    if fn is None:
        _log(f"unknown task: {name!r}")
        return False
    return fn(target)  # type: ignore[call-arg]
