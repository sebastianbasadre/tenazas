# Project Research Summary

**Project:** Tenazas Runtime Refactor
**Domain:** Python runtime refactoring (42+ module brownfield, production cron + Telegram bot)
**Researched:** 2026-04-28
**Confidence:** HIGH

## Executive Summary

Tenazas is a 42+ module Python runtime running as a cron-based infrastructure assistant inside a Docker container on a Synology NAS. The codebase has grown organically to include routing, skill execution, IoT control, personal finance, and autonomous learning -- all with a 2373-line god object (orchestrator.py) at its center. The primary problems are structural: 268 hardcoded IP occurrences across 65 files, 84 files doing naive json.dump() without atomicity (causing documented state corruption bugs), and zero dependency layering (any module imports any other). This is a textbook brownfield refactor: the system works in production, so the goal is reliability and maintainability without breaking the 700+ test suite or the 5-minute cron loop.

The recommended approach is a strict bottom-up refactor in 6 phases. All four research tracks converge on the same ordering: foundation first (atomic I/O, centralized config, standardized logging), then decompose the orchestrator into a routing/ package with facade preservation, then adopt the new primitives across the 84 files doing unsafe I/O, then centralize config references, then group remaining modules into domain packages, and finally clean dead code. The critical insight is that the orchestrator split (the most visible change) must NOT come first -- it depends on having config and state_io available so the extracted modules use the new patterns from day one.

The top risks are (1) breaking 700+ tests that use string-based mock.patch() paths when modules move, (2) circular imports when splitting the god object, and (3) incomplete config migration leaving orphaned hardcoded IPs. All three are preventable with discipline: update patch paths in the same commit as module moves, map the dependency graph before splitting, and run automated grep checks after each config migration batch. The stdlib-only constraint (no new pip dependencies in the container) is easily satisfied -- os.replace(), fcntl.flock(), dataclasses, and ast cover every tool need.

## Key Findings

### Recommended Stack

The entire refactoring toolset lives within Python standard library. No new container dependencies are required. Dev-only tooling (Ruff for linting) runs on the Windows machine, never in Docker.

**Core technologies:**
- **os.replace() + tempfile**: Atomic file writes -- same-filesystem temp file, fsync, atomic rename. Fixes the #1 production bug (state corruption on crash).
- **fcntl.flock()**: Advisory cross-process file locking -- sufficient when all writers cooperate (cron, Telegram polling, metrics_server). Linux-only, but container is Linux.
- **dataclasses (frozen)**: Typed, immutable config structures with __post_init__ validation. Already partially used in runtime_config.py. No pydantic needed.
- **ast module**: Static import analysis for building the dependency graph before splitting modules. 30-line script, no external tools.
- **Ruff (dev-only)**: Linting and formatting, 10-100x faster than pylint. Catches import/unused/style issues critical during refactoring.

### Expected Features

**Must have (table stakes):**
- Atomic file I/O (write_atomic()) -- fixes documented state corruption
- File locking for concurrent JSON access (cron + Telegram + metrics all write same files)
- Centralized config module -- eliminates 268 hardcoded IP occurrences
- Orchestrator decomposition (2373 lines into 6-8 focused modules)
- Dead code removal (deprecated auto-heal.sh, legacy model entries)
- Standardized error handling (5 different patterns today)
- Standardized JSONL logging (each module rolls its own)
- Utility function deduplication (_save_json() copy-pasted 20+ times)

**Should have (differentiators):**
- Typed return contracts (dataclass for RouteResult, SkillResult)
- Module dependency graph enforcement (layered imports)
- Config validation on startup (fail-fast on misconfig)
- Integration test harness (full cron cycle end-to-end)
- Structured error taxonomy (BackendOfflineError, ConfigError)

**Defer (v2+):**
- SQLite migration for JSON state (current scale is fine)
- Type hints everywhere (add incrementally, not bulk)
- Async/await rewrite (cron-based, no event loop benefit)
- Plugin/extension system (single maintainer, zero users)
- CI/CD pipeline (make CI-ready, build pipeline later)

### Architecture Approach

The target is domain-organized packages with facade preservation. The flat workspace/runtime/ directory gets split into layered subpackages (config.py, state_io.py, logging_config.py at Layer 0; routing/ at Layer 3; devices/, personal/, learning/, autofix/, observability/ as domain packages). Dependencies flow strictly downward. The orchestrator becomes a backward-compat shim (from routing import *) so all 14 existing import sites and 700+ tests continue working without changes until explicitly migrated.

**Major components:**
1. **config.py** -- Single source of truth for IPs, ports, paths, feature flags. Env var overrides.
2. **state_io.py** -- Atomic JSON read/write with file locking. write_json(), read_json(), locked_update().
3. **logging_config.py** -- JSONL logger factory with built-in rotation.
4. **routing/ package** -- Extracted from orchestrator.py: classifier, router, skill_executor, skill_parsers, skill_handlers, multitool, metrics. Facade re-exports everything.
5. **models/ package** -- Extracted from model_router.py (856 lines): router, guardrails, catalog, openrouter.
6. **Domain packages** -- autofix/, learning/, observability/, personal/ grouping existing modules with __init__.py re-exports.

### Critical Pitfalls

1. **Mock patch paths break when modules move** -- 700+ tests use string-based patch decorators targeting runtime.orchestrator.X. When functions move to routing/skill_executor.py, patches silently stop working (tests pass but mocks are inactive). Prevention: update patch paths in the SAME commit as module moves; use autospec=True everywhere.

2. **Circular imports after splitting the god object** -- route_and_execute() calls classification, execution, and escalation (all currently in one file). Splitting creates bidirectional dependencies that crash at import time. Prevention: map the dependency graph first; extract shared types into a neutral module; follow strict downward-only dependency flow.

3. **Atomic write edge cases on Synology Btrfs** -- os.rename() is not durable without fsync(); temp file must be on the same filesystem as target. Prevention: use tempfile.NamedTemporaryFile(dir=path.parent) + os.fsync() + os.replace().

4. **Advisory file locking is all-or-nothing** -- fcntl.flock() only works if ALL processes cooperate. Currently only 1 of 84 JSON-writing modules uses locking. Prevention: single state_io.py utility that ALL modules must use; grep-enforce no bare open(path, w).

5. **Incomplete config migration leaves orphaned IPs** -- Variable names vary (OLLAMA_URL, OLLAMA_GPU_URL, HOMELAB_URL, bare strings). Simple grep misses variants. Prevention: build complete IP inventory first; automated grep check post-migration; migrate one module at a time.

## Implications for Roadmap

### Phase 1: Foundation Layer
**Rationale:** Every subsequent phase depends on having atomic I/O, centralized config, and standardized logging available. These three modules have zero dependencies on existing code and zero risk of breaking anything.
**Delivers:** config.py, state_io.py, logging_config.py -- the infrastructure primitives.
**Addresses:** Atomic file I/O, centralized config module, standardized logging (table stakes).
**Avoids:** Module-level side effects (Pitfall 6) by designing config with lazy initialization from day one.

### Phase 2: Orchestrator Decomposition
**Rationale:** The 2373-line god object is the biggest single blocker to maintainability and testability. Must happen while the codebase is still familiar and before other modules start depending on foundation primitives.
**Delivers:** routing/ package (classifier, router, skill_executor, skill_parsers, skill_handlers, multitool, metrics) + backward-compat orchestrator.py shim.
**Addresses:** Orchestrator decomposition, utility function deduplication (table stakes).
**Avoids:** Circular imports (Pitfall 2) via strict layer ordering; mock patch breakage (Pitfall 1) by updating test paths in same commits as module moves.

### Phase 3: Atomic State Adoption
**Rationale:** With state_io.py available from Phase 1, now migrate the 84 files doing naive JSON I/O. Prioritize the 3 files with documented race conditions.
**Delivers:** All state file writes go through state_io.write_json() or state_io.locked_update(). Fixes documented corruption and race condition bugs.
**Addresses:** File locking, validated JSON on load, standardized error handling (table stakes).
**Avoids:** Advisory lock gaps (Pitfall 4) by enforcing ALL modules use the same utility.

### Phase 4: Config Centralization
**Rationale:** Mechanical find-replace across 65 files. Low risk per change, high cumulative value. Requires the config module from Phase 1 to exist.
**Delivers:** Zero hardcoded IPs remaining in runtime code. All endpoints configurable via env vars.
**Addresses:** Centralized config (table stakes), config validation on startup (differentiator).
**Avoids:** Incomplete migration (Pitfall 7) via automated grep checks and complete IP inventory.

### Phase 5: Domain Packaging
**Rationale:** Group related modules into subpackages for organizational clarity. By this point the foundation and biggest structural changes are done.
**Delivers:** autofix/, learning/, observability/, personal/, models/ packages with __init__.py facades.
**Addresses:** Module dependency graph enforcement (differentiator).
**Avoids:** sys.path breakage (Pitfall 5) by using __init__.py re-exports.

### Phase 6: Dead Code and Polish
**Rationale:** Safe to remove dead code only after all structural changes are done and tests confirm nothing references removed modules.
**Delivers:** Clean codebase: no deprecated scripts, no duplicate utilities, no backward-compat shims. Optionally add typed returns and error taxonomy.
**Addresses:** Dead code removal (table stakes), typed return contracts, structured error taxonomy (differentiators).
**Avoids:** Dynamic import breakage (Pitfall 8) by deleting one module at a time with full test verification.

### Phase Ordering Rationale

- Foundation before decomposition: The orchestrator split creates 6-8 new files. Those files should use state_io.write_json() and from config import X from day one, not inherit the old patterns.
- Decomposition before state adoption: The orchestrator split is the highest-risk structural change. Do it while the codebase is still familiar. State I/O migration is surgical (one file per commit) and lower risk.
- Config centralization after state adoption: Config changes are mechanical and low-risk. Doing them after the state fixes means fewer files are being touched concurrently.
- Domain packaging after individual modules are clean: Moving files into packages is purely organizational. It should not change any behavior, only directory structure.
- Dead code last: Removing code before structural changes risks removing something that turns out to be needed. Remove after everything else is stable.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 2 (Orchestrator Decomposition):** The 2373-line split requires a precise dependency graph and line-by-line responsibility map. The ARCHITECTURE.md provides the map, but actual implementation will need per-function verification. Recommend /gsd-research-phase to validate the split plan against actual import analysis.
- **Phase 3 (Atomic State Adoption):** The Synology Btrfs behavior with os.replace() + fsync() needs validation in the actual container. Recommend a smoke test early in the phase.

Phases with standard patterns (skip research-phase):
- **Phase 1 (Foundation):** Well-documented stdlib patterns. The STACK.md provides complete implementation code.
- **Phase 4 (Config Centralization):** Mechanical find-replace. No ambiguity.
- **Phase 5 (Domain Packaging):** Standard Python package-with-facade pattern.
- **Phase 6 (Dead Code):** Standard deletion + test verification.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | 100% stdlib for runtime; patterns verified against Python docs. Ruff is dev-only. |
| Features | HIGH | Derived from direct codebase analysis (grep counts, line counts). No speculation. |
| Architecture | HIGH | Layer diagram based on actual import analysis. Split map based on line-by-line orchestrator audit. |
| Pitfalls | HIGH | Every pitfall has evidence from the actual codebase (specific files, line numbers, grep counts). |

**Overall confidence:** HIGH

### Gaps to Address

- **Btrfs atomicity verification:** os.replace() is POSIX-atomic, but Synology Btrfs CoW behavior under power failure is not fully documented. Validate with a simple test in the container during Phase 3.
- **Test suite runtime after refactor:** 700+ tests currently run in ~2 minutes. Moving modules into packages may slow import resolution. Monitor test execution time.
- **model_router.py split timing:** At 856 lines, this is the second-largest module. The architecture research suggests splitting it into models/ package in Phase 5, but it could be done in Phase 2 alongside the orchestrator split. Decision depends on whether the orchestrator split alone is already too large for one phase.
- **Import strategy for dev machine vs container:** Tests use sys.path.insert(0, /home/tenazas) which only works in the container. The refactor should not make this worse but does not fix it either. A proper conftest.py or pyproject.toml is a future improvement.

## Sources

### Primary (HIGH confidence)
- Direct codebase analysis: workspace/runtime/ (84 Python files, 158 total modules)
- .planning/codebase/CONCERNS.md -- documented bugs and tech debt
- .planning/codebase/CONVENTIONS.md -- current coding patterns
- .planning/PROJECT.md -- project scope and constraints

### Secondary (HIGH confidence)
- Python os.replace() documentation (https://docs.python.org/3/library/os.html#os.replace)
- Python fcntl module documentation (https://docs.python.org/3/library/fcntl.html)
- Python dataclasses documentation (https://docs.python.org/3/library/dataclasses.html)
- Python unittest.mock.patch documentation (https://docs.python.org/3/library/unittest.mock.html)
- Ruff - Astral (https://astral.sh/ruff)

### Secondary (MEDIUM confidence)
- Python Circular Import: Causes and Fixes -- DataCamp
- Atomic File Writing in Python -- BSWEN
- Refactoring Python Applications -- Real Python
- Splitting a module into multiple files -- TestDriven.io

---
*Research completed: 2026-04-28*
*Ready for roadmap: yes*
