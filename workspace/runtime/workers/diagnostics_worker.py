#!/usr/bin/env python3
"""
Diagnostics Worker — Runs pattern analysis and infra diagnostics.

Isolated subprocess. Safe to timeout without affecting core runtime.
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/tenazas")

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = BASE_DIR / "logs" / "diagnostics_worker.log"


def _log(msg: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} {msg}\n")


def run():
    start = time.time()
    _log("diagnostics_worker start")
    results = {}

    try:
        from runtime import infra_diagnostics
        from runtime.state_paths import private_state_path
        state_file = private_state_path(BASE_DIR)
        try:
            with open(state_file) as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            state = {}

        diag = infra_diagnostics.run_diagnostics(state)
        results["diagnostics"] = {
            "issues": len(diag.get("issues", [])),
            "health": diag.get("quick_health", "unknown"),
        }
    except Exception as e:
        results["diagnostics"] = {"error": str(e)[:200]}

    try:
        from runtime import infra_memory
        patterns = infra_memory.update_memory()
        results["patterns"] = {
            "found": len(patterns) if patterns else 0,
        }
    except Exception as e:
        results["patterns"] = {"error": str(e)[:200]}

    elapsed = round(time.time() - start, 2)
    _log(f"diagnostics_worker done elapsed={elapsed}s")
    print(json.dumps({"ok": True, "elapsed": elapsed, **results}))


if __name__ == "__main__":
    run()
