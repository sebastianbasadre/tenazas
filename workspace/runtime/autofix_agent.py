#!/usr/bin/env python3
"""
autofix_agent.py — Autonomous auto-repair agent for Tenazas.

3-tier system with controlled autonomy:
  Tier 1: OPS — restart containers, pull models, clear cache (no LLM)
  Tier 2: CODE — Aider + Ollama/Gemini, git safety net, auto-rollback
  Tier 3: PROPOSAL — Telegram notification, human approval required

Reads health_sweep SQLite. Respects expected_degradations, never_existed,
protected_files. Kill switch: state/AUTOFIX_DISABLED.

Deploy with kill switch ON. Enable tiers gradually via config.

Usage:
  python3 autofix_agent.py                    # one-shot
  python3 autofix_agent.py --dry-run          # classify only
  python3 autofix_agent.py --watch            # loop 5 min
  python3 autofix_agent.py --force-tier 3     # all → Tier 3
  python3 autofix_agent.py --rollback <sha>   # revert a commit
  python3 autofix_agent.py --status           # show state
  python3 autofix_agent.py --enable           # remove kill switch
  python3 autofix_agent.py --kill             # create kill switch
"""

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, date
from pathlib import Path
from urllib.request import Request, urlopen

# ── Local imports ───────────────────────────────────────────────

sys.path.insert(0, str(Path(__file__).parent))
from autofix_tiers import (classify_regression, classify_tier, should_skip,
                           get_probe_history, get_latest_failures,
                           get_sweep_count, get_latest_score)
from autofix_llm_check import check_availability as check_llm
from autofix_sandbox import (git_is_clean, create_branch, validate_diff,
                             commit_fix, merge_to_main, rollback,
                             cleanup_branch, get_diff_summary)
from autofix_vram_manager import ensure_vram_available, restore_models
from autofix_aider import (is_installed as aider_installed, collect_context,
                           build_prompt, run_aider)
from autofix_ssh import ssh_available, ollama_pull
from autofix_telegram import (notify, notify_tier1, notify_tier2,
                              notify_tier2_rollback, notify_tier3_proposal,
                              notify_degraded, format_status)

# ── Paths ───────────────────────────────────────────────────────

_IN_CONTAINER = Path("/home/tenazas").exists()
STATE_DIR = Path("/home/tenazas/.openclaw/state") if _IN_CONTAINER else Path(__file__).parent.parent / "state"
CONFIG_PATH = STATE_DIR / "autofix_config.json"
STATE_PATH = STATE_DIR / "autofix_state.json"
LOG_PATH = STATE_DIR / "autofix_log.jsonl"
KILL_SWITCH = STATE_DIR / "AUTOFIX_DISABLED"
REJECTED_DIR = STATE_DIR / "rejected_fixes"

log = logging.getLogger("autofix_agent")

# ── Default config ──────────────────────────────────────────────

DEFAULT_CONFIG = {
    "tier_enabled": {
        "tier_1": False,
        "tier_2": False,
        "tier_3": True,
    },
    "rate_limits": {
        "tier_1_restarts_per_container_per_hour": 3,
        "tier_2_merges_per_day": 5,
        "tier_2_changes_per_file_per_day": 1,
        "tier_2_consecutive_failures_before_pause": 3,
    },
    "expected_degradations": [
        {
            "probe": "ollama_nas",
            "condition": "homelab_ollama_alive",
            "reason": "NAS Ollama es fallback, offline esperado si HOMELAB up",
        },
    ],
    "never_existed": [],
    "protected_files": [
        "workspace/runtime/orchestrator.py",
        "workspace/runtime/safety_guard.py",
        "workspace/runtime/docker_wrapper.py",
        "workspace/runtime/runtime.py",
        "workspace/runtime/executors.py",
        "workspace/runtime/infra_diagnostics.py",
        "state/openclaw.json",
        "credentials/",
    ],
    "llm_backends": {
        "aider_model": "qwen2.5-coder:14b",
        "aider_fallback_model": "gemini/gemini-2.5-flash",
        "ollama_base_url": "http://192.168.2.6:11434",
        "aider_path": "/home/tenazas/.openclaw/aider-venv/bin/aider",
    },
    "pullable_ollama_models": [
        "qwen2.5:3b", "qwen2.5:7b", "qwen2.5-coder:14b",
        "qwen3:8b", "phi4-mini", "nomic-embed-text",
    ],
    "vram_management": {
        "enabled": True,
        "required_gb_for_aider": 12,
        "protected_models": [],
        "restore_after_fix": True,
        "unload_warning_models": ["qwen3:8b", "qwen3:30b", "gemma3:12b"],
    },
    "diff_limits": {"max_lines": 50, "max_files": 3},
    "timeouts": {
        "aider_seconds": 180,
        "tests_seconds": 300,
        "ollama_pull_seconds": 900,
        "tier3_approval_hours": 24,
    },
}


# ── Config / State ──────────────────────────────────────────────

def load_config() -> dict:
    try:
        if CONFIG_PATH.exists():
            cfg = json.loads(CONFIG_PATH.read_text())
            # Merge defaults for missing keys
            for k, v in DEFAULT_CONFIG.items():
                if k not in cfg:
                    cfg[k] = v
            return cfg
    except Exception:
        pass
    # Write defaults
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2))
    return dict(DEFAULT_CONFIG)


def load_state() -> dict:
    try:
        if STATE_PATH.exists():
            return json.loads(STATE_PATH.read_text())
    except Exception:
        pass
    return {"tier1": [], "tier2": [], "tier3": [],
            "last_run": None, "total_fixes": 0, "total_proposals": 0,
            "tier2_consecutive_failures": 0, "tier2_paused_until": None}


def save_state(state: dict):
    try:
        STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        STATE_PATH.write_text(json.dumps(state, indent=2, default=str))
    except Exception as e:
        log.error("save state: %s", e)


def append_log(entry: dict):
    """Append to audit log (JSONL)."""
    try:
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        entry["ts"] = datetime.now().isoformat()
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(entry, default=str) + "\n")
    except Exception:
        pass


def record(state: dict, tier: int, probe: str, action: str, success: bool,
           detail: str = ""):
    key = f"tier{tier}"
    state.setdefault(key, []).append({
        "ts": time.time(), "dt": datetime.now().isoformat(),
        "probe": probe, "action": action, "success": success, "detail": detail,
    })
    state[key] = state[key][-100:]  # cap
    append_log({"tier": tier, "probe": probe, "action": action,
                "success": success, "detail": detail})


# ── Rate limiting ───────────────────────────────────────────────

def _count_recent(actions: list, hours: float = 1.0, **filters) -> int:
    cutoff = time.time() - (hours * 3600)
    count = 0
    for a in actions:
        if a.get("ts", 0) < cutoff:
            continue
        if all(a.get(k) == v for k, v in filters.items()):
            count += 1
    return count


def rate_ok_tier1(state: dict, config: dict, probe: str) -> bool:
    limit = config["rate_limits"]["tier_1_restarts_per_container_per_hour"]
    return _count_recent(state.get("tier1", []), 1.0, probe=probe) < limit


def rate_ok_tier2(state: dict, config: dict) -> bool:
    limit = config["rate_limits"]["tier_2_merges_per_day"]
    today = date.today().isoformat()
    today_merges = sum(1 for a in state.get("tier2", [])
                       if a.get("dt", "").startswith(today) and a.get("success"))
    if today_merges >= limit:
        return False
    # Check pause
    paused = state.get("tier2_paused_until")
    if paused and datetime.now().isoformat() < paused:
        return False
    return True


def rate_ok_tier2_file(state: dict, config: dict, filepath: str) -> bool:
    limit = config["rate_limits"]["tier_2_changes_per_file_per_day"]
    today = date.today().isoformat()
    count = sum(1 for a in state.get("tier2", [])
                if a.get("dt", "").startswith(today) and filepath in a.get("detail", ""))
    return count < limit


# ── Pre-flight ──────────────────────────────────────────────────

def preflight(config: dict) -> dict:
    """Run pre-flight checks. Returns {ok, errors}."""
    errors = []

    # Kill switch
    if KILL_SWITCH.exists():
        return {"ok": False, "errors": ["AUTOFIX_DISABLED exists (kill switch)"]}

    # Config
    if not CONFIG_PATH.exists():
        errors.append("config missing (will create defaults)")

    # Sweep baseline
    sweep_count = get_sweep_count()
    if sweep_count < 20:
        errors.append(f"insufficient baseline: {sweep_count}/20 sweeps")

    # LLM (only warn, don't fail)
    llm = check_llm()
    if not llm["recommended"]:
        errors.append(f"no LLM: {llm['reason']}")

    return {"ok": len(errors) == 0 or
            all("insufficient" not in e and "DISABLED" not in e for e in errors),
            "errors": errors,
            "sweep_count": sweep_count,
            "llm": llm}


# ════════════════════════════════════════════════════════════════
#  TIER 1 — OPS
# ════════════════════════════════════════════════════════════════

# Containers Tier 1 can restart (safety whitelist)
RESTARTABLE = {
    "chromadb", "prometheus", "grafana", "piper", "gluetun",
    "qbittorrent", "sonarr", "radarr", "lidarr", "prowlarr",
    "sabnzbd", "seerr", "requestrr", "tautulli",
}
PROTECTED_CONTAINERS = {"openclaw", "watchtower", "portainer"}

# Probe → container mapping for restarts
PROBE_CONTAINER = {
    "chromadb": "chromadb",
    "chromadb_frozen": "chromadb",
    "prometheus": "prometheus",
    "grafana": "grafana",
    "grafana_cache": "grafana",
    "piper": "piper",
    "vpn": "gluetun",
}


def exec_tier1(probe: str, detail: dict, config: dict, state: dict,
               dry_run: bool = False) -> dict:
    """Execute Tier 1 ops action."""
    # Container restart
    container = PROBE_CONTAINER.get(probe)
    if container:
        if container in PROTECTED_CONTAINERS:
            return {"success": False, "action": "blocked",
                    "reason": f"{container} is protected"}
        if container not in RESTARTABLE:
            return {"success": False, "action": "blocked",
                    "reason": f"{container} not restartable"}
        if not rate_ok_tier1(state, config, probe):
            return {"success": False, "action": "rate_limited",
                    "reason": "rate limit exceeded"}
        if dry_run:
            return {"success": True, "action": "dry_run",
                    "command": f"docker restart {container}"}
        try:
            r = subprocess.run(["docker", "restart", container],
                               capture_output=True, text=True, timeout=30)
            return {"success": r.returncode == 0, "action": "restart",
                    "container": container,
                    "output": r.stdout.strip()[:200]}
        except Exception as e:
            return {"success": False, "action": "restart", "error": str(e)}

    # Missing containers
    if probe == "containers":
        missing = detail.get("missing", [])
        results = []
        for name in missing[:3]:
            if name in PROTECTED_CONTAINERS or name not in RESTARTABLE:
                results.append({"container": name, "success": False, "action": "blocked"})
                continue
            if dry_run:
                results.append({"container": name, "success": True, "action": "dry_run"})
                continue
            try:
                r = subprocess.run(["docker", "restart", name],
                                   capture_output=True, text=True, timeout=30)
                results.append({"container": name, "success": r.returncode == 0,
                                "action": "restart"})
            except Exception as e:
                results.append({"container": name, "success": False, "error": str(e)})
        return {"success": all(r.get("success") for r in results),
                "action": "restart_missing", "results": results}

    # Ollama missing model
    if probe == "ollama_gpu":
        missing_models = detail.get("missing_models", [])
        pullable = set(config.get("pullable_ollama_models", []))
        for model in missing_models:
            if model in pullable:
                if dry_run:
                    return {"success": True, "action": "dry_run",
                            "command": f"ollama pull {model}"}
                return ollama_pull(model, pullable,
                                  config["timeouts"]["ollama_pull_seconds"])
        return None  # no pullable models missing

    # Ollama not loaded → force load
    if probe == "ollama_vram":
        if dry_run:
            return {"success": True, "action": "dry_run",
                    "command": "POST /api/generate (load model)"}
        try:
            payload = json.dumps({
                "model": "qwen2.5:7b", "prompt": "ok", "stream": False,
                "options": {"num_predict": 1},
            }).encode()
            resp = urlopen(Request(
                "http://192.168.2.6:11434/api/generate",
                data=payload, headers={"Content-Type": "application/json"},
            ), timeout=30)
            return {"success": resp.status == 200, "action": "load_model"}
        except Exception as e:
            return {"success": False, "action": "load_model", "error": str(e)}

    # Metrics exporter check/restart
    if probe == "metrics_exporter":
        if dry_run:
            return {"success": True, "action": "dry_run", "command": "pgrep metrics_server"}
        try:
            r = subprocess.run(["pgrep", "-f", "metrics_server.py"],
                               capture_output=True, text=True, timeout=5)
            if r.returncode == 0:
                return {"success": True, "action": "already_running"}
            subprocess.Popen(
                ["python3", "/home/tenazas/runtime/metrics_server.py"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return {"success": True, "action": "started"}
        except Exception as e:
            return {"success": False, "action": "check_process", "error": str(e)}

    return None  # no Tier 1 action for this probe


# ════════════════════════════════════════════════════════════════
#  TIER 2 — CODE
# ════════════════════════════════════════════════════════════════

def exec_tier2(probe: str, detail: dict, config: dict, state: dict,
               llm: dict, dry_run: bool = False) -> dict:
    """Execute Tier 2 code fix with VRAM management."""
    if not llm.get("recommended"):
        return {"success": False, "reason": "no LLM available", "escalate": 3}

    ctx = collect_context(probe, detail)
    if not ctx["files"] and not ctx["log_tail"]:
        return {"success": False, "reason": "no stacktrace or files", "escalate": 3}

    prompt = build_prompt(probe, detail, ctx)

    if dry_run:
        return {"success": True, "action": "dry_run",
                "model": llm["model"], "files": ctx["files"],
                "prompt_len": len(prompt)}

    if not rate_ok_tier2(state, config):
        return {"success": False, "reason": "rate limit", "escalate": 3}

    # VRAM management: free GPU before Aider
    vram_cfg = config.get("vram_management", {})
    unloaded_models = []
    if vram_cfg.get("enabled", True) and llm.get("backend") == "ollama":
        unloaded_models = ensure_vram_available(
            required_gb=vram_cfg.get("required_gb_for_aider", 12),
            protected_models=vram_cfg.get("protected_models", []),
            warning_models=vram_cfg.get("unload_warning_models", []),
            aider_model=llm.get("model", ""),
        )
        if unloaded_models:
            notify(f"⚙️ VRAM: descargados temporalmente: {', '.join(unloaded_models)}")

    # Git setup
    branch = create_branch(probe)

    try:
        # Run Aider
        aider_result = run_aider(
            prompt, ctx["files"], llm["model"], llm["backend"],
            timeout=config["timeouts"]["aider_seconds"])

        if aider_result.get("error"):
            cleanup_branch(branch)
            return {"success": False, "reason": aider_result["error"],
                    "escalate": 3}

        if aider_result.get("unfixable"):
            cleanup_branch(branch)
            return {"success": False,
                    "reason": f"UNFIXABLE: {aider_result['unfixable_reason']}",
                    "escalate": 3}

        # Validate diff
        validation = validate_diff(
            config["protected_files"],
            config["diff_limits"]["max_lines"],
            config["diff_limits"]["max_files"])

        if not validation["valid"]:
            cleanup_branch(branch)
            return {"success": False,
                    "reason": f"diff rejected: {validation['reason']}",
                    "escalate": 3, "diff": get_diff_summary()}

        # Run tests
        test_result = _run_tests(validation.get("files", []), config)
        if not test_result["success"] and not test_result.get("skipped"):
            cleanup_branch(branch)
            return {"success": False, "reason": "tests failed",
                    "tests": test_result, "escalate": 3,
                    "diff": get_diff_summary()}

        # Commit + merge
        if not commit_fix(probe, llm["model"]):
            cleanup_branch(branch)
            return {"success": False, "reason": "commit failed", "escalate": 3}

        merge = merge_to_main(branch)
        if not merge["success"]:
            cleanup_branch(branch)
            return {"success": False, "reason": merge["reason"], "escalate": 3}

        # Track consecutive failures reset
        state["tier2_consecutive_failures"] = 0

        changes = f"+{validation.get('insertions', 0)}/-{validation.get('deletions', 0)}"
        return {"success": True, "action": "merged", "sha": merge["sha"],
                "branch": branch, "model": llm["model"],
                "files": validation.get("files", []), "changes": changes}

    except Exception as e:
        cleanup_branch(branch)
        return {"success": False, "reason": str(e), "escalate": 3}
    finally:
        # Restore VRAM: reload models that were unloaded
        if unloaded_models and vram_cfg.get("restore_after_fix", True):
            restored = restore_models(unloaded_models)
            if restored:
                notify(f"♻️ VRAM: restaurados: {', '.join(restored)}")


def _run_tests(files: list[str], config: dict) -> dict:
    """Run tests for changed files."""
    test_dir = Path("/home/tenazas/tests") if _IN_CONTAINER else Path(__file__).parent.parent / "tests"
    tests = []
    for f in files:
        base = Path(f).stem
        t = test_dir / f"test_{base}.py"
        if t.exists() and str(t) not in tests:
            tests.append(str(t))

    if not tests:
        return {"success": True, "skipped": True}

    results = []
    for t in tests[:3]:
        try:
            r = subprocess.run(["python3", t], capture_output=True, text=True,
                               timeout=config["timeouts"]["tests_seconds"])
            results.append({"test": Path(t).name, "success": r.returncode == 0,
                            "output": r.stderr[-500:] if r.returncode != 0 else ""})
        except Exception as e:
            results.append({"test": Path(t).name, "success": False, "error": str(e)})

    return {"success": all(r["success"] for r in results), "tests": results}


# ════════════════════════════════════════════════════════════════
#  TIER 3 — PROPOSAL
# ════════════════════════════════════════════════════════════════

def exec_tier3(probe: str, detail: dict, reason: str = "",
               diff: str = "", dry_run: bool = False) -> dict:
    """Create Tier 3 proposal."""
    regression = classify_regression(probe)
    history = get_probe_history(probe, 5)

    if dry_run:
        return {"success": True, "action": "dry_run", "tier": 3,
                "probe": probe, "regression": regression,
                "reason": reason or detail.get("message", "")}

    notify_tier3_proposal(probe, regression, detail, reason, diff, history)
    return {"success": True, "action": "notified", "tier": 3,
            "probe": probe, "regression": regression}


# ════════════════════════════════════════════════════════════════
#  POST-DEPLOY MONITORING
# ════════════════════════════════════════════════════════════════

def post_deploy_check(sha: str, score_before: int, dry_run: bool = False) -> dict:
    """Wait and re-check health score after a Tier 2 merge."""
    if dry_run:
        return {"action": "dry_run", "sha": sha}

    # Wait 60s (not 5 min in practice, but enough for a sweep cycle)
    time.sleep(60)

    score_after = get_latest_score()
    if score_after is None:
        return {"action": "no_score", "sha": sha}

    drop = (score_before or 100) - (score_after or 100)
    if drop > 10:
        # Auto-rollback
        rb = rollback(sha)
        if rb["success"]:
            notify_tier2_rollback(sha, f"health drop: {score_before}→{score_after}",
                                  score_before, score_after)
        return {"action": "rollback", "sha": sha, "score_before": score_before,
                "score_after": score_after, "rollback": rb}

    return {"action": "ok", "score_before": score_before,
            "score_after": score_after}


# ════════════════════════════════════════════════════════════════
#  MAIN ORCHESTRATOR
# ════════════════════════════════════════════════════════════════

def process(dry_run: bool = False, force_tier: int | None = None) -> dict:
    """Main processing loop: get failures, classify, execute."""
    config = load_config()
    state = load_state()
    tiers = config.get("tier_enabled", {})

    failures = get_latest_failures()
    if not failures:
        return {"results": [], "total": 0, "message": "no failures"}

    score_before = get_latest_score()
    llm_info = None  # lazy
    results = []

    for failure in failures:
        probe = failure["probe"]
        detail = failure.get("detail", {})
        status = failure.get("status", "fail")

        # Expected degradation filter
        skip, skip_reason = should_skip(probe, status, config)
        if skip:
            results.append({"probe": probe, "action": "skipped", "reason": skip_reason})
            continue

        # Never existed → Tier 3 install decision
        if probe in config.get("never_existed", []):
            if tiers.get("tier_3", True):
                r = exec_tier3(probe, detail, "never installed — needs setup decision",
                               dry_run=dry_run)
                record(state, 3, probe, "never_existed", r["success"])
                results.append({"probe": probe, **r})
            else:
                results.append({"probe": probe, "action": "skipped",
                                "reason": "never_existed, tier3 disabled"})
            continue

        # Classify
        classification = classify_tier(probe, detail, config)
        tier = force_tier or classification["tier"]
        regression = classification["regression"]

        # Tier 0 = skip (flapping)
        if tier == 0:
            results.append({"probe": probe, "action": "skipped",
                            "reason": classification["reason"]})
            continue

        # ── Tier 1 ──
        if tier == 1 and tiers.get("tier_1", False):
            if rate_ok_tier1(state, config, probe):
                r = exec_tier1(probe, detail, config, state, dry_run)
                if r is not None:
                    record(state, 1, probe, r.get("action", ""), r.get("success", False),
                           r.get("output", r.get("error", ""))[:200])
                    if r.get("success") and not dry_run:
                        state["total_fixes"] = state.get("total_fixes", 0) + 1
                        notify_tier1(probe, r["action"],
                                     "OK" if r["success"] else "FAILED")
                    results.append({"probe": probe, "tier": 1, **r})
                    if r.get("success"):
                        continue
                    # Tier 1 failed → try Tier 2
                    tier = 2

        # ── Tier 2 ──
        if tier == 2 and tiers.get("tier_2", False):
            # Check consecutive failures
            consec = state.get("tier2_consecutive_failures", 0)
            max_consec = config["rate_limits"]["tier_2_consecutive_failures_before_pause"]
            if consec >= max_consec:
                if not state.get("tier2_paused_until"):
                    pause_until = datetime.now().isoformat()[:10] + "T23:59:59"
                    state["tier2_paused_until"] = pause_until
                    notify(f"⏸️ Tier 2 pausado ({consec} fallos consecutivos)")
                tier = 3  # degrade

            if tier == 2:
                if llm_info is None:
                    llm_info = check_llm()
                if not llm_info["recommended"]:
                    if not dry_run:
                        notify_degraded(llm_info["reason"])
                    tier = 3
                else:
                    r = exec_tier2(probe, detail, config, state, llm_info, dry_run)
                    record(state, 2, probe, r.get("action", ""),
                           r.get("success", False), str(r.get("files", "")))

                    if r.get("success"):
                        state["total_fixes"] = state.get("total_fixes", 0) + 1
                        if not dry_run:
                            notify_tier2(probe, llm_info["model"],
                                         r.get("files", []), r.get("changes", ""),
                                         r.get("sha", ""))
                            # Post-deploy check
                            post = post_deploy_check(r["sha"], score_before, dry_run)
                            r["post_deploy"] = post
                        results.append({"probe": probe, "tier": 2, **r})
                        continue
                    else:
                        state["tier2_consecutive_failures"] = consec + 1
                        if r.get("escalate") == 3:
                            tier = 3
                        else:
                            results.append({"probe": probe, "tier": 2, **r})
                            continue

        # ── Tier 3 ──
        if tier >= 3 and tiers.get("tier_3", True):
            escalation_reason = ""
            diff = ""
            # Check if we got here from escalation
            if len(results) > 0 and results[-1].get("probe") == probe:
                escalation_reason = results[-1].get("reason", "")
                diff = results[-1].get("diff", "")

            r = exec_tier3(probe, detail, escalation_reason, diff, dry_run)
            record(state, 3, probe, "proposal", r["success"])
            state["total_proposals"] = state.get("total_proposals", 0) + 1
            results.append({"probe": probe, **r})

    state["last_run"] = datetime.now().isoformat()
    save_state(state)

    return {
        "results": results,
        "total": len(results),
        "fixes": sum(1 for r in results if r.get("success") and r.get("tier") in (1, 2)),
        "proposals": sum(1 for r in results if r.get("tier") == 3),
        "skipped": sum(1 for r in results if r.get("action") == "skipped"),
    }


def get_status() -> dict:
    """Get current agent status."""
    config = load_config()
    state = load_state()
    llm = check_llm()
    tiers = config.get("tier_enabled", {})
    cutoff = time.time() - 3600

    return {
        "kill_switch": KILL_SWITCH.exists(),
        "tier1": tiers.get("tier_1", False),
        "tier2": tiers.get("tier_2", False),
        "tier3": tiers.get("tier_3", True),
        "llm_backend": llm.get("recommended") or "none",
        "llm_reason": llm.get("reason", ""),
        "last_run": state.get("last_run"),
        "total_fixes": state.get("total_fixes", 0),
        "total_proposals": state.get("total_proposals", 0),
        "t1_hour": _count_recent(state.get("tier1", []), 1.0),
        "t2_hour": _count_recent(state.get("tier2", []), 1.0),
        "t3_hour": _count_recent(state.get("tier3", []), 1.0),
        "tier2_consec_failures": state.get("tier2_consecutive_failures", 0),
        "tier2_paused_until": state.get("tier2_paused_until"),
        "sweep_count": get_sweep_count(),
        "expected_degradations": len(config.get("expected_degradations", [])),
        "never_existed": config.get("never_existed", []),
    }


# ════════════════════════════════════════════════════════════════
#  CLI
# ════════════════════════════════════════════════════════════════

def main():
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s")

    parser = argparse.ArgumentParser(description="Tenazas Autofix Agent")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--watch", action="store_true")
    parser.add_argument("--force-tier", type=int, choices=[1, 2, 3])
    parser.add_argument("--rollback", type=str, metavar="SHA")
    parser.add_argument("--status", action="store_true")
    parser.add_argument("--enable", action="store_true", help="Remove kill switch")
    parser.add_argument("--kill", action="store_true", help="Create kill switch")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    # Kill/enable
    if args.kill:
        KILL_SWITCH.parent.mkdir(parents=True, exist_ok=True)
        KILL_SWITCH.write_text(f"disabled at {datetime.now().isoformat()}")
        print("[autofix] kill switch ON — agent disabled")
        return

    if args.enable:
        if KILL_SWITCH.exists():
            KILL_SWITCH.unlink()
            print("[autofix] kill switch OFF — agent enabled")
        else:
            print("[autofix] already enabled")
        return

    # Status
    if args.status:
        status = get_status()
        if args.json:
            print(json.dumps(status, indent=2))
        else:
            print(format_status(status))
        return

    # Rollback
    if args.rollback:
        r = rollback(args.rollback)
        print(json.dumps(r, indent=2) if args.json else
              f"Rollback {'OK' if r['success'] else 'FAILED'}: {r}")
        if r["success"]:
            notify(f"⏪ Manual rollback: `{args.rollback}`")
        return

    # Pre-flight
    pf = preflight(load_config())
    if not pf["ok"]:
        for e in pf["errors"]:
            print(f"[preflight] {e}")
        if any("DISABLED" in e for e in pf["errors"]):
            return  # silent exit for kill switch
        sys.exit(1)

    # Watch mode
    if args.watch:
        print("[autofix] watch mode — interval 300s")
        while True:
            try:
                r = process(dry_run=args.dry_run, force_tier=args.force_tier)
                fixes = r.get("fixes", 0)
                proposals = r.get("proposals", 0)
                skipped = r.get("skipped", 0)
                print(f"[autofix] {fixes} fixes, {proposals} proposals, {skipped} skipped")
            except Exception as e:
                log.error("watch cycle error: %s", e)
            time.sleep(300)
    else:
        # One-shot
        result = process(dry_run=args.dry_run, force_tier=args.force_tier)
        if args.json:
            print(json.dumps(result, indent=2, default=str))
        else:
            print(f"[autofix] {result.get('fixes', 0)} fixes, "
                  f"{result.get('proposals', 0)} proposals, "
                  f"{result.get('skipped', 0)} skipped")


if __name__ == "__main__":
    main()
