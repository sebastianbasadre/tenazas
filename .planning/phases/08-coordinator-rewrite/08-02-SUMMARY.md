---
phase: 08-coordinator-rewrite
plan: "02"
subsystem: coordinator
tags: [coordinator-rewrite, routing, aggregation, correlation-id, backward-compat]
dependency_graph:
  requires: [agent-handle-correlation-id-kwarg]
  provides: [routing-brain, strategy-selection, emoji-aggregation, coordinator-rewrite-tests]
  affects: [agent_coordinator.py, test_sprint9.py]
tech_stack:
  added: []
  patterns: [parallel-owns-evaluation, first-wins-routing, skill-fallback, utf8-source-read]
key_files:
  created:
    - .planning/phases/08-coordinator-rewrite/08-02-SUMMARY.md
  modified:
    - workspace/runtime/agent_coordinator.py
    - tests/test_sprint9.py
decisions:
  - "Preserved legacy `responses` output and multi-domain trigger behavior while replacing registry-based routing."
  - "Zero-claim routing now falls back to classifier + skill_executor directly, never orchestrator."
  - "Windows test read uses UTF-8 explicitly because emoji aggregation makes platform-default decoding unsafe."
metrics:
  duration: "~25 min"
  completed: "2026-04-30"
  tasks_completed: 2
  files_changed: 3
---

# Phase 8 Plan 02: Rewrite coordinator routing brain and add coordinator rewrite tests

`agent_coordinator.py` now uses parallel `owns()` evaluation, per-query strategy selection (`skill_fallback`, `first_wins`, `parallel`), correlation ID propagation, and emoji-labeled aggregation. `tests/test_sprint9.py` now includes `TestCoordinatorRewrite` while keeping the older `TestCoordinator` expectations green.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Rewrite `agent_coordinator.py` with routing brain | not committed in this run | `workspace/runtime/agent_coordinator.py` |
| 2 | Add `TestCoordinatorRewrite` and keep legacy coordinator tests passing | not committed in this run | `tests/test_sprint9.py` |

## What Was Built

The coordinator rewrite removed the old registry-based dispatch path and replaced it with:

1. `_get_claimants()` using `ThreadPoolExecutor` to evaluate all agent `owns()` checks concurrently.
2. `_run_single()` and `_run_parallel()` for direct single-agent execution and multi-agent fan-out.
3. `_do_skill_fallback()` that routes zero-claim queries through `classifier.classify_query()` and `skill_executor.execute_skill()`, never `orchestrator.route_query()`.
4. `_aggregate()` that supports both the new `(Observation, recs)` tuples and the legacy `{agent: str}` test shape while adding emoji-labeled sections.
5. Backward-compatibility wrappers so `identify_agents()`, `is_multi_domain()`, `list_agents()`, and the older coordinator tests continue to work.

The new `TestCoordinatorRewrite` class covers:

- correlation ID generation
- routing strategy selection
- zero-claim fallback behavior
- emoji-labeled aggregation
- backward compatibility of `_aggregate()`
- absence of any orchestrator import
- presence of `correlation_id` kwargs across all five agents

## Verification

```text
import ok
TestCoordinatorRewrite: 14 tests passed
TestCoordinator: 10 tests passed
```

Commands run:

- `python -c "import sys; sys.path.insert(0, 'workspace/runtime'); import agent_coordinator; print('import ok')"`
- `python tests/test_sprint9.py TestCoordinatorRewrite`
- `python tests/test_sprint9.py TestCoordinator`

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Restored legacy multi-domain trigger behavior**
- **Found during:** verification of existing `TestCoordinator`
- **Issue:** `is_multi_domain("como esta todo")` returned `False` after the rewrite because it bypassed the legacy trigger fallback.
- **Fix:** changed `is_multi_domain()` to delegate to `identify_agents()` instead of calling `_get_claimants()` directly.
- **Files modified:** `workspace/runtime/agent_coordinator.py`
- **Verification:** `python tests/test_sprint9.py TestCoordinator`

**2. [Rule 1 - Bug] Made AST source-read test UTF-8 safe on Windows**
- **Found during:** verification of `TestCoordinatorRewrite`
- **Issue:** Windows default text decoding failed on the coordinator source because the new emoji labels are non-ASCII.
- **Fix:** updated the test to read `workspace/runtime/agent_coordinator.py` with `encoding="utf-8"`.
- **Files modified:** `tests/test_sprint9.py`
- **Verification:** `python tests/test_sprint9.py TestCoordinatorRewrite`

## Threat Flags

None new. The coordinator still treats agent failures as non-fatal and does not expose raw exceptions through aggregated user-facing output.

## Self-Check: PASSED

- [x] `workspace/runtime/agent_coordinator.py` imports `uuid`
- [x] `workspace/runtime/agent_coordinator.py` contains `ThreadPoolExecutor`
- [x] `workspace/runtime/agent_coordinator.py` does not import `agent_registry`
- [x] `workspace/runtime/agent_coordinator.py` does not call `orchestrator.route_query()`
- [x] `tests/test_sprint9.py` contains `class TestCoordinatorRewrite`
- [x] `python -c "import sys; sys.path.insert(0, 'workspace/runtime'); import agent_coordinator; print('import ok')"` succeeded
- [x] `python tests/test_sprint9.py TestCoordinatorRewrite` passed
- [x] `python tests/test_sprint9.py TestCoordinator` passed
