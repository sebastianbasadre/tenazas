---
phase: "05"
plan: "03"
subsystem: "multi-agent-framework"
tags: ["agent-protocol", "typed-contracts", "locked-writes", "backward-compat"]
dependency_graph:
  requires: ["05-01 agent_protocol.py + agent_registry.py", "05-02 StateLock + locked_write_json"]
  provides: ["5 conformant agents", "coordinator-registry delegation", "concurrent-safe registry writes"]
  affects: ["agent_coordinator.py", "agent_registry.py", "all 5 agent modules", "test_sprint9.py"]
tech_stack:
  added: []
  patterns: ["typed handle() returns (Observation, list[Recommendation])", "PREFERRED_ROLE per agent", "locked_write_json for shared state"]
key_files:
  created: []
  modified:
    - "workspace/runtime/agent_infra.py"
    - "workspace/runtime/agent_personal.py"
    - "workspace/runtime/agent_finance.py"
    - "workspace/runtime/agent_research.py"
    - "workspace/runtime/agent_self_improve.py"
    - "workspace/runtime/agent_coordinator.py"
    - "workspace/runtime/agent_registry.py"
    - "tests/test_sprint9.py"
decisions:
  - "agent_research.py had no assess() function; handle() wraps the old handle_query() logic directly with try/except around continuous_learning_loop.research"
  - "agent_self_improve.py assess() returns {pending, experiments} not {proposals, metrics, errors}; handle() maps the actual fields"
metrics:
  duration: "8m"
  completed: "2026-04-29T10:04:00Z"
  tasks_completed: 3
  tasks_total: 3
  tests_added: 21
  tests_total: 95
  files_modified: 8
---

# Phase 05 Plan 03: Agent Protocol Wiring + Integration Summary

All 5 agents conform to typed protocol, coordinator delegates to registry with locked writes, 95 tests pass.

## One-liner

Wired 5 agents to typed protocol (PREFERRED_ROLE + handle() returning Observation/Recommendation tuples), coordinator delegating to agent_registry, registry using locked_write_json for concurrent-safe persistence.

## Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | c1fe242 | Refactor 5 agents: PREFERRED_ROLE, handle(), replace _load/_save |
| 2 | 45d3849 | Coordinator delegates to registry + locked_write_json |
| 3 | a6a21af | 21 new tests: TestLockedState, TestAgentHandleProtocol, TestRegistryLockedWrites |

## Task Details

### Task 1: Refactor 5 agent modules

Applied mechanical migration to all 5 agents:
- Added `from agent_protocol import Observation, Recommendation`
- Added `from runtime_utils import load_json, save_json`
- Added `PREFERRED_ROLE` constant (infra/personal/finance="fast", research/self_improve="reason")
- Added `handle(query)` returning `(Observation, list[Recommendation])` wrapping existing assess/query logic
- Replaced `handle_query()` body with backward-compat wrapper: `obs, recs = handle(query); return obs.summary`
- Deleted private `_load()` and `_save()` helpers, replaced all calls with `load_json()`/`save_json()`

### Task 2: Coordinator + Registry wiring

**agent_coordinator.py:**
- Added `from agent_registry import register, list_agents, dispatch, init_default_agents`
- Added `_ensure_registry()` for lazy initialization
- `route()` uses `_reg_dispatch(query)` for typed dispatch, falls back to legacy `handle_query()` loop
- `list_agents()` delegates to `_reg_list()` with backward-compat fields (module, has_owns, has_handle)
- `_load_agent()` and `AGENT_MODULES` kept as fallback

**agent_registry.py:**
- Import `locked_write_json` from `runtime_utils`
- `_persist_snapshot()` now uses `locked_write_json()` instead of `save_json()` for concurrent-safe writes

### Task 3: Tests

Added 21 new tests across 3 test classes:
- **TestLockedState** (8 tests): locked_write_json creates/overwrites files, creates dirs, StateLock context manager, lock file creation on Linux, error propagation, HAS_FCNTL constant
- **TestAgentHandleProtocol** (10 tests): handle() returns (Observation, list) for all 5 agents, observation fields, handle_query backward compat, all agents have PREFERRED_ROLE, coordinator list_agents has role
- **TestRegistryLockedWrites** (3 tests): source contains locked_write_json, register/unregister call locked_write_json

Updated existing tests:
- `test_each_agent_independent`: now checks for `handle` and `PREFERRED_ROLE`
- `test_list_agents`: now checks for `preferred_role` key

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] agent_research.py has no assess() function**
- **Found during:** Task 1
- **Issue:** Plan assumed agent_research.py had assess() returning {summary, findings, rag_status}. Actually it had no assess() -- logic was directly in handle_query() calling continuous_learning_loop.research.
- **Fix:** Created handle() that wraps the original handle_query() logic (try/except around research), returns Observation + Recommendation list. No data shape mismatch.
- **Files modified:** workspace/runtime/agent_research.py

**2. [Rule 1 - Bug] agent_self_improve.py assess() returns different shape than plan assumed**
- **Found during:** Task 1
- **Issue:** Plan assumed assess() returns {proposals, metrics, errors}. Actually returns {agent, ts, pending, experiments}.
- **Fix:** handle() maps the actual assess() return fields (pending, experiments) instead of the plan's assumed fields.
- **Files modified:** workspace/runtime/agent_self_improve.py

## Verification

All verification commands pass:
- 95 tests passing (42 original + 32 Plan 01 + 21 new)
- All 5 agents: PREFERRED_ROLE set, handle() callable, handle_query() backward-compat
- Coordinator: list_agents returns preferred_role, route uses typed dispatch
- Registry: locked_write_json used for agents_registry.json writes (INTG-02)

## Self-Check: PASSED

All 8 modified files verified on disk. All 3 commit hashes found in git log.
