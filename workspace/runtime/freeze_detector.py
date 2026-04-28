#!/usr/bin/env python3
"""
Freeze Detector — Detects when the runtime stops making progress.

Each runtime phase calls heartbeat("step_name"). If more than
FREEZE_THRESHOLD seconds pass without a heartbeat, the system
is considered frozen.

Thread-safe for use with the watchdog thread.
"""

import json
import threading
import time
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
TELEMETRY_LOG = BASE_DIR / "logs" / "freeze_detector.log"

FREEZE_THRESHOLD = 30  # seconds without heartbeat = frozen



def _log(entry: dict):
    TELEMETRY_LOG.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(TELEMETRY_LOG, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")


class FreezeDetector:
    """Track runtime progress via heartbeats."""

    def __init__(self, threshold: float = FREEZE_THRESHOLD):
        self._lock = threading.Lock()
        self._threshold = threshold
        self._last_beat = time.monotonic()
        self._last_step = "init"
        self._step_start = time.monotonic()
        self._step_durations = {}
        self._freeze_count = 0
        self._active = False

    def start(self):
        """Mark the detector as active (runtime cycle started)."""
        with self._lock:
            self._active = True
            self._last_beat = time.monotonic()
            self._last_step = "start"
            self._step_durations = {}

    def stop(self):
        """Mark the detector as inactive (runtime cycle finished)."""
        with self._lock:
            self._active = False

    def heartbeat(self, step: str):
        """Record progress at a named step."""
        with self._lock:
            now = time.monotonic()
            # Record duration of previous step
            if self._last_step and self._last_step != "init":
                duration = now - self._step_start
                self._step_durations[self._last_step] = round(duration, 3)
            self._last_beat = now
            self._last_step = step
            self._step_start = now

    def check(self) -> dict:
        """Check if runtime is frozen.

        Returns: {status, last_step, since_last_beat, frozen}
        """
        with self._lock:
            if not self._active:
                return {
                    "status": "inactive",
                    "last_step": self._last_step,
                    "since_last_beat": 0,
                    "frozen": False,
                }

            elapsed = time.monotonic() - self._last_beat
            frozen = elapsed > self._threshold

            if frozen:
                self._freeze_count += 1

            return {
                "status": "frozen" if frozen else "ok",
                "last_step": self._last_step,
                "since_last_beat": round(elapsed, 1),
                "frozen": frozen,
                "freeze_count": self._freeze_count,
            }

    def get_step_durations(self) -> dict:
        """Get durations of all completed steps."""
        with self._lock:
            return dict(self._step_durations)

    @property
    def freeze_count(self) -> int:
        with self._lock:
            return self._freeze_count


# Global singleton
_detector = FreezeDetector()


def get_detector() -> FreezeDetector:
    return _detector


def heartbeat(step: str):
    """Convenience: record heartbeat on global detector."""
    _detector.heartbeat(step)


def check() -> dict:
    """Convenience: check global detector."""
    return _detector.check()


def start():
    _detector.start()


def stop():
    _detector.stop()
