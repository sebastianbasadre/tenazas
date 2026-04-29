# Technology Stack: Multi-Agent Coordinator System

**Project:** Tenazas Multi-Agent System
**Researched:** 2026-04-28
**Mode:** Subsequent milestone (additive to existing 42+ module runtime)

## Executive Summary

The existing codebase already contains Sprint 9 agent modules (`agent_infra.py`, `agent_personal.py`, `agent_finance.py`, `agent_research.py`, `agent_self_improve.py`, `agent_coordinator.py`, `agent_arbitrator.py`) with a working but minimal implementation. Each agent follows a consistent pattern: `owns()` for topic detection, `assess()` for status gathering, `handle_query()` for response generation, and JSON-file-backed memory. The coordinator does basic multi-domain routing, and the arbitrator resolves conflicts via static priority.

**What exists is a skeleton. What's missing is lifecycle management, robust shared state, proper coordination strategies, and the glue to make it production-grade.**

No new external dependencies are needed. The stdlib (`enum`, `dataclasses`, `threading`, `queue`, `concurrent.futures`, `fcntl`, `json`, `hashlib`, `time`) plus the existing `runtime_utils.py` (`load_json`, `save_json`, `write_atomic`) provide everything required.

## Recommended Stack Additions

### New Modules (stdlib only)

| Module | Purpose | Why | Stdlib Dependencies |
|--------|---------|-----|---------------------|
| `agent_base.py` | Base class + lifecycle enum + agent protocol | Eliminates the duplicated `_log/_load/_save/owns/assess/handle_query/get_memory/save_memory_event` boilerplate across all 5 agents (currently ~30 identical lines per agent). Defines the contract. | `dataclasses`, `enum`, `abc` |
| `agent_state.py` | Shared state bus with file-locking reads/writes | Replaces the current pattern where each agent does independent `_load/_save` of separate JSON files with no coordination. Provides atomic read-modify-write via `write_atomic` + `fcntl` advisory locks. | `fcntl`, `json`, `tempfile`, `threading` |
| `agent_lifecycle.py` | Agent lifecycle manager (init/ready/busy/error/stopped) | Currently agents have no lifecycle -- they are stateless functions called by coordinator. Need state tracking for: knowing which agents are available, detecting stuck agents, graceful degradation. | `enum`, `dataclasses`, `time` |
| `coordination.py` | Coordination strategies (sequential, parallel, fan-out/fan-in) | Current `agent_coordinator.py` only does sequential loop. Need parallel execution for multi-domain queries (infra + finance + personal simultaneously) with timeout per agent. | `concurrent.futures.ThreadPoolExecutor`, `time` |
| `conflict_resolver.py` | Enhanced conflict resolution (weighted voting, priority with context) | Current `agent_arbitrator.py` does static priority only. Need weighted voting for cases where multiple agents propose actions on overlapping resources, plus escalation tracking. | `hashlib`, `collections`, `dataclasses` |

### Modifications to Existing Modules

| Module | Change | Why |
|--------|--------|-----|
| `agent_coordinator.py` | Refactor to use `agent_base.AgentProtocol` + `coordination.py` strategies | Currently hardcodes sequential execution and string-match triggers. Needs to delegate strategy selection to `coordination.py` and use typed agent interface. |
| `agent_arbitrator.py` | Integrate `conflict_resolver.py` for weighted voting | Currently only static priority. Needs context-aware weighting (e.g., agent_finance weight triples during market hours). |
| `orchestrator.py` | Add `route_to_agents()` entry point | Currently agents are separate from orchestrator pipeline. Need a clean handoff point: orchestrator classifies -> if multi-domain -> coordinator -> agents. |
| `telegram_router.py` | Wire agent coordinator responses into Telegram output | Need to format multi-agent aggregated responses for Telegram (combine infra status + finance alerts + personal items). |
| All 5 `agent_*.py` | Inherit from `AgentBase` or implement `AgentProtocol` | Eliminate boilerplate, gain lifecycle tracking, standardize interface. |

## Detailed Recommendations

### 1. Agent Base Class (`agent_base.py`)

**Why:** All 5 agents repeat identical patterns: `_log()`, `_load()`, `_save()`, `owns()`, `assess()`, `handle_query()`, `get_memory()`, `save_memory_event()`. This is ~30 lines of duplicated code per agent. A base class eliminates this and enforces the contract.

**Pattern: Protocol + dataclass (not ABC inheritance)**

Use `typing.Protocol` because existing agents are already standalone modules with module-level functions. Converting to class-based requires wrapping, but Protocol lets us type-check without forcing inheritance on day one.

```python
from dataclasses import dataclass, field
from enum import Enum
from typing import Protocol, runtime_checkable

class AgentStatus(Enum):
    INIT = "init"
    READY = "ready"
    BUSY = "busy"
    ERROR = "error"
    STOPPED = "stopped"

@dataclass
class AgentResult:
    agent: str
    success: bool
    response: str = ""
    proposals: list = field(default_factory=list)
    issues: list = field(default_factory=list)
    latency_ms: float = 0.0
    error: str = ""

@runtime_checkable
class AgentProtocol(Protocol):
    def owns(self, topic: str) -> bool: ...
    def assess(self) -> dict: ...
    def handle_query(self, query: str) -> str: ...
```

**Why Protocol over ABC:** The existing agents are module-level functions, not classes. Protocol lets the coordinator type-check them without forcing a rewrite. Migration to class-based can happen incrementally: wrap each agent's functions in a thin class that delegates to the existing module functions.

**Why dataclass for AgentResult:** Replaces the current inconsistent dict returns (`{"agent": ..., "health": ..., "proposals": ...}` vs `{"agent": ..., "alerts": ..., "summary": ...}`). Typed, consistent, serializable via `dataclasses.asdict()`.

### 2. Shared State Bus (`agent_state.py`)

**Why:** Currently each agent reads/writes its own `agents/{name}_memory.json` independently. When the coordinator runs multiple agents, there is no coordination on shared resources (e.g., two agents both wanting to restart the same container). The existing `write_atomic()` in `runtime_utils.py` handles crash safety but not concurrent access.

**Pattern: File-locked state partitions**

```python
import fcntl
import json
from pathlib import Path
from runtime_utils import write_atomic, load_json

STATE_DIR = Path("/home/tenazas/.openclaw/state/agents")

def read_state(partition: str) -> dict:
    """Read a state partition (non-blocking, advisory lock)."""
    path = STATE_DIR / f"{partition}.json"
    return load_json(path, {})

def write_state(partition: str, data: dict):
    """Write a state partition with advisory file lock."""
    path = STATE_DIR / f"{partition}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            write_atomic(path, data)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)

def read_modify_write(partition: str, modifier_fn):
    """Atomic read-modify-write with file lock."""
    path = STATE_DIR / f"{partition}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = path.with_suffix(".lock")
    with open(lock_path, "w") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            current = load_json(path, {})
            updated = modifier_fn(current)
            write_atomic(path, updated)
            return updated
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
```

**Why file locks over threading locks:** Agents may run in different processes (worker_supervisor.py spawns subprocess workers). `fcntl.flock` works across processes on Linux. Threading locks only work within a single process.

**Why partitioned files over one big state file:** Each agent gets its own partition (`infra.json`, `finance.json`, etc.) plus a shared `coordination.json` for cross-agent state. This minimizes lock contention -- agents rarely need to write to each other's partitions.

**Integration with existing `write_atomic`:** The `write_atomic()` in `runtime_utils.py` already does temp-file + `os.replace()`. Adding `fcntl.flock` on top provides process-level mutual exclusion.

### 3. Agent Lifecycle Manager (`agent_lifecycle.py`)

**Why:** Currently, if an agent's `assess()` hangs (e.g., `homelab_snapshot.snapshot()` times out), the coordinator waits forever. No agent knows its own status. The coordinator cannot distinguish "agent is busy" from "agent crashed".

**Pattern: Heartbeat + status enum**

```python
@dataclass
class AgentState:
    name: str
    status: AgentStatus = AgentStatus.INIT
    last_heartbeat: str = ""
    last_error: str = ""
    consecutive_errors: int = 0
    total_queries: int = 0

MAX_CONSECUTIVE_ERRORS = 3  # -> status = ERROR, skip until manual reset
HEARTBEAT_STALE_SECONDS = 120  # -> treat as stuck
```

**State machine transitions:**
```
INIT -> READY     (on successful first assess())
READY -> BUSY     (on handle_query() entry)
BUSY -> READY     (on handle_query() return)
BUSY -> ERROR     (on exception or timeout)
READY -> ERROR    (on 3 consecutive errors)
ERROR -> READY    (on manual reset or successful assess())
* -> STOPPED      (on explicit shutdown)
```

**Why this matters:** The coordinator needs to skip agents in ERROR state, and the daily brief should report which agents are degraded. Currently impossible without lifecycle tracking.

### 4. Coordination Strategies (`coordination.py`)

**Why:** The current `agent_coordinator.route()` does a sequential loop over matched agents. For a "como esta todo" query hitting 3 agents, this means 3 serial Ollama calls (potentially 3 x 5s = 15s). Parallel execution cuts this to ~5s.

**Pattern: Strategy enum + ThreadPoolExecutor**

```python
from enum import Enum
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError

class Strategy(Enum):
    SEQUENTIAL = "sequential"    # One after another (current behavior)
    PARALLEL = "parallel"        # All at once, combine results
    FIRST_WINS = "first_wins"    # First agent to respond wins
    PIPELINE = "pipeline"        # Output of agent A feeds into agent B

AGENT_TIMEOUT = 15  # seconds per agent
MAX_PARALLEL = 3    # max concurrent agent calls

def execute_parallel(agents: list, query: str, timeout: int = AGENT_TIMEOUT) -> list:
    results = []
    with ThreadPoolExecutor(max_workers=MAX_PARALLEL) as pool:
        futures = {pool.submit(agent.handle_query, query): agent for agent in agents}
        for future in as_completed(futures, timeout=timeout + 2):
            agent = futures[future]
            try:
                resp = future.result(timeout=1)
                results.append(AgentResult(agent=agent.name, success=True, response=resp))
            except TimeoutError:
                results.append(AgentResult(agent=agent.name, success=False, error="timeout"))
            except Exception as e:
                results.append(AgentResult(agent=agent.name, success=False, error=str(e)))
    return results
```

**Why ThreadPoolExecutor over asyncio:** The entire codebase (42+ modules) uses synchronous `requests.post()` for Ollama calls. Converting to async would require rewriting every HTTP call. ThreadPoolExecutor provides parallelism within the existing sync paradigm with zero refactor cost.

**Why not multiprocessing:** Agents share in-memory state (module-level dicts). Multiprocessing requires serialization. ThreadPoolExecutor shares memory. Since agents are I/O-bound (waiting on Ollama/HTTP), the GIL is not a bottleneck.

**Strategy selection heuristic:**
- Single agent match -> SEQUENTIAL (no coordination needed)
- Multi-domain query ("como esta todo") -> PARALLEL
- Pipeline query ("diagnose and fix") -> PIPELINE (infra diagnoses, self_improve proposes fix)
- Urgent query ("que pasa con X") -> FIRST_WINS

### 5. Conflict Resolution (`conflict_resolver.py`)

**Why:** The current `agent_arbitrator.py` uses static priority (`PRIORITY = {"safety": 0, "infra_critical": 1, ...}`). This works for simple cases but fails when:
- Two agents propose different actions on the same container
- Context should shift weights (market hours -> finance agent gets priority)
- Persistent conflicts need escalation history

**Pattern: Weighted priority with context modifiers**

```python
@dataclass
class Proposal:
    agent: str
    action: str
    resource: str
    severity: str = "info"
    confidence: float = 0.5
    context: dict = field(default_factory=dict)

def weighted_resolve(proposals: list[Proposal]) -> dict:
    """Resolve using weighted priority + confidence."""
    # Base priority (from existing PRIORITY dict)
    # + context modifiers (time of day, active incidents, agent track record)
    # + confidence weighting
    # = effective score per proposal
    #
    # Winner = highest effective score
    # If scores within 10% of each other = escalate to Sebas
```

**Why not full voting/consensus:** With only 5 agents, voting is overkill. Weighted priority with context modifiers covers all realistic scenarios. Voting makes sense with 10+ agents where no single authority exists -- not this system.

**Integration with existing `policy_engine.py`:** The conflict resolver produces a winner proposal. That proposal then goes through `policy_engine.evaluate()` for risk classification before execution. The resolver decides *which* agent's proposal wins; the policy engine decides *whether* to execute it.

## What NOT to Add

| Candidate | Why Not |
|-----------|---------|
| **CrewAI / LangGraph / AutoGen** | Massive dependencies (LangChain alone is 50+ packages). The system has 5 agents with well-defined domains. These frameworks solve problems at 50+ agent scale with dynamic tool use. Overkill. Their patterns (role-based agents, sequential/hierarchical execution, shared state) are trivially implementable with stdlib for this scale. |
| **asyncio rewrite** | The entire codebase (42+ modules) uses synchronous `requests.post()` for Ollama calls. Converting to async requires rewriting every HTTP call. `ThreadPoolExecutor` provides the same parallelism benefit for I/O-bound agent calls with zero refactor. |
| **Redis / SQLite for agent state** | JSON files with `fcntl` locks are sufficient for 5 agents running on a single NAS. Redis adds a container dependency. SQLite adds complexity without benefit -- agents read/write ~1KB JSON files. The existing `health_sweep.py` uses SQLite for time-series data (correct use); agent state is key-value (JSON is simpler). |
| **Message queue (RabbitMQ, ZeroMQ)** | 5 agents on one machine. Function calls + shared files are the right IPC. Message queues solve distributed multi-machine coordination. |
| **gRPC between agents** | Same machine, same process (or adjacent subprocesses). Direct function calls. |
| **Pydantic for validation** | Not in existing deps. `dataclasses` + manual validation match the codebase style. Every existing module uses plain dicts or simple dataclasses. |
| **transitions library** (state machine) | Nice but unnecessary dependency. Agent lifecycle has 5 states and ~8 transitions -- a dict lookup or match statement handles this. |
| **Event bus / pub-sub** | With 5 agents, direct function calls from coordinator to agents are clear and debuggable. Event bus adds indirection without benefit at this scale. When you have 20+ agents that need to react to events they don't know about in advance, pub-sub makes sense. |

## Integration Points with Existing Code

### orchestrator.py Integration

```python
# In orchestrator.py, add to route_query():
def route_query(query, context=None):
    classification = classify_query(query, context)
    
    # NEW: Check if multi-agent routing is beneficial
    if _should_use_agents(classification, query):
        from agent_coordinator import route as agent_route
        return agent_route(query)
    
    # Existing pipeline continues unchanged
    ...
```

**`_should_use_agents()` heuristic:**
- Classification is "overview" or "diagnostic_request" with multi-domain keywords -> True
- Query matches 2+ agent `owns()` -> True  
- Single skill match (weather, calendar, etc.) -> False (skill engine handles directly)

### telegram_router.py Integration

The telegram router already calls `orchestrator.route_and_execute()`. The change is in how multi-agent responses are formatted:

```python
# In telegram_router.py, after getting agent coordinator response:
def _format_agent_response(results: list[AgentResult]) -> str:
    """Format multiple agent results for Telegram."""
    parts = []
    for r in results:
        if r.success and r.response:
            parts.append(r.response)
        elif not r.success:
            parts.append(f"[{r.agent}] no disponible")
    return "\n\n".join(parts) if parts else "Sin datos disponibles."
```

### Existing agent_coordinator.py Refactor Path

Current `agent_coordinator.route()` already does:
1. `identify_agents(query)` -> list of agent names
2. Loop over agents, call `handle_query()`
3. Aggregate responses

The refactor preserves this flow but adds:
- Lifecycle check (skip ERROR agents)
- Strategy selection (parallel for multi-domain)
- Timeout per agent (15s)
- Typed results via `AgentResult`

### Existing agent_arbitrator.py Enhancement Path

Current arbitrator already has:
- `PRIORITY` dict with static priorities
- `_get_effective_priority()` with basic context
- `resolve()` that filters safety violations

Enhancement adds:
- Confidence-weighted scoring
- Time-based context modifiers
- Escalation history (persistent, not just in-memory)
- Integration with `conflict_resolver.py` for the algorithm

## Implementation Pattern: Agent Migration

Each existing agent (`agent_infra.py`, etc.) should be migrated incrementally:

**Phase 1 (zero breaking changes):** Create `AgentBase` class. Create wrapper classes that delegate to existing module functions.
```python
class InfraAgent(AgentBase):
    name = "infra"
    def owns(self, topic): return agent_infra.owns(topic)
    def assess(self): return agent_infra.assess()
    def handle_query(self, query): return agent_infra.handle_query(query)
```

**Phase 2:** Move agent logic into class methods, add lifecycle tracking.

**Phase 3:** Add `agent_state.py` integration for shared state.

This preserves all existing tests (they test module-level functions) while enabling new class-based features.

## Installation

No new packages required. All recommendations use Python stdlib:

```bash
# Nothing to install -- stdlib only
# Existing deps are sufficient:
# - requests (already installed, used by Ollama calls)
# - json, dataclasses, enum, threading, concurrent.futures, fcntl, hashlib, abc, typing
```

## Confidence Assessment

| Recommendation | Confidence | Rationale |
|----------------|------------|-----------|
| AgentBase + Protocol | HIGH | Pattern directly observed in codebase duplication. 5 agents with identical boilerplate. |
| ThreadPoolExecutor for parallel | HIGH | stdlib, well-documented, matches existing sync codebase. Already used in `health_sweep.py`. |
| fcntl file locking | HIGH | Container runs Linux (Bookworm). fcntl is POSIX standard. Already used conceptually via lockfile in runtime.py. |
| No CrewAI/LangGraph | HIGH | 5 agents, single machine, well-defined domains. Framework overhead far exceeds benefit. |
| Weighted conflict resolution | MEDIUM | Current static priority works. Enhancement needed for production correctness but not urgent. |
| Strategy enum pattern | MEDIUM | Studied from LangGraph/CrewAI patterns. Simple to implement but strategy selection heuristic needs tuning. |

## Sources

- Existing codebase analysis: `agent_coordinator.py`, `agent_arbitrator.py`, `agent_infra.py`, `agent_finance.py`, `agent_personal.py`, `agent_research.py`, `agent_self_improve.py`
- Existing patterns: `runtime_utils.py` (write_atomic), `policy_engine.py` (PolicyDecision dataclass), `multi_agent_engine.py` (sequential reasoning)
- [CrewAI architecture patterns](https://docs.crewai.com/en/concepts/agents) - Sequential/hierarchical process patterns studied, not adopted
- [LangGraph state machine orchestration](https://docs.langchain.com/oss/python/langgraph/overview) - Shared state + graph-based routing patterns studied
- [Multi-agent conflict resolution techniques](https://lorojournals.com/index.php/emsj/article/download/1648/1603/2256) - Voting/priority/escalation algorithms
- [Python asyncio patterns](https://docs.python.org/3/library/asyncio.html) - Evaluated and rejected in favor of ThreadPoolExecutor for this codebase
- [Weighted voting for multi-agent AI](https://medium.com/@edoardo.schepis/patterns-for-democratic-multi-agent-ai-voting-based-council-part-2-implementation-2992c3e7c2be) - Weighted voting pattern reference
- [Python file locking with fcntl](https://gist.github.com/jirihnidek/430d45c54311661b47fb45a3a7846537) - fcntl.flock advisory lock pattern
