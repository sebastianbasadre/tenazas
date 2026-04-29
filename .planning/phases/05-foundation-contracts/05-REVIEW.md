---
phase: 05-foundation-contracts
reviewed: 2026-04-29T12:00:00Z
depth: standard
files_reviewed: 10
files_reviewed_list:
  - workspace/runtime/agent_protocol.py
  - workspace/runtime/agent_registry.py
  - workspace/runtime/runtime_utils.py
  - workspace/runtime/agent_infra.py
  - workspace/runtime/agent_personal.py
  - workspace/runtime/agent_finance.py
  - workspace/runtime/agent_research.py
  - workspace/runtime/agent_self_improve.py
  - workspace/runtime/agent_coordinator.py
  - tests/test_sprint9.py
findings:
  critical: 1
  warning: 5
  info: 3
  total: 9
status: issues_found
---

# Phase 5: Code Review Report

**Reviewed:** 2026-04-29T12:00:00Z
**Depth:** standard
**Files Reviewed:** 10
**Status:** issues_found

## Summary

The multi-agent system introduces a clean protocol layer (`agent_protocol.py`), a centralized registry (`agent_registry.py`), five domain agents, a coordinator, and an arbitrator. The architecture is sound -- typed contracts, duck-type validation, error isolation, and blocked-target filtering are all present. However, several issues need attention: a race condition in the coordinator log writer, inconsistent state persistence (arbitrator bypasses `locked_write_json`), unused imports across multiple agents, and a logic gap in the coordinator's dispatch-vs-fallback flow that can cause agents to be silently skipped.

## Critical Issues

### CR-01: TOCTOU Race in `_save_coord_log` -- Data Loss on Concurrent Access

**File:** `workspace/runtime/agent_coordinator.py:55-68`
**Issue:** `_save_coord_log` performs a read-then-write on `COORD_LOG` without any locking. The runtime loop, Telegram router, and worker supervisor can all invoke `route()` concurrently (different cron invocations or subprocesses). Between `COORD_LOG.exists()` (line 59) and `open(COORD_LOG, "w")` (line 65), another process can write the same file. The second writer truncates and overwrites, losing the first writer's appended entry. The entire exception is silently swallowed (line 67-68), so data loss is invisible.

This is the same pattern that `locked_write_json` was created to solve, and the registry module already uses it. The coordinator log does not.

**Fix:**
```python
def _save_coord_log(entry: dict):
    COORD_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        from runtime_utils import locked_write_json, load_json
        existing = load_json(str(COORD_LOG), [])
        if not isinstance(existing, list):
            existing = []
        existing.append(entry)
        existing = existing[-200:]
        locked_write_json(str(COORD_LOG), existing)
    except Exception:
        pass
```

## Warnings

### WR-01: Arbitrator Uses Raw `_save`/`_load` Instead of `locked_write_json`

**File:** `workspace/runtime/agent_arbitrator.py:50-57`
**Issue:** The arbitrator defines its own `_load` and `_save` helpers that use plain `open()` without locking or atomic writes. The `CONFLICTS_FILE` is written during conflict resolution (line 159) which can be triggered concurrently by multiple cron workers. This is inconsistent with the project convention established by `runtime_utils.py` and used by `agent_registry.py`. A crash mid-write will corrupt the conflicts file.

**Fix:** Replace `_load`/`_save` with `load_json`/`locked_write_json` from `runtime_utils`:
```python
from runtime_utils import load_json, locked_write_json

# In _handle_tie:
conflicts = load_json(str(CONFLICTS_FILE), {"history": {}})
# ...
locked_write_json(str(CONFLICTS_FILE), conflicts)
```

### WR-02: Coordinator Dispatch Mismatch -- Registry Dispatch and Fallback Cover Different Agent Sets

**File:** `workspace/runtime/agent_coordinator.py:126-146`
**Issue:** In `route()`, the code calls `_reg_dispatch(query)` (line 129) which routes to all **healthy registered** agents that claim ownership. The fallback (lines 137-146) only triggers if `typed_results` is completely empty. But `_reg_dispatch` dispatches to agents based on **registry ownership** (`module.owns()`), while `identify_agents` (line 118) uses **direct module imports** via `_load_agent`. If an agent is registered but its `owns()` does not match the query while the directly-loaded copy does (e.g., after a module reload changed ownership keywords), the agent is silently skipped by both paths.

More concretely: if registry dispatch returns results for *some* agents but not all the ones `identify_agents` found, the fallback never fires (because `typed_results` is non-empty), and those missing agents are silently lost.

**Fix:** The fallback should check which agents from `identify_agents()` were NOT covered by `typed_results`, not just whether `typed_results` is empty:
```python
responded_agents = {name for name, _, _ in typed_results}
missing = [n for n in agents if n not in responded_agents]
if missing:
    for name in missing:
        agent = _load_agent(name)
        # ... fallback logic
```

### WR-03: `_registry_initialized` Global Flag Never Resets -- Stale Registry After Re-import

**File:** `workspace/runtime/agent_coordinator.py:39-45`
**Issue:** `_registry_initialized` is a module-level boolean that prevents re-initialization. But `agent_registry._registry` is also module-level mutable state. If a test or runtime event clears the registry (e.g., `agent_registry._registry = {}`), the coordinator's `_ensure_registry()` will not re-register agents because `_registry_initialized` is still `True`. Tests work around this by directly manipulating `_registry`, but in production, a failed `init_default_agents()` (where all agents fail to import) will set the flag to `True` and never retry.

**Fix:** Either check `_registry` emptiness as a secondary condition, or expose a `reset()` function:
```python
def _ensure_registry():
    global _registry_initialized
    if not _registry_initialized or not _reg_list():
        init_default_agents()
        _registry_initialized = True
```

### WR-04: `agent_finance.py` BTC Alert Threshold Comparison Uses Mismatched Units

**File:** `workspace/runtime/agent_finance.py:42`
**Issue:** `BTC_ALERT_THRESHOLD` is defined as `0.05` (line 21, comment says "5%"). On line 42, it compares: `abs(pnl.get("change_pct", 0)) > BTC_ALERT_THRESHOLD * 100`. This means the alert fires when `change_pct > 5`. If `get_binance_pnl()` returns `change_pct` as a percentage (e.g., `3.5` meaning 3.5%), the threshold is 5% which matches the comment. But if it returns a ratio (e.g., `0.035` meaning 3.5%), the threshold becomes `5` and alerts never fire. The code assumes `change_pct` is already a percentage number, making `BTC_ALERT_THRESHOLD = 0.05` misleading -- it is not used as 0.05 but as 5.0.

**Fix:** Use a clear constant:
```python
BTC_ALERT_PCT_THRESHOLD = 5  # Alert if BTC moves more than 5%
# ...
if abs(pnl.get("change_pct", 0)) > BTC_ALERT_PCT_THRESHOLD:
```

### WR-05: `agent_infra.py` Recommendation Uses `severity` as `risk_level` Without Validation

**File:** `workspace/runtime/agent_infra.py:92`
**Issue:** Predictions from `infra_predictor.predict_all()` include a `severity` field (line 63: `p.get("severity", "info")`). This value is passed directly as `risk_level` to `Recommendation` (line 92). The `Recommendation.__post_init__` silently coerces invalid risk levels to `"low"` (agent_protocol.py:64). If `severity` is `"info"` or `"warning"` (common infra_predictor values), these are **not** in `VALID_RISK_LEVELS` (`"low"`, `"medium"`, `"high"`, `"critical"`), so they silently become `"low"`, losing the original severity signal.

**Fix:** Map predictor severities to valid risk levels:
```python
_SEVERITY_TO_RISK = {"info": "low", "warning": "medium", "critical": "critical"}
# In the loop:
risk_level=_SEVERITY_TO_RISK.get(p.get("severity", "info"), "low"),
```

## Info

### IN-01: Unused `datetime` Import in Multiple Agent Modules

**File:** `workspace/runtime/agent_infra.py:9`, `workspace/runtime/agent_personal.py:9`, `workspace/runtime/agent_finance.py:9`, `workspace/runtime/agent_research.py:9`, `workspace/runtime/agent_self_improve.py:9`
**Issue:** All five agent modules import `from datetime import datetime` but never use it directly -- they use `now()` from `runtime_utils` instead.
**Fix:** Remove the unused import line from each file.

### IN-02: Unused `json` Import in `agent_protocol.py` Absence, But Present in Agents

**File:** `workspace/runtime/agent_infra.py:8`, `workspace/runtime/agent_personal.py:8`, `workspace/runtime/agent_finance.py:8`, `workspace/runtime/agent_research.py:8`, `workspace/runtime/agent_self_improve.py:8`
**Issue:** All five agents import `json` at the top level, but only use it in `_log()`. The `_log()` function is identical across all five modules (4-line JSONL pattern). This is duplicated boilerplate that could be extracted to a shared helper. Not a bug, but unnecessary repetition across 5 files.
**Fix:** Consider a `_make_logger(log_file)` factory in `runtime_utils.py` or a small shared module.

### IN-03: Test Assertions Could Be Stronger in `TestArbitrator.test_escalate_after_3_ties`

**File:** `tests/test_sprint9.py:165-179`
**Issue:** The test comment says "escalate after 3 ties" but the test uses `infra` vs `personal` which have different priorities (4 vs 5) and will never tie. The test then only asserts `history` is a `dict` (line 179) -- this always passes regardless of whether escalation logic was exercised. The test does not verify the actual escalation path.
**Fix:** Use two agents with the same effective priority (e.g., `research` vs `self_improve` without context that changes priority) and assert `result.get("escalated")` is `True` after 3+ iterations.

---

_Reviewed: 2026-04-29T12:00:00Z_
_Reviewer: Claude (gsd-code-reviewer)_
_Depth: standard_
