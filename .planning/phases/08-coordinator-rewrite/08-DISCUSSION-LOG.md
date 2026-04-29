---
phase: 08-coordinator-rewrite
created: 2026-04-29
---

# Phase 8 Discussion Log

## Session: 2026-04-29

### Gray Areas Identified

After codebase scouting (agent_arbitrator.py, orchestrator.py, telegram_router.py, all 5 agents), 4 gray areas were identified for Phase 8:

1. **Orchestrator replacement strategy** — clean cut vs. coordinator as wrapper
2. **Multi-domain detection** — how to classify queries that span multiple agent domains
3. **Execution strategy** — parallel vs. sequential vs. first-wins
4. **Response aggregation** — deterministic sections vs. LLM synthesis

---

### D-01: Orchestrator Replacement Strategy

**Question presented:** Should the coordinator wrap orchestrator.route_and_execute() and delegate skills, or replace it entirely as the entry point?

**User decision:** Coordinator-first, skills fallback. Every query hits coordinator. Agents claim via `owns()`. Zero claims → skill_executor fallback → LLM chat. Orchestrator.py stays intact but is no longer called by telegram_router in Phase 9.

---

### D-02: Multi-Domain Detection

**Question presented:** How should the coordinator detect that a query spans multiple domains — keyword matching, LLM classification, or call all 5 `owns()` in parallel?

**User decision:** All 5 agents evaluate `owns(query)` in parallel. 2+ True = multi-domain. Lightweight keyword matching only — no LLM overhead for classification.

---

### D-03: Execution Strategy

**Question presented:** For multi-domain queries, should agents run in parallel (ThreadPoolExecutor), sequentially, or use first-wins?

**User decision:** Always parallel for multi-domain. First-wins for single-domain. Skill fallback for zero claims. 30s timeout per agent — timeout = omit from aggregation, not fatal.

---

### D-04: Response Aggregation

**Question presented:** For multi-domain responses, should the coordinator use deterministic labeled sections or an LLM to synthesize a unified response?

**User decision:** Structured sections per domain — deterministic, no LLM. Each agent response prefixed with domain emoji. Newline separator. Telegram plain-text friendly.

Domain emoji map decided:
- infra → 📡
- personal → 👤  
- finance → 💰
- research → 🔍
- self_improve → 🔧

---

### Summary

All 4 gray areas resolved. No deferred decisions blocking planning. Ready for `/gsd:plan-phase 8`.
