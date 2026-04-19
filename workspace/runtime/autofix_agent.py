#!/usr/bin/env python3
"""
autofix_agent.py — Auto-repair agent for Tenazas.

3-tier fix system:
  Tier 1: OPS — keyword/regex, no LLM (restart, pull, clear cache)
  Tier 2: CODE — Aider + Ollama local (qwen2.5-coder:32b) or Gemini fallback
  Tier 3: PROPOSAL — Telegram notification with context, awaits human decision

Reads from health_sweep SQLite for regression detection.
Respects expected_degradations and never_existed from config.

Usage:
  python3 autofix_agent.py                    # process latest sweep failures
  python3 autofix_agent.py --dry-run          # show what would happen
  python3 autofix_agent.py --tier1-only       # ops fixes only
  python3 autofix_agent.py --kill-switch      # disable all auto-fixes
  python3 autofix_agent.py --status           # show agent state
"""

import argparse
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import URLError
from urllib.request import Request, urlopen

# ── Paths ───────────────────────────────────────────────────────

_IN_CONTAINER = Path("/home/tenazas").exists()
BASE_DIR = Path(__file__).parent
CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json") if _IN_CONTAINER else BASE_DIR.parent / "state" / "openclaw.json"
STATE_DIR = Path("/home/tenazas/.openclaw/state") if _IN_CONTAINER else BASE_DIR.parent / "state"
AUTOFIX_CONFIG = STATE_DIR / "autofix_config.json"
AUTOFIX_STATE = STATE_DIR / "autofix_state.json"
SWEEP_DB = STATE_DIR / "health_sweeps.sqlite"
REPO_DIR = Path("/home/tenazas") if _IN_CONTAINER else BASE_DIR.parent
CHAT_ID = "1565794551"

HOMELAB_IP = "192.168.2.6"
HOMELAB_OLLAMA = f"http://{HOMELAB_IP}:11434"
HOMELAB_SSH_KEY = STATE_DIR / ".ssh" / "homelab_key"

# ── Logging ─────────────────────────────────────────────────────

log = logging.getLogger("autofix_agent")

# ── Rate limits ─────────────────────────────────────────────────

MAX_TIER1_PER_HOUR = 10
MAX_TIER2_PER_HOUR = 3
MAX_TIER3_PER_HOUR = 5
TIER2_TIMEOUT = 180       # 3 min for Aider
TIER1_TIMEOUT = 30        # 30s for ops commands
OLLAMA_PULL_TIMEOUT = 900 # 15 min for model downloads

# Models allowed to be pulled automatically
PULLABLE_MODELS = {
    "qwen2.5:3b", "qwen2.5:7b", "qwen2.5-coder:32b",
    "qwen3:8b", "phi4-mini", "nomic-embed-text",
}

# Files Aider is allowed to edit
EDITABLE_WHITELIST = {
    "runtime/", "tests/", "startup.sh", "ollama-check.sh",
}

# Files Aider must NEVER touch
EDIT_BLACKLIST = {
    "openclaw.json", "gcal-token.json", "gcal-creds.json",
    "credentials/", "himalaya-config/", ".ssh/",
    "docker-compose.yml", "Dockerfile",
}

# ── Helpers ─────────────────────────────────────────────────────

def _load_config() -> dict:
    try:
        return json.loads(CONFIG_PATH.read_text())
    except Exception:
        return {}


def _load_autofix_config() -> dict:
    """Load autofix-specific config with expected degradations."""
    defaults = {
        "expected_degradations": [
            {
                "probe": "ollama_nas",
                "condition": "homelab_ollama_alive",
                "reason": "NAS Ollama es fallback, offline esperado si HOMELAB up",
            },
        ],
        "never_existed": ["gpu_exporter"],
        "kill_switch": False,
        "tier2_enabled": True,
    }
    try:
        if AUTOFIX_CONFIG.exists():
            cfg = json.loads(AUTOFIX_CONFIG.read_text())
            # Merge with defaults
            for k, v in defaults.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
        else:
            # Write defaults
            AUTOFIX_CONFIG.parent.mkdir(parents=True, exist_ok=True)
            AUTOFIX_CONFIG.write_text(json.dumps(defaults, indent=2))
            return defaults
    except Exception:
        return defaults


def _load_state() -> dict:
    try:
        if AUTOFIX_STATE.exists():
            return json.loads(AUTOFIX_STATE.read_text())
    except Exception:
        pass
    return {
        "tier1_actions": [],  # [{ts, probe, action, result}]
        "tier2_actions": [],
        "tier3_proposals": [],
        "last_run": None,
        "total_fixes": 0,
        "total_proposals": 0,
    }


def _save_state(state: dict):
    try:
        AUTOFIX_STATE.parent.mkdir(parents=True, exist_ok=True)
        AUTOFIX_STATE.write_text(json.dumps(state, indent=2, default=str))
    except Exception as e:
        log.error("save state failed: %s", e)


def _notify(msg: str):
    try:
        cfg = _load_config()
        token = cfg["channels"]["telegram"]["botToken"]
        payload = json.dumps({"chat_id": CHAT_ID, "text": msg[:4090]}).encode()
        urlopen(Request(
            f"https://api.telegram.org/bot{token}/sendMessage",
            data=payload, headers={"Content-Type": "application/json"},
        ), timeout=10)
    except Exception as e:
        log.error("notify failed: %s", e)


def _rate_check(actions: list, max_per_hour: int) -> bool:
    """Return True if under rate limit."""
    cutoff = time.time() - 3600
    recent = [a for a in actions if a.get("ts", 0) > cutoff]
    return len(recent) < max_per_hour


def _record_action(state: dict, tier: str, probe: str, action: str, result: str):
    key = f"tier{tier}_actions" if tier in ("1", "2") else "tier3_proposals"
    state.setdefault(key, []).append({
        "ts": time.time(),
        "probe": probe,
        "action": action,
        "result": result,
        "dt": datetime.now().isoformat(),
    })
    # Keep last 100 per tier
    state[key] = state[key][-100:]


# ════════════════════════════════════════════════════════════════
#  REGRESSION DETECTION (from health_sweep SQLite)
# ════════════════════════════════════════════════════════════════

def _get_probe_history(probe: str, n: int = 20) -> list[dict]:
    """Get last N sweep results for a probe."""
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


def classify_regression(probe: str) -> str:
    """Classify probe failure type.

    Returns:
      'new'       — first time failing
      'regression' — was ok, now failing
      'chronic'   — failing > 5 consecutive sweeps
      'flapping'  — alternating ok/fail
      'unknown'   — not enough data
    """
    history = _get_probe_history(probe, 20)
    if not history:
        return "unknown"

    statuses = [h["status"] for h in history]

    # Current must be failing
    if statuses[0] == "ok":
        return "unknown"

    # Count consecutive failures from most recent
    consecutive_fails = 0
    for s in statuses:
        if s != "ok":
            consecutive_fails += 1
        else:
            break

    if consecutive_fails >= 5:
        return "chronic"

    # Check if it was ever ok
    has_ok = "ok" in statuses
    if not has_ok:
        return "new"  # never been ok in our history

    # Was ok recently, now failing
    if consecutive_fails == 1:
        # Check for flapping (ok-fail-ok-fail pattern)
        flap_count = 0
        for i in range(1, len(statuses)):
            if statuses[i] != statuses[i - 1]:
                flap_count += 1
        if flap_count > len(statuses) * 0.5:
            return "flapping"
        return "regression"

    return "regression"


# ════════════════════════════════════════════════════════════════
#  EXPECTED DEGRADATION FILTER
# ════════════════════════════════════════════════════════════════

def _check_condition(condition: str) -> bool:
    """Evaluate a condition string against current system state."""
    if condition == "homelab_ollama_alive":
        try:
            resp = urlopen(f"{HOMELAB_OLLAMA}/api/tags", timeout=3)
            return resp.status == 200
        except Exception:
            return False
    return False


def should_skip_probe(probe: str, status: str, config: dict) -> tuple[bool, str]:
    """Check if a failing probe should be skipped.

    Returns (skip, reason).
    """
    if status == "ok":
        return True, "probe is ok"

    # Expected degradations
    for deg in config.get("expected_degradations", []):
        if deg["probe"] == probe:
            cond = deg.get("condition", "")
            if not cond or _check_condition(cond):
                return True, f"expected: {deg.get('reason', 'configured')}"

    # Never existed
    if probe in config.get("never_existed", []):
        return True, f"never_existed: needs install decision"

    return False, ""


# ════════════════════════════════════════════════════════════════
#  LLM AVAILABILITY (pre-flight for Tier 2)
# ════════════════════════════════════════════════════════════════

def check_llm_availability() -> dict:
    """Check which LLM backends are available for Tier 2.

    Returns dict with availability info and recommended backend.
    """
    result = {
        "ollama_local": False,
        "ollama_model": None,
        "gemini": False,
        "recommended": None,
        "reason": "",
    }

    # 1. Check Ollama HOMELAB has qwen2.5-coder:32b
    try:
        resp = urlopen(f"{HOMELAB_OLLAMA}/api/tags", timeout=5)
        if resp.status == 200:
            data = json.loads(resp.read())
            models = {m.get("name", "") for m in data.get("models", [])}
            if "qwen2.5-coder:32b" in models:
                result["ollama_local"] = True
                result["ollama_model"] = "qwen2.5-coder:32b"
                result["recommended"] = "ollama"
                result["reason"] = "local, free, private"
                return result
            # Try smaller coder model
            for fallback in ["qwen2.5-coder:14b", "qwen2.5:7b"]:
                if fallback in models:
                    result["ollama_local"] = True
                    result["ollama_model"] = fallback
                    result["recommended"] = "ollama"
                    result["reason"] = f"local fallback ({fallback})"
                    return result
    except Exception:
        pass

    # 2. Check Gemini quota
    gemini_quota_file = STATE_DIR / "gemini_quota.json"
    try:
        if gemini_quota_file.exists():
            quota = json.loads(gemini_quota_file.read_text())
            if quota.get("exhausted", False):
                reset_at = quota.get("reset_at", "unknown")
                result["reason"] = f"Gemini quota exhausted, resets {reset_at}"
            else:
                result["gemini"] = True
                result["recommended"] = "gemini"
                result["reason"] = "cloud fallback (free tier)"
                return result
        else:
            # Try a dummy call
            api_key = os.environ.get("GOOGLE_API_KEY", "")
            if api_key:
                result["gemini"] = True
                result["recommended"] = "gemini"
                result["reason"] = "cloud fallback"
                return result
    except Exception:
        pass

    # 3. No LLM available
    result["reason"] = result.get("reason", "") or "no LLM backend available"
    return result


# ════════════════════════════════════════════════════════════════
#  TIER 1 — OPS (no LLM, keyword/regex, safe commands)
# ════════════════════════════════════════════════════════════════

# Closed catalog of Tier 1 actions
TIER1_CATALOG = {
    # Container restarts
    "chromadb": {
        "action": "restart_container",
        "command": ["docker", "restart", "chromadb"],
        "description": "Restart ChromaDB container",
    },
    "prometheus": {
        "action": "restart_container",
        "command": ["docker", "restart", "prometheus"],
        "description": "Restart Prometheus container",
    },
    "grafana": {
        "action": "restart_container",
        "command": ["docker", "restart", "grafana"],
        "description": "Restart Grafana container",
    },
    "piper": {
        "action": "restart_container",
        "command": ["docker", "restart", "piper"],
        "description": "Restart Piper TTS container",
    },
    "vpn": {
        "action": "restart_container",
        "command": ["docker", "restart", "gluetun"],
        "description": "Restart gluetun VPN gateway",
    },
    # Metrics exporter is inside openclaw — restart won't help,
    # but we can check if the process is running
    "metrics_exporter": {
        "action": "check_process",
        "command": ["pgrep", "-f", "metrics_server.py"],
        "fallback": ["python3", "/home/tenazas/runtime/metrics_server.py"],
        "description": "Check/restart metrics_server process",
    },
}

# Containers that Tier 1 can restart (safety whitelist)
RESTARTABLE_CONTAINERS = {
    "chromadb", "prometheus", "grafana", "piper", "gluetun",
    "qbittorrent", "sonarr", "radarr", "lidarr", "prowlarr",
    "sabnzbd", "seerr", "requestrr", "tautulli",
}

# NEVER restart these
PROTECTED_CONTAINERS = {"openclaw", "watchtower", "portainer"}


def _tier1_restart_container(name: str, dry_run: bool = False) -> dict:
    """Restart a container (Tier 1 action)."""
    if name in PROTECTED_CONTAINERS:
        return {"success": False, "action": "blocked",
                "reason": f"{name} is protected"}
    if name not in RESTARTABLE_CONTAINERS:
        return {"success": False, "action": "blocked",
                "reason": f"{name} not in restart whitelist"}
    if dry_run:
        return {"success": True, "action": "dry_run",
                "command": f"docker restart {name}"}
    try:
        r = subprocess.run(["docker", "restart", name],
                           capture_output=True, text=True, timeout=TIER1_TIMEOUT)
        return {"success": r.returncode == 0, "action": "restart",
                "output": r.stdout.strip()[:200]}
    except Exception as e:
        return {"success": False, "action": "restart", "error": str(e)}


def _tier1_pull_model(model: str, dry_run: bool = False) -> dict:
    """Pull a missing Ollama model on HOMELAB (Tier 1 action)."""
    if model not in PULLABLE_MODELS:
        return {"success": False, "action": "blocked",
                "reason": f"{model} not in pullable whitelist"}
    if not HOMELAB_SSH_KEY.exists():
        return {"success": False, "action": "needs_setup",
                "reason": "SSH key not configured",
                "setup": f"ssh-keygen -f {HOMELAB_SSH_KEY} && ssh-copy-id -i {HOMELAB_SSH_KEY} user@{HOMELAB_IP}"}

    if dry_run:
        return {"success": True, "action": "dry_run",
                "command": f"ssh HOMELAB 'ollama pull {model}'"}
    try:
        r = subprocess.run(
            ["ssh", "-i", str(HOMELAB_SSH_KEY), "-o", "StrictHostKeyChecking=no",
             f"user@{HOMELAB_IP}", f"ollama pull {model}"],
            capture_output=True, text=True, timeout=OLLAMA_PULL_TIMEOUT)
        return {"success": r.returncode == 0, "action": "pull_model",
                "output": r.stdout.strip()[-200:]}
    except subprocess.TimeoutExpired:
        return {"success": False, "action": "pull_model",
                "error": f"timeout after {OLLAMA_PULL_TIMEOUT}s"}
    except Exception as e:
        return {"success": False, "action": "pull_model", "error": str(e)}


def _tier1_load_model(model: str, dry_run: bool = False) -> dict:
    """Force-load a model into VRAM via dummy generate."""
    if dry_run:
        return {"success": True, "action": "dry_run",
                "command": f"POST {HOMELAB_OLLAMA}/api/generate (model={model})"}
    try:
        payload = json.dumps({
            "model": model, "prompt": "ok", "stream": False,
            "options": {"num_predict": 1},
        }).encode()
        resp = urlopen(Request(
            f"{HOMELAB_OLLAMA}/api/generate",
            data=payload,
            headers={"Content-Type": "application/json"},
        ), timeout=30)
        return {"success": resp.status == 200, "action": "load_model"}
    except Exception as e:
        return {"success": False, "action": "load_model", "error": str(e)}


def _tier1_check_process(probe: str, dry_run: bool = False) -> dict:
    """Check if a process is running, restart if not."""
    entry = TIER1_CATALOG.get(probe, {})
    cmd = entry.get("command", [])
    fallback = entry.get("fallback", [])

    if dry_run:
        return {"success": True, "action": "dry_run",
                "command": f"pgrep + fallback start"}
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=5)
        if r.returncode == 0:
            return {"success": True, "action": "already_running",
                    "pid": r.stdout.strip()}
        # Process not running, start it
        if fallback:
            subprocess.Popen(fallback, stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
            return {"success": True, "action": "started"}
        return {"success": False, "action": "not_running", "reason": "no fallback"}
    except Exception as e:
        return {"success": False, "action": "check_process", "error": str(e)}


def route_tier1(probe: str, detail: dict, dry_run: bool = False) -> dict | None:
    """Route a probe failure to a Tier 1 action. Returns None if no action."""
    # Container restarts
    if probe in TIER1_CATALOG:
        entry = TIER1_CATALOG[probe]
        if entry["action"] == "restart_container":
            container = entry["command"][-1]
            return _tier1_restart_container(container, dry_run)
        elif entry["action"] == "check_process":
            return _tier1_check_process(probe, dry_run)

    # Missing container (from containers probe)
    if probe == "containers":
        missing = detail.get("missing", [])
        results = []
        for name in missing[:3]:  # max 3 restarts per sweep
            r = _tier1_restart_container(name, dry_run)
            results.append({"container": name, **r})
        return {"success": all(r.get("success") for r in results),
                "action": "restart_missing", "results": results}

    # Ollama missing model
    if probe == "ollama_gpu":
        missing = detail.get("missing_models", [])
        for model in missing:
            if model in PULLABLE_MODELS:
                return _tier1_pull_model(model, dry_run)
        return None

    # Ollama not loaded
    if probe == "ollama_vram":
        loaded = detail.get("loaded", [])
        if not loaded:
            return _tier1_load_model("qwen2.5:7b", dry_run)
        return None

    return None


# ════════════════════════════════════════════════════════════════
#  TIER 2 — CODE (Aider + Ollama/Gemini)
# ════════════════════════════════════════════════════════════════

def _collect_context(probe: str, detail: dict) -> dict:
    """Collect error context for Tier 2 fix."""
    context = {
        "probe": probe,
        "detail": detail,
        "stacktrace": "",
        "log_tail": "",
        "files": [],
    }

    # Try to find relevant log
    log_dir = BASE_DIR / "logs"
    log_candidates = [
        log_dir / f"{probe}.log",
        log_dir / "runtime.log",
        log_dir / "orchestrator.log",
    ]
    for lf in log_candidates:
        if lf.exists():
            try:
                lines = lf.read_text().strip().split("\n")
                # Get last 30 lines with errors
                error_lines = [l for l in lines[-100:] if
                               any(k in l.lower() for k in
                                   ["error", "exception", "traceback", "fail"])]
                context["log_tail"] = "\n".join(error_lines[-30:])
                break
            except Exception:
                pass

    # Extract stacktrace if present
    msg = detail.get("message", "")
    if "Traceback" in msg or "Error" in msg:
        context["stacktrace"] = msg[:2000]

    # Identify files from stacktrace
    file_pattern = re.compile(r'File "([^"]+\.py)"')
    for match in file_pattern.finditer(context.get("stacktrace", "") + context.get("log_tail", "")):
        fpath = match.group(1)
        # Only include files in our repo
        if "/tenazas/" in fpath or "runtime/" in fpath:
            rel = fpath.split("/tenazas/")[-1] if "/tenazas/" in fpath else fpath
            if rel not in context["files"]:
                context["files"].append(rel)

    return context


def _validate_diff(dry_run: bool = False) -> dict:
    """Validate Aider's changes are safe."""
    try:
        r = subprocess.run(["git", "diff", "--stat"], capture_output=True,
                           text=True, timeout=10, cwd=str(REPO_DIR))
        diff_stat = r.stdout.strip()

        if not diff_stat:
            return {"valid": False, "reason": "no changes made"}

        # Parse files and line counts
        lines = diff_stat.split("\n")
        summary = lines[-1] if lines else ""

        # Extract changed files
        changed_files = []
        for line in lines[:-1]:
            parts = line.strip().split("|")
            if parts:
                fname = parts[0].strip()
                changed_files.append(fname)

        # Check file count
        if len(changed_files) > 3:
            return {"valid": False, "reason": f"too many files: {len(changed_files)}",
                    "files": changed_files}

        # Check total lines
        insertions = 0
        deletions = 0
        m = re.search(r"(\d+) insertion", summary)
        if m:
            insertions = int(m.group(1))
        m = re.search(r"(\d+) deletion", summary)
        if m:
            deletions = int(m.group(1))
        total_changes = insertions + deletions

        if total_changes > 50:
            return {"valid": False, "reason": f"too many changes: {total_changes} lines",
                    "files": changed_files}

        # Check whitelist/blacklist
        for f in changed_files:
            # Blacklist check
            for bl in EDIT_BLACKLIST:
                if bl in f:
                    return {"valid": False, "reason": f"blacklisted file: {f}",
                            "files": changed_files}
            # Whitelist check
            in_whitelist = any(f.startswith(w) or w in f for w in EDITABLE_WHITELIST)
            if not in_whitelist:
                return {"valid": False, "reason": f"file not in whitelist: {f}",
                        "files": changed_files}

        # py_compile check on Python files
        for f in changed_files:
            if f.endswith(".py"):
                full_path = REPO_DIR / f
                if full_path.exists():
                    r2 = subprocess.run(
                        ["python3", "-m", "py_compile", str(full_path)],
                        capture_output=True, text=True, timeout=10)
                    if r2.returncode != 0:
                        return {"valid": False,
                                "reason": f"syntax error in {f}: {r2.stderr[:200]}",
                                "files": changed_files}

        return {"valid": True, "files": changed_files,
                "insertions": insertions, "deletions": deletions}
    except Exception as e:
        return {"valid": False, "reason": str(e)}


def _run_tests(files: list[str]) -> dict:
    """Run relevant tests for changed files."""
    test_dir = REPO_DIR / "tests"
    tests_to_run = []

    for f in files:
        # Try to find matching test file
        basename = Path(f).stem
        candidates = [
            test_dir / f"test_{basename}.py",
            test_dir / f"test_health_sweep.py",  # always run if runtime changed
        ]
        for t in candidates:
            if t.exists() and str(t) not in tests_to_run:
                tests_to_run.append(str(t))

    if not tests_to_run:
        return {"success": True, "skipped": True, "reason": "no matching tests"}

    results = []
    for test in tests_to_run[:3]:  # max 3 test files
        try:
            r = subprocess.run(["python3", test], capture_output=True,
                               text=True, timeout=60, cwd=str(REPO_DIR))
            results.append({
                "test": Path(test).name,
                "success": r.returncode == 0,
                "output": r.stderr[-500:] if r.returncode != 0 else "",
            })
        except Exception as e:
            results.append({"test": Path(test).name, "success": False, "error": str(e)})

    return {
        "success": all(r["success"] for r in results),
        "tests": results,
    }


def run_tier2(probe: str, detail: dict, llm: dict,
              dry_run: bool = False) -> dict:
    """Run Tier 2 code fix via Aider."""
    context = _collect_context(probe, detail)

    if not context["files"] and not context["log_tail"]:
        return {"success": False, "tier": 2,
                "reason": "no stacktrace or files to fix"}

    # Build Aider prompt
    error_summary = detail.get("message", probe)
    prompt = (
        f"Bug detectado en Tenazas: {error_summary}\n"
        f"Stacktrace: {context['stacktrace'][:1500]}\n"
        f"Log relevante: {context['log_tail'][:1000]}\n\n"
        "Tu trabajo: arreglar SOLO este bug con el mínimo cambio posible.\n"
        "NO refactorices. NO agregues features. NO toques archivos fuera "
        "del stacktrace. Si no podés arreglarlo con <10 líneas o tocando "
        'más de 2 archivos, respondé EXACTAMENTE "UNFIXABLE: <razón>".'
    )

    # Determine backend
    if llm["recommended"] == "ollama":
        model_flag = f"ollama_chat/{llm['ollama_model']}"
        env = {**os.environ, "OLLAMA_API_BASE": HOMELAB_OLLAMA}
    elif llm["recommended"] == "gemini":
        model_flag = "gemini/gemini-2.5-flash"
        env = dict(os.environ)
    else:
        return {"success": False, "tier": 2, "reason": "no LLM available"}

    # Branch name
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = re.sub(r'[^a-z0-9]', '-', probe.lower())[:20]
    branch = f"autofix/{ts}-{slug}"

    if dry_run:
        return {"success": True, "tier": 2, "action": "dry_run",
                "branch": branch, "model": model_flag,
                "files": context["files"], "prompt_preview": prompt[:300]}

    # Create branch
    subprocess.run(["git", "checkout", "-b", branch],
                   capture_output=True, cwd=str(REPO_DIR))

    try:
        # Build Aider command
        aider_cmd = [
            "aider",
            "--model", model_flag,
            "--no-auto-commits",
            "--yes",
            "--map-tokens", "0",
            "--no-stream",
            "--message", prompt,
        ] + context["files"]

        r = subprocess.run(aider_cmd, capture_output=True, text=True,
                           timeout=TIER2_TIMEOUT, cwd=str(REPO_DIR), env=env)

        aider_output = r.stdout + r.stderr

        # Check for UNFIXABLE
        if "UNFIXABLE" in aider_output:
            reason = ""
            for line in aider_output.split("\n"):
                if "UNFIXABLE" in line:
                    reason = line.split("UNFIXABLE:")[-1].strip()
                    break
            _git_cleanup(branch)
            return {"success": False, "tier": 2, "reason": f"unfixable: {reason}",
                    "escalate_to": 3}

        # Validate diff
        validation = _validate_diff()
        if not validation["valid"]:
            _git_cleanup(branch)
            return {"success": False, "tier": 2,
                    "reason": f"diff rejected: {validation['reason']}",
                    "escalate_to": 3}

        # Run tests
        test_result = _run_tests(validation.get("files", []))
        if not test_result["success"] and not test_result.get("skipped"):
            _git_cleanup(branch)
            return {"success": False, "tier": 2,
                    "reason": f"tests failed",
                    "test_details": test_result,
                    "escalate_to": 3}

        # Commit the fix
        subprocess.run(["git", "add", "-A"], capture_output=True, cwd=str(REPO_DIR))
        subprocess.run(
            ["git", "commit", "-m",
             f"fix(autofix): {error_summary[:60]}\n\n"
             f"Auto-generated by autofix_agent Tier 2\n"
             f"Probe: {probe}\nModel: {model_flag}\n"
             f"Branch: {branch}"],
            capture_output=True, cwd=str(REPO_DIR))

        # Merge to main
        subprocess.run(["git", "checkout", "main"], capture_output=True, cwd=str(REPO_DIR))
        merge_r = subprocess.run(["git", "merge", branch],
                                 capture_output=True, text=True, cwd=str(REPO_DIR))

        if merge_r.returncode != 0:
            _git_cleanup(branch)
            return {"success": False, "tier": 2,
                    "reason": "merge conflict", "escalate_to": 3}

        # Cleanup branch
        subprocess.run(["git", "branch", "-d", branch],
                       capture_output=True, cwd=str(REPO_DIR))

        return {"success": True, "tier": 2, "branch": branch,
                "model": model_flag, "files": validation.get("files", []),
                "changes": f"+{validation.get('insertions', 0)}/{validation.get('deletions', 0)}"}

    except subprocess.TimeoutExpired:
        _git_cleanup(branch)
        return {"success": False, "tier": 2,
                "reason": f"aider timeout ({TIER2_TIMEOUT}s)", "escalate_to": 3}
    except Exception as e:
        _git_cleanup(branch)
        return {"success": False, "tier": 2, "reason": str(e), "escalate_to": 3}


def _git_cleanup(branch: str):
    """Cleanup failed Tier 2 branch."""
    try:
        subprocess.run(["git", "checkout", "main"], capture_output=True, cwd=str(REPO_DIR))
        subprocess.run(["git", "branch", "-D", branch], capture_output=True, cwd=str(REPO_DIR))
        subprocess.run(["git", "checkout", "--", "."], capture_output=True, cwd=str(REPO_DIR))
    except Exception:
        pass


# ════════════════════════════════════════════════════════════════
#  TIER 3 — PROPOSAL (Telegram notification, human decision)
# ════════════════════════════════════════════════════════════════

def run_tier3(probe: str, detail: dict, reason: str = "",
              dry_run: bool = False) -> dict:
    """Create a proposal for human review via Telegram."""
    regression = classify_regression(probe)
    msg = detail.get("message", "")
    suggestion = detail.get("suggestion", "")

    text = (
        f"🔧 *Autofix Proposal — Tier 3*\n\n"
        f"Probe: `{probe}`\n"
        f"Status: {regression}\n"
        f"Detail: {msg[:200]}\n"
    )

    if reason:
        text += f"Tier 2 result: {reason[:200]}\n"
    if suggestion:
        text += f"Sugerencia: `{suggestion}`\n"

    text += (
        f"\nHistory: últimos 5 sweeps → "
        f"{', '.join(h['status'] for h in _get_probe_history(probe, 5))}\n"
    )

    if dry_run:
        return {"success": True, "tier": 3, "action": "dry_run",
                "message_preview": text[:500]}

    _notify(text)
    return {"success": True, "tier": 3, "action": "notified",
            "probe": probe, "regression": regression}


# ════════════════════════════════════════════════════════════════
#  MAIN ORCHESTRATOR
# ════════════════════════════════════════════════════════════════

def get_latest_failures() -> list[dict]:
    """Get failing probes from most recent sweep."""
    try:
        conn = sqlite3.connect(str(SWEEP_DB))
        # Get latest sweep timestamp
        row = conn.execute(
            "SELECT ts FROM sweeps ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return []
        latest_ts = row[0]

        # Get all failing probes from that sweep
        rows = conn.execute(
            "SELECT probe, status, latency_ms, detail_json FROM sweeps "
            "WHERE ts = ? AND status != 'ok'", (latest_ts,)).fetchall()
        conn.close()

        return [{"probe": r[0], "status": r[1], "latency_ms": r[2],
                 "detail": json.loads(r[3]) if r[3] else {}} for r in rows]
    except Exception as e:
        log.error("get_latest_failures: %s", e)
        return []


def process_failures(failures: list[dict], dry_run: bool = False,
                     tier1_only: bool = False) -> dict:
    """Process a list of probe failures through the tier system."""
    config = _load_autofix_config()
    state = _load_state()

    if config.get("kill_switch"):
        return {"action": "killed", "reason": "kill switch is ON"}

    results = []
    llm_info = None  # lazy-loaded

    for failure in failures:
        probe = failure["probe"]
        detail = failure.get("detail", {})
        status = failure.get("status", "fail")

        # Check expected degradations
        skip, skip_reason = should_skip_probe(probe, status, config)
        if skip:
            results.append({"probe": probe, "action": "skipped",
                            "reason": skip_reason})
            continue

        # Never-existed → Tier 3 directly
        if probe in config.get("never_existed", []):
            r = run_tier3(probe, detail, "never installed", dry_run)
            _record_action(state, "3", probe, "never_existed_proposal", r.get("action", ""))
            results.append({"probe": probe, **r})
            continue

        # Classify regression
        regression = classify_regression(probe)

        # Tier 1 — try ops fix first
        if _rate_check(state.get("tier1_actions", []), MAX_TIER1_PER_HOUR):
            t1 = route_tier1(probe, detail, dry_run)
            if t1 is not None:
                _record_action(state, "1", probe, t1.get("action", ""),
                               "ok" if t1.get("success") else "fail")
                results.append({"probe": probe, "tier": 1, **t1})
                if t1.get("success"):
                    state["total_fixes"] = state.get("total_fixes", 0) + 1
                    continue
                # Tier 1 failed, fall through to Tier 2

        if tier1_only:
            results.append({"probe": probe, "action": "skipped",
                            "reason": "tier1_only mode"})
            continue

        # Tier 2 — code fix (only for regressions/new failures with code context)
        if config.get("tier2_enabled", True) and regression in ("regression", "new"):
            if _rate_check(state.get("tier2_actions", []), MAX_TIER2_PER_HOUR):
                # Lazy-load LLM availability
                if llm_info is None:
                    llm_info = check_llm_availability()

                if llm_info["recommended"]:
                    t2 = run_tier2(probe, detail, llm_info, dry_run)
                    _record_action(state, "2", probe, t2.get("action", ""),
                                   "ok" if t2.get("success") else "fail")
                    results.append({"probe": probe, **t2})
                    if t2.get("success"):
                        state["total_fixes"] = state.get("total_fixes", 0) + 1
                        continue
                    # Tier 2 failed, escalate to Tier 3
                    if t2.get("escalate_to") == 3:
                        t3 = run_tier3(probe, detail, t2.get("reason", ""), dry_run)
                        _record_action(state, "3", probe, "escalated_from_tier2",
                                       t3.get("action", ""))
                        results.append({"probe": probe, **t3})
                        state["total_proposals"] = state.get("total_proposals", 0) + 1
                        continue
                else:
                    # No LLM → degrade to Tier 3
                    if not dry_run:
                        _notify(
                            f"⚠️ Tier 2 degradado a Tier 3: {llm_info['reason']}.\n"
                            "Fixes solo reportados, no aplicados."
                        )

        # Tier 3 — proposal
        if _rate_check(state.get("tier3_proposals", []), MAX_TIER3_PER_HOUR):
            t3 = run_tier3(probe, detail, dry_run=dry_run)
            _record_action(state, "3", probe, "proposal", t3.get("action", ""))
            results.append({"probe": probe, **t3})
            state["total_proposals"] = state.get("total_proposals", 0) + 1

    state["last_run"] = datetime.now().isoformat()
    _save_state(state)

    return {"results": results, "total": len(results),
            "fixes": sum(1 for r in results if r.get("success") and r.get("tier") in (1, 2)),
            "proposals": sum(1 for r in results if r.get("tier") == 3),
            "skipped": sum(1 for r in results if r.get("action") == "skipped")}


def get_status() -> dict:
    """Get current autofix agent status."""
    config = _load_autofix_config()
    state = _load_state()
    llm = check_llm_availability()

    cutoff = time.time() - 3600
    return {
        "kill_switch": config.get("kill_switch", False),
        "tier2_enabled": config.get("tier2_enabled", True),
        "llm_backend": llm["recommended"] or "none",
        "llm_reason": llm["reason"],
        "last_run": state.get("last_run"),
        "total_fixes": state.get("total_fixes", 0),
        "total_proposals": state.get("total_proposals", 0),
        "tier1_last_hour": len([a for a in state.get("tier1_actions", [])
                                if a.get("ts", 0) > cutoff]),
        "tier2_last_hour": len([a for a in state.get("tier2_actions", [])
                                if a.get("ts", 0) > cutoff]),
        "tier3_last_hour": len([a for a in state.get("tier3_proposals", [])
                                if a.get("ts", 0) > cutoff]),
        "expected_degradations": len(config.get("expected_degradations", [])),
        "never_existed": config.get("never_existed", []),
    }


# ════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════

def run(dry_run: bool = False, tier1_only: bool = False):
    """Main entry: process latest sweep failures."""
    failures = get_latest_failures()
    if not failures:
        print("[autofix] no failures in latest sweep")
        return {"results": [], "total": 0}

    print(f"[autofix] processing {len(failures)} failure(s)...")
    result = process_failures(failures, dry_run=dry_run, tier1_only=tier1_only)

    # Summary
    print(f"[autofix] done — {result['fixes']} fixed, "
          f"{result['proposals']} proposals, {result['skipped']} skipped")

    # Notify summary if fixes were made
    if result["fixes"] > 0 and not dry_run:
        fixed = [r for r in result["results"] if r.get("success") and r.get("tier") in (1, 2)]
        msg = f"🔧 Autofix — {result['fixes']} fix(es) aplicados:\n"
        for f in fixed:
            msg += f"  • {f.get('probe', '?')} (Tier {f.get('tier', '?')})\n"
        _notify(msg)

    return result


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")
    parser = argparse.ArgumentParser(description="Tenazas Autofix Agent")
    parser.add_argument("--dry-run", action="store_true",
                        help="Show what would happen without acting")
    parser.add_argument("--tier1-only", action="store_true",
                        help="Only run Tier 1 (ops) fixes")
    parser.add_argument("--kill-switch", action="store_true",
                        help="Toggle kill switch")
    parser.add_argument("--status", action="store_true",
                        help="Show agent status")
    parser.add_argument("--json", action="store_true",
                        help="Output JSON")
    args = parser.parse_args()

    if args.kill_switch:
        cfg = _load_autofix_config()
        cfg["kill_switch"] = not cfg.get("kill_switch", False)
        AUTOFIX_CONFIG.write_text(json.dumps(cfg, indent=2))
        state = "ON (disabled)" if cfg["kill_switch"] else "OFF (enabled)"
        print(f"[autofix] kill switch: {state}")
    elif args.status:
        status = get_status()
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            print(f"Kill switch: {'ON' if status['kill_switch'] else 'OFF'}")
            print(f"Tier 2: {'enabled' if status['tier2_enabled'] else 'disabled'}")
            print(f"LLM: {status['llm_backend']} ({status['llm_reason']})")
            print(f"Last run: {status['last_run'] or 'never'}")
            print(f"Total fixes: {status['total_fixes']}")
            print(f"Total proposals: {status['total_proposals']}")
            print(f"Last hour: T1={status['tier1_last_hour']} "
                  f"T2={status['tier2_last_hour']} T3={status['tier3_last_hour']}")
    else:
        result = run(dry_run=args.dry_run, tier1_only=args.tier1_only)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
