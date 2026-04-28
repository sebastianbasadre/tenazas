# Codebase Concerns

**Analysis Date:** 2026-04-28

## Tech Debt

### JSON Files as Database (Critical)

**Issue:** 158 Python modules rely on direct JSON file read/write as persistent storage with no transaction semantics, locking, or concurrency control.

**Files:** `runtime.py`, `shared_memory.py`, `metrics_exporter.py`, `approval_queue.py`, `proposal_executor.py`, `api_call_counter.py`, and 40+ other modules.

**Impact:**
- Race conditions between cron job (every 5 min), background threads, and Telegram polling
- Data corruption if multiple processes write simultaneously (file truncation, incomplete JSON)
- Lost updates: if process A reads state.json, then process B writes, then A overwrites (lost B's updates)
- No rollback on failure: partial writes leave JSON files corrupted

**Evidence:**
- `shared_memory.py` lines 49-52: naive `open(path, "w")` + `json.dump()` without atomic ops
- `api_call_counter.py`: uses `threading.Lock()` but lock scope is local to that module — doesn't protect against external processes
- `approval_queue.py` line 60: writes entire queue with no atomic guarantee
- `runtime.py` lines 88-91: `save_json()` does basic `open/dump` with no atomic writes

**Fix approach:** 
- Replace JSON persistence with SQLite for critical state (approval_queue, api_calls, metrics)
- Use `fcntl.flock()` (Linux) / `msvcrt.locking()` (Windows) for atomic file operations
- Implement `write_atomic()` helper that writes to temp file then renames
- Add file versioning (VERSION field in JSON) to detect corrupted reads

### Deprecated auto-heal.sh Still in Cron (Medium)

**Issue:** `auto-heal.sh` is marked DEPRECATED in CLAUDE.md ("reemplazado por runtime layer") but still exists in repo and may be in DSM Task Scheduler cron.

**Files:** `/x/openclaw/auto-heal.sh` (exists), CLAUDE.md (documents as DEPRECATED_BY_RUNTIME#)

**Impact:** 
- Duplicate auto-healing logic running in parallel with `runtime.py` (same 5-min interval)
- Both trying to restart gluetun/arr-stack containers → race conditions
- Confusion about which one is actually responsible

**Evidence:** Line 8 of auto-heal.sh documents cron: `docker exec openclaw bash /home/tenazas/auto-heal.sh`

**Fix approach:** 
- Verify it's NOT in DSM Task Scheduler (check via SSH)
- If found, remove from cron
- Delete `auto-heal.sh` once confirmed replaced by runtime.py
- Add validation to runtime.py startup that auto-heal.sh is not running

## Known Bugs

### Orchestrator Model Selection Silent Fallback (High)

**Symptom:** When router V2 or cloud backends fail, model selection degrades to V1 silently with minimal logging.

**Files:** `orchestrator.py` lines 1544-1576, `model_router.py` lines 200-250

**Trigger:** 
- HomeLab (192.168.2.6:11434) offline
- OpenRouter API key missing or rate-limited
- Gemini API key missing

**Workaround:** Check `orchestrator.log` for router_version field; if V2 called but V1 returned, backend is offline.

**Root cause:** Exception handling at lines 1518-1532 catches all exceptions and falls back without distinguishing between "backend offline" vs "config error" vs "network error".

### Race Condition in Proposal Executor (High)

**Symptom:** Multiple approval requests for same proposal can execute simultaneously, applying changes twice.

**Files:** `proposal_executor.py` lines 520-600

**Trigger:** 
- Telegram message with approval arrives while first approval is still executing
- `pending_approvals.json` is read by both processes, neither sees the "in_progress" state

**Workaround:** Wait 30s between approvals; check execution logs for duplicates.

**Root cause:** No distributed lock mechanism for proposals. Uses simple file-based state without atomicity.

### State File Corruption on Crash (High)

**Symptom:** If `runtime.py` crashes mid-write to state.json, file becomes unparseable JSON. Next cron run fails to load state.

**Files:** `runtime.py` lines 88-91 (save_json helper), `shared_memory.py` lines 49-52

**Trigger:** 
- Process killed by watchdog at 120s timeout mid-write
- OOM killer terminates process
- Disk full during write
- SSD power loss

**Workaround:** Manually delete corrupted JSON file; runtime regenerates on next run (loses state history).

**Root cause:** No atomic writes, no temp file + rename pattern, no versioning to detect corruption.

### Hardcoded IPs and Ports (Medium)

**Issue:** Infrastructure endpoints hardcoded across 30+ modules, making migration/testing difficult.

**Files:** `action_validator.py:501`, `agent_lab.py:14`, `autofix_agent.py:48`, `auto_training_pipeline.py:35`, `continuous_learning_loop.py:15`, `deco_client.py:28`, `device_discovery.py:57-75`, etc.

**Impact:**
- Cannot easily run in different network without editing 30 files
- Testing environments conflict with production IPs
- No fallback if HomeLab IP changes (192.168.2.6 hardcoded as default everywhere)

**Evidence:** 
```python
OLLAMA_URL = "http://192.168.2.6:11434"  # appears in 20+ files
DECO_IP = os.environ.get("DECO_IP", "192.168.2.30")  # some use env vars, inconsistent
```

**Fix approach:**
- Centralize all infrastructure endpoints in `runtime_config.py`
- Load from `openclaw.json` with env var override
- Add validation on startup to verify connectivity to all endpoints
- Provide test fixtures with mocked endpoints

## Security Considerations

### docker.sock Mounted with No Fine-Grained Access Control (High)

**Risk:** Container `openclaw` has full Docker API access via mounted `/var/run/docker.sock`. If orchestrator.py is compromised, attacker can spawn privileged containers, mount host filesystem, etc.

**Files:** Docker-compose.yml volume mount, `docker_wrapper.py` (mitigation layer)

**Current mitigation:** `docker_wrapper.py` implements allowlist (restart, start, stop), blocklist (openclaw, watchtower), container name validation.

**Gaps:**
- Allowlist doesn't prevent `docker ps -a` (enumeration) → could probe for secrets in container env
- No rate limiting on docker API calls
- docker_wrapper.py can be imported/bypassed if someone imports Docker SDK directly
- Symlink attacks: if container_name resolves to "../../../evil", regex might pass

**Recommendations:**
- Use Docker socket API proxy (e.g., Traefik, docker-socket-proxy) with explicit allowlist at network level
- Add rate limiting: max 10 docker API calls per minute
- Audit all imports of `docker` or `subprocess` for Docker commands — enforce docker_wrapper.py usage
- Add tests for symlink/path traversal in container names
- Consider rootless Docker mode on NAS (DSM 7.3+ supports)

### API Keys in Environment Variables (Medium)

**Risk:** `OPENROUTER_API_KEY`, `GOOGLE_API_KEY`, `BINANCE_API_KEY`, `BINANCE_SECRET` stored in `docker-compose.yml` (checked in), visible in docker inspect.

**Files:** docker-compose.yml, runtime modules reading with `os.environ.get()`

**Current mitigation:** `.env` file not committed to git (per .gitignore).

**Gaps:**
- docker-compose.yml may have been committed with env vars inline (check git history)
- `docker inspect openclaw` reveals all env vars to any host user
- No secret rotation mechanism
- GOOGLE_API_KEY is write-only in CLAUDE.md (no details how it's used)

**Recommendations:**
- Move all secrets to encrypted `.env` file, load via `docker-compose` env_file
- Add validation on startup: if any API key missing, fail fast with clear error message
- Implement secret rotation for long-lived tokens (IOL refresh token, Telegram bot token)
- Use Docker secrets (Swarm mode) or external secret manager (HashiCorp Vault) for production

### Telegram Bot Token in openclaw.json (Medium)

**Risk:** Bot token stored in `state/openclaw.json` (likely world-readable on NAS).

**Files:** `state/openclaw.json` (gitignored but on filesystem)

**Impact:** Anyone with NAS filesystem access can impersonate Tenazas bot.

**Recommendations:**
- Move to Docker secret or encrypted storage
- Add audit logging of all Telegram API calls
- Rotate token if ever exposed
- Limit bot token scope (e.g., only allow sendMessage, not sendDocument)

## Performance Bottlenecks

### Orchestrator.py 2373 Lines Without Modularization (Medium)

**Problem:** Single 2373-line file contains model routing (V1/V2/V3), skill execution, orchestration, error handling, escalation logic, and 50+ helper functions.

**Files:** `orchestrator.py`

**Cause:** Refactoring interrupted; router V2/V3 patches kept growing same file instead of splitting.

**Improvement path:**
- Extract `model_router_v1.py`, `model_router_v2.py`, `model_router_v3.py` as separate modules
- Move skill execution to `skill_executor.py`
- Move escalation logic to `escalation_engine.py`
- Target: 600 lines per file (orchestrator becomes 800-line coordinator)

### Health Sweep ThreadPoolExecutor with MAX_WORKERS=25 (Medium)

**Problem:** `health_sweep.py` spawns 25 concurrent threads for probes. On NAS with limited CPU (Ryzen R1600), context-switching overhead dominates.

**Files:** `health_sweep.py` lines 950-1000

**Cause:** Original assumption: parallel probes faster. Reality: NAS CPU gets saturated, slowdown increases.

**Evidence:** health_sweep should take <30s but sometimes takes 60s+ (watchdog nearly triggers).

**Improvement path:**
- Reduce MAX_WORKERS to 5-8 (profile optimal value)
- Prioritize critical probes (Ollama, containers, VPN) to run first
- Use timeout=5s per probe instead of default (fail-fast)

### RAG Query with Rewrite + Hybrid Retrieval (Medium)

**Problem:** `query.py` runs sequential: query → rewrite → hybrid_retrieval → reranking. Total latency 8-12s.

**Files:** `skills/rag/query.py`

**Cause:** LLM query rewrite blocks on Ollama qwen2.5:7b (200-500ms), then retrieval does vector search + keyword search + reranking (another 5-8s).

**Improvement path:**
- Cache query rewrites (same query → same rewrite)
- Parallel vector + keyword search (current code is sequential)
- Skip rewrite if query already specific (keyword heuristic)
- Lazy reranking (return top-20 unranked, rerank only if confidence low)

## Fragile Areas

### Telegram Router Message Parsing (Medium)

**Files:** `telegram_router.py` lines 400-600

**Why fragile:**
- Regex-based skill detection with no dedup (same skill matched twice → executes twice)
- Conversation history limited to 10 turns, 10 min TTL — can drop context mid-conversation
- No rate limiting — spam messages from Telegram could DOS backend
- Handles "Markdown → plain text fallback" but no validation that fallback is safe

**Safe modification:**
- Add `_ensure_single_execution()` gate before skill dispatch
- Log all regex matches (not just final skill category)
- Add rate limiter: max 10 messages/min per chat_id
- Test all skill regexes with adversarial inputs (injection, long strings, Unicode)

### Proposal Executor with Auto-Apply Approval Logic (High)

**Files:** `proposal_executor.py` lines 100-200

**Why fragile:**
- Converts user Telegram "SI" to approval without confirmation UI
- If Telegram message is misread (regex false positive), wrong action could execute
- No timeout: if approval takes >5min, background metrics_server might re-apply same proposal

**Safe modification:**
- Use Telegram inline keyboards (buttons) instead of text parsing
- Implement `exec_approvals.py` state machine (pending→approved→executed→confirmed)
- Add 5-min expiration: proposals not executed within 5min auto-expire
- Log every approval with full context + user confirmation

### Autofix Agent Tier 1 Ops Auto-Execute (High)

**Files:** `autofix_agent.py` lines 100-150

**Why fragile:**
- Tier 1 (OPS) auto-executes without approval if enabled
- If rate limiting fails, could spam container restarts
- No pre-flight check: if restart changes state (e.g., cleanup task) could cause secondary failure

**Safe modification:**
- Default Tier 1 to OFF (kill switch: `state/AUTOFIX_DISABLED`)
- Require explicit approval from CLAUDE.md to enable
- Add backoff: exponential delay between retries (1s, 2s, 4s, 8s cap)
- Pre-flight: simulate action before executing (dry-run)

### Metrics Server IOL Portfolio Loop (Medium)

**Files:** `metrics_server.py` lines 300-400

**Why fragile:**
- Spawns `iol_cli.py` subprocess every 5 min
- If IOL token expires, subprocess hangs waiting for input
- No timeout on subprocess, metrics_server blocks until it completes

**Safe modification:**
- Add 60s timeout on IOL subprocess
- Detect expired token before subprocess call, skip if stale
- Move IOL to separate module with periodic refresh (not cron-based)
- Add heartbeat to detect hung subprocess

## Missing Critical Features

### No Distributed Lock for Concurrent Writes (Critical)

**Problem:** No mechanism to ensure only one process writes to state files at a time.

**Blocks:**
- Reliable approval queue (could process same approval twice)
- Metrics aggregation (could double-count events)
- Proposal execution (race condition: approve + execute could overlap)

**Recommendation:** Implement `FileLock` wrapper:
```python
class FileLock:
    def __init__(self, path, timeout=5):
        self.path = path  # path + .lock file
        self.timeout = timeout
    def __enter__(self): fcntl.flock(fd, LOCK_EX | LOCK_NB)
    def __exit__(self): fcntl.flock(fd, LOCK_UN)
```

### No Backup/Rollback for Critical State (Medium)

**Problem:** If `state/openclaw.json` or `learning/proposals.json` corrupts, no recovery.

**Blocks:** Automation confidence — users can't trust system won't lose state.

**Recommendation:**
- Daily backup: `cp state/*.json state/backups/$(date +%Y%m%d)/`
- Keep 30-day history
- Add rollback command: `tenazas --rollback-to YYYYMMDD`
- Add integrity check: `tenazas --check-state` (validate all JSON files)

### No Audit Trail for Policy-Critical Decisions (Medium)

**Problem:** If Tier 2 (CODE) autofix merges a bad change, only git log shows it. No decision trace.

**Blocks:** Root-cause analysis of why system auto-deployed bad code.

**Recommendation:**
- `decision_trace.py` already exists (lines 400+) but not integrated into autofix
- Add required fields: who (autofix_agent), what (git commit SHA), why (improvement_candidates reason), when (ts), outcome (success/rollback)
- Require approval review workflow: decision → approval → execution → validation → outcome

## Test Coverage Gaps

### 100+ Modules with No Tests

**What's not tested:**
- `action_budget.py` — spending limits enforcement (HIGH risk: could overspend)
- `adaptive_router.py` — learned routing preferences (MEDIUM: could route to wrong model)
- `agent_*.py` (6 multi-agent modules) — orchestration logic (HIGH: complex interactions)
- `auto_training_pipeline.py` — fine-tuning pipeline (HIGH: SSH, data transfer, GPU)
- `deco_client.py` — WiFi device info (MEDIUM: handles network state)
- `device_discovery.py` — network scan (MEDIUM: concurrent nmap)
- `experimental_runner.py` — A/B testing (MEDIUM: statistical significance)
- `proposal_executor.py` — execution of user-approved actions (CRITICAL: no tests)
- `telegram_router.py` — message parsing (CRITICAL: first line of defense)
- `temporal_guard.py` — time-based filters (MEDIUM: could block valid requests)
- `trading_*.py` (5 modules) — autonomous trading (CRITICAL: money-related)
- `unified_briefing.py` — daily briefing aggregation (MEDIUM: data aggregation)

**Priority order (fix first):**
1. `proposal_executor.py` — user-facing action execution
2. `telegram_router.py` — attack surface for input parsing
3. `action_budget.py` — spending limit enforcement
4. `auto_training_pipeline.py` — data/GPU intensive, failure impact
5. `agent_coordinator.py` — multi-agent conflict detection

**Files:** `tests/` directory has 62 test files but covers only 58 of 158 runtime modules.

## Scaling Limits

### State Files Scale Linearly with History (Medium)

**Current capacity:**
- `memory_log.json`: max 1000 entries
- `shared_memory.json`: max 100 entries per type
- `patterns.json`: unbounded, grows continuously
- `learning/proposals.json`: unbounded, grows continuously

**Limit:** At 1 entry per minute, `patterns.json` reaches 1MB in ~20 days, 100MB in ~1.8 years.

**Scaling path:**
- Cap history: keep only last N entries, archive old ones
- Use SQLite with indices for pattern queries (replace JSON)
- Compress archived history (gzip)

### Prometheus Metrics Server at 9105 (Low Capacity)

**Current:** prometheus_client Counter/Gauge with in-memory state, no persistence across restart.

**Limit:** If metrics_server crashes, metrics history lost (Prometheus scrapes but target unavailable).

**Scaling path:**
- Persist metrics to SQLite, reload on startup
- Add metric TTL: drop metrics older than 30 days

## Dependencies at Risk

### Deprecated Python 3.8 Code Pattern (Low)

**Risk:** Some modules use `dict` type hints (Python 3.9+) mixed with `Dict[]` imports (Python 3.8 compat).

**Files:** Various — inconsistent use of `dict[str, str]` vs `Dict[str, str]`

**Impact:** Potential compatibility issues if Dockerfile pinned to Python < 3.9.

**Migration plan:** Use `from __future__ import annotations` at top of all modules, modernize to `dict[str, str]` everywhere.

### whisper/torch Dependency for STT (Medium)

**Risk:** torch CPU-only in Dockerfile (5GB download, slow inference). If GPU inference needed, requires rebuild.

**Files:** Dockerfile, `telegram_router.py` (imports whisper)

**Current:** Whisper base model used for STT — works but slow on NAS CPU.

**Recommendation:**
- Profile STT latency (should be <5s for 30s audio)
- If over 10s, move to cloud (OpenAI Whisper API)
- Keep local as fallback only

---

*Concerns audit: 2026-04-28*
