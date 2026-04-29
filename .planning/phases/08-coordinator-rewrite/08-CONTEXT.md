---
phase: 08-coordinator-rewrite
created: 2026-04-29
status: complete
---

# Phase 8 Context: Coordinator Rewrite

## Phase Goal

A new coordinator routes every query to the right agent(s) and aggregates multi-domain responses, replacing the orchestrator as the routing brain.

## Gray Areas Resolved

### D-01: Orchestrator Replacement Strategy

**Decision:** Coordinator-first, skill fallback

Every query enters `agent_coordinator.py` first. The coordinator calls `owns(query)` on all 5 agents. If 1+ agent claims ownership, it routes through the agent pipeline. If 0 agents claim, it falls back to `skill_executor.execute_skill()` for deterministic skills (weather, calendar, etc.), then to LLM chat.

**Rationale:** Clean replacement with no parallel path confusion. The coordinator is always the entry point — orchestrator.py becomes legacy.

**Constraints:**
- The existing orchestrator.py facade remains intact (no deletion) for backward compatibility in Phase 9 feature flag
- `skill_executor.py` is called directly when no agent claims — coordinator does NOT call orchestrator.route_query()

---

### D-02: Multi-Domain Query Detection

**Decision:** All agents evaluate ownership via `owns(query)` — 2+ claims = multi-domain

Every query is evaluated by all 5 agents' `owns()` method in parallel. The coordinator collects all claimants. If 2+ agents return True, the query is classified as multi-domain and all claimants are routed in parallel.

**Rationale:** Agents already have query classification logic (keyword matching). Exposing it as `owns()` is minimal surface area — no new LLM call needed.

**Implementation note:**
- `owns(query: str) -> bool` is a lightweight method added to each agent (pure keyword matching, no I/O)
- All 5 `owns()` calls run in parallel via ThreadPoolExecutor
- Threshold: any `True` from `owns()` counts as a claim

---

### D-03: Execution Strategy

**Decision:** Always parallel for multi-domain; first-wins for single-domain; skill fallback for zero claims

| Claimants | Strategy | Mechanism |
|---|---|---|
| 2+ agents | Parallel | ThreadPoolExecutor, collect all results |
| 1 agent | First-wins | Direct call, no parallelism overhead |
| 0 agents | Skill fallback | `skill_executor.execute_skill()` → LLM chat |

**Rationale:** Parallel is always correct for multi-domain (agents are independent). First-wins for single-domain avoids unnecessary threading. Sequential execution was rejected — no dependency between agents in current design.

**Timeout:** Each agent call in parallel has a 30s timeout (same as existing skill timeout). If an agent times out, its result is omitted from aggregation (not a fatal error).

---

### D-04: Response Aggregation

**Decision:** Structured sections per domain — deterministic, no LLM

Multi-domain responses use labeled sections per agent:
```
📡 Infra: containers OK, GPU 45°C, VPN activo
💰 Finanzas: IOL balance $1.2M, variación +2.3%
```

Single-domain and skill fallback responses pass through unchanged.

**Rationale:** Deterministic aggregation (no LLM overhead). User (Sebas) prefers factual answers over natural prose for system status. Emojis per domain chosen for visual scannability in Telegram.

**Domain emoji map:**
- infra → 📡
- personal → 👤
- finance → 💰
- research → 🔍
- self_improve → 🔧

**Separator:** newline between sections, no markdown headers (Telegram plain text friendly).

---

## Implementation Constraints

1. **`owns(query: str) -> bool`** — new method added to all 5 agent modules. Pure keyword matching, no I/O, < 10ms.
2. **`agent_coordinator.py`** — new module in `workspace/runtime/`. Replaces orchestrator as routing brain.
3. **ThreadPoolExecutor** — stdlib only, no new deps. Max workers = 5.
4. **Correlation ID** — `uuid4()[:8]` generated per query, passed to all agent `handle()` calls as kwarg, logged at coordinator entry and agent execution.
5. **Fallback chain:** coordinator → skill_executor → LLM chat. No direct orchestrator.route_query() call.
6. **Telegram router wiring:** `telegram_router.py` will call `agent_coordinator.route(query)` instead of `orchestrator.route_and_execute(query)` — this is Phase 9 work, not Phase 8.

## Deferred Ideas

- Sequential execution strategy (agents in dependency order) — no cross-agent dependencies currently, defer
- LLM-based response synthesis for multi-domain — adds latency, defer to Phase 9 if needed
- Dynamic `owns()` threshold tuning — static keyword matching is sufficient for now
- Coordinator learning which agent handles which query best — defer to post-v2.0

## Files to Create/Modify

| File | Action | Purpose |
|---|---|---|
| `workspace/runtime/agent_coordinator.py` | **Create** | Main routing brain |
| `workspace/runtime/agent_infra.py` | **Modify** | Add `owns(query)` method |
| `workspace/runtime/agent_personal.py` | **Modify** | Add `owns(query)` method |
| `workspace/runtime/agent_finance.py` | **Modify** | Add `owns(query)` method |
| `workspace/runtime/agent_research.py` | **Modify** | Add `owns(query)` method |
| `workspace/runtime/agent_self_improve.py` | **Modify** | Add `owns(query)` method |
| `tests/test_sprint9.py` | **Modify** | Add coordinator test classes |
