# Codebase Structure

**Analysis Date:** 2026-04-28

## Directory Layout

```
openclaw/                                    # Project root
├── Dockerfile                               # Container image definition (Node.js 24, Python 3.9)
├── docker-compose.yml                       # Container orchestration (openclaw service)
├── startup.sh                               # Container startup entry point
├── auto-heal.sh                             # [DEPRECATED] VPN/container checks (replaced by runtime)
├── ollama-check.sh                          # Helper script for Ollama routing status
│
├── .planning/
│   └── codebase/
│       ├── ARCHITECTURE.md                  # This file — System architecture, layers, data flow
│       └── STRUCTURE.md                     # (you are here)
│
├── workspace/                               # Tenazas-specific code
│   ├── README.md
│   ├── TENAZAS-CONTEXT.md
│   ├── staging/                             # Pre-production isolation
│   │   └── README.md                        # Proposals reviewed but not yet applied
│   │
│   ├── runtime/                             # MAIN RUNTIME LAYER (event-driven, workers)
│   │   ├── __init__.py
│   │   ├── runtime.py                       # [ENTRY] 5-min event loop: detect→task→execute
│   │   ├── worker_supervisor.py             # [ENTRY] Isolate 5 workers + timeout protection
│   │   │
│   │   ├── orchestrator.py                  # [CORE] Query classification + routing + skill exec
│   │   ├── model_router.py                  # Model selection: role-based, VRAM-aware, V1/V2 APIs
│   │   ├── telegram_router.py               # [ENTRY] Telegram polling bot, deterministic interception
│   │   │
│   │   ├── workers/                         # Worker processes (isolated subprocesses)
│   │   │   ├── runtime_worker.py            # Core loop: update_state→detect→task→execute
│   │   │   ├── diagnostics_worker.py        # Pattern analysis, infra diagnostics
│   │   │   ├── rag_worker.py                # RAG retrieval quality monitoring
│   │   │   ├── agents_worker.py             # Multi-agent analysis, skill analytics
│   │   │   └── learning_worker.py           # Idle learning, background agents
│   │   │
│   │   ├── devices/                         # IoT device drivers (9 types, modular)
│   │   │   ├── __init__.py                  # Device dispatcher + composite actions
│   │   │   ├── hue_lights.py                # Philips Hue REST API (8 lights)
│   │   │   ├── samsung_tv.py                # Samsung WebSocket :8001
│   │   │   ├── marantz_avr.py               # Marantz CINEMA 60 HTTP
│   │   │   ├── hue_sync_box.py              # Hue Sync Box HTTPS+Bearer
│   │   │   ├── google_cast.py               # Nest Mini/Home Mini (pychromecast)
│   │   │   ├── xiaomi_vacuum.py             # Xiaomi S20+ (python-miio)
│   │   │   ├── shield_tv.py                 # NVIDIA SHIELD (androidtvremote2)
│   │   │   ├── ezviz_cameras.py             # EZVIZ (ping only, 10 devices)
│   │   │   └── nest_doorbell.py             # Nest Doorbell (ping only)
│   │   │
│   │   ├── finance/                         # Financial tracking storage
│   │   │   └── transactions.json            # Shared by finance.py + expenses.py
│   │   │
│   │   ├── finance.py                       # Parse NL transactions, categorize, alert
│   │   ├── expenses.py                      # Quick daily expense capture (ultra-short input)
│   │   ├── jobs.py                          # Job search pipeline (state machine)
│   │   ├── smoking.py                       # Cigarette tracking + metrics
│   │   │
│   │   ├── jobs/                            # Jobs storage
│   │   │   └── opportunities.json           # Pipeline state + analysis
│   │   │
│   │   ├── smoking/                         # Smoking tracker storage
│   │   │   └── log.json + metrics.json
│   │   │
│   │   ├── memory/                          # Episodic memory consolidation
│   │   │   └── dreams.md                    # REM consolidation (append-only)
│   │   │
│   │   ├── learning/                        # Self-improvement proposals
│   │   │   ├── proposals.json               # All proposals (status: proposed/reviewed/accepted)
│   │   │   ├── learning_report.md           # Human-readable analysis report
│   │   │   ├── daily_improvement.json       # 1 improvement idea per day
│   │   │   └── daily_improvement.md         # Formatted for Telegram
│   │   │
│   │   ├── logs/                            # Event logs (JSONL format)
│   │   │   ├── telegram_router.log          # Message classification, skill exec, LLM calls
│   │   │   ├── orchestrator.log             # Routing decisions, model selection
│   │   │   ├── model_router.log             # Backend pings, role classification
│   │   │   ├── runtime.log                  # Event detection, task execution
│   │   │   ├── finance.log                  # Transaction parsing, alerts
│   │   │   ├── health_sweep.log             # System health scores
│   │   │   └── *.log (others)               # 10+ other modules
│   │   │
│   │   ├── state/                           # Persistent runtime state (JSON)
│   │   │   ├── state.json                   # Current system snapshot
│   │   │   ├── events.json                  # Last detected events
│   │   │   ├── memory_log.json              # Event history (max 1000 entries, capped)
│   │   │   ├── patterns.json                # Time-correlated patterns + last_triggered
│   │   │   ├── guardrail_state.json         # Model router guardrail state
│   │   │   ├── device_inventory.json        # Network discovery cache (42 devices)
│   │   │   ├── deco_clients.json            # TP-Link Deco WiFi clients
│   │   │   ├── recent_context.json          # Working memory for LLM context
│   │   │   └── *.json (others)              # ~15 other state files
│   │   │
│   │   ├── schemas/                         # JSON schemas for validation
│   │   │   ├── proposal.json                # Proposal schema
│   │   │   └── *.json (others)
│   │   │
│   │   ├── tool_tuning/                     # Fine-tuning pipeline & model training
│   │   │   ├── build_multitool_dataset.py   # Generate ~296 examples (JSONL)
│   │   │   ├── train_multitool.py           # LoRA/QLoRA training on RTX 3090
│   │   │   ├── eval_runner.py               # Evaluate finetuned model (P/R/F1)
│   │   │   ├── Modelfile                    # Ollama model definition (7B finetuned)
│   │   │   ├── full_pipeline.bat            # End-to-end: train→merge→gguf→ollama
│   │   │   ├── convert_to_gguf.bat          # HF→GGUF conversion (required for ollama)
│   │   │   └── *.py (4 more: merge, compare, setup, verify)
│   │   │
│   │   ├── proposal_executors/              # Execute approved proposals
│   │   │   ├── __init__.py
│   │   │   ├── base.py                      # Abstract executor base class
│   │   │   ├── network_retry_policy.py      # Network retry logic upgrade
│   │   │   ├── runtime_name_error_fix.py    # Runtime NameError repairs
│   │   │   ├── sandbox_adapter.py           # Sandbox mode safety
│   │   │   └── threshold_change.py          # Guardrail threshold tuning
│   │   │
│   │   ├── CORE INTELLIGENCE (42 modules) ─
│   │   │ ├── confidence_engine.py           # RAG confidence scoring (similarity+recency+agreement)
│   │   │ ├── context_manager.py             # Token budget allocation, pruning
│   │   │ ├── decision_trace.py              # Decision tree logging
│   │   │ ├── agent_lab.py                   # Multi-agent reasoning
│   │   │ ├── agent_conflict.py              # Agent disagreement detection
│   │   │ ├── background_agents.py           # Watcher, pattern, metrics agents
│   │   │ ├── knowledge_graph.py             # Infra dependency graph
│   │   │ ├── infra_diagnostics.py           # Rule-based infra health assessment
│   │   │ ├── infra_memory.py                # Pattern detection from diagnostics
│   │   │ ├── predictive_engine.py           # Trend-based failure prediction
│   │   │ ├── action_mapper.py               # Problem → action recommendations
│   │   │ ├── action_registry.py             # Action risk levels + metadata
│   │   │ ├── action_validator.py            # Validate actions before execution
│   │   │ ├── action_budget.py               # Rate limiting for actions (per-container)
│   │   │ ├── decision_quality.py            # Decision accuracy metrics
│   │   │ ├── confidence_calibration.py      # Calibrate confidence scoring
│   │   │ ├── noise_filter.py                # Suppress false positives
│   │   │ └── (11 more intelligence modules)
│   │   │
│   │   ├── LEARNING & IMPROVEMENT ─────────
│   │   │ ├── idle_learning.py               # [MAIN] Critic+synthesizer analysis
│   │   │ ├── daily_improvement.py           # 1 concrete idea per day
│   │   │ ├── daily_brief.py                 # Unified daily digest (08:00)
│   │   │ ├── error_mining.py                # Extract problems from logs
│   │   │ ├── improvement_candidates.py      # Type proposals (routing, guardrail, etc.)
│   │   │ ├── dreaming.py                    # REM memory consolidation
│   │   │ ├── apply_improvements.py          # Execute approved proposals
│   │   │ ├── dataset_builder.py             # Tool augmentation + synthetic data gen
│   │   │ ├── eval_runner.py                 # Orchestrator classification eval
│   │   │ ├── fine_tune_pipeline.py          # End-to-end finetuning (HomeLab SSH)
│   │   │ ├── finetuned_router.py            # Intent classifier via Ollama
│   │   │ ├── intent_normalizer.py           # Router output → NormalizedCall
│   │   │ ├── benchmark_continuous.py        # 24/7 model quality monitoring
│   │   │ ├── continuous_learning_loop.py    # Closed-loop: approved→exp→validate
│   │   │ └── (4 more learning modules)
│   │   │
│   │   ├── AUTO-HEAL (3-tier safety) ──────
│   │   │ ├── autofix_agent.py               # [MAIN] Orchestrator + CLI + kill switch
│   │   │ ├── autofix_tiers.py               # Regression detection + tier classification
│   │   │ ├── autofix_sandbox.py             # Git ops (branch, diff, merge, rollback)
│   │   │ ├── autofix_aider.py               # Aider CLI wrapper (constrained)
│   │   │ ├── autofix_ssh.py                 # SSH whitelist (Ollama pull only)
│   │   │ ├── autofix_telegram.py            # Notify + approval flow
│   │   │ ├── autofix_llm_check.py           # Backend availability check
│   │   │ └── autofix_vram_manager.py        # VRAM optimization during fixes
│   │   │
│   │   ├── OBSERVABILITY ───────────────────
│   │   │ ├── metrics_server.py              # Prometheus :9105 scrape endpoint
│   │   │ ├── metrics_exporter.py            # Collect + format metrics
│   │   │ ├── health_sweep.py                # Full-stack health (25 probes, parallel)
│   │   │ ├── roi_tracker.py                 # Skill usage → hours saved
│   │   │ ├── roi_report.py                  # Weekly ROI Telegram digest
│   │   │ ├── shared_memory.py               # Episodic/semantic/procedural memory
│   │   │ ├── operational_memory.py          # Fact store by domain (finance/jobs/etc)
│   │   │ ├── world_model.py                 # Infra topology + status (entities, deps)
│   │   │ ├── log_rotation.py                # Rotate logs >5MB, keep 5 backups
│   │   │ └── (3 more observability modules)
│   │   │
│   │   ├── MISC ───────────────────────────
│   │   ├── docker_wrapper.py                # Whitelist-enforced Docker subprocess
│   │   ├── safety_guard.py                  # Central security validator
│   │   ├── executors.py                     # Task execution (restart, network check)
│   │   ├── freeze_detector.py               # Background watchdog for hangs
│   │   ├── devin_lite.py                    # Crash capture + suggestions
│   │   ├── adaptive_router.py               # Learned routing preferences
│   │   ├── multitool_parser.py              # XML-based multi-tool selection
│   │   ├── response_composer.py             # Deterministic response building
│   │   ├── personal_supervisor.py           # Domain-aware query routing
│   │   ├── classifier_v7.py                 # Intent classification v7
│   │   └── (10+ more utility modules)
│   │
│   └── skills/                              # [PROPOSED] Skills directory (not yet populated)
│       └── (Skills moved to runtime/ for now, would migrate here)
│
├── state/                                   # Container persistent state (docker-compose volume)
│   ├── openclaw.json                        # [SECRETS] Config: bot token, API keys
│   ├── .openclaw/                           # Home dir for tenazas user
│   │   ├── workspace/                       # Shared with container /home/tenazas/.openclaw/workspace
│   │   ├── SOUL.md                          # Agent personality + system prompt
│   │   ├── AGENTS.md                        # Agent definitions (5 agents)
│   │   └── MEMORY.md                        # Persistent user memory/preferences
│   │
│   └── (other files from Aider, pytest cache, etc.)
│
├── himalaya-config/                         # Email client configuration (gitignored)
│   └── config.toml                          # Gmail IMAP/SMTP credentials
│
├── credentials/                             # API keys directory (gitignored)
│   ├── gemini.json                          # Google Gemini API key
│   └── (other API keys)
│
├── gcal-token.json                          # [SECRETS] Google Calendar OAuth token
├── gcal-creds.json                          # [SECRETS] Google Calendar OAuth credentials
│
├── *.md                                     # Documentation
│   ├── CLAUDE.md                            # Project instructions for Claude
│   ├── INFRA.md                             # Infrastructure notes
│   └── (others)
│
├── *.sh & *.py (root)                       # Standalone scripts (legacy)
│   ├── learn.py                             # Weekly learning analysis
│   ├── self-evolve.py                       # Weekly self-evolution + Kimi K2 analysis
│   └── (others)
│
├── grafana-*.json                           # Grafana dashboard definitions (11 dashboards)
├── prometheus.yml                           # Prometheus scrape config
├── alerts.yml                               # Alert rules for Prometheus
│
└── tests/                                   # Test suites (100+ test files, 1000+ tests)
    ├── test_runtime.py                      # Runtime loop, events, tasks, patterns (54 tests)
    ├── test_orchestrator.py                 # Classification, routing, V1/V2 (65 tests)
    ├── test_model_router.py                 # V1/V2 model selection, guardrails (93 tests)
    ├── test_telegram_router.py              # Skill interception, LLM forward
    ├── test_query.py                        # RAG confidence, reranking (34 tests)
    ├── test_confidence.py                   # Confidence scoring, diagnostics (40 tests)
    ├── test_health_sweep.py                 # Full-stack health probes (31 tests)
    ├── test_autofix.py                      # 3-tier autofix, regression detection (54 tests)
    ├── test_idle_learning.py                # Multi-model analysis (27 tests)
    ├── test_error_mining.py                 # Log parsing, classification
    ├── test_finance.py                      # Transaction parsing, categorization
    ├── test_jobs.py                         # Job pipeline state machine
    ├── (50+ more test files)
    │
    └── simulation/                          # Scenario-based simulation tests
        ├── governance/                      # Container flapping, multi-incident
        └── longrun/                         # 1h activity, progressive degradation
```

## Directory Purposes

**`workspace/runtime/`** — Main Python runtime layer
- Purpose: Event-driven orchestration, model routing, skill execution, learning
- Contains: 100+ Python modules for monitoring, routing, skills, intelligence
- Key files: `runtime.py` (event loop), `orchestrator.py` (routing), `model_router.py` (model selection), `telegram_router.py` (Telegram bot)

**`workspace/runtime/devices/`** — IoT device drivers
- Purpose: Control smart home devices via modular drivers
- Contains: 9 device types (Hue, Samsung, Marantz, Deco, Nest, etc.)
- Key files: `__init__.py` (dispatcher), individual device modules
- Pattern: Each device exports `on()`, `off()`, `status()`, `command(action, args)` functions

**`workspace/runtime/finance/`** — Financial transaction storage
- Purpose: Persistent storage for income/expenses
- Contains: `transactions.json` (shared by finance.py + expenses.py)

**`workspace/runtime/jobs/`** — Job search pipeline storage
- Purpose: Persistent storage for opportunities
- Contains: `opportunities.json` (state machine state)

**`workspace/runtime/learning/`** — Self-improvement proposals
- Purpose: Staging area for improvement ideas (never auto-applied)
- Contains: `proposals.json` (all proposals with status), `learning_report.md` (analysis), `daily_improvement.*` (daily idea)

**`workspace/runtime/logs/`** — Event stream (JSONL format)
- Purpose: All runtime activities as structured logs
- Contains: 10+ log files (telegram_router.log, orchestrator.log, finance.log, etc.)
- Pattern: One JSON object per line, always has `ts` field, searchable/parseable

**`workspace/runtime/state/`** — Persistent runtime state
- Purpose: Current snapshot and historical state for monitoring/recovery
- Contains: `state.json` (containers/VPN/internet), `memory_log.json` (event history), `patterns.json` (recurring patterns), guardrail state
- Regenerated: Yes (can be manually deleted to reset state)

**`workspace/runtime/tool_tuning/`** — Fine-tuning infrastructure
- Purpose: Build finetuned models via LoRA, evaluate, deploy to Ollama
- Contains: Training pipeline, eval harness, model conversion tools, Home Lab training scripts
- Key files: `build_multitool_dataset.py`, `train_multitool.py`, `eval_runner.py`, `full_pipeline.bat`

**`workspace/runtime/proposal_executors/`** — Execute approved proposals
- Purpose: Safe execution of accepted improvement proposals
- Contains: Modular executors (network_retry_policy, threshold_change, runtime_name_error_fix, sandbox_adapter)
- Pattern: Each executor inherits from `base.py`, implements `execute(proposal_data)`

**`workspace/staging/`** — Pre-production isolation
- Purpose: Hold proposals/changes before user review + execution
- Contains: Nothing by default; proposals reviewed from `learning/proposals.json` and executed from here
- Pattern: Staging directory is for future use; currently proposals go directly to JSON

**`state/openclaw.json`** — Configuration + secrets (gitignored)
- Purpose: Runtime config, API keys, bot token, credentials
- Contents: Telegram bot token, Google Gemini key, Ollama endpoints, device config, feature flags
- Access: Loaded by `runtime_config.py` and orchestrator

**`tests/`** — Comprehensive test suite
- Purpose: Validate all major runtime components
- Contains: 100+ test files, 1000+ test cases covering runtime, orchestrator, model_router, health_sweep, autofix, learning
- Patterns: unittest + mock, simulation scenarios, adversarial tests, regression tests

## Key File Locations

**Entry Points:**
- `workspace/runtime/runtime.py` — Main 5-min event loop (docker exec openclaw python3 runtime.py)
- `workspace/runtime/worker_supervisor.py` — Worker orchestrator (isolated subprocesses with timeout)
- `workspace/runtime/telegram_router.py` — Telegram polling bot (runs continuously)

**Configuration:**
- `state/openclaw.json` — API keys, bot token, device config, feature flags (gitignored, mounted as volume)
- `state/.openclaw/SOUL.md` — System prompt, agent personality
- `state/.openclaw/AGENTS.md` — Agent definitions

**Core Logic:**
- `workspace/runtime/orchestrator.py` — Query classification + routing
- `workspace/runtime/model_router.py` — Model selection by role + hardware
- `workspace/runtime/docker_wrapper.py` — Safe Docker subprocess wrapper

**Monitoring & Learning:**
- `workspace/runtime/idle_learning.py` — Multi-model analysis (critic+synthesizer)
- `workspace/runtime/error_mining.py` — Extract problems from logs
- `workspace/runtime/health_sweep.py` — Full-stack health check (25 probes)
- `workspace/runtime/metrics_server.py` — Prometheus scrape endpoint (:9105)

**Skills & Automation:**
- `workspace/runtime/finance.py` — Personal finance tracking
- `workspace/runtime/jobs.py` — Job search pipeline
- `workspace/runtime/devices/` — IoT device control (Hue, Samsung, etc.)

**Testing:**
- `tests/test_runtime.py` — Event loop, detection, task execution (54 tests)
- `tests/test_orchestrator.py` — Classification + routing (65 tests)
- `tests/test_model_router.py` — V1/V2 routing (93 tests)
- `tests/test_health_sweep.py` — Health probes (31 tests)
- `tests/test_autofix.py` — Auto-healing tiers (54 tests)

## Naming Conventions

**Files:**
- `*.py` — Python source (snake_case module names)
- `*.log` — Text logs (rotated via RotatingFileHandler)
- `*.json` — State/config (JSON format, one root object or array)
- `*_test.py` / `test_*.py` — Test files
- `*.md` — Documentation (Markdown)

**Directories:**
- `lowercase/` for module grouping (devices, workers, tools, learning, etc.)
- `UPPERCASE.md` for doc files (ARCHITECTURE.md, STRUCTURE.md, SOUL.md)

**Python Functions:**
- `_private_function()` — Private (single leading underscore)
- `public_function()` — Public API
- `ClassName` — Classes (PascalCase)
- `CONSTANT` — Module constants (UPPER_CASE)

**Config Keys:**
- `snake_case` in JSON/TOML (e.g., `bot_token`, `ollama_gpu_url`)
- Secrets prefixed with `$` in docs (e.g., `$OPENROUTER_API_KEY`)

**Log Fields:**
- `ts` — ISO timestamp (always present)
- `type` / `action` — Log entry type
- `target` — Resource being acted on (container name, IP, etc.)
- `success` / `ok` — Boolean result
- `error` / `reason` — Failure explanation
- `detail` / `metadata` — Additional context

## Where to Add New Code

**New Feature:**
- Primary code: `workspace/runtime/{feature}.py` (if skill) or new module in runtime
- Tests: `tests/test_{feature}.py`
- Config: Keys in `state/openclaw.json` (device endpoints, feature flags)
- Docs: Update `state/.openclaw/SOUL.md` if affects system behavior

**New IoT Device:**
- Implementation: `workspace/runtime/devices/{device_name}.py`
- Register: Add to `DEVICE_MAP` in `workspace/runtime/devices/__init__.py`
- API: Implement `on()`, `off()`, `status()`, `command(action, args)`
- Tests: `tests/test_devices_{device_name}.py`

**New Skill:**
- Implementation: `workspace/runtime/{skill_name}.py` (follows finance.py, jobs.py pattern)
- Integrate: Add classification rule in `orchestrator.py` (CLASSIFICATION_RULES)
- Tests: `tests/test_{skill_name}.py`
- Docs: Describe in `state/.openclaw/SOUL.md` trigger keywords

**Utilities:**
- Shared helpers: `workspace/runtime/{helper_name}.py` (e.g., `confidence_engine.py`, `action_mapper.py`)
- Only use if 2+ modules import it; otherwise keep in parent module

**Database/Persistence:**
- Simple state: `workspace/runtime/{domain}/*.json` (finance/transactions.json, jobs/opportunities.json)
- Complex queries: Consider SQLite in `workspace/runtime/{domain}.sqlite` (only if >10k records)

**Tests:**
- Unit tests: `tests/test_{module}.py` — Mock external dependencies
- Integration tests: `tests/test_integration_{feature}.py` — Real state files, limited external calls
- Simulation tests: `tests/simulation/{scenario}.py` — Full scenario playback
- Regression tests: `tests/test_regression_{bug}.py` — Verify bug doesn't reoccur

## Special Directories

**`workspace/runtime/logs/`:**
- Purpose: Event stream for observability
- Generated: Yes (created by runtime modules)
- Committed: No (gitignored)
- Rotation: RotatingFileHandler 5MB max, 5 backups per file
- Format: JSONL (one JSON object per line)

**`workspace/runtime/state/`:**
- Purpose: Runtime state persistence
- Generated: Yes (created on first run)
- Committed: No (gitignored, except schema definitions)
- Rotation: Manual trimming on capacity (e.g., memory_log.json cap 1000 entries)
- Format: JSON (one root object per file)

**`workspace/staging/`:**
- Purpose: Pre-production proposal staging
- Generated: No (reserved for future use)
- Committed: Yes (README explaining purpose)
- Migration: Proposals currently live in `learning/proposals.json`

**`state/`:**
- Purpose: Docker volume mount for persistent data
- Generated: Yes (at runtime)
- Committed: No (gitignored)
- Contains: openclaw.json (secrets), workspace snapshots, Aider venv, pytest cache
- Cleanup: Safe to delete entire directory; runtime will regenerate state on startup

**`tests/`:**
- Purpose: Automated testing
- Generated: No (committed)
- Execution: `python3 -m pytest tests/` or per-suite execution
- CI: Run on every commit via pre-commit hooks (future)

---

*Structure analysis: 2026-04-28*
