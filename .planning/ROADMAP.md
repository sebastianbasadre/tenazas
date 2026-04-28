# Roadmap: Tenazas Runtime Refactor

## Overview

A bottom-up structural refactoring in 4 phases: extract shared utilities first (unblocking everything), centralize configuration next, decompose the orchestrator god object (biggest structural change, depends on config being in place), and finish with dead code cleanup (safest after all structural moves are done). Each phase delivers a verifiable improvement to code organization without changing user-facing behavior.

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3, 4): Planned milestone work
- Decimal phases (e.g., 2.1): Urgent insertions if needed

- [x] **Phase 1: Foundation Utilities** - Extract duplicated helpers into runtime_utils.py with atomic write support *(completed 2026-04-28)*
- [ ] **Phase 2: Config Centralization** - Replace 268 hardcoded IPs/ports with a single config module
- [ ] **Phase 3: Orchestrator Decomposition** - Split the 2373-line god object into focused modules
- [ ] **Phase 4: Dead Code Cleanup** - Remove deprecated scripts, legacy entries, and orphaned modules

## Phase Details

### Phase 1: Foundation Utilities
**Goal**: All modules share a single source of truth for common operations (JSON I/O, timestamps, atomic writes)
**Depends on**: Nothing (first phase)
**Requirements**: FOUND-01, FOUND-02, FOUND-03
**Success Criteria** (what must be TRUE):
  1. A `runtime_utils.py` module exists with `load_json()`, `save_json()`, `now()`, and `write_atomic()` functions
  2. Running `grep -r "def _save_json\|def _load_json\|def _now" workspace/runtime/` returns zero matches (all duplicates removed)
  3. All 700+ existing tests pass after the migration
  4. `write_atomic()` uses tempfile + os.replace() pattern (verifiable by reading the implementation)
**Plans:** 3 plans
Plans:
- [x] 01-01-PLAN.md — Create runtime_utils.py with load_json, save_json, now_iso, write_atomic
- [x] 01-02-PLAN.md — Migrate all 30 modules to import from runtime_utils
- [x] 01-03-PLAN.md — Migrate all 89 modules from local _now() to runtime_utils.now()

### Phase 2: Config Centralization
**Goal**: Every infrastructure endpoint is defined once and overridable, eliminating scattered hardcoded values
**Depends on**: Phase 1
**Requirements**: CONF-01, CONF-02, CONF-03, CONF-04
**Success Criteria** (what must be TRUE):
  1. A `runtime_config.py` module exists defining all IPs, ports, and URLs as importable constants
  2. `grep -rn "192\.168\.2\." workspace/runtime/*.py` returns only imports from runtime_config (no bare IP literals in logic)
  3. Tests can override any config value by patching `runtime_config.CONSTANT` in one place (not 30+ modules)
  4. Config loads from `openclaw.json` with env var overrides (e.g., `TENAZAS_OLLAMA_GPU_URL` overrides the HomeLab URL)
  5. All 700+ existing tests pass after the migration
**Plans:** 2 plans
Plans:
- [ ] 01-01-PLAN.md — Create runtime_utils.py with load_json, save_json, now_iso, write_atomic
- [ ] 01-02-PLAN.md — Migrate all 30 modules to import from runtime_utils

### Phase 3: Orchestrator Decomposition
**Goal**: The orchestrator is split into independently testable modules while preserving backward compatibility for all callers
**Depends on**: Phase 2
**Requirements**: ORCH-01, ORCH-02, ORCH-03, ORCH-04
**Success Criteria** (what must be TRUE):
  1. `orchestrator.py` is under 200 lines (a facade that re-exports from focused submodules)
  2. Each extracted module (classifier, skill_executor, escalation, routing_facade) can be imported and tested independently
  3. All existing `from runtime.orchestrator import X` and `from orchestrator import X` statements work without modification
  4. All existing orchestrator tests (65 tests in test_orchestrator.py) pass without changes to assertions (only patch paths updated if needed)
**Plans:** 2 plans
Plans:
- [ ] 01-01-PLAN.md — Create runtime_utils.py with load_json, save_json, now_iso, write_atomic
- [ ] 01-02-PLAN.md — Migrate all 30 modules to import from runtime_utils

### Phase 4: Dead Code Cleanup
**Goal**: The repository contains only code that is actively used, with no deprecated artifacts or orphaned modules
**Depends on**: Phase 3
**Requirements**: CLEN-01, CLEN-02, CLEN-03, CLEN-04
**Success Criteria** (what must be TRUE):
  1. `auto-heal.sh` is removed from the repository and confirmed absent from DSM Task Scheduler
  2. `model_router.py` MODEL_CATALOG contains only models that exist on HomeLab or NAS (no phantom entries)
  3. No Python module in `workspace/runtime/` is unreachable (verified via import graph from entry points)
  4. No test file, CLAUDE.md section, or module references any removed code
  5. All 700+ existing tests pass after cleanup
**Plans:** 2 plans
Plans:
- [ ] 01-01-PLAN.md — Create runtime_utils.py with load_json, save_json, now_iso, write_atomic
- [ ] 01-02-PLAN.md — Migrate all 30 modules to import from runtime_utils

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4

| Phase | Plans Complete | Status | Completed |
|-------|----------------|--------|-----------|
| 1. Foundation Utilities | 3/3 | Complete | 2026-04-28 |
| 2. Config Centralization | 0/? | Not started | - |
| 3. Orchestrator Decomposition | 0/? | Not started | - |
| 4. Dead Code Cleanup | 0/? | Not started | - |
