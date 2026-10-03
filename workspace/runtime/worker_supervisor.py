#!/usr/bin/env python3
"""
Worker Supervisor — Orchestrates isolated worker subprocesses.

Enhanced supervisor that runs workers sequentially with individual timeouts.
Each worker is a separate subprocess — if one freezes or crashes, the others
still execute.

Falls back to runtime.py if workers directory doesn't exist.

Workers (in order of priority):
  1. runtime_worker    — core loop (state, events, tasks, memory) [60s timeout]
  2. diagnostics_worker — pattern analysis, infra diagnostics      [30s timeout]
  3. learning_worker   — background agents, goals, idle learning   [30s timeout]

Usage:
  python3 worker_supervisor.py          # run all workers
  python3 worker_supervisor.py --status # show last run status

Designed for DSM Task Scheduler (cron every 5 minutes).
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from process_lock import ProcessLock
from runtime_utils import StateLock, load_json, write_atomic

BASE_DIR = Path(__file__).parent
LOCKFILE = "/tmp/tenazas_worker_supervisor.lock"
STALE_LOCK_SECONDS = 600  # 10 minutes
_process_lock = None
LOG_FILE = BASE_DIR / "logs" / "worker_supervisor.log"
STATE_FILE = BASE_DIR / "supervisor_state.json"
WORKERS_DIR = BASE_DIR / "workers"

# Worker definitions: (name, script, timeout_seconds)
WORKERS = [
    ("runtime_worker", "workers/runtime_worker.py", 60),
    ("diagnostics_worker", "workers/diagnostics_worker.py", 30),
    ("rag_worker", "workers/rag_worker.py", 30),
    ("agents_worker", "workers/agents_worker.py", 30),
    ("learning_worker", "workers/learning_worker.py", 30),
]


def log(msg: str):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    with open(LOG_FILE, "a") as f:
        f.write(f"{ts} {msg}\n")


def acquire_lock() -> bool:
    global _process_lock
    if _process_lock is not None:
        return False
    candidate = ProcessLock(LOCKFILE)
    if not candidate.acquire():
        log("supervisor already running, skipping")
        return False
    _process_lock = candidate
    return True


def release_lock():
    global _process_lock
    if _process_lock is not None:
        _process_lock.release()
        _process_lock = None


def run_worker(name: str, script: str, timeout: int) -> dict:
    """Run a worker as a subprocess with timeout.

    Returns: {name, ok, elapsed, result, error}
    """
    script_path = BASE_DIR / script
    if not script_path.exists():
        return {"name": name, "ok": False, "elapsed": 0,
                "error": f"Script not found: {script}"}

    log(f"worker:{name} start (timeout={timeout}s)")
    start = time.time()

    try:
        proc = subprocess.Popen(
            [sys.executable, str(script_path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(BASE_DIR),
        )

        try:
            stdout, stderr = proc.communicate(timeout=timeout)
            elapsed = round(time.time() - start, 2)

            if proc.returncode == 0:
                try:
                    result = json.loads(stdout.decode())
                except (json.JSONDecodeError, UnicodeDecodeError):
                    result = {"raw": stdout.decode()[:200]}
                log(f"worker:{name} done ({elapsed}s)")
                return {"name": name, "ok": True, "elapsed": elapsed, "result": result}
            else:
                err = stderr.decode()[-200:] if stderr else ""
                log(f"worker:{name} failed rc={proc.returncode} ({elapsed}s) {err}")
                return {"name": name, "ok": False, "elapsed": elapsed,
                        "error": f"exit {proc.returncode}: {err}"}

        except subprocess.TimeoutExpired:
            elapsed = round(time.time() - start, 2)
            log(f"worker:{name} TIMEOUT ({elapsed}s), killing")
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.terminate()
            return {"name": name, "ok": False, "elapsed": elapsed,
                    "error": f"timeout after {timeout}s"}

    except Exception as e:
        elapsed = round(time.time() - start, 2)
        log(f"worker:{name} exception: {e}")
        return {"name": name, "ok": False, "elapsed": elapsed, "error": str(e)[:200]}


def update_metrics(results: list):
    """Persist per-worker durations to metrics_state.json."""
    try:
        metrics_file = BASE_DIR / "logs" / "metrics_state.json"
        state = {}
        try:
            with open(metrics_file) as f:
                state = json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            pass

        workers = state.get("workers", {})
        for r in results:
            workers[r["name"]] = {
                "ok": r["ok"],
                "elapsed": r.get("elapsed", 0),
            }
        state["workers"] = workers

        metrics_file.parent.mkdir(parents=True, exist_ok=True)
        with StateLock(metrics_file):
            current = load_json(metrics_file, {})
            current.setdefault("workers", {}).update(workers)
            write_atomic(metrics_file, current)
    except Exception as e:
        log(f"metrics update error: {e}")


def save_state(results: list, total_elapsed: float):
    """Save supervisor run state for status queries."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    write_atomic(STATE_FILE, {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "total_elapsed": total_elapsed,
            "completed_ts": time.time(),
            "workers": results,
        })


def run_all():
    """Execute all workers sequentially with isolation."""
    if not acquire_lock():
        return

    previous = load_json(STATE_FILE, {})
    minimum_interval = int(os.getenv("TENAZAS_SUPERVISOR_MIN_INTERVAL", "300"))
    if "--force" not in sys.argv and time.time() - previous.get("started_ts", 0) < minimum_interval:
        release_lock()
        log("runtime already executed in this scheduling interval, skipping")
        return

    total_start = time.time()
    log("supervisor start")
    results = []

    # Log rotation before workers run
    try:
        from runtime.log_rotation import rotate_all
        rotated = rotate_all()
        if rotated.get("rotated"):
            log(f"log rotation: {rotated['rotated']}")
    except Exception as e:
        log(f"log rotation error: {e}")

    try:
        for name, script, timeout in WORKERS:
            # Clear heartbeat before worker starts
            try:
                from runtime.worker_heartbeat import clear
                clear(name)
            except Exception:
                pass

            result = run_worker(name, script, timeout)
            results.append(result)
            try:
                from runtime.worker_heartbeat import complete
                complete(name)
            except ImportError:
                pass

            # Check for freeze after each worker
            try:
                from runtime.worker_heartbeat import check_all
                frozen = check_all()
                if frozen:
                    for f in frozen:
                        log(f"FREEZE: {f['worker']} stuck at {f['last_step']} for {f['since_seconds']}s")
            except Exception:
                pass
        total_elapsed = round(time.time() - total_start, 2)
        update_metrics(results)
        save_state(results, total_elapsed)
        state = load_json(STATE_FILE, {})
        state["started_ts"] = total_start
        if all(result["ok"] for result in results):
            state["last_success_ts"] = time.time()
        else:
            state["last_success_ts"] = previous.get("last_success_ts", 0)
        write_atomic(STATE_FILE, state)
    finally:
        release_lock()

    ok_count = sum(1 for r in results if r["ok"])
    fail_count = len(results) - ok_count
    log(f"supervisor done: {ok_count} ok, {fail_count} failed, {total_elapsed}s total")
    return results


def show_status():
    """Print last run status."""
    try:
        with open(STATE_FILE) as f:
            state = json.load(f)
        print(json.dumps(state, indent=2))
    except FileNotFoundError:
        print("No supervisor state found (has it run yet?)")


def main():
    if "--status" in sys.argv:
        show_status()
    else:
        results = run_all()
        if results is not None and any(not result["ok"] for result in results):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
