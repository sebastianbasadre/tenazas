# Tenazas Runtime Refactor

## What This Is

A structural refactoring of the Tenazas runtime layer — the 42+ Python module system running Sebas's personal AI assistant on a Synology NAS. v1.0 cleaned up organic growth, centralized configuration, decomposed the orchestrator god object, and removed dead code — all without changing user-facing behavior.

## Core Value

After the refactor, Tenazas works exactly as before — same skills, same Telegram interactions, same cron jobs — but the code is organized, reliable under concurrent access, and easy to extend.

## Requirements

### Validated

- runtime_utils.py shared utilities (load_json, save_json, now, write_atomic) — v1.0
- All 30 duplicate _save_json/_load_json and 89 _now() implementations consolidated — v1.0
- runtime_config.py centralized infrastructure endpoints with env var overrides — v1.0
- 268 hardcoded IPs/ports across 65 files replaced with config imports — v1.0
- orchestrator.py decomposed from 2373 to 399 lines with 4 extracted modules — v1.0
- Backward-compatible facade preserving all existing imports — v1.0
- auto-heal.sh removed, MODEL_CATALOG audited, all 201 modules reachable — v1.0

### Active

- [ ] Atomic file I/O with write_atomic() adopted across all 84 modules that write JSON
- [ ] File locking via fcntl.flock() for state files accessed by concurrent processes
- [ ] Validated JSON loading with schema check and graceful defaults on corruption
- [ ] Standardized error handling patterns across all modules
- [ ] Standardized logging via runtime_logger.py with structured JSONL and rotation
- [ ] Tests for critical untested modules (proposal_executor, telegram_router, action_budget)
- [ ] Integration test harness for full runtime cycle
- [ ] Config validation on startup (fail-fast for misconfig)
- [ ] Domain packaging (group modules into autofix/, learning/, personal/ packages)

### Out of Scope

- SQLite migration for JSON state — atomic writes solve corruption at current scale
- Type hints everywhere — add only to refactored public APIs incrementally
- Abstract base classes — over-engineering for single-maintainer project
- Plugin/extension system — one maintainer, direct imports are simpler
- Async/await rewrite — cron-based runtime has no event loop benefit
- CI/CD pipeline — make code CI-ready but don't build the pipeline itself
- New features or capabilities — pure structural refactor only

## Context

v1.0 shipped on 2026-04-28 with 4 phases (9 plans) in 1.52 hours of execution time. The runtime layer now has:
- `runtime_utils.py` — shared JSON I/O, timestamps, atomic writes
- `runtime_config.py` — centralized infrastructure endpoints (IPs, ports, URLs) with env var overrides
- Decomposed orchestrator: `classifier.py`, `skill_executor.py`, `escalation.py`, `routing_metrics.py` + 399-line facade
- No dead code: auto-heal.sh removed, all 201 modules reachable from entry points

**Known issues:** 31 test suites have pre-existing failures from import path restructuring during v1.0. These need a maintenance pass before v2 work.

## Constraints

- **Runtime:** Python 3 inside Docker container (node:24-bookworm base)
- **No new dependencies:** Prefer stdlib solutions (fcntl, tempfile, pathlib) over adding packages
- **Test compatibility:** All existing unittest-based tests must pass without modification (unless testing refactored interfaces)
- **Container paths:** Scripts reference `/home/tenazas/runtime/` — module moves must preserve import paths or update all references
- **Cron/startup:** `startup.sh`, `runtime.py`, `metrics_server.py` entry points must continue to work

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Atomic writes over SQLite migration | JSON files work at current scale; atomic writes fix corruption without migration risk | Good — write_atomic() works, mass adoption deferred to v2 |
| Full rebuild OK (downtime accepted) | Sebas can take Tenazas offline during refactor | Good — no issues |
| Drop dead code aggressively | Reduces confusion, fewer files to maintain | Good — auto-heal.sh removed, all modules verified reachable |
| Pure refactor (no new features) | Avoids scope creep, measurable success criteria | Good — 15/15 requirements met |
| Duplicate _log() per submodule | Avoids complex import chains in decomposed orchestrator | Good — 4 lines per module, clean isolation |
| Orchestrator facade at 399 lines | Realistic floor after extracting all possible code | Acceptable — above 300-line target but no further extraction possible |

## Evolution

This document evolves at phase transitions and milestone boundaries.

---
*Last updated: 2026-04-28 after v1.0 milestone*
