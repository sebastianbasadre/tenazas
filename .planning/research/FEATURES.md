# Feature Landscape

**Domain:** Multi-agent system for personal AI assistant (Tenazas)
**Researched:** 2026-04-28

## Current State Assessment

Sprint 9 of the B+A Roadmap created 7 agent modules (`agent_infra.py`, `agent_personal.py`, `agent_finance.py`, `agent_research.py`, `agent_self_improve.py`, `agent_coordinator.py`, `agent_arbitrator.py`) plus 42 tests. However, these modules are **not integrated** into the actual request path -- `telegram_router.py` and `orchestrator.py` have zero references to `agent_coordinator` or `multi_domain`. The agents are thin wrappers (60-80 lines each) with basic `owns()` keyword matching and `handle_query()` that calls existing modules.

The gap: the *scaffolding* exists but the agents don't actually route queries, share context, observe each other's actions, or provide value beyond what the monolithic orchestrator already does.

## Table Stakes

Features users expect from a multi-agent system. Without these, the agents are decorative wrappers around the existing orchestrator.

| Feature | Why Expected | Complexity | Dependencies | Notes |
|---------|--------------|------------|--------------|-------|
| Orchestrator integration | Agents are useless if `telegram_router.py` never calls them. The coordinator must be wired into the main request path as an alternative to monolithic skill routing for multi-domain queries. | Medium | `telegram_router.py`, `orchestrator.py`, `agent_coordinator.py` | Feature flag to enable/disable. Monolithic path stays as fallback. Only activate for queries matching 2+ agent domains. |
| Reliable agent ownership boundaries | Current `owns()` uses flat keyword sets with overlap (e.g., "learning" matches both `research` and `self_improve`). Ambiguous ownership causes silent drops or double-handling. | Medium | All 5 agent modules | Define exclusive keyword sets OR use a scoring approach where coordinator picks the highest-confidence agent. Add conflict detection at classification time, not just execution time. |
| Structured agent response contract | Agents return raw strings today. Coordinator can't tell success from failure, can't merge structured data, can't present partial results gracefully. | Low | All 5 agent modules, `agent_coordinator.py` | Define a `dataclass` or `TypedDict`: `{agent, status, data, text, confidence, latency_ms}`. All agents must return this shape. |
| Aggregation logic for multi-agent responses | Current `_aggregate()` is `"\n\n".join(parts)` -- literal string concatenation. Useless for structured data, duplicate detection, or coherent summaries. | Medium | `agent_coordinator.py`, response_composer pattern from `response_composer.py` | Use the existing `response_composer.py` pattern: structured data in, composed text out. Don't concatenate -- compose. |
| Agent-level error isolation | If `agent_finance` crashes, the entire coordinator response fails today (generic error string). Each agent must fail independently without poisoning the aggregate. | Low | `agent_coordinator.py` | Try/except per agent with timeout. Already partially done but the error handling returns a string that gets mixed into the response. Should return structured error state. |
| Per-agent timeout enforcement | Agents call external APIs (IOL, Binance, Google Calendar, Ollama). A slow agent blocks the entire coordinator response. | Low | `agent_coordinator.py` | Use `concurrent.futures.ThreadPoolExecutor` with per-agent timeout (5s default, 15s for research). Already used by `health_sweep.py` as a pattern. |
| Agent observability / decision trace | Without logging which agents handled a query, what they returned, and how long they took, debugging multi-agent behavior is impossible. | Low | `agent_coordinator.py`, existing `decision_trace.py` module | Log to `logs/agent_coordinator.log` (JSONL): `{query, agents_matched, agents_executed, responses, latency_per_agent, aggregation_method}`. Feed existing Prometheus metrics. |
| Fallback to monolithic path | When agents fail or produce low-confidence results, the system must degrade gracefully to the existing orchestrator skill routing. Users should never see worse results because agents exist. | Low | `telegram_router.py`, `orchestrator.py` | If coordinator returns `handled=False` or all agents error, fall through to `route_and_execute()`. This is the safety net. |

## Differentiators

Features that make the multi-agent system genuinely better than the monolithic orchestrator. Not expected in MVP, but deliver real value.

| Feature | Value Proposition | Complexity | Dependencies | Notes |
|---------|-------------------|------------|--------------|-------|
| Cross-agent context sharing | When `agent_infra` detects Ollama offline and `agent_personal` gets a calendar query that needs LLM summarization, the personal agent should know the LLM is degraded and adjust its response (e.g., return raw data instead of summarized). | High | `shared_memory.py` (working memory type), all agents | Use existing `shared_memory.py` working memory. Each agent writes its assessment status; others read before acting. The "world model" pattern already exists in `world_model.py`. |
| Proactive agent coordination | Instead of only responding to queries, agents should proactively detect situations. E.g., `agent_infra` detects disk at 90% + `agent_finance` sees backup job failed = coordinated alert to Sebas with full context. | High | `runtime.py` event loop, all agents, `agent_coordinator.py` | Wire into the 5-min runtime loop. Each agent runs `assess()` (already implemented), coordinator checks for cross-domain correlations. |
| Agent memory with learning | Each agent remembers past interactions and improves. `agent_finance` learns Sebas always asks about BTC on Monday mornings. `agent_personal` learns meeting prep is needed 15 min before calendar events. | High | `operational_memory.py` per agent, `preference_learning.py` | `operational_memory.py` already supports domain-keyed facts. Extend to per-agent domains. `preference_learning.py` from Sprint 4 already tracks approval patterns. |
| Parallel agent execution | For multi-domain queries ("como esta todo"), run all relevant agents in parallel instead of sequentially. Reduces response time from sum-of-latencies to max-of-latencies. | Medium | `agent_coordinator.py` | `ThreadPoolExecutor` with agent-specific timeouts. Already proven pattern in `health_sweep.py` (25 parallel probes). |
| Agent-specific model routing | Different agents benefit from different LLM models. `agent_research` needs reasoning (deepseek-r1), `agent_infra` needs fast JSON parsing (qwen2.5:7b), `agent_self_improve` needs deep analysis (qwen2.5:32b). | Medium | `model_router.py` V2 role system | Map agents to roles: infra=fast, personal=fast, finance=fast, research=reason, self_improve=heavy. Use existing `route_by_role()`. |
| Coordinator as Telegram-visible layer | Surface which agents handled a query in the Telegram response. "Infra: 20 containers up. Finance: BTC +2.3%. Personal: 2 meetings today." gives Sebas transparency into who answered what. | Low | `telegram_router.py`, `agent_coordinator.py` | Format each agent's response with a header. Users appreciate knowing the system's reasoning structure. |
| Conflict resolution with escalation | When agents disagree (e.g., infra wants to restart a container but personal says Sebas is in a meeting), the arbitrator resolves or escalates to Telegram approval. | Medium | `agent_arbitrator.py`, `exec_approvals.py` | `agent_arbitrator.py` already has priority logic and postpone mechanics. Wire it into the proposal execution path. |
| Agent performance dashboard | Per-agent metrics in Grafana: response latency, error rate, queries handled, ownership accuracy. | Low | `metrics_server.py`, Grafana | Prometheus gauges: `tenazas_agent_{name}_queries_total`, `tenazas_agent_{name}_errors_total`, `tenazas_agent_{name}_latency_seconds`. One Grafana dashboard. |

## Anti-Features

Features to explicitly NOT build. These are common multi-agent mistakes that would hurt Tenazas.

| Anti-Feature | Why Avoid | What to Do Instead |
|--------------|-----------|-------------------|
| Agent-to-agent direct communication | Agents calling each other creates a spaghetti graph. With 5 agents, that's 20 possible communication paths. Debugging becomes impossible. The "Bag of Agents" anti-pattern amplifies errors 17x per research. | All communication goes through the coordinator. Agents read shared state but never call each other's `handle_query()`. |
| LLM-based routing to agents | Using an LLM to decide which agent handles a query adds 2-5s latency and costs tokens for every message. The orchestrator already has deterministic classification that works. | Keep keyword/regex classification for agent ownership. Only use LLM for ambiguous queries that no agent claims. |
| Per-agent LLM personalities | Each agent having its own system prompt and LLM conversation creates context explosion and inconsistent responses. Tokens multiply by agent count. | Agents return structured data. A single response composer (existing `response_composer.py`) formats the final answer. Only one LLM call if humanization is needed. |
| Autonomous agent actions without approval | Agents should NEVER auto-execute destructive actions (restarts, trades, emails). The existing approval flow is critical safety infrastructure. | All proposals go through `exec_approvals.py` Telegram buttons. Agents propose, Sebas approves. Same as today. |
| Complex negotiation protocols | Academic multi-agent papers describe voting, auction, and consensus mechanisms. Overkill for 5 agents on a personal NAS with one user. | Simple priority ordering (already in `agent_arbitrator.py`) + escalation to human. No voting needed. |
| Agent spawning / dynamic agent creation | Some frameworks allow agents to create sub-agents dynamically. This is uncontrollable complexity for a personal assistant. | Fixed set of 5 agents. New capabilities go into existing agents or are added as new fixed agents via code changes. |
| Shared mutable state between agents | Agents writing to each other's state files creates race conditions and invisible coupling. The existing codebase already has file contention issues. | Read-only shared context (world model, shared memory). Each agent writes only to its own state directory (`state/agents/{name}/`). Coordinator aggregates. |

## Feature Dependencies

```
Structured agent response contract
  -> Aggregation logic (needs structured data to aggregate)
  -> Agent-level error isolation (needs structured error state)
  -> Coordinator as Telegram-visible layer (needs per-agent responses)

Orchestrator integration
  -> Fallback to monolithic path (needs integration point to fall back from)
  -> Per-agent timeout enforcement (only matters when agents are in request path)

Agent observability
  -> Agent performance dashboard (needs metrics to display)

Cross-agent context sharing
  -> Proactive agent coordination (needs shared state to correlate)

Reliable ownership boundaries
  -> Agent-specific model routing (routing depends on knowing which agent handles)
```

## MVP Recommendation

Prioritize (Phase 1 -- make agents actually work):
1. **Structured agent response contract** -- Foundation for everything else. Without this, no reliable aggregation, error handling, or observability. Low complexity, high leverage.
2. **Orchestrator integration** -- Wire coordinator into telegram_router.py behind a feature flag. Without this, agents are dead code.
3. **Agent-level error isolation + timeout** -- Safety net. Without these, one slow API call breaks the whole system.
4. **Reliable ownership boundaries** -- Fix the keyword overlap problem. Use scoring instead of flat sets.
5. **Fallback to monolithic path** -- Safety guarantee that agents can never make things worse.

Prioritize (Phase 2 -- make agents valuable):
6. **Aggregation logic** -- Replace string concatenation with structured composition.
7. **Agent observability** -- Log everything for debugging and learning.
8. **Parallel agent execution** -- Performance improvement for multi-domain queries.
9. **Agent-specific model routing** -- Each agent uses the optimal LLM.

Defer:
- **Cross-agent context sharing**: High complexity, needs the basics working first. Also depends on shared_memory.py being reliable under concurrent access (the refactor milestone addresses this).
- **Proactive agent coordination**: Requires cross-agent context + runtime loop integration. Phase 3 at earliest.
- **Agent memory with learning**: Nice to have but not blocking. The system works without per-agent learning.
- **Conflict resolution with escalation**: Edge case until agents actually propose conflicting actions simultaneously, which requires proactive coordination.

## Sources

- [Azure AI Agent Design Patterns](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns)
- [Multi-Agent Coordination Patterns (Tacnode)](https://tacnode.io/post/multi-agent-architecture)
- [Choosing the Right Multi-Agent Architecture (LangChain)](https://blog.langchain.com/choosing-the-right-multi-agent-architecture/)
- [Why Your Multi-Agent System is Failing: The 17x Error Trap (TDS)](https://towardsdatascience.com/why-your-multi-agent-system-is-failing-escaping-the-17x-error-trap-of-the-bag-of-agents/)
- [Anti-Pattern: Multi-Agent Overkill](https://www.agentpatterns.tech/en/anti-patterns/multi-agent-overkill)
- [Multi-Agent Workflows That Don't Fail (GitHub Blog)](https://github.blog/ai-and-ml/generative-ai/multi-agent-workflows-often-fail-heres-how-to-engineer-ones-that-dont/)
- [Agent Observability Best Practices (Microsoft Azure)](https://azure.microsoft.com/en-us/blog/agent-factory-top-5-agent-observability-best-practices-for-reliable-ai/)
- [Agent Anti-Patterns: Architectural Pitfalls (Medium)](https://achan2013.medium.com/ai-agent-anti-patterns-part-1-architectural-pitfalls-that-break-enterprise-agents-before-they-32d211dded43)
- [Simon Willison: Agentic Engineering Anti-Patterns](https://simonwillison.net/guides/agentic-engineering-patterns/anti-patterns/)
