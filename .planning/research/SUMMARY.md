# Project Research Summary

**Project:** Tenazas v2.0 Multi-Agent System
**Domain:** Multi-agent coordination layer for existing Python AI assistant
**Researched:** 2026-04-28
**Confidence:** HIGH

## Executive Summary

Tenazas already has 7 Sprint 9 agent modules (5 domain agents + coordinator + arbitrator) with 42 tests, but they are dead code -- `telegram_router.py` and `orchestrator.py` never call them. The existing monolithic orchestrator handles 25+ skills deterministically with 900+ tests. The multi-agent system should be built as an **advisory layer on top of the orchestrator**, not as a replacement. Agents observe queries, recommend actions, and enrich LLM context. Execution stays with the proven orchestrator + skill engine. All agent actions require Telegram approval.

The recommended approach uses zero new dependencies (stdlib only: `dataclasses`, `concurrent.futures`, `fcntl`, `enum`). The key new modules are `agent_protocol.py` (typed interfaces), `agent_state.py` (file-locked shared state), and a rewritten `agent_coordinator.py` that plugs into `telegram_router.py` via two try/except-wrapped hooks. The coordinator activates only for multi-domain queries; single-domain queries bypass agents entirely. ThreadPoolExecutor provides parallel agent execution (proven pattern from `health_sweep.py`).

The top risks are: routing regression (agents breaking what the orchestrator already handles well), JSON state file corruption under concurrent access, and response noise (5 agents producing a wall of text instead of a 3-line summary). All three are mitigated by specific architectural decisions: agents are additive-only behind a feature flag, atomic writes with `fcntl` locks before parallel execution, and response budgets enforced at the coordinator level. The system should start with 3 agents (infra, personal, finance) and add more only when justified by activation rates.
## Key Findings

### Recommended Stack

No new external dependencies. The entire multi-agent system is built on Python stdlib plus existing runtime utilities (`write_atomic`, `load_json` from `runtime_utils.py`). ThreadPoolExecutor replaces the need for asyncio or message queues. File-based coordination with `fcntl` advisory locks replaces the need for Redis or SQLite.

**Core new modules:**
- `agent_protocol.py`: Typed interfaces (`Observation`, `Recommendation` dataclasses) -- eliminates inconsistent dict returns across agents
- `agent_state.py`: File-locked shared state management -- provides atomic read-modify-write for cross-agent coordination
- `coordination.py` (or inline in coordinator): ThreadPoolExecutor-based parallel agent dispatch with per-agent timeouts
- `conflict_resolver.py` (or inline in coordinator): Weighted priority resolution, can start as a simple priority dict

**What NOT to add:** CrewAI/LangGraph (massive dependency for 5 agents), asyncio rewrite (42+ sync modules), Redis/SQLite for state (overkill for 5 agents), message queues (same machine), Pydantic (not in existing deps).

### Expected Features

**Must have (table stakes):**
- Orchestrator integration via feature flag (agents are dead code without it)
- Structured agent response contract (dataclass, not raw strings)
- Agent-level error isolation with per-agent timeouts (5s default, 15s research)
- Reliable ownership boundaries (exclusive keywords, no overlap)
- Fallback to monolithic path (agents can never make things worse)
- Agent observability with JSONL logging and correlation IDs

**Should have (differentiators):**
- Parallel agent execution for multi-domain queries (ThreadPoolExecutor)
- Aggregation logic using response_composer pattern (not string concatenation)
- Agent-specific model routing (infra=fast, research=reason, self_improve=heavy)
- Coordinator as Telegram-visible layer (per-agent headers in responses)
- Prometheus metrics per agent (queries, errors, latency)

**Defer (v2+):**
- Cross-agent context sharing (needs basics working first)
- Proactive agent coordination (needs cross-agent context + runtime loop integration)
- Agent memory with learning (nice-to-have, not blocking)
- Complex conflict resolution with escalation (edge case until proactive coordination exists)

**Anti-features (never build):**
- Agent-to-agent direct communication (spaghetti graph, 20 possible paths for 5 agents)
- LLM-based routing to agents (2-5s latency for something regex handles in 2ms)
- Per-agent LLM personalities (context explosion, inconsistent responses)
- Autonomous agent actions without approval (breaks existing safety infrastructure)
- Agent spawning / dynamic creation (uncontrollable complexity)

### Architecture Approach

The coordinator sits between `telegram_router.py` and `orchestrator.py` as an advisory layer. Two hooks in telegram_router: (1) post-skill notification to agents (fire-and-forget), (2) pre-LLM observation for non-skill queries (with timeout + try/except). The orchestrator, classifier, skill executor, model router, and all safety modules remain **completely unchanged**. Agent `assess()` runs in the 5-minute cron loop (background); Telegram queries read cached results (foreground). This separation is the architecture biggest strength.

**Major components:**
1. `agent_protocol.py` -- Typed interfaces: `Observation`, `Recommendation` dataclasses defining the contract
2. `agent_state.py` -- Shared JSON state with `fcntl` file locking for cross-process safety
3. `agent_coordinator.py` (rewrite) -- Route queries to relevant agents, dispatch observe/assess, aggregate results, delegate conflicts to arbitrator
4. 5 domain agents (modified) -- Each gains an `observe()` method returning `Observation | None`
5. `agent_arbitrator.py` (minor update) -- Updated to work with `Recommendation` dataclass instead of raw dicts

### Critical Pitfalls

1. **Routing regression** -- Agents misroute queries the orchestrator handles correctly. Prevent: agents activate ONLY for multi-domain queries via `is_multi_domain()` check. Single-domain queries bypass agents entirely. Add regression test suite with top 100 real queries from logs.

2. **JSON state file corruption** -- Non-atomic `_save()` in all agent modules causes truncated JSON under concurrent access. Prevent: fix ALL `_save()` to atomic writes (`os.replace()` pattern) before enabling parallel execution. This is a Phase 1 prerequisite.

3. **Response noise (17x error trap)** -- 5 agents produce 500+ words when user wants 3 lines. Prevent: 100-char response budget per agent, priority-based inclusion (omit all-clear agents), single synthesizer pass. Study `daily_brief.py` pattern.

4. **Over-engineering for solo maintainer** -- 7 new modules with 7 log files and 7 state files is too much for one person. Prevent: start with 3 agents (infra, personal, finance). Merge research + self_improve. Inline arbitrator into coordinator.

5. **Assess latency kills Telegram response time** -- Live probes during Telegram queries add 5-15s. Prevent: cache-first architecture. Agents write status in background cron loop, coordinator reads cached files during Telegram queries. Hard 2s timeout per agent.

## Implications for Roadmap

### Phase 1: Foundation and Contracts
**Rationale:** Everything depends on typed interfaces and safe state management. Without these, parallel execution corrupts data and agents return inconsistent structures.
**Delivers:** `agent_protocol.py` with Observation/Recommendation dataclasses, `agent_state.py` with file-locked state management, atomic `_save()` fix across all agent modules, exclusive ownership registry, regression test baseline from production logs.
**Addresses:** Structured response contract, reliable ownership boundaries, error isolation foundation.
**Avoids:** JSON state corruption (Pitfall 2), ownership overlap (Pitfall 6), circular imports (Pitfall 8).

### Phase 2: Agent Upgrade (Backward Compatible)
**Rationale:** Agents need the `observe()` method before the coordinator can call them. This phase modifies existing agents without wiring them in -- zero behavioral change.
**Delivers:** `observe()` method on each agent returning typed `Observation`, cached `assess()` results (60-120s TTL), response budget enforcement (100 chars), unit tests per agent with mocked dependencies.
**Addresses:** Agent-level error isolation, per-agent timeout, cache-first architecture.
**Avoids:** Assess latency (Pitfall 5), response noise (Pitfall 3), over-engineering (Pitfall 4 -- start with 3 agents only: infra, personal, finance).

### Phase 3: Coordinator Rewrite
**Rationale:** The coordinator is the central hub. It must be rewritten with the new typed interfaces before wiring into Telegram. Can be built and tested in isolation.
**Delivers:** Rewritten `agent_coordinator.py` with `route_with_context()`, `notify_agents()`, `periodic_assess()`. Parallel execution via ThreadPoolExecutor. Conflict delegation to arbitrator. Backward-compatible `route()` preserved for existing tests.
**Addresses:** Aggregation logic, parallel execution, coordination strategies.
**Avoids:** Routing regression (Pitfall 1 -- only activates for multi-domain queries), blocking calls (Pitfall 5 -- hard timeouts).

### Phase 4: Pipeline Integration (Behavioral Change)
**Rationale:** This is the only phase that changes user-visible behavior. Must come after foundation, agents, and coordinator are all tested.
**Delivers:** Two hooks in `telegram_router.py` (post-skill notify, pre-LLM observe), worker integration for periodic assessment, feature flag `USE_AGENT_COORDINATOR`, integration tests, end-to-end message flow.
**Addresses:** Orchestrator integration, fallback to monolithic path, Telegram-visible agent layer.
**Avoids:** Routing regression (feature flag, regression tests), breaking Telegram response SLA (try/except wrapping, cached state reads).

### Phase 5: Observability and Hardening
**Rationale:** Production monitoring and polish after the system is live.
**Delivers:** Prometheus metrics per agent, Grafana dashboard, JSONL logging with correlation IDs, response length monitoring, feature flag cleanup, one-at-a-time agent rollout (infra first, then personal, then finance).
**Addresses:** Agent observability, performance dashboard, feature flag debt.
**Avoids:** Log proliferation (Pitfall 9 -- correlation IDs), testing complexity (Pitfall 7 -- contract + snapshot tests).

### Phase Ordering Rationale

- **Foundation first** because atomic writes and typed contracts are prerequisites for every subsequent phase. Parallel execution without atomic writes corrupts state (Pitfall 2).
- **Agent upgrade before coordinator** because the coordinator calls `observe()` on agents -- agents must have this method first. Backward-compatible: system works exactly as before.
- **Coordinator before integration** because the coordinator must be fully tested in isolation before being wired into the production Telegram path. Testing in isolation is cheap; debugging in production is expensive.
- **Integration as a distinct phase** because this is the only phase with user-visible impact. Isolating it makes rollback trivial (disable feature flag).
- **Observability last** because metrics and dashboards are only useful once the system is live and generating data.

### Research Flags

Phases likely needing deeper research during planning:
- **Phase 3 (Coordinator Rewrite):** Strategy selection heuristic (sequential vs parallel vs pipeline) needs tuning with real query patterns. Research actual multi-domain query frequency from `telegram_router.log`.
- **Phase 4 (Pipeline Integration):** Telegram response time impact needs benchmarking. The 2-hook integration pattern is simple but latency at the 95th percentile needs measurement.

Phases with standard patterns (skip research-phase):
- **Phase 1 (Foundation):** Dataclass contracts, fcntl file locking, atomic writes -- all well-documented stdlib patterns with existing codebase precedent.
- **Phase 2 (Agent Upgrade):** Adding methods to existing modules with mocked tests -- standard refactoring.
- **Phase 5 (Observability):** Prometheus + Grafana -- identical pattern to existing dashboards (tenazas-runtime, tenazas-agent, tenazas-roi).

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All recommendations use Python stdlib. No new dependencies. Patterns proven in existing codebase (ThreadPoolExecutor in health_sweep.py, fcntl via runtime lockfile, dataclasses in policy_engine.py). |
| Features | HIGH | Feature landscape derived from direct codebase analysis + 8 external sources on multi-agent patterns. Anti-features backed by specific failure research (17x error trap, MAST taxonomy). |
| Architecture | HIGH | Architecture directly analyzed from existing code. Integration points identified by reading actual module source. Sprint 9 modules examined line by line. |
| Pitfalls | HIGH | Pitfalls grounded in codebase-specific analysis (actual _save() implementation, actual import chains, actual cron structure) plus production failure research (MAST: 42% specification, 37% coordination failures). |

**Overall confidence:** HIGH

### Gaps to Address

- **Multi-domain query frequency:** Unknown what percentage of actual Telegram queries would trigger the coordinator. Need to analyze `telegram_router.log` to validate that multi-agent routing serves a meaningful portion of traffic. If <5%, the system may not justify its complexity.
- **Agent activation rates:** No data on how often each agent `owns()` would fire on real traffic. Risk of building 5 agents when 2-3 cover 95% of use cases. Mitigation: start with 3, measure, expand.
- **Response time budget:** Unknown actual latency of cached `assess()` reads + coordinator aggregation. Assumed <200ms but needs benchmarking. If >500ms, the pre-LLM hook degrades Telegram UX.
- **Model router guardrail interaction:** Five agents each requesting LLM calls could exhaust the global heavy model guardrail (max 3 heavy/10min). Agents must default to `fast` role. Needs explicit testing.
- **Worker supervisor concurrency:** Telegram process and worker process both accessing agent state files is the primary contention vector. The atomic write fix (Phase 1) should handle this, but needs stress testing.

## Sources

### Primary (HIGH confidence)
- Direct codebase analysis of all 7 Sprint 9 agent modules, orchestrator.py, telegram_router.py, worker_supervisor.py, classifier.py, skill_executor.py
- Existing test suite analysis: test_sprint9.py (42 tests), test_orchestrator.py (65 tests)
- Existing patterns: health_sweep.py (ThreadPoolExecutor), runtime.py (lockfile), policy_engine.py (dataclasses)

### Secondary (MEDIUM confidence)
- [MAST taxonomy (Augment Code)](https://www.augmentcode.com/guides/why-multi-agent-llm-systems-fail-and-how-to-fix-them) -- 42% specification, 37% coordination, 21% verification failures
- [17x Error Trap (Towards Data Science)](https://towardsdatascience.com/why-your-multi-agent-system-is-failing-escaping-the-17x-error-trap-of-the-bag-of-agents/) -- Error amplification in uncoordinated agents
- [Azure AI Agent Design Patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) -- Registry + Router + State Store + Supervisor pattern
- [LangChain Multi-Agent Architecture Guide](https://blog.langchain.com/choosing-the-right-multi-agent-architecture/) -- Sequential/hierarchical patterns
- [GitHub Blog: Multi-Agent Workflows](https://github.blog/ai-and-ml/generative-ai/multi-agent-workflows-often-fail-heres-how-to-engineer-ones-that-dont/) -- Engineering reliability

### Tertiary (LOW confidence)
- [Weighted voting for multi-agent AI](https://medium.com/@edoardo.schepis/patterns-for-democratic-multi-agent-ai-voting-based-council-part-2-implementation-2992c3e7c2be) -- Weighted voting pattern (not recommended for 5-agent system, referenced for completeness)
- [Agent Anti-Patterns (Medium)](https://achan2013.medium.com/ai-agent-anti-patterns-part-1-architectural-pitfalls-that-break-enterprise-agents-before-they-32d211dded43) -- Enterprise-focused, partially applicable

---
*Research completed: 2026-04-28*
*Ready for roadmap: yes*
