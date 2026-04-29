# Phase 5: Foundation & Contracts - Context

**Gathered:** 2026-04-28
**Status:** Ready for planning

<domain>
## Phase Boundary

Typed agent protocol (Observation/Recommendation dataclasses), agent registry with register/unregister/health-check, and shared JSON state with file locking for concurrent access. This is the foundation all 5 domain agents (Phase 6) and the coordinator rewrite (Phase 8) will build on. No new agent behavior — just the contracts and infrastructure.

</domain>

<decisions>
## Implementation Decisions

### Protocol Design
- **D-01:** Agents declare ownership via keyword sets (existing pattern — `OWNERSHIP = {"container", "gpu", ...}`). Protocol formalizes return types, not ownership mechanism.
- **D-02:** Observation and Recommendation are flat dataclasses with minimal fields. Observation: agent_name, domain, summary, confidence, details(dict), timestamp. Recommendation: action, target, risk_level, requires_approval(bool), rationale. No nested typed Details per domain.
- **D-03:** Each agent declares `PREFERRED_ROLE = "reason"` (or fast/code/heavy/etc.) as a module-level constant. Registry reads it at registration time.
- **D-04:** Single entry point: `handle(query)` returns `(Observation, list[Recommendation])`. No separate observe()/recommend() phases. Agents internally separate logic however they want.
- **D-05:** Duck-typing with validation — no base class (no ABC). Registry validates at registration time that the module has required functions (`owns`, `handle`) and `PREFERRED_ROLE` constant.

### Registry Behavior
- **D-06:** Lazy loading on first use via `importlib.import_module()`. If import fails, registry marks agent as unhealthy and skips it. Matches existing `agent_coordinator.py` pattern.
- **D-07:** Error isolation via try/except per agent.handle() call. On error: log, return None for that agent, continue with others. Agent stays registered but gets a failure counter.
- **D-08:** Health checks are registration-time validation only. No ongoing background health probes. Failures at query time handled by error isolation (D-07).

### Shared State & Locking
- **D-09:** Per-agent state files: `state/agents/infra.json`, `personal.json`, etc. A shared index (`agents_registry.json`) tracks who's registered. Minimizes lock contention.
- **D-10:** fcntl.flock() for advisory locking inside Docker container (Linux). On Windows (dev/test), fall back to write_atomic() only (no locking). Concurrency only matters in production container.
- **D-11:** Cross-agent reads via direct load_json() of other agent's state file. No API layer. Files are the API. Phase 7 adds structured Observation caching on top.

### Migration Strategy
- **D-12:** Refactor existing agent_*.py files in place to conform to new protocol (add PREFERRED_ROLE, return Observation/Recommendation from handle()). Keep working logic, standardize interface.
- **D-13:** Update test_sprint9.py alongside code to expect new typed returns (Observation/Recommendation) instead of loose dicts. Same file, updated assertions.
- **D-14:** Create two new files: `agent_protocol.py` (dataclasses + validation helpers) and `agent_registry.py` (lifecycle management). Existing `agent_coordinator.py` stays but delegates to registry.

### Claude's Discretion
- Exact field names and default values for Observation/Recommendation dataclasses
- Whether to add a `to_dict()` method on dataclasses (likely yes, existing NormalizedCall has one)
- Failure counter implementation details (simple int vs rolling window)
- Exact fcntl locking wrapper API (context manager vs explicit lock/unlock)
- How agent_coordinator.py delegates to the new registry (thin wrapper vs full refactor — Phase 8 does the real rewrite)

</decisions>

<canonical_refs>
## Canonical References

**Downstream agents MUST read these before planning or implementing.**

### Requirements
- `.planning/REQUIREMENTS.md` — FRMK-01 through FRMK-04 (agent framework), INTG-02 (shared state with locking)
- `.planning/ROADMAP.md` §Phase 5 — Success criteria (5 items)

### Existing Agent Code (refactor targets)
- `workspace/runtime/agent_coordinator.py` — Current coordinator with lazy imports, owns() checks, route(), identify_agents()
- `workspace/runtime/agent_infra.py` — Reference agent with OWNERSHIP set, owns(), assess(), handle_query()
- `workspace/runtime/agent_arbitrator.py` — Priority-based conflict resolution (Phase 7 concern, but avoid breaking)

### Shared Utilities
- `workspace/runtime/runtime_utils.py` — write_atomic(), load_json(), save_json() — foundation for state persistence
- `workspace/runtime/intent_normalizer.py` — NormalizedCall dataclass as pattern reference for Observation/Recommendation

### Project Decisions
- `.planning/PROJECT.md` §Key Decisions — "Shared JSON state for agent comms", "Observe + recommend only"

</canonical_refs>

<code_context>
## Existing Code Insights

### Reusable Assets
- `runtime_utils.write_atomic()`: Crash-safe JSON writes via tempfile + os.replace. Use as base for locked writes.
- `runtime_utils.load_json()` / `save_json()`: Standard JSON I/O. Agents should migrate from private `_load()/_save()` to these.
- `NormalizedCall` dataclass in `intent_normalizer.py`: Pattern for Observation/Recommendation — flat dataclass with `to_dict()`.
- `agent_coordinator._load_agent()`: Lazy importlib pattern to reuse in registry.

### Established Patterns
- Module-level constants for config: `OWNERSHIP`, `BLOCKED_TARGETS`, `PRIORITY` — each agent already follows this.
- Private `_log()` helper per module: 3-4 lines writing JSONL to module-specific log file.
- `BASE_DIR = Path(__file__).parent` and `STATE_DIR` path resolution pattern used by all agents.
- No type hints anywhere — dynamic Python throughout. Dataclasses are the closest to typed contracts.

### Integration Points
- `agent_coordinator.py` line 88-99: `identify_agents()` loops over AGENT_MODULES calling `agent.owns()` — registry replaces this loop.
- `agent_coordinator.py` line 102-146: `route()` calls `agent.handle_query()` with try/except — registry formalizes this error handling.
- `model_router.py`: `route_by_role()` accepts role string — agents' PREFERRED_ROLE feeds into this.
- `state/agents/` directory: Already used by existing agents for per-agent state files.

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

*Phase: 05-foundation-contracts*
*Context gathered: 2026-04-28*
