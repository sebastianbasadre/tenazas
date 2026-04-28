---
phase: 01-foundation-utilities
verified: 2026-04-28T20:00:00Z
status: gaps_found
score: 2/4 must-haves verified
overrides_applied: 0
gaps:
  - truth: "Running grep -r 'def _save_json|def _load_json|def _now' workspace/runtime/ returns zero matches"
    status: failed
    reason: "def _now remains in 89 runtime modules. Plans deliberately skipped _now migration but roadmap SC2 requires it."
    artifacts:
      - path: "workspace/runtime/runtime_utils.py"
        issue: "Provides now_iso() but 89 modules still define their own _now()"
    missing:
      - "Migrate all 89 _now() definitions to use now_iso() from runtime_utils (or add now() that returns datetime object)"
      - "OR add both now() (returns datetime) and now_iso() (returns string) to runtime_utils, then migrate all 89 modules"
  - truth: "runtime_utils.py provides load_json, save_json, now(), and write_atomic functions (SC1 naming)"
    status: failed
    reason: "Function is named now_iso() not now() as specified in roadmap SC1. Additionally, _now() returns datetime object while now_iso() returns string -- they serve different purposes and neither replaces the other."
    artifacts:
      - path: "workspace/runtime/runtime_utils.py"
        issue: "Exports now_iso() but not now() -- naming mismatch with roadmap SC1"
    missing:
      - "Add a now() function to runtime_utils.py that returns datetime.now().astimezone() (matching existing _now() behavior)"
      - "Then migrate 89 modules to import now from runtime_utils instead of defining _now()"
---

# Phase 1: Foundation Utilities Verification Report

**Phase Goal:** All modules share a single source of truth for common operations (JSON I/O, timestamps, atomic writes)
**Verified:** 2026-04-28T20:00:00Z
**Status:** gaps_found
**Re-verification:** No -- initial verification

## Goal Achievement

### Observable Truths

| # | Truth | Status | Evidence |
|---|-------|--------|----------|
| 1 | runtime_utils.py exists with load_json, save_json, now(), write_atomic (SC1) | FAILED | Module exists with load_json, save_json, now_iso, write_atomic -- name is now_iso not now |
| 2 | grep -r "def _save_json\|def _load_json\|def _now" returns zero matches (SC2) | FAILED | _save_json: 0 matches, _load_json: 0 matches, _now: 89 matches remaining |
| 3 | All 700+ existing tests pass after migration (SC3) | UNCERTAIN | Cannot run Docker tests from verification environment; summary claims pass |
| 4 | write_atomic uses tempfile + os.replace pattern (SC4) | VERIFIED | Lines 54-59: NamedTemporaryFile + os.replace confirmed in implementation |

**Score:** 2/4 truths verified (SC4 verified, SC2 partial for _save_json/_load_json)

### Required Artifacts

| Artifact | Expected | Status | Details |
|----------|----------|--------|---------|
| `workspace/runtime/runtime_utils.py` | Shared utility module | VERIFIED | 68 lines, 4 functions, proper implementation |
| `tests/test_runtime_utils.py` | Unit tests | VERIFIED | 152 lines, 11 test methods, 4 test classes |

### Key Link Verification

| From | To | Via | Status | Details |
|------|-----|-----|--------|---------|
| 30 runtime modules | runtime_utils.py | `from runtime_utils import load_json, save_json` | WIRED | 30 modules confirmed importing |
| runtime_utils.py | stdlib | `import tempfile, os, json` | WIRED | All stdlib imports present |

### Data-Flow Trace (Level 4)

Not applicable -- utility module, not a data-rendering component.

### Behavioral Spot-Checks

| Behavior | Command | Result | Status |
|----------|---------|--------|--------|
| No _save_json defs remain | `grep -rn "def _save_json" workspace/runtime/` | 0 matches | PASS |
| No _load_json defs remain | `grep -rn "def _load_json" workspace/runtime/` | 0 matches (only _load_jsonl) | PASS |
| _now still present | `grep -rn "def _now" workspace/runtime/` | 89 matches | FAIL |
| write_atomic uses os.replace | `grep "os.replace" runtime_utils.py` | Found | PASS |

### Requirements Coverage

| Requirement | Source Plan | Description | Status | Evidence |
|-------------|------------|-------------|--------|----------|
| FOUND-01 | 01-01 | Extract _save_json, _load_json, _now into runtime_utils.py | BLOCKED | _save_json/_load_json extracted; _now NOT extracted (89 copies remain) |
| FOUND-02 | 01-02 | All modules import from runtime_utils instead | SATISFIED | 30 modules migrated, zero _save_json/_load_json defs remain |
| FOUND-03 | 01-01 | write_atomic uses tempfile + os.replace | SATISFIED | Implementation confirmed at lines 48-67 |

### Anti-Patterns Found

| File | Line | Pattern | Severity | Impact |
|------|------|---------|----------|--------|
| 89 modules | various | `def _now()` still defined locally | BLOCKER | Roadmap SC2 explicitly requires zero _now matches |

### Human Verification Required

1. **Test Suite Pass**
   - **Test:** Run `docker exec openclaw bash /home/tenazas/tests/run_tests.sh`
   - **Expected:** All 700+ tests pass with exit code 0
   - **Why human:** Requires Docker container running on NAS

### Gaps Summary

The phase successfully eliminated all `_save_json` and `_load_json` duplicates (the JSON I/O part of the goal). However, the **timestamps** part of the goal was not addressed. The roadmap SC2 explicitly includes `def _now` in the grep command that must return zero matches, and 89 modules still define their own `_now()`.

The root cause is a scope decision in the plans: Plan 01 created `now_iso()` (returns string) but did not create `now()` (returns datetime). Plan 02 explicitly stated "Do NOT change _now() calls." This was a reasonable engineering decision (since _now() returns datetime used for arithmetic, while now_iso() returns string for logs), but it violates the roadmap contract.

**To close these gaps:**
1. Add `def now(): return datetime.now().astimezone()` to runtime_utils.py
2. Migrate all 89 modules to `from runtime_utils import now` (replacing local `_now`)
3. This is a mechanical change identical to the _save_json migration already completed

---

_Verified: 2026-04-28T20:00:00Z_
_Verifier: Claude (gsd-verifier)_
