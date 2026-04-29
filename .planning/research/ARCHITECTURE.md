# Architecture Patterns: Multi-Agent System Integration

**Domain:** Multi-agent coordination layer for existing AI assistant
**Researched:** 2026-04-28
**Overall confidence:** HIGH (based on direct codebase analysis)

## Current Architecture (As-Is)

```
Telegram message
  |
  v
telegram_router.py
  |-- classify_intent() [3-tier: hybrid_router -> orchestrator.classify_query -> chat]
  |-- INTERCEPTED_SKILLS set (weather, email, calendar, smart-home, etc.)
  |
  |-- skill match? -> execute_intercepted_skill() -> orchestrator.execute_skill()
  |-- iol_broker?  -> _execute_iol() [subprocess to iol_handler_cli.py]
  |-- chat/other?  -> Ollama /api/chat with SOUL.md context
  |
  v
orchestrator.py (facade)
  |-- classifier.py      -> classify_query() [regex + keyword scoring]
  |-- skill_executor.py  -> execute_skill() [SKILL_MAP -> subprocess/handler]
  |-- escalation.py      -> escalate() [knowledge->reasoning, etc.]
  |-- routing_metrics.py -> counters, latency tracking
  |
  |-- route_query()         -> classify -> select_model -> pipeline
  |-- route_and_execute()   -> classify -> if skill: execute; if complex: multi_agent_engine
  |-- select_model()        -> V2 (model_router.route_by_role) -> V1 fallback
```

### Key Integration Points (Existing)

| Module | Entry Function | What It Does | Callers |
|--------|---------------|--------------|---------|
| `telegram_router.py` | `classify_intent()` | 3-tier classification | `_process_message()` |
| `telegram_router.py` | `execute_intercepted_skill()` | Skill execution via orchestrator | `_process_message()` |
| `orchestrator.py` | `route_and_execute()` | Classify + execute if skill | telegram_router, MCP bridge |
| `orchestrator.py` | `classify_query()` | Re-export from classifier.py | telegram_router, bridge |
| `orchestrator.py` | `execute_skill()` | Re-export from skill_executor.py | telegram_router, bridge |
| `model_router.py` | `route_by_role()` | Hardware-aware model selection | orchestrator._select_model_v2 |
| `agent_coordinator.py` | `route()` | Multi-domain agent routing | NOT currently wired in |
| `agent_arbitrator.py` | `resolve()` | Conflict resolution | agent_coordinator only |

### Existing Sprint 9 Agent Modules (Already Present)

These modules exist but are **not wired into the main pipeline**:

| Module | `owns()` Keywords | `assess()` | `handle_query()` | Status |
|--------|-------------------|------------|-------------------|--------|
| `agent_infra.py` | container, gpu, docker, vpn... | Yes (reads snapshot + predictor) | Yes | Standalone |
| `agent_personal.py` | calendar, email, reminder... | Yes (checks calendar + email) | Yes | Standalone |
| `agent_finance.py` | binance, iol, crypto, trading... | Yes (Binance PnL + IOL) | Yes | Standalone |
| `agent_research.py` | research, rag, knowledge... | No | Yes (SearXNG) | Standalone |
| `agent_self_improve.py` | N/A | N/A | N/A | Needs check |
| `agent_coordinator.py` | Multi-domain triggers | N/A | `route()` aggregates | Standalone CLI |
| `agent_arbitrator.py` | Priority-based resolution | N/A | `resolve()` | Called by coordinator |

**Critical gap:** `agent_coordinator.route()` exists but `telegram_router.py` and `orchestrator.py` never call it. The agents are islands.

---

## Recommended Architecture (To-Be)

### Design Principle: Coordinator as Smart Router, Not Replacement

**Do NOT replace orchestrator.** Instead, insert `agent_coordinator` as a layer between `telegram_router` and `orchestrator`. The orchestrator's `classify_query()` and `execute_skill()` remain the execution engine. The coordinator decides *which agent(s) should observe/recommend*, while orchestrator handles *how to execute*.

Rationale:
- Orchestrator has 65+ tests, proven classification, skill execution, model selection
- Replacing it means rewriting all callers (telegram_router, MCP bridge, hybrid_router)
- The coordinator's job is domain-aware routing, not skill execution
- Skills (weather, calendar, email) should remain deterministic -- agents add *advisory* capability

### New Data Flow

```
Telegram message
  |
  v
telegram_router.py
  |-- classify_intent() [unchanged]
  |
  |-- skill match? -> execute_intercepted_skill() [unchanged, fast path]
  |                    \_ AFTER execution: notify_agents(query, skill_result)  [NEW, async]
  |
  |-- NOT skill? -> agent_coordinator.route_with_context(query, context) [NEW]
  |                    |
  |                    |-- identify_agents(query) [existing]
  |                    |-- for each agent: agent.observe(query, context) [NEW method]
  |                    |-- any recommendations? -> format + send to Telegram
  |                    |-- conflicts? -> agent_arbitrator.resolve() [existing]
  |                    |
  |                    |-- falls through to orchestrator for LLM response
  |                    \_ orchestrator.route_and_execute(query, context) [unchanged]
  |
  v
response to user (LLM response + agent recommendations if any)
```

### Component Boundaries

| Component | Responsibility | New? | Communicates With |
|-----------|---------------|------|-------------------|
| `telegram_router.py` | Message intake, skill interception, response formatting | Modified (2 hooks) | coordinator, orchestrator |
| `agent_coordinator.py` | Agent selection, observation dispatch, recommendation aggregation | **Rewritten** | all agents, arbitrator, orchestrator |
| `agent_arbitrator.py` | Conflict resolution when agents disagree | Existing (minor changes) | coordinator |
| `agent_infra.py` | Observe infra-related queries, recommend actions | Modified (add `observe()`) | coordinator, homelab_snapshot |
| `agent_personal.py` | Observe personal queries, recommend actions | Modified (add `observe()`) | coordinator, calendar/email |
| `agent_finance.py` | Observe finance queries, recommend actions | Modified (add `observe()`) | coordinator, IOL/Binance |
| `agent_research.py` | Observe knowledge queries, suggest research | Modified (add `observe()`) | coordinator, RAG |
| `agent_self_improve.py` | Observe patterns, suggest improvements | Modified (add `observe()`) | coordinator, learning |
| `orchestrator.py` | Classification, skill execution, model selection | **Unchanged** | telegram_router, MCP bridge |
| `agent_protocol.py` | Base protocol/interface for agents | **New** | all agents |
| `agent_state.py` | Shared JSON state management (read/write) | **New** | coordinator, all agents |

---

## Detailed Design

### 1. Agent Protocol (`agent_protocol.py`) -- NEW

Define a consistent interface that all agents must implement:

```python
"""Agent Protocol -- Base interface for domain agents."""
from dataclasses import dataclass, field
from typing import Optional

@dataclass
class Observation:
    """What an agent noticed about a query/event."""
    agent: str              # "infra", "finance", etc.
    relevance: float        # 0.0-1.0 how relevant this is to the agent
    summary: str            # one-liner observation
    recommendations: list   # list of Recommendation
    context_additions: dict # extra context for the LLM (injected into prompt)

@dataclass
class Recommendation:
    """A concrete action the agent suggests."""
    action: str             # "restart sonarr", "check IOL balance"
    reason: str             # why
    severity: str           # "info", "warning", "critical"
    requires_approval: bool # always True for this milestone
    agent: str              # source agent
    confidence: float       # 0.0-1.0

# Required interface for all agents:
# - owns(topic: str) -> bool           [existing]
# - observe(query: str, context: dict) -> Observation | None  [NEW]
# - assess() -> dict                   [existing]
# - handle_query(query: str) -> str    [existing, for direct questions]
```

**Why dataclasses, not ABC:** The agents already exist with varied signatures. An ABC forces refactoring all at once. Dataclasses as return types let agents adopt incrementally -- return `Observation` from `observe()`, but keep existing methods working.

### 2. Agent State (`agent_state.py`) -- NEW

Centralized shared state for inter-agent communication:

```python
"""Agent State -- Shared JSON state for agent coordination."""

STATE_FILE = STATE_DIR / "agents" / "shared_state.json"

# Structure:
# {
#     "last_updated": "ISO timestamp",
#     "agent_states": {
#         "infra": {"health": "ok", "last_assess": "...", "active_issues": []},
#         "finance": {"alerts": [], "last_assess": "..."},
#         ...
#     },
#     "pending_recommendations": [
#         {"id": "sha12", "agent": "infra", "action": "restart sonarr",
#          "reason": "down 10 min", "severity": "warning",
#          "status": "pending", "created": "..."}
#     ],
#     "recent_observations": [
#         {"agent": "infra", "query": "...", "relevance": 0.8, "ts": "..."}
#     ]
# }

# API:
# get_agent_state(agent: str) -> dict
# update_agent_state(agent: str, state: dict)
# add_recommendation(rec: Recommendation) -> str  # returns ID
# get_pending_recommendations(agent: str = None) -> list
# mark_recommendation(rec_id: str, status: str, note: str = "")
# get_recent_observations(limit: int = 20) -> list
# add_observation(obs: Observation)
```

**Why a single JSON file, not per-agent files:** The agents already write to `state/agents/{name}_status.json`. The shared state is for cross-agent coordination only. Per-agent state stays in per-agent files (no change needed).

### 3. Coordinator Rewrite (`agent_coordinator.py`) -- REWRITE

Current coordinator is minimal (identifies agents, calls `handle_query`, concatenates strings). New coordinator must:

```python
def route_with_context(query: str, context: dict = None) -> dict:
    """Main entry point. Called by telegram_router for non-skill queries.
    
    Flow:
    1. Identify relevant agents (existing identify_agents())
    2. For each: call agent.observe(query, context)
    3. Collect observations with relevance > 0.3
    4. If any recommendations: format for Telegram (observe+recommend only)
    5. If conflicts: call arbitrator.resolve()
    6. Return: {observations, recommendations, llm_context_additions, conflicts}
    """

def notify_agents(query: str, skill_result: dict):
    """Called AFTER skill execution to let agents observe what happened.
    
    Non-blocking. Agents can update their state based on skill outputs.
    E.g., finance agent notices an expense was logged.
    """

def periodic_assess() -> list:
    """Called by cron/worker. Each agent runs assess(), results aggregated.
    
    Critical findings -> Telegram notification.
    """
```

**Critical design decision:** `route_with_context()` does NOT replace the LLM call. It runs agents in parallel, collects observations, and returns them to telegram_router. The telegram_router then:
1. Sends agent recommendations (if any) as a separate message or prefix
2. Still sends the query to Ollama for the conversational response

### 4. Telegram Router Modifications -- 2 HOOKS ONLY

```python
# In _process_message(), AFTER skill execution:
# HOOK 1 (post-skill notification)
if category in INTERCEPTED_SKILLS and skill_output:
    try:
        from agent_coordinator import notify_agents
        notify_agents(text, {"skill": category, "output": skill_output[:500]})
    except Exception:
        pass  # never block on agent failure

# In _process_message(), BEFORE LLM call (for non-skill queries):
# HOOK 2 (pre-LLM observation)
agent_context = {}
try:
    from agent_coordinator import route_with_context
    coord_result = route_with_context(text, {"chat_id": chat_id})
    if coord_result.get("recommendations"):
        # Send recommendations as separate message
        _send_agent_recommendations(token, chat_id, coord_result["recommendations"])
    agent_context = coord_result.get("llm_context_additions", {})
except Exception:
    pass  # never block on agent failure

# LLM call proceeds as normal, optionally with enriched context
```

**Zero-risk integration:** Both hooks are wrapped in `try/except` with `pass`. If agent system crashes, telegram_router works exactly as before.

### 5. Worker Integration -- PERIODIC ASSESSMENT

Add a new worker or extend `agents_worker`:

```python
# In workers/agents_worker.py (already exists):
# Add periodic agent assessment
from agent_coordinator import periodic_assess
results = periodic_assess()
# Critical findings -> Telegram alert (via existing notification path)
```

---

## Anti-Patterns to Avoid

### Anti-Pattern 1: Coordinator Replaces Orchestrator
**What:** Making coordinator the "main brain" that handles classification + execution
**Why bad:** Orchestrator has 65+ tests, proven classification, skill execution engine. Rewriting breaks everything.
**Instead:** Coordinator is an advisory layer. Orchestrator remains the execution engine.

### Anti-Pattern 2: Agents Execute Actions Directly
**What:** Agents calling `docker_wrapper.restart()` or `finance.add_from_text()` themselves
**Why bad:** Bypasses safety_guard, approval gates, audit logging. Double-execution risk.
**Instead:** Agents return `Recommendation` objects. Execution always goes through orchestrator + approval.

### Anti-Pattern 3: Blocking Agent Calls in Telegram Router
**What:** `route_with_context()` blocking the response indefinitely
**Why bad:** If any agent is slow (LLM call, API timeout), user waits with no response
**Instead:** Agent observation should have a hard timeout (5s max). Recommendations sent as separate message.

### Anti-Pattern 4: Agents Talking to Each Other Directly
**What:** `agent_infra` importing `agent_finance` to check portfolio when containers down
**Why bad:** Circular imports, untestable, coupling nightmare
**Instead:** All cross-agent communication goes through `agent_state.py` shared state file.

### Anti-Pattern 5: Replacing classify_query with Agent Ownership
**What:** Using `agent.owns()` as the primary classifier instead of orchestrator's regex rules
**Why bad:** `owns()` is coarse (keyword matching). Orchestrator's classifier has scoring, context, multi-tier fallback.
**Instead:** Orchestrator classifies. Coordinator uses classification result to select agents.

---

## Integration Points (Complete Map)

### Files That Need Modification

| File | Change | Risk | Lines Affected |
|------|--------|------|----------------|
| `telegram_router.py` | Add 2 hooks (post-skill notify, pre-LLM observe) | LOW | ~20 lines in `_process_message()` |
| `agent_coordinator.py` | Rewrite with `route_with_context()`, `notify_agents()`, `periodic_assess()` | MEDIUM | Full rewrite (~200 lines) |
| `agent_infra.py` | Add `observe()` method returning `Observation` | LOW | ~30 lines added |
| `agent_personal.py` | Add `observe()` method returning `Observation` | LOW | ~30 lines added |
| `agent_finance.py` | Add `observe()` method returning `Observation` | LOW | ~30 lines added |
| `agent_research.py` | Add `observe()` method returning `Observation` | LOW | ~30 lines added |
| `agent_self_improve.py` | Add `observe()` method returning `Observation` | LOW | ~30 lines added |
| `agent_arbitrator.py` | Update `resolve()` to work with `Recommendation` dataclass | LOW | ~10 lines changed |
| `workers/agents_worker.py` | Add `periodic_assess()` call | LOW | ~5 lines added |

### Files That Must NOT Change

| File | Reason |
|------|--------|
| `orchestrator.py` | Execution engine, 65+ tests, used by MCP bridge |
| `classifier.py` | Classification rules, used by orchestrator + telegram_router |
| `skill_executor.py` | Skill execution, SKILL_MAP, parsers |
| `model_router.py` | Model selection, guardrails, VRAM management |
| `escalation.py` | Escalation paths |
| `routing_metrics.py` | Metrics collection |
| `hybrid_router.py` | Multi-tier classification |
| `docker_wrapper.py` | Safety layer for Docker operations |
| `safety_guard.py` | Central safety validation |

### New Files

| File | Purpose | Dependencies |
|------|---------|-------------|
| `agent_protocol.py` | Dataclasses: Observation, Recommendation, CoordinatorResult | None (stdlib only) |
| `agent_state.py` | Shared JSON state management | pathlib, json (stdlib) |

---

## Build Order (Minimizes Disruption)

### Phase 1: Foundation (no behavioral change)
1. **`agent_protocol.py`** -- Define dataclasses. No callers yet. Pure addition.
2. **`agent_state.py`** -- Shared state management. No callers yet. Pure addition.
3. **Tests for protocol + state** -- Unit tests for dataclass creation, state CRUD.

*After Phase 1: System runs exactly as before. Two new files exist, unused.*

### Phase 2: Agent Upgrade (backward compatible)
4. **Add `observe()` to each agent** -- Returns `Observation` or `None`. Does NOT remove existing methods.
   - `agent_infra.py` -- observe infra queries, check snapshot
   - `agent_personal.py` -- observe personal queries, check calendar
   - `agent_finance.py` -- observe finance queries, check alerts
   - `agent_research.py` -- observe knowledge queries
   - `agent_self_improve.py` -- observe patterns
5. **Tests for each agent's `observe()`** -- Mock dependencies, verify Observation format.

*After Phase 2: System runs exactly as before. Agents have new method, nobody calls it.*

### Phase 3: Coordinator Rewrite (still isolated)
6. **Rewrite `agent_coordinator.py`** with:
   - `route_with_context(query, context)` -- calls `observe()` on relevant agents
   - `notify_agents(query, skill_result)` -- post-execution notification
   - `periodic_assess()` -- cron-driven assessment
   - Keep backward-compatible `route()` and `identify_agents()` for existing tests
7. **Tests for coordinator** -- Mock agents, verify routing, aggregation, conflict delegation.

*After Phase 3: System runs exactly as before. Coordinator rewritten but not wired in.*

### Phase 4: Wire Into Pipeline (behavioral change)
8. **Modify `telegram_router.py`** -- Add 2 hooks:
   - Post-skill: `notify_agents()` (fire-and-forget)
   - Pre-LLM: `route_with_context()` (with timeout + try/except)
9. **Modify `workers/agents_worker.py`** -- Add `periodic_assess()` call
10. **Integration tests** -- End-to-end: message -> classification -> agent observation -> LLM response

*After Phase 4: Multi-agent system is live. Agents observe and recommend. Everything approval-gated.*

### Phase 5: Polish
11. **Telegram formatting** -- Format agent recommendations nicely for Telegram
12. **Prometheus metrics** -- `tenazas_agent_observations_total{agent}`, `tenazas_agent_recommendations_total{agent}`
13. **Grafana dashboard update** -- Agent activity panel

---

## Data Flow Details

### Scenario 1: User asks about weather (deterministic skill)

```
User: "como esta el clima"
  -> telegram_router.classify_intent() -> "skill:weather"
  -> execute_intercepted_skill() -> curl Open-Meteo -> "18C, despejado"
  -> HOOK 1: notify_agents("como esta el clima", {skill: "skill:weather", output: "18C..."})
      -> coordinator.notify_agents() -> [no agent cares about weather] -> noop
  -> send "18C, despejado" to Telegram
```

No change in user experience. Agents silently notified.

### Scenario 2: User asks about something multi-domain

```
User: "como esta todo"
  -> telegram_router.classify_intent() -> "chat" (not a skill)
  -> HOOK 2: route_with_context("como esta todo", {chat_id: "..."})
      -> identify_agents() -> ["infra", "personal", "finance"]
      -> agent_infra.observe() -> Observation(relevance=0.9, summary="all containers up")
      -> agent_personal.observe() -> Observation(relevance=0.7, summary="2 events today")
      -> agent_finance.observe() -> Observation(relevance=0.6, summary="BTC +2.3%")
      -> no recommendations (everything normal) -> return observations
  -> LLM call with enriched context: "Infra: all up. Calendar: 2 events. Markets: BTC +2.3%"
  -> send LLM response to Telegram
```

User gets a richer response because LLM has agent context.

### Scenario 3: Agent detects issue and recommends action

```
User: "que paso con sonarr"
  -> telegram_router.classify_intent() -> "chat"
  -> HOOK 2: route_with_context("que paso con sonarr", {chat_id: "..."})
      -> identify_agents() -> ["infra"]
      -> agent_infra.observe() -> Observation(
            relevance=0.95,
            summary="sonarr stopped 15 min ago",
            recommendations=[Recommendation(
                action="restart sonarr",
                reason="container stopped, no crash loop detected",
                severity="warning",
                requires_approval=True  # ALWAYS
            )]
        )
      -> send recommendation to Telegram: "Sonarr caido 15 min. Reinicio? [Aprobar] [Cancelar]"
  -> LLM call proceeds for conversational response
  -> Sebas taps [Aprobar] -> exec_approvals flow -> orchestrator.execute_skill()
```

### Scenario 4: Periodic assessment (cron)

```
workers/agents_worker.py runs every 5 min:
  -> coordinator.periodic_assess()
      -> agent_infra.assess() -> {health: "degraded", issues: ["gluetun stopped"]}
      -> agent_finance.assess() -> {alerts: ["BTC -8%"]}
      -> critical findings? -> send Telegram alert
      -> update shared state
```

---

## State File Structure

### `state/agents/shared_state.json` (NEW)

```json
{
    "schema_version": 1,
    "last_updated": "2026-04-28T15:30:00-03:00",
    "agent_states": {
        "infra": {
            "health": "ok",
            "last_assess": "2026-04-28T15:25:00-03:00",
            "active_issues": [],
            "containers_up": 20
        },
        "finance": {
            "last_assess": "2026-04-28T15:25:00-03:00",
            "alerts": [],
            "portfolio_value": 1500000
        },
        "personal": {
            "last_assess": "2026-04-28T15:25:00-03:00",
            "items": ["2 eventos hoy", "3 emails sin leer"]
        }
    },
    "pending_recommendations": [],
    "recent_observations": []
}
```

Cap: 50 pending recommendations, 100 recent observations. Older entries pruned on write.

### Existing State Files (Unchanged)

| File | Used By | Keep? |
|------|---------|-------|
| `state/agents/infra_status.json` | agent_infra.assess() | Yes, internal to agent |
| `state/agents/infra_memory.json` | agent_infra memory | Yes |
| `state/agents/coordinator_log.json` | agent_coordinator route history | Yes |
| `state/agents/conflicts.json` | agent_arbitrator | Yes |

---

## Scalability and Performance

| Concern | Approach |
|---------|----------|
| Agent timeout | Each `observe()` has 5s hard timeout in coordinator |
| Slow agent blocks response | `notify_agents()` is fire-and-forget (try/except with timeout) |
| State file contention | Single writer (coordinator), multiple readers (agents). Atomic write via temp+rename. |
| Agent crash | Each agent wrapped in try/except in coordinator. Crashed agent returns None. |
| Too many recommendations | Coordinator filters: only relevance > 0.3, max 3 recommendations per response |
| State file bloat | Capped collections: 50 pending, 100 observations. Pruned on every write. |

---

## Testing Strategy

| Level | What | How |
|-------|------|-----|
| Unit | agent_protocol.py dataclasses | Direct instantiation, field validation |
| Unit | agent_state.py CRUD | Mock filesystem, verify JSON structure |
| Unit | Each agent's observe() | Mock dependencies (snapshot, calendar, IOL), verify Observation |
| Unit | Coordinator routing | Mock agents, verify dispatch + aggregation |
| Unit | Arbitrator with Recommendation objects | Existing tests + new dataclass inputs |
| Integration | telegram_router hooks | Mock coordinator, verify hooks called, verify no-block on error |
| Integration | Worker periodic_assess | Mock coordinator, verify cron integration |
| E2E | Full message flow | Mock Telegram + agents, verify recommendations formatted |

Estimated: ~40-50 new tests across 4-5 test files.

---

## Sources

- Direct codebase analysis of all files referenced above
- Existing Sprint 9 modules (`agent_*.py`, `agent_coordinator.py`, `agent_arbitrator.py`)
- Existing test patterns from `tests/test_sprint9.py` (42 tests)
- Orchestrator facade pattern from v1.0 refactor (`orchestrator.py` + submodules)
