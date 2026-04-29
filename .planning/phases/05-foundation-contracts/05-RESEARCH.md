# Phase 5: Foundation & Contracts - Research

**Researched:** 2026-04-28
**Domain:** Python dataclasses, agent protocol design, file locking, registry patterns
**Confidence:** HIGH

## Summary

Phase 5 creates the typed foundation for the multi-agent system: two dataclasses (`Observation`, `Recommendation`), an agent registry with lifecycle management, and file-locking primitives for concurrent JSON state access. All five existing agent modules (`agent_infra.py`, `agent_personal.py`, `agent_finance.py`, `agent_research.py`, `agent_self_improve.py`) already follow a consistent pattern with `OWNERSHIP` sets, `owns()`, and `handle_query()` functions. The refactor standardizes their return types and adds `PREFERRED_ROLE` constants.

The codebase already has a working `fcntl.flock()` implementation in `proposal_executor.py` (Lock class, lines 180-238) that handles stale detection, PID tracking, and context manager usage. The `write_atomic()` function in `runtime_utils.py` provides crash-safe writes via `tempfile + os.replace()`. The locking layer for agent state combines both: `fcntl.flock()` for mutual exclusion + `write_atomic()` for crash safety.

**Primary recommendation:** Create `agent_protocol.py` with flat dataclasses following the `NormalizedCall` pattern from `intent_normalizer.py`, and `agent_registry.py` with lazy-load + duck-type validation. Refactor each agent in-place to add `PREFERRED_ROLE` and convert `handle_query()` to `handle()` returning `(Observation, list[Recommendation])`.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **D-01:** Agents declare ownership via keyword sets (existing pattern -- `OWNERSHIP = {"container", "gpu", ...}`). Protocol formalizes return types, not ownership mechanism.
- **D-02:** Observation and Recommendation are flat dataclasses with minimal fields. Observation: agent_name, domain, summary, confidence, details(dict), timestamp. Recommendation: action, target, risk_level, requires_approval(bool), rationale. No nested typed Details per domain.
- **D-03:** Each agent declares `PREFERRED_ROLE = "reason"` (or fast/code/heavy/etc.) as a module-level constant. Registry reads it at registration time.
- **D-04:** Single entry point: `handle(query)` returns `(Observation, list[Recommendation])`. No separate observe()/recommend() phases.
- **D-05:** Duck-typing with validation -- no base class (no ABC). Registry validates at registration time that the module has required functions (`owns`, `handle`) and `PREFERRED_ROLE` constant.
- **D-06:** Lazy loading on first use via `importlib.import_module()`. If import fails, registry marks agent as unhealthy and skips it.
- **D-07:** Error isolation via try/except per agent.handle() call. On error: log, return None for that agent, continue with others. Agent stays registered but gets a failure counter.
- **D-08:** Health checks are registration-time validation only. No ongoing background health probes.
- **D-09:** Per-agent state files: `state/agents/infra.json`, `personal.json`, etc. A shared index (`agents_registry.json`) tracks who's registered.
- **D-10:** fcntl.flock() for advisory locking inside Docker container (Linux). On Windows (dev/test), fall back to write_atomic() only (no locking).
- **D-11:** Cross-agent reads via direct load_json() of other agent's state file. No API layer.
- **D-12:** Refactor existing agent_*.py files in place to conform to new protocol.
- **D-13:** Update test_sprint9.py alongside code to expect new typed returns.
- **D-14:** Create two new files: `agent_protocol.py` and `agent_registry.py`.

### Claude's Discretion
- Exact field names and default values for Observation/Recommendation dataclasses
- Whether to add a `to_dict()` method on dataclasses (likely yes)
- Failure counter implementation details (simple int vs rolling window)
- Exact fcntl locking wrapper API (context manager vs explicit lock/unlock)
- How agent_coordinator.py delegates to the new registry

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| FRMK-01 | Typed agent protocol with observe(), recommend(), owns() contract via dataclasses | Observation and Recommendation dataclasses in agent_protocol.py; NormalizedCall pattern verified in intent_normalizer.py (line 19-41) |
| FRMK-02 | Agent registry with register/unregister, health checks, graceful degradation on failure | agent_registry.py with lazy importlib loading (pattern from agent_coordinator.py line 61-66), duck-type validation at registration |
| FRMK-03 | Error isolation -- each agent runs in try/except, one crash never affects others | try/except per agent.handle() in registry dispatch; existing pattern in agent_coordinator.py line 117-124 |
| FRMK-04 | Per-agent model routing -- agents specify preferred LLM role for domain queries | PREFERRED_ROLE module-level constant; model_router.route_by_role() accepts role string |
| INTG-02 | Shared JSON state with file locking (fcntl) and atomic writes for concurrent access | fcntl.flock() pattern from proposal_executor.py (line 180-238) + write_atomic() from runtime_utils.py (line 53-72) |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Dataclass definitions (Observation, Recommendation) | Runtime Python layer | -- | Pure data contracts, no I/O |
| Agent registry (register, unregister, list, dispatch) | Runtime Python layer | -- | In-process module management |
| Duck-type validation | Runtime Python layer | -- | Registration-time checks using hasattr/callable |
| File locking (fcntl.flock) | Docker container (Linux) | -- | Advisory locks on Linux filesystem |
| Atomic writes (write_atomic) | Runtime Python layer | -- | Cross-platform, already in runtime_utils.py |
| Agent state persistence | Database/Storage (JSON files) | -- | Per-agent JSON files in state/agents/ |
| LLM role declaration | Runtime Python layer | API/Backend (model_router) | Agent declares, router consumes |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| dataclasses | stdlib | Observation/Recommendation typed contracts | Already used in intent_normalizer.py, action_validator.py, policy_engine.py [VERIFIED: codebase grep] |
| importlib | stdlib | Lazy agent module loading | Already used in agent_coordinator.py line 64 [VERIFIED: codebase grep] |
| fcntl | stdlib (Linux) | Advisory file locking for concurrent state writes | Already used in proposal_executor.py line 31, 193-224 [VERIFIED: codebase grep] |
| tempfile | stdlib | Atomic write temp files | Already used in runtime_utils.py write_atomic() [VERIFIED: codebase grep] |
| json | stdlib | State serialization | Ubiquitous in codebase [VERIFIED: codebase grep] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| typing | stdlib | Optional, Dict type hints for dataclass fields | Minimal -- codebase has no type hints convention |
| pathlib | stdlib | Path resolution for state files | Already standard pattern across all agents [VERIFIED: codebase grep] |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| dataclasses | TypedDict | TypedDict is dict-compatible but no methods (to_dict), no defaults syntax, less readable |
| fcntl.flock | filelock (pip) | External dep; project constraint is "no new dependencies" |
| importlib | direct import | Direct import causes circular deps and fails hard; lazy loading is the existing pattern |

**Installation:**
```bash
# No installation needed -- all stdlib
```

## Architecture Patterns

### System Architecture Diagram

```
Telegram query
    |
    v
agent_coordinator.py (existing, delegates to registry)
    |
    v
agent_registry.py (NEW)
    |
    +-- register(name, module_path) --> importlib.import_module()
    |       |                              |
    |       +-- validate: has owns(), handle(), PREFERRED_ROLE
    |       +-- store in _registry dict + persist to agents_registry.json
    |
    +-- dispatch(query) --> for each agent where owns(query):
    |       |
    |       +-- try: agent.handle(query) --> (Observation, list[Recommendation])
    |       +-- except: log, increment failure_count, return None
    |
    +-- list_agents() --> [{name, status, preferred_role, failure_count}]
    |
    +-- unregister(name) --> remove from _registry + update agents_registry.json

agent_protocol.py (NEW)
    |
    +-- @dataclass Observation: agent_name, domain, summary, confidence, details, timestamp
    +-- @dataclass Recommendation: action, target, risk_level, requires_approval, rationale
    +-- validate_agent_module(module) --> bool (duck-type checks)

State Layer:
    state/agents/
        +-- agents_registry.json   <-- shared index (locked writes)
        +-- infra.json             <-- per-agent state
        +-- personal.json
        +-- finance.json
        +-- research.json
        +-- self_improve.json
```

### Recommended Project Structure
```
workspace/runtime/
├── agent_protocol.py      # NEW: Observation, Recommendation, validate_agent_module()
├── agent_registry.py      # NEW: AgentRegistry class with register/unregister/dispatch
├── agent_coordinator.py   # MODIFIED: delegates to registry instead of manual loop
├── agent_infra.py         # MODIFIED: adds PREFERRED_ROLE, handle() returns typed tuple
├── agent_personal.py      # MODIFIED: adds PREFERRED_ROLE, handle() returns typed tuple
├── agent_finance.py       # MODIFIED: adds PREFERRED_ROLE, handle() returns typed tuple
├── agent_research.py      # MODIFIED: adds PREFERRED_ROLE, handle() returns typed tuple
├── agent_self_improve.py  # MODIFIED: adds PREFERRED_ROLE, handle() returns typed tuple
├── agent_arbitrator.py    # UNTOUCHED (Phase 7 concern)
├── runtime_utils.py       # EXTENDED: add locked_write_json() wrapper
└── ...
```

### Pattern 1: Flat Dataclass with to_dict()

**What:** Observation and Recommendation as flat dataclasses matching the NormalizedCall pattern.
**When to use:** All agent return types.
**Example:**
```python
# Source: intent_normalizer.py lines 19-41 (existing pattern in codebase)
from dataclasses import dataclass, field, asdict
from datetime import datetime

@dataclass
class Observation:
    agent_name: str
    domain: str
    summary: str
    confidence: float = 0.0          # 0.0 - 1.0
    details: dict = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().astimezone().isoformat()

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Recommendation:
    action: str
    target: str
    risk_level: str = "low"          # low / medium / high / critical
    requires_approval: bool = True   # safe default: always require approval
    rationale: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
```

### Pattern 2: Duck-Type Validation at Registration

**What:** Check module interface without requiring inheritance.
**When to use:** `agent_registry.register()` calls this.
**Example:**
```python
# Source: derived from D-05 decision + existing hasattr checks in agent_coordinator.py line 90
def validate_agent_module(module) -> tuple:
    """Validate module has required protocol interface.

    Returns (ok: bool, errors: list[str])
    """
    errors = []
    if not hasattr(module, "owns") or not callable(module.owns):
        errors.append("missing owns(topic) function")
    if not hasattr(module, "handle") or not callable(module.handle):
        errors.append("missing handle(query) function")
    if not hasattr(module, "PREFERRED_ROLE"):
        errors.append("missing PREFERRED_ROLE constant")
    elif module.PREFERRED_ROLE not in ("fast", "reason", "code", "heavy", "vision", "embed"):
        errors.append(f"invalid PREFERRED_ROLE: {module.PREFERRED_ROLE}")
    return (len(errors) == 0, errors)
```

### Pattern 3: fcntl Locking with write_atomic Fallback

**What:** Context manager for locked JSON writes. fcntl on Linux, write_atomic-only on Windows.
**When to use:** Writing to shared state files (agents_registry.json, per-agent state).
**Example:**
```python
# Source: proposal_executor.py lines 180-238 (existing pattern) + D-10 decision
import os
import json
import tempfile
from pathlib import Path

try:
    import fcntl
    HAS_FCNTL = True
except ImportError:
    HAS_FCNTL = False

class StateLock:
    """Advisory file lock for agent state. Context manager."""

    def __init__(self, path):
        self.path = Path(path)
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")
        self.fh = None

    def __enter__(self):
        if HAS_FCNTL:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            self.fh = open(self.lock_path, "a+")
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if self.fh:
            try:
                fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self.fh.close()
            self.fh = None
        return False

    def write(self, data):
        """Write JSON atomically while holding the lock."""
        from runtime_utils import write_atomic
        write_atomic(self.path, data)

    def read(self):
        """Read JSON (no lock needed for reads)."""
        from runtime_utils import load_json
        return load_json(self.path)
```

### Pattern 4: Lazy Loading with Error Isolation

**What:** Import agent modules on first use, catch import errors gracefully.
**When to use:** Registry loading agents.
**Example:**
```python
# Source: agent_coordinator.py lines 61-66 (existing pattern)
import importlib

def _load_agent(module_path: str):
    """Lazily import an agent module. Returns module or None."""
    try:
        return importlib.import_module(module_path)
    except Exception as e:
        _log({"action": "load_error", "module": module_path, "error": str(e)[:200]})
        return None
```

### Pattern 5: Agent Migration (handle_query -> handle)

**What:** Existing agents get `PREFERRED_ROLE` and `handle()` that wraps existing logic.
**When to use:** Each agent_*.py refactor.
**Example:**
```python
# Migration pattern for agent_infra.py
from agent_protocol import Observation, Recommendation

PREFERRED_ROLE = "fast"  # NEW: infra checks are quick

# EXISTING: owns() stays exactly the same
def owns(topic: str) -> bool:
    return any(k in topic.lower() for k in OWNERSHIP)

# NEW: handle() wraps existing assess() + handle_query() logic
def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    status = assess()
    obs = Observation(
        agent_name="infra",
        domain="infrastructure",
        summary=f"Infra: {status['health']}",
        confidence=0.9 if status["health"] == "ok" else 0.7,
        details=status,
    )
    recs = []
    for prop in status.get("proposals", []):
        recs.append(Recommendation(
            action=prop["action"],
            target=prop.get("target", ""),
            risk_level=prop.get("severity", "low"),
            requires_approval=True,
            rationale=prop.get("reason", ""),
        ))
    return (obs, recs)

# KEEP: handle_query() stays for backward compatibility during transition
def handle_query(query: str) -> str:
    obs, recs = handle(query)
    return obs.summary
```

### Anti-Patterns to Avoid
- **ABC/base class for agents:** Decision D-05 explicitly chose duck-typing. Do NOT create `class BaseAgent(ABC)`. Validation is at registration time.
- **Global lock for all agents:** Decision D-09 specifies per-agent state files. One lock per file, not one global lock.
- **Removing handle_query():** Keep it as backward-compat wrapper during transition. agent_coordinator.py and test_sprint9.py may still call it.
- **Type hints everywhere:** Codebase has no type hints convention. Use them only in dataclass fields and function signatures of new files.
- **Importing agent_protocol at module level in agents:** Use lazy import to avoid circular deps if protocol imports anything from runtime.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Atomic file writes | Custom temp+rename | `runtime_utils.write_atomic()` | Already battle-tested, handles crash recovery |
| JSON load with defaults | Per-module `_load()` | `runtime_utils.load_json()` | 5 agents each have private `_load()` -- consolidate |
| JSON save with dirs | Per-module `_save()` | `runtime_utils.save_json()` | 5 agents each have private `_save()` -- consolidate |
| File locking | Custom flock wrapper | Adapt `proposal_executor.Lock` pattern | Already handles stale detection, PID tracking |
| Module lazy loading | Custom import cache | `importlib.import_module()` | stdlib, already used in agent_coordinator.py |
| Dict serialization | Manual dict building | `dataclasses.asdict()` | stdlib, handles nested dataclasses automatically |

**Key insight:** Five agents each have identical private `_load()`, `_save()`, `_log()` helpers. Phase 5 consolidates to `runtime_utils` functions, reducing ~60 lines of duplicated code.

## Common Pitfalls

### Pitfall 1: Breaking handle_query() Callers
**What goes wrong:** Renaming `handle_query()` to `handle()` breaks `agent_coordinator.py` and `test_sprint9.py` which call `handle_query()`.
**Why it happens:** Eager cleanup during refactor.
**How to avoid:** Keep `handle_query()` as a thin wrapper that calls `handle()` and returns the string form. Update callers in the same commit.
**Warning signs:** ImportError or AttributeError in test_sprint9.py tests.

### Pitfall 2: fcntl Import on Windows
**What goes wrong:** `import fcntl` raises ImportError on Windows (dev machine is Windows 11).
**Why it happens:** fcntl is Linux-only stdlib module.
**How to avoid:** Use `try: import fcntl; HAS_FCNTL = True except ImportError: HAS_FCNTL = False` pattern. Tests must work on Windows.
**Warning signs:** Tests failing on Windows but passing in Docker.

### Pitfall 3: Circular Import Between protocol and agents
**What goes wrong:** `agent_protocol.py` imports something from runtime that imports an agent.
**Why it happens:** agent_protocol.py should be a leaf module with zero runtime imports.
**How to avoid:** `agent_protocol.py` imports ONLY `dataclasses`, `datetime`, `typing` from stdlib. Nothing from runtime.
**Warning signs:** ImportError when first agent tries to import agent_protocol.

### Pitfall 4: Lock Contention on agents_registry.json
**What goes wrong:** Multiple cron processes try to register agents simultaneously, blocking.
**Why it happens:** runtime.py runs every 5 min, worker_supervisor.py may overlap.
**How to avoid:** Registry loads from file at startup but caches in memory. Writes only on register/unregister (rare operations). Use non-blocking lock with retry for registry writes.
**Warning signs:** Runtime loop hanging at agent load step.

### Pitfall 5: Observation Confidence Inconsistency
**What goes wrong:** Different agents use different scales (0-1 vs 0-100 vs "high/medium/low").
**Why it happens:** No validated range on the confidence field.
**How to avoid:** Add `__post_init__` validation: `assert 0.0 <= self.confidence <= 1.0`. Document in docstring.
**Warning signs:** Arbitrator comparing 0.8 (infra) with 85 (finance).

### Pitfall 6: Breaking agent_arbitrator.py
**What goes wrong:** Arbitrator expects old-style proposals dict, gets Recommendation dataclass.
**Why it happens:** Phase 5 changes agent return types but arbitrator refactor is Phase 7.
**How to avoid:** Arbitrator stays untouched in Phase 5. The coordinator converts Recommendations to the dict format arbitrator expects. Or use `to_dict()`.
**Warning signs:** TypeError in agent_arbitrator.resolve().

## Code Examples

### Complete Observation and Recommendation Dataclasses

```python
# agent_protocol.py -- complete implementation
from dataclasses import dataclass, field, asdict
from datetime import datetime

VALID_ROLES = ("fast", "reason", "code", "heavy", "vision", "embed")
VALID_RISK_LEVELS = ("low", "medium", "high", "critical")


@dataclass
class Observation:
    agent_name: str
    domain: str
    summary: str
    confidence: float = 0.0
    details: dict = field(default_factory=dict)
    timestamp: str = ""

    def __post_init__(self):
        if not self.timestamp:
            self.timestamp = datetime.now().astimezone().isoformat()
        self.confidence = max(0.0, min(1.0, float(self.confidence)))

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class Recommendation:
    action: str
    target: str
    risk_level: str = "low"
    requires_approval: bool = True
    rationale: str = ""

    def __post_init__(self):
        if self.risk_level not in VALID_RISK_LEVELS:
            self.risk_level = "low"

    def to_dict(self) -> dict:
        return asdict(self)


def validate_agent_module(module) -> tuple:
    """Returns (ok, errors_list)."""
    errors = []
    if not hasattr(module, "owns") or not callable(module.owns):
        errors.append("missing owns(topic)")
    if not hasattr(module, "handle") or not callable(module.handle):
        errors.append("missing handle(query)")
    if not hasattr(module, "PREFERRED_ROLE"):
        errors.append("missing PREFERRED_ROLE")
    elif module.PREFERRED_ROLE not in VALID_ROLES:
        errors.append(f"invalid PREFERRED_ROLE: {module.PREFERRED_ROLE}")
    return (len(errors) == 0, errors)
```

### Registry Core Logic

```python
# agent_registry.py -- core dispatch pattern
import importlib
import json
from pathlib import Path
from runtime_utils import load_json, now
from agent_protocol import validate_agent_module

_registry = {}  # name -> {module, preferred_role, failure_count, status}

def register(name, module_path):
    """Load and validate an agent module."""
    try:
        module = importlib.import_module(module_path)
    except Exception as e:
        _registry[name] = {"module": None, "status": "import_error",
                           "error": str(e)[:200], "failure_count": 0}
        return False
    ok, errors = validate_agent_module(module)
    if not ok:
        _registry[name] = {"module": None, "status": "invalid",
                           "errors": errors, "failure_count": 0}
        return False
    _registry[name] = {
        "module": module,
        "preferred_role": module.PREFERRED_ROLE,
        "status": "healthy",
        "failure_count": 0,
    }
    return True

def unregister(name):
    """Remove agent from registry."""
    _registry.pop(name, None)

def dispatch(query):
    """Route query to matching agents with error isolation."""
    results = []
    for name, entry in _registry.items():
        if entry["status"] != "healthy" or entry["module"] is None:
            continue
        if not entry["module"].owns(query):
            continue
        try:
            obs, recs = entry["module"].handle(query)
            results.append((name, obs, recs))
        except Exception as e:
            entry["failure_count"] += 1
            _log({"action": "handle_error", "agent": name,
                  "error": str(e)[:200], "failures": entry["failure_count"]})
            results.append((name, None, []))
    return results

def list_agents():
    """List all registered agents with status."""
    return [{"name": n, "status": e["status"],
             "preferred_role": e.get("preferred_role"),
             "failure_count": e["failure_count"]}
            for n, e in _registry.items()]
```

### Locked State Write

```python
# runtime_utils.py extension -- locked_write_json()
def locked_write_json(path, data):
    """Write JSON with advisory file lock (Linux) or atomic-only (Windows)."""
    path = Path(path)
    try:
        import fcntl
        lock_path = path.with_suffix(path.suffix + ".lock")
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        with open(lock_path, "a+") as fh:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
            try:
                write_atomic(path, data)
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
    except ImportError:
        # Windows fallback -- atomic write only, no locking
        write_atomic(path, data)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Private _load/_save per module | runtime_utils.load_json/save_json | Phase 1 (v1.0) | Consolidation available but not yet adopted by agents |
| Loose dicts from handle_query() | Typed Observation/Recommendation | Phase 5 (now) | Enables arbitrator scoring, coordinator aggregation |
| Manual importlib in coordinator | Registry with validation | Phase 5 (now) | Centralized lifecycle, error isolation |
| No file locking on state | fcntl.flock() advisory locking | Phase 5 (now) | Safe concurrent cron + worker writes |

**Deprecated/outdated:**
- Private `_load()` / `_save()` helpers in each agent: migrate to `runtime_utils.load_json()` / `save_json()` [VERIFIED: codebase grep shows 5 identical copies]
- `handle_query() -> str`: being replaced by `handle() -> (Observation, list[Recommendation])` but kept as backward-compat wrapper

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | `state/agents/` directory already exists in production container | Architecture Patterns | Low -- mkdir(parents=True) handles creation; existing agents already use AGENT_DIR |
| A2 | No other code besides agent_coordinator.py calls agent.handle_query() directly | Pitfall 1 | Medium -- if other callers exist, they break. Grep should catch this during planning |
| A3 | Runtime loop (cron 5-min) and worker_supervisor do not run truly simultaneously | Pitfall 4 | Low -- even if they do, fcntl handles it; but lock contention could slow things |

## Open Questions

1. **Should handle_query() wrapper return observation.summary or the full formatted text?**
   - What we know: Current handle_query() returns a formatted string with issues and proposals
   - What's unclear: Whether any caller parses the string format
   - Recommendation: Return `obs.summary` for simple callers; add `format_observation()` helper for rich output

2. **Failure counter: simple int or rolling window?**
   - What we know: D-07 says "failure counter" without specifying
   - What's unclear: Whether failures should expire (e.g., last 1 hour)
   - Recommendation: Start with simple int + reset on successful handle(). Phase 7 can add rolling window if needed.

3. **Should agents_registry.json persist across container restarts?**
   - What we know: It lives in state/agents/ which is a mounted volume
   - What's unclear: Whether the registry should auto-populate from AGENT_MODULES or require explicit registration
   - Recommendation: Auto-register all known agents on startup (from a hardcoded list matching current AGENT_MODULES), persist to JSON as audit trail. The JSON is descriptive, not prescriptive.

## Project Constraints (from CLAUDE.md)

- **No new dependencies:** All solutions use stdlib (dataclasses, fcntl, importlib, tempfile) [VERIFIED: CLAUDE.md constraint]
- **Container paths:** PYTHONPATH=/home/tenazas/runtime -- bare imports work (`from agent_protocol import Observation`) [VERIFIED: Dockerfile]
- **Subprocess safety:** Not applicable to this phase (no subprocess calls)
- **Imports:** Use `from runtime_utils import load_json, save_json, now` -- do NOT duplicate helpers [VERIFIED: CLAUDE.md coding rules]
- **json.dumps with datetime:** Always use `default=str` [VERIFIED: CLAUDE.md coding rules]
- **Test runner:** `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py` [VERIFIED: CLAUDE.md tests section]
- **Naming:** Lowercase with underscores for files and functions [VERIFIED: CLAUDE.md conventions]

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | unittest (stdlib) |
| Config file | None -- tests run directly via `python3 tests/test_*.py` |
| Quick run command | `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py` |
| Full suite command | `docker exec openclaw bash /home/tenazas/tests/run_tests.sh` |

### Phase Requirements -> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| FRMK-01 | Import agent_protocol gives Observation/Recommendation dataclasses | unit | `python3 -c "from agent_protocol import Observation, Recommendation"` | No -- Wave 0 |
| FRMK-01 | Dataclasses have correct fields and to_dict() | unit | test_sprint9.py::TestAgentProtocol | No -- Wave 0 |
| FRMK-02 | Registry register/unregister/list/health-check | unit | test_sprint9.py::TestAgentRegistry | No -- Wave 0 |
| FRMK-03 | Agent exception does not crash registry | unit | test_sprint9.py::TestErrorIsolation | No -- Wave 0 |
| FRMK-04 | Agent PREFERRED_ROLE exposed via registry | unit | test_sprint9.py::TestPreferredRole | No -- Wave 0 |
| INTG-02 | Concurrent writes produce valid JSON | unit | test_sprint9.py::TestLockedWrites | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py`
- **Per wave merge:** Full suite via `docker exec openclaw bash /home/tenazas/tests/run_tests.sh`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] New test classes in `tests/test_sprint9.py` -- TestAgentProtocol, TestAgentRegistry, TestErrorIsolation, TestPreferredRole, TestLockedWrites
- [ ] Test helpers: mock agent modules with/without required interface
- [ ] No new test file needed -- D-13 says update test_sprint9.py

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | N/A -- internal agent modules, no user auth |
| V3 Session Management | No | N/A -- no sessions |
| V4 Access Control | Yes | BLOCKED_TARGETS in agents prevent restart of openclaw/watchtower (existing safety_guard.py) |
| V5 Input Validation | Yes | validate_agent_module() checks module interface; PREFERRED_ROLE validated against allowlist; confidence clamped 0.0-1.0 |
| V6 Cryptography | No | N/A -- no crypto operations |

### Known Threat Patterns for Agent Protocol

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malicious agent module injection | Tampering | Hardcoded AGENT_MODULES allowlist (no dynamic registration from external input) |
| State file corruption from concurrent writes | Tampering | fcntl.flock() advisory locking + write_atomic() crash safety |
| Agent returning unbounded details dict | Denial of Service | Cap details dict size in to_dict() or log warning if > 10KB |

## Sources

### Primary (HIGH confidence)
- `workspace/runtime/intent_normalizer.py` -- NormalizedCall dataclass pattern (lines 19-41)
- `workspace/runtime/agent_coordinator.py` -- lazy loading, identify_agents, route patterns (lines 61-146)
- `workspace/runtime/agent_infra.py` -- reference agent with OWNERSHIP, owns(), handle_query() (full file)
- `workspace/runtime/runtime_utils.py` -- write_atomic(), load_json(), save_json() (lines 20-72)
- `workspace/runtime/proposal_executor.py` -- Lock class with fcntl.flock() (lines 180-238)
- `workspace/runtime/action_validator.py` -- ValidationResult dataclass with __post_init__ (lines 43-54)

### Secondary (MEDIUM confidence)
- All 5 agent modules verified via grep: identical _load/_save/_log pattern, OWNERSHIP sets, owns() and handle_query() signatures [VERIFIED: codebase grep]

### Tertiary (LOW confidence)
- None -- all findings verified from codebase

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all stdlib, all already used in codebase
- Architecture: HIGH -- patterns directly derived from existing code (coordinator, proposal_executor, intent_normalizer)
- Pitfalls: HIGH -- derived from actual code analysis (handle_query callers, fcntl platform, import chains)

**Research date:** 2026-04-28
**Valid until:** 2026-05-28 (stable -- all stdlib, no external deps)
