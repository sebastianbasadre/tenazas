# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-04-28)

**Core value:** Same features, clean code -- Tenazas works exactly as before but organized, reliable, and maintainable.
**Current focus:** Phase 3 - Orchestrator Decomposition

## Current Position

Phase: 3 of 4 (Orchestrator Decomposition)
Plan: 2 of 3 in current phase
Status: Executing
Last activity: 2026-04-28 -- Plan 03-02 complete (skill_executor + escalation extraction)

Progress: [########..] 80%

## Performance Metrics

**Velocity:**
- Total plans completed: 7
- Average duration: 10 min
- Total execution time: 1.26 hours

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 01-foundation-utilities | 3 | 30 min | 10 min |
| 02-config-centralization | 2 | 20 min | 10 min |
| 03-orchestrator-decomposition | 2 | 24 min | 12 min |

**Recent Trend:**
- Last 5 plans: 01-03 (19 min), 02-01 (2 min), 02-02 (18 min), 03-01 (12 min), 03-02 (12 min)
- Trend: extraction plans with facade re-exports

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [Roadmap]: 4 phases derived from requirement categories (FOUND, CONF, ORCH, CLEN) matching research dependency order
- [Roadmap]: Atomic state adoption (v2 REL-01/02/03) deferred -- foundation provides write_atomic() but mass adoption is v2
- [Roadmap]: Domain packaging (research Phase 5) deferred to v2 -- not in current requirements
- [01-01]: write_atomic catches BaseException (not Exception) for temp cleanup on signals
- [01-01]: ensure_ascii=False + default=str matches existing 19 _save_json implementations
- [01-02]: Used try/except relative/absolute import pattern (matches runtime_config precedent)
- [01-02]: Left _load_jsonl specialized functions untouched (not same as _load_json)
- [01-03]: deco_client.py and device_discovery.py use now_iso() (return string variant)
- [01-03]: Parameter-level _now in action_budget.py left untouched (test injection)
- [02-01]: Constants computed at import time (not lazily) for read-only infra values
- [02-01]: Derived URLs compose from base constant (OLLAMA_GPU_TAGS_URL = f"{OLLAMA_GPU_URL}/api/tags")
- [02-01]: MAC addresses not env-overridable (hardware-bound identifiers)
- [02-02]: Device inventory IPs (cameras, phones, tablets) left as literals (data, not config)
- [02-02]: Alias pattern (LOCAL_NAME = IMPORTED_CONSTANT) for backward-compatible local names
- [02-02]: Docstring/comment IPs preserved as documentation
- [03-01]: Duplicate _log() in each submodule (4 lines) rather than complex import chain
- [03-01]: _routing_metrics dict object identity preserved via Python import semantics for mutable types
- [03-01]: Programmatic extraction of CLASSIFICATION_RULES to guarantee byte-for-byte fidelity
- [03-02]: sys.modules lookup in escalation._log() to respect test redirects of orch.ORCHESTRATOR_LOG
- [03-02]: Lazy import inside escalate() body to avoid circular imports with orchestrator
- [03-02]: route_and_execute kept in facade (circular import avoidance per research)

### Pending Todos

None yet.

### Blockers/Concerns

- Phase 3 (Orchestrator) is highest-risk -- research-phase recommended before planning
- Btrfs atomicity of os.replace() should be smoke-tested early in Phase 1

## Session Continuity

Last session: 2026-04-28
Stopped at: Completed 03-02-PLAN.md (skill_executor + escalation extraction)
Resume file: None
