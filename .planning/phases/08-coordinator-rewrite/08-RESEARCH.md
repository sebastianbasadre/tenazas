# Phase 8: Coordinator Rewrite — Research

**Researched:** 2026-04-29
**Domain:** Python multi-agent coordinator — ThreadPoolExecutor routing, owns() protocol, correlation ID tracing
**Confidence:** HIGH (all key findings from direct codebase inspection)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

**D-01: Coordinator-first, skill fallback**
Every query enters `agent_coordinator.py` first. The coordinator calls `owns(query)` on all 5 agents. If 1+ agent claims ownership, it routes through the agent pipeline. If 0 agents claim, it falls back to `skill_executor.execute_skill()` for deterministic skills (weather, calendar, etc.), then to LLM chat. The existing `orchestrator.py` facade remains intact (no deletion) for backward compatibility in Phase 9 feature flag. Coordinator does NOT call `orchestrator.route_query()`.

**D-02: Multi-domain detection via parallel owns()**
Every query is evaluated by all 5 agents' `owns()` method in parallel. The coordinator collects all claimants. 2+ True = multi-domain, all claimants routed in parallel.

**D-03: Execution strategy table**
| Claimants | Strategy | Mechanism |
|---|---|---|
| 2+ agents | Parallel | ThreadPoolExecutor, collect all results |
| 1 agent | First-wins | Direct call, no parallelism overhead |
| 0 agents | Skill fallback | `skill_executor.execute_skill()` → LLM chat |
Timeout: 30s per agent. Timed-out agents are omitted from aggregation (not fatal).

**D-04: Structured section aggregation**
```
📡 Infra: containers OK, GPU 45°C, VPN activo
💰 Finanzas: IOL balance $1.2M, variación +2.3%
```
Domain emoji map: infra→📡, personal→👤, finance→💰, research→🔍, self_improve→🔧
Newline separator between sections. No markdown headers. Telegram plain-text friendly.

**Implementation constraints:**
- `owns(query: str) -> bool` — new method added to all 5 agents. Pure keyword matching, no I/O, < 10ms.
- `agent_coordinator.py` — module exists; Phase 8 is a full rewrite of it.
- ThreadPoolExecutor — stdlib only, no new deps. Max workers = 5.
- Correlation ID — `uuid4()[:8]` per query, passed to agent `handle()` calls as kwarg, logged at coordinator entry and agent execution.
- Fallback chain: coordinator → skill_executor → LLM chat. No direct `orchestrator.route_query()` call.
- Telegram router wiring is Phase 9 work, not Phase 8.

### Claude's Discretion

None documented in CONTEXT.md.

### Deferred Ideas (OUT OF SCOPE)

- Sequential execution strategy (agents in dependency order) — no cross-agent dependencies currently
- LLM-based response synthesis for multi-domain — adds latency, defer to Phase 9 if needed
- Dynamic `owns()` threshold tuning — static keyword matching is sufficient for now
- Coordinator learning which agent handles which query best — defer to post-v2.0
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| CORD-01 | Query classification and routing to domain agents, replacing orchestrator as entry point | Current `agent_coordinator.py` exists but uses registry dispatch — needs rewrite to `owns()`-parallel + execution strategies |
| CORD-02 | Multi-domain query aggregation — combine responses spanning multiple agents | Aggregation must use emoji-labeled sections (D-04); current `_aggregate()` in coordinator does plain newline join |
| CORD-03 | Strategy selection — parallel vs sequential vs first-wins based on query type | ThreadPoolExecutor for parallel; direct call for single; `execute_skill()` fallback for zero claims |
| CORD-04 | Correlation IDs for end-to-end query tracing through coordinator → agent → skill | `uuid4()[:8]`, kwarg passed to `handle()`, logged in coordinator log + agent log |
</phase_requirements>

---

## Summary

Phase 8 rewrites `agent_coordinator.py` from its current additive/registry-delegation pattern into the new routing brain described in the CONTEXT.md decisions. The existing file (90% complete as a scaffold) requires replacing its `route()` function with one that: (1) runs all 5 `owns()` calls in parallel to determine claimants, (2) selects execution strategy (parallel/first-wins/skill-fallback), (3) calls `handle(query, correlation_id=...)` on winning agents, (4) aggregates results using emoji-labeled sections, and (5) traces the entire path via correlation IDs.

The five agents all implement `owns(topic: str) -> bool` and `handle(query: str) -> tuple[Observation, list[Recommendation]]`. No agent currently accepts a `correlation_id` kwarg — this is Phase 8's job to add as a kwarg to all five `handle()` definitions. The `skill_executor.execute_skill(skill_category, query)` is the existing fallback path and returns `{skill, success, output, raw, destructive, error}`.

The biggest implementation risk is the `handle()` signature change: all 5 agent modules must accept `correlation_id=None` as an optional kwarg without breaking the existing empty-query proactive caching pattern in `agents_worker.py` (which calls `agent.handle("")`).

**Primary recommendation:** Rewrite `agent_coordinator.py` in-place. Add `correlation_id=None` kwarg to all 5 `handle()` functions. Use `concurrent.futures.ThreadPoolExecutor(max_workers=5)` for both `owns()` evaluation and multi-agent `handle()` execution. Skip arbitrator for Phase 8 (multi-domain = run all claimants in parallel, not winner-picking).

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Query entry point | Coordinator (`agent_coordinator.py`) | — | D-01 locks this; orchestrator becomes legacy |
| Domain classification | Coordinator (parallel `owns()`) | — | Each agent's keyword set is authoritative for its domain |
| Parallel execution | Coordinator (ThreadPoolExecutor) | — | stdlib concurrency, no new deps |
| Single-domain execution | Coordinator (direct call) | — | First-wins avoids threading overhead for common case |
| Skill execution fallback | `skill_executor.execute_skill()` | LLM chat | When 0 agents claim; existing code unchanged |
| Response aggregation | Coordinator (`_aggregate()` rewrite) | — | Deterministic emoji-labeled sections, no LLM |
| Conflict arbitration | `agent_arbitrator.resolve()` | — | Existing Phase 7 module; NOT called in Phase 8 (parallel, not winner-pick) |
| Correlation ID generation | Coordinator | Each agent log | `uuid4()[:8]` at coordinator entry, propagated as kwarg |
| Observability | Coordinator log + per-agent logs | — | JSONL lines with `correlation_id` field |

---

## Standard Stack

### Core (all stdlib — no new deps)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `concurrent.futures.ThreadPoolExecutor` | stdlib | Parallel owns() + parallel handle() | No new deps, existing codebase is sync throughout |
| `uuid` | stdlib | Correlation ID generation | `uuid4()` is the standard; `str(uuid4())[:8]` gives 8-char hex |
| `json` | stdlib | JSONL logging | Project-wide pattern |
| `pathlib.Path` | stdlib | File paths | Project-wide pattern |
| `importlib` | stdlib | Lazy agent module loading | Current coordinator already uses this pattern |

### Supporting (already in codebase)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `agent_protocol.Observation` | local | Typed return from handle() | All agent results go through this |
| `agent_arbitrator.resolve()` | local | Conflict resolution | NOT used in Phase 8 (deferred — parallel runs all claimants) |
| `skill_executor.execute_skill()` | local | Fallback when 0 agents claim | Zero-claim path only |
| `runtime_utils.now` | local | Timestamps in logs | Project-wide pattern |

**No installation required** — all code uses existing runtime or stdlib.

---

## Architecture Patterns

### System Architecture Diagram

```
Telegram message (or direct call)
         │
         ▼
agent_coordinator.route(query)
         │
         ├─ generate correlation_id = uuid4()[:8]
         ├─ log {action:"route_start", correlation_id, query[:80]}
         │
         ▼
ThreadPoolExecutor(max_workers=5)
  ├─ agent_infra.owns(query)    ─┐
  ├─ agent_personal.owns(query)  │  parallel, < 10ms each
  ├─ agent_finance.owns(query)   │
  ├─ agent_research.owns(query)  │
  └─ agent_self_improve.owns(query) ─┘
         │
         ▼
    claimants = [agents that returned True]
         │
    ┌────┴────────────────────┐
    │                         │
  0 claims               1+ claims
    │                         │
    ▼                    ┌────┴────┐
skill_executor          1 claim  2+ claims
.execute_skill()          │        │
    │                  direct   ThreadPoolExecutor
    │                  call()   parallel handle()
    │                  (first-  (30s timeout each)
    │                   wins)
    │                     │       │
    └──────────────────────┴───────┘
                          │
                     aggregate(results)
                          │
                     ┌────┴──────────────────┐
                     │  1 result              │ 2+ results
                     │  pass-through          │ emoji-labeled sections
                     │  (obs.summary)         │ 📡 Infra: ...
                     └────────────────────────┘
                          │
                     log {action:"route_done", correlation_id,
                          agents, strategy, elapsed}
                          │
                          ▼
                     return {query, agents, strategy, correlation_id,
                             responses, aggregated, handled}
```

### Recommended Project Structure

No new directories needed. All changes within `workspace/runtime/`:
```
workspace/runtime/
├── agent_coordinator.py   # REWRITE (Phase 8 primary deliverable)
├── agent_infra.py         # MODIFY — add correlation_id=None to handle()
├── agent_personal.py      # MODIFY — add correlation_id=None to handle()
├── agent_finance.py       # MODIFY — add correlation_id=None to handle()
├── agent_research.py      # MODIFY — add correlation_id=None to handle()
├── agent_self_improve.py  # MODIFY — add correlation_id=None to handle()
└── logs/
    └── agent_coordinator.log  # JSONL with correlation_id field
```

### Pattern 1: Parallel owns() Evaluation

**What:** All 5 `owns()` calls in a single ThreadPoolExecutor.map() or submit() call.

**When to use:** Every incoming query, before any agent handle() call.

**Example:**
```python
# Source: CONTEXT.md D-02 + stdlib concurrent.futures
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

AGENT_REGISTRY = {
    "infra": agent_infra,
    "personal": agent_personal,
    "finance": agent_finance,
    "research": agent_research,
    "self_improve": agent_self_improve,
}

def _get_claimants(query: str) -> list:
    """Run all owns() in parallel. Returns list of agent names that claimed."""
    claimants = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(mod.owns, query): name
            for name, mod in AGENT_REGISTRY.items()
        }
        for future in as_completed(futures, timeout=2):  # owns() is <10ms, 2s is generous
            name = futures[future]
            try:
                if future.result():
                    claimants.append(name)
            except Exception:
                pass  # owns() failure = no claim
    return claimants
```

### Pattern 2: Parallel handle() with Timeout

**What:** All claimant agents called in ThreadPoolExecutor with 30s timeout per agent.

**When to use:** When 2+ agents claim the query (multi-domain path).

**Example:**
```python
# Source: CONTEXT.md D-03 + stdlib concurrent.futures
def _run_parallel(claimants: list, query: str, correlation_id: str) -> dict:
    """Call handle() on all claimants in parallel. Timeout = 30s per agent."""
    results = {}
    with ThreadPoolExecutor(max_workers=len(claimants)) as pool:
        futures = {
            pool.submit(AGENT_REGISTRY[name].handle, query, correlation_id=correlation_id): name
            for name in claimants
        }
        for future in as_completed(futures, timeout=31):  # +1s buffer over agent timeout
            name = futures[future]
            try:
                obs, recs = future.result(timeout=30)
                results[name] = (obs, recs)
            except TimeoutError:
                _log({"action": "agent_timeout", "agent": name, "correlation_id": correlation_id})
            except Exception as e:
                _log({"action": "agent_error", "agent": name, "error": str(e)[:100],
                      "correlation_id": correlation_id})
    return results
```

**Critical note:** `as_completed(futures, timeout=31)` raises `TimeoutError` from the iterator itself after 31s — but individual `future.result(timeout=30)` also raises `TimeoutError` per agent. Use both for defense in depth.

### Pattern 3: Correlation ID Threading

**What:** Generate once at coordinator entry; pass as kwarg to handle(); log at each tier.

**When to use:** Every query.

**Example:**
```python
# Source: CONTEXT.md D-04 constraint + stdlib uuid
import uuid

def route(query: str) -> dict:
    correlation_id = str(uuid.uuid4())[:8]
    _log({"action": "route_start", "correlation_id": correlation_id, "query": query[:80]})

    # ... routing logic ...

    # Agent handle() signature:
    obs, recs = agent.handle(query, correlation_id=correlation_id)

    _log({"action": "route_done", "correlation_id": correlation_id,
          "agents": claimants, "strategy": strategy, "elapsed": elapsed})
    return {..., "correlation_id": correlation_id}
```

**In each agent (e.g., agent_infra.py):**
```python
def handle(query: str, correlation_id: str = None):
    _log({"action": "handle", "query": query[:80], "correlation_id": correlation_id,
          "sub": data.get("sub", "full"), "confidence": obs.confidence})
    return (obs, recs)
```

### Pattern 4: Emoji-Labeled Section Aggregation

**What:** Each agent's `obs.summary` prepended with domain emoji. Newline-separated.

**When to use:** When 2+ agents return results (multi-domain path).

**Example:**
```python
# Source: CONTEXT.md D-04
DOMAIN_EMOJI = {
    "infra": "📡",
    "personal": "👤",
    "finance": "💰",
    "research": "🔍",
    "self_improve": "🔧",
}

def _aggregate(results: dict) -> str:
    """results: {agent_name: (Observation, [Recommendation])}"""
    parts = []
    for name, (obs, recs) in results.items():
        emoji = DOMAIN_EMOJI.get(name, "•")
        label = name.replace("_", " ").title()
        parts.append(f"{emoji} {label}: {obs.summary}")
    return "\n".join(parts)
```

### Pattern 5: Skill Fallback for Zero Claims

**What:** When `_get_claimants(query)` returns `[]`, call `execute_skill()` for deterministic skills.

**When to use:** Zero-claim path only — the coordinator does NOT call `orchestrator.route_query()`.

**Example:**
```python
# Source: CONTEXT.md D-01 + skill_executor.py signature inspection
from skill_executor import execute_skill
from classifier import classify_query

def _skill_fallback(query: str) -> dict:
    category = classify_query(query)
    if category.startswith("skill:"):
        result = execute_skill(category, query)
        return {"handled": True, "output": result["output"], "source": "skill_executor"}
    # Pure LLM chat — return unhandled so caller can forward to LLM
    return {"handled": False, "output": None, "source": "llm_needed"}
```

### Anti-Patterns to Avoid

- **Calling `orchestrator.route_query()` from coordinator:** D-01 explicitly forbids this. Coordinator calls `skill_executor.execute_skill()` directly for fallback, not via orchestrator.
- **Calling `agent_arbitrator.resolve()` for multi-domain:** Arbitrator is for CONFLICT resolution (two agents fighting over the same action on the same resource). In Phase 8, multi-domain = run all claimants in parallel. Arbitrator is not needed here.
- **Passing `query` already-lowercased to `owns()`:** Agent `owns()` implementations call `topic.lower()` themselves. Pass raw query.
- **Blocking `owns()` evaluation on a single agent timeout:** If one agent's `owns()` takes > 2s, don't block all other agents. Use `as_completed()` with timeout per future.
- **Raising an exception from coordinator if one agent fails:** Per FRMK-03, agent isolation is a hard requirement. Any agent exception must be caught, logged with `correlation_id`, and the query continues with remaining agents.
- **Reusing the existing `agent_coordinator.py` `route()` logic:** The current implementation uses `_reg_dispatch()` (registry) and `handle_query()` (backward compat wrapper). Phase 8 replaces this with `owns()` + `handle()` directly.

---

## Key Codebase Findings

### Finding 1: existing agent_coordinator.py is a scaffold, not the final design

The file at `workspace/runtime/agent_coordinator.py` (90 lines) already exists from Phase 5 work. It has:
- `identify_agents(query)` — calls `_load_agent(name).owns(q)` sequentially, not in parallel
- `route(query)` — delegates to `_reg_dispatch()` (registry), falls back to `handle_query()` (backward-compat wrapper)
- `_aggregate(responses: dict)` — plain newline join, no emoji sections
- NO correlation IDs anywhere
- NO ThreadPoolExecutor

**Phase 8 is a complete rewrite of this file's core logic.** The module name, log file paths, and `AGENT_MODULES` dict can be retained.

[VERIFIED: direct file read]

### Finding 2: handle() signature — all 5 agents take `query: str` only

Current signature across all 5 agents:
```python
def handle(query: str):  # in agent_infra, agent_personal, agent_finance, agent_research, agent_self_improve
    """Returns (Observation, list[Recommendation])."""
```

None accept `correlation_id`. Phase 8 must add `correlation_id: str = None` as a keyword argument to all 5. This is backward-compatible (existing callers like `agents_worker.py` which calls `agent.handle("")` will not break).

[VERIFIED: `grep "^def handle(" workspace/runtime/agent_*.py`]

### Finding 3: owns() — all 5 agents already implement it

| Agent | OWNERSHIP set |
|---|---|
| `agent_infra` | `{"container", "containers", "gpu", "ram", "disk", "network", "vpn", "ollama", "docker", "gluetun", "infra", "homelab", "backend_offline"}` |
| `agent_personal` | `{"calendar", "email", "reminder", "smoking", "briefing", "agenda", "morning", "greeting", "habit", "schedule"}` |
| `agent_finance` | `{"finance", "binance", "iol", "crypto", "btc", "portfolio", "market", "trading", "investment", "dolar", "pnl"}` |
| `agent_research` | `{"research", "searxng", "knowledge_base", "knowledge", "rag", "investigate", "documentation"}` |
| `agent_self_improve` | `{"improve", "benchmark", "error_mining", "experiment", "staging", "candidate", "self-evolve", "calibration", "idle_learning"}` |

All use identical pattern: `return any(k in topic.lower() for k in OWNERSHIP)`.

**No overlap between domains** (no keyword appears in two agents' OWNERSHIP sets). Multi-domain detection will only trigger for genuinely multi-topic queries. [VERIFIED: direct file read]

### Finding 4: execute_skill() signature and return shape

```python
# skill_executor.py line 1042
def execute_skill(skill_category: str, query: str = "") -> dict:
```
Returns:
```python
{
    "skill": str,        # "skill:weather" etc
    "success": bool,
    "output": str,       # human-readable, safe to send to Telegram
    "raw": str,          # raw command output
    "destructive": bool,
    "error": str|None,
}
```
The `output` field is what coordinator should surface to users. [VERIFIED: direct file read]

### Finding 5: route_and_execute() return shape (for interface compatibility reference)

The current `orchestrator.route_and_execute()` returns:
```python
{
    "query": str,
    "classification": str,
    "pipeline": dict,
    "model": dict,
    "parameters": dict,
    "context_signals": list,
    "executed": bool,
    "skill_result": dict | None,  # populated when classification starts with "skill:"
    # optionally "router_v2": dict
}
```
Phase 8 coordinator's `route()` does NOT need to match this shape — telegram_router.py wiring is Phase 9 work. The coordinator can return a simpler, cleaner dict.

[VERIFIED: direct file read]

### Finding 6: telegram_router.py currently calls orchestrator directly

`telegram_router.py` imports and calls `classify_query` from `orchestrator`. It does NOT call `route_and_execute()` directly for most queries — it calls skill execution via its own internal logic. The Telegram wiring to the coordinator is explicitly deferred to Phase 9.

**Phase 8 does not touch telegram_router.py.** [VERIFIED: grep result showing only `classify_query` import]

### Finding 7: No import cycles with the proposed coordinator design

Agent modules import: `runtime_utils`, `agent_protocol`, and domain-specific modules (e.g., `homelab_snapshot`, `finance_daily`). They do NOT import `agent_coordinator` or `orchestrator`.

`agent_coordinator.py` imports agents via `importlib.import_module()` (lazy). This is already the pattern in the existing scaffold. Keep using `importlib` for lazy loading to prevent any future cycle. Do NOT import agent modules at the top level of `agent_coordinator.py`.

[VERIFIED: direct file read of all 5 agent modules]

### Finding 8: agents_worker.py calls handle("") — must remain compatible

```python
# workers/agents_worker.py line 36
obs, recs = agent.handle("")  # empty query -> full assessment
```

Adding `correlation_id=None` as a kwarg to `handle()` is backward-compatible. Passing `""` as query when no specific query is available is already established behavior and should be preserved — each agent's `handle()` falls through to `_assess_full()` / `_observe_full()` when no keywords match.

[VERIFIED: direct file read of workers/agents_worker.py]

### Finding 9: Arbitrator `resolve()` is NOT for Phase 8 multi-domain routing

The arbitrator (`agent_arbitrator.resolve()`) takes a `proposals: list` of dicts with `agent`, `action`, `resource` keys and returns a single winner. This is for CONFLICT RESOLUTION — two agents wanting contradictory actions on the same resource.

Phase 8 multi-domain is NOT a conflict — it is collaborative parallel execution where all claimants run and their independent summaries are concatenated. The arbitrator should NOT be called in the `route()` function. Arbitrator can optionally be called if Phase 9 needs single-winner selection, but this is deferred.

[VERIFIED: direct file read of agent_arbitrator.py]

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Parallel execution with timeout | Custom thread pool + join logic | `concurrent.futures.ThreadPoolExecutor` + `as_completed(timeout=N)` | stdlib handles futures, cancellation, exceptions cleanly |
| Unique correlation IDs | Random string, timestamp, counter | `str(uuid.uuid4())[:8]` | Cryptographically random, no collision risk at small scale |
| Skill classification for fallback | Duplicate orchestrator keyword rules | `classifier.classify_query(query)` | Already battle-tested; 11 skill categories supported |
| Agent module loading | Global imports at top of coordinator | `importlib.import_module(name)` | Existing pattern; prevents circular import risk |

---

## Common Pitfalls

### Pitfall 1: ThreadPoolExecutor timeout silently drops futures

**What goes wrong:** `as_completed(futures, timeout=N)` raises `concurrent.futures.TimeoutError` when the iterator times out — but futures that haven't completed yet are NOT automatically cancelled. If the coordinator's function exits, orphaned threads continue running in the background.

**Why it happens:** Python threads cannot be forcibly killed. `ThreadPoolExecutor` does not propagate cancellation.

**How to avoid:** Log timeout, skip the result, and return what you have. The 30s per-agent timeout matches existing `SKILL_TIMEOUT` in `skill_executor.py`. For `owns()` (which is pure keyword matching with no I/O), set a short timeout (2s) since it should complete in microseconds.

**Warning signs:** If agents consistently time out, check if `handle()` is calling external services that are down.

### Pitfall 2: handle() kwarg vs positional argument

**What goes wrong:** Calling `agent.handle(query, correlation_id)` positionally will break because the current signature is `handle(query: str)` — `correlation_id` becomes an unexpected positional argument.

**How to avoid:** Always call as `agent.handle(query, correlation_id=correlation_id)` (keyword). Add as `def handle(query: str, correlation_id: str = None):` in all 5 agents.

### Pitfall 3: owns() called on pre-lowercased query

**What goes wrong:** `owns()` already calls `topic.lower()` internally. If coordinator passes an already-lowercased string, there's no harm, but if coordinator logic uses the original query for handle(), the agent will also lower it internally. No double-lowercasing issue, but keep query casing consistent — pass the original query to both `owns()` and `handle()`.

### Pitfall 4: Aggregated empty results

**What goes wrong:** An agent returns an `Observation` with `summary=""` or `None`. The aggregation produces an emoji label with an empty body.

**How to avoid:** Guard in `_aggregate()`: skip any agent whose `obs.summary` is empty or None. If all results are empty, fall through to skill fallback.

### Pitfall 5: Coordinator log file location

**What goes wrong:** `agent_coordinator.py` currently logs to two places: `BASE_DIR / "logs" / "agent_coordinator.log"` (JSONL) and `STATE_DIR / "agents" / "coordinator_log.json"` (JSON array of route entries). The second file is used for observability but requires a list append with cap (currently at 200).

**How to avoid:** Keep both log destinations. The JSONL file is for operational debugging; the `coordinator_log.json` is the structured history. Both should include `correlation_id`.

---

## Code Examples

### Full coordinator.route() skeleton

```python
# Source: CONTEXT.md decisions + codebase inspection
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout

def route(query: str) -> dict:
    correlation_id = str(uuid.uuid4())[:8]
    _log({"action": "route_start", "correlation_id": correlation_id, "query": query[:80]})
    t0 = now()

    # Step 1: parallel owns() — determine claimants
    claimants = _get_claimants(query)

    # Step 2: select strategy
    if not claimants:
        # Zero claims → skill fallback
        strategy = "skill_fallback"
        result = _do_skill_fallback(query, correlation_id)
        elapsed = (now() - t0).total_seconds()
        _log({"action": "route_done", "correlation_id": correlation_id,
              "strategy": strategy, "agents": [], "elapsed": elapsed})
        return {**result, "correlation_id": correlation_id, "strategy": strategy,
                "query": query, "agents": []}

    elif len(claimants) == 1:
        # One claim → direct call (first-wins)
        strategy = "first_wins"
        results = _run_single(claimants[0], query, correlation_id)
    else:
        # Multiple claims → parallel
        strategy = "parallel"
        results = _run_parallel(claimants, query, correlation_id)

    # Step 3: aggregate
    if len(results) > 1:
        aggregated = _aggregate(results)
    elif results:
        name = list(results.keys())[0]
        obs, _ = results[name]
        aggregated = obs.summary
    else:
        aggregated = None

    elapsed = (now() - t0).total_seconds()
    _log({"action": "route_done", "correlation_id": correlation_id,
          "strategy": strategy, "agents": claimants, "elapsed": elapsed,
          "responded": list(results.keys())})

    return {
        "query": query,
        "correlation_id": correlation_id,
        "strategy": strategy,
        "agents": claimants,
        "responded": list(results.keys()),
        "aggregated": aggregated,
        "handled": bool(aggregated),
    }
```

### Coordinator test skeleton (for test_sprint9.py additions)

```python
# Source: existing test_sprint9.py TestCoordinator pattern
class TestCoordinatorRewrite(unittest.TestCase):

    def test_route_generates_correlation_id(self):
        import agent_coordinator as ac
        with patch.object(ac, "_get_claimants", return_value=[]):
            with patch.object(ac, "_do_skill_fallback",
                              return_value={"handled": False, "output": None}):
                result = ac.route("test query")
        self.assertIn("correlation_id", result)
        self.assertEqual(len(result["correlation_id"]), 8)

    def test_route_single_agent_uses_first_wins(self):
        import agent_coordinator as ac
        with patch.object(ac, "_get_claimants", return_value=["infra"]):
            with patch.object(ac, "_run_single",
                              return_value={"infra": (MagicMock(summary="ok"), [])}):
                result = ac.route("containers down")
        self.assertEqual(result["strategy"], "first_wins")

    def test_route_multi_agent_uses_parallel(self):
        import agent_coordinator as ac
        with patch.object(ac, "_get_claimants", return_value=["infra", "finance"]):
            with patch.object(ac, "_run_parallel", return_value={}):
                result = ac.route("como esta todo el sistema y mis finanzas")
        self.assertEqual(result["strategy"], "parallel")

    def test_aggregate_uses_emoji_labels(self):
        import agent_coordinator as ac
        mock_infra = MagicMock()
        mock_infra.summary = "containers OK"
        mock_finance = MagicMock()
        mock_finance.summary = "IOL: $1.2M"
        result = ac._aggregate({"infra": (mock_infra, []), "finance": (mock_finance, [])})
        self.assertIn("📡", result)
        self.assertIn("💰", result)
        self.assertIn("containers OK", result)
        self.assertIn("IOL: $1.2M", result)

    def test_zero_claims_calls_skill_fallback(self):
        import agent_coordinator as ac
        with patch.object(ac, "_get_claimants", return_value=[]):
            with patch.object(ac, "_do_skill_fallback",
                              return_value={"handled": False, "output": None}) as mock_fb:
                ac.route("completely unrecognized query xyz")
        mock_fb.assert_called_once()

    def test_agent_exception_does_not_crash_coordinator(self):
        import agent_coordinator as ac
        with patch.object(ac, "_get_claimants", return_value=["infra"]):
            with patch.object(ac, "_run_single", side_effect=Exception("agent blew up")):
                result = ac.route("infra query")
        self.assertIn("correlation_id", result)  # coordinator returned something
```

---

## Runtime State Inventory

Not applicable — this is a greenfield addition of a coordinator module and minor modifications to existing agent `handle()` signatures. No stored data, service configs, or OS registrations need updating.

---

## Environment Availability

Step 2.6: SKIPPED — no external dependencies. Phase 8 is code-only changes using Python stdlib.

---

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | unittest (stdlib) |
| Config file | none — direct `python3 test_sprint9.py` |
| Quick run command | `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py TestCoordinatorRewrite` |
| Full suite command | `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py` |

### Phase Requirements → Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| CORD-01 | Coordinator routes query to correct agent(s) | unit | `python3 test_sprint9.py TestCoordinatorRewrite.test_route_single_agent_uses_first_wins` | ✅ (TestCoordinator exists, needs new class) |
| CORD-02 | Multi-domain responses use emoji-labeled sections | unit | `python3 test_sprint9.py TestCoordinatorRewrite.test_aggregate_uses_emoji_labels` | ❌ Wave 0 |
| CORD-03 | Strategy selection: parallel/first-wins/skill-fallback | unit | `python3 test_sprint9.py TestCoordinatorRewrite.test_route_multi_agent_uses_parallel` | ❌ Wave 0 |
| CORD-04 | Correlation ID generated and present in result | unit | `python3 test_sprint9.py TestCoordinatorRewrite.test_route_generates_correlation_id` | ❌ Wave 0 |

### Sampling Rate
- **Per task commit:** `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py TestCoordinatorRewrite 2>&1 | tail -5`
- **Per wave merge:** `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py`
- **Phase gate:** Full test_sprint9.py green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] `tests/test_sprint9.py` — add `TestCoordinatorRewrite` class (9 test methods from skeleton above)
- [ ] No new test file needed — append to existing `test_sprint9.py`

---

## Security Domain

`security_enforcement: true`, `security_asvs_level: 1`.

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | no | Coordinator is internal runtime; no new auth surface |
| V3 Session Management | no | Stateless routing function |
| V4 Access Control | no | No new user-facing endpoints |
| V5 Input Validation | yes | `query[:80]` in logs; `owns()` receives raw user query — keyword matching only, no eval or subprocess |
| V6 Cryptography | no | uuid4() is for tracing only, not security |

### Known Threat Patterns for this stack

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Query injection via `owns()` keyword match | Tampering | `any(k in topic.lower() for k in OWNERSHIP)` — no eval, no subprocess, no file write. Low risk. |
| Correlation ID collision (8-char hex) | — | ~4B combinations; collision probability negligible for tracing at this scale. Not a security concern. |
| Agent exception revealing internals in aggregated response | Information Disclosure | Catch all exceptions in `_run_parallel()`/`_run_single()`; log with correlation_id but never include raw exception in user-facing `aggregated` string |
| Orphaned threads from timed-out agents | DoS (resource exhaustion) | ThreadPoolExecutor context manager ensures cleanup on exit; 30s timeout matches existing SKILL_TIMEOUT |

**No new attack surface introduced.** Coordinator is a pure routing function — it does not introduce new auth endpoints, user data storage, or subprocess execution paths.

---

## Open Questions

1. **Should `_do_skill_fallback()` call `classifier.classify_query()` internally, or should the coordinator just return `handled=False` to let the caller decide?**
   - What we know: `classify_query()` is available in `classifier.py`; `execute_skill()` takes a `skill_category` string.
   - What's unclear: Whether the coordinator should complete the skill execution or just signal the caller to handle it.
   - Recommendation: Since Phase 9 is when telegram_router.py is wired in, the coordinator's skill fallback should attempt `execute_skill()` for `skill:*` categories and return `handled=False` for `chat` category. This matches D-01.

2. **Should the `route()` return dict include the raw `(Observation, [Recommendation])` tuples or just the aggregated string?**
   - What we know: Telegram router in Phase 9 needs to send a string to the user. Recommendations are for action approval, not direct user text.
   - Recommendation: Return `aggregated` (the string for display) plus `responses: {agent: obs.summary}` (for observability). Do not expose raw Recommendation objects in the return dict to keep the interface clean. Phase 9 can add recommendation surfacing if needed.

---

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `_get_claimants()` calling `owns()` on all 5 agents in ~2ms total is fast enough to not add perceptible latency | Architecture Patterns | If `owns()` is slower than expected (e.g., due to module import time on first call), parallel evaluation with warm modules is still fast — use module caching via `_load_agent()` |
| A2 | `concurrent.futures.TimeoutError` (not `concurrent.futures.Future.TimeoutError`) is the correct import for Python 3.8+ | Code Examples | `TimeoutError` is a builtin in Python 3.11+; for Python 3.8/3.9 use `from concurrent.futures import TimeoutError as FutureTimeout` — confirm Python version in container |

**Confirmed via CLAUDE.md:** container runs `node:24-bookworm` base with `python3` system packages. Debian Bookworm ships Python 3.11, where `TimeoutError` is a builtin. The import alias is safe and correct either way.

---

## Sources

### Primary (HIGH confidence)
- `workspace/runtime/agent_coordinator.py` — existing scaffold; complete content read
- `workspace/runtime/agent_infra.py` — handle() signature, owns() OWNERSHIP set, BLOCKED_TARGETS
- `workspace/runtime/agent_personal.py` — handle() signature, OWNERSHIP set
- `workspace/runtime/agent_finance.py` — handle() signature, OWNERSHIP set
- `workspace/runtime/agent_research.py` — handle() signature, OWNERSHIP set
- `workspace/runtime/agent_self_improve.py` — handle() signature, OWNERSHIP set
- `workspace/runtime/agent_protocol.py` — Observation/Recommendation dataclasses
- `workspace/runtime/agent_arbitrator.py` — resolve() signature and semantics
- `workspace/runtime/skill_executor.py` — execute_skill() signature and return shape
- `workspace/runtime/orchestrator.py` — route_and_execute() return shape
- `workspace/runtime/telegram_router.py` — current orchestrator usage (classify_query import only)
- `workspace/runtime/workers/agents_worker.py` — `handle("")` empty-query proactive caching pattern
- `tests/test_sprint9.py` — existing TestCoordinator tests; what's present vs what's missing
- `.planning/phases/08-coordinator-rewrite/08-CONTEXT.md` — all locked decisions

### Secondary (MEDIUM confidence)
- Python stdlib docs (`concurrent.futures`) — [ASSUMED] from training knowledge; stdlib API is stable across Python 3.8+

---

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH — all stdlib, no new deps
- Architecture: HIGH — all patterns derive directly from locked CONTEXT.md decisions + verified codebase
- Pitfalls: HIGH — found by direct inspection of existing coordinator scaffold and ThreadPoolExecutor semantics
- Agent signatures: HIGH — grep-verified across all 5 agent files

**Research date:** 2026-04-29
**Valid until:** 2026-06-01 (stable internal codebase; no external dependencies to go stale)
