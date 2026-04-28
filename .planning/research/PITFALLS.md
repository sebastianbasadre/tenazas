# Domain Pitfalls

**Domain:** Python runtime refactoring (42+ module monolith, production cron + Telegram bot)
**Researched:** 2026-04-28
**Confidence:** HIGH (evidence from codebase analysis + verified Python documentation)

## Critical Pitfalls

Mistakes that cause rewrites, production outages, or broken test suites.

### Pitfall 1: unittest.mock.patch() Paths Break When Modules Move

**What goes wrong:** Every `@patch("runtime.orchestrator.some_function")` in the test suite is a string literal tied to the current module path. When you split `orchestrator.py` into `skill_executor.py`, `escalation_engine.py`, etc., every patch target string becomes invalid. Tests pass with no errors (the mock silently patches a non-existent path), but the actual function is no longer mocked -- tests become unreliable or pass when they should fail.

**Why it happens:** Python's `unittest.mock.patch()` requires you to patch where an object is *looked up*, not where it is *defined*. When a function moves from `runtime.orchestrator` to `runtime.skill_executor`, but another module still does `from orchestrator import execute_skill`, the patch target depends on the *importing* module's namespace. This creates a fragile web of string-based references that no linter catches.

**Evidence in this codebase:**
- `test_orchestrator.py` uses `from runtime import orchestrator as orch` (65 tests)
- `test_daily_brief.py` has 20+ `@patch("runtime.daily_brief.gather_*")` decorators
- `test_advanced.py` patches `runtime.multi_agent_engine._ollama_available`
- `test_model_router.py` patches `runtime.model_router.subprocess.run` (93 tests)
- Total: 700+ tests with string-based patch paths

**Consequences:**
- Silent test degradation (mocks stop working, tests still "pass")
- False confidence in refactored code
- Hours of debugging mock failures after module splits

**Prevention:**
1. Before moving any function, grep all test files for `@patch("runtime.<old_module>` and update patch paths in the same commit
2. Use `autospec=True` on all patches -- this raises `AttributeError` if the target function signature changed or doesn't exist
3. Create a helper that maps old module paths to new ones during transition
4. Run the full test suite after every module move (not just the module's own tests)
5. Add a CI-like check: `grep -r '@patch.*orchestrator\.' tests/` to find stale references

**Detection:** Tests pass but behavior is wrong. Run tests with `--verbose` and check that mocked functions are actually being called (add `mock.assert_called()` checks).

**Phase:** Must be addressed in the same phase as orchestrator split (Phase 1). Do NOT split modules and update tests in separate phases.

### Pitfall 2: Circular Imports After Module Splitting

**What goes wrong:** When you extract `skill_executor.py` from `orchestrator.py`, the new module needs orchestrator's `classify_query()`, and orchestrator needs `skill_executor.execute_skill()`. Python raises `ImportError` at module load time, and the entire runtime fails to start. The cron job crashes silently every 5 minutes.

**Why it happens:** A 2373-line god object can reference any internal function freely. The moment you split it, you create bidirectional dependencies that Python's import system cannot resolve at module level. This is the most common failure mode when splitting monoliths.

**Evidence in this codebase:**
- `orchestrator.py` contains both routing logic AND skill execution AND escalation
- `route_and_execute()` calls classification, then execution, then escalation -- all currently in the same file
- `model_router.py` already has 3 cross-references back to orchestrator patterns
- `action_registry.py` imports from both `docker_wrapper` and `safety_guard` -- shows cross-cutting dependency patterns already exist

**Consequences:**
- `ImportError` at container startup
- Cron job fails silently every 5 min (no Telegram, no monitoring)
- Runtime completely down until fixed and container rebuilt

**Prevention:**
1. Map the dependency graph BEFORE splitting: which functions call which, in which direction
2. Extract a `types.py` or `interfaces.py` module for shared data classes and constants -- this breaks the cycle
3. Use lazy imports (`import X` inside function body) for the less-frequent direction of the dependency
4. Follow the "dependency inversion" rule: high-level orchestrator depends on abstractions, not on concrete skill executor
5. Test imports in isolation: `python3 -c "import skill_executor"` before committing

**Detection:** `ImportError` or `AttributeError: partially initialized module` at startup. The 5-min cron will fail silently unless you check container logs.

**Phase:** Phase 1 (orchestrator split). Must be the first concern when planning the split.

### Pitfall 3: Atomic Write Implementation Has Cross-Platform and Edge Case Traps

**What goes wrong:** The naive pattern (`write to temp file, os.rename to target`) has three hidden failure modes: (1) temp file on different filesystem than target, (2) `os.rename()` on Linux does NOT guarantee durability without `fsync()`, (3) on NAS filesystems (Btrfs/ext4 on Synology), rename semantics may differ from standard Linux.

**Why it happens:** `os.rename()` is atomic (the directory entry swap) but not durable -- if power fails between `rename()` and the filesystem journal flush, the file can be empty or missing. The Synology DS923+ uses Btrfs, which has CoW semantics that interact differently with rename.

**Evidence in this codebase:**
- 268 occurrences of `json.dump()` or `json.load()` across 84 files
- `shared_memory.py` lines 49-52: naive `open(path, "w")` + `json.dump()`
- `runtime.py` lines 88-91: basic `open/dump` with no atomic writes
- Only `proposal_executor.py` uses `fcntl.flock()` -- the other 83 files have zero protection
- CONCERNS.md documents state file corruption as HIGH priority bug

**Consequences:**
- Corrupted state files when watchdog kills process at 120s timeout mid-write
- `json.JSONDecodeError` on next cron run, losing all state history
- Lost proposal approvals, lost metrics, lost memory entries

**Prevention:**
1. Implement `write_atomic(path, data)` in a single `file_utils.py` module
2. Use `tempfile.NamedTemporaryFile(dir=path.parent, delete=False)` to guarantee same filesystem
3. Call `f.flush()` then `os.fsync(f.fileno())` before close
4. Use `os.replace()` (not `os.rename()`) -- `os.replace()` is guaranteed to overwrite atomically on both POSIX and Windows
5. Add a JSON validation step on load: if `json.load()` fails, look for `.bak` file
6. Keep one `.bak` copy: copy current file to `.bak` before replace

**Detection:** `json.JSONDecodeError` in runtime logs. Monitor for zero-byte JSON files.

**Phase:** Phase 2 (concurrency safety). Must be implemented BEFORE adding file locking, because locking without atomic writes still corrupts on crash.

### Pitfall 4: File Locking with fcntl.flock() Doesn't Work Across Processes the Way You Think

**What goes wrong:** `fcntl.flock()` is advisory, not mandatory -- any process that doesn't acquire the lock can still read/write the file freely. Since the cron job spawns a new Python process every 5 minutes, and `telegram_router.py` runs as a separate long-lived process, and `metrics_server.py` is yet another process, ALL three must use the same locking protocol. If even one module forgets, the lock is useless.

**Why it happens:** `fcntl.flock()` locks are per-file-descriptor, not per-file. A new process opening the same file gets a new file descriptor and can acquire its own lock. The lock only works if all processes cooperate by acquiring before read/write. Also: flock locks are NOT inherited across `fork()` in all cases, and subprocess calls (which this codebase uses extensively) may not respect them.

**Evidence in this codebase:**
- Only `proposal_executor.py` uses `fcntl.flock()` -- the other 83 JSON-writing modules don't
- Three concurrent access patterns: cron (every 5 min), Telegram polling (continuous), metrics_server (persistent)
- `api_call_counter.py` uses `threading.Lock()` which only protects within the same process
- CONCERNS.md explicitly calls out: "lock scope is local to that module -- doesn't protect against external processes"

**Consequences:**
- False sense of safety: adding locks to some modules while others bypass them
- Intermittent data corruption that only manifests under load (hard to reproduce)
- Double-execution of proposals (the documented race condition)

**Prevention:**
1. Create a single `file_lock.py` utility that ALL modules must use for JSON state access
2. Enforce usage via code review: grep for bare `open(path, "w")` calls -- they should all go through the utility
3. Lock granularity: one lock file per state file (e.g., `proposals.json.lock`), not one global lock
4. Add timeout: `LOCK_EX | LOCK_NB` with retry loop and 5s timeout, fail loudly if can't acquire
5. NEVER hold a lock across a network call (Ollama, Telegram API) -- read, release, compute, re-acquire, write
6. Test with concurrent processes: spawn two Python processes writing to same file, verify no corruption

**Detection:** Enable lock acquisition logging. If a process waits >2s for a lock, log a warning. Monitor for `BlockingIOError` in logs.

**Phase:** Phase 2 (concurrency safety). Must be implemented as a universal utility, not per-module.

## Moderate Pitfalls

### Pitfall 5: sys.path Manipulation Creates Fragile Import Resolution

**What goes wrong:** Tests use `sys.path.insert(0, "/home/tenazas")` to find `runtime` modules. Some simulation tests insert THREE paths. When modules move to subdirectories (e.g., `runtime/routing/model_router.py`), the sys.path no longer resolves, and imports fail. Worse: if two modules with the same name exist at different path levels, Python loads the wrong one.

**Why it happens:** The codebase has no proper Python package structure (no `setup.py`, no `pyproject.toml`). All imports rely on bare module names (`from orchestrator import ...`) resolved by sys.path manipulation. This works in the container (where CWD is `/home/tenazas/runtime`) but breaks when:
- Tests run from a different directory
- Modules move to subdirectories
- Two files with the same name exist at different depths

**Evidence:**
- Tests use hardcoded `/home/tenazas` path (won't work on dev machine)
- Simulation tests insert 3 different paths per file
- `__init__.py` in runtime is nearly empty (just a comment)
- Modules use bare imports (`from docker_wrapper import ...`) not package imports (`from runtime.docker_wrapper import ...`)

**Prevention:**
1. Do NOT introduce package-relative imports mid-refactor -- keep bare imports working
2. If creating subdirectories (e.g., `runtime/routing/`), add `__init__.py` that re-exports public API
3. Add a `conftest.py` or test bootstrap that sets up sys.path once, not per-file
4. Test import resolution: `python3 -c "from runtime import orchestrator; print('ok')"` in container after every structural change

**Phase:** Phase 1 (module splitting). Must decide import strategy before moving any files.

### Pitfall 6: Module-Level Side Effects Block Testing and Import

**What goes wrong:** Many modules compute paths, connect to services, or read config at module load time (top of file). When you import such a module in tests, the side effect fires immediately -- trying to connect to Ollama at 192.168.2.6, reading `/home/tenazas/.openclaw/openclaw.json`, etc. Tests fail on dev machines or in CI.

**Why it happens:** Python executes all module-level code at import time. Constants like `STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else ...` run during import. If the path check fails or config file is missing, the module crashes before any test runs.

**Evidence:**
- 30+ modules define `BASE_DIR = Path(__file__).parent` and `STATE_DIR = Path("/home/tenazas/...")` at module level
- `api_call_counter.py` hardcodes `STATE_DIR = Path("/home/tenazas/.openclaw/state")`
- `approval_queue.py` reads `CONFIG_PATH = Path("/home/tenazas/.openclaw/openclaw.json")` at import time
- `apply_improvements.py` sets `OLLAMA_URL = os.environ.get("OLLAMA_GPU_URL", "http://192.168.2.6:11434")` at module level

**Prevention:**
1. Move path resolution to functions or a centralized config module that can be mocked
2. Use lazy initialization: `_config = None; def get_config(): global _config; if _config is None: _config = load(); return _config`
3. For the config centralization phase, ensure the config module itself has no side effects -- just reads env vars and returns defaults
4. Test all new modules with `python3 -c "import module_name"` on a machine without the container paths

**Phase:** Phase 3 (config centralization). Must be addressed when creating the central config module.

### Pitfall 7: Hardcoded IP Centralization Must Be Backward Compatible

**What goes wrong:** You create `runtime_config.py` with `OLLAMA_URL = os.environ.get("OLLAMA_GPU_URL", "http://192.168.2.6:11434")`. Now you need to update 30+ files to import from this module instead of defining their own constant. If you miss even one, that module still has the old hardcoded IP. If the IP changes, 29 files use the new config and 1 uses the old -- causing intermittent, hard-to-diagnose failures.

**Why it happens:** Find-and-replace across 30+ files is error-prone. Some modules use `OLLAMA_URL`, others use `OLLAMA_GPU_URL`, others use `OLLAMA_ENDPOINT`. The variable names aren't consistent, so a simple grep misses variants.

**Evidence:**
- CONCERNS.md: "hardcoded IPs appear in 30+ files"
- Variable names vary: `OLLAMA_URL`, `OLLAMA_GPU_URL`, `HOMELAB_URL`, bare string `"http://192.168.2.6:11434"`
- Some modules use `os.environ.get()` with default, others hardcode directly

**Prevention:**
1. First, grep for ALL IP patterns: `grep -rn "192\.168\.2\." workspace/runtime/` and create a complete inventory
2. Create the config module and import it in ONE module first, verify it works, then migrate module-by-module
3. After migration, add a linter check: `grep -rn "192\.168\.2\." --include="*.py" | grep -v runtime_config.py` should return zero results
4. Add a startup validation: the config module checks connectivity to all endpoints and logs which ones are reachable
5. Keep backward compatibility: if a module's old env var (`OLLAMA_GPU_URL`) is set, the config module should respect it

**Phase:** Phase 3 (config centralization). Inventory must be complete before starting migration.

### Pitfall 8: Removing Dead Code Breaks Dynamic/Lazy Imports

**What goes wrong:** You identify `auto-heal.sh` and legacy model references as dead code and delete them. But some module does `try: from legacy_router import X; except ImportError: use_default()` -- the ImportError path was never tested because the module existed. Now the fallback path runs and has a bug, changing runtime behavior.

**Why it happens:** Python's dynamic nature means imports can happen at runtime in try/except blocks, `importlib.import_module()` calls, or string-based references. Static analysis (grep for `import`) misses these.

**Evidence:**
- `test_daily_brief.py` explicitly tests `side_effect=ImportError` for `runtime.finance`, `runtime.expenses`, `runtime.jobs` -- showing that ImportError fallbacks are a pattern in this codebase
- Multiple modules use `try: import X; except ImportError:` for optional dependencies
- `finetuned_router.py` has keyword fallback when model import fails

**Prevention:**
1. Before deleting any module, grep for its name in ALL files (not just `import` statements): `grep -rn "module_name" .`
2. Search for string references too: `grep -rn "'module_name'" .` and `grep -rn '"module_name"' .`
3. Delete one module at a time, run full test suite, verify no behavior change
4. Mark as deprecated first (add `warnings.warn("deprecated", DeprecationWarning)` at top), run for one cron cycle, check logs

**Phase:** Phase 4 (dead code removal). Must be incremental, not bulk deletion.

## Minor Pitfalls

### Pitfall 9: JSON State Files Need Schema Validation After Refactor

**What goes wrong:** You add a `version` field to state files or change the structure of `proposals.json`. The new code writes format v2, but old state files on disk are format v1. The next read fails or silently drops fields.

**Prevention:**
1. Always handle both old and new formats in the reader: `if "version" not in data: migrate(data)`
2. Never delete fields in the first release -- deprecate, then remove in next cycle
3. Add `_migrate_state(data)` functions that upgrade v1 to v2 on read

**Phase:** Phase 2 (atomic writes). Schema changes happen when adding file versioning.

### Pitfall 10: Logging Format Changes Break Log Parsers

**What goes wrong:** You standardize logging to consistent JSON, but `metrics_server.py` parses `telegram_handler.log` with specific field expectations. Changing log format breaks downstream metric collection.

**Prevention:**
1. Inventory all log parsers before changing any log format
2. Change log format and parsers in the same commit
3. Add a log format version field: `{"_v": 2, ...}`

**Phase:** Phase 5 (logging standardization). Must audit all log consumers first.

### Pitfall 11: Test Isolation Assumptions Break with Shared State Files

**What goes wrong:** Tests use `tempfile.mkdtemp()` for isolation but patch module-level constants (`ce.DIAGNOSTICS_LOG = Path(self.tmpdir) / "diagnostics.log"`). If a test forgets to restore the original in `tearDown()`, subsequent tests in the same process write to the wrong path.

**Prevention:**
1. Use `unittest.mock.patch.object()` instead of direct attribute assignment -- it auto-restores
2. Add a test that verifies all module-level paths are restored after each test class
3. Prefer dependency injection (pass paths as args) over module-level constants in new code

**Phase:** Ongoing during all phases. Review test tearDown when touching test files.

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Orchestrator split | Circular imports between router, executor, escalation | Map dependency graph first; extract shared types module |
| Orchestrator split | 65+ test patch paths become invalid | Update patches in same commit as module move; use autospec=True |
| Orchestrator split | sys.path resolution breaks for new submodules | Keep flat structure initially; add __init__.py re-exports if using subdirs |
| Atomic writes | os.rename() not durable without fsync() on Btrfs (Synology) | Always fsync() before os.replace(); use same-directory temp files |
| Atomic writes | JSON schema changes break existing state files | Add migration functions; handle both old and new formats |
| File locking | Advisory locks ignored by non-participating processes | Single file_lock.py utility; grep-enforce no bare open("w") calls |
| File locking | Lock held across network calls causes deadlock/timeout | Release lock before any HTTP/API call; re-acquire for write |
| Config centralization | Incomplete migration leaves stale hardcoded IPs | Automated grep check post-migration; IP inventory before starting |
| Config centralization | Module-level side effects fire at import time | Lazy initialization pattern; test imports on dev machine |
| Dead code removal | Dynamic imports or try/except fallbacks break silently | Full-text grep (not just import grep); delete one at a time |
| Logging standardization | Log format changes break metrics_server parsers | Audit log consumers first; change format + parser together |

## Sources

- Codebase analysis: `workspace/runtime/` (84 Python files, 268 JSON I/O occurrences)
- `.planning/codebase/CONCERNS.md` (current bug inventory)
- `.planning/codebase/TESTING.md` (test patterns and mock usage)
- [Python unittest.mock.patch documentation](https://docs.python.org/3/library/unittest.mock.html)
- [Python Circular Import: Causes and Fixes (DataCamp)](https://www.datacamp.com/tutorial/python-circular-import)
- [Avoiding Circular Imports in Python (Brex Engineering)](https://medium.com/brexeng/avoiding-circular-imports-in-python-7c35ec8145ed)
- [Atomic File Writing in Python (BSWEN)](https://docs.bswen.com/blog/2026-04-04-atomic-file-writing-python/)
- [python-atomicwrites documentation](https://python-atomicwrites.readthedocs.io/)
- [fcntl.flock() usage patterns (GitHub Gist)](https://gist.github.com/jirihnidek/430d45c54311661b47fb45a3a7846537)
- [unittest.mock.patch compatibility issue (CPython #117860)](https://github.com/python/cpython/issues/117860)

---

*Pitfalls analysis: 2026-04-28*
