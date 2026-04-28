# Phase 1: Foundation Utilities - Context

**Gathered:** 2026-04-28
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

Extract all duplicated helper functions (_save_json, _load_json, _now) into a single runtime_utils.py module. Provide write_atomic() using tempfile + os.replace() pattern. All modules that previously defined their own helpers import from runtime_utils.py instead.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — pure infrastructure phase. Follow codebase conventions from .planning/codebase/CONVENTIONS.md. Use stdlib only (no new pip dependencies). Ensure all 700+ existing tests pass after migration.

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `workspace/runtime/` contains 42+ modules with duplicate `_save_json()`, `_load_json()`, `_now()` functions
- Each implementation is 3-5 lines, functionally identical (open/json.dump or open/json.load)
- Some modules already use `os.makedirs(exist_ok=True)` pattern

### Established Patterns
- JSON files written with `json.dump(data, f, indent=2, ensure_ascii=False)` (most modules)
- Load functions return empty dict/list on FileNotFoundError
- Timestamps use `datetime.now().isoformat()` or `datetime.now().strftime("%Y-%m-%d %H:%M:%S")`
- Error handling: catch JSONDecodeError, return default

### Integration Points
- All 20+ modules with `_save_json()` will import from new `runtime_utils.py`
- `runtime.py` entry point is the main consumer of save/load operations
- Tests mock individual module `_save_json` — patch paths will change to `runtime_utils.save_json`

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase. Key constraints:
- stdlib only (tempfile, os, json, datetime)
- write_atomic must use temp file on same filesystem then os.replace()
- Backward compatible: functions have same signatures as existing helpers

</specifics>

<deferred>
## Deferred Ideas

- File locking (fcntl.flock) — deferred to v2 (REL-02)
- Mass adoption of write_atomic across all 84 modules — deferred to v2 (REL-01)
- JSON schema validation on load — deferred to v2 (REL-03)

</deferred>
