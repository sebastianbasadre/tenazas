# Coding Conventions

**Analysis Date:** 2026-04-28

## Python Overview

All runtime code uses Python 3 (minimum 3.9+). The codebase is heavily concentrated in `workspace/runtime/` with 42+ modules handling orchestration, model routing, health monitoring, and autonomous improvements.

## Naming Patterns

**Files:**
- Lowercase with underscores: `model_router.py`, `orchestrator.py`, `docker_wrapper.py`
- Test files use prefix: `test_*.py` (e.g., `test_orchestrator.py`, `test_runtime.py`)
- Bash scripts use hyphens: `auto-heal.sh`, `ollama-check.sh`

**Functions:**
- Lowercase with underscores for public functions: `classify_query()`, `select_model()`, `detect_backends()`
- Private functions prefixed with underscore: `_now()`, `_log()`, `_ping_backend()`, `_check_action()`
- Inline helper functions use underscore prefix: `_mock_backends()` in test files

**Variables:**
- Lowercase with underscores: `routing_metrics`, `confidence_score`, `available_backends`
- Constants in UPPERCASE_WITH_UNDERSCORES: `ALLOWED_ACTIONS`, `BLOCKED_CONTAINERS`, `MODEL_CATALOG`, `GUARDRAIL_COOLDOWN`
- Internal module-level state prefixed with underscore: `_guardrail_state`, `_routing_metrics`, `_ollama_status`

**Types:**
- Classes use PascalCase: `TestDockerWrapper`, `TestQueryClassification` (unittest)
- Enums/Role constants: `VALID_ROLES`, `ROLE_MAP`, `CATEGORY_TO_ROLE`
- Dict keys use lowercase with underscores: `{"ok": bool, "error": str, "ts": datetime}`

## Code Style

**Formatting:**
- No explicit formatter is configured (no `.prettier`, `.flake8`, `pyproject.toml`)
- Implicit style: 4 spaces indentation (Python standard)
- Line length: appears to be ~100-120 characters based on existing code
- No trailing whitespace

**Linting:**
- No explicit linter configured (no `.eslintrc`, `.pylintrc`)
- Code follows PEP8-like conventions but not enforced
- Type hints absent — dynamic typing throughout
- Comments are inline and docstring-based (see below)

**Import Organization:**

Order (observed in modules like `orchestrator.py`, `model_router.py`):
1. Shebang: `#!/usr/bin/env python3`
2. Module docstring (multiline, describes purpose + core concepts)
3. Standard library imports: `json`, `re`, `subprocess`, `sys`, `time`, `tempfile`
4. Third-party imports: `unittest` (for tests only)
5. Relative imports from runtime: `from runtime import docker_wrapper`

**Example from `orchestrator.py` (lines 1-32):**
```python
#!/usr/bin/env python3
"""
Orchestrator — Central AI routing layer for Tenazas.

Classifies user queries and routes them to the optimal pipeline + model.

Categories (pipeline selection):
  chat               → direct LLM
  knowledge_query    → RAG pipeline
  ...
"""

import json
import os
import re
import subprocess
import time
from collections import defaultdict
from datetime import datetime
from pathlib import Path

# ── Config ───────────────────────────────────────────────────────

BASE_DIR = Path(__file__).parent
ORCHESTRATOR_LOG = BASE_DIR / "logs" / "orchestrator.log"
```

## Error Handling

**Patterns:**

1. **Try-except for I/O operations:**
   - File operations wrapped in try-except, fallback to default/empty
   - Example from `model_router.py` (lines 186-190):
     ```python
     try:
         with open(GUARDRAIL_STATE_FILE) as f:
             saved = json.load(f)
     except (FileNotFoundError, json.JSONDecodeError):
         return
     ```

2. **Subprocess error handling:**
   - Always use list args (NO `shell=True` — security critical)
   - Capture stdout/stderr, set timeouts
   - Example from `docker_wrapper.py` (lines 51-61):
     ```python
     try:
         result = subprocess.run(
             ["docker", "ps", "-a", "--format", "{{.Names}}\t{{.Status}}"],
             capture_output=True, text=True, timeout=15,
         )
         if result.returncode != 0:
             _log({"ts": _now(), "action": "ps", "ok": False, "error": result.stderr.strip()})
             return []
     except (subprocess.TimeoutExpired, FileNotFoundError) as e:
         _log({"ts": _now(), "action": "ps", "ok": False, "error": str(e)})
         return []
     ```

3. **Validation errors:**
   - Raise `ValueError` for invalid input
   - Example from `docker_wrapper.py` (lines 37-46):
     ```python
     def sanitize_name(name: str) -> str:
         name = name.strip()
         if not name:
             raise ValueError("Empty container name")
         if not CONTAINER_NAME_RE.match(name):
             raise ValueError(f"Invalid container name: {name!r}")
         if name.lower() in BLOCKED_CONTAINERS:
             raise ValueError(f"Container {name!r} is protected — action blocked")
         return name
     ```

4. **Default return values (graceful degradation):**
   - Empty dict/list on failure: `return {}`, `return []`
   - Boolean flags: `return False`
   - Example from `docker_wrapper.py` (lines 49-73):
     ```python
     def docker_ps() -> list:
         try:
             # ...
         except (...) as e:
             _log(...)
             return []  # Graceful fallback
     ```

5. **Policy: Never block on errors:**
   - All operations have fallback paths
   - Errors logged but don't propagate
   - System continues operating at degraded capability

## Logging

**Framework:** Stdlib `json` module (not `logging` library)

**Patterns:**

1. **JSON line format (JSONL):**
   - One dict per line, serialized with `json.dumps()`
   - Example from `docker_wrapper.py` (lines 30-34):
     ```python
     def _log(entry: dict):
         LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
         with open(LOG_FILE, "a") as f:
             f.write(json.dumps(entry) + "\n")
     ```

2. **Mandatory fields in log entries:**
   - `ts`: ISO8601 timestamp from `_now()` (always first, for sorting)
   - `action`: What happened (e.g., "docker_action", "classify_query", "route_by_role")
   - Context fields depend on action (e.g., `container`, `ok`, `error`)

3. **Example log entry (from `model_router.py`):**
   ```python
   _log({
       "ts": _now().isoformat(),
       "action": "select_model",
       "task": task,
       "result": "no_backend_available",
   })
   ```

4. **Log locations:**
   - `workspace/runtime/logs/` directory
   - Named by module: `orchestrator.log`, `docker_wrapper.log`, `model_router.log`, `safety.log`, `diagnostics.log`
   - Append-only (no rotation in code, handled externally via `log_rotation.py`)

5. **Structured data:**
   - Booleans as `true/false` (JSON compatible)
   - Timestamps as ISO8601 strings
   - Lists/dicts serialize fully (no truncation in runtime code)
   - Custom objects use `default=str` in `json.dump()` call

## Comments

**When to comment:**
- Explain *why*, not *what* (code shows what)
- Complex business logic or non-obvious heuristics
- Trade-offs and design decisions
- Warnings about side effects

**Not used:**
- Docstrings on simple functions (rare)
- Type hints (dynamic Python, not enforced)
- Inline comments for obvious code

**Style:**
- Line comments use `#` with single space: `# This explains logic`
- Section markers (e.g., above major sections): 
  ```python
  # ── Docker Wrapper ──────────────────────────────────────
  # ── Imports ──────────────────────────────────────────────
  # ── Constants ────────────────────────────────────────────
  ```
- Example from `orchestrator.py` (lines 28-35):
  ```python
  # ── Config ───────────────────────────────────────────────────────
  
  BASE_DIR = Path(__file__).parent
  ORCHESTRATOR_LOG = BASE_DIR / "logs" / "orchestrator.log"
  
  # Feature flag: use Router V2 for model selection
  # Set to False to revert to V1 behavior without code changes
  USE_ROUTER_V2 = True
  ```

## Module Design

**Exports:**
- No explicit `__all__` (all public functions available)
- Private helpers prefixed with `_` (naming convention only)
- No barrel files (each module imports directly)

**Module structure (pattern from `docker_wrapper.py`, `safety_guard.py`):**
1. Shebang + docstring
2. Imports
3. Constants/config
4. Helper functions (`_now()`, `_log()`)
5. Main functions (public API)
6. Class definitions (for tests)

**Singleton patterns:**
- Module-level dicts for metrics/state: `_guardrail_state`, `_routing_metrics`
- Lazy loading with guard variables: `_guardrail_loaded` in `model_router.py`
- JSON file persistence for state: `guardrail_state.json`, `gluetun_restart_state.json`

## Function Design

**Size:**
- Most functions 10-50 lines
- Complex multi-step operations wrapped in functions (e.g., `route_by_role()` is ~80 lines but broken into logical sections)
- Helper functions extracted for readability

**Parameters:**
- Few args (2-4 typical), use dicts for complex options
- Optional args have defaults: `flags: dict | None = None`
- Use `**kwargs` rarely (prefer explicit dicts)

**Return values:**
- Consistent types per function (don't mix list/dict/None for same function)
- Dicts with known keys for complex returns:
  ```python
  {
      "ok": bool,
      "error": str,
      "model": str,
      "backend": str,
      "url": str,
      "reason": str,
  }
  ```
- Tuples for fixed-length multi-return: `(backend, model, url)`
- Empty dict `{}` or list `[]` instead of `None` (graceful defaults)

## Subprocess & Security

**Critical convention:**
- ONLY use list arguments: `["docker", "restart", "sonarr"]`
- NEVER use `shell=True`
- Example from `docker_wrapper.py` (lines 94-98):
  ```python
  result = subprocess.run(
      ["docker", action, container],
      capture_output=True, text=True, timeout=30,
  )
  ```

**Validation:**
- Container names validated via regex before subprocess call
- All Docker actions whitelisted in `docker_wrapper.py`
- All actions logged to `logs/docker_actions.log`

## Code Organization by Domain

**Infrastructure:** `docker_wrapper.py`, `safety_guard.py`, `runtime.py` — Docker, event detection, task queueing

**Model Selection:** `model_router.py` (V1 + V2), `orchestrator.py` — routing, classification, model selection

**Quality:** `confidence_engine.py`, `action_mapper.py`, `infra_diagnostics.py` — decision quality, action mapping, diagnostics

**Learning:** `idle_learning.py`, `daily_improvement.py`, `self_debugger.py`, `self_dev_engine.py` — continuous improvement, analysis

**Personal:** `finance.py`, `expenses.py`, `jobs.py`, `smoking.py` — personal domain trackers

**Autonomous Agents:** `agent_*.py`, `multi_agent_engine.py` — specialized agents for domains

---

*Convention analysis: 2026-04-28*
