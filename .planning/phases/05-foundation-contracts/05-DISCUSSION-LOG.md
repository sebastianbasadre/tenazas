# Phase 5: Foundation & Contracts - Discussion Log

> **Audit trail only.** Do not use as input to planning, research, or execution agents.
> Decisions are captured in CONTEXT.md — this log preserves the alternatives considered.

**Date:** 2026-04-28
**Phase:** 05-foundation-contracts
**Areas discussed:** Protocol design, Registry behavior, Shared state & locking, Migration from existing agents

---

## Protocol Design

### Ownership mechanism

| Option | Description | Selected |
|--------|-------------|----------|
| Keyword ownership | Keep existing pattern — each agent declares keyword set via owns(). Protocol formalizes return types. | ✓ |
| Score-based ownership | Each agent returns confidence score (0-1). Coordinator picks highest. More flexible but needs calibration. | |
| You decide | Claude picks based on codebase patterns. | |

**User's choice:** Keyword ownership
**Notes:** Proven pattern already in use across all 5 agents.

### Observation/Recommendation structure

| Option | Description | Selected |
|--------|-------------|----------|
| Flat and minimal | Observation: agent_name, domain, summary, confidence, details(dict), timestamp. Recommendation: action, target, risk_level, requires_approval, rationale. | ✓ |
| Nested with metadata | Observation wraps typed Details union per domain. More type-safe but complex. | |
| You decide | Claude picks based on project style. | |

**User's choice:** Flat and minimal
**Notes:** Mirrors existing dict patterns but typed via dataclasses.

### LLM role declaration

| Option | Description | Selected |
|--------|-------------|----------|
| Class-level constant | PREFERRED_ROLE = 'reason' at module level. Registry reads it. | ✓ |
| Method on protocol | get_preferred_role() method. More flexible but over-engineered. | |
| You decide | Claude picks simplest. | |

**User's choice:** Class-level constant

### Contract entry point

| Option | Description | Selected |
|--------|-------------|----------|
| observe() + recommend() | Two-phase contract separating analysis from action proposal. | |
| Single handle(query) | One entry point returning (Observation, list[Recommendation]). Simpler. | ✓ |
| You decide | Claude picks. | |

**User's choice:** Single handle(query)

### Base class vs duck-typing

| Option | Description | Selected |
|--------|-------------|----------|
| Duck-typing + validation | No base class. Registry validates required functions at registration. | ✓ |
| Minimal base class | AgentBase with NotImplementedError stubs. IDE autocomplete but against conventions. | |
| You decide | Claude picks. | |

**User's choice:** Duck-typing + validation

---

## Registry Behavior

### Agent loading

| Option | Description | Selected |
|--------|-------------|----------|
| Lazy on first use | Import agent module only when first queried. Mark unhealthy on import failure. | ✓ |
| Eager at startup | Import all agents at init. Catches errors early but slows startup. | |
| You decide | Claude picks. | |

**User's choice:** Lazy on first use

### Error isolation

| Option | Description | Selected |
|--------|-------------|----------|
| Try/except + skip | Wrap handle() in try/except. Log, return None, continue with others. Failure counter. | ✓ |
| Circuit breaker | Temporarily disable after N failures. More robust but complex. | |
| You decide | Claude picks. | |

**User's choice:** Try/except + skip

### Health checks

| Option | Description | Selected |
|--------|-------------|----------|
| Registration-time validation only | Validate required functions exist when registering. No ongoing probes. | ✓ |
| Periodic background probes | Registry calls health() every N minutes. Requires background thread. | |
| You decide | Claude picks. | |

**User's choice:** Registration-time validation only

---

## Shared State & Locking

### State file organization

| Option | Description | Selected |
|--------|-------------|----------|
| Per-agent files | Each agent gets own file. Shared index tracks registry. Minimal lock contention. | ✓ |
| Single shared file | All state in one file. Simpler to read but high contention. | |
| You decide | Claude picks. | |

**User's choice:** Per-agent files

### Locking strategy

| Option | Description | Selected |
|--------|-------------|----------|
| fcntl in container, skip on Windows | Advisory locking in Docker (Linux). write_atomic() only on Windows dev. | ✓ |
| Cross-platform with filelock package | python-filelock for both. But violates no-new-deps constraint. | |
| You decide | Claude picks. | |

**User's choice:** fcntl in container, skip on Windows

### Cross-agent reads

| Option | Description | Selected |
|--------|-------------|----------|
| Read other agent's state file directly | Direct load_json(). Files are the API. Phase 7 adds caching. | ✓ |
| State manager API | shared_state.py mediates all reads/writes. Adds indirection. | |
| You decide | Claude picks. | |

**User's choice:** Read other agent's state file directly

---

## Migration from Existing Agents

### Migration approach

| Option | Description | Selected |
|--------|-------------|----------|
| Refactor in place | Modify existing agent_*.py to conform to new protocol. Keep working logic. | ✓ |
| New modules, deprecate old | Create agent_v2_*.py. Clean but doubles file count. | |
| You decide | Claude picks. | |

**User's choice:** Refactor in place

### Test strategy

| Option | Description | Selected |
|--------|-------------|----------|
| Update tests alongside code | Modify test_sprint9.py for new typed returns. Tests evolve with code. | ✓ |
| New test suite, keep old | New test_agent_protocol.py. Old tests break until Phase 6. | |
| You decide | Claude picks. | |

**User's choice:** Update tests alongside code

### File layout

| Option | Description | Selected |
|--------|-------------|----------|
| New files: agent_protocol.py + agent_registry.py | Protocol defines dataclasses, registry manages lifecycle. Two focused files. | ✓ |
| Extend agent_coordinator.py | Add protocol + registry into existing coordinator. Fewer files but muddied responsibility. | |
| You decide | Claude picks. | |

**User's choice:** New files: agent_protocol.py + agent_registry.py

---

## Claude's Discretion

- Exact field names and default values for Observation/Recommendation dataclasses
- Whether to add to_dict() on dataclasses
- Failure counter implementation (simple int vs rolling window)
- fcntl locking wrapper API (context manager vs explicit)
- How agent_coordinator.py delegates to new registry

## Deferred Ideas

None — discussion stayed within phase scope.
