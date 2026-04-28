# Phase 4: Dead Code Cleanup - Context

**Gathered:** 2026-04-28
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

Remove deprecated scripts, legacy model entries, and orphaned modules. After cleanup, the repository contains only code that is actively used. Verify no references to removed code remain in tests, CLAUDE.md, or other modules.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — pure infrastructure phase. Key targets:
- auto-heal.sh (deprecated, replaced by runtime.py)
- Legacy model entries in model_router.py MODEL_CATALOG
- Orphaned/unused modules (verify via import graph from entry points)
- Stale references in tests, CLAUDE.md, docs
- All 700+ existing tests must pass after cleanup

</decisions>

<code_context>
## Existing Code Insights

### Known Dead Code
- `auto-heal.sh` — deprecated per CLAUDE.md, replaced by runtime layer
- Legacy model entries (models no longer on HomeLab/NAS)
- test_auto_heal.sh — tests for deprecated script
- Possible orphaned modules — need import graph analysis

### Established Patterns
- Modules import from runtime_utils (Phase 1), runtime_config (Phase 2)
- orchestrator.py is now a facade (Phase 3) — check if any orphaned references point to old orchestrator internals
- Entry points: runtime.py (cron), telegram_router.py (polling), metrics_server.py (persistent), startup.sh

### Integration Points
- CLAUDE.md documents auto-heal.sh — section needs removal/update
- DSM Task Scheduler may reference auto-heal.sh — confirm absent
- test files may reference removed modules

</code_context>

<specifics>
## Specific Ideas

No specific requirements. Approach: scan → identify → verify unreachable → remove → test.

</specifics>

<deferred>
## Deferred Ideas

- Domain packaging (grouping modules into packages) — v2 STRC-03
- CLAUDE.md full rewrite — separate initiative

</deferred>
