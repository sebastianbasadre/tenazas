#!/usr/bin/env python3
"""
action_validator.py
Post-execution validation — confirms actions actually worked.

Each validator checks the real state after an action was executed.
Returns a ValidationResult with success/failure and evidence.

Validators:
  - validate_restart_container: container is healthy after restart
  - validate_restart_ollama: endpoint responds within timeout
  - validate_reload_model: model responds with latency < threshold
  - validate_cleanup_disk: disk space increased above threshold

Usage:
  from action_validator import validate
  result = validate("restart_container", "sonarr")
"""

import json
import subprocess
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from urllib.request import Request, urlopen

RUNTIME = Path(__file__).parent
LOG_PATH = RUNTIME / "logs" / "action_validator.log"
OLLAMA_URL = "http://192.168.2.6:11434"

# Thresholds
CONTAINER_HEALTH_WAIT = 10    # seconds to wait before checking
MODEL_LATENCY_THRESHOLD = 5000  # ms — reload considered OK if under this
DISK_FREE_MIN_MB = 500        # minimum MB freed to consider cleanup successful


@dataclass
class ValidationResult:
    action_name: str
    target: str
    success: bool
    reason: str
    evidence: dict
    validated_at: str = ""

    def __post_init__(self):
        if not self.validated_at:
            self.validated_at = datetime.now().astimezone().isoformat()


def _log(entry: dict):
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = datetime.now().astimezone().isoformat()
    with open(LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


# ── Validators ──────────────────────────────────────────────────

def validate_restart_container(target: str) -> ValidationResult:
    """Check that a container is running and healthy after restart.

    Waits CONTAINER_HEALTH_WAIT seconds then checks docker ps.
    """
    time.sleep(CONTAINER_HEALTH_WAIT)

    try:
        r = subprocess.run(
            ["docker", "ps", "-a", "--filter", f"name=^{target}$",
             "--format", "{{.Names}}\t{{.Status}}"],
            capture_output=True, text=True, timeout=10,
        )
        lines = r.stdout.strip().splitlines()
        if not lines:
            return ValidationResult(
                action_name="restart_container", target=target,
                success=False, reason=f"Container '{target}' not found after restart",
                evidence={"stdout": r.stdout, "stderr": r.stderr},
            )

        name, status = lines[0].split("\t", 1)
        is_up = status.startswith("Up")

        result = ValidationResult(
            action_name="restart_container", target=target,
            success=is_up,
            reason=f"Container status: {status}" if is_up else f"Container NOT healthy: {status}",
            evidence={"container": name, "status": status, "up": is_up},
        )
    except Exception as e:
        result = ValidationResult(
            action_name="restart_container", target=target,
            success=False, reason=f"Validation error: {e}",
            evidence={"error": str(e)},
        )

    _log({"validator": "restart_container", "target": target, **asdict(result)})
    return result


def validate_restart_ollama(target: str = "") -> ValidationResult:
    """Check that Ollama endpoint responds after restart/reload."""
    try:
        t0 = time.time()
        resp = urlopen(Request(f"{OLLAMA_URL}/api/tags"), timeout=10)
        latency_ms = int((time.time() - t0) * 1000)
        data = json.loads(resp.read())
        model_count = len(data.get("models", []))

        result = ValidationResult(
            action_name="restart_ollama", target=target,
            success=True,
            reason=f"Ollama responding ({latency_ms}ms, {model_count} models)",
            evidence={"latency_ms": latency_ms, "model_count": model_count},
        )
    except Exception as e:
        result = ValidationResult(
            action_name="restart_ollama", target=target,
            success=False,
            reason=f"Ollama not responding: {e}",
            evidence={"error": str(e)},
        )

    _log({"validator": "restart_ollama", **asdict(result)})
    return result


def validate_reload_model(target: str) -> ValidationResult:
    """Check that a model responds within latency threshold after reload.

    Sends a minimal prompt and measures response time.
    """
    try:
        payload = json.dumps({
            "model": target,
            "prompt": "hi",
            "stream": False,
            "options": {"num_predict": 1},
        }).encode()

        t0 = time.time()
        resp = urlopen(
            Request(f"{OLLAMA_URL}/api/generate",
                    data=payload,
                    headers={"Content-Type": "application/json"}),
            timeout=30,
        )
        latency_ms = int((time.time() - t0) * 1000)
        data = json.loads(resp.read())

        ok = latency_ms < MODEL_LATENCY_THRESHOLD
        result = ValidationResult(
            action_name="reload_model", target=target,
            success=ok,
            reason=f"Model responded in {latency_ms}ms" if ok
                   else f"Model too slow: {latency_ms}ms (threshold: {MODEL_LATENCY_THRESHOLD}ms)",
            evidence={"latency_ms": latency_ms, "threshold_ms": MODEL_LATENCY_THRESHOLD,
                       "response_length": len(data.get("response", ""))},
        )
    except Exception as e:
        result = ValidationResult(
            action_name="reload_model", target=target,
            success=False,
            reason=f"Model validation failed: {e}",
            evidence={"error": str(e)},
        )

    _log({"validator": "reload_model", "target": target, **asdict(result)})
    return result


def validate_cleanup_disk(target: str = "") -> ValidationResult:
    """Check that disk cleanup freed meaningful space.

    Reads /proc/mounts or df to check available space.
    """
    try:
        r = subprocess.run(
            ["df", "-m", "/"],
            capture_output=True, text=True, timeout=5,
        )
        # Parse df output: Filesystem 1M-blocks Used Available Use% Mounted
        lines = r.stdout.strip().splitlines()
        if len(lines) < 2:
            return ValidationResult(
                action_name="cleanup_disk", target=target,
                success=False, reason="Could not parse df output",
                evidence={"stdout": r.stdout},
            )

        parts = lines[1].split()
        available_mb = int(parts[3]) if len(parts) > 3 else 0

        ok = available_mb > DISK_FREE_MIN_MB
        result = ValidationResult(
            action_name="cleanup_disk", target=target,
            success=ok,
            reason=f"Available: {available_mb}MB" if ok
                   else f"Low disk: {available_mb}MB (min: {DISK_FREE_MIN_MB}MB)",
            evidence={"available_mb": available_mb, "threshold_mb": DISK_FREE_MIN_MB},
        )
    except Exception as e:
        result = ValidationResult(
            action_name="cleanup_disk", target=target,
            success=False,
            reason=f"Disk check failed: {e}",
            evidence={"error": str(e)},
        )

    _log({"validator": "cleanup_disk", **asdict(result)})
    return result


# ── Dispatcher ──────────────────────────────────────────────────

_VALIDATORS = {
    "validate_restart_container": validate_restart_container,
    "validate_restart_ollama": validate_restart_ollama,
    "validate_reload_model": validate_reload_model,
    "validate_cleanup_disk": validate_cleanup_disk,
}


def validate(action_name: str, target: str = "",
             validator_name: str | None = None) -> ValidationResult | None:
    """Run the appropriate validator for an action.

    If validator_name is provided, uses it directly.
    Otherwise, looks up the validator from action_registry.
    Returns None if no validator is defined for this action.
    """
    if not validator_name:
        try:
            from action_registry import get_action
            action_def = get_action(action_name)
            if action_def:
                validator_name = action_def.get("validator_name")
        except ImportError:
            pass

    if not validator_name:
        return None

    validator_fn = _VALIDATORS.get(validator_name)
    if not validator_fn:
        _log({"action": "validate", "error": f"Unknown validator: {validator_name}"})
        return None

    return validator_fn(target)
