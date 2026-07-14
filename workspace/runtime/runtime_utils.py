#!/usr/bin/env python3
"""
runtime_utils.py — Shared utility functions for all runtime modules.

Replaces the per-module _load_json/_save_json/_now duplicates (19+ copies)
with a single importable source of truth. write_atomic provides crash-safe
JSON persistence via tempfile + os.replace pattern.
"""

import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False


# -- JSON I/O --


def load_json(path, default=None):
    """Load JSON file, return default on missing/corrupt."""
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path, data):
    """Save data as JSON with indent=2. Creates parent dirs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False, default=str)


# -- Time --


def now():
    """Return current local time as timezone-aware datetime."""
    return datetime.now().astimezone()


def now_iso():
    """Return current local time as ISO8601 string."""
    return datetime.now().astimezone().isoformat()


# -- Atomic Writes --


def write_atomic(path, data):
    """Write JSON atomically: temp file + os.replace(). Crash-safe."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd = None
    try:
        tmp_fd = tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, suffix=".tmp", delete=False
        )
        json.dump(data, tmp_fd, indent=2, ensure_ascii=False, default=str)
        tmp_fd.close()
        os.replace(tmp_fd.name, path)
    except BaseException:
        if tmp_fd is not None:
            tmp_fd.close()
            try:
                os.unlink(tmp_fd.name)
            except OSError:
                pass
        raise


# -- File Locking --


class StateLock:
    """Advisory file lock for agent state. Context manager.

    Uses fcntl.flock() on Linux. Falls back to no-op on Windows.
    Combine with write_atomic() for crash-safe locked writes.
    """

    def __init__(self, path):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.fh = None

    def __enter__(self):
        if HAS_FCNTL:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = open(self.lock_path, "a+")
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self.fh:
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self.fh.close()
            self.fh = None
        return False


def locked_write_json(path, data):
    """Write JSON with advisory file lock (Linux) or atomic-only (Windows).

    Uses fcntl.flock() for mutual exclusion + write_atomic() for crash safety.
    Safe for concurrent cron + worker_supervisor access.
    """
    with StateLock(path):
        write_atomic(path, data)
