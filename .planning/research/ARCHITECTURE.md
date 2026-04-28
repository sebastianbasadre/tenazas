# Architecture Patterns

**Domain:** Python runtime refactoring (42+ modules, 2373-line god object)
**Researched:** 2026-04-28
**Confidence:** HIGH (based on codebase analysis + standard Python packaging patterns)

## Recommended Architecture

### Target: Domain-Organized Packages with Facade Preservation

Split the flat `workspace/runtime/` directory into domain-scoped subpackages while preserving existing import paths via `__init__.py` re-exports. The orchestrator becomes a thin facade delegating to focused submodules.

### Layer Diagram

```
Layer 0: Foundation (no internal deps)
  config.py, state_io.py, logging_config.py

Layer 1: Infrastructure (depends on Layer 0 only)
  docker_wrapper.py, safety_guard.py, executors.py

Layer 2: Models (depends on Layer 0 only)
  models/router.py, models/guardrails.py, models/catalog.py, models/openrouter.py

Layer 3: Routing & Orchestration (depends on Layers 0-2)
  routing/classifier.py, routing/router.py, routing/skill_executor.py
  routing/skill_parsers.py, routing/skill_handlers.py, routing/multitool.py

Layer 4: Skills & Domains (depends on Layers 0-2, NOT on Layer 3)
  devices/*, personal/ (finance, jobs, expenses, smoking)

Layer 5: Autonomous & Learning (depends on Layers 0-2)
  learning/ (idle_learning, dreaming, error_mining, improvement_candidates)
  autofix/ (agent, tiers, sandbox, aider, ssh, telegram)

Layer 6: Observability (depends on Layers 0-1)
  observability/ (metrics_server, health_sweep, roi_tracker)

Layer 7: Entry Points (depends on all layers)
  runtime.py, telegram_router.py, worker_supervisor.py, startup.sh
```

**Critical rule:** Dependencies flow downward only. Layer 3 (routing) never imports from Layer 5 (learning). Layer 4 (skills) never imports from Layer 3 (routing). This fixes the current circular dependency where orchestrator.py lazily imports from multi_agent_engine.

### Target Directory Structure

```
workspace/runtime/
  __init__.py
  config.py                    # NEW -- centralized IPs, ports, paths, env overrides
  state_io.py                  # NEW -- atomic JSON read/write with file locking
  logging_config.py            # NEW -- unified JSONL logger factory

  routing/                     # SPLIT FROM orchestrator.py (2373 lines -> 6 files)
    __init__.py                # facade: re-exports classify_query, route_and_execute, etc.
    classifier.py              # classify_query() + CLASSIFICATION_RULES
    router.py                  # route_query() + escalate() + select_model()
    skill_executor.py          # execute_skill() + SKILL_MAP + dispatch
    skill_parsers.py           # _parse_weather, _parse_calendar, _parse_infra, etc.
    skill_handlers.py          # _handle_smart_home, _handle_email, _handle_tts, etc.
    multitool.py               # route_and_execute_multitool, _call_multitool_model
    metrics.py                 # _routing_metrics, get_routing_metrics, report_inference

  models/                      # EXTRACTED FROM model_router.py (856 lines -> 4 files)
    __init__.py                # re-exports route_by_role, classify_task, select_model
    router.py                  # route_by_role, classify_task, detect_backends
    guardrails.py              # apply_guardrails, record_heavy_usage, state persistence
    catalog.py                 # MODEL_CATALOG, ROLE_MAP, get_model_info, list_models
    openrouter.py              # call_openrouter, _is_openrouter_available

  infra/                       # Event loop + infrastructure monitoring
    __init__.py
    runtime_loop.py            # main(), update_state, detect_events (was runtime.py)
    executors.py               # existing executors.py
    docker_wrapper.py          # existing (whitelist-enforced Docker subprocess)
    safety_guard.py            # existing (central security validator)
    diagnostics.py             # infra_diagnostics.py
    patterns.py                # pattern detection extracted from runtime.py

  devices/                     # EXISTING -- already well-structured, no change needed
    __init__.py, hue_lights.py, samsung_tv.py, marantz_avr.py, etc.

  personal/                    # GROUP existing standalone modules
    __init__.py
    finance.py, expenses.py, jobs.py, smoking.py

  learning/                    # GROUP existing learning modules
    __init__.py
    idle_learning.py, dreaming.py, error_mining.py, improvement_candidates.py
    daily_improvement.py, daily_brief.py, apply_improvements.py
    dataset_builder.py, eval_runner.py, fine_tune_pipeline.py
    # data/ subdirectory for proposals.json, learning_report.md, etc.

  autofix/                     # GROUP existing autofix modules
    __init__.py
    agent.py, tiers.py, sandbox.py, aider.py, ssh.py, telegram.py, llm_check.py

  observability/               # GROUP existing observability modules
    __init__.py
    metrics_server.py, health_sweep.py, roi_tracker.py, roi_report.py

  # Backward-compat shims (Phase 2g -- can remove once all imports updated)
  orchestrator.py              # from routing import *
  model_router.py              # from models import *
```

### Component Boundaries

| Component | Responsibility | Communicates With |
|-----------|---------------|-------------------|
| `config.py` | Single source of truth for IPs, ports, paths, feature flags | Every module reads from it; nothing writes to it |
| `state_io.py` | Atomic JSON read/write, file locking, corruption detection | Every module that persists state |
| `logging_config.py` | JSONL logger factory with rotation | Every module that logs |
| `routing/classifier` | Map user text to category (skill:weather, chat, etc.) | telegram_router, routing/router |
| `routing/router` | Select pipeline + model for a classified query | classifier, models/router |
| `routing/skill_executor` | Execute deterministic skills | parsers, handlers, external subprocesses |
| `routing/skill_handlers` | Parameterized skill logic (smart-home, email send, TTS) | devices/, external APIs |
| `routing/skill_parsers` | Parse raw skill output into human-readable text | Pure functions, no deps |
| `models/router` | Select optimal LLM backend by role + hardware constraints | Ollama endpoints, OpenRouter API |
| `models/guardrails` | Rate-limit heavy model usage, cooldown enforcement | models/router (pre-inference gate) |
| `infra/runtime_loop` | 5-min event detection and task dispatch | docker_wrapper, executors, state_io |
| `devices/` | IoT device control (9 types) | External device APIs (REST, WebSocket) |
| `personal/` | Finance, jobs, smoking tracking | state_io for persistence |
| `learning/` | Analyze logs, generate proposals, memory consolidation | models/router for LLM calls, state_io |
| `autofix/` | 3-tier self-repair with approval gates | Telegram API, models/router, docker_wrapper |
| `observability/` | Prometheus metrics, health probes, ROI tracking | state_io, external endpoints |

### Data Flow

```
User Message (Telegram)
  |
  v
telegram_router.py --> routing.classifier.classify_query()
  |                         |
  |  skill match?           | no match
  |  YES                    |
  v                         v
routing.skill_executor  --> routing.router.route_query()
  |                              |
  v                              v
skill_handlers /            models.router.route_by_role()
  skill_parsers /                |
  devices/                       v
  subprocess calls          Ollama / OpenRouter / Gemini
  |                              |
  v                              v
Format + send response     <-----+

Cron (5 min) --> infra/runtime_loop.py
  --> executors.py (docker ps, nmap, ping)
  --> detect events (compare prev vs curr state)
  --> enqueue tasks (state_io.locked_update)
  --> execute tasks (docker_wrapper for restarts)
  --> append memory (state_io.write_json)
  --> pattern detection (infra/patterns.py)
  --> notify Telegram (if actions taken)
```

## How to Split orchestrator.py (2373 lines)

The orchestrator currently contains 6 distinct responsibilities crammed into one file. Split by responsibility, not arbitrary line count.

### Responsibility Map (Current Lines -> Target File)

| Lines (approx) | Responsibility | Target File | Est. Lines |
|-----------------|---------------|-------------|------------|
| 1-50 | Imports, config, metrics globals | `routing/metrics.py` | ~50 |
| 52-104 | V1 MODELS dict, CATEGORY_TO_ROLE | `models/catalog.py` | ~60 |
| 105-142 | PIPELINES definition | `routing/router.py` | ~40 |
| 144-465 | CLASSIFICATION_RULES + IOL_KEYWORDS + all skill patterns | `routing/classifier.py` | ~320 |
| 466-479 | `_now()`, `_log()` helpers | `logging_config.py` | ~20 |
| 480-572 | `classify_query()` | `routing/classifier.py` | ~90 |
| 573-757 | `_select_model_v2()`, `select_model()` | `routing/router.py` | ~185 |
| 758-875 | `get_pipeline()`, `route_query()` | `routing/router.py` | ~120 |
| 876-1058 | `escalate()`, SKILL_MAP definition | `routing/router.py` + `routing/skill_executor.py` | ~180 |
| 1060-1165 | `execute_skill()` | `routing/skill_executor.py` | ~105 |
| 1167-1320 | `_parse_calendar()`, `_parse_weather()`, `_parse_infra()`, `_parse_proposals()` | `routing/skill_parsers.py` | ~155 |
| 1322-2013 | All `_handle_*` functions (smart_home, tts, calendar, morning_greeting, email, reminders, model_trainer, expense, smoking, network_scanner) | `routing/skill_handlers.py` | ~690 |
| 2018-2096 | `route_and_execute()` | `routing/__init__.py` (facade) | ~80 |
| 2098-2260 | Multitool execution | `routing/multitool.py` | ~165 |
| 2263-2373 | `_get_parameters()`, `_extract_signals()`, `report_inference()`, metrics helpers, `_build_flags_from_context()` | `routing/metrics.py` | ~110 |

### Facade Pattern (Preserve Import Compatibility)

```python
# routing/__init__.py -- re-exports everything external code imports
from .classifier import classify_query, CLASSIFICATION_RULES
from .router import route_query, escalate, select_model, get_pipeline
from .skill_executor import execute_skill, SKILL_MAP
from .metrics import get_routing_metrics, reset_routing_metrics, report_inference
from .multitool import route_and_execute_multitool

def route_and_execute(query, context=None, available_backends=None):
    """Thin facade: classify -> execute if skill, else route."""
    classification = classify_query(query, context)
    if classification.startswith("skill:"):
        skill_result = execute_skill(classification, query)
        route = route_query(query, context, available_backends)
        route["classification"] = classification
        route["executed"] = True
        route["skill_result"] = skill_result
        return route
    route = route_query(query, context, available_backends)
    route["executed"] = False
    route["skill_result"] = None
    return route
```

Then `orchestrator.py` becomes a backward-compat shim:

```python
# orchestrator.py -- backward compat, remove after all imports updated
from routing import *  # noqa: F401,F403
```

This means `from orchestrator import classify_query` continues to work without changes to tests or callers. The 14 import sites found in codebase grep all continue working.

## Config Centralization Pattern

### Problem: 30+ Files with Hardcoded IPs

Current state from codebase grep:
- `http://192.168.2.6:11434` (HomeLab Ollama) appears in 15+ files, each with its own env var override
- `192.168.2.2` (NAS) appears in 10+ files
- `192.168.2.11` (Hue Bridge) in 5+ files
- Each module independently does `OLLAMA_URL = os.environ.get("OLLAMA_GPU_URL", "http://192.168.2.6:11434")`

### Solution: Single `config.py` Module

```python
# config.py -- Single source of truth for all infrastructure endpoints
import os
from pathlib import Path

# Base paths
BASE_DIR = Path(__file__).parent
LOGS_DIR = BASE_DIR / "logs"
STATE_DIR = BASE_DIR / "state"

# Infrastructure endpoints (env var override -> sensible default)
HOMELAB_IP = os.environ.get("HOMELAB_IP", "192.168.2.6")
NAS_IP = os.environ.get("NAS_IP", "192.168.2.2")

OLLAMA_GPU_URL = os.environ.get("OLLAMA_GPU_URL", f"http://{HOMELAB_IP}:11434")
OLLAMA_NAS_URL = os.environ.get("OLLAMA_NAS_URL", "http://127.0.0.1:11434")
CHROMADB_URL = os.environ.get("CHROMADB_URL", f"http://{NAS_IP}:8100")
GLUETUN_PROXY = os.environ.get("GLUETUN_PROXY", f"http://{NAS_IP}:8888")
HUE_BRIDGE_IP = os.environ.get("HUE_BRIDGE_IP", "192.168.2.11")
DECO_IP = os.environ.get("DECO_IP", "192.168.2.30")

PROMETHEUS_PORT = int(os.environ.get("PROMETHEUS_PORT", "9105"))
NETWORK_EXPORTER_PORT = int(os.environ.get("NETWORK_EXPORTER_PORT", "9108"))

# Telegram
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "1565794551")

# Timeouts
SKILL_TIMEOUT = int(os.environ.get("SKILL_TIMEOUT", "30"))
RUNTIME_TIMEOUT = int(os.environ.get("RUNTIME_TIMEOUT", "120"))

# Feature flags
USE_ROUTER_V2 = os.environ.get("USE_ROUTER_V2", "true").lower() == "true"
USE_FINETUNED_ROUTER = os.environ.get("USE_FINETUNED_ROUTER", "false").lower() == "true"

# LAN
LAN_SUBNET = os.environ.get("LAN_SUBNET", "192.168.2.0/24")

# HomeLab SSH
HOMELAB_SSH_USER = os.environ.get("HOMELAB_SSH_USER", "sbasadre")
HOMELAB_SSH_TARGET = f"{HOMELAB_SSH_USER}@{HOMELAB_IP}"
```

**Why not `openclaw.json`?** The JSON config file contains secrets (bot tokens, API keys) and is mounted from `state/`. Infrastructure endpoints are not secrets and should be in committed code with env var overrides. `config.py` is testable and type-checkable. Secrets stay in `openclaw.json`.

**Migration:** Replace one module at a time. Each changes from:
```python
OLLAMA_URL = os.environ.get("OLLAMA_GPU_URL", "http://192.168.2.6:11434")
```
to:
```python
from config import OLLAMA_GPU_URL
```

## Shared State Management

### Problem: Naive JSON I/O Everywhere

Pattern repeated in 40+ modules with zero atomicity:
```python
with open("state.json", "w") as f:
    json.dump(data, f)
```

### Solution: `state_io.py` Module

```python
# state_io.py -- Atomic JSON persistence with file locking
import json
import os
import tempfile
from pathlib import Path

def read_json(path, default=None):
    """Read JSON file. Returns default on missing/corrupt. Never raises."""
    try:
        raw = Path(path).read_text()
        return json.loads(raw)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default if default is not None else {}

def write_json(path, data, *, indent=None):
    """Atomic write: tempfile in same dir -> fsync -> os.replace.

    Safe against: crash mid-write, concurrent reads, disk full.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, default=str, indent=indent)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, str(path))  # atomic on POSIX
    except BaseException:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

def locked_update(path, updater_fn, default=None):
    """Read-modify-write with advisory file lock.

    Usage:
        def add_entry(data):
            data.setdefault("entries", []).append(new_entry)
            return data
        locked_update("proposals.json", add_entry, default=[])
    """
    import fcntl  # Linux-only (container runs bookworm)
    path = Path(path)
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.touch(exist_ok=True)
    with open(lock_path, "r") as lock_fd:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        try:
            data = read_json(path, default=default if default is not None else {})
            data = updater_fn(data)
            write_json(path, data)
        finally:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
```

**Platform note:** The container runs Linux (node:24-bookworm), so `fcntl.flock` works. Dev machine is Windows -- tests should mock the locking layer or run in Docker.

### State File Migration Priority

| File | Concurrent Writers | Bug Fixed | Priority |
|------|--------------------|-----------|----------|
| `learning/proposals.json` | cron + telegram + idle_learning | Race condition (CONCERNS.md) | HIGH |
| `state.json` | runtime.py cron + telegram_router | Crash corruption (CONCERNS.md) | HIGH |
| `pending_approvals.json` | telegram callbacks + cron | Duplicate execution (CONCERNS.md) | HIGH |
| `guardrail_state.json` | model_router + orchestrator | No known bug but concurrent | MEDIUM |
| `finance/transactions.json` | expenses.py + finance.py | User-triggered, low frequency | MEDIUM |
| `memory_log.json` | runtime.py (single writer) | Already capped at 1000 | LOW |

## Patterns to Follow

### Pattern 1: Package with Facade (Import Path Preservation)

**What:** Convert a module into a package by creating `dir/__init__.py` that re-exports the public API.
**When:** Splitting a large module where external code imports specific functions.
**Why:** Zero-breakage migration. 14 import sites for orchestrator keep working. Tests pass unchanged.

### Pattern 2: Constructor Injection for Testability

**What:** Pass dependencies (config values, logger, state_io functions) as parameters with defaults.
**When:** Any module that needs isolated testing without monkeypatching globals.
**Example:**
```python
def execute_skill(category, query, *, timeout=None, logger=None):
    timeout = timeout or config.SKILL_TIMEOUT
    logger = logger or get_logger("skill_executor")
```
**Why:** Tests inject mock dependencies. No framework needed. Python's default arguments are sufficient.

### Pattern 3: Layered Dependency Direction (Downward Only)

**What:** Higher layers depend on lower layers, never the reverse.
**Current violation:** `orchestrator.py` line 2061 lazily imports `from multi_agent_engine import reason`. Learning module called from routing layer.
**Fix:** `telegram_router.py` (Layer 7 entry point) decides whether to invoke multi-agent reasoning, not the routing layer.

### Pattern 4: Atomic State Access

**What:** Every state file read/write goes through `state_io.py`.
**When:** Any module that touches a `.json` state file.
**Why:** Fixes the 3 documented HIGH-priority bugs (race condition, crash corruption, duplicate execution).

## Anti-Patterns to Avoid

### Anti-Pattern 1: Direct json.dump() to Target File

**What:** `open(path, "w") + json.dump(data, f)` with no atomic guarantee.
**Why bad:** Process kill mid-write = truncated file = corrupted state = crash on next load.
**Current scope:** 40+ modules use this pattern.
**Instead:** Use `state_io.write_json()`.

### Anti-Pattern 2: Module-Level Config Constants

**What:** Each module defines `OLLAMA_URL = os.environ.get(...)` independently.
**Why bad:** 15 modules each with same default, different env var names. Changing HomeLab IP requires editing 15+ files.
**Instead:** Single `config.py`. All modules import from it.

### Anti-Pattern 3: Lazy Imports to Avoid Circular Dependencies

**What:** `from X import Y` inside function bodies.
**Why bad:** Hides dependency cycles. Breaks IDE navigation. Performance cost per call.
**Current violations:** orchestrator.py (3 lazy imports), classifier_v7.py (1), telegram_router.py (2).
**Instead:** Fix the dependency direction. Extract shared constants into a shared module.

### Anti-Pattern 4: Copy-Paste Utility Functions

**What:** 20+ modules each define their own `_save_json()`, `_load_json()`, `_now()`, `_log()`.
**Why bad:** When the pattern needs fixing (atomic writes), must fix in 20 places.
**Instead:** Single `state_io.py` and `logging_config.py`.

### Anti-Pattern 5: God Module with Mixed Responsibilities

**What:** `orchestrator.py` doing classification + model selection + skill execution + response parsing + email state management + metrics tracking.
**Why bad:** Every change risks breaking unrelated functionality. 2373 lines means nobody reads the whole file.
**Instead:** One module, one responsibility.

## Suggested Refactoring Order (Dependency-Aware)

Order matters because of dependency chains. Refactor from the bottom (shared infrastructure) upward (domain logic).

### Phase 1: Foundation (No Behavior Change)

Create three new modules that have zero dependencies on existing code:

**1a. `config.py`** -- Centralized config. All modules can start importing immediately.
**1b. `state_io.py`** -- Atomic JSON helpers. Adopted module-by-module.
**1c. `logging_config.py`** -- Standard JSONL logger factory.

**Why first:** These provide infrastructure every subsequent step uses. Independently testable. Zero risk.

### Phase 2: Extract Routing Package from orchestrator.py

Split in this specific order (each step independently compilable):

**2a.** `routing/classifier.py` -- CLASSIFICATION_RULES + classify_query(). Most self-contained piece, no outbound deps except config.
**2b.** `routing/skill_parsers.py` -- All _parse_* functions. Pure functions, zero dependencies.
**2c.** `routing/skill_handlers.py` -- All _handle_* functions. Depends on devices/ and subprocess.
**2d.** `routing/skill_executor.py` -- execute_skill() + SKILL_MAP. Depends on parsers + handlers.
**2e.** `routing/metrics.py` -- Metrics globals and helpers.
**2f.** `routing/router.py` -- route_query() + escalate() + select_model(). Depends on classifier + models/.
**2g.** `routing/multitool.py` -- Multitool execution path.
**2h.** `routing/__init__.py` -- Facade with route_and_execute() + all re-exports.
**2i.** Replace `orchestrator.py` body with `from routing import *`.

**Why this order:** Each step introduces one file. After each step, all tests pass (facade ensures import compat). Step 2a has zero deps; each subsequent step depends only on files created in prior steps.

### Phase 3: Adopt Atomic State I/O (Fix Documented Bugs)

**3a.** `proposals.json` writes -> `state_io.locked_update()` (fixes race condition)
**3b.** `state.json` writes -> `state_io.write_json()` (fixes crash corruption)
**3c.** `pending_approvals.json` -> `state_io.locked_update()` (fixes duplicate execution)
**3d.** Audit + migrate remaining JSON writes module-by-module.

**Why after Phase 2:** The routing split is the biggest structural change. State I/O migration is surgical (one file per change). Better to do the risky structural work first.

### Phase 4: Adopt Centralized Config

**4a.** Migrate all `OLLAMA_URL` references (15+ files) to `from config import OLLAMA_GPU_URL`.
**4b.** Migrate device IPs to config imports.
**4c.** Migrate port numbers and timeouts.

**Why:** Low risk, high reward. Each change is mechanical find-replace. One file at a time.

### Phase 5: Group Remaining Modules into Subpackages

**5a.** Move autofix_*.py into `autofix/` package (7 files).
**5b.** Move learning modules into `learning/` package (add `__init__.py`).
**5c.** Group observability modules into `observability/` package.
**5d.** Group personal modules into `personal/` package.
**5e.** Optionally split model_router.py (856 lines) into `models/` package.

**Why last:** By this point, foundation (config, state_io) and the biggest file (orchestrator) are done. Grouping remaining modules is organizational, not bug-fixing.

### Phase 6: Clean Up Dead Code

**6a.** Delete `auto-heal.sh` after confirming not in DSM cron.
**6b.** Remove legacy model entries.
**6c.** Remove orphaned modules identified during prior phases.
**6d.** Remove backward-compat shims (orchestrator.py, model_router.py) once all imports updated.

### Dependency Graph (Build Order)

```
Phase 1 (foundation, independent):
  config.py
  state_io.py
  logging_config.py

Phase 2 (orchestrator split, sequential):
  routing/classifier.py    <- config
  routing/skill_parsers.py <- (nothing)
  routing/skill_handlers.py <- config, devices/
  routing/skill_executor.py <- parsers, handlers
  routing/metrics.py       <- config
  routing/router.py        <- classifier, models/, config
  routing/multitool.py     <- config
  routing/__init__.py      <- all above (facade)

Phase 3 (state fixes, independent per file):
  Each module: replace json.dump -> state_io.write_json

Phase 4 (config adoption, independent per file):
  Each module: replace OLLAMA_URL = ... -> from config import ...

Phase 5 (packaging, independent per group):
  autofix/*.py, learning/*.py, observability/*.py, personal/*.py
```

## Scalability Considerations

| Concern | Current (1 user) | At 5 users | At production scale |
|---------|-------------------|------------|---------------------|
| JSON state files | Works with atomic writes | Race conditions manageable with fcntl | Switch to SQLite |
| File locking (fcntl) | Sufficient | Sufficient (same container) | Need distributed locking |
| Config module | Perfect | Perfect | Consider YAML/env-only config |
| Orchestrator split | Needed now for maintainability | Needed now | Microservice boundary |

This system serves one user (Sebas) on one NAS. The refactoring targets **maintainability and reliability**, not horizontal scalability. SQLite migration is explicitly out of scope per PROJECT.md.

## Sources

- [Refactoring Python Applications -- Real Python](https://realpython.com/python-refactoring/)
- [Splitting a module into multiple files -- TestDriven.io](https://testdriven.io/tips/3660b476-7aaa-4f7b-af22-28aa00fc871e/)
- [How to Refactor Complex Codebases -- freeCodeCamp](https://www.freecodecamp.org/news/how-to-refactor-complex-codebases)
- [Python Dependency Injection Guide -- DataCamp](https://www.datacamp.com/tutorial/python-dependency-injection)
- [DI in Python -- Dependency Injector docs](https://python-dependency-injector.ets-labs.org/introduction/di_in_python.html)
- [Atomic file writes in Python](https://iifx.dev/en/articles/460341744/how-to-implement-atomic-file-operations-in-python-for-crash-safe-data-storage)
- [Python os.replace atomicity](https://zetcode.com/python/os-replace/)
- [Escape Python Script Hell with Modules](https://blog.inedo.com/python/modularization-and-packages)
- Codebase analysis: `.planning/codebase/CONCERNS.md`, `ARCHITECTURE.md`, `STRUCTURE.md`

---

*Architecture research: 2026-04-28*
