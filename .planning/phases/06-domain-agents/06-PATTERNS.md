# Phase 6: Domain Agents - Pattern Map

**Mapped:** 2026-04-29
**Files analyzed:** 6 (5 agent modules + 1 test file)
**Analogs found:** 5 / 6

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `workspace/runtime/agent_infra.py` | agent (modify) | request-response | `workspace/runtime/agent_infra.py` (self -- current assess()) | exact |
| `workspace/runtime/agent_personal.py` | agent (modify) | request-response | `workspace/runtime/agent_personal.py` (self -- current assess()) | exact |
| `workspace/runtime/agent_finance.py` | agent (modify) | request-response | `workspace/runtime/agent_finance.py` (self -- current assess()) | exact |
| `workspace/runtime/agent_research.py` | agent (modify) | request-response | `workspace/runtime/agent_research.py` (self -- current handle()) | exact |
| `workspace/runtime/agent_self_improve.py` | agent (modify) | request-response | `workspace/runtime/agent_self_improve.py` (self -- current assess()) | exact |
| `tests/test_sprint9.py` | test (modify) | N/A | `tests/test_sprint9.py` (self -- existing test classes) | exact |

## Pattern Assignments

### `workspace/runtime/agent_infra.py` (agent, request-response)

**Analog:** Self (current implementation) + `workspace/runtime/classifier.py` (keyword matching pattern)

**Imports pattern** (lines 1-12):
```python
#!/usr/bin/env python3
import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now, load_json, save_json
from agent_protocol import Observation, Recommendation
```

**Module constants pattern** (lines 14-24):
```python
BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "infra_memory.json"
STATUS_FILE = AGENT_DIR / "infra_status.json"
LOG_FILE = BASE_DIR / "logs" / "agent_infra.log"

OWNERSHIP = {"container", "containers", "gpu", "ram", "disk", "network", "vpn",
             "ollama", "docker", "gluetun", "infra", "homelab", "backend_offline"}
BLOCKED_TARGETS = {"openclaw", "watchtower"}
PREFERRED_ROLE = "fast"
```

**Logging pattern** (lines 27-32):
```python
def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass
```

**Current handle() to refactor** (lines 76-95 -- replaces blind assess() call with query-responsive routing):
```python
def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    status = assess()  # <-- THIS is the problem: ignores query parameter
    obs = Observation(
        agent_name="infra",
        domain="infrastructure",
        summary=f"Infra: {status['health']}" + (f" - {', '.join(status['issues'][:3])}" if status.get("issues") else ""),
        confidence=0.9 if status["health"] == "ok" else 0.6,
        details=status,
    )
    recs = []
    for p in status.get("proposals", []):
        recs.append(Recommendation(
            action=p.get("action", "investigate"),
            target=p.get("target", ""),
            risk_level=p.get("severity", "low"),
            requires_approval=True,
            rationale=p.get("reason", ""),
        ))
    return (obs, recs)
```

**Keyword matching pattern to adopt** (from `classifier.py` lines 36-57 -- simplified `any(k in q for k in ...)` approach):
```python
# classifier.py uses regex + keyword sets; agents use simpler `any(k in q)` pattern
# already used by owns():
def owns(topic: str) -> bool:
    return any(k in topic.lower() for k in OWNERSHIP)
```

**Try/except per-module-call pattern** (from assess(), lines 42-65):
```python
    try:
        from homelab_snapshot import snapshot
        snap = snapshot()
        c = snap.get("containers", {})
        if c.get("stopped", 0) > 0 or c.get("unhealthy", 0) > 0:
            status["health"] = "degraded"
            status["issues"].append(f"{c.get('stopped',0)} stopped, {c.get('unhealthy',0)} unhealthy")
    except Exception as e:
        status["issues"].append(f"snapshot error: {str(e)[:100]}")
```

---

### `workspace/runtime/agent_finance.py` (agent, request-response)

**Analog:** Self (current implementation)

**Current handle() to refactor** (lines 55-75 -- same pattern as infra, blindly calls assess()):
```python
def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    status = assess()
    alerts = status.get("alerts", [])
    obs = Observation(
        agent_name="finance",
        domain="finance",
        summary=status.get("summary", "Finance: sin alertas") if not alerts else f"Finance: {', '.join(alerts[:3])}",
        confidence=0.85,
        details=status,
    )
    recs = []
    for alert in alerts:
        recs.append(Recommendation(
            action="alert",
            target="telegram",
            risk_level="medium",
            requires_approval=False,
            rationale=alert,
        ))
    return (obs, recs)
```

**Domain module calls pattern** (from assess(), lines 37-52):
```python
    try:
        from finance_daily import get_binance_pnl
        pnl = get_binance_pnl()
        if pnl and abs(pnl.get("change_pct", 0)) > BTC_ALERT_THRESHOLD * 100:
            status["alerts"].append(f"BTC: {pnl['change_pct']:+.1f}%")
    except Exception: pass
    try:
        from finance_daily import get_iol_portfolio
        iol = get_iol_portfolio()
        if iol:
            status["summary"] = f"IOL: ${iol.get('total', 0):,.0f}"
    except Exception: pass
```

---

### `workspace/runtime/agent_personal.py` (agent, request-response)

**Analog:** Self (current implementation)

**Current handle() to refactor** (lines 53-65):
```python
def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    status = assess()
    items = status.get("items", [])
    obs = Observation(
        agent_name="personal",
        domain="personal",
        summary=f"Personal: {len(items)} items" if items else "Personal: nada pendiente",
        confidence=0.8,
        details=status,
    )
    recs = []
    return (obs, recs)
```

**Domain module calls pattern** (from assess(), lines 35-49):
```python
    try:
        from notification_poller import check_calendar
        events = check_calendar()
        if events:
            status["items"].append(f"{len(events)} eventos hoy")
    except Exception: pass
    try:
        from notification_poller import check_email
        emails = check_email()
        if emails:
            status["items"].append(f"{len(emails)} emails")
    except Exception: pass
```

---

### `workspace/runtime/agent_research.py` (agent, request-response)

**Analog:** Self (current implementation -- already uses query parameter but only routes to SearXNG)

**Current handle() -- already query-aware but needs RAG routing** (lines 35-64):
```python
def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    findings = []
    summary = "Research: idle"
    try:
        from continuous_learning_loop import research
        findings = research([query]) or []
        if findings:
            summary = f"Research: {len(findings)} results"
            save_memory_event({"query": query, "results": len(findings)})
    except Exception:
        summary = "Research: SearXNG no disponible"
    # ... builds Observation + Recommendations from findings
```

---

### `workspace/runtime/agent_self_improve.py` (agent, request-response)

**Analog:** Self (current implementation)

**Current handle() to refactor** (lines 51-64):
```python
def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    status = assess()
    pending = status.get("pending", 0)
    experiments = status.get("experiments", 0)
    obs = Observation(
        agent_name="self_improve",
        domain="self_improvement",
        summary=f"Self-improve: {pending} pendientes, {experiments} experimentos" if pending or experiments else "Self-improve: sin propuestas",
        confidence=0.75,
        details=status,
    )
    recs = []
    return (obs, recs)
```

**Domain module calls pattern** (from assess() + run_daily(), lines 36-88):
```python
    try:
        from improvement_candidates import list_candidates
        cands = list_candidates(status="proposed")
        status["pending"] = len(cands)
    except Exception: pass
    try:
        from experiment_runner import list_active
        status["experiments"] = len(list_active())
    except Exception: pass
```

---

### `tests/test_sprint9.py` (test, N/A)

**Analog:** Self (existing test classes in same file)

**Test class structure pattern** (lines 34-99 -- TestAgentOwnership):
```python
class TestAgentOwnership(unittest.TestCase):

    def test_infra_owns_containers(self):
        import agent_infra as ai
        self.assertTrue(ai.owns("containers down"))

    def test_infra_does_not_own_email(self):
        import agent_infra as ai
        self.assertFalse(ai.owns("check my email"))
```

**Mock pattern for agent dependencies** (lines 296-316 -- TestAgentSafety):
```python
    def test_infra_assess_filters_blocked(self):
        import agent_infra as ai
        tmpdir = tempfile.mkdtemp()
        orig_sf = ai.STATUS_FILE
        ai.STATUS_FILE = Path(tmpdir) / "status.json"
        mock_snap = MagicMock(return_value={"containers": {"running": 20, "total": 20, "stopped": 0, "unhealthy": 0}, "ollama": {"status": "online"}})
        mock_pred = MagicMock(return_value=[...])
        with patch.dict("sys.modules", {"homelab_snapshot": MagicMock(snapshot=mock_snap),
                                         "infra_predictor": MagicMock(predict_all=mock_pred)}):
            import importlib
            importlib.reload(ai)
            status = ai.assess()
```

**Registry mock pattern for dispatch tests** (lines 454-535):
```python
    def _make_valid_module(self, owns_result=True, handle_result=None, role="fast"):
        from agent_protocol import Observation
        if handle_result is None:
            handle_result = (Observation("mock", "test", "ok", 0.9), [])
        mock_mod = MagicMock()
        mock_mod.owns = MagicMock(return_value=owns_result)
        mock_mod.handle = MagicMock(return_value=handle_result)
        mock_mod.PREFERRED_ROLE = role
        return mock_mod
```

**Test for typed return contract** (lines 323-335):
```python
    def test_each_agent_independent(self):
        for mod_name in ["agent_infra", "agent_personal", "agent_finance",
                         "agent_research", "agent_self_improve"]:
            try:
                import importlib
                mod = importlib.import_module(mod_name)
                self.assertTrue(hasattr(mod, "owns"))
                self.assertTrue(hasattr(mod, "handle_query"))
                self.assertTrue(hasattr(mod, "handle"))
                self.assertTrue(hasattr(mod, "PREFERRED_ROLE"))
            except Exception as e:
                self.fail(f"{mod_name} failed to import: {e}")
```

---

## Shared Patterns

### Query Keyword Matching (apply to all 5 agents)
**Source:** `workspace/runtime/classifier.py` lines 36-57 + existing `owns()` in all agents
**Apply to:** agent_infra, agent_personal, agent_finance, agent_research, agent_self_improve

```python
# Pattern: simple `any(k in q for k in ...)` -- same as owns() but for sub-domain routing
q = query.lower()
if any(k in q for k in ("container", "docker", "stopped")):
    data = _observe_containers()
elif any(k in q for k in ("gpu", "vram", "ollama")):
    data = _observe_ollama()
else:
    data = _assess_full()
```

### Try/Except per Domain Module Call (apply to all 5 agents)
**Source:** `workspace/runtime/agent_infra.py` lines 42-65
**Apply to:** All _observe_X() functions in all agents

```python
def _observe_something() -> dict:
    try:
        from some_module import some_function
        result = some_function()
        # ... parse result ...
        return {"summary": "...", "confidence": 0.9, "sub": "something", "issues": [], "raw": result}
    except Exception as e:
        return {"summary": f"Error: {str(e)[:60]}", "confidence": 0.3, "sub": "something", "issues": [str(e)[:100]], "raw": {}}
```

### Observation Construction (apply to all 5 agents)
**Source:** `workspace/runtime/agent_protocol.py` lines 32-48
**Apply to:** All handle() return paths

```python
obs = Observation(
    agent_name="infra",          # agent's name
    domain="infrastructure",     # domain string
    summary=data.get("summary", "Infra: sin datos"),
    confidence=data.get("confidence", 0.5),
    details=data,
)
```

### Recommendation Construction (apply to agents with actionable conditions)
**Source:** `workspace/runtime/agent_infra.py` lines 86-94 + `workspace/runtime/agent_protocol.py` lines 55-68
**Apply to:** agent_infra, agent_finance, agent_self_improve

```python
recs.append(Recommendation(
    action=p.get("action", "investigate"),
    target=p.get("target", ""),
    risk_level=p.get("severity", "low"),       # must be in VALID_RISK_LEVELS: low, medium, high, critical
    requires_approval=True,
    rationale=p.get("reason", ""),
))
```

### JSONL Logging (apply to all 5 agents)
**Source:** `workspace/runtime/agent_infra.py` lines 27-32
**Apply to:** All agents (already present -- add query routing info to log entries)

```python
_log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"), "confidence": obs.confidence})
```

### Test Pattern for Query-Responsive handle()
**Source:** `tests/test_sprint9.py` lines 296-316 (mock + patch.dict pattern)
**Apply to:** New test classes TestAgentInfraHandle, TestAgentPersonalHandle, etc.

```python
class TestAgentInfraHandle(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import agent_infra as ai
        self._orig_sf = ai.STATUS_FILE
        ai.STATUS_FILE = Path(self.tmpdir) / "status.json"
        self.ai = ai

    def tearDown(self):
        self.ai.STATUS_FILE = self._orig_sf
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_container_query_returns_container_details(self):
        mock_snap = MagicMock(return_value={"containers": {"running": 18, "total": 20, "stopped": 2, "unhealthy": 0}, "ollama": {"status": "online"}})
        with patch.dict("sys.modules", {"homelab_snapshot": MagicMock(snapshot=mock_snap)}):
            import importlib
            importlib.reload(self.ai)
            obs, recs = self.ai.handle("containers status")
        self.assertIsInstance(obs, Observation)
        self.assertIn("container", obs.summary.lower())
        self.assertIsInstance(recs, list)

    def test_handle_always_returns_tuple(self):
        with patch.dict("sys.modules", {"homelab_snapshot": MagicMock(snapshot=MagicMock(return_value={}))}):
            import importlib
            importlib.reload(self.ai)
            result = self.ai.handle("anything")
        self.assertIsInstance(result, tuple)
        self.assertEqual(len(result), 2)
```

## No Analog Found

No files without analogs. All 6 files are modifications to existing files with clear self-referential patterns.

## Metadata

**Analog search scope:** `workspace/runtime/agent_*.py`, `workspace/runtime/classifier.py`, `workspace/runtime/agent_protocol.py`, `tests/test_sprint9.py`
**Files scanned:** 8
**Pattern extraction date:** 2026-04-29
