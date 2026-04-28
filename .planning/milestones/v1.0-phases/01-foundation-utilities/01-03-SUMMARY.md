---
phase: 01-foundation-utilities
plan: 03
subsystem: runtime-utilities
tags: [refactor, gap-closure, time-utils]
dependency_graph:
  requires: [01-01, 01-02]
  provides: [centralized-now, zero-local-_now]
  affects: [89 runtime modules]
tech_stack:
  added: []
  patterns: [centralized-time-utility]
key_files:
  created: []
  modified:
    - workspace/runtime/runtime_utils.py
    - "89 runtime modules (see files_modified in PLAN.md)"
decisions:
  - "deco_client.py and device_discovery.py use now_iso() (return isoformat string)"
  - "87 other modules use now() (return tz-aware datetime)"
  - "Parameter-level _now (e.g., action_budget.py test injection) left untouched"
metrics:
  duration: "19 min"
  completed: "2026-04-28"
  tasks: 2
  files_modified: 90
---

# Phase 1 Plan 3: Migrate _now() to runtime_utils Summary

Centralized timezone-aware datetime via now() in runtime_utils.py, eliminating 89 duplicate _now() definitions across all runtime modules.

## What Was Done

### Task 1: Add now() to runtime_utils.py
- Added `now()` function returning `datetime.now().astimezone()` in the Time section
- Positioned above existing `now_iso()` for logical grouping
- Commit: da07e70

### Task 2: Migrate all 89 modules
- Removed all module-level `def _now()` definitions
- 87 modules: import `now` from runtime_utils, replace `_now()` calls with `now()`
- 2 modules (deco_client.py, device_discovery.py): import `now_iso`, replace with `now_iso()`
- Preserved `_now` as parameter names in action_budget.py (test injection points)
- All 89+1 files pass Python compilation check
- Commit: df08b46

## Verification Results

| Check | Result |
|-------|--------|
| `grep -r "def _now" workspace/runtime/` | Zero matches (PASS) |
| `now()` returns tz-aware datetime | PASS |
| `now_iso()` still works | PASS |
| Import coverage (files importing now/now_iso) | 89 (PASS) |
| All files compile (py_compile) | 92 files OK |

## Deviations from Plan

None - plan executed exactly as written.

## Decisions Made

1. Used line-by-line removal approach for `def _now` (handles all formatting variants)
2. Left `_iso_now()` in proposal_executor.py untouched (different function, different purpose)
3. Left `utc_now_iso()` in obs.py untouched (UTC variant, not same as local _now)

## Self-Check: PASSED
