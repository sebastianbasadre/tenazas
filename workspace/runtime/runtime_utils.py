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


# -- JSON I/O --


def load_json(path, default=None):
    """Load JSON file, return default on missing/corrupt."""
    try:
        with open(path) as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default if default is not None else {}


def save_json(path, data):
    """Save data as JSON with indent=2. Creates parent dirs."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w") as f:
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
            mode="w", dir=path.parent, suffix=".tmp", delete=False
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
