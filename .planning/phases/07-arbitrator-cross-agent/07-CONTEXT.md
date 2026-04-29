# Phase 7: Arbitrator & Cross-Agent - Context

**Gathered:** 2026-04-29
**Status:** Ready for planning

<domain>
## Phase Boundary

Conflict resolution when 2+ agents claim a query, cross-agent context sharing via cached observations, and proactive cron-based observations. Builds on Phase 5 protocol (Observation/Recommendation) and Phase 6 domain agents (5 agents with query-responsive handle()). Does NOT touch the coordinator routing — that's Phase 8.

</domain>

<decisions>
## Implementation Decisions

### Conflict Scoring Strategy
- **D-01:** Keep existing static priority tiers from agent_arbitrator.py (safety=0, infra_critical=1, personal_active=2, finance_extreme=3, infra=4, personal=5, finance=6, research=7, self_improve=8). Do NOT replace with weighted composite scoring.
- **D-02:** When two agents have the same effective priority, break ties using Observation.confidence (0.0-1.0). Higher confidence wins. No historical accuracy tracking needed.
- **D-03:** Context-aware priority boosts remain as-is: infra+critical→1, personal+calendar_active→2, finance+variation>10%→3.

### Escalation Behavior
- **D-04:** When agents tie (same priority AND same confidence) or produce contradictory recommendations (opposite actions on same target), escalate immediately to Telegram using exec_approvals.py inline keyboard buttons.
- **D-05:** Escalation message shows both agents' observations + recommendations side by side. Sebas taps to choose the winner.
- **D-06:** 5-minute timeout on escalation. If no response, default to the agent with higher static priority (lower number wins).

### Cross-Agent Observation Cache
- **D-07:** Each agent writes its latest observation to its own per-agent state file (`state/agents/{name}.json`) under an `observation` key. Consistent with Phase 5 decisions D-09 and D-11.
- **D-08:** Other agents read cross-agent observations via `load_json()` of other agent's state file. No API layer — files are the API.
- **D-09:** Cached observations have 10-minute TTL. Observation older than 10 minutes is considered stale and ignored.

### Proactive Cron Observations
- **D-10:** All 5 agents (infra, personal, finance, research, self_improve) run proactively every 5 minutes, piggybacking on the existing runtime.py cron cycle.
- **D-11:** Each proactive run calls `handle("")` (empty query) which triggers the agent's `assess()` function (generic status check), writing the observation to the per-agent state file.
- **D-12:** When a Telegram query arrives, the coordinator reads cached observations first. If cache is fresh (<10min), use it immediately. If stale, call `handle(query)` for a live observation.

### Claude's Discretion
- How to structure the escalation message format (exact Telegram markdown)
- Whether to add a `last_observation_ts` field to the per-agent state file or derive staleness from the Observation.timestamp
- How to integrate proactive observation into the existing worker_supervisor.py cycle vs runtime.py
- Exact conflict detection logic (when to consider two recommendations "contradictory")

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Arbitrator (refactor target)
- `workspace/runtime/agent_arbitrator.py` — Existing priority-based conflict resolution with PRIORITY dict, _get_effective_priority(), postpone logic

### Protocol & Registry (Phase 5)
- `workspace/runtime/agent_protocol.py` — Observation/Recommendation dataclasses, validation helpers
- `workspace/runtime/agent_registry.py` — Agent lifecycle, register/unregister, health checks

### Telegram Approvals
- `workspace/runtime/exec_approvals.py` — send_approval_request(), handle_callback_query(), inline keyboard buttons

### Agent State Files
- `workspace/runtime/runtime_utils.py` — load_json(), save_json(), write_atomic(), locked_write_json()

### Domain Agents (Phase 6 — all 5)
- `workspace/runtime/agent_infra.py` — Infrastructure agent with OWNERSHIP, handle(), _observe_* functions
- `workspace/runtime/agent_personal.py` — Personal agent
- `workspace/runtime/agent_finance.py` — Finance agent
- `workspace/runtime/agent_research.py` — Research agent
- `workspace/runtime/agent_self_improve.py` — Self-improvement agent

### Cron Integration
- `workspace/runtime/runtime.py` — Main 5-min event loop
- `workspace/runtime/worker_supervisor.py` — Worker subprocess isolation

### Requirements
- `.planning/REQUIREMENTS.md` — ARBT-01..03 (arbitrator), AGNT-06..07 (cross-agent)
- `.planning/ROADMAP.md` §Phase 7 — Success criteria (5 items)

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `agent_arbitrator.py`: Already has PRIORITY dict, _get_effective_priority() with context boosts, _conflict_id() for dedup, _load/_save helpers, JSONL logging
- `exec_approvals.py`: send_approval_request(summary, details, skill) with Approve/Cancel/View Details buttons, poll_approval(id) for status check
- `runtime_utils.py`: locked_write_json() for concurrent state writes

### Established Patterns
- Per-agent state files at `state/agents/{name}.json` — already used by all 5 agents
- JSONL logging with `_log()` helper and `now().isoformat()` timestamp
- Error isolation: try/except per operation, degrade gracefully

### Integration Points
- `agent_coordinator.py` line 88-99: `identify_agents()` returns list of claiming agents — this is where the arbitrator intercepts when >1 agent claims
- `runtime.py`: main cron loop where proactive observations can be inserted
- `worker_supervisor.py`: alternative integration point for proactive observations as a dedicated worker

</code_context>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 07-arbitrator-cross-agent*
*Context gathered: 2026-04-29*
