# Requirements: Tenazas Multi-Agent System

**Defined:** 2026-04-28
**Core Value:** Every query reaches the right domain agent, gets an expert-level response, and any recommended action flows through Telegram approval before execution.

## v2.0 Requirements

Requirements for multi-agent system. Each maps to roadmap phases.

### Agent Framework

- [ ] **FRMK-01**: Typed agent protocol with observe(), recommend(), owns() contract via dataclasses
- [ ] **FRMK-02**: Agent registry with register/unregister, health checks, graceful degradation on failure
- [ ] **FRMK-03**: Error isolation — each agent runs in try/except, one crash never affects others
- [ ] **FRMK-04**: Per-agent model routing — agents specify preferred LLM role for domain queries

### Coordinator

- [ ] **CORD-01**: Query classification and routing to domain agents, replacing orchestrator as entry point
- [ ] **CORD-02**: Multi-domain query aggregation — combine responses spanning multiple agents
- [ ] **CORD-03**: Strategy selection — parallel vs sequential vs first-wins based on query type
- [ ] **CORD-04**: Correlation IDs for end-to-end query tracing through coordinator → agent → skill

### Domain Agents

- [ ] **AGNT-01**: agent_infra — infrastructure monitoring (containers, GPU, VPN, disk, health)
- [ ] **AGNT-02**: agent_personal — personal assistant (calendar, email, reminders)
- [ ] **AGNT-03**: agent_finance — finance domain (IOL, expenses, Binance alerts)
- [ ] **AGNT-04**: agent_research — knowledge domain (SearXNG, RAG queries, knowledge base)
- [ ] **AGNT-05**: agent_self_improve — self-improvement (error mining, proposals, experiments)
- [ ] **AGNT-06**: Cross-agent context sharing — agents read each other's recent observations
- [ ] **AGNT-07**: Proactive observations via background cron — cached for fast Telegram responses

### Arbitrator

- [ ] **ARBT-01**: Priority-based conflict resolution when 2+ agents claim a query
- [ ] **ARBT-02**: Weighted multi-factor scoring (keyword match + confidence + historical accuracy)
- [ ] **ARBT-03**: Escalation to Telegram when agents tie or produce contradictory recommendations

### Integration

- [ ] **INTG-01**: Wire coordinator into telegram_router.py as the new routing entry point
- [ ] **INTG-02**: Shared JSON state with file locking (fcntl) and atomic writes for concurrent access
- [ ] **INTG-03**: Feature flag USE_AGENT_COORDINATOR for enable/disable without code changes
- [ ] **INTG-04**: Prometheus metrics per agent (latency, success rate, query count) + Grafana dashboard

### Testing

- [ ] **TEST-01**: Unit tests for each new module (protocol, state, coordinator, arbitrator, 5 agents)
- [ ] **TEST-02**: Integration tests for full pipeline (telegram → coordinator → agent → skill → response)
- [ ] **TEST-03**: Regression tests ensuring existing routing behavior preserved during transition

## Future Requirements

### Advanced Autonomy (deferred to v2.1+)

- **AUTO-01**: Tiered autonomy — agents auto-execute safe actions (status checks, reports)
- **AUTO-02**: Agent learning from Telegram approval/rejection patterns
- **AUTO-03**: Coordinator promotes to full replacement after validation period

### Advanced Features

- **ADV-01**: Agent memory persistence across restarts (beyond JSON state)
- **ADV-02**: Natural language agent composition ("ask infra and finance about...")
- **ADV-03**: Agent-to-agent direct delegation without coordinator mediation

## Out of Scope

| Feature | Reason |
|---------|--------|
| External agent frameworks (CrewAI, LangGraph, AutoGen) | 5 agents on single NAS — stdlib covers all patterns, frameworks add 50+ deps |
| Model fine-tuning for agents | Agents use existing Ollama models via model_router; no new training |
| Async/await agent execution | Entire codebase is sync; ThreadPoolExecutor provides sufficient I/O parallelism |
| Agent auto-execution | v2.0 is observe+recommend only; all actions require Telegram approval |
| Real-time inter-agent messaging | Shared JSON state files sufficient at current scale |
| SQLite for agent state | JSON + atomic writes + file locking sufficient for 5-7 agents |

## Traceability

Which phases cover which requirements. Updated during roadmap creation.

| Requirement | Phase | Status |
|-------------|-------|--------|
| (populated by roadmapper) | | |

**Coverage:**
- v2.0 requirements: 21 total
- Mapped to phases: 0
- Unmapped: 21

---
*Requirements defined: 2026-04-28*
*Last updated: 2026-04-28 after initial definition*
