#!/usr/bin/env python3
"""
Tenazas Runtime Supervisor

Protege el runtime principal contra:
- deadlocks
- loops infinitos
- subprocess colgados
- ejecuciones concurrentes

Diseñado para ejecutarse desde cron cada 5 minutos.
"""

import os
import subprocess
import time
from pathlib import Path

LOCKFILE = "/tmp/tenazas_runtime.lock"
RUNTIME_SCRIPT = "/home/tenazas/runtime/runtime.py"
MAX_RUNTIME_SECONDS = 240   # 4 minutos
LOGFILE = "/home/tenazas/runtime/logs/runtime_supervisor.log"


def log(msg):
    ts = time.strftime("%Y-%m-%d %H:%M:%S")
    Path(LOGFILE).parent.mkdir(parents=True, exist_ok=True)
    with open(LOGFILE, "a") as f:
        f.write(f"{ts} {msg}\n")


def acquire_lock():
    if os.path.exists(LOCKFILE):
        age = time.time() - os.path.getmtime(LOCKFILE)

        if age < MAX_RUNTIME_SECONDS:
            log("runtime already running, skipping")
            return False

        log("stale lock detected, removing")
        os.remove(LOCKFILE)

    Path(LOCKFILE).touch()
    return True


def release_lock():
    if os.path.exists(LOCKFILE):
        os.remove(LOCKFILE)


def run_runtime():
    log("starting runtime")

    process = subprocess.Popen(
        ["python3", RUNTIME_SCRIPT],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    start = time.time()

    while True:
        if process.poll() is not None:
            elapsed = round(time.time() - start, 1)
            rc = process.returncode
            if rc == 0:
                log(f"runtime finished normally ({elapsed}s)")
            else:
                stderr = process.stderr.read().decode()[-200:] if process.stderr else ""
                log(f"runtime exited with code {rc} ({elapsed}s) {stderr}")
            return

        if time.time() - start > MAX_RUNTIME_SECONDS:
            log("runtime timeout exceeded, killing")
            process.kill()

            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.terminate()

            return

        time.sleep(2)


def main():
    if not acquire_lock():
        return

    try:
        run_runtime()
    finally:
        release_lock()


if __name__ == "__main__":
    main()
