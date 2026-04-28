# Feature Landscape

**Domain:** Python codebase refactor (42+ module runtime layer)
**Researched:** 2026-04-28

## Table Stakes

Features that are baseline expectations for a clean refactor. Missing any of these means the refactor is incomplete.

| Feature | Why Expected | Complexity | Notes |
|---------|--------------|------------|-------|
| Atomic file I/O (`write_atomic()`) | 20+ modules each define their own `_save_json()` doing naive `open(path, "w")` + `json.dump()`. Crash mid-write = corrupted state. This is the #1 reliability bug. | Low | `tempfile.NamedTemporaryFile` + `os.replace()` is ~15 lines. Single module, imported everywhere. |
| File locking for concurrent access | Cron (5 min), Telegram polling, and background threads all write to same JSON files with zero coordination. Lost updates and double-execution are known bugs. | Medium | `fcntl.flock()` wrapper. Must handle stale locks (timeout). Linux-only is fine (runs in Docker). |
| Centralized config module | 268 occurrences of hardcoded IPs across 65 files. `192.168.2.6` appears in 20+ modules. Any IP change requires editing 30 files. | Medium | Single `runtime_config.py` loading from `openclaw.json` with env var overrides. Every module imports from it. |
| Orchestrator decomposition | `orchestrator.py` is 2373 lines containing routing V1/V2/V3, skill execution, escalation, and 50+ helpers in one file. Untestable as a unit. | High | Split into: `skill_executor.py`, `escalation.py`, `classifier.py`. Orchestrator becomes ~800 line coordinator. Requires updating all imports. |
| Dead code removal | `auto-heal.sh` (deprecated), legacy model entries, orphaned modules. Dead code creates confusion about what's actually running. | Low | Audit + delete. Verify nothing references removed code. |
| Standardized error handling | 5 different error handling patterns coexist. Some modules swallow exceptions silently, others raise, others return empty dicts. No consistency. | Medium | Define 3 patterns: (1) I/O operations return default on failure + log, (2) validation raises ValueError, (3) subprocess uses timeout + capture. Document which pattern applies where. |
| Standardized logging | Each module rolls its own `_log()` function writing JSONL. Fields vary: some use `ts`, others `timestamp`. Some log to `logs/`, others to module directory. | Medium | Single `runtime_logger.py` with `get_logger(module_name)` returning a structured JSONL writer. Mandatory fields: `ts`, `module`, `action`, `ok`. |
| Validated JSON on load | No module validates JSON structure on load. Corrupted file = crash or silent wrong behavior. | Low | `load_json_safe(path, schema=None, default=...)` that catches `JSONDecodeError`, validates expected keys, returns default on failure. |
| Remove duplicate utility functions | `_save_json()` is copy-pasted in 20+ modules. `_now()` appears in 15+. `_load_json()` in 10+. Each is 3-5 lines, identical. | Low | Extract to `runtime_utils.py`: `save_json()`, `load_json()`, `now_iso()`, `ensure_dir()`. |
| Test coverage for critical untested modules | `proposal_executor.py` (executes user-approved actions), `telegram_router.py` (input parsing), `action_budget.py` (spending limits) have zero tests. | High | Priority: proposal_executor > telegram_router > action_budget > auto_training_pipeline > agent_coordinator. |

## Differentiators

Features that go beyond basic cleanup. Not expected in a minimal refactor, but significantly improve the codebase quality and long-term maintainability.

| Feature | Value Proposition | Complexity | Notes |
|---------|-------------------|------------|-------|
| Typed return contracts | Currently all functions return `dict` with ad-hoc keys. Adding `TypedDict` or `dataclass` for return types makes the API self-documenting and catches key typos at development time. | Medium | Use `dataclass` for returns like `RouteResult`, `SkillResult`, `HealthProbe`. No runtime cost. Python 3.10+ `dataclass(slots=True)` for memory efficiency. |
| Module dependency graph enforcement | Current imports are a web: any module can import any other. No layering. Circular import risk grows with each change. | Medium | Define layers: `core` (config, utils, logging) -> `infra` (docker, health) -> `routing` (orchestrator, model_router) -> `skills` (individual skill modules) -> `autonomous` (learning, agents). Enforce via import linting or convention. |
| Graceful degradation contracts | The "never block on errors" policy exists informally. Making it explicit per-module (what degrades, what the fallback is, what gets logged) catches silent failures. | Low | Document in each module's docstring: `Degrades: [what happens on failure]`. Add `DEGRADATION_MODE` constant per module. |
| Config validation on startup | Currently if an IP is wrong or a service is down, it fails at first use (could be 5 min into runtime). Fail-fast on startup catches misconfig immediately. | Low | `runtime_config.validate()` at startup: check all endpoints reachable, all required env vars present, all JSON state files parseable. |
| Rate limiting for Telegram input | No rate limiting on incoming messages. Spam could DOS the backend (every message triggers skill detection + potential LLM call). | Low | Simple sliding window: max 10 messages/min per chat_id. Already identified in CONCERNS.md. |
| Structured error taxonomy | Currently errors are strings. A taxonomy (`BackendOfflineError`, `ConfigError`, `StateCorruptionError`) enables different handling per error type instead of catching `Exception`. | Medium | Define 5-8 error classes in `runtime_errors.py`. Gradually adopt in modules. Don't boil the ocean -- start with orchestrator and model_router. |
| Integration test harness | 700+ unit tests exist but no integration tests that verify the full cron cycle: `runtime.py` -> detect events -> enqueue tasks -> execute -> notify. | High | Docker-based test that runs one full runtime cycle with mocked Docker socket and Ollama endpoint. Verifies end-to-end flow. |
| Log rotation built into logger | `log_rotation.py` exists as separate module. Building rotation into the standard logger (max 5MB, 5 backups) means modules don't need to remember to configure it. | Low | Use `logging.handlers.RotatingFileHandler` inside `runtime_logger.py`. |

## Anti-Features

Features to explicitly NOT build during this refactor. Each has a specific reason.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| SQLite migration for JSON state | Scope creep. Atomic writes + file locking solve the corruption problem at current scale (~1000 entries max). SQLite adds a dependency, migration risk, and changes every test that mocks file I/O. | Implement `write_atomic()` + `FileLock`. Revisit SQLite only if scale exceeds 10K entries per file. |
| Type hints everywhere | 42+ modules with zero type hints. Adding them all at once is weeks of work that doesn't fix any bug. | Add `TypedDict`/`dataclass` only for public API return types in refactored modules. Don't retrofit existing working code. |
| Abstract base classes for modules | Over-engineering. The modules are scripts, not a framework. ABCs add inheritance complexity without benefit when there's one implementation per interface. | Use duck typing + convention. Document expected function signatures in module docstrings. |
| Plugin/extension system | The codebase is maintained by one person (Sebas + Claude). A plugin architecture adds indirection for zero users. | Keep direct imports. New skills follow the existing SKILL.md + script pattern. |
| Async/await rewrite | The runtime is cron-based (5 min cycle). There's no event loop benefit. `ThreadPoolExecutor` already handles parallel health probes. Async would require rewriting every I/O call. | Keep synchronous. Reduce `health_sweep.py` thread count from 25 to 8 for the NAS CPU. |
| Custom ORM or data layer | JSON files work. Adding a data abstraction layer between modules and their 3-5 line save/load operations is over-abstraction. | Use `runtime_utils.save_json()` / `load_json()` with atomic writes. That's the "data layer". |
| Comprehensive docstrings | The codebase convention is comments-explain-why, not docstrings-on-everything. Forcing docstrings on 500+ functions is make-work. | Add docstrings only to public API functions in newly refactored modules. Existing working code keeps its current comments. |
| CI/CD pipeline | There's no GitHub Actions, no automated testing on push. Adding CI is a separate initiative. The refactor should make the codebase CI-ready but not build the pipeline. | Ensure all tests can run via `run_tests.sh`. Document how to run tests. CI comes later. |
| Centralized state manager (Redux-style) | A single state object that all modules read/write through would require rewriting every module's state management. Massive scope for a refactor. | Keep per-module state files. Fix them with atomic writes + locking. |

## Feature Dependencies

```
runtime_utils.py (save_json, load_json, now_iso)
  -> write_atomic() (temp file + rename)
     -> FileLock wrapper (fcntl.flock)
        -> All modules that write state files

runtime_config.py (centralized IPs, ports, paths)
  -> Config validation on startup
  -> Every module that currently hardcodes IPs

runtime_logger.py (structured JSONL logging)
  -> Log rotation built-in
  -> Every module that currently defines _log()

runtime_errors.py (error taxonomy)
  -> Standardized error handling patterns
  -> orchestrator decomposition (uses typed errors)

orchestrator.py decomposition
  -> skill_executor.py (extracted)
  -> classifier.py (extracted)
  -> escalation.py (extracted)
  -> Requires: runtime_config.py, runtime_logger.py (to avoid re-importing old patterns)

Test coverage for critical modules
  -> Requires: orchestrator decomposition (smaller units are testable)
  -> Requires: runtime_config.py (tests can mock config instead of patching constants)
```

## Dependency Order (Build Sequence)

1. **Foundation:** `runtime_utils.py` + `write_atomic()` + `FileLock` -- everything else depends on safe I/O
2. **Config:** `runtime_config.py` -- unblocks removing hardcoded IPs from all modules
3. **Logging:** `runtime_logger.py` -- unblocks replacing per-module `_log()` functions
4. **Errors:** `runtime_errors.py` -- optional but useful before orchestrator split
5. **Decomposition:** Split `orchestrator.py` -- the largest single change
6. **Cleanup:** Dead code removal, duplicate function removal -- safe after foundation in place
7. **Testing:** Add tests for critical untested modules -- easier after decomposition
8. **Polish:** Typed returns, config validation, rate limiting -- differentiators after table stakes done

## MVP Recommendation

Prioritize:
1. **Atomic writes + file locking** -- fixes the #1 production bug (state corruption) with ~50 lines of code
2. **Centralized config** -- eliminates 268 hardcoded IP occurrences, makes testing possible
3. **Utility extraction** -- removes 20+ duplicate `_save_json()` definitions, immediate code reduction
4. **Orchestrator decomposition** -- the biggest maintainability win, enables testing the routing logic

Defer:
- **Typed returns**: valuable but not urgent. Add incrementally as modules are touched.
- **Integration test harness**: important but high effort. Unit tests cover the critical paths today.
- **Error taxonomy**: nice-to-have. Current graceful degradation works; taxonomy improves debugging but doesn't fix bugs.
- **Module dependency enforcement**: convention-based is fine for a single-maintainer project. Formal enforcement is overhead.

## Sources

- `.planning/codebase/CONCERNS.md` -- tech debt analysis (2026-04-28)
- `.planning/codebase/CONVENTIONS.md` -- current coding patterns (2026-04-28)
- `.planning/PROJECT.md` -- project scope and constraints (2026-04-28)
- Direct codebase analysis: `grep` across `workspace/runtime/*.py` confirming 20+ duplicate `_save_json()`, 268 hardcoded IP occurrences across 65 files
