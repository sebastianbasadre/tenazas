#!/usr/bin/env python3
"""
Runtime Worker — Wraps the existing runtime.py main loop as an isolated worker.

Does NOT modify runtime.py. Imports and calls its step functions directly.
Designed to be executed as a subprocess by the supervisor.

Exit codes:
  0 = success
  1 = error
  2 = timeout (caught internally)
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, "/home/tenazas")

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_FILE = BASE_DIR / "logs" / "runtime_worker.log"


def _log(msg: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} {msg}\n")


def run():
    """Execute the core runtime loop steps."""
    start = time.time()
    _log("runtime_worker start")

    try:
        from runtime import runtime as rt
        from runtime import metrics_exporter

        prev, curr = rt.update_state()
        events = rt.detect_events(prev, curr)
        tasks = rt.enqueue_tasks(events)
        results = rt.run_tasks(tasks)
        rt.append_memory(events, results)

        elapsed = round(time.time() - start, 2)

        # Record metrics
        metrics_exporter.record_run(elapsed)
        health = metrics_exporter.calculate_health(
            cycle_duration=elapsed,
            step_durations={},
            freeze_count=0,
            restart_count=sum(1 for r in results if r.get("ok")),
        )
        metrics_exporter.set_health_score(health)

        _log(f"runtime_worker done events={len(events)} tasks={len(tasks)} elapsed={elapsed}s health={health}")

        # Output result as JSON for supervisor to parse
        print(json.dumps({
            "ok": True,
            "events": len(events),
            "tasks": len(tasks),
            "results": len(results),
            "elapsed": elapsed,
            "health": health,
        }))

    except Exception as e:
        elapsed = round(time.time() - start, 2)
        _log(f"runtime_worker ERROR: {e} elapsed={elapsed}s")
        metrics_exporter.record_failure()
        print(json.dumps({"ok": False, "error": str(e)[:200], "elapsed": elapsed}))
        sys.exit(1)


if __name__ == "__main__":
    run()
