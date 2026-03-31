#!/usr/bin/env python3
"""
Log Rotation — Ensures runtime logs don't grow indefinitely.

Scans workspace/runtime/logs/*.log and rotates any file exceeding MAX_SIZE.
Keeps MAX_BACKUPS rotated copies (.1, .2, etc).

Safe to call from any worker or supervisor. Does NOT modify log content.

Usage:
    from runtime.log_rotation import rotate_all
    rotate_all()
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"

MAX_SIZE = 5 * 1024 * 1024  # 5 MB
MAX_BACKUPS = 5


def rotate_file(log_path: Path):
    """Rotate a single log file if it exceeds MAX_SIZE.

    Rotation scheme: file.log → file.log.1, file.log.1 → file.log.2, etc.
    Oldest backup (file.log.{MAX_BACKUPS}) is deleted.
    """
    if not log_path.exists():
        return False

    if log_path.stat().st_size < MAX_SIZE:
        return False

    # Shift existing backups
    for i in range(MAX_BACKUPS, 0, -1):
        src = Path(f"{log_path}.{i}")
        dst = Path(f"{log_path}.{i + 1}")
        if i == MAX_BACKUPS and src.exists():
            src.unlink()  # Delete oldest
        elif src.exists():
            src.rename(dst)

    # Rotate current → .1
    backup = Path(f"{log_path}.1")
    log_path.rename(backup)

    # Create fresh empty log
    log_path.touch()
    return True


def rotate_all() -> dict:
    """Rotate all .log files in the logs directory.

    Returns: {"rotated": [list of rotated filenames], "checked": int}
    """
    if not LOGS_DIR.exists():
        return {"rotated": [], "checked": 0}

    rotated = []
    checked = 0

    for log_file in sorted(LOGS_DIR.glob("*.log")):
        checked += 1
        if rotate_file(log_file):
            rotated.append(log_file.name)

    return {"rotated": rotated, "checked": checked}


def get_log_sizes() -> dict:
    """Return sizes of all log files in bytes."""
    if not LOGS_DIR.exists():
        return {}

    return {
        f.name: f.stat().st_size
        for f in sorted(LOGS_DIR.glob("*.log"))
        if f.is_file()
    }
