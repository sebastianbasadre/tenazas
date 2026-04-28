# Requirements: Tenazas Runtime Refactor

**Defined:** 2026-04-28
**Core Value:** Same features, clean code — Tenazas works exactly as before but the code is organized, reliable, and maintainable.

## v1 Requirements

Requirements for this refactoring milestone. Each maps to roadmap phases.

### Foundation

- [x] **FOUND-01**: All duplicate `_save_json()`, `_load_json()`, `_now()` functions extracted into a single `runtime_utils.py` module
- [ ] **FOUND-02**: All modules that previously defined their own `_save_json()` import from `runtime_utils.py` instead
- [x] **FOUND-03**: `runtime_utils.py` provides `write_atomic()` using tempfile + os.replace() pattern for safe writes

### Config

- [ ] **CONF-01**: A single `runtime_config.py` module defines all infrastructure endpoints (IPs, ports, URLs)
- [ ] **CONF-02**: Every hardcoded IP/port in the codebase (268 occurrences, 65 files) is replaced with imports from `runtime_config.py`
- [ ] **CONF-03**: `runtime_config.py` loads values from `openclaw.json` with env var overrides for each endpoint
- [ ] **CONF-04**: Tests can override config values without patching constants in 30+ modules

### Orchestrator

- [ ] **ORCH-01**: `orchestrator.py` (2373 lines) is split into focused modules: classifier, skill executor, escalation engine, and routing facade
- [ ] **ORCH-02**: Each extracted module is independently testable (can import and test without loading the full orchestrator)
- [ ] **ORCH-03**: Existing imports from `orchestrator` continue to work via backward-compatible facade (no breaking changes for callers)
- [ ] **ORCH-04**: All existing orchestrator tests pass after decomposition (patch paths updated)

### Cleanup

- [ ] **CLEN-01**: `auto-heal.sh` removed from repository (confirmed not in DSM cron)
- [ ] **CLEN-02**: Legacy model entries (models no longer used/available) removed from model catalog
- [ ] **CLEN-03**: Orphaned/unused modules identified and removed (verified via import graph analysis)
- [ ] **CLEN-04**: No references to removed code remain in tests, CLAUDE.md, or other modules

## v2 Requirements

Deferred to future release. Tracked but not in current roadmap.

### Reliability

- **REL-01**: Atomic file I/O with `write_atomic()` adopted across all 84 modules that write JSON
- **REL-02**: File locking via `fcntl.flock()` wrapper for state files accessed by concurrent processes
- **REL-03**: Validated JSON loading with schema check and graceful defaults on corruption

### Quality

- **QUAL-01**: Standardized error handling patterns (3 patterns documented, consistently applied)
- **QUAL-02**: Standardized logging via `runtime_logger.py` with structured JSONL and rotation
- **QUAL-03**: Typed return contracts (dataclass) for public API functions in refactored modules
- **QUAL-04**: Structured error taxonomy (5-8 error classes in `runtime_errors.py`)

### Testing

- **TEST-01**: Tests for critical untested modules (proposal_executor, telegram_router, action_budget)
- **TEST-02**: Integration test harness for full runtime cycle
- **TEST-03**: Telegram rate limiting (max 10 messages/min per chat_id)

### Structure

- **STRC-01**: Config validation on startup (fail-fast for misconfig)
- **STRC-02**: Module dependency graph enforcement (layered imports)
- **STRC-03**: Domain packaging (group modules into autofix/, learning/, personal/ packages)

## Out of Scope

| Feature | Reason |
|---------|--------|
| SQLite migration for JSON state | Atomic writes solve corruption at current scale; SQLite adds migration risk |
| Type hints everywhere | 42+ untyped modules; add only to refactored public APIs incrementally |
| Abstract base classes | Over-engineering for single-maintainer project |
| Plugin/extension system | One maintainer; direct imports are simpler |
| Async/await rewrite | Cron-based runtime has no event loop benefit |
| CI/CD pipeline | Make code CI-ready but don't build the pipeline itself |
| Comprehensive docstrings | Convention is comments-explain-why; don't force docstrings on everything |
| New features or capabilities | Pure structural refactor only |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| FOUND-01 | Phase 1 | Complete |
| FOUND-02 | Phase 1 | Pending |
| FOUND-03 | Phase 1 | Complete |
| CONF-01 | Phase 2 | Pending |
| CONF-02 | Phase 2 | Pending |
| CONF-03 | Phase 2 | Pending |
| CONF-04 | Phase 2 | Pending |
| ORCH-01 | Phase 3 | Pending |
| ORCH-02 | Phase 3 | Pending |
| ORCH-03 | Phase 3 | Pending |
| ORCH-04 | Phase 3 | Pending |
| CLEN-01 | Phase 4 | Pending |
| CLEN-02 | Phase 4 | Pending |
| CLEN-03 | Phase 4 | Pending |
| CLEN-04 | Phase 4 | Pending |

**Coverage:**
- v1 requirements: 15 total
- Mapped to phases: 15
- Unmapped: 0

---
*Requirements defined: 2026-04-28*
*Last updated: 2026-04-28 after roadmap creation*
