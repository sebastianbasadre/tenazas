---
phase: 08-coordinator-rewrite
reviewed: 2026-05-02T00:00:00Z
depth: standard
files_reviewed: 8
files_reviewed_list:
  - tests/test_agent_correlation_id.py
  - workspace/runtime/agent_infra.py
  - workspace/runtime/agent_personal.py
  - workspace/runtime/agent_finance.py
  - workspace/runtime/agent_research.py
  - workspace/runtime/agent_self_improve.py
  - workspace/runtime/agent_coordinator.py
  - tests/test_sprint9.py
findings:
  critical: 2
  warning: 5
  info: 3
  total: 10
status: issues_found
---

# Phase 8: Code Review Report

**Reviewed:** 2026-05-02
**Depth:** standard
**Files Reviewed:** 8
**Status:** issues_found

## Summary

Phase 8 adds `correlation_id` to all five domain-agent `handle()` signatures and rewrites `agent_coordinator.py` with parallel `owns()` evaluation, three routing strategies (`first_wins` / `parallel` / `skill_fallback`), and emoji-labeled aggregation. The structural intent is sound, the `_do_skill_fallback` path correctly uses `classifier` + `skill_executor` instead of `orchestrator`, and the correlation-ID threading through logs is consistent across all five agents.

Two blockers were found: a race condition on the coordinator's shared state file and a broken outer timeout in `_run_parallel` that can leave `ThreadPoolExecutor` threads alive indefinitely. Five warnings cover subtle correctness and reliability issues.

---

## Critical Issues

### CR-01: TOCTOU Race on `coordinator_log.json` in `_save_coord_log`

**File:** `workspace/runtime/agent_coordinator.py:52-65`

**Issue:** `_save_coord_log` reads the file, appends, and writes back in three un-coordinated steps with no locking. When the coordinator is invoked by two simultaneous callers (e.g., `telegram_router.py` handling a message while `agents_worker` runs a proactive sweep), both can read the same snapshot, append their own entry, and the second write silently overwrites the first write's entry. The log cap of 200 entries is enforced only on the in-process read; concurrent writes bypass it.

The project CLAUDE.md convention is explicit: `runtime_utils.locked_write_json` exists for exactly this purpose. The five domain agents all use `save_json` (which internally uses `write_atomic`) for their status files, but the coordinator skips locking for its own shared log.

```python
# workspace/runtime/agent_coordinator.py:52-65  (current — racy)
def _save_coord_log(entry: dict):
    COORD_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = []
        if COORD_LOG.exists():
            existing = json.loads(COORD_LOG.read_text())   # read
            if not isinstance(existing, list):
                existing = []
        existing.append(entry)
        existing = existing[-200:]
        with open(COORD_LOG, "w") as f:
            json.dump(existing, f, indent=2, default=str)  # write (un-locked)
    except Exception:
        pass
```

**Fix:**

```python
from runtime_utils import locked_write_json, load_json

def _save_coord_log(entry: dict):
    COORD_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = load_json(str(COORD_LOG), [])
        if not isinstance(existing, list):
            existing = []
        existing.append(entry)
        existing = existing[-200:]
        locked_write_json(str(COORD_LOG), existing)
    except Exception:
        pass
```


### CR-02: `_run_parallel` Outer Timeout Is 1 Second Longer Than Per-Future Timeout — Threads Can Leak

**File:** `workspace/runtime/agent_coordinator.py:124-162`

**Issue:** The `as_completed` outer timeout is `31` seconds and each `future.result()` call inside the loop has a timeout of `30` seconds. This means if any single agent hangs at exactly the 30-second mark (and raises `FutureTimeout` inside the loop), the outer `as_completed` timeout of 31 seconds is still counting from when the loop started — not from when the last agent finished. When there are 2+ agents in `callable_agents`, the outer 31s budget is easily exhausted in the scenario where one agent uses 30s and any subsequent future then exceeds the remaining 1s. More critically: when `as_completed(futures, timeout=31)` raises `FutureTimeout` on line 154, the `with ThreadPoolExecutor(...)` block still waits for the executor's `__exit__` to join all threads. Since the underlying futures are submitted with no cancellation mechanism, any still-running agent thread will block `__exit__` until it completes (which may never happen if the agent hangs on a network call). `ThreadPoolExecutor` `__exit__` calls `shutdown(wait=True)` by default.

This can cause the coordinator — and by extension `telegram_router.py` — to hang for minutes when a backend service (Ollama, Hue Bridge, Samsung TV) stalls.

```python
# workspace/runtime/agent_coordinator.py:132-154  (current)
with ThreadPoolExecutor(max_workers=len(callable_agents)) as pool:
    futures = {
        pool.submit(mod.handle, query, correlation_id=correlation_id): name
        ...
    }
    try:
        for future in as_completed(futures, timeout=31):
            name = futures[future]
            try:
                obs, recs = future.result(timeout=30)   # inner 30s
                ...
            except FutureTimeout:
                ...
    except FutureTimeout:
        ...
# __exit__ calls shutdown(wait=True) — threads still running block here
```

**Fix:**

```python
with ThreadPoolExecutor(max_workers=len(callable_agents)) as pool:
    futures = {
        pool.submit(mod.handle, query, correlation_id=correlation_id): name
        ...
    }
    # Collect results with a hard budget
    deadline = 30  # seconds
    try:
        for future in as_completed(futures, timeout=deadline):
            ...
    except FutureTimeout:
        _log({...})
    # Cancel pending futures before __exit__ joins threads
    for future in futures:
        future.cancel()
# Optionally: use shutdown(wait=False, cancel_futures=True) — Python 3.9+
```

---

## Warnings

### WR-01: `_run_parallel` Inner `future.result(timeout=30)` Catches Wrong Exception Type

**File:** `workspace/runtime/agent_coordinator.py:141`

**Issue:** `future.result(timeout=30)` raises `concurrent.futures.TimeoutError` (imported as `FutureTimeout` on line 14) when the timeout fires. However, `future.result()` is called on a future that was already returned by `as_completed` — meaning the future is *already done* at that point. Calling `.result(timeout=30)` on an already-completed future never raises `FutureTimeout`; instead, any exception the agent thread raised will be re-raised as the original exception type. The `except FutureTimeout` block on line 143 is therefore dead code that will never execute. Genuine agent exceptions will fall through to the bare `except Exception` on line 145, which is correct — but the timeout path adds confusion and a false sense of per-future timeout enforcement.

**Fix:** Remove the `timeout=30` from `future.result()` calls inside an `as_completed` loop, and remove the now-unreachable `except FutureTimeout` block:

```python
for future in as_completed(futures, timeout=31):
    name = futures[future]
    try:
        obs, recs = future.result()   # already done; no timeout needed
        results[name] = (obs, recs)
    except Exception as e:
        _log({"action": "agent_error", "agent": name, "error": str(e)[:100], "correlation_id": correlation_id})
```


### WR-02: `_aggregate` String-Value Branch Emits No Emoji Label

**File:** `workspace/runtime/agent_coordinator.py:165-179`

**Issue:** When `results[name]` is a plain `str` (the legacy path exercised by `TestCoordinator.test_aggregate_multiple_responses`), the code falls to the `else` branch where `summary = value if value else None`. It appends `f"{emoji} {label}: {summary}"` — so it does use the emoji, which is correct. However, the branch produces output without any domain disambiguation check: an empty string (`""`) will be treated as falsy and `summary` becomes `None`, so the entry is skipped — that is the desired behavior. This is not a crash but the string-path test `test_aggregate_backward_compat_string_values` at line 2011 only asserts `"Docker"` and `"IOL"` are present; it does not assert emoji presence. If the legacy string path is ever expected to carry emoji labels for real output, this gap will surface. Documenting this limitation is the minimum fix.

**Fix:** Add a comment noting that the string legacy path is not guaranteed to emit emoji headers if the value is empty, and ensure the test in `TestCoordinatorRewrite` covers the emoji assertion for the string path. Alternatively, treat the string path symmetrically:

```python
# In _aggregate — string path (legacy compat)
else:
    summary = value if value else None
if not summary:
    continue
emoji = DOMAIN_EMOJI.get(name, "•")
label = name.replace("_", " ").title()
parts.append(f"{emoji} {label}: {summary}")
```

The current code already does this; the warning is that the backward-compat test in `TestCoordinator` (line 257-264) passes plain strings without emoji assertions, so a future refactor removing the string branch would pass all existing tests but silently break the legacy caller pattern in `test_aggregate_backward_compat_string_values` (which was added to `TestCoordinatorRewrite` but not `TestCoordinator`).


### WR-03: `agent_research._observe_rag` Passes User Query Directly to Subprocess Without Shell-Injection Guard

**File:** `workspace/runtime/agent_research.py:42-46`

**Issue:** The subprocess call constructs arguments as a list (correctly, no `shell=True`). However, `safe_query = query[:200].replace(...)` does not strip characters that are meaningful to `query.py`'s argument parser (e.g., `--`, `--generate`, `--debug`, `--json`). A query string beginning with `--` (e.g., `"--generate --debug"` after the `[:200]` truncation) will be passed as a positional argument to `query.py`, but argparse may interpret it as a flag if `query.py` uses `parse_known_args`. If `--generate` is accepted, it could trigger LLM inference instead of a silent JSON lookup.

The `--json` flag is already appended, so a crafted query like `"--generate anything"` would result in both `--json` and `--generate` being active, potentially causing unintended LLM calls and cost.

```python
# workspace/runtime/agent_research.py:42-46 (current)
safe_query = query[:200].replace('\x00', '').replace('\n', ' ').replace('\r', ' ')
result = subprocess.run(
    ["python3", "/home/tenazas/.openclaw/workspace/skills/rag/query.py",
     safe_query, "--json"],
    ...
)
```

**Fix:**

```python
# Ensure the query cannot be mistaken for an argparse flag
safe_query = query[:200].replace('\x00', '').replace('\n', ' ').replace('\r', ' ')
if safe_query.startswith('-'):
    safe_query = ' ' + safe_query   # argparse won't treat leading-space as a flag
result = subprocess.run(
    ["python3", "/home/tenazas/.openclaw/workspace/skills/rag/query.py",
     "--", safe_query, "--json"],   # "--" terminates option processing
    ...
)
```


### WR-04: `agent_infra.assess()` Silently Filters Blocked Proposals by Checking `action` Field Instead of `target`

**File:** `workspace/runtime/agent_infra.py:67-68`

**Issue:** The blocked-target filter checks whether any blocked name appears in `p.get("action", "").lower()`:

```python
status["proposals"] = [p for p in status["proposals"]
                       if not any(t in p.get("action", "").lower() for t in BLOCKED_TARGETS)]
```

`BLOCKED_TARGETS = {"openclaw", "watchtower"}`. The `infra_predictor.predict_all()` returns items with `recommended_action` (renamed to `action` via the dict-comprehension two lines above). A predictor returning `{"recommended_action": "restart", "target": "openclaw"}` will produce `p = {"action": "restart", ...}` — `"openclaw"` is not in `"restart"`, so the filter passes and the blocked target reaches the proposal list.

The test `TestAgentSafety.test_infra_assess_filters_blocked` (line 296) passes a predictor with `"recommended_action": "restart openclaw"` (action contains the target), so the test passes. But a predictor returning the more natural `{"recommended_action": "restart", "target": "openclaw"}` bypasses the filter.

**Fix:**

```python
status["proposals"] = [
    p for p in status["proposals"]
    if not any(
        t in p.get("action", "").lower() or t in p.get("target", "").lower()
        for t in BLOCKED_TARGETS
    )
]
```

Also apply the same fix in `_build_recommendations` which checks `target.lower() in BLOCKED_TARGETS` (line 213) — that path is correct.


### WR-05: `test_no_orchestrator_import_in_route` Uses a Relative Path That Will Fail Outside the Repo Root

**File:** `tests/test_sprint9.py:2045`

**Issue:**

```python
src = pathlib.Path("workspace/runtime/agent_coordinator.py").read_text(encoding="utf-8")
```

This is a relative path resolved against the current working directory at test execution time. When tests are run from inside the container (`/home/tenazas/tests/`), the cwd is typically `/home/tenazas/` and `workspace/runtime/agent_coordinator.py` resolves to `/home/tenazas/workspace/runtime/agent_coordinator.py` — which does not exist (it is at `/home/tenazas/runtime/agent_coordinator.py` via the Docker volume mount). The test will fail with `FileNotFoundError` in the container, passing only when run from `X:/openclaw/` on the Windows host.

The same pattern is used for `agent_self_improve` on line 319:
```python
content = (RUNTIME / "agent_self_improve.py").read_text()
```
Line 319 uses the already-computed `RUNTIME` path (which handles both host and container paths) and is correct. Line 2045 does not.

**Fix:**

```python
# tests/test_sprint9.py:2045
src = (RUNTIME / "agent_coordinator.py").read_text(encoding="utf-8")
```

---

## Info

### IN-01: `_get_claimants` Loads All Five Agent Modules on Every `route()` Call

**File:** `workspace/runtime/agent_coordinator.py:81-101`

**Issue:** `_get_claimants` calls `_load_agent(name)` for all five modules synchronously before submitting to `ThreadPoolExecutor`. `_load_agent` calls `importlib.import_module` each time (no module cache check). Since Python's `import_module` does use `sys.modules` as a cache, the first call is expensive (~disk I/O) and subsequent calls are fast. However, `_load_agent` deliberately does not check `sys.modules` and calls `importlib.import_module` unconditionally, which still has lock acquisition overhead for each call under CPython's import lock. On a loaded NAS this is a minor but measurable latency on every message. No behavioral bug, but worth noting for the eventual optimization pass.


### IN-02: `COORD_LOG` Path Uses `STATE_DIR / "agents" / "coordinator_log.json"` While `LOG_FILE` Uses `BASE_DIR / "logs"`

**File:** `workspace/runtime/agent_coordinator.py:20-22`

**Issue:** The two log destinations are inconsistent with each other and with the conventions of the other four agents (`LOG_FILE = BASE_DIR / "logs" / "agent_*.log"`). `COORD_LOG` writes into the `state/agents/` directory (where per-agent status JSONs live), not in `runtime/logs/`. This means `coordinator_log.json` is co-located with `infra_status.json`, `finance_status.json`, etc., which may surprise operators browsing the agents directory. Not a bug, but inconsistent with the established pattern.


### IN-03: `agent_research.handle` Has Two Divergent `_log` Call Sites That Emit Different Keys

**File:** `workspace/runtime/agent_research.py:132-135`

**Issue:**

```python
if recs:
    _log({"action": "handle", "query": ..., "sub": ..., "recs": len(recs), "correlation_id": correlation_id})
else:
    _log({"action": "handle", "query": ..., "sub": ..., "confidence": obs.confidence, "correlation_id": correlation_id})
```

When recommendations exist, `confidence` is omitted from the log. When they don't exist, `recs` is omitted. This inconsistency makes log-based queries (e.g., `jq 'select(.action=="handle") | .confidence'`) silently skip half the entries. The other four agents emit a single consistent log entry. `TestCorrelationIdInLog` still passes because it only checks for the `correlation_id` and `action` keys. Consider emitting both keys unconditionally.

---

_Reviewed: 2026-05-02_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
