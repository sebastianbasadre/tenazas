# Project Retrospective

*A living document updated after each milestone. Lessons feed forward into future planning.*

## Milestone: v1.0 — Runtime Refactor

**Shipped:** 2026-04-28
**Phases:** 4 | **Plans:** 9

### What Was Built
- `runtime_utils.py` — shared JSON I/O, timestamps, atomic writes (eliminated 30+ duplicate helpers)
- `runtime_config.py` — centralized 268 hardcoded IPs/ports with env var overrides
- Decomposed orchestrator.py from 2373 to 399 lines (4 extracted modules)
- Dead code cleanup — auto-heal.sh removed, import graph verified, MODEL_CATALOG audited

### What Worked
- Bottom-up phase ordering (utilities first, then config, then orchestrator) avoided circular dependency issues
- Programmatic extraction of classification rules to guarantee byte-for-byte fidelity during decomposition
- YOLO mode with well-scoped plans kept execution fast (1.52 hours for 9 plans)
- Research phase before orchestrator decomposition identified realistic floor (399 lines vs 300-line goal)

### What Was Inefficient
- Phase 1 needed a gap closure plan (01-03) for _now() migration that wasn't caught in initial planning
- 31 test suites broke from import path changes — not caught during planning because scope was "tests must pass" but some tests had hardcoded import paths
- Orchestrator facade target (300 lines) was unrealistic — research corrected to 399 but this could have been caught earlier

### Patterns Established
- Try/except relative/absolute import pattern for modules used both inside and outside the runtime package
- Alias pattern (LOCAL_NAME = IMPORTED_CONSTANT) for backward-compatible local names during config migration
- Duplicate small helpers (4-line _log()) per submodule rather than complex import chains
- sys.modules lookup for test redirect compatibility in extracted modules

### Key Lessons
1. Always audit existing test import paths before planning a module extraction — broken tests from import restructuring are the #1 risk
2. Research before decomposition pays off — the orchestrator research identified circular import risks and realistic line counts before planning
3. Gap closure plans are normal, not failures — discovering _now() needed its own plan was a healthy iteration

### Cost Observations
- Model: inherit (parent session model)
- Execution time: 1.52 hours for 9 plans (avg 10 min/plan)
- Notable: Phase 4 (cleanup) was fastest at 8 min — cleanup after structural work is straightforward

---

## Cross-Milestone Trends

### Process Evolution

| Milestone | Phases | Plans | Key Change |
|-----------|--------|-------|------------|
| v1.0 | 4 | 9 | First milestone — established discuss/plan/execute workflow |

### Cumulative Quality

| Milestone | Requirements | Complete | Coverage |
|-----------|-------------|----------|----------|
| v1.0 | 15 | 15 | 100% |

### Top Lessons (Verified Across Milestones)

1. Bottom-up dependency ordering eliminates rework — start with utilities, end with cleanup
2. Research before complex decomposition prevents unrealistic targets
