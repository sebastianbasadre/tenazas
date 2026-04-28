# Phase 3: Orchestrator Decomposition - Context

**Gathered:** 2026-04-28
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

Split orchestrator.py (2373 lines) into focused, independently testable modules while preserving backward compatibility. The orchestrator becomes a thin facade (~200 lines) that re-exports from submodules. All existing `from orchestrator import X` statements continue to work.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — pure infrastructure phase. Key constraints:
- orchestrator.py must become a facade under 200 lines
- Each extracted module independently importable and testable
- All existing imports must work (backward-compatible facade)
- All 65 orchestrator tests must pass (patch paths updated if needed)
- Use runtime_config imports (from Phase 2) in new modules
- Use runtime_utils imports (from Phase 1) in new modules

Research recommended splitting into:
- classifier.py — query classification (classify_query, skill detection)
- skill_executor.py — skill execution engine (execute_skill, route_and_execute, handlers)
- escalation.py — escalation logic
- routing_metrics.py — routing metrics tracking
- orchestrator.py — facade that re-exports everything

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `workspace/runtime/orchestrator.py` — 2373 lines, the file to decompose
- `workspace/runtime/runtime_config.py` — centralized config (Phase 2)
- `workspace/runtime/runtime_utils.py` — shared utilities (Phase 1)
- `workspace/runtime/model_router.py` — already separate (856 lines, model routing V1/V2)

### Established Patterns
- orchestrator.py has 6 distinct responsibilities: classification rules, model selection, skill execution, response parsing, skill handlers, multitool dispatch
- 14 callers import from orchestrator (telegram_router, multi_agent_engine, etc.)
- Tests use `@patch("runtime.orchestrator.X")` and `@patch("orchestrator.X")` patterns
- try/except import pattern established in Phase 1 for dual import paths

### Integration Points
- telegram_router.py imports classify_query, route_and_execute, execute_skill
- multi_agent_engine.py imports route_query
- test_orchestrator.py has 65 tests that patch orchestrator internals

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase. The facade pattern preserves all existing imports:
```python
# orchestrator.py (facade)
from .classifier import classify_query, _classify_skill
from .skill_executor import execute_skill, route_and_execute
from .escalation import escalate
# ... re-export everything
```

</specifics>

<deferred>
## Deferred Ideas

- Typed return contracts for RouteResult, SkillResult (v2 QUAL-03)
- Error taxonomy (v2 QUAL-04)
- model_router.py decomposition (defer to v2 STRC-03)

</deferred>
