# Tenazas Multi-Agent System

## What This Is

Tenazas is Sebas's personal AI assistant running on a Synology NAS — 42+ Python modules handling infrastructure monitoring, smart home, finance, job search, and self-improvement. v2.0 replaces the monolithic orchestrator with a multi-agent architecture: 5 specialized domain agents coordinated by a central routing brain with conflict resolution.

## Core Value

Every query reaches the right domain agent, gets an expert-level response, and any recommended action flows through Telegram approval before execution.

## Current Milestone: v2.0 Multi-Agent System

**Goal:** Replace the monolithic orchestrator with a multi-agent architecture — 5 specialized domain agents coordinated by a central coordinator, with conflict resolution via an arbitrator.

**Target features:**
- agent_coordinator.py — Central routing brain replacing orchestrator.py
- agent_arbitrator.py — Conflict resolution when agents disagree
- agent_infra.py — Infrastructure agent (containers, GPU, VPN, health)
- agent_personal.py — Personal agent (calendar, email, reminders)
- agent_finance.py — Finance agent (IOL, expenses, Binance alerts)
- agent_research.py — Research agent (SearXNG, RAG, knowledge base)
- agent_self_improve.py — Self-improvement agent (error mining, proposals, experiments)

## Requirements

### Validated

- runtime_utils.py shared utilities (load_json, save_json, now, write_atomic) — v1.0
- All 30 duplicate _save_json/_load_json and 89 _now() implementations consolidated — v1.0
- runtime_config.py centralized infrastructure endpoints with env var overrides — v1.0
- 268 hardcoded IPs/ports across 65 files replaced with config imports — v1.0
- orchestrator.py decomposed from 2373 to 399 lines with 4 extracted modules — v1.0
- Backward-compatible facade preserving all existing imports — v1.0
- auto-heal.sh removed, MODEL_CATALOG audited, all 201 modules reachable — v1.0

### Active

(Defined in REQUIREMENTS.md for v2.0)

### Out of Scope

- SQLite migration for JSON state — atomic writes solve corruption at current scale
- Type hints everywhere — add only to refactored public APIs incrementally
- Abstract base classes — over-engineering for single-maintainer project
- Async/await rewrite — cron-based runtime has no event loop benefit
- CI/CD pipeline — make code CI-ready but don't build the pipeline itself
- Autonomous agent execution — all actions require Telegram approval (observe + recommend only)
- Real-time inter-agent messaging — shared JSON state files are sufficient at current scale
- v1.0 reliability hardening (atomic write adoption, file locking, logging standardization) — deferred, not blocking v2.0

## Context

v1.0 shipped on 2026-04-28 with 4 phases (9 plans) in 1.52 hours. The runtime now has shared utilities, centralized config, and a decomposed orchestrator. v2.0 builds on this foundation — the coordinator replaces orchestrator.py as the main routing entry point from telegram_router.py while keeping the existing skill execution engine intact.

**Architecture decisions for v2.0:**
- Agents communicate via shared JSON state files (fits existing patterns)
- All agents observe + recommend only — actions flow through Telegram approval
- Coordinator replaces orchestrator.py as main routing brain
- Existing skill execution engine stays intact — agents delegate to it

**Known issues:** 31 test suites have pre-existing failures from v1.0 import path restructuring.

## Constraints

- **Runtime:** Python 3 inside Docker container (node:24-bookworm base)
- **No new dependencies:** Prefer stdlib solutions (fcntl, tempfile, pathlib) over adding packages
- **Test compatibility:** All existing unittest-based tests must pass without modification (unless testing refactored interfaces)
- **Container paths:** Scripts reference `/home/tenazas/runtime/` — module moves must preserve import paths or update all references
- **Cron/startup:** `startup.sh`, `runtime.py`, `metrics_server.py` entry points must continue to work
- **Approval gate:** Every agent-recommended action must go through Telegram approval before execution

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| Atomic writes over SQLite migration | JSON files work at current scale; atomic writes fix corruption without migration risk | Good — write_atomic() works, mass adoption deferred |
| Full rebuild OK (downtime accepted) | Sebas can take Tenazas offline during refactor | Good — no issues |
| Drop dead code aggressively | Reduces confusion, fewer files to maintain | Good — auto-heal.sh removed, all modules verified reachable |
| Duplicate _log() per submodule | Avoids complex import chains in decomposed orchestrator | Good — 4 lines per module, clean isolation |
| Orchestrator facade at 399 lines | Realistic floor after extracting all possible code | Acceptable — above 300-line target but no further extraction possible |
| Coordinator replaces orchestrator | Multi-agent needs a new routing brain; orchestrator is a monolithic classifier | — Pending |
| Shared JSON state for agent comms | Fits existing patterns (state.json, events.json); no new infra needed | — Pending |
| Observe + recommend only | Safety first; agents never auto-execute. Builds trust before adding autonomy | — Pending |

## Evolution

This document evolves at phase transitions and milestone boundaries.

**After each phase transition** (via `/gsd-transition`):
1. Requirements invalidated? → Move to Out of Scope with reason
2. Requirements validated? → Move to Validated with phase reference
3. New requirements emerged? → Add to Active
4. Decisions to log? → Add to Key Decisions
5. "What This Is" still accurate? → Update if drifted

**After each milestone** (via `/gsd-complete-milestone`):
1. Full review of all sections
2. Core Value check — still the right priority?
3. Audit Out of Scope — reasons still valid?
4. Update Context with current state

---
*Last updated: 2026-04-28 after v2.0 milestone start*
