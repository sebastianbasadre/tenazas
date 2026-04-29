---
phase: 06-domain-agents
plan: 01
subsystem: domain-agents
tags: [agent-infra, agent-finance, agent-self-improve, query-routing, observers]
dependency_graph:
  requires: [agent_protocol.py, homelab_snapshot.py, finance_daily.py, error_mining.py]
  provides: [query-responsive handle() for 3 domain agents]
  affects: [agent_coordinator.py, agent_arbitrator.py]
tech_stack:
  added: []
  patterns: [keyword-routed query dispatch, focused observer functions, degraded dict fallback]
key_files:
  created: []
  modified:
    - workspace/runtime/agent_infra.py
    - workspace/runtime/agent_finance.py
    - workspace/runtime/agent_self_improve.py
decisions:
  - "Observer functions use try/except with degraded dict return (confidence=0.3) to never crash the agent"
  - "BLOCKED_TARGETS filtering applied in _build_recommendations for infra agent"
  - "expenses observer uses two-level import fallback: expenses.weekly_summary then finance.summary"
metrics:
  duration: "2m 18s"
  completed: "2026-04-29T13:53:53Z"
  tasks_completed: 2
  tasks_total: 2
  files_modified: 3
---

# Phase 06 Plan 01: Query-Responsive Domain Agent Handles Summary

Keyword-routed handle() for agent_infra, agent_finance, and agent_self_improve with 13 focused observer functions and typed Recommendation builders.

## Task Results

| Task | Name | Commit | Files |
|------|------|--------|-------|
| 1 | Query-responsive handle() for agent_infra.py | f3d008f | workspace/runtime/agent_infra.py |
| 2 | Query-responsive handle() for agent_finance.py and agent_self_improve.py | 8a54dce | workspace/runtime/agent_finance.py, workspace/runtime/agent_self_improve.py |

## Changes Made

### agent_infra.py
- Added 4 focused observers: `_observe_containers()`, `_observe_ollama()`, `_observe_vpn()`, `_observe_disk()`
- Added `_assess_full()` wrapper that calls existing `assess()` and reformats output
- Added `_build_recommendations(data)` with BLOCKED_TARGETS filtering (openclaw, watchtower never targeted)
- `handle()` now parses query keywords to route to specific observer (containers/docker, gpu/ollama, vpn/gluetun, disk/storage, or full fallback)

### agent_finance.py
- Added 5 focused observers: `_observe_binance()`, `_observe_iol()`, `_observe_expenses()`, `_observe_dolar()`, `_observe_full()`
- Added `_build_finance_recs(data)` that creates alert Recommendations from issues
- `_observe_expenses()` has two-level import fallback (expenses.weekly_summary then finance.summary)
- `handle()` routes by keyword (binance/btc, iol/portfolio, gasto/expense, dolar/usd, or full fallback)

### agent_self_improve.py
- Added 5 focused observers: `_observe_errors()`, `_observe_proposals()`, `_observe_experiments()`, `_observe_benchmark()`, `_assess_full()`
- Added `_build_improve_recs(data)` that creates review Recommendations from errors/proposals
- `handle()` routes by keyword (error/falla, proposal/propuesta, experiment, benchmark, or full fallback)
- `run_daily()` left completely unchanged

## Backward Compatibility

- `assess()` in all 3 agents is UNCHANGED -- cron and existing callers work identically
- `handle_query()` wrapper still works via handle()
- `owns()`, `get_memory()`, `save_memory_event()` all untouched
- All module constants (OWNERSHIP, BLOCKED_TARGETS, PREFERRED_ROLE, BTC_ALERT_THRESHOLD) preserved

## Deviations from Plan

None -- plan executed exactly as written.

## Known Stubs

None -- all observers are wired to real domain modules (homelab_snapshot, finance_daily, error_mining, improvement_candidates, experiment_runner, self_benchmark, expenses, finance). Each has try/except fallback returning degraded dict if the module is unavailable.

## Self-Check: PASSED
