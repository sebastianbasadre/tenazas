<!-- refreshed: 2026-04-28 -->
# Architecture

**Analysis Date:** 2026-04-28

## System Overview

```text
┌────────────────────────────────────────────────────────────────┐
│                   User Input Layer                              │
├──────────────────┬──────────────────┬────────────────────────┤
│ Telegram Bot     │ HTTP Endpoints   │ CLI Commands           │
│ `telegram_router`│ (OpenClaw GW)    │ (Skills / Direct)      │
└────────┬─────────┴────────┬─────────┴──────────┬─────────────┘
         │                  │                     │
         ▼                  ▼                     ▼
┌────────────────────────────────────────────────────────────────┐
│              Orchestration Layer                                │
│  `orchestrator.py` — Central AI routing                        │
│  - Classifies queries (chat, skill, infra, reasoning)          │
│  - Routes to optimal model via Router V2                       │
│  - Executes skills deterministically                           │
└────────┬──────┬──────────────────────────┬────────────────────┘
         │      │                          │
         ▼      ▼                          ▼
     ┌─────────────────────────────────────────────────┐
     │      Model Router V2 (role-based)               │
     │ `model_router.py`                               │
     │ - Detects backends (HomeLab GPU, NAS CPU)       │
     │ - Routes by role: fast/reason/code/vision/embed │
     │ - Manages VRAM, guardrails, fallback chains     │
     └──────────┬──────────────────┬────────────────┬──┘
                │                  │                │
    ┌───────────▼────┐  ┌──────────▼──────┐   ┌────▼─────────┐
    │ HomeLab GPU    │  │ NAS CPU Fallback│   │ Cloud APIs   │
    │ 192.168.2.6:11434  │ 127.0.0.1:11434│   │ OpenRouter   │
    │ Ollama models  │  │ Lightweight     │   │ Gemini       │
    └────────────────┘  └─────────────────┘   └──────────────┘
         │
         ▼
     ┌──────────────────────────────────────┐
     │  Runtime Loop (5-min cron event)    │
     │  `runtime.py` — Main orchestration  │
     │  1. Detect events (docker, VPN, net)│
     │  2. Enqueue tasks (restart, heal)   │
     │  3. Execute safely via docker_wrapper│
     │  4. Log memory & patterns           │
     │  5. Pattern-based preemptive action │
     └──────────────────────────────────────┘
         │
         ▼
     ┌─────────────────────────────────────────┐
     │  Skill Execution & Integrations        │
     │  ─────────────────────────────────────  │
     │  email, weather, calendar, smart-home  │
     │  network-scanner, arr-monitor,         │
     │  infra-monitor, tts, reminders,        │
     │  self-evolve, rag, morning-greeting    │
     └──────────────────────────────────────────┘
         │
         ▼
     ┌─────────────────────────────────────────┐
     │  IoT Device Layer                      │
     │  `devices/` — Modular device control   │
     │  Hue, Samsung TV, Marantz, Deco, etc. │
     └──────────────────────────────────────────┘
         │
         ▼
     ┌──────────────────────────────────────────┐
     │  Personal Modules (Standalone)          │
     │  ─────────────────────────────────────── │
     │  finance.py — Transactions, alerts      │
     │  expenses.py — Quick expense capture    │
     │  jobs.py — Job search pipeline          │
     │  smoking.py — Habit tracking            │
     └──────────────────────────────────────────┘
         │
         ▼
     ┌────────────────────────────────────────────┐
     │  Learning & Improvement Layer              │
     │  ──────────────────────────────────────── │
     │  idle_learning.py — Analysis + proposals  │
     │  daily_improvement.py — 1 concrete idea   │
     │  daily_brief.py — Unified daily digest    │
     │  dreaming.py — REM memory consolidation   │
     │  error_mining.py — Production analytics  │
     │  improvement_candidates.py — Typing      │
     │  dataset_builder.py — Tool augmentation  │
     │  apply_improvements.py — Approved apply  │
     └────────────────────────────────────────────┘
         │
         ▼
     ┌─────────────────────────────────────┐
     │  Observability & Persistence        │
     │  metrics_server.py — Prometheus     │
     │  health_sweep.py — Full-stack check │
     │  state files (JSON) — Runtime state │
     │  logs (JSONL) — Event stream        │
     │  learning/ — Proposals & dreaming   │
     └─────────────────────────────────────┘
```

## Component Responsibilities

| Component | Responsibility | File |
|-----------|----------------|------|
| **Orchestrator** | Classify queries, route to models/skills, execute deterministically | `orchestrator.py` |
| **Model Router V2** | Select optimal model by role, manage VRAM/guardrails, fallback chains | `model_router.py` |
| **Telegram Router** | Poll Telegram, classify skills deterministically, forward others to LLM | `telegram_router.py` |
| **Runtime Loop** | Event detection → task enqueue → safe execution → memory → patterns | `runtime.py` |
| **Docker Wrapper** | Whitelist-enforced Docker subprocess calls (restart/ps/start/stop only) | `docker_wrapper.py` |
| **IoT Dispatcher** | Route device commands, manage composite actions, handle multi-device queries | `devices/__init__.py` |
| **Worker Supervisor** | Run 5 isolated worker subprocesses, protect against freezes, stagger execution | `worker_supervisor.py` |
| **Learning Engine** | Multi-model critic+synthesizer, extract truths, generate typed proposals | `idle_learning.py` |
| **Error Mining** | Parse logs, classify failures, detect patterns for improvement candidates | `error_mining.py` |
| **Finance Module** | Parse natural language transactions, categorize, alert on anomalies | `finance.py` |
| **RAG Pipeline** | Retrieve + rerank context from ChromaDB, inject into LLM prompts | `skills/rag/query.py` |
| **Self-Heal Agent** | Tier-1 OPS, Tier-2 CODE, Tier-3 PROPOSAL approval workflow | `autofix_agent.py` |

## Pattern Overview

**Overall:** Event-driven, deterministic-first architecture with optional LLM augmentation.

**Key Characteristics:**
- **5-minute event loop** — Cron runs `runtime.py` every 5 min for infrastructure monitoring
- **Skill-first routing** — Orchestrator intercepts known skills (weather, calendar, email) before LLM
- **Role-based model selection** — V2 router classifies prompts by role (fast/reason/code) and selects hardware-optimal model
- **Deterministic execution** — Docker restarts, device commands, finance transactions executed safely without LLM
- **Approval gates** — High-risk actions (code fixes, tier-2 autofix) require Telegram approval before execution
- **Learning in isolation** — Proposals never auto-apply; always review→accept→execute workflow
- **VRAM-aware routing** — Model selection respects GPU/CPU constraints, falls back to cloud if needed
- **Modular devices** — Each IoT device type in separate module, composed via dispatcher

## Layers

**Layer 1: User Interface**
- Purpose: Accept input from users (Telegram, HTTP endpoints, CLI)
- Location: `telegram_router.py`, OpenClaw gateway (Node.js), CLI scripts
- Contains: Message polling, payload parsing, response formatting
- Depends on: Orchestrator for routing
- Used by: All upstream; public-facing interface

**Layer 2: Orchestration**
- Purpose: Classify queries, route to optimal pipeline (LLM/skill/diagnostics), execute
- Location: `orchestrator.py`
- Contains: Classification rules (skill, infra, reasoning, etc.), pipeline definitions, skill execution engine
- Depends on: Model Router V2 (for model selection), skill modules, RAG, diagnostics
- Used by: Telegram Router, HTTP gateway, CLI commands

**Layer 3: Model Selection & Inference**
- Purpose: Route queries to best LLM by capability, manage hardware constraints
- Location: `model_router.py`
- Contains: Backend detection (ping HomeLab/NAS), role classification, VRAM tracking, guardrails, fallback chains
- Depends on: Ollama endpoints (local GPU/CPU), OpenRouter API (cloud)
- Used by: Orchestrator, Worker pipeline

**Layer 4: Event & Task Execution**
- Purpose: Monitor infrastructure, detect anomalies, execute safe remediation
- Location: `runtime.py`, `executors.py`, `docker_wrapper.py`
- Contains: State monitoring (docker ps, network scan, VPN check), task enqueue, execution with cooldown
- Depends on: Docker socket, network tools (nmap, curl), pattern engine
- Used by: Cron scheduler (every 5 min)

**Layer 5: Skill Execution**
- Purpose: Deterministic implementation of specific capabilities (weather, calendar, smart-home, etc.)
- Location: `workspace/skills/*/`, `devices/`
- Contains: Email (himalaya), Calendar (Google Calendar API), Weather (Open-Meteo), Device commands (REST/WebSocket)
- Depends on: External APIs, local services (Piper for TTS)
- Used by: Orchestrator skill_execution engine

**Layer 6: IoT Devices**
- Purpose: Control smart home devices and infrastructure
- Location: `devices/hue_lights.py`, `devices/samsung_tv.py`, `devices/marantz_avr.py`, etc.
- Contains: Device drivers for 9 device types, composite action support (e.g., cinema mode)
- Depends on: Device REST/WebSocket endpoints
- Used by: Orchestrator via device dispatcher

**Layer 7: Personal Data Modules**
- Purpose: Standalone management of personal finance, jobs, health tracking
- Location: `finance.py`, `expenses.py`, `jobs.py`, `smoking.py`
- Contains: Natural language parsing, categorization, state persistence
- Depends on: JSON persistence (no external APIs)
- Used by: Telegram router, daily brief, personal supervisor

**Layer 8: Learning & Improvement**
- Purpose: Analyze logs/metrics, generate improvement proposals, support review workflow
- Location: `idle_learning.py`, `error_mining.py`, `improvement_candidates.py`, `dreaming.py`
- Contains: Multi-model analysis (critic+synthesizer), typed proposals, memory consolidation
- Depends on: Runtime logs, metrics, proposal execution engine
- Used by: Daily cron, workers (learning_worker)

**Layer 9: Observability**
- Purpose: Expose metrics, monitor health, support dashboards
- Location: `metrics_server.py`, `health_sweep.py`
- Contains: Prometheus scrape targets, SQLite health history, Grafana payload builders
- Depends on: Runtime state files, external service health checks
- Used by: Grafana dashboards, alert systems

## Data Flow

### Primary Request Path (User Message to Response)

1. **Message arrives** → Telegram API → `telegram_router.py` polls updates (`/api/getUpdates`)
2. **Classify intent** → `classify_intent()` using skill keywords + fallback to orchestrator
3. **Skill match?** → YES: execute directly via `orchestrator.route_and_execute()` → return raw data
4. **Skill match?** → NO: forward to LLM with conversation history
5. **Route to model** → Orchestrator calls `model_router.route_by_role(prompt, flags)` → selects model by role + hardware
6. **Query LLM** → Call selected backend (HomeLab GPU, NAS CPU, or cloud fallback)
7. **Stream/format response** → Inject conversation history, strip to Telegram text limit
8. **Send response** → Telegram `/api/sendMessage` → user receives

**File locations:**
- User input: `telegram_router.py` line ~200 (update loop)
- Classification: `telegram_router.py` line ~400 (`classify_intent()`)
- Skill execution: `orchestrator.py` line ~600 (`route_and_execute()`)
- Model selection: `model_router.py` line ~250 (`route_by_role()`)
- Response send: `telegram_router.py` line ~800 (`_send_response()`)

### Infrastructure Monitoring Path (Event-Driven)

1. **Cron trigger** → DSM Task Scheduler runs `runtime.py` every 5 minutes
2. **Update state** → `update_state()` polls docker containers, internet status, VPN IP, network devices, Ollama health
3. **Detect events** → Compare prev vs curr state → emit events (container_down, vpn_leak, internet_lost, etc.)
4. **Enqueue tasks** → Map events to safe actions (docker.restart for arr-stack, gluetun restart on VPN)
5. **Execute tasks** → `docker_wrapper.py` subprocess each action with validation
6. **Append memory** → Log all events+results to `memory_log.json`
7. **Pattern detection** → Analyze 7-day lookback for recurring (event_type, target, hour) patterns
8. **Trigger patterns** → If current hour matches pattern and not triggered today, enqueue preemptive restart
9. **Notify Telegram** → Only send notification if actions taken or Ollama offline

**File locations:**
- Main loop: `runtime.py` line ~1 (`main()`)
- Update state: `runtime.py` line ~150 (`update_state()`)
- Event detection: `runtime.py` line ~206 (`detect_events()`)
- Task enqueue: `runtime.py` line ~296 (`enqueue_tasks()`)
- Execution: `runtime.py` line ~343 (`run_tasks()`)
- Pattern analysis: `runtime.py` line ~381 (`analyze_patterns()`, `trigger_patterns()`)

### Self-Improvement Path (Learning → Proposal → Review → Execute)

1. **Learning worker runs** → `idle_learning.py` (daily or manual trigger)
2. **Critic analysis** → deepseek-r1:14b or Kimi K2 (OpenRouter) reads logs/metrics
3. **Synthesizer generation** → qwen2.5:32b or Kimi generates structured proposals
4. **Save proposals** → Append to `learning/proposals.json` with status=pending
5. **User review** → Telegram message with proposal summary, user clicks "Approve" or "Reject"
6. **Mark reviewed** → `review_proposal(id, "reviewed", "note")`
7. **Decision** → `review_proposal(id, "accepted"/"rejected", "reasoning")`
8. **If accepted** → `apply_improvements.py` executes via `proposal_executors/`
9. **Verify outcome** → Health sweep checks metrics before/after
10. **Log decision** → `decision_trace.log` records full decision tree

**File locations:**
- Learning loop: `idle_learning.py` line ~1
- Critic: `idle_learning.py` line ~100 (`run_critic()`)
- Synthesizer: `idle_learning.py` line ~150 (`run_synthesizer()`)
- Proposals save: `idle_learning.py` line ~200 (`save_proposals()`)
- Execution: `apply_improvements.py` line ~1

### State Management

**Persistent state files:**
- `state.json` — Current system snapshot (containers, internet, VPN, Ollama status)
- `events.json` — Last detected events
- `memory_log.json` — Event history (max 1000 entries, capped on trim)
- `patterns.json` — Learned time-correlated patterns + last_triggered timestamps
- `finance/transactions.json` — Income/expenses with timestamps
- `jobs/opportunities.json` — Job search pipeline state
- `smoking/log.json` — Cigarette log with timestamps
- `learning/proposals.json` — All proposals (pending/reviewed/accepted/rejected)
- `learning/dreams.md` — REM memory consolidation (append-only, dedup)

**In-memory state:**
- Routing metrics (v2_calls, v1_calls, fallbacks, role distribution) — reset on process restart
- Telegram conversation history per chat (sliding window 10 turns, 10 min TTL)
- Guardrail state (cooldowns, frequency counters, VRAM swap tracking) — persistent file `guardrail_state.json`

## Key Abstractions

**Query Classification:**
- Purpose: Map user messages to execution paths without full LLM inference
- Examples: `telegram_router.py` keyword matchers for skills, `orchestrator.py` category rules, `model_router.py` role heuristics
- Pattern: Regex rules first, keyword scoring second, fallback to LLM if ambiguous

**Task Execution Safety:**
- Purpose: Prevent dangerous or unauthorized Docker/file operations
- Examples: `docker_wrapper.py` whitelist (ps/restart/start/stop), blocklist (openclaw/watchtower), container name regex validation
- Pattern: Default-deny, explicit allowlist, sanitization before subprocess call

**Model Routing by Role:**
- Purpose: Select optimal model for task type without user specification
- Examples: fast (qwen2.5:7b), reason (deepseek-r1:14b), code (qwen2.5-coder:14b), heavy (qwen2.5:32b)
- Pattern: Heuristic classification → role → backend detection → VRAM fit check → guardrails → fallback chain

**Skill as Deterministic Subprocess:**
- Purpose: Execute known capabilities (weather, calendar, email) without LLM hallucination risk
- Examples: Weather calls Open-Meteo curl, Calendar queries Google Calendar API, Email queries Gmail via himalaya
- Pattern: Orchestrator intercepts → calls skill subprocess → captures output → formats for user

**Approval Gate Pattern:**
- Purpose: Require human review for risky actions before execution
- Examples: Autofix tier-2 code changes, high-risk proposals, infrastructure restarts
- Pattern: Action generated → queued for approval → Telegram buttons (approve/reject/details) → execute only if approved

**Proposal with Metadata:**
- Purpose: Track improvement ideas with context, decision history, and execution status
- Examples: Error-mining proposals (type: routing_adjustment), guardrail tuning (type: guardrail_tuning)
- Pattern: Generated with `id`, `type`, `status` (proposed/reviewed/accepted/rejected), `source`, `evidence`, never auto-applied

**Multi-Agent Coordination:**
- Purpose: Combine insights from multiple models (Qwen for synthesis, Deepseek for reasoning, Ollama for local speed)
- Examples: Critic (deepseek-r1) analyzes logs, Synthesizer (qwen2.5:32b) generates solutions
- Pattern: Critic provides analysis → synthesizer builds on analysis → merge results with dedup

## Entry Points

**HTTP/Telegram (Sync):**
- Location: `telegram_router.py` line ~1 (`main()`)
- Triggers: Telegram bot poll updates every 30s (long-polling)
- Responsibilities: Classify message, intercept skills, forward to LLM if needed, send response

**Cron Event Loop (Async every 5 min):**
- Location: `runtime.py` line ~1 (`main()`)
- Triggers: DSM Task Scheduler via `docker exec` every 5 minutes
- Responsibilities: Detect infrastructure events, enqueue tasks, execute, log, pattern detection

**Manual Learning (On-Demand):**
- Location: `idle_learning.py` line ~1 (`main()`)
- Triggers: CLI `python3 idle_learning.py` or cron (configurable)
- Responsibilities: Analyze logs/metrics via multi-model, generate proposals, save JSON

**Worker Supervisor (Isolated Subprocesses):**
- Location: `worker_supervisor.py` line ~1
- Triggers: Cron (alternative to direct `runtime.py`)
- Responsibilities: Run 5 workers (runtime, diagnostics, rag, agents, learning) with isolation + timeout protection

## Architectural Constraints

- **Threading:** Single-threaded event loop (runtime.py, telegram_router.py) — all concurrency via subprocess + multiprocessing for workers
- **Global state:** Orchestrator metrics stored in-memory with reset on process restart; guardrail state persisted to `guardrail_state.json`
- **Circular imports:** Models imported lazily in `model_router.py` to avoid import cycles with orchestrator
- **IPC:** No shared memory between workers; coordination via JSON state files + SQL (health_sweep.sqlite)
- **Model loading:** All Ollama models loaded on-demand (not preloaded); latency ~2-5s first inference
- **API rate limits:** OpenRouter balance checked hourly (cached), Telegram uses long-polling (no rate limit)
- **Docker socket:** Mounted at `/var/run/docker.sock` in container, only accessible via `docker_wrapper.py`

## Anti-Patterns

### LLM Over Determinism

**What happens:** Queries that have deterministic answers (weather, calendar, simple math) are sent to LLM instead of being intercepted
**Why it's wrong:** Adds latency, consumes API quota, LLM may hallucinate (e.g., inventing calendar events)
**Do this instead:** Add skill to orchestrator interception list + implement subprocess. Example: `telegram_router.py` line ~400 checks for skill keywords before forwarding to LLM

### Missing Approval for Risky Actions

**What happens:** Auto-executing code changes or infrastructure restarts without user review
**Why it's wrong:** Can break production systems; user loses visibility into what changed
**Do this instead:** Use approval gates in `approval_queue.py` + Telegram inline buttons. Tier-2 code changes always require approval before merge

### Blocking on External API Calls

**What happens:** Runtime loop hangs waiting for slow external service (Ollama offline, API timeout)
**Why it's wrong:** Blocks infrastructure monitoring; other checks starve; lockfile stales
**Do this instead:** Set explicit timeouts (30s default in `orchestrator.py`) + fallback to cached result or skip. Example: `update_state()` in `runtime.py` has GLOBAL_TIMEOUT_SECONDS

### Unbounded Memory Growth

**What happens:** `memory_log.json` grows indefinitely, consuming disk/RAM
**Why it's wrong:** Query latency degrades; state loading becomes slow
**Do this instead:** Cap entries at MAX_MEMORY_ENTRIES (1000) + trim oldest 50% on overflow (`runtime.py` line ~372)

## Error Handling

**Strategy:** Fail-safe with logged context; never crash the loop.

**Patterns:**
- Load state gracefully: `load_json()` returns default dict on missing/corrupt file
- Subprocess safety: All Docker calls wrapped in try-except + logged; non-zero exit doesn't stop loop
- LLM fallback: If Ollama offline, try NAS CPU; if both down, try cloud; if all fail, log and return empty
- Approval timeout: If user doesn't approve within 5 min, auto-expire and log decision
- Proposal parsing: JSON parse failures logged with `_raw` field for manual recovery

## Cross-Cutting Concerns

**Logging:** JSON-per-line format (JSONL) in `logs/` directory for parseability
- `telegram_router.log` — message classification, skill execution, LLM calls
- `orchestrator.log` — routing decisions, model selection, pipeline execution
- `model_router.log` — backend pings, role classification, guardrail decisions
- `runtime.log` — event detection, task execution, pattern triggers
- `finance.log` — transaction parsing, alerts

**Validation:**
- Container names: Regex `^[a-zA-Z0-9][a-zA-Z0-9_.-]*$` before Docker subprocess
- API responses: Check `ok` field, handle missing/malformed data gracefully
- User input (Telegram): Strip to 4096 char limit, sanitize Markdown for Telegram format

**Authentication:**
- Telegram: Bot token from `openclaw.json`, allowlist chat IDs from config
- Google Calendar: OAuth refresh token in `gcal-token.json` (read-only)
- Gmail: App password via himalaya config (IMAP/SMTP)
- IOL InvertirOnline: Bearer token from `openclaw.json`, refresh every 10 min

---

*Architecture analysis: 2026-04-28*
