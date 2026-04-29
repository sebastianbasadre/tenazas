# Phase 7: Arbitrator & Cross-Agent - Pattern Map

**Mapped:** 2026-04-29
**Files analyzed:** 7
**Analogs found:** 7 / 7

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `workspace/runtime/agent_arbitrator.py` | service | request-response | `workspace/runtime/agent_arbitrator.py` (self, refactor) | exact |
| `workspace/runtime/agent_infra.py` | service | request-response | `workspace/runtime/agent_infra.py` (self, add caching) | exact |
| `workspace/runtime/agent_personal.py` | service | request-response | `workspace/runtime/agent_infra.py` (handle + STATUS_FILE pattern) | exact |
| `workspace/runtime/agent_finance.py` | service | request-response | `workspace/runtime/agent_infra.py` (handle + STATUS_FILE pattern) | exact |
| `workspace/runtime/agent_research.py` | service | request-response | `workspace/runtime/agent_infra.py` (handle + STATUS_FILE pattern) | exact |
| `workspace/runtime/agent_self_improve.py` | service | request-response | `workspace/runtime/agent_infra.py` (handle + STATUS_FILE pattern) | exact |
| `tests/test_sprint9.py` | test | CRUD | `tests/test_sprint9.py` (self, extend) | exact |

## Pattern Assignments

### `workspace/runtime/agent_arbitrator.py` (service, request-response) -- REFACTOR

**Analog:** Self (refactor in-place). Also uses `workspace/runtime/exec_approvals.py` for escalation pattern.

**Imports pattern** (lines 1-6):
```python
import json
import time
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from runtime_utils import now
```

**New imports needed:**
```python
from runtime_utils import now, load_json, save_json
from agent_protocol import Observation, Recommendation
```

**Core resolve() pattern** (lines 82-146) -- current interface to refactor:
```python
def resolve(proposals: list) -> dict:
    """Resolve conflicts between agent proposals on the same resource.

    Args:
        proposals: list of {agent, action, resource, severity, context}

    Returns:
        {winner, loser, reason, action, escalated}
    """
    if len(proposals) < 2:
        if proposals:
            return {"winner": proposals[0]["agent"], "loser": None,
                    "reason": "no conflict", "action": proposals[0]["action"],
                    "escalated": False}
        return {"winner": None, "loser": None, "reason": "no proposals",
                "action": None, "escalated": False}

    # ... safety filter ...

    sorted_props = sorted(proposals,
                          key=lambda p: _get_effective_priority(p["agent"], p.get("context", {})))
    winner = sorted_props[0]
    loser = sorted_props[1]

    w_prio = _get_effective_priority(winner["agent"], winner.get("context", {}))
    l_prio = _get_effective_priority(loser["agent"], loser.get("context", {}))

    if w_prio == l_prio:
        return _handle_tie(winner, loser)

    result = {
        "winner": winner["agent"],
        "loser": loser["agent"],
        "reason": f"priority {winner['agent']}({w_prio}) > {loser['agent']}({l_prio})",
        "action": winner["action"],
        "escalated": False,
    }
    _log({"action": "resolve", **result})
    return result
```

**Tie-handling pattern to REPLACE** (lines 149-178):
```python
def _handle_tie(a: dict, b: dict) -> dict:
    """Handle equal-priority conflict. Postpone and track."""
    resource = a.get("resource", a.get("target", ""))
    cid = _conflict_id(a["agent"], b["agent"], resource)

    conflicts = _load(CONFLICTS_FILE, {"history": {}})
    history = conflicts["history"].get(cid, {"count": 0, "first": now().isoformat()})
    history["count"] += 1
    history["last"] = now().isoformat()
    conflicts["history"][cid] = history
    _save(CONFLICTS_FILE, conflicts)

    if history["count"] >= MAX_POSTPONE_COUNT:
        # ... escalate ...
        return {"winner": None, "loser": None, "reason": ..., "escalated": True, ...}

    # ... postpone ...
    return {"winner": None, "loser": None, "reason": ..., "postponed": True}
```

**Priority system to KEEP** (lines 27-37, 60-71):
```python
PRIORITY = {
    "safety": 0,
    "infra_critical": 1,
    "personal_active": 2,
    "finance_extreme": 3,
    "infra": 4,
    "personal": 5,
    "finance": 6,
    "research": 7,
    "self_improve": 8,
}

def _get_effective_priority(agent: str, context: dict = None) -> int:
    ctx = context or {}
    if agent == "infra" and ctx.get("severity") == "critical":
        return PRIORITY["infra_critical"]
    if agent == "personal" and ctx.get("calendar_active"):
        return PRIORITY["personal_active"]
    if agent == "finance" and ctx.get("variation_pct", 0) > 10:
        return PRIORITY["finance_extreme"]
    return PRIORITY.get(agent, 9)
```

**JSONL logging pattern** (lines 43-48):
```python
def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass
```

**Telegram escalation pattern from exec_approvals.py** (lines 138-209):
```python
def send_approval_request(
    action_summary: str,
    action_details: str,
    skill_name: str,
    chat_id: str = CHAT_ID,
    token=None,
) -> str:
    # ... saves to pending_approvals.json, builds inline keyboard, sends to Telegram ...
    keyboard = {
        "inline_keyboard": [
            [
                {"text": "Aprobar", "callback_data": f"approve:{approval_id}"},
                {"text": "Cancelar", "callback_data": f"cancel:{approval_id}"},
            ],
            [
                {"text": "Ver detalles", "callback_data": f"details:{approval_id}"},
            ],
        ]
    }
    # ...
    return approval_id
```

**get_approval_status pattern** (lines 311-322):
```python
def get_approval_status(approval_id: str) -> str:
    pending = _load_pending()
    entry = pending.get(approval_id)
    if entry is None:
        return "not_found"
    if time.time() - entry.get("created_at", 0) > APPROVAL_TIMEOUT_SECONDS:
        return "expired"
    return entry.get("status", "pending")
```

---

### `workspace/runtime/agent_infra.py` (service, request-response) -- ADD OBSERVATION CACHING

**Analog:** Self. Already has STATUS_FILE and save_json in handle().

**STATUS_FILE declaration** (line 17):
```python
STATUS_FILE = AGENT_DIR / "infra_status.json"
```

**Current handle() tail -- already saves to STATUS_FILE** (lines 243-253):
```python
    obs = Observation(
        agent_name="infra",
        domain="infrastructure",
        summary=data.get("summary", "Infra: sin datos"),
        confidence=data.get("confidence", 0.5),
        details=data,
    )
    recs = _build_recommendations(data)
    save_json(str(STATUS_FILE), data)  # <-- CHANGE: save obs.to_dict() under "observation" key
    _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"), "confidence": obs.confidence})
    return (obs, recs)
```

**Change needed:** Replace `save_json(str(STATUS_FILE), data)` with saving both `data` and `obs.to_dict()` under an `"observation"` key:
```python
    state = load_json(str(STATUS_FILE), {})
    state.update(data)
    state["observation"] = obs.to_dict()
    save_json(str(STATUS_FILE), state)
```

---

### `workspace/runtime/agent_personal.py` (service, request-response) -- ADD STATUS_FILE + OBSERVATION CACHING

**Analog:** `workspace/runtime/agent_infra.py` (STATUS_FILE + handle save pattern)

**Current file constants** (lines 12-16):
```python
BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "personal_memory.json"
LOG_FILE = BASE_DIR / "logs" / "agent_personal.log"
```

**Add (copy from agent_infra.py line 17):**
```python
STATUS_FILE = AGENT_DIR / "personal_status.json"
```

**Current handle() tail** (lines 149-158):
```python
    obs = Observation(
        agent_name="personal",
        domain="personal",
        summary=data.get("summary", "Personal: sin datos"),
        confidence=data.get("confidence", 0.7),
        details=data,
    )
    recs = []
    _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"), "confidence": obs.confidence})
    return (obs, recs)
```

**Add before return (copy pattern from agent_infra.py lines 251):**
```python
    # Save observation to per-agent state file for cross-agent reads
    state = load_json(str(STATUS_FILE), {})
    state.update(data)
    state["observation"] = obs.to_dict()
    save_json(str(STATUS_FILE), state)
```

---

### `workspace/runtime/agent_finance.py` (service, request-response) -- ADD STATUS_FILE + OBSERVATION CACHING

**Analog:** `workspace/runtime/agent_infra.py` (same pattern as personal)

**Current file constants** (lines 12-16):
```python
BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "finance_memory.json"
LOG_FILE = BASE_DIR / "logs" / "agent_finance.log"
```

**Add:**
```python
STATUS_FILE = AGENT_DIR / "finance_status.json"
```

**Current handle() tail** (lines 175-184):
```python
    obs = Observation(
        agent_name="finance",
        domain="finance",
        summary=data.get("summary", "Finance: sin datos"),
        confidence=data.get("confidence", 0.7),
        details=data,
    )
    recs = _build_finance_recs(data)
    _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"), "confidence": obs.confidence})
    return (obs, recs)
```

**Add before return (same pattern):**
```python
    state = load_json(str(STATUS_FILE), {})
    state.update(data)
    state["observation"] = obs.to_dict()
    save_json(str(STATUS_FILE), state)
```

---

### `workspace/runtime/agent_research.py` (service, request-response) -- ADD STATUS_FILE + OBSERVATION CACHING

**Analog:** `workspace/runtime/agent_infra.py` (same pattern)

**Current file constants** (lines 12-16):
```python
BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "research_memory.json"
LOG_FILE = BASE_DIR / "logs" / "agent_research.log"
```

**Add:**
```python
STATUS_FILE = AGENT_DIR / "research_status.json"
```

**Current handle() tail** (lines 106-127):
```python
    obs = Observation(
        agent_name="research",
        domain="research",
        summary=data.get("summary", "Research: sin datos"),
        confidence=data.get("confidence", 0.5),
        details=data,
    )
    recs = []
    for f in data.get("raw", {}).get("findings", data.get("raw", {}).get("results", []))[:3]:
        # ... build recommendations ...
    if recs:
        _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"), "recs": len(recs)})
    else:
        _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"), "confidence": obs.confidence})
    return (obs, recs)
```

**Add before return (same pattern):**
```python
    state = load_json(str(STATUS_FILE), {})
    state.update(data)
    state["observation"] = obs.to_dict()
    save_json(str(STATUS_FILE), state)
```

---

### `workspace/runtime/agent_self_improve.py` (service, request-response) -- ADD STATUS_FILE + OBSERVATION CACHING

**Analog:** `workspace/runtime/agent_infra.py` (same pattern)

**Current file constants** (lines 12-16):
```python
BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "self_improve_memory.json"
LOG_FILE = BASE_DIR / "logs" / "agent_self_improve.log"
```

**Add:**
```python
STATUS_FILE = AGENT_DIR / "self_improve_status.json"
```

**Current handle() tail** (lines 180-189):
```python
    obs = Observation(
        agent_name="self_improve",
        domain="self_improvement",
        summary=data.get("summary", "Self-improve: sin datos"),
        confidence=data.get("confidence", 0.5),
        details=data,
    )
    recs = _build_improve_recs(data)
    _log({"action": "handle", "query": query[:80], "sub": data.get("sub", "full"), "confidence": obs.confidence})
    return (obs, recs)
```

**Add before return (same pattern):**
```python
    state = load_json(str(STATUS_FILE), {})
    state.update(data)
    state["observation"] = obs.to_dict()
    save_json(str(STATUS_FILE), state)
```

---

### `tests/test_sprint9.py` (test, extend) -- ADD ARBITRATOR + CROSS-AGENT TESTS

**Analog:** Self (existing test_sprint9.py, 1496 lines). Copy test class structure from existing TestArbitrator class.

**Test file setup pattern** (lines 1-28):
```python
#!/usr/bin/env python3
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

RUNTIME = Path(__file__).parent.parent / "workspace" / "runtime"
_C = Path("/home/tenazas/runtime")
if _C.exists():
    RUNTIME = _C
sys.path.insert(0, str(RUNTIME.parent))
sys.path.insert(0, str(RUNTIME))
```

**Arbitrator test class pattern** (lines 105-204):
```python
class TestArbitrator(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import agent_arbitrator as aa
        self._orig = aa.CONFLICTS_FILE
        aa.CONFLICTS_FILE = Path(self.tmpdir) / "conflicts.json"
        self.aa = aa

    def tearDown(self):
        self.aa.CONFLICTS_FILE = self._orig
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_infra_critical_beats_finance(self):
        result = self.aa.resolve([
            {"agent": "infra", "action": "restart gluetun", "resource": "gluetun",
             "severity": "critical", "context": {"severity": "critical"}},
            {"agent": "finance", "action": "send alert", "resource": "telegram",
             "context": {}},
        ])
        self.assertEqual(result["winner"], "infra")
        self.assertEqual(result["loser"], "finance")
```

**Agent handle test pattern with mocking** (lines 967-1091):
```python
class TestAgentInfraHandle(unittest.TestCase):

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        import agent_infra as ai
        self._orig_sf = ai.STATUS_FILE
        self._orig_lf = ai.LOG_FILE
        ai.STATUS_FILE = Path(self.tmpdir) / "status.json"
        ai.LOG_FILE = Path(self.tmpdir) / "agent_infra.log"
        self.ai = ai

    def tearDown(self):
        self.ai.STATUS_FILE = self._orig_sf
        self.ai.LOG_FILE = self._orig_lf
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _mock_snapshot(self, containers=None, ollama=None, vpn=None, disk=None):
        snap = {}
        # ... build mock snapshot ...
        return snap

    def test_container_query_routes_to_containers(self):
        snap = self._mock_snapshot(containers={...})
        mock_snap = MagicMock(return_value=snap)
        with patch.dict("sys.modules", {"homelab_snapshot": MagicMock(snapshot=mock_snap), ...}):
            import importlib
            importlib.reload(self.ai)
            obs, recs = self.ai.handle("containers status")
        from agent_protocol import Observation
        self.assertIsInstance(obs, Observation)
```

**Safety invariant test pattern** (lines 286-351):
```python
class TestAgentSafety(unittest.TestCase):

    def test_infra_blocks_openclaw(self):
        import agent_infra as ai
        self.assertIn("openclaw", ai.BLOCKED_TARGETS)
```

---

## Shared Patterns

### JSONL Logging
**Source:** All agent files (`agent_infra.py` line 26-31, identical in all 5 agents)
**Apply to:** agent_arbitrator.py (already has it, keep as-is)
```python
def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass
```

### Observation Construction
**Source:** `agent_infra.py` lines 243-249 (identical pattern in all 5 agents)
**Apply to:** All agents (already present, no change needed)
```python
obs = Observation(
    agent_name="infra",
    domain="infrastructure",
    summary=data.get("summary", "Infra: sin datos"),
    confidence=data.get("confidence", 0.5),
    details=data,
)
```

### Observation Serialization
**Source:** `agent_protocol.py` lines 47-48
**Apply to:** All agents (for caching to state files)
```python
def to_dict(self) -> dict:
    return asdict(self)
```

### State File I/O
**Source:** `runtime_utils.py` (via `agent_infra.py` line 10)
**Apply to:** All 5 agents for observation caching
```python
from runtime_utils import now, load_json, save_json
```

### Error Isolation in try/except
**Source:** All agent handle() functions and test_sprint9.py error isolation tests (lines 553-639)
**Apply to:** Arbitrator escalation calls, proactive observation calls
```python
try:
    # risky operation
except Exception as e:
    _log({"action": "error", "error": str(e)[:100]})
    # degrade gracefully, never crash
```

### Test setUp/tearDown with tmpdir
**Source:** `tests/test_sprint9.py` lines 107-116 (TestArbitrator)
**Apply to:** New test classes for arbitrator confidence tiebreak, escalation, cross-agent cache
```python
def setUp(self):
    self.tmpdir = tempfile.mkdtemp()
    import agent_arbitrator as aa
    self._orig = aa.CONFLICTS_FILE
    aa.CONFLICTS_FILE = Path(self.tmpdir) / "conflicts.json"
    self.aa = aa

def tearDown(self):
    self.aa.CONFLICTS_FILE = self._orig
    shutil.rmtree(self.tmpdir, ignore_errors=True)
```

---

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| (none) | -- | -- | All files have exact analogs in the existing codebase |

**Note:** The proactive cron observation function (`_run_proactive_observations`) has no direct analog, but the pattern is trivially composed from: (1) agent handle("") calls already documented above, and (2) the worker_supervisor.py sequential worker execution pattern. No new infrastructure needed.

## Key Differences: agent_infra vs Other 4 Agents

| Aspect | agent_infra | personal/finance/research/self_improve |
|--------|-------------|----------------------------------------|
| STATUS_FILE constant | **Has it** (line 17) | **Missing** -- must add |
| save_json in handle() | **Has it** (line 251) | **Missing** -- must add |
| Observation to_dict() save | **Needs change** (saves `data`, not `obs.to_dict()`) | **Needs addition** (saves nothing currently) |

The modification pattern is identical across all 5 agents: declare STATUS_FILE, save observation dict to state file in handle() before return.

## Metadata

**Analog search scope:** `workspace/runtime/agent_*.py`, `workspace/runtime/exec_approvals.py`, `workspace/runtime/agent_protocol.py`, `tests/test_sprint9.py`
**Files scanned:** 10
**Pattern extraction date:** 2026-04-29
