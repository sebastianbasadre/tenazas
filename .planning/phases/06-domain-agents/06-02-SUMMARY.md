---
phase: "06-domain-agents"
plan: "02"
subsystem: "agent-personal, agent-research"
tags: [agents, query-routing, personal, research, rag]
dependency_graph:
  requires: [agent_protocol.py, notification_poller.py, continuous_learning_loop.py]
  provides: [query-responsive agent_personal.handle(), query-responsive agent_research.handle()]
  affects: [agent_coordinator.py (future consumer)]
tech_stack:
  added: []
  patterns: [keyword-routing, observer-pattern, graceful-degradation]
key_files:
  created: []
  modified:
    - workspace/runtime/agent_personal.py
    - workspace/runtime/agent_research.py
decisions:
  - "agent_personal smoking observer reads log/metrics files directly as fallback when smoking.get_stats() not importable"
  - "agent_research RAG observer uses subprocess call to query.py (not module import) to avoid import path issues"
  - "agent_research _observe_full tries RAG first (fast/local) then SearXNG (slow/external)"
metrics:
  duration: "123s"
  completed: "2026-06-26"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 2
---

# Phase 06 Plan 02: Query-Responsive Agent Handles Summary

Query-responsive handle() for agent_personal (5 observers: calendar/email/smoking/reminders/full) and agent_research (3 observers: rag/searxng/full) with keyword routing and graceful degradation.

## Task Results

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Query-responsive handle() for agent_personal.py | 568c9fe | workspace/runtime/agent_personal.py |
| 2 | Query-responsive handle() for agent_research.py | 285b842 | workspace/runtime/agent_research.py |

## What Changed

### agent_personal.py
- Added 5 private observer functions: `_observe_calendar()`, `_observe_email()`, `_observe_smoking()`, `_observe_reminders()`, `_observe_all_personal()`
- `handle()` now parses query with `query.lower()` and routes to specific observer based on keywords (Spanish + English)
- Calendar/email observers use `notification_poller` imports with try/except degradation
- Smoking observer tries `smoking.get_stats()` first, falls back to direct JSON file reads
- Reminders observer acknowledges the query but notes it should be handled by skill:reminders
- `_observe_all_personal()` wraps existing `assess()` for backward compatibility
- `assess()` left completely unchanged for cron usage

### agent_research.py
- Added 3 private observer functions: `_observe_rag(query)`, `_observe_research(query)`, `_observe_full(query)`
- `handle()` now parses query and routes: "rag"/"knowledge" keywords go to RAG, "research"/"buscar"/"searxng" go to SearXNG, default tries both
- RAG observer calls query.py via subprocess (list args, no shell=True, 10s timeout, query truncated to 200 chars)
- `_observe_full()` tries RAG first (fast, local), falls back to SearXNG if RAG returns nothing
- PREFERRED_ROLE="reason" preserved for deeper model routing in future coordinator
- Added `import subprocess` for RAG subprocess call

## Deviations from Plan

None - plan executed exactly as written.

## Threat Mitigations Applied

- **T-06-05 (DoS on SearXNG):** SearXNG calls wrapped in try/except; RAG used as fast fallback
- **T-06-06 (Injection via RAG subprocess):** Query passed as list argument to subprocess.run (not shell=True); query truncated to 200 chars

## Self-Check: PASSED
