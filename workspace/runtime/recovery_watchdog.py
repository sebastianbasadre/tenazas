"""Recover unhealthy managed containers with a bounded restart budget."""

import json
import subprocess
import time
from pathlib import Path

from runtime_utils import load_json, write_atomic

STATE = Path("/home/tenazas/watchdog-state/recovery.json")
MANAGED = ("openclaw", "tenazas-job-runner")


def eligible(container, attempts, current):
    return (
        container.get("State", {}).get("Running", False)
        and container.get("State", {}).get("Health", {}).get("Status") == "unhealthy"
        and len([ts for ts in attempts if current - ts < 3600]) < 3
        and (not attempts or current - attempts[-1] > 120)
    )


def run_once():
    state = load_json(STATE, {})
    for name in MANAGED:
        result = subprocess.run(
            ["docker", "inspect", name], capture_output=True, timeout=10, check=False
        )
        if result.returncode:
            continue
        container = json.loads(result.stdout)[0]
        current = time.time()
        attempts = [ts for ts in state.get(name, []) if current - ts < 3600]
        if eligible(container, attempts, current):
            attempts.append(current)
            state[name] = attempts
            write_atomic(STATE, state)
            subprocess.run(
                ["docker", "restart", "--time", "45", name],
                capture_output=True,
                timeout=60,
                check=False,
            )
        state[name] = attempts
    write_atomic(STATE, state)


if __name__ == "__main__":
    while True:
        try:
            run_once()
        except (OSError, ValueError, subprocess.TimeoutExpired):
            pass
        time.sleep(30)
