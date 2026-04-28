---
phase: 01-foundation-utilities
plan: 02
subsystem: runtime-utilities
tags: [refactor, deduplication, imports]
dependency_graph:
  requires: [01-01-runtime-utils-module]
  provides: [deduplicated-json-io]
  affects: [all-runtime-modules, test-suites]
tech_stack:
  added: []
  patterns: [try-except-relative-import]
key_files:
  modified:
    - workspace/runtime/apply_improvements.py
    - workspace/runtime/background_agents.py
    - workspace/runtime/benchmark_continuous.py
    - workspace/runtime/contextual_awareness.py
    - workspace/runtime/continuous_learning_loop.py
    - workspace/runtime/daily_improvement.py
    - workspace/runtime/dataset_builder.py
    - workspace/runtime/devin_lite.py
    - workspace/runtime/error_mining.py
    - workspace/runtime/goal_engine.py
    - workspace/runtime/homelab_dashboard_payload.py
    - workspace/runtime/idle_learning_engine.py
    - workspace/runtime/improvement_candidates.py
    - workspace/runtime/improvement_loop.py
    - workspace/runtime/incident_clustering.py
    - workspace/runtime/infra_diagnostics.py
    - workspace/runtime/infra_memory.py
    - workspace/runtime/infra_predictor.py
    - workspace/runtime/meta_optimizer.py
    - workspace/runtime/model_distillation.py
    - workspace/runtime/notification_poller.py
    - workspace/runtime/personal_optimizer.py
    - workspace/runtime/predictive_engine.py
    - workspace/runtime/research_engine.py
    - workspace/runtime/self_benchmark.py
    - workspace/runtime/self_dev_engine.py
    - workspace/runtime/self_optimizer.py
    - workspace/runtime/shared_memory.py
    - workspace/runtime/skill_gap_detector.py
    - workspace/runtime/skill_generator.py
    - tests/test_background_agents.py
    - tests/test_goal_engine.py
    - tests/test_idle_learning.py
    - tests/test_self_dev_engine.py
    - tests/test_self_improvement_regression.py
    - tests/test_shared_memory.py
    - tests/test_sprint3.py
decisions:
  - "Used try/except relative/absolute import pattern matching existing runtime_config precedent"
  - "continuous_learning_loop.py special-cased with default=[] to preserve list return behavior"
  - "Left _load_jsonl functions untouched (specialized JSONL readers, not same as _load_json)"
metrics:
  duration: "9 min"
  completed: "2026-04-28"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 37
---

# Phase 1 Plan 2: Migrate _save_json/_load_json to runtime_utils Summary

Eliminated all 30 duplicate _save_json/_load_json helper definitions across runtime modules, replacing them with imports from the canonical runtime_utils.py created in Plan 01.

## Tasks Completed

| Task | Name | Commit | Key Changes |
|------|------|--------|-------------|
| 1 | Migrate 19 _save_json modules | 48fa89c | Removed 19 _save_json + _load_json defs, added imports |
| 2 | Migrate 11 _load_json-only modules + fix imports | e7a8291 | Removed 11 _load_json defs, fixed import pattern to try/except, updated 6 test files |

## What Changed

- **30 modules** now import `load_json`/`save_json` from `runtime_utils` instead of defining their own
- **Import pattern:** `try: from .runtime_utils import ... except ImportError: from runtime_utils import ...` (matches existing `runtime_config` precedent)
- **6 test files** updated to reference new function names (`module.save_json` instead of `module._save_json`)
- **Net code reduction:** ~120 lines of duplicated boilerplate removed

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Import pattern incompatible with package imports**
- **Found during:** Task 2 (test execution)
- **Issue:** Plain `from runtime_utils import` failed when modules were imported as `from runtime import shared_memory` (package context)
- **Fix:** Changed all 30 imports to try/except relative/absolute pattern matching existing `runtime_config` imports
- **Commit:** e7a8291

**2. [Rule 1 - Bug] continuous_learning_loop.py default value mismatch**
- **Found during:** Task 1
- **Issue:** Original `_load_json` returned `[]` on failure, but `load_json()` returns `{}` by default
- **Fix:** Added `default=[]` to all call sites in that module
- **Commit:** 48fa89c

## Test Results

- **1440 tests pass** across 36 suites
- **6 pre-existing failures** unrelated to migration (nmap mock timing, exec_approvals expiry, telegram config, compact module import, multitool action injection)
- **Specialized _load_jsonl functions preserved** (8 modules still have these for JSONL log parsing)

## Verification

```
grep -rn "def _save_json\b" workspace/runtime/*.py | grep -v runtime_utils  → 0 matches
grep -rn "def _load_json\b" workspace/runtime/*.py | grep -v runtime_utils  → 0 matches
grep -rc "from.*runtime_utils import" workspace/runtime/*.py | grep -v ":0$" | wc -l  → 30
```

## Self-Check: PASSED

- [x] 48fa89c commit exists
- [x] e7a8291 commit exists
- [x] Zero _save_json/_load_json definitions remain
- [x] 30 modules import from runtime_utils
- [x] All test files updated and passing
