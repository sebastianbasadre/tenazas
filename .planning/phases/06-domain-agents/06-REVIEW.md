---
phase: 06-domain-agents
reviewed: 2026-04-29T12:00:00Z
depth: standard
files_reviewed: 6
files_reviewed_list:
  - workspace/runtime/agent_infra.py
  - workspace/runtime/agent_finance.py
  - workspace/runtime/agent_self_improve.py
  - workspace/runtime/agent_personal.py
  - workspace/runtime/agent_research.py
  - tests/test_sprint9.py
findings:
  critical: 1
  warning: 5
  info: 3
  total: 9
status: issues_found
---

# Phase 6: Code Review Report

**Reviewed:** 2026-04-29T12:00:00Z
**Depth:** standard
**Files Reviewed:** 6
**Status:** issues_found

## Summary

The multi-agent system (Sprint 9) implements five domain agents (infra, finance, personal, research, self_improve) with a shared protocol, registry, coordinator, and arbitrator. The architecture is well-structured with good error isolation and consistent patterns across agents. However, there is a logic bug in the smoking tracker fallback, a subprocess call that bypasses the project's safety conventions, and several quality issues around unused imports and overly broad ownership matching.

## Critical Issues

### CR-01: agent_research subprocess call bypasses docker_wrapper security layer

**File:** `workspace/runtime/agent_research.py:42-45`
**Issue:** The `_observe_rag` function calls `subprocess.run(["python3", ...])` directly. While this specific call invokes a Python script (not Docker), the project's CLAUDE.md explicitly states that subprocess calls should use list arguments (which it does, good), but the path is hardcoded to `/home/tenazas/.openclaw/workspace/skills/rag/query.py`. If a user-controlled `query` string is passed to this subprocess via the `safe_query` argument, it becomes a command-line argument to the Python script. The `safe_query` is truncated to 200 chars but not sanitized for shell metacharacters. Since `subprocess.run` is called with a list (not `shell=True`), direct shell injection is mitigated. However, the hardcoded absolute path means this will silently fail outside the container environment with no useful error reporting, and the 10-second timeout could be chained to cause slowdowns if an attacker triggers many RAG queries. More critically, the `query` parameter passed to `_observe_rag` flows directly from user input in Telegram via the coordinator, and the subprocess invocation has no allowlist or validation of the query content before passing it as an argument to an external script.

**Fix:** While `shell=True` is not used (mitigating the worst case), consider validating that the query contains no null bytes or excessively long arguments, and add a comment documenting the security boundary. The truncation to 200 chars is a start but does not filter control characters:
```python
safe_query = query[:200].replace('\x00', '').replace('\n', ' ').replace('\r', ' ')
```

## Warnings

### WR-01: Smoking tracker fallback counts ALL entries as week_count

**File:** `workspace/runtime/agent_personal.py:88-89`
**Issue:** In the `_observe_smoking` fallback path (when `smoking.get_stats` is not available), the code iterates over all entries in `log.json` and increments `week_count` for every entry, not just entries from the current week. The comment says "Simplified -- count all recent" but this is incorrect -- it counts ALL historical entries as the weekly count, producing wildly inflated numbers.

```python
for entry in log_data.get("entries", []):
    ts = entry.get("ts", "")
    if ts.startswith(today_str):
        today_count += 1
    week_count += 1  # BUG: counts ALL entries, not just this week
```

**Fix:** Filter by date range for the week:
```python
from datetime import timedelta
week_start = (now() - timedelta(days=7)).strftime("%Y-%m-%d")
for entry in log_data.get("entries", []):
    ts = entry.get("ts", "")
    if ts.startswith(today_str):
        today_count += 1
    if ts[:10] >= week_start:
        week_count += 1
```

### WR-02: agent_infra calls homelab_snapshot.snapshot() multiple times in one handle() call

**File:** `workspace/runtime/agent_infra.py:76-158, 161-170`
**Issue:** When `handle()` routes to `_assess_full()`, the `assess()` function calls `homelab_snapshot.snapshot()` once. But if the query matches a specific sub-observer (containers, ollama, vpn, disk), that observer ALSO calls `homelab_snapshot.snapshot()`. The `_assess_full()` path calls `assess()` which calls `snapshot()`, so there is a single call. However, there is no caching -- each sub-observer independently imports and calls `snapshot()`. If `snapshot()` makes network calls (HTTP to Ollama, docker ps, etc.), this results in redundant I/O on every query. This is not a performance review finding per se, but the lack of caching means the data could be inconsistent between the `assess()` call in `_assess_full()` and a subsequent observation if the system state changes mid-call.

**Fix:** Cache the snapshot result at the start of `handle()` and pass it to sub-observers:
```python
def handle(query: str):
    try:
        from homelab_snapshot import snapshot
        snap = snapshot()
    except Exception:
        snap = {}
    # Pass snap to sub-observers instead of having each one call snapshot()
```

### WR-03: Unused `datetime` import in all five agent modules

**File:** `workspace/runtime/agent_infra.py:9`, `workspace/runtime/agent_finance.py:8`, `workspace/runtime/agent_self_improve.py:9`, `workspace/runtime/agent_personal.py:8`, `workspace/runtime/agent_research.py:9`
**Issue:** All five agent modules import `datetime` from the `datetime` module directly but never use it. The `now()` function from `runtime_utils` is used instead. This is dead import code duplicated across all agents.

**Fix:** Remove the unused import line from each file:
```python
# Remove: from datetime import datetime
```

### WR-04: agent_finance `_observe_expenses` IOL P&L "issue" triggers on any non-zero value

**File:** `workspace/runtime/agent_finance.py:85-86`
**Issue:** The `_observe_iol` function adds an "issue" whenever `abs(pnl) > 0`, which is virtually always true for any active portfolio. This means every IOL observation will generate at least one recommendation via `_build_finance_recs()`, flooding Telegram with alerts for normal market activity. The `BTC_ALERT_THRESHOLD` (5%) is properly checked for Binance, but IOL has no equivalent threshold check.

```python
if abs(pnl) > 0:  # Always true for active portfolio
    issues.append(f"IOL P&L dia: ${pnl:,.0f}")
```

**Fix:** Apply a threshold similar to BTC:
```python
if abs(pnl) > IOL_ALERT_THRESHOLD * total:  # e.g., 3% of portfolio value
    issues.append(f"IOL P&L dia: ${pnl:,.0f}")
```

### WR-05: Overly broad ownership matching can cause false agent routing

**File:** `workspace/runtime/agent_self_improve.py:21`, `workspace/runtime/agent_research.py:20-21`
**Issue:** `agent_self_improve` owns "learning" and "test", while `agent_research` owns "learning" and "search". The keyword "test" in self_improve's OWNERSHIP set is extremely broad -- any query containing the word "test" (e.g., "test my internet connection") will be claimed by the self_improve agent. Similarly, "learning" is shared between research and self_improve, causing ambiguous routing. The word "search" in research's ownership will match finance queries like "search my expenses".

**Fix:** Use more specific keywords or multi-word matching:
```python
# agent_self_improve.py - remove "test" or use "benchmark_test"
OWNERSHIP = {"improve", "benchmark", "error_mining", "experiment", "staging",
             "candidate", "self-evolve", "calibration", "idle_learning"}

# agent_research.py - remove generic "learning"  
OWNERSHIP = {"research", "searxng", "knowledge_base", "knowledge",
             "rag", "investigate", "documentation"}
```

## Info

### IN-01: Unused `json` import in agent_personal.py (partially)

**File:** `workspace/runtime/agent_personal.py:7`
**Issue:** `json` is imported but only used in `_log()`. Since all agents follow the same pattern, this is consistent but could be simplified if `_log` were extracted to a shared utility.

**Fix:** Low priority -- consider extracting the `_log` pattern to a shared agent logging utility to reduce duplication across all five agents.

### IN-02: AGENT_DIR not created before first use in agent modules

**File:** `workspace/runtime/agent_infra.py:17`, `workspace/runtime/agent_finance.py:17`
**Issue:** `AGENT_DIR` is defined as `STATE_DIR / "agents"` but the directory is never explicitly created before `save_json(str(STATUS_FILE), ...)` is called. The `save_json` utility from `runtime_utils` likely creates parent directories, but this is an implicit dependency. If `save_json` does not create parent dirs, `save_json` to `AGENT_DIR/infra_status.json` would fail on first run.

**Fix:** Add `AGENT_DIR.mkdir(parents=True, exist_ok=True)` at module initialization or verify that `save_json` handles directory creation.

### IN-03: Test file uses importlib.reload extensively, risking module state leaks

**File:** `tests/test_sprint9.py` (throughout)
**Issue:** Many test methods use `importlib.reload()` to pick up mocked `sys.modules`. This pattern can cause subtle test ordering dependencies if module-level state (like `_registry_initialized` in agent_coordinator) is not properly reset between tests. The test suite does not systematically reset the `_registry_initialized` flag in `agent_coordinator`, which means test execution order could affect results.

**Fix:** Reset `agent_coordinator._registry_initialized = False` in relevant test setUp methods, or use a more isolated testing pattern.

---

_Reviewed: 2026-04-29T12:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
