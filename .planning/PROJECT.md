# Tenazas Runtime Refactor

## What This Is

A structural refactoring of the Tenazas runtime layer — the 42+ Python module system running Sebas's personal AI assistant on a Synology NAS. The goal is to clean up organic growth, fix reliability issues, remove dead code, and make the codebase testable and maintainable without changing any user-facing behavior.

## Core Value

After the refactor, Tenazas works exactly as before — same skills, same Telegram interactions, same cron jobs — but the code is organized, reliable under concurrent access, and easy to extend.

## Requirements

### Validated

- All existing Telegram skills work (weather, email, calendar, smart-home, finance, jobs, etc.)
- Runtime loop runs every 5 min via cron without hanging or crashing
- Model routing selects correct backend (HomeLab GPU → NAS CPU → Cloud)
- Health sweep, autofix, and self-evolve pipelines function
- Prometheus metrics export on :9105
- All 700+ existing tests pass

### Active

- [ ] Split orchestrator.py (2373 lines) into focused modules (router, skill executor, escalation)
- [ ] Centralize hardcoded IPs/ports into a single config module with env var overrides
- [ ] Replace naive JSON file I/O with atomic writes (temp file + rename pattern)
- [ ] Add file locking for state files accessed by concurrent processes
- [ ] Remove deprecated auto-heal.sh and legacy model references
- [ ] Remove unused/orphaned modules and dead code paths
- [ ] Fix race condition in proposal executor (concurrent approvals)
- [ ] Fix silent model fallback (distinguish backend offline vs config error)
- [ ] Fix state file corruption on crash (atomic writes + validation on load)
- [ ] Add tests for currently untested modules identified in CONCERNS.md
- [ ] Standardize error handling patterns across all modules
- [ ] Standardize logging format (consistent JSON structure across modules)

### Out of Scope

- New features or capabilities — this is a pure structural refactor
- Migrating JSON storage to SQLite — atomic writes are sufficient for current scale
- Docker security (socket proxy, rootless mode) — separate security initiative
- RAG pipeline performance tuning — separate optimization initiative
- UI/frontend changes — Tenazas has no frontend
- Changing the Node.js gateway layer — only Python runtime is in scope

## Context

The runtime layer grew from a few bash scripts (auto-heal.sh, ollama-check.sh) into 42+ Python modules over several months. Each module was added to solve a specific problem, but there was no architectural planning. The result:

- **orchestrator.py** became a 2373-line god object containing routing V1/V2/V3, skill execution, escalation, and 50+ helpers
- **Hardcoded IPs** (192.168.2.6, 192.168.2.2, etc.) appear in 30+ files instead of a central config
- **JSON state files** are read/written by cron (every 5 min), Telegram polling, and background threads with no concurrency protection
- **Dead code** remains: auto-heal.sh (replaced by runtime.py), legacy model entries, unused modules

The system runs in production on the NAS — cron fires every 5 min, Telegram bot polls continuously. Full rebuild/downtime is acceptable for this refactor.

The existing codebase map in `.planning/codebase/` (7 documents, 2322 lines) provides detailed analysis of the current state.

## Constraints

- **Runtime:** Python 3 inside Docker container (node:24-bookworm base)
- **No new dependencies:** Prefer stdlib solutions (fcntl, tempfile, pathlib) over adding packages
- **Test compatibility:** All existing unittest-based tests must pass without modification (unless testing refactored interfaces)
- **Container paths:** Scripts reference `/home/tenazas/runtime/` — module moves must preserve import paths or update all references
- **Cron/startup:** `startup.sh`, `runtime.py`, `metrics_server.py` entry points must continue to work

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Atomic writes over SQLite migration | JSON files work at current scale; atomic writes fix corruption without migration risk | -- Pending |
| Full rebuild OK (downtime accepted) | Sebas can take Tenazas offline during refactor | -- Pending |
| Drop dead code aggressively | Reduces confusion, fewer files to maintain | -- Pending |
| Pure refactor (no new features) | Avoids scope creep, measurable success criteria | -- Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? -> Move to Out of Scope with reason
2. Requirements validated? -> Move to Validated with phase reference
3. New requirements emerged? -> Add to Active
4. Decisions to log? -> Add to Key Decisions
5. "What This Is" still accurate? -> Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check -- still the right priority?
3. Audit Out of Scope -- reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-28 after initialization*
