---
phase: 06-domain-agents
fixed_at: 2026-04-29T12:30:00Z
review_path: .planning/phases/06-domain-agents/06-REVIEW.md
iteration: 1
findings_in_scope: 6
fixed: 6
skipped: 0
status: all_fixed
---

# Phase 6: Code Review Fix Report

**Fixed at:** 2026-04-29T12:30:00Z
**Source review:** .planning/phases/06-domain-agents/06-REVIEW.md
**Iteration:** 1

**Summary:**
- Findings in scope: 6
- Fixed: 6
- Skipped: 0

## Fixed Issues

### CR-01: agent_research subprocess call bypasses docker_wrapper security layer

**Files modified:** `workspace/runtime/agent_research.py`
**Commit:** 6b903d8
**Applied fix:** Added sanitization of control characters (null bytes, newlines, carriage returns) from the user-supplied query before passing it as a subprocess argument to the RAG query script. While subprocess.run with list args mitigates shell injection, control characters could still cause unexpected behavior.

### WR-01: Smoking tracker fallback counts ALL entries as week_count

**Files modified:** `workspace/runtime/agent_personal.py`
**Commit:** 01efcf1
**Applied fix:** Replaced the unconditional `week_count += 1` with a proper date-range filter using `timedelta(days=7)`. Only entries from the last 7 days are now counted toward the weekly smoking total in the fallback path. Status: fixed: requires human verification (logic bug).

### WR-02: agent_infra calls homelab_snapshot.snapshot() multiple times in one handle() call

**Files modified:** `workspace/runtime/agent_infra.py`
**Commit:** 15ef6e8
**Applied fix:** Added `cached_snap` parameter to all four sub-observers (`_observe_containers`, `_observe_ollama`, `_observe_vpn`, `_observe_disk`). The `handle()` function now calls `snapshot()` once at the top and passes the cached result to whichever sub-observer is selected, eliminating redundant I/O.

### WR-03: Unused datetime import in all five agent modules

**Files modified:** `workspace/runtime/agent_infra.py`, `workspace/runtime/agent_finance.py`, `workspace/runtime/agent_self_improve.py`, `workspace/runtime/agent_personal.py`, `workspace/runtime/agent_research.py`
**Commit:** 58853c0
**Applied fix:** Removed `from datetime import datetime` from all five agent modules. The `now()` function from `runtime_utils` is used throughout instead.

### WR-04: agent_finance _observe_expenses IOL P&L "issue" triggers on any non-zero value

**Files modified:** `workspace/runtime/agent_finance.py`
**Commit:** 3976f01
**Applied fix:** Changed the IOL P&L alert condition from `abs(pnl) > 0` to `total > 0 and abs(pnl) > IOL_ALERT_THRESHOLD * total`, using the already-defined `IOL_ALERT_THRESHOLD = 0.03` (3%). This prevents alerts for normal market fluctuations. Status: fixed: requires human verification (logic bug).

### WR-05: Overly broad ownership matching can cause false agent routing

**Files modified:** `workspace/runtime/agent_self_improve.py`, `workspace/runtime/agent_research.py`
**Commit:** 2b73c24
**Applied fix:** Narrowed OWNERSHIP keywords to prevent false routing: self_improve changed "learning" to "idle_learning" (specific to its domain); research removed generic "search" (could match finance queries) and shared "learning", added "knowledge_base" for specificity.

---

_Fixed: 2026-04-29T12:30:00Z_
_Fixer: Claude (gsd-code-fixer)_
_Iteration: 1_
