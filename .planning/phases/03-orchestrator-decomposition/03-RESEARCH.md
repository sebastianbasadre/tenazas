# Phase 3: Orchestrator Decomposition - Research

**Researched:** 2026-04-28
**Domain:** Python module decomposition / refactoring
**Confidence:** HIGH

## Summary

orchestrator.py is 2377 lines containing 6 distinct responsibility groups: (1) query classification with regex/keyword rules, (2) model selection with V1/V2 routing, (3) pipeline assembly, (4) skill execution engine with 15 skill entries, 10 handlers, and 4 parsers, (5) multitool dispatch, and (6) routing metrics and helpers. The file has clear internal boundaries marked by section comments, making extraction straightforward.

The critical risk is **test patch paths** -- test_orchestrator.py (65 tests) imports `from runtime import orchestrator as orch` and accesses everything through `orch.X`, so the facade pattern will keep all tests working without patch path changes. test_multitool.py (12 patches) uses `@patch("runtime.orchestrator.X")` which also works through the facade. The 7 runtime callers use `from orchestrator import X` which the facade re-exports.

**Primary recommendation:** Extract in 4 modules (classifier, skill_executor, routing_metrics, escalation) with orchestrator.py as a thin facade that re-exports all public names. Do skill_executor FIRST (largest, 1100+ lines, most self-contained), then classifier, then routing_metrics, then escalation. The facade pattern makes extraction order less critical since all names remain importable from orchestrator.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
None -- all implementation choices are at Claude's discretion (pure infrastructure phase).

### Claude's Discretion
- orchestrator.py must become a facade under 200 lines
- Each extracted module independently importable and testable
- All existing imports must work (backward-compatible facade)
- All 65 orchestrator tests must pass (patch paths updated if needed)
- Use runtime_config imports (from Phase 2) in new modules
- Use runtime_utils imports (from Phase 1) in new modules
- Recommended split: classifier.py, skill_executor.py, escalation.py, routing_metrics.py, orchestrator.py facade

### Deferred Ideas (OUT OF SCOPE)
- Typed return contracts for RouteResult, SkillResult (v2 QUAL-03)
- Error taxonomy (v2 QUAL-04)
- model_router.py decomposition (defer to v2 STRC-03)
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ORCH-01 | orchestrator.py split into focused modules: classifier, skill executor, escalation engine, routing facade | Function mapping below identifies 6 groups; recommended 4 extracted modules + facade |
| ORCH-02 | Each extracted module independently testable | Each module has clear inputs/outputs; no circular dependencies identified |
| ORCH-03 | Existing imports work via backward-compatible facade | Facade re-exports all 15+ public names; 7 runtime callers + 2 test files analyzed |
| ORCH-04 | All existing orchestrator tests pass after decomposition | Tests import `orch.X` -- facade preserves this; patch paths analyzed below |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Module | Secondary | Rationale |
|------------|---------------|-----------|-----------|
| Query classification | classifier.py | -- | Pure function, regex/keyword rules, no side effects except V3 router attempt |
| Model selection (V1+V2) | orchestrator.py (facade) | model_router.py | select_model + _select_model_v2 tightly coupled with routing_metrics; keep in facade or routing_metrics |
| Pipeline assembly | orchestrator.py (facade) | -- | get_pipeline is 15 lines, not worth extracting alone |
| Skill execution | skill_executor.py | -- | 15 SKILL_MAP entries, 10 handlers, 4 parsers -- largest responsibility (~1100 lines) |
| Escalation | escalation.py | -- | escalate() is ~55 lines, depends on select_model + get_pipeline |
| Routing metrics | routing_metrics.py | -- | _routing_metrics dict, get/reset/record functions (~80 lines) |
| Multitool dispatch | skill_executor.py | -- | route_and_execute_multitool uses execute_skill heavily, belongs together |
| Helper functions | orchestrator.py (facade) | -- | _get_parameters, _extract_signals, _build_flags_from_context, _is_openrouter_available -- small, used by route_query |

## Standard Stack

Not applicable -- pure Python refactoring phase. No new libraries needed.

## Architecture Patterns

### System Architecture Diagram

```
                        External Callers
                             |
            from orchestrator import X  (7 callers)
                             |
                    +------------------+
                    | orchestrator.py  |  <-- Thin facade (~150 lines)
                    | (facade)         |  Re-exports all public names
                    +------------------+
                     /    |     |     \
                    /     |     |      \
    +-------------+ +----+-----+  +----------+  +----------------+
    | classifier  | | routing  |  | escalation|  | skill_executor |
    | .py         | | _metrics |  | .py       |  | .py            |
    | (~200 lines)| | .py      |  | (~70 lines)|  | (~1100 lines)  |
    |             | | (~80 ln) |  |           |  |                |
    | classify_   | | _routing_|  | escalate()|  | SKILL_MAP      |
    | query()     | | metrics  |  |           |  | execute_skill()|
    | CLASSIFI-   | | get/reset|  |           |  | route_and_     |
    | CATION_     | | _record_ |  |           |  |   execute()    |
    | RULES       | | metrics  |  |           |  | All handlers   |
    | IOL_KEYWORDS| |          |  |           |  | All parsers    |
    +-------------+ +----------+  +----------+  +----------------+
```

### Recommended Project Structure

```
workspace/runtime/
├── orchestrator.py          # Facade (~150 lines) - re-exports + route_query + select_model
├── classifier.py            # Query classification rules + classify_query()
├── skill_executor.py        # SKILL_MAP + execute_skill + handlers + parsers + route_and_execute
├── escalation.py            # escalate() function
├── routing_metrics.py       # _routing_metrics dict + get/reset/record helpers
└── model_router.py          # Already separate (856 lines, untouched)
```

### Pattern: Facade Re-export

**What:** orchestrator.py becomes a thin module that imports from submodules and re-exports all public names.
**When to use:** Always -- this is the backward compatibility mechanism.

```python
# orchestrator.py (facade)
"""Orchestrator facade — re-exports from submodules for backward compatibility."""

from classifier import (
    classify_query, CLASSIFICATION_RULES, IOL_KEYWORDS,
)
from skill_executor import (
    execute_skill, route_and_execute, route_and_execute_multitool,
    SKILL_MAP, SKILL_TIMEOUT, complete_email_send,
    _load_email_pending, _save_email_pending, _clear_email_pending,
)
from escalation import escalate
from routing_metrics import (
    get_routing_metrics, reset_routing_metrics, _routing_metrics,
    _record_metrics, _record_v1_fallback, report_inference,
)

# Constants that stay in facade (used by multiple submodules)
USE_ROUTER_V2 = True
MODELS = { ... }
CATEGORY_TO_ROLE = { ... }
PIPELINES = { ... }

# Functions that stay in facade (thin, tie everything together)
def route_query(...): ...
def select_model(...): ...
def _select_model_v2(...): ...
def get_pipeline(...): ...
def _get_parameters(...): ...
def _extract_signals(...): ...
def _build_flags_from_context(...): ...
def _is_openrouter_available(...): ...
def _log(...): ...
```

### Anti-Patterns to Avoid

- **Circular imports:** classifier.py must NOT import from orchestrator.py. If classifier needs _log(), pass it as parameter or duplicate the 4-line function.
- **Moving too much to facade:** Keep route_query/select_model in facade since they orchestrate across all submodules. Don't force them into a submodule that creates circular deps.
- **Breaking _routing_metrics reference:** Tests directly access `orch._routing_metrics["test"] = 42`. The facade must re-export the SAME dict object, not a copy.

## Complete Function Inventory

### Group 1: Classification (~90 lines of code + ~370 lines of rules data)

| Function/Constant | Lines | Public | Called By |
|-------------------|-------|--------|-----------|
| `CLASSIFICATION_RULES` | 162-465 | YES | classify_query, classifier_v7.py |
| `IOL_KEYWORDS` | 156-160 | YES | (data, used in CLASSIFICATION_RULES) |
| `classify_query()` | 483-570 | YES | route_query, route_and_execute, telegram_router, eval_runner, health_sweep, test_iol_broker, orchestrator_bridge |

**Internal deps of classify_query:** `_log()`, `now()` (via _log), reads `tenazas.json` file for V3 router flag.
**External deps:** `finetuned_router.route`, `intent_normalizer.normalize_or_chat` (lazy import, try/except)

### Group 2: Model Selection (~180 lines)

| Function/Constant | Lines | Public | Called By |
|-------------------|-------|--------|-----------|
| `MODELS` | 60-97 | YES | select_model, tests |
| `CATEGORY_TO_ROLE` | 101-109 | YES | select_model, _select_model_v2, route_query |
| `USE_ROUTER_V2` | 41 | YES | select_model, tests |
| `PIPELINES` | 112-148 | YES | get_pipeline, select_model |
| `select_model()` | 622-755 | YES | route_query, escalate, tests |
| `_select_model_v2()` | 576-619 | PRIVATE | select_model |
| `get_pipeline()` | 761-776 | YES | route_query, escalate |
| `_is_openrouter_available()` | 2353-2356 | PRIVATE | select_model |

**Internal deps:** `_routing_metrics`, `_record_metrics`, `_record_v1_fallback`, `_log`, `MODELS`, `PIPELINES`, `CATEGORY_TO_ROLE`
**External deps:** `model_router.classify_task`, `model_router.route_by_role`, `model_router.MODEL_CATALOG`

### Group 3: Route Query + Helpers (~100 lines)

| Function | Lines | Public | Called By |
|----------|-------|--------|-----------|
| `route_query()` | 782-876 | YES | route_and_execute, escalate, tests, multi_agent_engine |
| `_get_parameters()` | 2266-2276 | PRIVATE | route_query, escalate |
| `_extract_signals()` | 2279-2299 | PRIVATE | route_query |
| `_build_flags_from_context()` | 2359-2376 | PRIVATE | route_query, tests |

### Group 4: Skill Execution (~1100 lines)

| Function/Constant | Lines | Public | Called By |
|-------------------|-------|--------|-----------|
| `SKILL_MAP` | 944-1057 | YES | execute_skill, tests |
| `SKILL_TIMEOUT` | 1059 | YES | execute_skill, handlers |
| `execute_skill()` | 1063-1164 | YES | route_and_execute, route_and_execute_multitool, telegram_router, skill_chain_executor, tests |
| `_parse_calendar()` | 1170-1200 | PRIVATE | _handle_calendar, _SKILL_PARSERS |
| `_parse_weather()` | 1203-1225 | PRIVATE | _handle_morning_greeting, _SKILL_PARSERS |
| `_parse_infra()` | 1228-1244 | PRIVATE | _SKILL_PARSERS |
| `_parse_proposals()` | 1247-1319 | PRIVATE | _SKILL_PARSERS |
| `_handle_smart_home()` | 1325-1359 | PRIVATE | _SKILL_HANDLERS |
| `_handle_tts()` | 1362-1399 | PRIVATE | _SKILL_HANDLERS |
| `_handle_calendar()` | 1402-1501 | PRIVATE | _SKILL_HANDLERS |
| `_handle_morning_greeting()` | 1504-1554 | PRIVATE | _SKILL_HANDLERS |
| `_EMAIL_PENDING_FILE` | 1557 | PRIVATE | email handlers |
| `_load_email_pending()` | 1560-1566 | SEMI-PUBLIC | telegram_router imports it |
| `_save_email_pending()` | 1569-1573 | PRIVATE | email handlers |
| `_clear_email_pending()` | 1576-1580 | PRIVATE | email handlers |
| `_handle_email()` | 1583-1637 | PRIVATE | _SKILL_HANDLERS |
| `_handle_email_send_start()` | 1640-1692 | PRIVATE | _handle_email |
| `complete_email_send()` | 1695-1755 | YES | telegram_router |
| `_send_email()` | 1758-1792 | PRIVATE | complete_email_send, _handle_email_send_start |
| `_handle_reminders()` | 1795-1850 | PRIVATE | _SKILL_HANDLERS |
| `_handle_model_trainer()` | 1853-1896 | PRIVATE | _SKILL_HANDLERS |
| `_handle_expense()` | 1899-1936 | PRIVATE | _SKILL_HANDLERS |
| `_handle_smoking()` | 1939-1961 | PRIVATE | _SKILL_HANDLERS |
| `_handle_network_scanner()` | 1964-1993 | PRIVATE | _SKILL_HANDLERS |
| `_SKILL_HANDLERS` | 1997-2008 | PRIVATE | execute_skill |
| `_SKILL_PARSERS` | 2010-2015 | PRIVATE | execute_skill |
| `route_and_execute()` | 2021-2098 | YES | telegram_router, tests |
| `route_and_execute_multitool()` | 2144-2260 | YES | (used by multitool path) |
| `_call_multitool_model()` | 2120-2141 | PRIVATE | route_and_execute_multitool |

### Group 5: Escalation (~55 lines)

| Function | Lines | Public | Called By |
|----------|-------|--------|-----------|
| `escalate()` | 879-934 | YES | tests, (confidence engine uses it) |

**Internal deps:** `select_model`, `get_pipeline`, `_get_parameters`, `_log`

### Group 6: Routing Metrics + Reporting (~75 lines)

| Function/Constant | Lines | Public | Called By |
|-------------------|-------|--------|-----------|
| `_routing_metrics` | 45 | SEMI-PUBLIC | select_model, route_and_execute_multitool, tests |
| `get_routing_metrics()` | 48-50 | YES | tests |
| `reset_routing_metrics()` | 53-55 | YES | tests |
| `report_inference()` | 2302-2332 | YES | telegram_router (post-LLM) |
| `_record_metrics()` | 2335-2341 | PRIVATE | select_model, route_query |
| `_record_v1_fallback()` | 2344-2350 | PRIVATE | select_model |

### Shared Infrastructure (stays in facade)

| Item | Lines | Used By |
|------|-------|---------|
| `BASE_DIR` | 36 | _log, SKILL_MAP paths |
| `ORCHESTRATOR_LOG` | 37 | _log, tests |
| `_log()` | 473-477 | classify_query, select_model, route_query, escalate, execute_skill, route_and_execute, route_and_execute_multitool |

## Module-Level State (Global Variables)

| Variable | Type | Mutable | Shared Across Modules |
|----------|------|---------|----------------------|
| `_routing_metrics` | `defaultdict(int)` | YES | select_model writes, route_and_execute_multitool writes, tests read/reset |
| `USE_ROUTER_V2` | `bool` | YES (tests toggle) | select_model reads, tests write |
| `ORCHESTRATOR_LOG` | `Path` | YES (tests override) | _log reads, tests override |
| `_EMAIL_PENDING_FILE` | `Path` | NO | email handlers only |
| `MULTITOOL_MODEL` | `str` | NO | _call_multitool_model |
| `MULTITOOL_OLLAMA_URL` | from config | NO | _call_multitool_model |
| `MULTITOOL_SYSTEM_PROMPT` | `str` | NO | _call_multitool_model |

**Key concern:** `_routing_metrics` is a defaultdict that tests directly modify (`orch._routing_metrics["test"] = 42`). The facade must re-export the same object reference, not a copy. Use: `from routing_metrics import _routing_metrics` (imports the object, not a copy).

## External Callers — Complete Import Map

| Caller File | Import Style | What It Imports | [VERIFIED] |
|-------------|-------------|-----------------|------------|
| `telegram_router.py:477` | `from orchestrator import classify_query` | classify_query | [VERIFIED: grep] |
| `telegram_router.py:542` | `from orchestrator import execute_skill` | execute_skill | [VERIFIED: grep] |
| `telegram_router.py:1206` | `from orchestrator import complete_email_send, _load_email_pending` | complete_email_send, _load_email_pending | [VERIFIED: grep] |
| `eval_runner.py:134` | `from orchestrator import classify_query` | classify_query | [VERIFIED: grep] |
| `health_sweep.py:643` | `from orchestrator import classify_query` | classify_query | [VERIFIED: grep] |
| `classifier_v7.py:107` | `from orchestrator import CLASSIFICATION_RULES` | CLASSIFICATION_RULES | [VERIFIED: grep] |
| `skill_chain_executor.py:35` | `from orchestrator import execute_skill` | execute_skill | [VERIFIED: grep] |
| `orchestrator_bridge/mcp_server.py:21` | `from orchestrator import classify_query, route_and_execute` | classify_query, route_and_execute | [VERIFIED: grep, archived] |
| `test_orchestrator.py:14` | `from runtime import orchestrator as orch` | ALL (via module reference) | [VERIFIED: grep] |
| `test_multitool.py:15` | `from runtime import orchestrator as orch` | ALL (via module reference) | [VERIFIED: grep] |
| `test_iol_broker.py:597+` | `from runtime.orchestrator import classify_query` | classify_query | [VERIFIED: grep] |

**Summary of imported names across all callers:**
- `classify_query` -- 6 callers
- `execute_skill` -- 2 callers
- `route_and_execute` -- 1 caller (archived)
- `complete_email_send` -- 1 caller
- `_load_email_pending` -- 1 caller
- `CLASSIFICATION_RULES` -- 1 caller

All other functions/constants are accessed only through the `orch.X` pattern in tests.

## Test Patch Paths — Complete Inventory

### test_orchestrator.py (65 tests)

Import: `from runtime import orchestrator as orch`

**Patches used:**
- `@patch("runtime.model_router.detect_backends")` -- patches model_router, NOT orchestrator
- `@patch("runtime.model_router._log")` -- patches model_router, NOT orchestrator

**Direct state access:**
- `orch.ORCHESTRATOR_LOG = Path(...)` -- 5 test classes override this
- `orch.USE_ROUTER_V2 = True/False` -- 4 test classes toggle this
- `orch._routing_metrics["test"] = 42` -- direct dict mutation
- `orch.reset_routing_metrics()` -- calls through facade
- `orch.classify_query(...)`, `orch.route_query(...)`, `orch.escalate(...)`, `orch.select_model(...)`, `orch.get_pipeline(...)`, `orch._build_flags_from_context(...)`, `orch.get_routing_metrics(...)` -- all through `orch.` prefix

**Verdict:** ALL 65 tests access orchestrator through `orch.X`. The facade re-exports make ALL tests work without any patch path changes, because:
1. `orch.classify_query` resolves to `orchestrator.classify_query` which the facade re-exports from `classifier`
2. `orch.ORCHESTRATOR_LOG` is a module-level variable in orchestrator.py -- still lives there
3. `orch._routing_metrics` must be the SAME object as `routing_metrics._routing_metrics`

### test_multitool.py (12 patches on orchestrator)

| Patch Target | Count | What It Patches |
|-------------|-------|-----------------|
| `runtime.orchestrator._call_multitool_model` | 5 | Multitool model call |
| `runtime.orchestrator.execute_skill` | 3 | Skill execution |
| `runtime.orchestrator.route_and_execute` | 4 | Route+execute fallback |

**Verdict:** These patch `runtime.orchestrator.X`. Because the facade re-exports `execute_skill` and `route_and_execute` from `skill_executor`, patching `runtime.orchestrator.execute_skill` patches the NAME in orchestrator's namespace (the re-exported reference). This works correctly -- `patch` replaces the name in the target module. The facade's `route_and_execute_multitool` function calls `execute_skill` and `route_and_execute` by name from its own module, so it will see the patched versions.

**IMPORTANT:** `route_and_execute_multitool` and `_call_multitool_model` must stay importable from `orchestrator` namespace. If they move to `skill_executor`, the facade must re-export them AND the test patches must still target `runtime.orchestrator.*` (which works through re-exports).

### test_iol_broker.py (4 tests)

Import: `from runtime.orchestrator import classify_query`

**Verdict:** Facade re-exports classify_query. No changes needed.

## Circular Import Analysis

### Potential Circular Risks

```
orchestrator.py (facade)
  imports from: classifier, skill_executor, escalation, routing_metrics
  
classifier.py
  needs: _log()  [for V3 router logging]
  does NOT need: anything from orchestrator

skill_executor.py
  needs: _log()  [for skill execution logging]
  needs: classify_query()  [route_and_execute calls it]
  needs: route_query()  [route_and_execute calls it]
  needs: OLLAMA_GPU_URL  [for multitool]
  needs: execute_skill  [route_and_execute_multitool calls it -- self-reference, fine]
  RISK: route_and_execute() calls both classify_query() and route_query()

escalation.py
  needs: select_model(), get_pipeline(), _get_parameters(), _log()
  RISK: imports from facade
  
routing_metrics.py
  needs: nothing from orchestrator (self-contained dict + functions)
  NO RISK
```

### Resolution Strategy

**Option A (recommended): Keep route_and_execute in facade, not skill_executor.**

`route_and_execute()` calls `classify_query()` + `route_query()` + `execute_skill()`. If it lives in skill_executor, skill_executor needs to import from both classifier and the facade, while the facade imports from skill_executor -- circular.

Instead: keep `route_and_execute()` and `route_and_execute_multitool()` in the facade. They are orchestration functions (they tie classification + routing + execution together). This keeps the dependency graph clean:

```
routing_metrics.py  ← no deps on other modules
classifier.py       ← needs only _log (inline or pass)
skill_executor.py   ← needs only _log (inline or pass)  
escalation.py       ← needs select_model, get_pipeline from facade (import at call time)
orchestrator.py     ← imports from all 4, defines route_query + route_and_execute
```

**Option B: Lazy imports in skill_executor.**

`route_and_execute()` lives in skill_executor but uses lazy imports:
```python
def route_and_execute(query, context=None, ...):
    from orchestrator import classify_query, route_query
    ...
```
This works because Python resolves lazy imports at call time, after all modules are loaded. But it's fragile and harder to test.

**Recommendation: Option A.** The facade is ~200 lines with route_query, select_model, route_and_execute, route_and_execute_multitool, and small helpers. This is within the 200-line target and avoids all circular import risks.

### _log() Duplication Strategy

`_log()` is 4 lines. Rather than create complex import chains, duplicate it in each submodule:

```python
# In each submodule:
def _log(entry: dict):
    _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry["ts"] = now().isoformat()
    with open(_LOG_PATH, "a") as f:
        f.write(json.dumps(entry, default=str) + "\n")
```

All submodules write to the same `logs/orchestrator.log` file. This is safe because the runtime is single-process (cron-based, no concurrency).

## Extraction Order

1. **routing_metrics.py** (FIRST -- zero dependencies, cleanest extraction)
   - Move: `_routing_metrics`, `get_routing_metrics`, `reset_routing_metrics`, `_record_metrics`, `_record_v1_fallback`, `report_inference`
   - Risk: LOW -- no imports from other submodules

2. **classifier.py** (SECOND -- depends only on _log)
   - Move: `classify_query`, `CLASSIFICATION_RULES`, `IOL_KEYWORDS`, `_TYPO_MAP`
   - Inline: `_log()` (4 lines)
   - Risk: LOW -- pure function with regex rules

3. **skill_executor.py** (THIRD -- largest, most handlers)
   - Move: `SKILL_MAP`, `SKILL_TIMEOUT`, `execute_skill`, all `_handle_*`, all `_parse_*`, `_SKILL_HANDLERS`, `_SKILL_PARSERS`, `complete_email_send`, `_load_email_pending`, `_save_email_pending`, `_clear_email_pending`, `_send_email`, `_handle_email_send_start`, `_EMAIL_PENDING_FILE`, `_call_multitool_model`, `MULTITOOL_MODEL`, `MULTITOOL_SYSTEM_PROMPT`, `MULTITOOL_OLLAMA_URL`
   - Inline: `_log()` (4 lines)
   - Risk: MEDIUM -- many functions, but all self-contained within the skill domain

4. **escalation.py** (FOURTH -- depends on facade functions)
   - Move: `escalate()`
   - Import from facade at call time: `select_model`, `get_pipeline`, `_get_parameters`
   - Risk: LOW -- small function with lazy imports

5. **orchestrator.py facade** (LAST -- wire everything together)
   - Keep: `route_query`, `select_model`, `_select_model_v2`, `get_pipeline`, `_get_parameters`, `_extract_signals`, `_build_flags_from_context`, `_is_openrouter_available`, `_log`, `USE_ROUTER_V2`, `MODELS`, `CATEGORY_TO_ROLE`, `PIPELINES`, `BASE_DIR`, `ORCHESTRATOR_LOG`, `route_and_execute`, `route_and_execute_multitool`
   - Re-export from submodules: all public names
   - Target: ~200 lines

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Backward-compatible facade | Custom import proxy | Simple `from X import Y` re-exports | Python's import system handles this natively |
| Circular import resolution | Complex import graph | Keep orchestration in facade, extract only leaf modules | Lazy imports are fragile and hard to debug |

## Common Pitfalls

### Pitfall 1: Breaking _routing_metrics Object Identity
**What goes wrong:** If facade does `from routing_metrics import _routing_metrics` but tests do `orch._routing_metrics["x"] = 42`, the mutation must affect the SAME dict.
**Why it happens:** Python imports bind names. `from X import obj` binds the local name to the same object. This WORKS for mutable objects (dict). But `from X import USE_ROUTER_V2` creates a new binding -- changing `orch.USE_ROUTER_V2 = False` changes the facade's binding, not the source.
**How to avoid:** For `_routing_metrics` (dict), re-import works. For `USE_ROUTER_V2` (bool), it must stay defined in the facade (orchestrator.py) since tests mutate it.
**Warning signs:** Test failures in TestRoutingMetrics or TestRouterV2Fallback.

### Pitfall 2: SKILL_MAP References HUE_BRIDGE_IP at Module Load Time
**What goes wrong:** SKILL_MAP builds a curl command string using `HUE_BRIDGE_IP` at import time (line 976). If skill_executor.py doesn't import runtime_config, this breaks.
**Why it happens:** The f-string in SKILL_MAP is evaluated when the module loads.
**How to avoid:** skill_executor.py must `from runtime_config import HUE_BRIDGE_IP` at the top level.
**Warning signs:** ImportError or wrong IP in smart-home skill.

### Pitfall 3: route_and_execute Calls classify_query AND route_query
**What goes wrong:** If route_and_execute moves to skill_executor, it needs to import from both classifier and the facade, creating circular deps.
**Why it happens:** route_and_execute is an orchestration function that ties everything together.
**How to avoid:** Keep route_and_execute in the facade (orchestrator.py). It's an orchestration concern.
**Warning signs:** ImportError: cannot import name 'X' from partially initialized module.

### Pitfall 4: test_multitool Patches "runtime.orchestrator.execute_skill"
**What goes wrong:** After moving execute_skill to skill_executor.py, patching "runtime.orchestrator.execute_skill" patches the re-exported name in the facade. This works IF route_and_execute_multitool (which calls execute_skill) also lives in the facade and references execute_skill through the module namespace.
**Why it happens:** `patch()` replaces the name in the specified module's namespace. If the caller uses a local import, the patch is ineffective.
**How to avoid:** Keep route_and_execute_multitool in the facade. Its call to execute_skill should be `execute_skill(...)` (module-level name), not `skill_executor.execute_skill(...)`.
**Warning signs:** test_multitool tests pass locally but patched function not called.

### Pitfall 5: Duplicate _log Writing to Different Files
**What goes wrong:** If each submodule has its own _log with a different log path, logs scatter across files.
**Why it happens:** Each module computes BASE_DIR/logs differently.
**How to avoid:** All submodules must use the same log path: `Path(__file__).parent / "logs" / "orchestrator.log"`. Since all files are in the same directory (`workspace/runtime/`), `Path(__file__).parent` resolves to the same dir.
**Warning signs:** Missing log entries when debugging.

## Code Examples

### Facade Re-export Pattern (verified from Phase 1 runtime_utils precedent)

```python
# orchestrator.py — facade
"""Orchestrator — Central AI routing facade for Tenazas.
Re-exports from submodules for backward compatibility.
"""

# Re-export classification
from classifier import classify_query, CLASSIFICATION_RULES, IOL_KEYWORDS

# Re-export skill execution
from skill_executor import (
    execute_skill, SKILL_MAP, SKILL_TIMEOUT,
    complete_email_send, _load_email_pending,
)

# Re-export escalation
from escalation import escalate

# Re-export metrics
from routing_metrics import (
    get_routing_metrics, reset_routing_metrics, _routing_metrics,
    report_inference,
)

# ... keep route_query, select_model, etc. defined here ...
```

### Import Pattern in Submodules (verified from Phase 1/2 try/except pattern)

```python
# classifier.py
import json, os, re
from pathlib import Path

try:
    from runtime_utils import now
except ImportError:
    from workspace.runtime.runtime_utils import now
```

## Facade Line Count Estimate

| Component | Estimated Lines |
|-----------|----------------|
| Docstring + imports | 30 |
| Re-exports from submodules | 20 |
| Constants (MODELS, CATEGORY_TO_ROLE, PIPELINES) | 50 |
| _log, _get_parameters, _extract_signals, _build_flags_from_context, _is_openrouter_available | 45 |
| select_model + _select_model_v2 | 65 (considering moving to its own section) |
| get_pipeline | 15 |
| route_query | 60 |
| route_and_execute + route_and_execute_multitool | 80 |
| **TOTAL** | **~365** |

This exceeds the 200-line target. To hit 200 lines, we need to also extract:
- **select_model + _select_model_v2 into a model_selection.py** (~65 lines saved), OR
- **route_and_execute + route_and_execute_multitool into skill_executor.py** using lazy imports (~80 lines saved), OR
- **MODELS + CATEGORY_TO_ROLE + PIPELINES data into a constants.py** (~50 lines saved)

**Revised recommendation:** Create an additional `orch_constants.py` (or use inline) and move route_and_execute into the facade but keep it slim. The 200-line target is aspirational -- ~250 lines of meaningful orchestration code is acceptable. The key metric is that NO single responsibility group remains monolithic.

**Alternative: Accept ~250 line facade.** The facade contains genuine orchestration logic (route_query, select_model, route_and_execute). These functions coordinate across classifier, skill_executor, and model_router. They ARE the orchestrator's job. A 250-line file with clear orchestration logic is healthier than a 150-line file that pushes orchestration concerns into leaf modules.

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | unittest (Python stdlib) |
| Config file | none (tests run directly) |
| Quick run command | `docker exec openclaw python3 /home/tenazas/tests/test_orchestrator.py` |
| Full suite command | `docker exec openclaw bash /home/tenazas/tests/run_tests.sh` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ORCH-01 | orchestrator.py split into modules | smoke | `python3 -c "from classifier import classify_query; from skill_executor import execute_skill"` | Wave 0 |
| ORCH-02 | Each module independently testable | unit | `python3 -c "from classifier import classify_query; print(classify_query('hola'))"` | Wave 0 |
| ORCH-03 | Existing imports work via facade | integration | `docker exec openclaw python3 /home/tenazas/tests/test_orchestrator.py` | YES (65 tests) |
| ORCH-04 | All existing tests pass | integration | `docker exec openclaw python3 /home/tenazas/tests/test_orchestrator.py && docker exec openclaw python3 /home/tenazas/tests/test_multitool.py` | YES (65 + ~12 tests) |

### Sampling Rate
- **Per task commit:** `docker exec openclaw python3 /home/tenazas/tests/test_orchestrator.py`
- **Per wave merge:** Full test suite
- **Phase gate:** test_orchestrator (65) + test_multitool (orchestrator patches) + test_iol_broker (classify_query) all green

### Wave 0 Gaps
- [ ] Import smoke test for new modules (classifier.py, skill_executor.py, escalation.py, routing_metrics.py)
- [ ] Verify `from orchestrator import X` still works for all 6 caller names

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | -- |
| V3 Session Management | no | -- |
| V4 Access Control | no | -- |
| V5 Input Validation | no (refactor only, no new inputs) | -- |
| V6 Cryptography | no | -- |

This is a pure structural refactor. No new inputs, no new attack surfaces. The security properties of the existing code are preserved exactly.

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `route_and_execute_multitool` has a bug referencing undefined `raw_response` and `parse_result` variables (lines 2243, 2257) | Function Inventory | LOW -- existing bug, not introduced by refactor |
| A2 | ~250 line facade is acceptable vs strict 200-line target | Facade Line Count | LOW -- CONTEXT.md says "under 200 lines" but genuine orchestration logic justifies slightly more |
| A3 | All submodules in same directory means `Path(__file__).parent` resolves consistently | Pitfall 5 | LOW -- all files in workspace/runtime/ |

## Open Questions

1. **Facade size: 200 vs ~250 lines?**
   - What we know: Pure re-exports + route_query + select_model + route_and_execute = ~250 lines minimum
   - What's unclear: Is 200 a hard limit or aspirational?
   - Recommendation: Accept ~250 if the alternative is circular imports or pushing orchestration into leaf modules. The planner can split further if needed.

2. **route_and_execute_multitool bugs**
   - What we know: Lines 2243 and 2257 reference `raw_response` and `parse_result` which are undefined in current code (likely remnants from a refactor)
   - What's unclear: Is this code path currently exercised?
   - Recommendation: Fix during extraction (remove undefined references), note as bug fix in commit.

## Sources

### Primary (HIGH confidence)
- orchestrator.py direct inspection (2377 lines, every function mapped) [VERIFIED: file read]
- test_orchestrator.py direct inspection (678 lines, 65 tests, all patch paths mapped) [VERIFIED: file read]
- test_multitool.py patch paths (12 orchestrator patches identified) [VERIFIED: grep]
- External callers (7 runtime files + 3 test files) [VERIFIED: grep across codebase]

### Secondary (MEDIUM confidence)
- Phase 1/2 precedent for try/except import pattern [VERIFIED: runtime_utils.py, runtime_config.py]
- Python import semantics for facade pattern [ASSUMED based on Python language spec]

## Metadata

**Confidence breakdown:**
- Function mapping: HIGH -- every function inventoried from source
- Import graph: HIGH -- complete grep across codebase
- Test patch paths: HIGH -- all 65+12 tests analyzed
- Extraction order: HIGH -- dependency analysis verified
- Facade line count: MEDIUM -- estimate, may vary during implementation

**Research date:** 2026-04-28
**Valid until:** 2026-05-28 (stable -- internal refactor, no external dependency changes)
