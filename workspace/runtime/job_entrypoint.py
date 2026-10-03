"""Execute only an allowlisted job inside OpenClaw, retaining its process lock."""

import subprocess
import sys
import os
import signal
from pathlib import Path

from job_runner import JOBS
from process_lock import ProcessLock


def main():
    name = sys.argv[1] if len(sys.argv) == 2 else ""
    if name not in JOBS:
        return 2
    lock = ProcessLock(Path("/tmp/tenazas-jobs") / (name + ".lock"))
    if not lock.acquire():
        return 75
    try:
        spec = JOBS[name]
        process = subprocess.Popen(spec.command, start_new_session=True)
        try:
            return process.wait(timeout=spec.timeout)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.wait(timeout=5)
            return 124
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
