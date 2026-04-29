# Phase 7: Arbitrator & Cross-Agent - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions captured in CONTEXT.md — this log preserves the conversation.

**Date:** 2026-04-29
**Phase:** 07-arbitrator-cross-agent
**Mode:** discuss (interactive)
**Areas discussed:** Conflict scoring strategy, Escalation behavior, Cross-agent observation cache, Proactive cron observations

## Questions & Answers

### Conflict Scoring Strategy
- **Q:** How should conflict scoring work when 2+ agents claim a query?
- **Options:** Extend static priorities with confidence tiebreaker / Weighted composite score / Priority-first cascade
- **Selected:** Extend static priorities with confidence tiebreaker
- **Notes:** Keep existing PRIORITY dict, add confidence as tiebreaker. No historical accuracy tracking.

### Escalation Behavior
- **Q:** When agents tie or contradict, how should escalation to Telegram work?
- **Options:** Immediate Telegram with inline buttons / Postpone 5min then escalate / Log-only silent pick
- **Selected:** Immediate Telegram with inline buttons
- **Notes:** Use exec_approvals.py, 5-min timeout, default to higher priority if no response.

### Cross-Agent Observation Cache
- **Q:** How should cross-agent observation caching work?
- **Options:** Per-agent state files with observation section / Shared cache file / In-memory only
- **Selected:** Per-agent state files with observation section
- **Notes:** Consistent with D-09/D-11 from Phase 5. 10-min TTL.

### Proactive Cron Observations
- **Q:** Which agents should run proactively via cron?
- **Options:** Infra + Finance only / All 5 agents / Top 3 (infra + finance + personal)
- **Selected:** All 5 agents proactively
- **Notes:** Every agent produces cached observation every 5 min on runtime.py cron cycle.

## Deferred Ideas
None.
