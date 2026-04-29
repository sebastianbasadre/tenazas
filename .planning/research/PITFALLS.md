# Domain Pitfalls: Adding Multi-Agent System to Tenazas

**Domain:** Multi-agent coordination layer for existing Python AI assistant
**Researched:** 2026-04-28
**Overall confidence:** HIGH (based on codebase analysis + production multi-agent failure research)

## Critical Pitfalls

Mistakes that cause rewrites, broken routing, or production outages.

### Pitfall 1: Routing Regression — Agents Break What the Orchestrator Already Does Well

**What goes wrong:** The existing `classifier.py` + `skill_executor.py` + `telegram_router.py` chain handles 25+ skills deterministically with proven accuracy (tested across 900+ unit tests). Inserting an agent coordinator into this path introduces a new decision layer that can misroute queries the current system handles correctly. A "what's the weather" query that today hits `skill:weather` in 2ms via regex could get routed to `agent_personal` which calls `assess()` which calls `check_calendar()` before eventually falling back to the weather skill.

**Why it happens:** The coordinator tries to be "smart" about routing instead of deferring to the existing proven classifier. The existing `orchestrator.route_and_execute()` is battle-tested; new agent routing is not.

**Consequences:** Slower responses, incorrect skill dispatch, test regressions across 30+ test suites. Telegram users (Sebas) notice immediately because weather/lights/email answers degrade.

**Prevention:**
- Agents MUST be additive, not replacement. The coordinator should only activate for multi-domain queries (the existing `is_multi_domain()` check is correct). Single-domain queries go through `orchestrator.route_and_execute()` unchanged.
- Add a regression test suite: take the top 100 queries from `telegram_router.log` and verify they route identically before/after agent integration.
- Feature flag: `USE_AGENT_COORDINATOR = False` by default. Enable per-query-type, not globally.
- Monitor: `routing_regression_total` counter in Prometheus. If routing changes >5% of traffic, alert.

**Detection:** Watch `orchestrator.log` for `route_changed: true` entries. Compare response latency histograms before/after.

**Phase:** Phase 1 (Foundation) must establish the bypass rule and regression test before any agent code runs in production.

---

### Pitfall 2: JSON State File Contention — Agents Clobber Each Other's State

**What goes wrong:** Every existing agent module (`agent_infra.py`, `agent_finance.py`, etc.) uses the same pattern: `_save(p, data)` which does `open(p, "w")` followed by `json.dump()`. No locking, no atomic writes. When the worker_supervisor runs agents_worker alongside learning_worker, two processes can write to the same state file simultaneously. Result: truncated JSON, empty files, or lost updates.

**Why it happens:** The current system gets away with it because agents rarely run concurrently (sequential workers). But the multi-agent system design implies agents assessing state in parallel (coordinator calls multiple agents, each writes status). The `_save()` function does a non-atomic write: truncates file on open, then writes. A crash or concurrent write between truncation and completion yields corrupt JSON.

**Consequences:** `json.JSONDecodeError` on next read. Agent loses its memory. Silent data loss (the `_load()` fallback returns empty dict, hiding the corruption).

**Prevention:**
- Atomic writes: write to temp file, then `os.replace()` (atomic on POSIX/Linux, which the container uses). This is a 3-line fix to every `_save()`:
  ```python
  def _save(p, data):
      p.parent.mkdir(parents=True, exist_ok=True)
      tmp = p.with_suffix('.tmp')
      with open(tmp, 'w') as f:
          json.dump(data, f, indent=2, default=str)
      os.replace(str(tmp), str(p))
  ```
- One state file per agent, NEVER shared. Each agent writes only to its own `agents/{name}_status.json`. The coordinator reads all, writes only to `agents/coordinator_log.json`.
- If agents must share data: reader-only pattern. Agent A writes its file, Agent B reads it. Never two writers to one file.

**Detection:** Add a `_validate_json(path)` check after writes in debug mode. Log `state_corruption` events.

**Phase:** Phase 1 (Foundation) must fix `_save()` to atomic writes across ALL agent modules before enabling parallel execution.

---

### Pitfall 3: The "Too Many Cooks" Problem — 5 Agents Produce Noise, Not Signal

**What goes wrong:** For a query like "como esta todo" (overview), the coordinator fans out to 5 agents. Each returns a status dict. The coordinator concatenates them into a wall of text: infra status + finance status + personal status + research status + self-improve status. The response is 500+ words when Sebas wants a 3-line summary.

**Why it happens:** Each agent optimizes for completeness within its domain. No agent knows what the others said. The coordinator does string concatenation, not synthesis. Research shows this is the "17x error trap" -- uncoordinated agents amplify noise rather than reducing it (Moran, 2025).

**Consequences:** Unusable Telegram responses (Telegram has a 4096 char limit). User ignores the system because responses are too long. Response time increases linearly with agent count (5x the LLM calls, 5x the API latency).

**Prevention:**
- Response budget: Each agent gets max 100 chars for its summary. Coordinator enforces truncation.
- Priority-based inclusion: Only include agents with actionable findings. "All clear" agents are omitted (a single "todo bien" suffices).
- Single synthesizer: After collecting agent outputs, pass them through ONE summarization step (use `qwen2.5:7b` with "summarize these 5 status reports in 3 lines for a busy user").
- The existing `daily_brief.py` already does this well -- study its pattern of section-by-section degradation and reuse it.

**Detection:** Monitor response length distribution. If average Telegram response exceeds 500 chars for overview queries, the synthesis is failing.

**Phase:** Phase 2 (Agent Implementation) should include response budgets from day one. Do not defer to "polish" phase.

---

### Pitfall 4: Over-Engineering Agent Boundaries for a Single-Maintainer Project

**What goes wrong:** The design splits into 5 domain agents + coordinator + arbitrator (7 new modules). Each agent has its own memory file, log file, status file, and assess/handle_query interface. For a project maintained by one person (Sebas), this creates 7 modules that must be kept in sync, 7 log files to monitor, and 7 state files to debug. When something breaks at 3AM, you're reading 7 log files instead of 1.

**Why it happens:** Multi-agent patterns are designed for teams with dedicated ownership per agent. Solo maintainers don't get that benefit -- they own all 7 agents plus the coordinator plus the arbitrator.

**Consequences:** Maintenance burden grows sublinearly with features but superlinearly with module count. Agent boundaries become fuzzy over time (is "IOL portfolio performance" a finance query or an infra query when Ollama is needed for analysis?). The arbitrator handles <1% of queries but adds permanent complexity.

**Prevention:**
- Start with 3 agents maximum: infra, personal, finance. Research and self-improve can be submodules of an "autonomous" agent.
- The arbitrator should be a simple priority dict (already exists as `PRIORITY` in `agent_arbitrator.py`), not a full module. Inline it into the coordinator.
- Merge research + self_improve into one agent. Their ownership sets barely overlap with others and they only run in background workers anyway.
- Each agent must earn its existence: if an agent's `assess()` returns empty/trivial >90% of the time, merge it into its nearest neighbor.

**Detection:** Track per-agent activation rate. If `agent_research` fires on <5% of coordinator queries, it doesn't justify a separate module.

**Phase:** Phase 1 (Foundation) architecture decision. Commit to 3 agents. Add more only when the 3-agent system proves insufficient.

---

## Moderate Pitfalls

### Pitfall 5: Agent Assess Latency Kills Telegram Response Time

**What goes wrong:** `agent_infra.assess()` calls `homelab_snapshot.snapshot()` which pings HomeLab GPU, checks containers, queries Ollama. This takes 2-5 seconds. `agent_finance.assess()` calls Binance API + IOL API. Another 2-3 seconds. Running 5 agents sequentially: 10-15 seconds. Running them in parallel with ThreadPoolExecutor: still 5 seconds (bottlenecked by slowest agent).

**Why it happens:** The current design has `assess()` doing live queries every time. No caching layer. Each coordinator fan-out re-probes the entire infrastructure.

**Prevention:**
- Cache assess results: Each agent's `assess()` result should be cached for 60-120 seconds (same pattern as `ollama-check.sh`'s 120s cache). The coordinator reads cached state, doesn't trigger live probes.
- Separate the "observe" loop (background, every 5 min via worker) from the "respond" path (Telegram query, must be <3s). Agents write status to their JSON file during the observe loop. Coordinator reads files during respond path.
- Hard timeout: Coordinator gives each agent 2s max. If `assess()` doesn't return, use last cached state.

**Phase:** Phase 2 (Agent Implementation). Cache-first architecture must be established before Telegram integration.

---

### Pitfall 6: Ownership Overlap Creates Ambiguous Routing

**What goes wrong:** "cuanto gaste en cloud" -- is this finance (expenses) or infra (OpenRouter usage)? "estado del portfolio" -- finance (IOL) or infra (Ollama model portfolio)? The existing `OWNERSHIP` sets in agents have keyword overlap. `agent_infra` owns "backend_offline" while `agent_finance` owns "portfolio" -- but "portfolio health when backend offline" touches both.

**Why it happens:** Keyword-based ownership (the current `owns()` method) is inherently ambiguous. Natural language doesn't respect module boundaries. The MAST taxonomy research shows specification problems (role ambiguity) cause 42% of multi-agent failures.

**Prevention:**
- Ownership tiebreaker: If multiple agents claim ownership, the coordinator picks ONE primary (highest priority for that query type) and passes context from secondary agents as read-only input.
- Exclusive keywords: Each keyword belongs to exactly ONE agent. Remove duplicates from OWNERSHIP sets. "portfolio" = finance (always). "backend" = infra (always). Document the boundary in a shared OWNERSHIP_REGISTRY.
- Test ownership exhaustively: For every keyword in every agent's OWNERSHIP set, assert no other agent claims it. The existing `test_sprint9.py` tests are a good start but need expansion.

**Phase:** Phase 1 (Foundation) must define and test exclusive ownership before agents are connected.

---

### Pitfall 7: Testing Complexity Explosion

**What goes wrong:** The current test suite has 900+ tests across 30+ files. Adding 5 agents + coordinator + arbitrator means: unit tests per agent, integration tests for coordinator routing, arbitrator conflict tests, regression tests against existing routing, end-to-end tests through Telegram. The test matrix grows from N to N*M where M is the number of agent interaction paths.

**Why it happens:** Multi-agent systems have combinatorial interaction paths. Agent A's output feeds into Coordinator's decision which triggers Agent B's action -- each step is testable individually but the combinations explode.

**Prevention:**
- Contract testing: Each agent has a strict interface contract (assess() returns dict with required keys, handle_query() returns string). Test contracts, not internals. Mock agents in coordinator tests.
- Snapshot testing for routing: Record coordinator routing decisions for 50 canonical queries. Assert they don't change across refactors.
- Simulation tests (already exist in `tests/simulation/`): Extend with agent scenarios. Don't unit-test every agent interaction -- simulate realistic 1-hour windows.
- Testing pyramid: 70% unit (per-agent, mocked), 20% integration (coordinator + real agents), 10% e2e (Telegram dry-run).

**Phase:** Phase 1 must establish contract interfaces and test patterns. Each subsequent phase adds tests for its agents.

---

### Pitfall 8: Circular Import Hell

**What goes wrong:** `agent_infra` imports `homelab_snapshot` which imports `model_router` which imports `orchestrator` which (in the new design) imports `agent_coordinator` which imports `agent_infra`. Python raises `ImportError` or returns partially-initialized modules.

**Why it happens:** The existing codebase already has defensive `try/except ImportError` blocks everywhere (orchestrator.py lines 14-37). Adding agent modules that reference existing infrastructure modules creates new import cycles. The lazy import pattern (`importlib.import_module` in `agent_coordinator.py` line 62) mitigates but doesn't eliminate this.

**Prevention:**
- Agents must NEVER import the coordinator or orchestrator. Data flows one way: coordinator reads agent output, agents don't call coordinator.
- Use the existing lazy import pattern (`_load_agent()`) consistently. Don't add top-level imports between agent modules.
- Shared utilities go in `runtime_utils.py` or `runtime_config.py` (already the pattern). Never in agent modules.
- Test: Add a CI check that imports each agent module in isolation (`python -c "import agent_infra"`) without the full runtime on sys.path.

**Phase:** Phase 1 (Foundation) import architecture. Enforce with automated check.

---

## Minor Pitfalls

### Pitfall 9: Log File Proliferation

**What goes wrong:** Each agent creates its own log file (`logs/agent_infra.log`, `logs/agent_finance.log`, etc.). Combined with existing logs (orchestrator.log, model_router.log, telegram_router.log, runtime.log), the system generates 10+ log files. Debugging a single user query requires correlating entries across 5+ files.

**Prevention:** Add a correlation ID (query_id) to all log entries in a single request path. Use the existing `_log()` pattern but include a shared `qid` field. Alternatively, log agent decisions to the coordinator log with agent name as a field, not to separate files.

**Phase:** Phase 2, as part of agent implementation.

---

### Pitfall 10: Arbitrator Adds Latency for Rare Events

**What goes wrong:** The arbitrator checks for conflicts on every multi-agent query, but conflicts occur <1% of the time. The conflict detection (hash computation, file read of conflicts.json, priority comparison) adds 5-10ms per query for a feature that almost never triggers.

**Prevention:** Only invoke arbitrator when coordinator detects >1 agent proposing actions on the same resource. Skip entirely for read-only assess queries. The existing `POSTPONE_SECONDS = 300` and `MAX_POSTPONE_COUNT = 3` are reasonable but should only run when there's an actual conflict.

**Phase:** Phase 3 (Integration). Inline conflict resolution into coordinator rather than separate module.

---

### Pitfall 11: Feature Flag Debt

**What goes wrong:** The system already has `USE_ROUTER_V2`, `tenazas.use_finetuned_router`, and will need `USE_AGENT_COORDINATOR`. Each flag creates a testing matrix (V2 on/off x finetuned on/off x agents on/off = 8 configurations). Not all combinations are tested.

**Prevention:** Deprecate old flags when new system is stable. Don't accumulate more than 2 active feature flags. Set a deadline: if agents aren't enabled within 4 weeks, remove the code.

**Phase:** Phase 4 (Hardening). Clean up feature flags after agent system is proven.

---

## Phase-Specific Warnings

| Phase Topic | Likely Pitfall | Mitigation |
|-------------|---------------|------------|
| Foundation (agent contracts) | Over-specifying agent interfaces too early | Start with `assess() -> dict` and `handle_query(str) -> str`. Evolve from there. |
| Foundation (state files) | Not fixing atomic writes before enabling agents | Fix `_save()` in ALL 9 agent modules to use `os.replace()` pattern. |
| Agent Implementation | Making agents too independent (each re-discovers system state) | Agents read shared `homelab_snapshot` output, don't probe independently. |
| Agent Implementation | Response budget not enforced | Hard-code 100-char limit in agent `assess()` summary field. |
| Coordinator Integration | Replacing orchestrator.py routing instead of augmenting | `is_multi_domain()` is the ONLY entry point to agents. Single-domain stays on orchestrator. |
| Coordinator Integration | Not preserving Telegram response time SLA (<3s) | Cache agent assess results. Coordinator reads files, doesn't trigger probes. |
| Arbitrator Integration | Building a complex conflict resolution engine | Start with priority dict inline in coordinator. Extract to module only if conflicts are frequent. |
| Testing and Hardening | Trying to test all agent combinations | Contract testing + snapshot routing tests. Not combinatorial coverage. |
| Telegram Integration | Breaking existing `telegram_router.py` skill interception | Agents never touch Telegram directly. Coordinator output goes through existing response path. |
| Production Rollout | Enabling all agents at once | Roll out one agent at a time: infra first, then personal, then finance. Each gets a week of monitoring. |

## Integration-Specific Pitfalls (THIS System)

These are pitfalls unique to Tenazas's architecture, not generic multi-agent advice.

### The Telegram Router Is Already an Agent

The `telegram_router.py` already classifies intents, executes skills deterministically, and falls back to LLM. Adding a "coordinator" risks duplicating this logic. The coordinator must sit BEHIND telegram_router's skill interception, not replace it.

### The Worker Supervisor Creates Hidden Concurrency

`worker_supervisor.py` runs 5 workers sequentially with individual timeouts. If agents are triggered from `agents_worker`, they run in the same process. But if the coordinator is called from `telegram_router.py` (a different process), state files are shared between the worker process and the Telegram process. This is the primary contention vector.

### Model Router Guardrails Apply to Agent LLM Calls

Each agent that calls an LLM (for synthesis, analysis) goes through `model_router.py`. The guardrails (cooldown 120s, max 3 heavy/10min) are GLOBAL, not per-agent. Five agents each requesting `qwen2.5:32b` will hit guardrails immediately. Agents should use `fast` role by default, reserving `reason`/`heavy` only for the coordinator's synthesis step.

### The 5-Minute Cron Loop Is the Natural Agent Heartbeat

Don't invent a new scheduling mechanism. The existing `runtime.py` -> `worker_supervisor.py` -> `agents_worker.py` loop already runs every 5 minutes. Agent `assess()` runs there. Telegram queries read cached results. This separation (background observe vs. foreground respond) is the architecture's biggest strength -- don't break it by making agents do live probes on Telegram queries.

### Existing Approval Flow Must Not Be Duplicated

`exec_approvals.py` already handles Telegram inline keyboard approvals. Agents that propose actions must feed into the EXISTING approval queue, not create agent-specific approval flows. One approval system, multiple producers.

## Sources

- [Why Multi-Agent LLM Systems Fail (Augment Code)](https://www.augmentcode.com/guides/why-multi-agent-llm-systems-fail-and-how-to-fix-them) -- MAST taxonomy: 42% specification, 37% coordination, 21% verification failures
- [17x Error Trap (Towards Data Science)](https://towardsdatascience.com/why-your-multi-agent-system-is-failing-escaping-the-17x-error-trap-of-the-bag-of-agents/) -- Error amplification in uncoordinated agent systems
- [Multi-Agent Architecture Patterns (TrueFoundry)](https://www.truefoundry.com/blog/multi-agent-architecture) -- State management as first failure point in production
- [AI Agent Orchestration Patterns (Microsoft Azure)](https://learn.microsoft.com/en-us/azure/architecture/ai-ml/guide/ai-agent-design-patterns) -- Registry + Router + State Store + Supervisor pattern
- Codebase analysis: `agent_coordinator.py`, `agent_arbitrator.py`, `agent_infra.py`, `agent_finance.py`, `agent_personal.py`, `orchestrator.py`, `telegram_router.py`, `worker_supervisor.py`, `skill_executor.py`, `classifier.py`
