# Phase 5: Foundation & Contracts - Pattern Map

**Mapped:** 2026-04-28
**Files analyzed:** 9 (2 new, 6 modified, 1 extended)
**Analogs found:** 9 / 9

## File Classification

| New/Modified File | Role | Data Flow | Closest Analog | Match Quality |
|-------------------|------|-----------|----------------|---------------|
| `workspace/runtime/agent_protocol.py` | model | transform | `workspace/runtime/intent_normalizer.py` | exact |
| `workspace/runtime/agent_registry.py` | service | request-response | `workspace/runtime/agent_coordinator.py` | exact |
| `workspace/runtime/agent_infra.py` | service | request-response | (self — refactor in place) | exact |
| `workspace/runtime/agent_personal.py` | service | request-response | `workspace/runtime/agent_infra.py` | exact |
| `workspace/runtime/agent_finance.py` | service | request-response | `workspace/runtime/agent_infra.py` | exact |
| `workspace/runtime/agent_research.py` | service | request-response | `workspace/runtime/agent_infra.py` | exact |
| `workspace/runtime/agent_self_improve.py` | service | request-response | `workspace/runtime/agent_infra.py` | exact |
| `workspace/runtime/agent_coordinator.py` | service | request-response | (self — delegates to registry) | exact |
| `tests/test_sprint9.py` | test | N/A | (self — add new test classes) | exact |

## Pattern Assignments

### `workspace/runtime/agent_protocol.py` (model, transform) -- NEW

**Analog:** `workspace/runtime/intent_normalizer.py`

**Imports pattern** (lines 1-13):
```python
"""
intent_normalizer.py
Convierte el output del finetuned_router en un NormalizedCall validado.
...
"""

from dataclasses import dataclass, field
from typing import Optional
```

**Dataclass with to_dict() pattern** (lines 19-41):
```python
@dataclass
class NormalizedCall:
    intent:        str
    skill:         str           # nombre exacto del skill: "skill:smart-home", etc.
    action:        str           # acción dentro del skill
    args:          dict
    confidence:    str           # "high" | "medium" | "low"
    source:        str           # origen del parseo
    missing_slots: list = field(default_factory=list)
    valid:         bool = True
    raw_message:   str = ""

    def to_dict(self) -> dict:
        return {
            "intent":        self.intent,
            "skill":         self.skill,
            "action":        self.action,
            "args":          self.args,
            "confidence":    self.confidence,
            "source":        self.source,
            "missing_slots": self.missing_slots,
            "valid":         self.valid,
        }
```

**Secondary analog: `workspace/runtime/action_validator.py` -- __post_init__ with timestamp** (lines 43-54):
```python
@dataclass
class ValidationResult:
    action_name: str
    target: str
    success: bool
    reason: str
    evidence: dict
    validated_at: str = ""

    def __post_init__(self):
        if not self.validated_at:
            self.validated_at = datetime.now().astimezone().isoformat()
```

**Key notes for agent_protocol.py:**
- Import ONLY stdlib (`dataclasses`, `datetime`). Zero runtime imports to avoid circular deps.
- Use `asdict()` for `to_dict()` (simpler than manual dict building in NormalizedCall).
- Add `__post_init__` for timestamp auto-fill (ValidationResult pattern) and confidence clamping.
- Module-level constants `VALID_ROLES` and `VALID_RISK_LEVELS` for validation.

---

### `workspace/runtime/agent_registry.py` (service, request-response) -- NEW

**Analog:** `workspace/runtime/agent_coordinator.py`

**Imports + path setup pattern** (lines 1-19):
```python
#!/usr/bin/env python3
"""Agent Coordinator — Routes queries to appropriate agent(s).
...
"""

import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
COORD_LOG = STATE_DIR / "agents" / "coordinator_log.json"
LOG_FILE = BASE_DIR / "logs" / "agent_coordinator.log"
```

**_log() helper pattern** (lines 38-43):
```python
def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass
```

**Lazy import pattern** (lines 61-67):
```python
def _load_agent(name: str):
    """Lazily import an agent module."""
    try:
        import importlib
        return importlib.import_module(AGENT_MODULES[name])
    except Exception:
        return None
```

**Duck-type validation pattern** (lines 88-91 -- hasattr checks):
```python
for name, module_name in AGENT_MODULES.items():
    agent = _load_agent(name)
    if agent and hasattr(agent, "owns") and agent.owns(q):
        matching.append(name)
```

**Error isolation in dispatch** (lines 116-124):
```python
for name in agents:
    agent = _load_agent(name)
    if agent and hasattr(agent, "handle_query"):
        try:
            resp = agent.handle_query(query)
            if resp:
                responses[name] = resp
        except Exception as e:
            responses[name] = f"[{name} error: {str(e)[:50]}]"
```

**AGENT_MODULES dict** (lines 23-29):
```python
AGENT_MODULES = {
    "infra": "agent_infra",
    "personal": "agent_personal",
    "finance": "agent_finance",
    "research": "agent_research",
    "self_improve": "agent_self_improve",
}
```

**Secondary analog for locking: `workspace/runtime/proposal_executor.py` Lock class** (lines 180-242):
```python
class Lock:
    """File lock. Uses flock; falls back to content check for stale detection."""

    def __init__(self, path: str):
        self.path = path
        self.fh = None
        self.acquired = False

    def acquire(self) -> None:
        self.fh = open(self.path, "a+")
        try:
            fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except (OSError, BlockingIOError):
            # stale detection by mtime ...
            ...

    def release(self) -> None:
        if not self.fh: return
        try: fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        except OSError: pass
        try: self.fh.close()
        except OSError: pass
        ...

    def __enter__(self) -> "Lock":
        self.acquire()
        return self

    def __exit__(self, *args) -> None:
        self.release()
```

**Secondary analog for atomic writes: `workspace/runtime/runtime_utils.py` write_atomic()** (lines 53-72):
```python
def write_atomic(path, data):
    """Write JSON atomically: temp file + os.replace(). Crash-safe."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_fd = None
    try:
        tmp_fd = tempfile.NamedTemporaryFile(
            mode="w", dir=path.parent, suffix=".tmp", delete=False
        )
        json.dump(data, tmp_fd, indent=2, ensure_ascii=False, default=str)
        tmp_fd.close()
        os.replace(tmp_fd.name, path)
    except BaseException:
        if tmp_fd is not None:
            tmp_fd.close()
            try: os.unlink(tmp_fd.name)
            except OSError: pass
        raise
```

---

### `workspace/runtime/agent_infra.py` (service, request-response) -- MODIFY

**Analog:** Self (refactor in place). Pattern from own file.

**Current module structure** (lines 1-22):
```python
#!/usr/bin/env python3
"""Agent Infra — Specialized in homelab infrastructure.

Ownership: containers, GPU, RAM, disk, network, VPN.
Reads snapshot, uses predictor + root_cause_engine, proposes via action_registry.
"""

import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
MEMORY_FILE = AGENT_DIR / "infra_memory.json"
STATUS_FILE = AGENT_DIR / "infra_status.json"
LOG_FILE = BASE_DIR / "logs" / "agent_infra.log"

OWNERSHIP = {"container", "containers", "gpu", "ram", "disk", "network", "vpn",
             "ollama", "docker", "gluetun", "infra", "homelab", "backend_offline"}
BLOCKED_TARGETS = {"openclaw", "watchtower"}
```

**owns() pattern** (line 42-43):
```python
def owns(topic: str) -> bool:
    return any(k in topic.lower() for k in OWNERSHIP)
```

**assess() with proposals** (lines 46-80):
```python
def assess() -> dict:
    """Assess current infra state. Returns status + proposals."""
    status = {"agent": "infra", "ts": now().isoformat(), "health": "ok", "proposals": [], "issues": []}
    try:
        from homelab_snapshot import snapshot
        snap = snapshot()
        # ... check containers, ollama ...
    except Exception as e:
        status["issues"].append(f"snapshot error: {str(e)[:100]}")
    # ... infra_predictor ...
    status["proposals"] = [p for p in status["proposals"]
                           if not any(t in p.get("action", "").lower() for t in BLOCKED_TARGETS)]
    _save(STATUS_FILE, status)
    _log({"action": "assess", "health": status["health"], "proposals": len(status["proposals"])})
    return status
```

**handle_query() -- will become backward-compat wrapper** (lines 83-93):
```python
def handle_query(query: str) -> str:
    """Handle an infra-related query."""
    status = assess()
    if status["health"] == "ok" and not status["issues"]:
        return "Infra OK — todo verde."
    parts = [f"Infra: {status['health']}"]
    for issue in status["issues"][:3]:
        parts.append(f"  ! {issue}")
    for prop in status["proposals"][:2]:
        parts.append(f"  → {prop['action']}")
    return "\n".join(parts)
```

**Migration changes needed:**
1. Add `from agent_protocol import Observation, Recommendation` (at top)
2. Add `PREFERRED_ROLE = "fast"` (module-level constant after OWNERSHIP)
3. Replace private `_load()/_save()` with `from runtime_utils import load_json, save_json`
4. Add `handle(query)` that wraps `assess()` and returns `(Observation, list[Recommendation])`
5. Keep `handle_query()` as backward-compat wrapper calling `handle()`

---

### `workspace/runtime/agent_personal.py` (service, request-response) -- MODIFY

**Analog:** `workspace/runtime/agent_infra.py` (identical structure)

**Identical boilerplate** (lines 1-38):
```python
#!/usr/bin/env python3
"""Agent Personal — Specialized in Sebas's daily life.
Ownership: calendar, email, reminders, smoking, briefing.
"""
import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now

BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
# ... OWNERSHIP, _log, _load, _save, owns, assess, handle_query ...
```

**Migration: same as agent_infra.py** -- add PREFERRED_ROLE, handle(), keep handle_query().

---

### `workspace/runtime/agent_finance.py` (service, request-response) -- MODIFY

**Analog:** `workspace/runtime/agent_infra.py` (identical structure)

**Identical boilerplate** (lines 1-39):
```python
#!/usr/bin/env python3
"""Agent Finance — Specialized in financial monitoring.
Ownership: Binance, IOL, market alerts.
"""
import json
from datetime import datetime
from pathlib import Path
from runtime_utils import now
# ... BASE_DIR, STATE_DIR, AGENT_DIR, OWNERSHIP, _log, _load, _save, owns, assess, handle_query ...
```

**Migration: same as agent_infra.py.**

---

### `workspace/runtime/agent_research.py` and `workspace/runtime/agent_self_improve.py` -- MODIFY

**Analog:** `workspace/runtime/agent_infra.py` (identical structure across all 5 agents)

All 5 agents share identical `_log()`, `_load()`, `_save()`, `owns()`, `assess()`, `handle_query()` structure. Migration is mechanical: same changes applied to each.

---

### `workspace/runtime/agent_coordinator.py` (service, request-response) -- MODIFY

**Analog:** Self (thin delegation to new registry).

**Lines to modify:**
- `identify_agents()` (lines 83-99): Replace manual loop with `agent_registry.dispatch()` or `agent_registry.list_matching(query)`
- `route()` (lines 102-146): Replace per-agent `handle_query()` calls with `agent_registry.dispatch(query)`
- `list_agents()` (lines 158-171): Delegate to `agent_registry.list_agents()`
- Keep `is_multi_domain()`, `_aggregate()`, `resolve_conflict()` as-is.

---

### `tests/test_sprint9.py` (test) -- MODIFY

**Analog:** Self (existing test_sprint9.py structure).

**Test setup pattern** (lines 1-28):
```python
#!/usr/bin/env python3
"""test_sprint9.py — Real Multi-Agent System tests."""
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

**Test class pattern** (lines 34-55):
```python
class TestAgentOwnership(unittest.TestCase):

    def test_infra_owns_containers(self):
        import agent_infra as ai
        self.assertTrue(ai.owns("containers down"))

    def test_infra_does_not_own_email(self):
        import agent_infra as ai
        self.assertFalse(ai.owns("check my email"))
```

**New test classes to add:** TestAgentProtocol, TestAgentRegistry, TestErrorIsolation, TestPreferredRole, TestLockedWrites. Follow the same `unittest.TestCase` pattern with inline module imports.

---

## Shared Patterns

### _log() Helper
**Source:** `workspace/runtime/agent_infra.py` lines 25-30
**Apply to:** `agent_registry.py` (new), all agent_*.py files already have it
```python
def _log(e):
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    e["ts"] = now().isoformat()
    try:
        with open(LOG_FILE, "a") as f: f.write(json.dumps(e, default=str) + "\n")
    except OSError: pass
```

### Path Setup
**Source:** `workspace/runtime/agent_infra.py` lines 12-18
**Apply to:** `agent_registry.py` (new), all agent_*.py files already have it
```python
BASE_DIR = Path(__file__).parent
STATE_DIR = Path("/home/tenazas/.openclaw/state") if Path("/home/tenazas").exists() else BASE_DIR.parent / "state"
AGENT_DIR = STATE_DIR / "agents"
LOG_FILE = BASE_DIR / "logs" / "agent_<name>.log"
```

### Private _load/_save Replacement
**Source:** `workspace/runtime/runtime_utils.py` lines 20-34
**Apply to:** All 5 agent_*.py files (replace private `_load()`/`_save()` with shared utils)
```python
from runtime_utils import load_json, save_json
# Replace: _load(path, default) -> load_json(path, default)
# Replace: _save(path, data) -> save_json(path, data)
```

### Module-Level Constants
**Source:** `workspace/runtime/agent_infra.py` lines 20-22
**Apply to:** All agent_*.py files -- add PREFERRED_ROLE after OWNERSHIP
```python
OWNERSHIP = {"container", "containers", ...}
BLOCKED_TARGETS = {"openclaw", "watchtower"}
PREFERRED_ROLE = "fast"   # NEW -- feeds into model_router.route_by_role()
```

### Backward-Compatible handle_query() Wrapper
**Apply to:** All 5 agent_*.py files
```python
# NEW: typed entry point
def handle(query: str):
    """Returns (Observation, list[Recommendation])."""
    status = assess()
    obs = Observation(agent_name="infra", domain="infrastructure", summary=..., ...)
    recs = [Recommendation(...) for p in status.get("proposals", [])]
    return (obs, recs)

# KEEP: backward compat
def handle_query(query: str) -> str:
    obs, recs = handle(query)
    return obs.summary
```

## No Analog Found

| File | Role | Data Flow | Reason |
|------|------|-----------|--------|
| (none) | -- | -- | All files have exact analogs in the existing codebase |

## Metadata

**Analog search scope:** `workspace/runtime/`, `tests/`
**Files scanned:** 9 analogs read
**Pattern extraction date:** 2026-04-28
