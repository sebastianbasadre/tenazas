---
phase: "05"
plan: "02"
subsystem: "runtime-utils"
tags: [file-locking, concurrent-access, fcntl, crash-safety]
dependency_graph:
  requires: []
  provides: [locked_write_json, StateLock, HAS_FCNTL]
  affects: [runtime_utils.py]
tech_stack:
  added: [fcntl-advisory-locking]
  patterns: [context-manager-lock, conditional-import-fallback]
key_files:
  created: []
  modified:
    - workspace/runtime/runtime_utils.py
decisions:
  - "D-10: fcntl.flock() on Linux, no-op fallback on Windows"
  - "Lock file uses .lock suffix appended to target path"
  - "StateLock is a context manager for flexible use patterns"
metrics:
  duration_seconds: 114
  completed: "2026-04-29T10:48:30Z"
  tasks_completed: 1
  tasks_total: 1
---

# Phase 05 Plan 02: Concurrent-Safe File Locking Summary

fcntl advisory locking via StateLock context manager + locked_write_json convenience wrapper added to runtime_utils.py

## Tasks Completed

| Task | Name | Commit | Key Changes |
|------|------|--------|-------------|
| 1 | Add locked_write_json() and StateLock | 0789840 | HAS_FCNTL flag, StateLock class, locked_write_json function |

## Implementation Details

Extended `workspace/runtime/runtime_utils.py` with three additions:

1. **Conditional fcntl import** -- `HAS_FCNTL = True` on Linux (production Docker container), `False` on Windows (dev environment). Uses try/except ImportError pattern.

2. **StateLock class** -- Context manager that acquires exclusive advisory lock via `fcntl.flock(LOCK_EX)` on Linux. On Windows, acts as no-op (enters and exits without locking). Lock file created at `{target_path}.lock` in same directory as target.

3. **locked_write_json(path, data)** -- Convenience function combining `StateLock` + `write_atomic()` for concurrent-safe crash-safe JSON writes. Callers needing read-modify-write patterns can use `with StateLock(path):` directly.

All existing functions (`load_json`, `save_json`, `write_atomic`, `now`, `now_iso`) are completely unchanged.

## Verification

- Verified inside Docker container (Linux): `HAS_FCNTL=True`, locked_write_json writes correctly, StateLock context manager works with write_atomic, existing API functions unaffected.

## Deviations from Plan

None -- plan executed exactly as written.

## Self-Check: PASSED
