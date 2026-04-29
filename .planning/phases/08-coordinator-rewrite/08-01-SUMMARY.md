---
phase: 08-coordinator-rewrite
plan: "01"
subsystem: domain-agents
tags: [correlation-id, agent-interface, tdd, coordinator-rewrite]
dependency_graph:
  requires: []
  provides: [agent-handle-correlation-id-kwarg]
  affects: [agent_coordinator.py, agents_worker.py]
tech_stack:
  added: []
  patterns: [optional-kwarg-backward-compat, jsonl-structured-logging]
key_files:
  created:
    - tests/test_agent_correlation_id.py
  modified:
    - workspace/runtime/agent_infra.py
    - workspace/runtime/agent_personal.py
    - workspace/runtime/agent_finance.py
    - workspace/runtime/agent_research.py
    - workspace/runtime/agent_self_improve.py
decisions:
  - "Exactly 2 edits per file: signature + log call(s). Zero other changes."
  - "agent_research.py has two _log branches (recs path and confidence path) — both updated."
  - "TDD RED/GREEN followed: 15 failing tests committed before implementation."
metrics:
  duration: "~8 min"
  completed: "2026-04-29"
  tasks_completed: 1
  files_changed: 6
---

# Phase 8 Plan 01: Add correlation_id kwarg to all 5 domain agent handle() functions

All 5 domain agent `handle()` functions updated with `correlation_id: str = None` optional kwarg and JSONL log field, enabling the coordinator (plan 02) to propagate trace IDs while keeping `agents_worker.py` backward-compatible.

## Tasks Completed

| Task | Name | Commit | Files |
|------|------|--------|-------|
| RED | Add failing tests for correlation_id kwarg | d3e154e | tests/test_agent_correlation_id.py |
| GREEN | Add correlation_id kwarg to all 5 agents | 5272c1c | 5 agent files |

## What Was Built

Each agent module received exactly two edits:

1. Signature change: `def handle(query: str)` → `def handle(query: str, correlation_id: str = None)`
2. Log call update: added `"correlation_id": correlation_id` to the `_log({...})` dict

`agent_research.py` has two `_log` branches inside `handle()` (one for when `recs` is non-empty, one for empty recs) — both were updated.

Legacy wrapper `handle_query(query: str)` was left unchanged in all 5 files. `agents_worker.py` calls `agent.handle("")` with no kwargs and continues to work.

## Verification

```
ALL 5 AGENTS: handle() correlation_id kwarg verified
Ran 25 tests in 55.881s — OK
```

25 tests across 3 test classes:
- `TestHandleSignature` (5 tests) — signature has `correlation_id` param with `None` default
- `TestBackwardCompat` (10 tests) — `handle("")` and `handle("q", correlation_id=...)` don't raise
- `TestCorrelationIdInLog` (10 tests) — log entries contain the field with correct value or null

## Deviations from Plan

None — plan executed exactly as written. Exactly 2 edits per file, no other lines changed.

## Threat Flags

None — `correlation_id` is an internal 8-char hex trace token passed between trusted internal modules only, never from user input.

## Self-Check: PASSED

- [x] `workspace/runtime/agent_infra.py` — `def handle(query: str, correlation_id: str = None)` present
- [x] `workspace/runtime/agent_personal.py` — same
- [x] `workspace/runtime/agent_finance.py` — same
- [x] `workspace/runtime/agent_research.py` — same (2 log entries)
- [x] `workspace/runtime/agent_self_improve.py` — same
- [x] `tests/test_agent_correlation_id.py` — 25/25 tests pass
- [x] Commits d3e154e (test) and 5272c1c (feat) both exist in git log
