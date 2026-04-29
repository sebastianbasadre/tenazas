# Phase 6: Domain Agents - Research

**Researched:** 2026-04-29
**Domain:** Python multi-agent observe/recommend, domain-specific query handling
**Confidence:** HIGH

## Summary

Phase 6 makes each of the 5 existing agents produce meaningful, query-responsive Observations and Recommendations. The agents already have the protocol wiring from Phase 5 (PREFERRED_ROLE, handle() returning (Observation, list[Recommendation]), owns() for ownership), but their handle() implementations are thin wrappers around assess() functions that do background-style status checks, not query-responsive behavior. The gap is that none of the agents currently parse or use the `query` parameter -- they all call assess() and return generic status regardless of what was asked.

The work is strictly inside the 5 agent files plus their tests. No new files are created. Each agent must: (1) parse the incoming query to understand what the user is asking, (2) call the appropriate existing modules to get domain-specific data, and (3) return a typed Observation with relevant details and actionable Recommendations. Each agent already imports from agent_protocol and runtime_utils; the infrastructure is in place.

**Primary recommendation:** Implement query-responsive handle() in each agent by adding keyword parsing of the query string, delegating to existing domain modules (homelab_snapshot, infra_predictor, notification_poller, finance_daily, finance.py, expenses.py, continuous_learning_loop, error_mining, improvement_candidates, experiment_runner), and constructing Observations/Recommendations with appropriate confidence levels and risk ratings. Keep assess() for background/cron use; handle() becomes the query-driven entry point.

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| AGNT-01 | agent_infra -- infrastructure monitoring (containers, GPU, VPN, disk, health) | handle() must parse infra queries and call homelab_snapshot.snapshot(), infra_predictor.predict_all(), health_sweep probes. Current assess() already calls these but ignores the query. [VERIFIED: agent_infra.py lines 39-73] |
| AGNT-02 | agent_personal -- personal assistant (calendar, email, reminders) | handle() must parse personal queries and call notification_poller.check_calendar(), notification_poller.check_email(), smoking.py stats. Current assess() calls check_calendar/check_email but ignores query. [VERIFIED: agent_personal.py lines 35-49] |
| AGNT-03 | agent_finance -- finance domain (IOL, expenses, Binance alerts) | handle() must parse finance queries and call finance_daily.get_binance_pnl(), finance_daily.get_iol_portfolio(), finance.summary(), expenses.py weekly stats. Current assess() calls binance/iol but ignores query. [VERIFIED: agent_finance.py lines 37-53] |
| AGNT-04 | agent_research -- knowledge domain (SearXNG, RAG queries, knowledge base) | handle() must route to RAG query.py or continuous_learning_loop.research() based on query. Current handle() already calls research() but doesn't use RAG. PREFERRED_ROLE="reason" enables deeper model. [VERIFIED: agent_research.py lines 35-64] |
| AGNT-05 | agent_self_improve -- self-improvement (error mining, proposals, experiments) | handle() must parse improvement queries and call error_mining.mine_all(), improvement_candidates.list_candidates(), experiment_runner.list_active(). Current assess() calls these but returns bare counts. [VERIFIED: agent_self_improve.py lines 36-48] |
</phase_requirements>

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Query parsing (keyword extraction) | Runtime Python (agent modules) | -- | Each agent parses query keywords locally, no LLM needed for classification |
| Infrastructure observation | Runtime Python (agent_infra) | Docker socket / Network | Delegates to homelab_snapshot (subprocess docker ps), infra_predictor (Prometheus queries) |
| Personal assistant observation | Runtime Python (agent_personal) | External APIs (Google, Gmail) | Delegates to notification_poller (gcal.py, himalaya CLI), smoking.py |
| Finance observation | Runtime Python (agent_finance) | External APIs (Binance, IOL) | Delegates to finance_daily (Binance API, IOL API), finance.py (local JSON), expenses.py |
| Research observation | Runtime Python (agent_research) | External (SearXNG, ChromaDB) | Delegates to continuous_learning_loop.research() and RAG query.py |
| Self-improvement observation | Runtime Python (agent_self_improve) | -- | Delegates to error_mining, improvement_candidates, experiment_runner (all local) |

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| agent_protocol | local | Observation, Recommendation dataclasses | Created in Phase 5, all agents already import it [VERIFIED: all 5 agent files] |
| runtime_utils | local | load_json, save_json, now | Phase 1 foundation, all agents already import it [VERIFIED: all 5 agent files] |
| re | stdlib | Query keyword extraction | Simple regex matching for query parsing [VERIFIED: classifier.py uses same pattern] |

### Supporting (existing domain modules to delegate to)
| Module | Purpose | Used by Agent |
|--------|---------|---------------|
| homelab_snapshot | Container, Ollama, VPN, disk status | agent_infra |
| infra_predictor | Predictive failure analysis | agent_infra |
| health_sweep | Full-stack health probes | agent_infra |
| notification_poller | Calendar events, email check | agent_personal |
| smoking | Cigarette log and stats | agent_personal |
| finance_daily | Binance PNL, IOL portfolio | agent_finance |
| finance | Transaction summary, alerts | agent_finance |
| expenses | Weekly expense stats, alerts | agent_finance |
| continuous_learning_loop | SearXNG research | agent_research |
| error_mining | Production error extraction | agent_self_improve |
| improvement_candidates | Typed improvement proposals | agent_self_improve |
| experiment_runner | Active A/B experiments | agent_self_improve |
| self_benchmark | Performance gate checks | agent_self_improve |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| Keyword parsing in each agent | Shared classifier.py | Agents need domain-specific parsing; classifier.py is for orchestrator-level routing. Keep separate. |
| Calling skill_executor from agents | Direct module imports | skill_executor runs subprocess commands; agents should call Python functions directly for typed returns |

**Installation:**
```bash
# No installation needed -- all existing modules
```

## Architecture Patterns

### System Architecture Diagram

```
User query via Telegram
    |
    v
agent_coordinator.route(query)
    |
    +---> agent_registry.dispatch(query)
              |
              +-- for each agent where owns(query):
                      |
                      +-- agent.handle(query)
                              |
                              1. Parse query keywords
                              2. Select data source(s)
                              3. Call domain module(s) with try/except
                              4. Build Observation (summary, confidence, details)
                              5. Build Recommendation(s) if actionable
                              6. Return (Observation, list[Recommendation])
```

### Pattern 1: Query-Responsive handle()

**What:** Each agent's handle() parses the query to decide which data to fetch, rather than blindly calling assess().
**When to use:** All 5 agents.
**Example:**
```python
# Source: derived from classifier.py keyword matching pattern + agent_infra.py assess()
def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    q = query.lower()
    
    # Determine what the user is asking about
    if any(k in q for k in ("container", "docker", "restart")):
        data = _observe_containers()
    elif any(k in q for k in ("gpu", "vram", "ollama")):
        data = _observe_ollama()
    elif any(k in q for k in ("vpn", "gluetun", "leak")):
        data = _observe_vpn()
    else:
        # Broad query -- full assessment
        data = assess()
    
    obs = Observation(
        agent_name="infra",
        domain="infrastructure",
        summary=data["summary"],
        confidence=data.get("confidence", 0.7),
        details=data,
    )
    recs = _build_recommendations(data)
    return (obs, recs)
```

### Pattern 2: Private Observer Functions

**What:** Break assess() into focused _observe_X() functions that return structured dicts.
**When to use:** When an agent covers multiple sub-domains (infra: containers, GPU, VPN; finance: Binance, IOL, expenses).
**Example:**
```python
# Source: derived from homelab_snapshot.py + existing assess() pattern
def _observe_containers() -> dict:
    """Get container status. Returns dict with summary, issues, confidence."""
    try:
        from homelab_snapshot import snapshot
        snap = snapshot()
        c = snap.get("containers", {})
        issues = []
        if c.get("stopped", 0) > 0:
            issues.append(f"{c['stopped']} containers stopped")
        if c.get("unhealthy", 0) > 0:
            issues.append(f"{c['unhealthy']} unhealthy")
        return {
            "summary": f"Containers: {c.get('running', 0)}/{c.get('total', 0)} running" + (f" -- {', '.join(issues)}" if issues else ""),
            "confidence": 0.6 if issues else 0.9,
            "issues": issues,
            "raw": c,
        }
    except Exception as e:
        return {"summary": f"Containers: error ({str(e)[:60]})", "confidence": 0.3, "issues": [str(e)[:100]], "raw": {}}
```

### Pattern 3: Recommendation Generation from Domain Data

**What:** Convert domain-specific data into typed Recommendations with appropriate risk levels.
**When to use:** When agents detect actionable conditions.
**Example:**
```python
# Source: derived from agent_infra.py existing proposal pattern
def _build_recommendations(data: dict) -> list:
    recs = []
    for issue in data.get("issues", []):
        if "stopped" in issue or "unhealthy" in issue:
            recs.append(Recommendation(
                action="restart",
                target=issue,
                risk_level="medium",
                requires_approval=True,
                rationale=f"Detected: {issue}",
            ))
        elif "offline" in issue:
            recs.append(Recommendation(
                action="investigate",
                target="ollama",
                risk_level="low",
                requires_approval=False,
                rationale=f"Ollama offline -- may need model reload",
            ))
    return recs
```

### Pattern 4: Finance Query Routing

**What:** Finance agent routes between Binance, IOL, expenses, and general summary based on query keywords.
**When to use:** agent_finance.handle()
**Example:**
```python
# Source: derived from classifier.py IOL_KEYWORDS + finance.py patterns
def handle(query: str):
    q = query.lower()
    if any(k in q for k in ("binance", "btc", "crypto", "bitcoin")):
        data = _observe_binance()
    elif any(k in q for k in ("iol", "portafolio", "portfolio", "acciones", "cedears")):
        data = _observe_iol()
    elif any(k in q for k in ("gasto", "expense", "gasté", "cuánto")):
        data = _observe_expenses()
    else:
        data = _observe_full()  # combines all
    # ... build Observation + Recommendations
```

### Anti-Patterns to Avoid
- **Ignoring query parameter:** Current handle() ignores what the user asked. Every handle() MUST parse the query to return relevant data.
- **Calling skill_executor from agents:** skill_executor runs shell commands; agents should call Python modules directly for structured data.
- **LLM-dependent classification:** Query parsing inside agents uses keyword matching only (same pattern as classifier.py). No LLM calls for routing.
- **Removing assess():** Keep assess() for background cron use. handle() can call assess() for broad queries but should prefer focused _observe_X() for specific queries.
- **Catching all exceptions silently:** Each _observe_X() should catch exceptions and return degraded data with low confidence, not silently pass.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Container status | Parse `docker ps` output | `homelab_snapshot.snapshot()` | Already parses containers, Ollama, VPN into structured dict [VERIFIED: homelab_snapshot.py lines 1-20] |
| Binance PNL | Direct API call | `finance_daily.get_binance_pnl()` | Handles auth, HMAC signing, proxy config [VERIFIED: finance_daily.py line 68] |
| IOL portfolio | Direct API call | `finance_daily.get_iol_portfolio()` | Handles bearer token, refresh, error handling [VERIFIED: finance_daily.py line 186] |
| Calendar events | Parse gcal.py output | `notification_poller.check_calendar()` | Handles OAuth, gcal.py subprocess [VERIFIED: notification_poller.py line 68] |
| Email digest | Call himalaya directly | `notification_poller.check_email()` | Handles himalaya CLI, parsing [VERIFIED: notification_poller.py line 187] |
| Error mining | Scan logs directly | `error_mining.mine_all()` | Handles log rotation, pattern matching [VERIFIED: error_mining.py line 392] |
| Expense summary | Read transactions.json | `finance.summary()` / `expenses.py` functions | Handles categorization, date filtering, alerts [VERIFIED: CLAUDE.md finance section] |
| Prediction | Read Prometheus directly | `infra_predictor.predict_all()` | Handles Prometheus queries, probability calculation [VERIFIED: infra_predictor.py lines 1-15] |

**Key insight:** Every data source an agent needs is already wrapped in a callable Python function. The agent layer adds query parsing and typed output formatting -- nothing else.

## Common Pitfalls

### Pitfall 1: Modules Not Available in Container
**What goes wrong:** `from homelab_snapshot import snapshot` raises ImportError because module has a broken import chain.
**Why it happens:** Some modules import from `runtime_config` using relative imports (`from .runtime_config`) which fails when called from agents.
**How to avoid:** Every module call inside agents must be wrapped in try/except ImportError with graceful degradation. Current assess() functions already do this. [VERIFIED: agent_infra.py lines 43-54, 56-65]
**Warning signs:** Agent returning "error: ..." observations in tests.

### Pitfall 2: Slow Queries from External APIs
**What goes wrong:** agent_finance.handle() blocks for 10+ seconds waiting on Binance/IOL API.
**Why it happens:** External API calls have network latency.
**How to avoid:** Each agent should have a timeout awareness. The registry dispatch already has no timeout per agent, but skill_executor uses SKILL_TIMEOUT=30s. Individual API calls inside agents should fail fast (5s timeout). finance_daily already uses timeout=10 on urlopen. [VERIFIED: finance_daily.py line 54]
**Warning signs:** Telegram responses taking >15 seconds.

### Pitfall 3: Stale assess() Data vs Fresh handle() Data
**What goes wrong:** assess() was designed for cron (runs every 5 min), returns cached/stale data. handle() is user-initiated, needs fresh data.
**Why it happens:** assess() may read from state files written by the last cron run rather than probing live.
**How to avoid:** handle() calls domain modules directly (not via state files). assess() remains for cron use with state file caching.
**Warning signs:** User asks "how are containers" and gets a response from 5 minutes ago.

### Pitfall 4: Overlapping OWNERSHIP Keywords
**What goes wrong:** "learning" is in both agent_research.OWNERSHIP and agent_self_improve.OWNERSHIP. A query about "machine learning" activates self_improve instead of research.
**Why it happens:** Keyword sets not disjoint.
**How to avoid:** Review OWNERSHIP sets for overlaps. Overlaps are OK (Phase 7 arbitrator resolves), but each agent should set confidence appropriately -- the agent with higher confidence for a query type should win. Document known overlaps.
**Warning signs:** Wrong agent answering queries.

### Pitfall 5: Breaking Existing Tests
**What goes wrong:** test_sprint9.py tests that assert on assess() output or specific handle() behavior break.
**Why it happens:** Changing handle() internals.
**How to avoid:** handle() contract remains (Observation, list[Recommendation]). Existing tests check types, not specific summary text. Add NEW tests for query-responsive behavior; don't modify existing passing tests unless the behavior contract changes.
**Warning signs:** test_sprint9.py failures.

### Pitfall 6: agent_research Calling LLM Inside handle()
**What goes wrong:** agent_research uses continuous_learning_loop.research() which calls SearXNG/LLM and may take 30+ seconds or fail.
**Why it happens:** Research is inherently slow.
**How to avoid:** For quick queries about knowledge/RAG, use the RAG pipeline (query.py) which is local ChromaDB + embeddings. Reserve continuous_learning_loop for explicit research requests. Set confidence=0.5 for slow paths that may time out.
**Warning signs:** Research agent timing out in Telegram context.

## Code Examples

### agent_infra.py -- Query-Responsive handle()

```python
# Source: derived from agent_infra.py assess() + homelab_snapshot.py
def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    q = query.lower()
    
    # Route to specific observer based on query
    if any(k in q for k in ("container", "docker", "stopped", "unhealthy", "restart")):
        data = _observe_containers()
    elif any(k in q for k in ("gpu", "vram", "ollama", "modelo", "model")):
        data = _observe_ollama()
    elif any(k in q for k in ("vpn", "gluetun", "leak", "ip")):
        data = _observe_vpn()
    elif any(k in q for k in ("disk", "disco", "espacio", "storage")):
        data = _observe_disk()
    else:
        # Broad query -- full assessment
        data = _assess_full()
    
    obs = Observation(
        agent_name="infra",
        domain="infrastructure",
        summary=data.get("summary", "Infra: sin datos"),
        confidence=data.get("confidence", 0.5),
        details=data,
    )
    recs = _build_recommendations(data)
    save_json(str(STATUS_FILE), data)
    _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"), "confidence": obs.confidence})
    return (obs, recs)
```

### agent_finance.py -- IOL + Binance + Expenses Routing

```python
# Source: derived from agent_finance.py assess() + finance_daily.py + expenses.py
def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    q = query.lower()
    
    if any(k in q for k in ("binance", "btc", "crypto", "bitcoin")):
        data = _observe_binance()
    elif any(k in q for k in ("iol", "portafolio", "portfolio", "acciones", "cedears", "invertido")):
        data = _observe_iol()
    elif any(k in q for k in ("gasto", "gasté", "expense", "cuánto")):
        data = _observe_expenses()
    elif any(k in q for k in ("dolar", "dólar", "usd", "blue", "mep")):
        data = _observe_dolar()
    else:
        data = _observe_full()
    
    obs = Observation(
        agent_name="finance",
        domain="finance",
        summary=data.get("summary", "Finance: sin datos"),
        confidence=data.get("confidence", 0.7),
        details=data,
    )
    recs = _build_finance_recs(data)
    _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full")})
    return (obs, recs)
```

### agent_personal.py -- Calendar/Email/Smoking Routing

```python
# Source: derived from agent_personal.py assess() + notification_poller.py + smoking.py
def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    q = query.lower()
    
    if any(k in q for k in ("calendar", "calendario", "agenda", "evento")):
        data = _observe_calendar()
    elif any(k in q for k in ("email", "mail", "correo", "inbox")):
        data = _observe_email()
    elif any(k in q for k in ("smoking", "pucho", "cigarrillo", "fumando", "fumé")):
        data = _observe_smoking()
    elif any(k in q for k in ("reminder", "recordatorio", "recordar")):
        data = _observe_reminders()
    else:
        data = _observe_all_personal()
    
    obs = Observation(
        agent_name="personal",
        domain="personal",
        summary=data.get("summary", "Personal: sin datos"),
        confidence=data.get("confidence", 0.7),
        details=data,
    )
    recs = []  # Personal rarely has actionable recs
    return (obs, recs)
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| handle() ignores query, calls assess() | handle() parses query, routes to _observe_X() | Phase 6 (now) | Query-responsive observations |
| assess() is the only data path | assess() for cron, handle() for user queries | Phase 6 (now) | Fresh data for user, cached for cron |
| Agents return generic status summary | Agents return targeted details per query type | Phase 6 (now) | Better confidence, actionable recommendations |

## Assumptions Log

| # | Claim | Section | Risk if Wrong |
|---|-------|---------|---------------|
| A1 | homelab_snapshot.snapshot() is callable from agents (no circular import) | AGNT-01 | Low -- already called by agent_infra.assess(), wrapped in try/except [VERIFIED] |
| A2 | finance_daily.get_binance_pnl() and get_iol_portfolio() return dicts or None | AGNT-03 | Low -- signatures verified via grep [VERIFIED: finance_daily.py lines 68, 186] |
| A3 | notification_poller.check_calendar() returns a list of events | AGNT-02 | Medium -- function signature verified but return shape not fully inspected [ASSUMED] |
| A4 | RAG query.py can be called as a Python module (not just CLI) | AGNT-04 | Medium -- most RAG usage is CLI; may need to verify importable API [ASSUMED] |
| A5 | smoking.py has a stats/get function callable from agents | AGNT-02 | Low -- CLAUDE.md documents CLI + log.json, but Python API needs verification [ASSUMED] |

## Open Questions

1. **Should handle() for research agent use RAG query.py or continuous_learning_loop?**
   - What we know: continuous_learning_loop.research() calls SearXNG (slow, external). RAG query.py uses ChromaDB (fast, local).
   - What's unclear: Whether query.py has a Python-callable API or is CLI-only.
   - Recommendation: Use RAG for knowledge-base queries, continuous_learning_loop for explicit "research X" requests. Check query.py for importable functions.

2. **Should agents log to their own log files or to a shared agent log?**
   - What we know: Each agent already has its own LOG_FILE (logs/agent_infra.log, etc.)
   - What's unclear: Whether a unified agent log would be better for Phase 9 metrics.
   - Recommendation: Keep per-agent logs (existing pattern). Phase 9 adds Prometheus metrics.

## Project Constraints (from CLAUDE.md)

- **No new dependencies:** All solutions use existing modules and stdlib [VERIFIED: CLAUDE.md]
- **Container paths:** PYTHONPATH=/home/tenazas/runtime -- bare imports work [VERIFIED: Dockerfile]
- **Imports:** Use `from runtime_utils import load_json, save_json, now` [VERIFIED: CLAUDE.md coding rules]
- **json.dumps with datetime:** Always use `default=str` [VERIFIED: CLAUDE.md coding rules]
- **Tests:** Run via `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py` [VERIFIED: CLAUDE.md]
- **Error handling:** try/except per external module call, never crash the agent [VERIFIED: CLAUDE.md error handling patterns]
- **Safety:** agent_infra BLOCKED_TARGETS must stay (openclaw, watchtower) [VERIFIED: agent_infra.py line 22]

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
| AGNT-01 | agent_infra.handle("containers down") returns Observation with container details | unit | test_sprint9.py::TestAgentInfraHandle | No -- Wave 0 |
| AGNT-01 | agent_infra.handle("gpu status") returns Observation with GPU/Ollama details | unit | test_sprint9.py::TestAgentInfraHandle | No -- Wave 0 |
| AGNT-02 | agent_personal.handle("calendar today") returns Observation with calendar events | unit | test_sprint9.py::TestAgentPersonalHandle | No -- Wave 0 |
| AGNT-02 | agent_personal.handle("email inbox") returns Observation with email summary | unit | test_sprint9.py::TestAgentPersonalHandle | No -- Wave 0 |
| AGNT-03 | agent_finance.handle("binance pnl") returns Observation with Binance data | unit | test_sprint9.py::TestAgentFinanceHandle | No -- Wave 0 |
| AGNT-03 | agent_finance.handle("iol portfolio") returns Observation with IOL data | unit | test_sprint9.py::TestAgentFinanceHandle | No -- Wave 0 |
| AGNT-04 | agent_research.handle("research kubernetes") returns Observation with findings | unit | test_sprint9.py::TestAgentResearchHandle | No -- Wave 0 |
| AGNT-05 | agent_self_improve.handle("error_mining report") returns Observation with error data | unit | test_sprint9.py::TestAgentSelfImproveHandle | No -- Wave 0 |
| All | handle() always returns (Observation, list[Recommendation]) tuple | unit | test_sprint9.py::TestAgentHandleContract | Partial -- Phase 5 tests exist |
| All | Domain modules wrapped in try/except, never crash agent | unit | test_sprint9.py::TestAgentErrorIsolation | Partial -- Phase 5 tests exist |

### Sampling Rate
- **Per task commit:** `docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py`
- **Per wave merge:** Full suite via `docker exec openclaw bash /home/tenazas/tests/run_tests.sh`
- **Phase gate:** Full suite green before `/gsd-verify-work`

### Wave 0 Gaps
- [ ] New test classes in `tests/test_sprint9.py` -- TestAgentInfraHandle, TestAgentPersonalHandle, TestAgentFinanceHandle, TestAgentResearchHandle, TestAgentSelfImproveHandle
- [ ] Mock helpers for domain modules (homelab_snapshot, finance_daily, notification_poller, etc.)
- [ ] Tests must mock all external dependencies (no real API calls in tests)

## Security Domain

### Applicable ASVS Categories

| ASVS Category | Applies | Standard Control |
|---------------|---------|-----------------|
| V2 Authentication | No | N/A -- internal agent modules |
| V3 Session Management | No | N/A -- no sessions |
| V4 Access Control | Yes | BLOCKED_TARGETS in agent_infra prevents restart of openclaw/watchtower; requires_approval=True on high-risk recommendations |
| V5 Input Validation | Yes | Query strings capped by Telegram (4096 chars); keyword matching uses `in` not regex injection |
| V6 Cryptography | No | N/A -- no crypto in agent layer |

### Known Threat Patterns for Agent Layer

| Pattern | STRIDE | Standard Mitigation |
|---------|--------|---------------------|
| Agent recommending restart of protected container | Tampering | BLOCKED_TARGETS filter in agent_infra [VERIFIED: line 22, 68-69] |
| External API credentials leaking into Observation details | Information Disclosure | Domain modules handle credentials internally; agents only get sanitized results |
| Unbounded details dict in Observation | Denial of Service | Truncate details to reasonable size; Observation.to_dict() returns full dict |

## Sources

### Primary (HIGH confidence)
- `workspace/runtime/agent_infra.py` -- Full file analyzed, handle() and assess() patterns [VERIFIED: codebase read]
- `workspace/runtime/agent_personal.py` -- Full file analyzed [VERIFIED: codebase read]
- `workspace/runtime/agent_finance.py` -- Full file analyzed [VERIFIED: codebase read]
- `workspace/runtime/agent_research.py` -- Full file analyzed [VERIFIED: codebase read]
- `workspace/runtime/agent_self_improve.py` -- Full file analyzed [VERIFIED: codebase read]
- `workspace/runtime/agent_protocol.py` -- Observation, Recommendation contracts [VERIFIED: codebase read]
- `workspace/runtime/agent_registry.py` -- dispatch(), list_agents() [VERIFIED: codebase read]
- `workspace/runtime/agent_coordinator.py` -- route(), identify_agents() [VERIFIED: codebase read]
- `tests/test_sprint9.py` -- 95 existing tests, all class structures [VERIFIED: codebase read]
- `.planning/phases/05-foundation-contracts/05-03-SUMMARY.md` -- Phase 5 agent refactor details [VERIFIED]

### Secondary (MEDIUM confidence)
- `workspace/runtime/homelab_snapshot.py` -- snapshot() function verified [VERIFIED: lines 1-60]
- `workspace/runtime/finance_daily.py` -- get_binance_pnl(), get_iol_portfolio() signatures [VERIFIED: grep]
- `workspace/runtime/notification_poller.py` -- check_calendar(), check_email() signatures [VERIFIED: grep]
- `workspace/runtime/error_mining.py` -- mine_all() signature [VERIFIED: grep]
- `workspace/runtime/improvement_candidates.py` -- list_candidates(), generate() [VERIFIED: grep]
- `workspace/runtime/experiment_runner.py` -- list_active() [VERIFIED: grep]

### Tertiary (LOW confidence)
- RAG query.py Python API importability [ASSUMED -- needs verification]
- smoking.py Python callable API [ASSUMED -- needs verification]
- notification_poller.check_calendar() return shape [ASSUMED]

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- all existing modules verified in codebase
- Architecture: HIGH -- straightforward enhancement of existing agent handle() functions
- Pitfalls: HIGH -- derived from actual code analysis of all 5 agents and their dependencies

**Research date:** 2026-04-29
**Valid until:** 2026-05-29 (stable -- all internal modules, no external deps)
