---
phase: 05-foundation-contracts
plan: 01
subsystem: agents
tags: [dataclasses, registry, multi-agent, duck-typing, error-isolation]

# Dependency graph
requires: []
provides:
  - "Observation and Recommendation dataclasses for typed agent returns"
  - "validate_agent_module() duck-type interface checker"
  - "Agent registry with register/unregister/dispatch/list_agents lifecycle"
  - "Error isolation: one agent exception does not crash others"
  - "VALID_ROLES and VALID_RISK_LEVELS constants shared across agent system"
affects: [05-02, 05-03, agent_coordinator]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Typed dataclasses with __post_init__ validation (confidence clamping, risk level reset)"
    - "Duck-type module validation via hasattr+callable checks at registration time"
    - "Error-isolated dispatch: try/except per agent.handle() with failure counter"
    - "Registry snapshot persistence (JSON without module objects)"

key-files:
  created:
    - workspace/runtime/agent_protocol.py
    - workspace/runtime/agent_registry.py
  modified:
    - tests/test_sprint9.py

key-decisions:
  - "Zero runtime imports in agent_protocol.py to prevent circular dependencies (stdlib only: dataclasses, datetime)"
  - "requires_approval defaults to True on Recommendation (safe default per observe+recommend policy)"
  - "Registry stores failed modules with status='import_error' or 'invalid' for diagnostics rather than silently dropping"

patterns-established:
  - "Agent interface contract: owns(topic), handle(query), PREFERRED_ROLE constant"
  - "Observation: agent_name, domain, summary, confidence (0-1 clamped), details dict, auto-timestamp"
  - "Recommendation: action, target, risk_level (validated), requires_approval (default True)"

requirements-completed: [FRMK-01, FRMK-02, FRMK-03, FRMK-04]

# Metrics
duration: 4min
completed: 2026-04-29
---

# Phase 05 Plan 01: Agent Protocol and Registry Summary

**Typed Observation/Recommendation dataclasses with duck-type validation and error-isolated agent registry supporting register/unregister/dispatch/list lifecycle**

## Performance

- **Duration:** 4 min
- **Started:** 2026-04-29T10:46:32Z
- **Completed:** 2026-04-29T10:50:42Z
- **Tasks:** 3
- **Files modified:** 3

## Accomplishments
- Created agent_protocol.py with Observation and Recommendation dataclasses, VALID_ROLES/VALID_RISK_LEVELS constants, and validate_agent_module() checker
- Created agent_registry.py with full lifecycle management (register, unregister, dispatch, list_agents, get_agent, init_default_agents)
- Added 32 new tests across 4 test classes (TestAgentProtocol, TestAgentRegistry, TestErrorIsolation, TestPreferredRole)
- All 42 existing test_sprint9.py tests continue passing

## Task Commits

Each task was committed atomically:

1. **Task 1: Create agent_protocol.py** - `cf5bade` (feat)
2. **Task 2: Create agent_registry.py** - `ea6eea2` (feat)
3. **Task 3: Add test classes** - `e2a2691` (test)

## Files Created/Modified
- `workspace/runtime/agent_protocol.py` - Typed contracts: Observation, Recommendation dataclasses, validate_agent_module, VALID_ROLES, VALID_RISK_LEVELS
- `workspace/runtime/agent_registry.py` - Agent lifecycle: register with importlib + validation, unregister, dispatch with error isolation, list_agents, snapshot persistence
- `tests/test_sprint9.py` - 32 new tests in 4 classes covering protocol, registry, error isolation, preferred role

## Decisions Made
- Zero runtime imports in agent_protocol.py (only stdlib: dataclasses, datetime) to avoid circular dependency pitfall
- Failed modules stored with error status rather than silently dropped, enabling diagnostic visibility
- requires_approval=True as safe default on Recommendation per observe+recommend only policy

## Deviations from Plan

None - plan executed exactly as written.

## Known Stubs

None - all data types are fully implemented with validation, serialization, and tests.

## Issues Encountered

Container testing required copying files to NAS volume since worktree is not mounted in Docker container. Files were copied temporarily for verification, then cleaned up. This is expected behavior for parallel worktree execution.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- agent_protocol.py provides the typed contracts that Plan 02 (locked_write_json) and Plan 03 (coordinator rewrite) depend on
- agent_registry.py provides the lifecycle management that Plan 03 will integrate into agent_coordinator.py
- All 74 tests passing (42 existing + 32 new)

---
*Phase: 05-foundation-contracts*
*Completed: 2026-04-29*
