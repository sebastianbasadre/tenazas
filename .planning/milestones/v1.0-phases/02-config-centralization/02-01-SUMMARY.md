---
phase: 02-config-centralization
plan: 01
subsystem: infra
tags: [config, env-vars, endpoints, constants]

requires:
  - phase: 01-foundation-utilities
    provides: runtime_utils.py (load_json, save_json, write_atomic)
provides:
  - "Centralized infrastructure endpoint constants (25+ IPs/URLs/ports)"
  - "_env_or_default() and _cfg_get() helpers for env var overrides"
  - "Single import target for all 65 consumer modules (Plan 02)"
affects: [02-config-centralization-plan-02, 03-orchestrator-cleanup]

tech-stack:
  added: []
  patterns: ["TENAZAS_ env var prefix for all config overrides", "_env_or_default(key, default) pattern"]

key-files:
  created: [tests/test_runtime_config.py]
  modified: [workspace/runtime/runtime_config.py]

key-decisions:
  - "Constants computed at module import time (not per-call) for performance"
  - "Derived URLs (OLLAMA_GPU_TAGS_URL etc.) reference base constant, not hardcoded IP"
  - "MAC addresses are static constants (not env-overridable) since hardware-bound"

patterns-established:
  - "TENAZAS_ prefix convention for all infrastructure env var overrides"
  - "Grouped constants by service domain (Network, Ollama, NAS Services, Smart Home, Exporters)"

requirements-completed: [CONF-01, CONF-03, CONF-04]

duration: 2min
completed: 2026-04-28
---

# Phase 2 Plan 1: Config Centralization Summary

**Centralized 25+ infrastructure endpoint constants in runtime_config.py with TENAZAS_ env var overrides and derived URL composition**

## Performance

- **Duration:** 2 min
- **Started:** 2026-04-28T20:45:10Z
- **Completed:** 2026-04-28T20:47:38Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Extended runtime_config.py with all infrastructure endpoints (IPs, URLs, ports) as importable constants
- Added _env_or_default() and _cfg_get() helpers for clean env var override pattern
- Created 25-test suite validating defaults, overrides, derived URLs, existing API preservation, and patchability

## Task Commits

Each task was committed atomically:

1. **Task 1: Extend runtime_config.py with infrastructure constants** - `97fbd85` (feat)
2. **Task 2: Create test suite for runtime_config** - `7cd9701` (test)

## Files Created/Modified
- `workspace/runtime/runtime_config.py` - Added 25+ endpoint constants with env var override pattern
- `tests/test_runtime_config.py` - 25 tests covering defaults, overrides, helpers, patchability

## Decisions Made
- Constants computed at import time (not lazily) since they are read-only infrastructure values
- Derived URLs (e.g. OLLAMA_GPU_TAGS_URL) compose from base constant to maintain single-point-of-change
- MAC addresses not env-overridable since they are hardware-bound identifiers

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- runtime_config.py is ready for Plan 02 to migrate 65 files from hardcoded IPs to imports
- All constants verified importable from both relative and absolute paths

---
*Phase: 02-config-centralization*
*Completed: 2026-04-28*
