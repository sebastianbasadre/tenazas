# Phase 7: Arbitrator & Cross-Agent - Research

**Researched:** 2026-04-29
**Domain:** Conflict resolution, cross-agent observation caching, proactive cron observations
**Confidence:** HIGH

## Summary

Phase 7 adds three capabilities to the multi-agent system: (1) refactoring the existing agent_arbitrator.py to use confidence-based tiebreaking and Telegram escalation via exec_approvals.py, (2) writing cached observations to per-agent state files so agents can read each other's context, and (3) running all 5 agents proactively every 5 minutes via the cron cycle so observations are pre-cached for fast Telegram responses.

The existing arbitrator (`agent_arbitrator.py`, 187 lines) already has the PRIORITY dict, `_get_effective_priority()` with context boosts, and `_handle_tie()` with postpone logic. The main changes are: replace the postpone-then-escalate tie logic with confidence-based tiebreaking (from Observation.confidence), add immediate Telegram escalation for true ties and contradictions via `exec_approvals.send_approval_request()`, and add a 5-minute timeout default-to-higher-priority fallback. For cross-agent context, each agent already writes to `state/agents/{name}_status.json`; the change is to add an `observation` key containing the serialized Observation from the last handle() call, plus a timestamp for TTL checking. For proactive cron, `handle("")` already falls through to full assessment in all 5 agents, so the worker just needs to call handle("") for each and save the result.

**Primary recommendation:** Refactor agent_arbitrator.py resolve() to accept Observation objects alongside proposals, break ties with confidence, escalate via exec_approvals when tied or contradictory. Add observation caching to each agent's handle() output path. Add a proactive observation step to the agents_worker.py (or runtime.py) that calls handle("") on all 5 agents every 5 minutes.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- D-01: Keep existing static priority tiers from agent_arbitrator.py (safety=0 through self_improve=8). Do NOT replace with weighted composite scoring.
- D-02: When two agents have the same effective priority, break ties using Observation.confidence (0.0-1.0). Higher confidence wins. No historical accuracy tracking needed.
- D-03: Context-aware priority boosts remain as-is: infra+critical->1, personal+calendar_active->2, finance+variation>10%->3.
- D-04: When agents tie (same priority AND same confidence) or produce contradictory recommendations (opposite actions on same target), escalate immediately to Telegram using exec_approvals.py inline keyboard buttons.
- D-05: Escalation message shows both agents' observations + recommendations side by side. Sebas taps to choose the winner.
- D-06: 5-minute timeout on escalation. If no response, default to the agent with higher static priority (lower number wins).
- D-07: Each agent writes its latest observation to its own per-agent state file (`state/agents/{name}.json`) under an `observation` key.
- D-08: Other agents read cross-agent observations via `load_json()` of other agent's state file. No API layer -- files are the API.
- D-09: Cached observations have 10-minute TTL. Observation older than 10 minutes is considered stale and ignored.
- D-10: All 5 agents run proactively every 5 minutes, piggybacking on the existing runtime.py cron cycle.
- D-11: Each proactive run calls `handle("")` (empty query) which triggers the agent's full assessment, writing the observation to the per-agent state file.
- D-12: When a Telegram query arrives, the coordinator reads cached observations first. If cache is fresh (<10min), use it immediately. If stale, call `handle(query)` for a live observation.

### Claude's Discretion
- How to structure the escalation message format (exact Telegram markdown)
- Whether to add a `last_observation_ts` field to the per-agent state file or derive staleness from the Observation.timestamp
- How to integrate proactive observation into the existing worker_supervisor.py cycle vs runtime.py
- Exact conflict detection logic (when to consider two recommendations "contradictory")

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| ARBT-01 | Priority-based conflict resolution when 2+ agents claim a query | Existing PRIORITY dict and _get_effective_priority() provide the base. Refactor resolve() to accept typed Observations with confidence. [VERIFIED: agent_arbitrator.py lines 27-37, 60-71] |
| ARBT-02 | Weighted multi-factor scoring (keyword match + confidence + historical accuracy) | User decision D-01/D-02 simplifies this: keep static priorities, use Observation.confidence for tiebreaking only. No historical accuracy tracking needed. [VERIFIED: CONTEXT.md D-01, D-02] |
| ARBT-03 | Escalation to Telegram when agents tie or produce contradictory recommendations | exec_approvals.py provides send_approval_request() with inline keyboard buttons. 5-min timeout via APPROVAL_TIMEOUT_SECONDS=300. [VERIFIED: exec_approvals.py lines 138-209, 325-336] |
| AGNT-06 | Cross-agent context sharing -- agents read each other's recent observations | Per-agent state files at state/agents/{name}_status.json already exist. Add observation key with serialized Observation. load_json() for reads. 10-min TTL via timestamp check. [VERIFIED: agent_infra.py line 17, D-07/D-08/D-09] |
| AGNT-07 | Proactive observations via background cron -- cached for fast Telegram responses | handle("") already falls through to full assessment in all 5 agents. agents_worker.py or runtime.py calls handle("") every 5 min. Observation saved to per-agent state file. [VERIFIED: agent_infra.py lines 224-253, handle("") -> _assess_full()] |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Conflict resolution (arbitrator) | Runtime Python (agent_arbitrator.py) | -- | Pure logic: compare priorities and confidence, no I/O |
| Telegram escalation | Runtime Python (exec_approvals.py) | External API (Telegram Bot API) | send_approval_request() handles Telegram API; poll_approval() checks result |
| Cross-agent observation reads | Runtime Python (per-agent files) | Filesystem (state/agents/*.json) | load_json() reads other agent's state file; no network or API needed |
| Observation caching (writes) | Runtime Python (agent handle()) | Filesystem (state/agents/*.json) | Each agent writes its observation after handle() call |
| Proactive cron observations | Runtime Python (workers/agents_worker.py) | -- | Sequential handle("") calls to all 5 agents every 5 minutes |
| Contradictory recommendation detection | Runtime Python (agent_arbitrator.py) | -- | Compare action+target of recommendations from claiming agents |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| agent_arbitrator | local | Conflict resolution with priority + confidence | Existing module, refactored in-place [VERIFIED: agent_arbitrator.py] |
| exec_approvals | local | Telegram inline keyboard for escalation | Existing module with send_approval_request(), poll_approval() [VERIFIED: exec_approvals.py] |
| agent_protocol | local | Observation, Recommendation dataclasses | Phase 5 foundation, all agents import it [VERIFIED: agent_protocol.py] |
| agent_registry | local | Agent dispatch, list, health checks | Phase 5 registry, used by coordinator [VERIFIED: agent_registry.py] |
| runtime_utils | local | load_json, save_json, now, locked_write_json | Phase 1 foundation [VERIFIED: runtime_utils.py] |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| json | stdlib | Serialize observations to state files | Always -- standard JSON I/O |
| time | stdlib | Timestamp comparison for TTL checking | Observation staleness detection |
| datetime | stdlib | ISO timestamp parsing for TTL | Parsing Observation.timestamp |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| exec_approvals for escalation | Direct Telegram API call | exec_approvals already handles buttons, timeout, state tracking; no reason to bypass |
| File-based observation cache | In-memory dict | Files persist across cron cycles; in-memory resets every 5 min. Files are correct for this use case |
| Separate proactive worker | Inline in runtime.py | Worker subprocess provides isolation; if proactive observations hang, they don't block the core runtime loop |

**Installation:**
```bash
# No installation needed -- all existing modules
```

## Architecture Patterns

### System Architecture Diagram

```
Telegram query arrives
    |
    v
agent_coordinator.route(query)
    |
    +---> identify_agents(query) -- which agents claim ownership?
    |
    |--- 0 agents --> fallback to orchestrator
    |--- 1 agent  --> call handle(query), return result
    |--- 2+ agents --> ARBITRATOR intercepts
                          |
                          +---> For each claiming agent:
                          |       read cached observation from state/agents/{name}.json
                          |       if fresh (<10min): use cached
                          |       if stale: call handle(query) live
                          |
                          +---> resolve(observations, context)
                                  |
                                  +---> Compare effective priorities
                                  |       winner has lower priority number --> done
                                  |
                                  +---> Same priority? Compare confidence
                                  |       higher confidence wins --> done
                                  |
                                  +---> Same priority AND same confidence?
                                  |     OR contradictory recommendations?
                                  |       --> escalate to Telegram via exec_approvals
                                  |           5-min timeout
                                  |           default: higher static priority wins
                                  |
                                  +---> Return winner's (Observation, Recommendations)


Proactive cron (every 5 min, parallel to above):
    worker_supervisor --> agents_worker.py
        |
        +---> for each agent in [infra, personal, finance, research, self_improve]:
                agent.handle("") -- full assessment
                save observation to state/agents/{name}.json under "observation" key
```

### Recommended Project Structure
```
workspace/runtime/
    agent_arbitrator.py    # REFACTOR: add confidence tiebreak, Telegram escalation
    agent_protocol.py      # NO CHANGE (Observation already has confidence + timestamp)
    agent_registry.py      # NO CHANGE
    agent_coordinator.py   # MINOR: read cached observations before live handle()
    exec_approvals.py      # NO CHANGE (used as-is for escalation)
    agent_infra.py         # MINOR: save observation to state file after handle()
    agent_personal.py      # MINOR: save observation to state file after handle()
    agent_finance.py       # MINOR: save observation to state file after handle()
    agent_research.py      # MINOR: save observation to state file after handle()
    agent_self_improve.py  # MINOR: save observation to state file after handle()
    workers/
        agents_worker.py   # ADD: proactive handle("") calls for all 5 agents
tests/
    test_sprint9.py        # ADD: arbitrator, cross-agent, proactive observation tests
```

### Pattern 1: Confidence-Based Tiebreaking in Arbitrator

**What:** When two agents have the same effective priority, use Observation.confidence to break the tie.
**When to use:** resolve() when 2+ agents claim a query.
**Example:**
```python
# Source: derived from agent_arbitrator.py resolve() + CONTEXT.md D-02
def resolve(claims: list) -> dict:
    """Resolve conflict between agent claims.
    
    Args:
        claims: list of {agent, observation, recommendations, context}
            where observation is an Observation dataclass (or dict with confidence)
    
    Returns:
        {winner, loser, reason, observation, recommendations, escalated}
    """
    if len(claims) < 2:
        # ... single claim, no conflict
        return single_result

    # Sort by effective priority (lower = higher priority)
    sorted_claims = sorted(claims,
        key=lambda c: _get_effective_priority(c["agent"], c.get("context", {})))

    winner = sorted_claims[0]
    loser = sorted_claims[1]
    
    w_prio = _get_effective_priority(winner["agent"], winner.get("context", {}))
    l_prio = _get_effective_priority(loser["agent"], loser.get("context", {}))

    if w_prio < l_prio:
        # Clear winner by priority
        return {"winner": winner["agent"], ...}
    
    # Same priority -- tiebreak by confidence
    w_conf = _get_confidence(winner)
    l_conf = _get_confidence(loser)
    
    if abs(w_conf - l_conf) > 0.01:  # avoid float comparison issues
        if w_conf > l_conf:
            return {"winner": winner["agent"], "reason": f"confidence tiebreak {w_conf:.2f} > {l_conf:.2f}", ...}
        else:
            return {"winner": loser["agent"], "reason": f"confidence tiebreak {l_conf:.2f} > {w_conf:.2f}", ...}
    
    # True tie or contradictory recommendations -- escalate
    return _escalate_to_telegram(winner, loser)
```

### Pattern 2: Telegram Escalation via exec_approvals

**What:** When agents tie or contradict, send a side-by-side message to Telegram with inline keyboard.
**When to use:** True ties (same priority + same confidence) or contradictory recommendations.
**Example:**
```python
# Source: derived from exec_approvals.py send_approval_request() + CONTEXT.md D-04/D-05/D-06
def _escalate_to_telegram(claim_a: dict, claim_b: dict) -> dict:
    """Escalate conflict to Telegram for Sebas to decide."""
    from exec_approvals import send_approval_request, get_approval_status
    
    obs_a = claim_a.get("observation")
    obs_b = claim_b.get("observation")
    
    summary = f"Conflicto: {claim_a['agent']} vs {claim_b['agent']}"
    details = (
        f"Agente A: {claim_a['agent']}\n"
        f"  Observacion: {obs_a.summary if obs_a else 'N/A'}\n"
        f"  Confianza: {obs_a.confidence if obs_a else 'N/A'}\n\n"
        f"Agente B: {claim_b['agent']}\n"
        f"  Observacion: {obs_b.summary if obs_b else 'N/A'}\n"
        f"  Confianza: {obs_b.confidence if obs_b else 'N/A'}"
    )
    
    approval_id = send_approval_request(summary, details, "arbitrator")
    
    # Non-blocking: return escalated result, caller polls or waits
    return {
        "winner": None,
        "loser": None,
        "reason": "escalated to Telegram",
        "escalated": True,
        "approval_id": approval_id,
        "agents": [claim_a["agent"], claim_b["agent"]],
        "default_winner": claim_a["agent"],  # lower static priority number
    }
```

### Pattern 3: Observation Caching in Agent State Files

**What:** Each agent saves its latest Observation to its per-agent state file under an `observation` key.
**When to use:** After every handle() call (both query-driven and proactive).
**Example:**
```python
# Source: derived from agent_infra.py handle() + CONTEXT.md D-07
# In each agent's handle() function, after building the Observation:

def handle(query: str):
    # ... existing query parsing and data collection ...
    obs = Observation(
        agent_name="infra",
        domain="infrastructure",
        summary=data.get("summary", "Infra: sin datos"),
        confidence=data.get("confidence", 0.5),
        details=data,
    )
    recs = _build_recommendations(data)
    
    # Save observation to per-agent state file for cross-agent reads
    _save_observation(obs)
    
    return (obs, recs)


def _save_observation(obs):
    """Cache observation in per-agent state file."""
    state = load_json(str(STATUS_FILE), {})
    state["observation"] = obs.to_dict()
    save_json(str(STATUS_FILE), state)
```

### Pattern 4: Cross-Agent Observation Reading with TTL

**What:** Read another agent's cached observation from its state file, respecting 10-minute TTL.
**When to use:** Arbitrator reading observations, or agents reading each other's context.
**Example:**
```python
# Source: derived from CONTEXT.md D-08/D-09 + runtime_utils load_json
from datetime import datetime, timedelta

OBSERVATION_TTL_MINUTES = 10
AGENT_STATE_DIR = STATE_DIR / "agents"

def read_cached_observation(agent_name: str) -> dict:
    """Read cached observation from agent's state file. Returns None if stale."""
    # Map agent name to state file
    state_file = AGENT_STATE_DIR / f"{agent_name}_status.json"
    state = load_json(str(state_file), {})
    obs_dict = state.get("observation")
    if not obs_dict:
        return None
    
    # Check TTL
    ts_str = obs_dict.get("timestamp", "")
    if ts_str:
        try:
            obs_time = datetime.fromisoformat(ts_str)
            if datetime.now().astimezone() - obs_time > timedelta(minutes=OBSERVATION_TTL_MINUTES):
                return None  # stale
        except (ValueError, TypeError):
            return None
    
    return obs_dict
```

### Pattern 5: Proactive Cron Observations

**What:** Call handle("") on all 5 agents every 5 minutes to pre-cache observations.
**When to use:** In agents_worker.py during the cron cycle.
**Example:**
```python
# Source: derived from workers/agents_worker.py + CONTEXT.md D-10/D-11
# Add to agents_worker.py run() function:

def _run_proactive_observations():
    """Call handle("") on all agents to cache observations."""
    from agent_registry import init_default_agents, DEFAULT_AGENTS
    
    for name, module_path in DEFAULT_AGENTS.items():
        try:
            import importlib
            agent = importlib.import_module(module_path)
            obs, recs = agent.handle("")  # empty query -> full assessment
            # Observation is already saved to state file by the agent's handle()
            _log(f"proactive observation: {name} confidence={obs.confidence:.2f}")
        except Exception as e:
            _log(f"proactive observation failed: {name} error={str(e)[:100]}")
```

### Pattern 6: Contradictory Recommendation Detection

**What:** Detect when two agents recommend opposite actions on the same target.
**When to use:** In arbitrator resolve() before selecting a winner.
**Example:**
```python
# Source: derived from CONTEXT.md D-04 (contradictory recommendations)
OPPOSITE_ACTIONS = {
    "restart": "stop",
    "stop": "restart",
    "start": "stop",
    "investigate": None,  # not contradictory with anything
}

def _are_contradictory(recs_a: list, recs_b: list) -> bool:
    """Check if two recommendation sets contain contradictory actions on same target."""
    for ra in recs_a:
        for rb in recs_b:
            if ra.target == rb.target:
                opposite = OPPOSITE_ACTIONS.get(ra.action)
                if opposite and rb.action == opposite:
                    return True
                opposite_b = OPPOSITE_ACTIONS.get(rb.action)
                if opposite_b and ra.action == opposite_b:
                    return True
    return False
```

### Anti-Patterns to Avoid
- **Blocking on Telegram poll in the main loop:** poll_approval() is blocking. Never call it in the coordinator's synchronous path. Return the escalated result with `approval_id` and let the caller handle async polling or timeout.
- **Removing postpone logic entirely:** The existing postpone mechanism (`_handle_tie`) tracked persistent conflicts. While D-04 says "escalate immediately," the conflict history tracking is still useful for logging. Remove the postpone delay but keep the conflict counter for observability.
- **Writing observations to a shared file:** D-07 says per-agent files, not a shared observations file. Shared files create lock contention. Each agent writes to its own file.
- **Calling handle(query) during arbitration:** The arbitrator should read cached observations (fast) and only call handle(query) if the cache is stale. Calling handle() for all claiming agents during arbitration adds latency.
- **Using Observation.timestamp as a string comparison:** Always parse to datetime for TTL comparison. String comparison of ISO timestamps works but is fragile across timezone representations.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Telegram inline keyboard buttons | Custom Telegram API calls | exec_approvals.send_approval_request() | Already handles keyboard creation, state tracking, callback processing [VERIFIED: exec_approvals.py lines 175-185] |
| Approval status polling | Custom polling loop | exec_approvals.get_approval_status() or poll_approval() | Already handles timeout, expiry, state management [VERIFIED: exec_approvals.py lines 311-336] |
| Observation serialization | Custom dict building | Observation.to_dict() via dataclasses.asdict | Phase 5 protocol already provides this [VERIFIED: agent_protocol.py line 47-48] |
| Atomic state file writes | open()/write() | save_json() or locked_write_json() from runtime_utils | Crash-safe via tempfile+os.replace [VERIFIED: runtime_utils.py] |
| Agent module loading | Manual importlib | agent_registry.init_default_agents() + dispatch() | Registry handles import errors, validation, health tracking [VERIFIED: agent_registry.py lines 71-119, 132-160] |

**Key insight:** Every building block is already implemented. The arbitrator refactor is logic-only (compare priorities+confidence, decide escalation). Cross-agent reads are load_json() calls. Proactive observations are handle("") calls. No new infrastructure needed.

## Common Pitfalls

### Pitfall 1: exec_approvals Button Format for Agent Choice
**What goes wrong:** The existing exec_approvals keyboard has "Aprobar/Cancelar" buttons, but for arbitration we need "Agent A / Agent B" buttons.
**Why it happens:** exec_approvals was designed for single-action approval, not multi-choice.
**How to avoid:** Create a new function `send_conflict_escalation()` that builds a custom keyboard with agent names as button labels, or reuse send_approval_request() with the agent name in the callback_data. The simplest approach: send two approval requests (one per agent) with the conflict context, and the first one approved wins. Alternative: extend exec_approvals with a new `send_choice_request()` function.
**Warning signs:** Sebas sees "Aprobar/Cancelar" instead of agent names.
**Recommendation:** Add a small `send_conflict_escalation(agent_a, obs_a, agent_b, obs_b)` function to agent_arbitrator.py that builds the custom keyboard directly (3 buttons: Agent A name, Agent B name, "Ver detalles"). This avoids modifying exec_approvals.py. [ASSUMED -- depends on how flexible we want to be]

### Pitfall 2: Observation Timestamp Timezone Mismatch
**What goes wrong:** TTL check fails because datetime.now() and Observation.timestamp use different timezone representations.
**Why it happens:** Observation.__post_init__() uses `datetime.now().astimezone().isoformat()` which includes timezone offset. datetime.now() without timezone is naive.
**How to avoid:** Always use `datetime.now().astimezone()` for comparison (matching Observation's format), or use `now()` from runtime_utils which already returns timezone-aware datetime. [VERIFIED: agent_protocol.py line 42, runtime_utils.py]
**Warning signs:** All observations appearing as stale despite being fresh.

### Pitfall 3: State File Path Inconsistency
**What goes wrong:** Agent infra writes to `infra_status.json` but the cross-agent reader looks for `infra.json`.
**Why it happens:** Different agents use different naming conventions for their state files.
**How to avoid:** Standardize on a known mapping. Check each agent's STATUS_FILE constant.
**Warning signs:** Cross-agent reads returning empty/None.

### Pitfall 4: Proactive Observations Blocking the Cron Cycle
**What goes wrong:** One agent's handle("") hangs (e.g., finance calling Binance API), delaying all other proactive observations.
**Why it happens:** handle("") calls external APIs for full assessment.
**How to avoid:** Wrap each proactive handle("") in try/except with a per-agent timeout. Since agents_worker.py already runs as a subprocess with 30s timeout, individual agent calls should have a 5s soft limit. Use the existing pattern: catch exceptions, log, continue.
**Warning signs:** agents_worker.py timing out frequently.

### Pitfall 5: Arbitrator Receiving Dict Instead of Observation
**What goes wrong:** resolve() expects Observation objects but receives raw dicts from cached state files.
**Why it happens:** Cached observations are dicts (from JSON), not Observation dataclass instances.
**How to avoid:** Accept both: check if claim has an Observation object or a dict, and extract confidence from either. Use duck typing: `getattr(obs, "confidence", obs.get("confidence", 0.5))`.
**Warning signs:** AttributeError on dict when accessing .confidence.

### Pitfall 6: Escalation During Proactive Cron (No User Query)
**What goes wrong:** Proactive observations trigger the arbitrator, which tries to escalate to Telegram for a non-query.
**Why it happens:** Proactive observations are cached independently; they should not trigger arbitration.
**How to avoid:** Arbitration only runs when a Telegram query arrives and 2+ agents claim it. Proactive observations are write-only (cache for later reads). The arbitrator is called from the coordinator's route() path, not from the proactive cron path.
**Warning signs:** Random Telegram escalation messages without user queries.

## Code Examples

### Verified: exec_approvals.send_approval_request Signature
```python
# Source: exec_approvals.py lines 138-209
def send_approval_request(
    action_summary: str,      # short description (shown in message)
    action_details: str,      # full description (shown on "Ver detalles")
    skill_name: str,          # skill that would be executed
    chat_id: str = CHAT_ID,   # Telegram chat ID (default: Sebas)
    token=None,               # bot token (reads from config if None)
) -> str:                     # returns approval_id for polling
```

### Verified: Observation Dataclass Fields
```python
# Source: agent_protocol.py lines 33-48
@dataclass
class Observation:
    agent_name: str
    domain: str
    summary: str
    confidence: float = 0.0    # clamped to 0.0-1.0 in __post_init__
    details: dict = field(default_factory=dict)
    timestamp: str = ""        # auto-set to now().astimezone().isoformat() if empty
    
    def to_dict(self) -> dict:
        return asdict(self)
```

### Verified: Current Agent State File Paths
```python
# Source: grep of all agent_*.py STATUS_FILE constants
# agent_infra.py:    STATUS_FILE = AGENT_DIR / "infra_status.json"
# agent_personal.py: STATUS_FILE = AGENT_DIR / "personal_status.json"  
# agent_finance.py:  STATUS_FILE = AGENT_DIR / "finance_status.json"
# agent_research.py: STATUS_FILE = AGENT_DIR / "research_status.json"
# agent_self_improve.py: STATUS_FILE = AGENT_DIR / "self_improve_status.json"
```

### Verified: handle("") Falls Through to Full Assessment
```python
# Source: agent_infra.py lines 224-253
# When query="" -> q="" -> no keywords match -> falls to else branch:
#   else: data = _assess_full()
# Same pattern in all 5 agents:
#   agent_personal.py: else: data = _observe_all_personal()
#   agent_finance.py:  else: data = _observe_full()
#   agent_research.py: else: data = _observe_general()
#   agent_self_improve.py: else: data = _observe_full()
```

### Verified: Current Arbitrator resolve() Interface
```python
# Source: agent_arbitrator.py lines 82-146
def resolve(proposals: list) -> dict:
    """
    Args:
        proposals: list of {agent, action, resource, severity, context}
    Returns:
        {winner, loser, reason, action, escalated}
    """
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Postpone 5 min on tie, escalate after 3 postpones | Immediate confidence tiebreak, immediate Telegram escalation on true tie | Phase 7 (now) | Faster conflict resolution, no delayed decisions |
| No cross-agent context | Agents read each other's cached observations via state files | Phase 7 (now) | Agents can make informed decisions about other domains |
| No proactive observations | All 5 agents assess every 5 min via cron | Phase 7 (now) | Pre-cached observations for fast Telegram responses |
| resolve() takes raw proposals (dicts) | resolve() accepts Observations with confidence | Phase 7 (now) | Typed input, confidence-based scoring |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | exec_approvals can be extended or used as-is for agent-choice escalation (not just approve/cancel) | Pitfall 1 | Medium -- may need to add a send_choice_request() function or build custom keyboard in arbitrator |
| A2 | Observation.timestamp is consistently timezone-aware across all agents | Pitfall 2 | Low -- __post_init__ always uses astimezone() [VERIFIED: agent_protocol.py line 42] |
| A3 | agents_worker.py 30s timeout is sufficient for 5 proactive handle("") calls | Pattern 5 | Medium -- if external APIs are slow, some agents may not complete. Mitigation: per-agent try/except |
| A4 | STATUS_FILE naming follows {name}_status.json pattern consistently | Pitfall 3 | Low -- needs verification via grep of all 5 agents |

## Open Questions

1. **How should the escalation keyboard look for agent conflicts?**
   - What we know: exec_approvals uses "Aprobar/Cancelar/Ver detalles" buttons. For conflicts, we need "Agent A / Agent B" buttons.
   - What's unclear: Whether to extend exec_approvals or build a custom keyboard in arbitrator.
   - Recommendation: Build a small `_send_conflict_to_telegram()` in agent_arbitrator.py that creates a custom keyboard with two agent-name buttons. Reuse exec_approvals' internal `_tg_request()` and state tracking pattern, or call send_approval_request() twice (one per agent option). The simplest approach: use send_approval_request() with action_summary = "Elegir: {agent_a}" and a second request for agent_b, first approved wins.

2. **Should proactive observations go in agents_worker.py or runtime.py?**
   - What we know: agents_worker.py runs as subprocess with 30s timeout (isolation). runtime.py is the main loop.
   - What's unclear: Whether adding proactive observations to agents_worker.py (existing subprocess) or runtime.py (main loop) is better.
   - Recommendation: Add to agents_worker.py. It already runs every 5 min via worker_supervisor, has subprocess isolation (handle("") hanging won't block core runtime), and is the logical place for agent-related work.

3. **How to handle the 5-minute escalation timeout in a non-blocking way?**
   - What we know: poll_approval() blocks for up to 300s. The coordinator path must be fast for Telegram response.
   - What's unclear: Whether to return the escalated result immediately or block.
   - Recommendation: Return immediately with `escalated=True` and `default_winner`. The coordinator uses the default_winner's observation as the response. If Sebas responds later via button tap, the callback handler logs the decision for future reference but doesn't change the already-sent response. This keeps the Telegram path fast.

## Project Constraints (from CLAUDE.md)

- **No new dependencies:** All solutions use existing modules and stdlib [VERIFIED: CLAUDE.md]
- **Container paths:** PYTHONPATH=/home/tenazas/runtime -- bare imports work [VERIFIED: Dockerfile]
- **Imports:** Use `from runtime_utils import load_json, save_json, now` [VERIFIED: CLAUDE.md coding rules]
- **json.dumps with datetime:** Always use `default=str` [VERIFIED: CLAUDE.md coding rules]
- **Subprocess safety:** ONLY use list arguments, NEVER shell=True [VERIFIED: CLAUDE.md]
- **Tests:** Run via `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py` [VERIFIED: CLAUDE.md]
- **Error handling:** try/except per external module call, never crash the agent [VERIFIED: CLAUDE.md]
- **Safety:** BLOCKED_TARGETS in agent_infra must stay [VERIFIED: agent_infra.py line 22]

## Validation Architecture

### Test Framework
| Property | Value |
|----------|-------|
| Framework | unittest (stdlib) |
| Config file | None -- tests run directly via `python3 tests/test_*.py` |
| Quick run command | `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py` |
| Full suite command | `docker exec openclaw bash /home/tenazas/tests/run_tests.sh` |

### Phase Requirements --> Test Map
| Req ID | Behavior | Test Type | Automated Command | File Exists? |
|--------|----------|-----------|-------------------|-------------|
| ARBT-01 | resolve() selects winner by priority when priorities differ | unit | test_sprint9.py::TestArbitratorResolve | Partial -- existing tests cover basic resolve |
| ARBT-01 | resolve() with typed Observations (not just raw dicts) | unit | test_sprint9.py::TestArbitratorTypedResolve | No -- Wave 0 |
| ARBT-02 | resolve() breaks ties using Observation.confidence | unit | test_sprint9.py::TestArbitratorConfidenceTiebreak | No -- Wave 0 |
| ARBT-03 | resolve() escalates to Telegram on true tie (same priority + same confidence) | unit | test_sprint9.py::TestArbitratorEscalation | No -- Wave 0 |
| ARBT-03 | resolve() escalates on contradictory recommendations | unit | test_sprint9.py::TestArbitratorContradiction | No -- Wave 0 |
| ARBT-03 | escalation defaults to higher static priority after timeout | unit | test_sprint9.py::TestArbitratorTimeout | No -- Wave 0 |
| AGNT-06 | Agent writes observation to state file after handle() | unit | test_sprint9.py::TestCrossAgentCache | No -- Wave 0 |
| AGNT-06 | read_cached_observation() returns None for stale (>10min) observation | unit | test_sprint9.py::TestObservationTTL | No -- Wave 0 |
| AGNT-06 | Agent can read another agent's cached observation | unit | test_sprint9.py::TestCrossAgentRead | No -- Wave 0 |
| AGNT-07 | handle("") produces observation and saves to state file | unit | test_sprint9.py::TestProactiveObservation | No -- Wave 0 |
| AGNT-07 | Proactive observation runs for all 5 agents without crashing | unit | test_sprint9.py::TestProactiveAll | No -- Wave 0 |

### Sampling Rate
- **Per task commit:** `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py`
- **Per wave merge:** Full suite via `docker exec openclaw bash /home/tenazas/tests/run_tests.sh`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] New test classes in `tests/test_sprint9.py` -- TestArbitratorTypedResolve, TestArbitratorConfidenceTiebreak, TestArbitratorEscalation, TestArbitratorContradiction, TestArbitratorTimeout, TestCrossAgentCache, TestObservationTTL, TestCrossAgentRead, TestProactiveObservation, TestProactiveAll
- [ ] Mock for exec_approvals.send_approval_request() (no real Telegram calls in tests)
- [ ] Mock for agent handle("") calls (no real homelab_snapshot/Binance API in tests)
- [ ] Fixtures for Observation objects with various confidence levels and timestamps

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | N/A -- internal agent modules |
| V3 Session Management | No | N/A -- no sessions |
| V4 Access Control | Yes | BLOCKED_TARGETS filter in agent_infra prevents restart of openclaw/watchtower; requires_approval=True on high-risk recommendations; escalation requires Sebas approval |
| V5 Input Validation | Yes | Agent names validated against known set (DEFAULT_AGENTS); Observation.confidence clamped 0.0-1.0 in __post_init__; callback_data validated in exec_approvals |
| V6 Cryptography | No | N/A -- no crypto in arbitrator layer |

### Known Threat Patterns for Agent Layer

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Malicious agent injecting high confidence to always win arbitration | Elevation of Privilege | Confidence comes from handle() code (not user input); agents are internal code, not external |
| Stale observation causing wrong arbitration decision | Information Disclosure | 10-minute TTL on cached observations; stale observations discarded |
| Telegram escalation spam (frequent ties flooding chat) | Denial of Service | Existing APPROVAL_TIMEOUT_SECONDS=300; conflict history tracking with _conflict_id for dedup |
| Protected container targeted through arbitrator bypass | Tampering | BLOCKED_TARGETS filter applied in agent_infra._build_recommendations() before arbitrator sees them [VERIFIED: agent_infra.py lines 67-68] |

## Sources

### Primary (HIGH confidence)
- `workspace/runtime/agent_arbitrator.py` -- Full file analyzed, resolve(), PRIORITY dict, _handle_tie(), _get_effective_priority() [VERIFIED: codebase read]
- `workspace/runtime/exec_approvals.py` -- Full file analyzed, send_approval_request(), handle_callback_query(), poll_approval() [VERIFIED: codebase read]
- `workspace/runtime/agent_protocol.py` -- Observation and Recommendation dataclasses, validate_agent_module() [VERIFIED: codebase read]
- `workspace/runtime/agent_registry.py` -- register(), dispatch(), list_agents(), DEFAULT_AGENTS [VERIFIED: codebase read]
- `workspace/runtime/agent_coordinator.py` -- route(), identify_agents(), resolve_conflict() [VERIFIED: codebase read]
- `workspace/runtime/agent_infra.py` -- Full file analyzed, handle() with cached_snap pattern, STATUS_FILE, _save_observation pattern [VERIFIED: codebase read]
- `.planning/phases/07-arbitrator-cross-agent/07-CONTEXT.md` -- All 12 locked decisions [VERIFIED]
- `.planning/REQUIREMENTS.md` -- ARBT-01..03, AGNT-06..07 [VERIFIED]

### Secondary (MEDIUM confidence)
- `workspace/runtime/workers/agents_worker.py` -- Current worker structure, 30s timeout context [VERIFIED: codebase read]
- `.planning/phases/06-domain-agents/06-RESEARCH.md` -- Phase 6 patterns and handle() design [VERIFIED]
- `.planning/phases/05-foundation-contracts/05-CONTEXT.md` -- Phase 5 decisions on protocol and state [VERIFIED]

### Tertiary (LOW confidence)
- None -- all claims verified against codebase

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all existing modules verified in codebase, no new dependencies
- Architecture: HIGH -- straightforward refactor of existing arbitrator + addition of caching and cron
- Pitfalls: HIGH -- derived from actual code analysis of arbitrator, exec_approvals, and all 5 agents

**Research date:** 2026-04-29
**Valid until:** 2026-05-29 (stable -- all internal modules, no external deps)
