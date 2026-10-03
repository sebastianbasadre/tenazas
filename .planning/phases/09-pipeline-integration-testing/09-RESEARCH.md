# Phase 9: Pipeline Integration & Testing - Research

**Researched:** 2026-05-04
**Domain:** Telegram routing integration, feature-flag rollout, Prometheus/Grafana observability, test pyramid for multi-agent coordinator
**Confidence:** HIGH (key findings from direct codebase inspection; no external library changes required)

---

<user_constraints>
## User Constraints (from CONTEXT.md)

No CONTEXT.md exists for Phase 9.
</user_constraints>

<phase_requirements>
## Phase Requirements

| ID | Description | Research Support |
|----|-------------|------------------|
| INTG-01 | Wire coordinator into `telegram_router.py` as the new routing entry point | `workspace/runtime/agent_coordinator.py` already exists and routes queries; `telegram_router.py` is the integration seam |
| INTG-03 | Feature flag `USE_AGENT_COORDINATOR` for enable/disable without code changes | Existing config-centralization patterns favor `runtime_config.py` or env-first config reads; no such flag exists yet |
| INTG-04 | Prometheus metrics per agent (latency, success rate, query count) + Grafana dashboard | Current stack already exports metrics on `:9105`, `:9106`, `:9107`, `:9108`, `:9109`; `metrics_server.py` already owns `:9105`, so Phase 9 should extend that path, not invent a new exporter |
| TEST-01 | Unit tests for every new module | Existing suites already cover coordinator and correlation IDs; add targeted unit tests for flag/wiring/metrics helpers |
| TEST-02 | Integration tests for full pipeline | Existing repo uses mocked end-to-end Python tests; Phase 9 should test router -> coordinator -> agent -> skill path with dependency patching |
| TEST-03 | Regression tests ensuring routing preserved | Existing routing/classifier behavior can be snapshotted with focused regression fixtures |
</phase_requirements>

---

## Summary

Phase 9 is not a net-new architecture phase. The coordinator already exists in [`workspace/runtime/agent_coordinator.py`](/X:/openclaw/workspace/runtime/agent_coordinator.py:1), all five agents already accept `correlation_id`, and there are already tests asserting Phase 8 routing behavior. The real work is operational integration: making `telegram_router.py` choose coordinator vs legacy path behind a feature flag, exposing coordinator/agent metrics with the same Prometheus conventions the project already uses, and building enough integration/regression coverage to switch the flag safely.

The safest implementation is an adapter rollout, not a rewrite. Keep `telegram_router.py` as the Telegram boundary, inject a small decision point near the current execution path, and route through `agent_coordinator.route(query)` only when the feature flag is enabled. Preserve the current monolithic path as the immediate fallback. This minimizes blast radius and satisfies the zero-code-change rollback requirement in INTG-03.

The largest risk is fragmented observability: there is already rich metrics infrastructure, but no single per-agent metric family for query count, latency, and outcome. If Phase 9 adds metrics in ad hoc locations, Grafana and regression debugging will get worse, not better. The right move is a single small `agent_metrics.py` helper or equivalent centralized instrumentation called from coordinator entry/exit points and agent execution wrappers.

One planning caveat is verified from the local repo: the planning metadata is stale. `.planning/ROADMAP.md` and `.planning/STATE.md` still describe Phase 8 as not started/current, but the codebase already contains `agent_coordinator.py` plus Phase 8-style tests. For Phase 9, direct code inspection is more trustworthy than status docs when deciding what remains.

**Primary recommendation:** Treat Phase 9 as three deliverables in order: `(1)` feature-flag router integration, `(2)` centralized per-agent metrics emission, `(3)` router/coordinator integration + regression tests. Do not refactor core coordinator logic again in this phase.

---

## Current Verified Implementation State

- `telegram_router.py` has no current `agent_coordinator` hook; free-text traffic still goes to deterministic skill intercepts or direct LLM fallback.
- `agent_coordinator.py` already implements claimant detection, `first_wins` / `parallel` / `skill_fallback` strategies, aggregation, and correlation-ID logging.
- `runtime_config.py` centralizes runtime settings, but there is no `USE_AGENT_COORDINATOR` helper or equivalent feature flag yet.
- `metrics_server.py` already owns the Prometheus scrape endpoint on `:9105` and is driven by shared state plus log parsing; Phase 9 should extend this pipeline instead of starting a second coordinator-specific exporter.
- Existing tests cover coordinator behavior and correlation IDs, but not router flag integration, exported coordinator metrics, or router/coordinator parity.

---

## Architectural Responsibility Map

| Capability | Primary Tier | Secondary Tier | Rationale |
|------------|-------------|----------------|-----------|
| Telegram ingress | `telegram_router.py` | — | Existing production boundary; Phase 9 only changes dispatch target |
| Routing brain | `agent_coordinator.route()` | legacy orchestrator path | Already implemented; feature flag selects which one handles the query |
| Feature flag decision | config helper (`runtime_config.py` or equivalent) | env var | Existing project pattern is env-first then config |
| Multi-agent execution | `agent_coordinator.py` | agent modules | Already supports first-wins / parallel / skill fallback |
| Skill fallback | `skill_executor.execute_skill()` | LLM fallback | Existing coordinator behavior; no reason to replace in Phase 9 |
| Per-agent telemetry | centralized metrics helper | coordinator log JSONL | Prometheus needs stable, low-cardinality metrics |
| Dashboard visualization | Grafana dashboard JSON | Prometheus queries | Existing repo stores dashboard JSON in-root |
| Integration verification | tests + feature flag | logs | Safer than live-only validation |

---

## Standard Stack

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| `prometheus_client` | existing dependency | Query count, latency, success/failure gauges/counters | Already used broadly in `metrics_server.py` and `finance_exporter.py` |
| `json` / `pathlib` / `time` | stdlib | Logging and metric timestamps | Project standard, zero extra deps |
| `unittest` + `unittest.mock` | stdlib | Integration and regression tests | Existing test suite already uses this extensively |
| `ThreadPoolExecutor` | stdlib | Existing multi-agent parallel execution | Already adopted in coordinator; no new concurrency primitive needed |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| `runtime_config.py` | local | Feature-flag/config resolution | Add `USE_AGENT_COORDINATOR` here or equivalent helper |
| `agent_coordinator.py` | local | New routing path | Enabled path only |
| `telegram_router.py` | local | Telegram ingress and response formatting | Flag gate belongs here |
| `metrics_server.py` | local | Existing Prometheus endpoint `:9105` | Export aggregated coordinator/agent metrics here or through shared state/log parsing, not a second HTTP server |
| `grafana-*.json` | repo assets | Dashboard provisioning | Reuse existing JSON-managed dashboard workflow |

**Installation:** No new external packages are required if Phase 9 extends the current observability stack.

---

## Architecture Patterns

### Recommended Project Structure

```
workspace/runtime/
├── telegram_router.py          # MODIFY - add feature-flag dispatch
├── agent_coordinator.py        # MODIFY lightly - add metric hooks only
├── runtime_config.py           # MODIFY - expose USE_AGENT_COORDINATOR helper
├── metrics_server.py           # MODIFY - publish coordinator/agent metrics
├── agent_registry.py           # optional read-only dependency for agent listing
├── logs/
│   ├── agent_coordinator.log
│   └── telegram_router.log
tests/
├── test_phase9_router_flag.py  # NEW - flag on/off router behavior
├── test_phase9_metrics.py      # NEW - per-agent metric updates
└── test_phase9_e2e.py          # NEW - mocked pipeline integration
```

### Pattern 1: Feature-Flag Router Adapter

**What:** Keep Telegram parsing exactly where it is and branch only at the handoff point.

**When to use:** Every incoming Telegram text query after deterministic command/approval handling.

**Example:**
```python
# Pattern derived from existing runtime_config + coordinator usage
from runtime_config import use_agent_coordinator
import agent_coordinator

def _route_query(query: str) -> str:
    if use_agent_coordinator():
        result = agent_coordinator.route(query)
        if result.get("handled") and result.get("aggregated"):
            return result["aggregated"]
    return _legacy_route_query(query)
```

**Why this is the right pattern:** It preserves a one-line rollback path and keeps Telegram-side behavior stable while Phase 9 hardens the coordinator.

### Pattern 2: Coordinator-Owned Metrics

**What:** Instrument at coordinator execution boundaries rather than inside every agent implementation.

**When to use:** Query count, route strategy, per-agent latency, success/failure, fallback type.

**Example metric families:**
```python
agent_queries_total{agent="finance"}
agent_query_latency_seconds{agent="finance"}
agent_query_success_total{agent="finance", outcome="ok|error|timeout"}
coordinator_routes_total{strategy="first_wins|parallel|skill_fallback"}
coordinator_fallback_total{source="skill_executor|llm_needed"}
```

**Why this is the right pattern:** Low cardinality, one place to maintain, and no need to duplicate instrumentation logic across five agents.

**Implementation note from current code:** `metrics_server.py` is a polling exporter. The cleanest fit is to persist coordinator/agent metric state or parse coordinator logs, rather than calling `start_http_server()` from coordinator code.

### Pattern 3: Mocked Integration Tests Instead of Live Telegram

**What:** Patch router-side Telegram API calls and downstream agent/skill execution to verify behavior deterministically.

**When to use:** Full pipeline tests in CI or local runs.

**Example scope:**
- input message enters router
- feature flag on
- coordinator returns aggregated response
- router sends that response
- correlation ID and routing logs emitted

**Why this is the right pattern:** Fast, deterministic, and aligned with the current repo’s test style.

---

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Feature flag framework | Custom flag registry system | Simple env/config helper in `runtime_config.py` | One boolean flag does not justify a framework |
| Metrics transport | Custom HTTP exporter | Existing Prometheus client / `metrics_server.py` pattern | Export stack already exists |
| E2E Telegram harness | Live Bot API integration tests | `unittest.mock`-based integration tests | Live Telegram tests are flaky and slow |
| Response synthesis | New LLM summarizer for multi-agent output | Existing deterministic `_aggregate()` | Phase 8 already defined deterministic aggregation |
| Agent health database | SQLite or Redis for Phase 9 | Existing logs + Prometheus metrics | Adds ops overhead with no requirement support |

**Key insight:** Phase 9 should exploit the infrastructure already built in Phases 5-8. Any “clean slate” solution here is over-engineering.

---

## Common Pitfalls

### Pitfall 1: Wiring Coordinator Too Early in Router Flow
**What goes wrong:** Coordinator intercepts approval callbacks, voice/photo flows, or deterministic commands that should bypass it.
**Why it happens:** `telegram_router.py` handles multiple message classes, not just free-text routing.
**How to avoid:** Insert the feature-flag branch only in the existing free-text query route, after approval/admin special cases.
**Warning signs:** Approval buttons stop working, media messages get treated like text queries.

### Pitfall 2: High-Cardinality Prometheus Labels
**What goes wrong:** Metrics become expensive or unusable if labels include `query`, `correlation_id`, or raw errors.
**Why it happens:** Logging concerns get mixed into metrics concerns.
**How to avoid:** Keep labels to bounded enums like `agent`, `strategy`, `outcome`, `source`.
**Warning signs:** Dashboard queries get noisy, Prometheus series count spikes.

### Pitfall 3: Dual Path Behavior Drift
**What goes wrong:** Flag-on and flag-off paths produce materially different answers for the same query.
**Why it happens:** Regression tests cover only coordinator behavior, not parity against legacy routing.
**How to avoid:** Add regression fixtures for top routing patterns and assert handled domain + output class in both modes.
**Warning signs:** Feature flag is technically reversible, but rollback changes user-visible behavior too much.

### Pitfall 4: Testing Only Happy Paths
**What goes wrong:** Coordinator path looks green until one agent throws or times out under Telegram traffic.
**Why it happens:** Integration tests ignore degraded outcomes.
**How to avoid:** Add tests for single-agent exception, multi-agent partial timeout, and skill fallback under flag-on mode.
**Warning signs:** Production logs show `agent_error` or `parallel_timeout` cases never seen in tests.

---

## Code Examples

Verified patterns from the current codebase:

### Correlation ID and Route Entry
Source: [agent_coordinator.py](/X:/openclaw/workspace/runtime/agent_coordinator.py:198)

```python
def route(query: str) -> dict:
    correlation_id = str(uuid.uuid4())[:8]
    _log({"action": "route_start", "correlation_id": correlation_id, "query": query[:80]})
```

### Parallel Agent Execution
Source: [agent_coordinator.py](/X:/openclaw/workspace/runtime/agent_coordinator.py:109)

```python
with ThreadPoolExecutor(max_workers=len(callable_agents)) as pool:
    futures = {
        pool.submit(mod.handle, query, correlation_id=correlation_id): name
        for name, mod in callable_agents.items()
    }
```

### Existing Prometheus Gauge Pattern
Source: [metrics_server.py](/X:/openclaw/workspace/runtime/metrics_server.py:155)

```python
G_IOL_PORTFOLIO_ARS = Gauge("iol_portfolio_value_ars", "IOL portfolio total value ARS")
G_IOL_PORTFOLIO_PNL = Gauge("iol_portfolio_pnl_day_ars", "IOL portfolio day P&L ARS")
```

### Existing Coordinator Coverage Anchor
Source: [tests/test_agent_correlation_id.py](/X:/openclaw/tests/test_agent_correlation_id.py:36)

```python
self.assertIn("correlation_id", params,
              f"{mod_name}.handle() missing 'correlation_id' parameter")
```

---

## Research Conclusions

### What is already done
- Coordinator routing engine exists.
- All five agents accept `correlation_id`.
- There is already direct test coverage for correlation IDs and coordinator behavior.
- Prometheus/Grafana infrastructure already exists and is repo-managed.
- `telegram_router.py` still does not call the coordinator, so this phase remains a real integration step rather than a docs-only cleanup.

### What Phase 9 actually needs
1. Router integration behind `USE_AGENT_COORDINATOR`.
2. Centralized per-agent/coordinator metrics.
3. Dashboard updates for those metrics.
4. Integration and regression test suites that compare flag-on vs flag-off behavior.

### Recommended implementation order
1. Add config helper for `USE_AGENT_COORDINATOR`.
2. Add router dispatch branch with legacy fallback.
3. Add coordinator metrics emission.
4. Add Grafana panel JSON updates.
5. Add tests: router flag, metrics helper, end-to-end mocked path, regression parity.

### Confidence notes
- HIGH confidence that no new dependency is needed.
- HIGH confidence that Telegram integration should be adapter-style, not rewrite-style.
- HIGH confidence that coordinator metrics should plug into the existing `metrics_server.py` pipeline instead of introducing a new exporter process.
- MEDIUM confidence on exact Grafana JSON file to extend, because multiple finance/runtime dashboards exist and Phase 9 should choose one intentionally rather than append everywhere.

---

## Next Planning Guidance

The planner for Phase 9 should split work into at least three plans:

1. Router feature-flag integration.
2. Coordinator/agent observability metrics + dashboard.
3. Integration/regression tests and rollout validation.

Avoid mixing all three into one execution plan. The rollback boundary is the feature flag, and the validation boundary is the test suite plus metrics visibility.
