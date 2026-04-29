# Roadmap: Tenazas Multi-Agent System

## Milestones

- ✅ **v1.0 Runtime Refactor** — Phases 1-4 (shipped 2026-04-28)
- 🚧 **v2.0 Multi-Agent System** — Phases 5-9 (in progress)

## Phases

<details>
<summary>v1.0 Runtime Refactor (Phases 1-4) — SHIPPED 2026-04-28</summary>

- [x] **Phase 1: Foundation Utilities** - Extract duplicated helpers into runtime_utils.py with atomic write support *(3/3 plans, completed 2026-04-28)*
- [x] **Phase 2: Config Centralization** - Replace 268 hardcoded IPs/ports with a single config module *(2/2 plans, completed 2026-04-28)*
- [x] **Phase 3: Orchestrator Decomposition** - Split the 2373-line god object into focused modules *(3/3 plans, completed 2026-04-28)*
- [x] **Phase 4: Dead Code Cleanup** - Remove deprecated scripts, legacy entries, and orphaned modules *(1/1 plan, completed 2026-04-28)*

</details>

### v2.0 Multi-Agent System

**Milestone Goal:** Replace the monolithic orchestrator with 5 specialized domain agents coordinated by a central routing brain, with conflict resolution and Telegram approval gates.

**Phase Numbering:**
- Integer phases (5, 6, 7, 8, 9): Planned milestone work
- Decimal phases (5.1, 6.1): Urgent insertions (marked with INSERTED)

- [x] **Phase 5: Foundation & Contracts** - Typed agent protocol, registry, shared state with file locking *(3/3 plans, completed 2026-04-29)*
- [ ] **Phase 6: Domain Agents** - 5 specialized agents implementing observe/recommend contract
- [ ] **Phase 7: Arbitrator & Cross-Agent** - Conflict resolution, cross-agent context, proactive observations
- [ ] **Phase 8: Coordinator Rewrite** - Central routing brain replacing orchestrator as entry point
- [ ] **Phase 9: Pipeline Integration & Testing** - Wire into Telegram, feature flag, metrics, full test coverage

## Phase Details

### Phase 5: Foundation & Contracts
**Goal**: Every agent module shares typed interfaces and safe concurrent state access
**Depends on**: Phase 4 (v1.0 complete)
**Requirements**: FRMK-01, FRMK-02, FRMK-03, FRMK-04, INTG-02
**Success Criteria** (what must be TRUE):
  1. Importing `agent_protocol` gives Observation and Recommendation dataclasses that all agents use as return types
  2. Agent registry can register, list, and health-check agents; unregistering an agent removes it from routing
  3. An agent raising an exception does not crash the registry or affect other registered agents
  4. Two processes writing agent state concurrently never produce corrupted JSON (atomic writes + fcntl locking)
  5. Each agent can declare its preferred LLM role (fast/reason/code/heavy) and the registry exposes it
**Plans:** 3 plans
  Plans:
  - [x] 05-01-PLAN.md -- Protocol dataclasses + agent registry + tests
  - [x] 05-02-PLAN.md -- Locked state writes (fcntl + atomic) + tests
  - [x] 05-03-PLAN.md -- Refactor 5 agents + coordinator delegation + integration tests

### Phase 6: Domain Agents
**Goal**: Each domain has a dedicated agent that can observe queries and recommend actions within its expertise
**Depends on**: Phase 5
**Requirements**: AGNT-01, AGNT-02, AGNT-03, AGNT-04, AGNT-05
**Success Criteria** (what must be TRUE):
  1. agent_infra observes infrastructure queries (container status, GPU health, VPN) and returns typed Observations with actionable recommendations
  2. agent_personal observes personal queries (calendar, email, reminders) and returns typed Observations
  3. agent_finance observes finance queries (IOL portfolio, expenses, Binance) and returns typed Observations
  4. agent_research observes knowledge queries (RAG, SearXNG) and returns typed Observations using reason-role LLM
  5. agent_self_improve observes self-improvement queries (error mining, proposals) and returns typed Observations

### Phase 7: Arbitrator & Cross-Agent
**Goal**: When multiple agents claim a query, the system resolves conflicts and agents can share context
**Depends on**: Phase 6
**Requirements**: ARBT-01, ARBT-02, ARBT-03, AGNT-06, AGNT-07
**Success Criteria** (what must be TRUE):
  1. When 2+ agents claim ownership of a query, the arbitrator selects the winner using priority + confidence scoring
  2. Arbitrator applies weighted multi-factor scoring (keyword match strength, agent confidence, historical accuracy) to break ties
  3. When agents tie or produce contradictory recommendations, the system escalates to Telegram for Sebas to choose
  4. Each agent can read other agents' recent observations from shared state (cross-agent context)
  5. Agents produce cached observations via background cron that the coordinator reads instantly during Telegram queries

### Phase 8: Coordinator Rewrite
**Goal**: A new coordinator routes every query to the right agent(s) and aggregates multi-domain responses
**Depends on**: Phase 7
**Requirements**: CORD-01, CORD-02, CORD-03, CORD-04
**Success Criteria** (what must be TRUE):
  1. Coordinator classifies incoming queries and routes them to the correct domain agent(s), replacing orchestrator as the routing brain
  2. Multi-domain queries (e.g., "how is my portfolio and any infra alerts?") receive an aggregated response combining outputs from multiple agents
  3. Coordinator selects execution strategy per query: parallel (multi-domain), sequential (dependent), or first-wins (single-domain)
  4. Every query passing through the coordinator carries a correlation ID traceable through coordinator, agent, and skill execution logs

### Phase 9: Pipeline Integration & Testing
**Goal**: The multi-agent system is live in Telegram behind a feature flag with full observability and test coverage
**Depends on**: Phase 8
**Requirements**: INTG-01, INTG-03, INTG-04, TEST-01, TEST-02, TEST-03
**Success Criteria** (what must be TRUE):
  1. Setting USE_AGENT_COORDINATOR=true in config routes Telegram queries through the coordinator; setting it false restores monolithic orchestrator behavior with zero code changes
  2. Prometheus exposes per-agent metrics (query count, latency, success rate) and a Grafana dashboard visualizes agent activity
  3. Unit tests exist for every new module (protocol, state, coordinator, arbitrator, 5 agents) with mocked dependencies
  4. Integration tests verify the full pipeline: Telegram message in, coordinator routes, agent observes, skill executes, response sent back
  5. Regression tests confirm that the top query patterns routed correctly before v2.0 still route correctly after

## Progress

**Execution Order:**
Phases execute in numeric order: 5 -> 5.x -> 6 -> 6.x -> 7 -> 8 -> 9

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Foundation Utilities | v1.0 | 3/3 | Complete | 2026-04-28 |
| 2. Config Centralization | v1.0 | 2/2 | Complete | 2026-04-28 |
| 3. Orchestrator Decomposition | v1.0 | 3/3 | Complete | 2026-04-28 |
| 4. Dead Code Cleanup | v1.0 | 1/1 | Complete | 2026-04-28 |
| 5. Foundation & Contracts | v2.0 | 3/3 | Complete | 2026-04-29 |
| 6. Domain Agents | v2.0 | 0/0 | Not started | - |
| 7. Arbitrator & Cross-Agent | v2.0 | 0/0 | Not started | - |
| 8. Coordinator Rewrite | v2.0 | 0/0 | Not started | - |
| 9. Pipeline Integration & Testing | v2.0 | 0/0 | Not started | - |
