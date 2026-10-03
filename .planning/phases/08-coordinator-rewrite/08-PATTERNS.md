# Phase 8: Coordinator Rewrite — Pattern Map

**Mapped:** 2026-04-29
**Files analyzed:** 7 (1 rewrite, 5 modify, 1 test modify)
**Analogs found:** 7 / 7

---

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|---|---|---|---|---|
| `workspace/runtime/agent_coordinator.py` | coordinator/router | event-driven, parallel dispatch | `workspace/runtime/agent_coordinator.py` (existing scaffold) | exact — full rewrite of same file |
| `workspace/runtime/agent_infra.py` | agent/service | request-response | `workspace/runtime/agent_infra.py` (existing) | exact — kwarg addition only |
| `workspace/runtime/agent_personal.py` | agent/service | request-response | `workspace/runtime/agent_personal.py` (existing) | exact — kwarg addition only |
| `workspace/runtime/agent_finance.py` | agent/service | request-response | `workspace/runtime/agent_finance.py` (existing) | exact — kwarg addition only |
| `workspace/runtime/agent_research.py` | agent/service | request-response | `workspace/runtime/agent_research.py` (existing) | exact — kwarg addition only |
| `workspace/runtime/agent_self_improve.py` | agent/service | request-response | `workspace/runtime/agent_self_improve.py` (existing) | exact — kwarg addition only |
| `tests/test_sprint9.py` | test | unit | `tests/test_sprint9.py` (existing TestCoordinator class) | exact — append new class |

---

## Pattern Assignments

### `workspace/runtime/agent_coordinator.py` (coordinator, parallel dispatch) — FULL REWRITE

**Analog:** `workspace/runtime/agent_coordinator.py` (existing scaffold, lines 1–219) plus research patterns

**File header + imports pattern** (lines 1–17 of existing scaffold, to be kept):
```python
#!/usr/bin/env python3
"""Agent Coordinator — Routes queries to appropriate agent(s).

Usage:
    python3 agent_coordinator.py --route "como esta todo"
    python3 agent_coordinator.py --agents   # list agents + status
"""

import json
from pathlib import Path
from runtime_utils import now
```
Add to imports block:
```python
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed, TimeoutError as FutureTimeout
import importlib
```

**Module-level constants pattern** (lines 18–31 of existing scaffold, to be kept with minor changes):
```python
BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
COORD_LOG = STATE_DIR / "agents" / "coordinator_log.json"
LOG_FILE = BASE_DIR / "logs" / "agent_coordinator.log"

# Import agents lazily to avoid circular imports
AGENT_MODULES = {
    "infra": "agent_infra",
    "personal": "agent_personal",
    "finance": "agent_finance",
    "research": "agent_research",
    "self_improve": "agent_self_improve",
}
```
Add after AGENT_MODULES:
```python
DOMAIN_EMOJI = {
    "infra": "📡",
    "personal": "👤",
    "finance": "💰",
    "research": "🔍",
    "self_improve": "🔧",
}
```

**Logging helper pattern** (lines 48–53 of existing scaffold — KEEP VERBATIM):
```python
def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass
```

**Structured history log pattern** (lines 55–68 of existing scaffold — KEEP VERBATIM):
```python
def _save_coord_log(entry: dict):
    COORD_LOG.parent.mkdir(parents=True, exist_ok=True)
    try:
        existing = []
        if COORD_LOG.exists():
            existing = json.loads(COORD_LOG.read_text())
            if not isinstance(existing, list):
                existing = []
        existing.append(entry)
        existing = existing[-200:]
        with open(COORD_LOG, "w") as f:
            json.dump(existing, f, indent=2, default=str)
    except Exception:
        pass
```

**Lazy agent loader pattern** (lines 71–77 of existing scaffold — KEEP VERBATIM):
```python
def _load_agent(name: str):
    """Lazily import an agent module."""
    try:
        import importlib
        return importlib.import_module(AGENT_MODULES[name])
    except Exception:
        return None
```

**Core route() function — NEW pattern** (from RESEARCH.md Pattern 1–4 + CONTEXT.md D-01 to D-04):
```python
def route(query: str) -> dict:
    """Route query to appropriate agent(s) and aggregate responses.

    Returns: {query, correlation_id, strategy, agents, responded, aggregated, handled}
    """
    correlation_id = str(uuid.uuid4())[:8]
    _log({"action": "route_start", "correlation_id": correlation_id, "query": query[:80]})
    t0 = now()

    claimants = _get_claimants(query)

    if not claimants:
        strategy = "skill_fallback"
        result = _do_skill_fallback(query, correlation_id)
        elapsed = (now() - t0).total_seconds()
        _log({"action": "route_done", "correlation_id": correlation_id,
              "strategy": strategy, "agents": [], "elapsed": elapsed})
        _save_coord_log({**result, "correlation_id": correlation_id,
                         "strategy": strategy, "query": query,
                         "agents": [], "ts": now().isoformat()})
        return {**result, "correlation_id": correlation_id, "strategy": strategy,
                "query": query, "agents": []}

    elif len(claimants) == 1:
        strategy = "first_wins"
        results = _run_single(claimants[0], query, correlation_id)
    else:
        strategy = "parallel"
        results = _run_parallel(claimants, query, correlation_id)

    if len(results) > 1:
        aggregated = _aggregate(results)
    elif results:
        name = list(results.keys())[0]
        obs, _ = results[name]
        aggregated = obs.summary if obs and obs.summary else None
    else:
        aggregated = None

    elapsed = (now() - t0).total_seconds()
    responded = list(results.keys())
    _log({"action": "route_done", "correlation_id": correlation_id,
          "strategy": strategy, "agents": claimants,
          "responded": responded, "elapsed": elapsed})
    _save_coord_log({"query": query, "correlation_id": correlation_id,
                     "strategy": strategy, "agents": claimants,
                     "responded": responded, "handled": bool(aggregated),
                     "ts": now().isoformat()})
    return {
        "query": query,
        "correlation_id": correlation_id,
        "strategy": strategy,
        "agents": claimants,
        "responded": responded,
        "aggregated": aggregated,
        "handled": bool(aggregated),
    }
```

**Parallel owns() evaluation pattern** (RESEARCH.md Pattern 1):
```python
def _get_claimants(query: str) -> list:
    """Run all owns() in parallel. Returns list of agent names that claimed."""
    claimants = []
    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = {
            pool.submit(_load_agent(name).owns, query): name
            for name in AGENT_MODULES
            if _load_agent(name) is not None
        }
        for future in as_completed(futures, timeout=2):
            name = futures[future]
            try:
                if future.result():
                    claimants.append(name)
            except Exception:
                pass  # owns() failure = no claim
    return claimants
```

**Parallel handle() execution pattern** (RESEARCH.md Pattern 2):
```python
def _run_parallel(claimants: list, query: str, correlation_id: str) -> dict:
    """Call handle() on all claimants in parallel. Timeout = 30s per agent."""
    results = {}
    with ThreadPoolExecutor(max_workers=len(claimants)) as pool:
        futures = {
            pool.submit(AGENT_REGISTRY[name].handle, query,
                        correlation_id=correlation_id): name
            for name in claimants
        }
        for future in as_completed(futures, timeout=31):
            name = futures[future]
            try:
                obs, recs = future.result(timeout=30)
                results[name] = (obs, recs)
            except FutureTimeout:
                _log({"action": "agent_timeout", "agent": name,
                      "correlation_id": correlation_id})
            except Exception as e:
                _log({"action": "agent_error", "agent": name,
                      "error": str(e)[:100], "correlation_id": correlation_id})
    return results
```

**Single-agent direct call pattern**:
```python
def _run_single(name: str, query: str, correlation_id: str) -> dict:
    """Direct call to one agent. No threading overhead."""
    try:
        agent = _load_agent(name)
        if agent is None:
            return {}
        obs, recs = agent.handle(query, correlation_id=correlation_id)
        return {name: (obs, recs)}
    except Exception as e:
        _log({"action": "agent_error", "agent": name,
              "error": str(e)[:100], "correlation_id": correlation_id})
        return {}
```

**Emoji-labeled aggregation pattern** (RESEARCH.md Pattern 4 + CONTEXT.md D-04):
```python
def _aggregate(results: dict) -> str:
    """Aggregate multiple agent results using emoji-labeled sections.

    results: {agent_name: (Observation, list[Recommendation])}
    """
    parts = []
    for name, (obs, _recs) in results.items():
        if not obs or not obs.summary:
            continue
        emoji = DOMAIN_EMOJI.get(name, "•")
        label = name.replace("_", " ").title()
        parts.append(f"{emoji} {label}: {obs.summary}")
    return "\n".join(parts) if parts else None
```

**Skill fallback pattern** (RESEARCH.md Pattern 5 + CONTEXT.md D-01):
```python
def _do_skill_fallback(query: str, correlation_id: str) -> dict:
    """Zero-claim fallback: try skill_executor, then signal LLM needed."""
    from classifier import classify_query
    from skill_executor import execute_skill

    category = classify_query(query)
    if category.startswith("skill:"):
        result = execute_skill(category, query)
        _log({"action": "skill_fallback", "category": category,
              "success": result.get("success"), "correlation_id": correlation_id})
        return {
            "handled": result.get("success", False),
            "output": result.get("output"),
            "source": "skill_executor",
        }
    # Pure chat — signal caller to use LLM
    _log({"action": "llm_fallback", "category": category,
          "correlation_id": correlation_id})
    return {"handled": False, "output": None, "source": "llm_needed"}
```

**Backward-compat helpers to retain** (lines 83–110 of existing scaffold):
- `is_multi_domain(query)` — keep for existing callers
- `identify_agents(query)` — keep for existing TestCoordinator tests
- `list_agents()` — keep for `--agents` CLI flag
- `resolve_conflict(proposals)` — keep as pass-through to agent_arbitrator

---

### `workspace/runtime/agent_infra.py` (agent, request-response) — KWARG ADDITION ONLY

**Analog:** `workspace/runtime/agent_infra.py` lines 224–260

**Current handle() signature** (line 224):
```python
def handle(query: str):
```

**New handle() signature** (only this line changes):
```python
def handle(query: str, correlation_id: str = None):
```

**Updated log line at end of handle()** (line 259, add `correlation_id` field):
```python
_log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"),
      "confidence": obs.confidence, "correlation_id": correlation_id})
```

No other changes. The full function body (lines 225–260) stays verbatim.

---

### `workspace/runtime/agent_personal.py` (agent, request-response) — KWARG ADDITION ONLY

**Analog:** `workspace/runtime/agent_personal.py` lines 137–167

**Current handle() signature** (line 137):
```python
def handle(query: str):
```

**New handle() signature**:
```python
def handle(query: str, correlation_id: str = None):
```

**Updated log line** (line 166, add `correlation_id` field):
```python
_log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"),
      "confidence": obs.confidence, "correlation_id": correlation_id})
```

No other changes.

---

### `workspace/runtime/agent_finance.py` (agent, request-response) — KWARG ADDITION ONLY

**Analog:** `workspace/runtime/agent_finance.py` lines 163–193

**Current handle() signature** (line 163):
```python
def handle(query: str):
```

**New handle() signature**:
```python
def handle(query: str, correlation_id: str = None):
```

**Updated log line** (line 192, add `correlation_id` field):
```python
_log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"),
      "confidence": obs.confidence, "correlation_id": correlation_id})
```

No other changes.

---

### `workspace/runtime/agent_research.py` (agent, request-response) — KWARG ADDITION ONLY

**Analog:** `workspace/runtime/agent_research.py` lines 98–136

**Current handle() signature** (line 98):
```python
def handle(query: str):
```

**New handle() signature**:
```python
def handle(query: str, correlation_id: str = None):
```

**Updated log lines** (lines 133–135, add `correlation_id` field to both branches):
```python
if recs:
    _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"),
          "recs": len(recs), "correlation_id": correlation_id})
else:
    _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"),
          "confidence": obs.confidence, "correlation_id": correlation_id})
```

No other changes.

---

### `workspace/runtime/agent_self_improve.py` (agent, request-response) — KWARG ADDITION ONLY

**Analog:** `workspace/runtime/agent_self_improve.py` lines 168–198

**Current handle() signature** (line 168):
```python
def handle(query: str):
```

**New handle() signature**:
```python
def handle(query: str, correlation_id: str = None):
```

**Updated log line** (line 197, add `correlation_id` field):
```python
_log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"),
      "confidence": obs.confidence, "correlation_id": correlation_id})
```

No other changes.

---

### `tests/test_sprint9.py` (test) — APPEND NEW CLASS

**Analog:** `tests/test_sprint9.py` lines 207–280 (existing `TestCoordinator` class)

**Existing test class header pattern** (lines 207–211 — follow this section-header format):
```python
# ══════════════════════════════════════════════════════════════
#  Coordinator Rewrite — owns() + correlation_id + strategies
# ══════════════════════════════════════════════════════════════

class TestCoordinatorRewrite(unittest.TestCase):
```

**Existing mock pattern for coordinator** (lines 244–255 — patch `_load_agent` or coordinator internals):
```python
def test_route_returns_structure(self):
    import agent_coordinator as ac
    with patch.object(ac, "_load_agent") as mock_load:
        mock_agent = MagicMock()
        mock_agent.owns.return_value = True
        mock_agent.handle_query.return_value = "test response"
        mock_load.return_value = mock_agent
        result = ac.route("containers status")
    self.assertIn("query", result)
    self.assertIn("agents", result)
```

**New test class mock pattern** — patch module-level helpers `_get_claimants`, `_run_single`, `_run_parallel`, `_do_skill_fallback` (from RESEARCH.md test skeleton):
```python
class TestCoordinatorRewrite(unittest.TestCase):

    def test_route_generates_correlation_id(self):
        import agent_coordinator as ac
        with patch.object(ac, "_get_claimants", return_value=[]):
            with patch.object(ac, "_do_skill_fallback",
                              return_value={"handled": False, "output": None}):
                result = ac.route("test query")
        self.assertIn("correlation_id", result)
        self.assertEqual(len(result["correlation_id"]), 8)

    def test_route_single_agent_uses_first_wins_strategy(self):
        import agent_coordinator as ac
        with patch.object(ac, "_get_claimants", return_value=["infra"]):
            with patch.object(ac, "_run_single",
                              return_value={"infra": (MagicMock(summary="ok"), [])}):
                result = ac.route("containers down")
        self.assertEqual(result["strategy"], "first_wins")

    def test_route_multi_agent_uses_parallel_strategy(self):
        import agent_coordinator as ac
        with patch.object(ac, "_get_claimants", return_value=["infra", "finance"]):
            with patch.object(ac, "_run_parallel", return_value={}):
                result = ac.route("como esta todo el sistema y mis finanzas")
        self.assertEqual(result["strategy"], "parallel")

    def test_route_zero_claims_uses_skill_fallback(self):
        import agent_coordinator as ac
        with patch.object(ac, "_get_claimants", return_value=[]):
            with patch.object(ac, "_do_skill_fallback",
                              return_value={"handled": False, "output": None}) as mock_fb:
                ac.route("completely unrecognized query xyz")
        mock_fb.assert_called_once()

    def test_aggregate_uses_emoji_labels(self):
        import agent_coordinator as ac
        mock_infra = MagicMock()
        mock_infra.summary = "containers OK"
        mock_finance = MagicMock()
        mock_finance.summary = "IOL: $1.2M"
        result = ac._aggregate({
            "infra": (mock_infra, []),
            "finance": (mock_finance, []),
        })
        self.assertIn("📡", result)
        self.assertIn("💰", result)
        self.assertIn("containers OK", result)
        self.assertIn("IOL: $1.2M", result)

    def test_agent_exception_does_not_crash_coordinator(self):
        import agent_coordinator as ac
        with patch.object(ac, "_get_claimants", return_value=["infra"]):
            with patch.object(ac, "_run_single", side_effect=Exception("agent blew up")):
                result = ac.route("infra query")
        self.assertIn("correlation_id", result)

    def test_owns_kwarg_backward_compat_empty_query(self):
        """handle("") must still work for agents_worker.py proactive caching."""
        import agent_infra as ai
        with patch("agent_infra.homelab_snapshot", create=True) as mock_snap:
            mock_snap.snapshot.return_value = {
                "containers": {"running": 20, "total": 20, "stopped": 0, "unhealthy": 0},
                "ollama": {"status": "online"},
            }
            obs, recs = ai.handle("")  # no correlation_id
        self.assertIsNotNone(obs)

    def test_handle_accepts_correlation_id_kwarg(self):
        """All 5 agents must accept correlation_id=None without error."""
        import agent_infra, agent_personal, agent_finance, agent_research, agent_self_improve
        for mod in [agent_infra, agent_personal, agent_finance, agent_research, agent_self_improve]:
            import inspect
            sig = inspect.signature(mod.handle)
            self.assertIn("correlation_id", sig.parameters,
                          f"{mod.__name__}.handle() missing correlation_id kwarg")
```

---

## Shared Patterns

### _log() — JSONL logging helper
**Source:** `workspace/runtime/agent_infra.py` lines 26–31 (identical in all 5 agents)
**Apply to:** All agent modules and agent_coordinator.py (already present)
```python
def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass
```

### owns() — OWNERSHIP keyword matcher
**Source:** `workspace/runtime/agent_infra.py` line 34–35 (identical pattern in all 5 agents)
**Apply to:** All 5 agent modules (already implemented — do NOT modify)
```python
def owns(topic: str) -> bool:
    return any(k in topic.lower() for k in OWNERSHIP)
```
Note: topic is lowercased inside `owns()`. Pass raw query from coordinator.

### handle() return contract
**Source:** `workspace/runtime/agent_infra.py` lines 224–260 (same structure in all 5 agents)
**Apply to:** All 5 agent modules when adding `correlation_id` kwarg
```python
def handle(query: str, correlation_id: str = None):
    """Returns (Observation, list[Recommendation])."""
    # ... routing logic and observers ...
    obs = Observation(
        agent_name="<domain>",
        domain="<domain>",
        summary=data.get("summary", "<Domain>: sin datos"),
        confidence=data.get("confidence", 0.5),
        details=data,
    )
    recs = _build_*_recs(data)
    # ... state caching ...
    _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"),
          "confidence": obs.confidence, "correlation_id": correlation_id})
    return (obs, recs)
```

### State dir path pattern
**Source:** `workspace/runtime/agent_infra.py` lines 13–18 (identical in all 5 agents)
**Apply to:** `agent_coordinator.py` (already uses the same pattern)
```python
BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
```

### Test class header format
**Source:** `tests/test_sprint9.py` line 207–211
**Apply to:** New `TestCoordinatorRewrite` class
```python
# ══════════════════════════════════════════════════════════════
#  Coordinator Rewrite — <description>
# ══════════════════════════════════════════════════════════════

class TestCoordinatorRewrite(unittest.TestCase):
```

---

## No Analog Found

All files have exact analogs in the codebase. No greenfield patterns needed.

---

## Key Constraints Extracted from Code Inspection

1. **agents_worker.py backward compat:** Calls `agent.handle("")` with no kwargs (RESEARCH.md Finding 8). All 5 `handle()` modifications must use `correlation_id: str = None` as a keyword-only default — never positional-only.

2. **agent_registry dependency removal:** The existing `agent_coordinator.py` imports from `agent_registry` (line 16). The rewrite replaces this with direct `_load_agent()` + `AGENT_MODULES` dict. The `agent_registry` import should be dropped from the new file.

3. **MULTI_DOMAIN_QUERIES list:** Lines 33–37 of existing scaffold. This list can be dropped — the new `_get_claimants()` replaces this hardcoded trigger list with `owns()` parallel evaluation. `is_multi_domain()` can delegate to `_get_claimants()` for backward compat.

4. **_aggregate() signature change:** Existing `_aggregate(responses: dict)` takes `{str: str}`. New version takes `{str: (Observation, list)}`. The existing `TestCoordinator.test_aggregate_multiple_responses` test passes `{str: str}` — this test will break unless `_aggregate()` guards for both shapes or the test is updated alongside.

5. **Coordinator log fields:** Both log destinations (`LOG_FILE` JSONL + `COORD_LOG` JSON array) must include `correlation_id` in every entry per RESEARCH.md Pitfall 5.

---

## Metadata

**Analog search scope:** `workspace/runtime/agent_*.py`, `tests/test_sprint9.py`, `workspace/runtime/agent_protocol.py`, `workspace/runtime/skill_executor.py`
**Files scanned:** 9
**Pattern extraction date:** 2026-04-29
