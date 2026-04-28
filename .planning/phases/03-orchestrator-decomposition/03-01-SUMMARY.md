---
phase: 03-orchestrator-decomposition
plan: 01
subsystem: infra
tags: [python, refactoring, facade-pattern, orchestrator]

# Dependency graph
requires:
  - phase: 01-foundation-utilities
    provides: runtime_utils.now() for timestamps
  - phase: 02-config-centralization
    provides: runtime_config constants (OLLAMA_GPU_URL)
provides:
  - routing_metrics.py as independent module (metrics dict + get/reset + reporting)
  - classifier.py as independent module (classify_query + CLASSIFICATION_RULES + IOL_KEYWORDS)
  - orchestrator.py facade re-exports for backward compatibility
affects: [03-orchestrator-decomposition]

# Tech tracking
tech-stack:
  added: []
  patterns: [facade-re-export with try/except, _log duplication for module independence]

key-files:
  created:
    - workspace/runtime/routing_metrics.py
    - workspace/runtime/classifier.py
  modified:
    - workspace/runtime/orchestrator.py

key-decisions:
  - "Duplicate _log() in each submodule (4 lines) rather than complex import chain"
  - "_routing_metrics dict object identity preserved via Python import semantics for mutable types"
  - "Programmatic extraction of CLASSIFICATION_RULES to guarantee byte-for-byte fidelity of regex patterns"

patterns-established:
  - "Facade re-export: orchestrator.py imports from submodules and re-exports for backward compatibility"
  - "_log() duplication: each submodule has its own _log() writing to same orchestrator.log file"

requirements-completed: [ORCH-01, ORCH-02]

# Metrics
duration: 12min
completed: 2026-04-28
---

# Phase 3 Plan 01: Orchestrator Decomposition Wave 1 Summary

**Extract routing_metrics.py and classifier.py from orchestrator.py with facade re-exports -- orchestrator reduced from 2377 to 1925 lines**

## Performance

- **Duration:** 12 min
- **Started:** 2026-04-28T10:27:30Z
- **Completed:** 2026-04-28T10:39:30Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments
- Extracted routing_metrics.py (97 lines): _routing_metrics dict, get/reset functions, report_inference, _record_metrics, _record_v1_fallback
- Extracted classifier.py (435 lines): IOL_KEYWORDS, CLASSIFICATION_RULES (21 rules), classify_query with Router V3 support
- Orchestrator.py reduced by 452 lines while maintaining full backward compatibility via facade re-exports
- All 81/83 tests pass (2 pre-existing failures in TestRouterV2Integration confirmed unrelated)

## Task Commits

Each task was committed atomically:

1. **Task 1: Extract routing_metrics.py** - `1d85d0b` (refactor)
2. **Task 2: Extract classifier.py** - `de27876` (refactor)

## Files Created/Modified
- `workspace/runtime/routing_metrics.py` - Routing metrics dict, get/reset, Prometheus reporting helpers
- `workspace/runtime/classifier.py` - Query classification rules and classify_query() function
- `workspace/runtime/orchestrator.py` - Updated with facade re-imports from both new modules

## Decisions Made
- Duplicated _log() (4 lines) in each submodule rather than creating shared import -- avoids circular dependency risk
- Used programmatic extraction (Python script) for CLASSIFICATION_RULES to guarantee exact byte-for-byte fidelity of Unicode regex patterns
- Kept _CLASSIFICATION_RULES alias in orchestrator.py for any legacy references
- Removed unused `from collections import defaultdict` import from orchestrator.py after metrics extraction

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed corrupted newline in classifier.py _log function**
- **Found during:** Task 2 (Extract classifier.py)
- **Issue:** Python heredoc script interpreted `\n` as literal newline in _log() f.write call, causing SyntaxError
- **Fix:** Replaced corrupted string literal with proper `"\n"` escape
- **Files modified:** workspace/runtime/classifier.py
- **Verification:** Module imports successfully, all tests pass
- **Committed in:** de27876 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Minor script artifact, fixed immediately. No scope creep.

## Issues Encountered
- Test count is 83 (not 65 as stated in plan/research) -- tests were added since research was conducted
- 2 pre-existing test failures (test_critical_context_flag, test_metrics_track_heavy_route) confirmed as pre-existing by testing against original code with git stash

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- routing_metrics.py and classifier.py are independently importable
- Facade pattern proven -- ready for larger extractions (skill_executor.py, escalation.py) in plans 02 and 03
- orchestrator.py at 1925 lines, target ~250 lines by end of phase

---
*Phase: 03-orchestrator-decomposition*
*Completed: 2026-04-28*
