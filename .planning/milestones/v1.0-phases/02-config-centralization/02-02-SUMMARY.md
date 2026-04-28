---
phase: 02-config-centralization
plan: 02
subsystem: infra
tags: [config, centralization, runtime_config, ip-migration, constants]

requires:
  - phase: 02-config-centralization/01
    provides: runtime_config.py with all infrastructure endpoint constants
provides:
  - All 60+ runtime modules import endpoints from runtime_config.py
  - Zero bare IP literals in functional code
  - Single-point override via env var or unittest.mock.patch
affects: [03-orchestrator-simplification, testing, deployment]

tech-stack:
  added: []
  patterns: ["try/except import pattern for runtime_config constants", "alias pattern for backward-compatible local names"]

key-files:
  created: []
  modified:
    - workspace/runtime/model_router.py
    - workspace/runtime/executors.py
    - workspace/runtime/health_sweep.py
    - workspace/runtime/orchestrator.py
    - workspace/runtime/metrics_server.py
    - workspace/runtime/telegram_router.py
    - workspace/runtime/device_discovery.py
    - workspace/runtime/knowledge_graph.py
    - workspace/runtime/world_model.py
    - workspace/runtime/devices/*.py (9 device modules)
    - workspace/runtime/tool_tuning/*.py (4 files)

key-decisions:
  - "Device inventory IPs (cameras, phones, tablets) left as literals -- they are data, not config"
  - "ezviz_cameras.py unchanged -- camera IPs are per-device inventory, not overridable infrastructure"
  - "infra_diagnostics.py IPs in message strings left as-is -- user-facing diagnostic text"
  - "Docstring/comment IPs preserved -- documentation describing what system connects to"
  - "Used alias pattern (LOCAL_NAME = IMPORTED_CONSTANT) to minimize diff in files with established local names"

patterns-established:
  - "Import from runtime_config: try/except with relative then absolute import path"
  - "Name collision handling: import with alias (GLUETUN_PROXY as GLUETUN_PROXY_CFG)"
  - "Device inventory data stays literal: unique per-device IPs not in runtime_config"

requirements-completed: [CONF-02, CONF-04]

duration: 18min
completed: 2026-04-28
---

# Phase 2 Plan 2: IP Migration Summary

**Migrated 60+ runtime modules from hardcoded IPs to centralized runtime_config imports; zero bare IP literals remain in functional code**

## Performance

- **Duration:** 18 min
- **Started:** 2026-04-28T20:50:05Z
- **Completed:** 2026-04-28T21:08:17Z
- **Tasks:** 3 (2 implementation + 1 verification)
- **Files modified:** 60

## Accomplishments
- Migrated 42 service/infrastructure modules to import from runtime_config
- Migrated 18 device modules, data registries, and tool_tuning files
- Grep gate passes: zero bare IPs in functional code outside documented exceptions
- All existing tests maintain their pre-migration pass/fail status

## Task Commits

Each task was committed atomically:

1. **Task 1: Migrate service/infra modules (42 files)** - `2b662fd` (refactor)
2. **Task 2: Migrate device modules and data registries (18 files)** - `07ea67e` (refactor)
3. **Task 3: Final verification** - verification only, no code changes

## Files Created/Modified

**Service/Infra modules (42 files):**
- `workspace/runtime/model_router.py` - BACKENDS dict uses OLLAMA_GPU_URL, OLLAMA_GPU_TAGS_URL, OLLAMA_NAS_URL
- `workspace/runtime/executors.py` - Uses OLLAMA_GPU_TAGS_URL, GLUETUN_PROXY, LAN_SUBNET, OLLAMA_NAS_URL
- `workspace/runtime/health_sweep.py` - 9 inline URLs replaced with CHROMADB_URL, PROMETHEUS_URL, GRAFANA_URL, GLUETUN_PROXY, HOMELAB_IP
- `workspace/runtime/orchestrator.py` - MULTITOOL_OLLAMA_URL and HUE_BRIDGE_IP migrated
- `workspace/runtime/metrics_server.py` - OLLAMA_GPU_URL and OLLAMA_MODELS_URL migrated
- `workspace/runtime/telegram_router.py` - OLLAMA_GPU_URL and OLLAMA_NAS_URL migrated
- Plus 36 more files with simple OLLAMA_URL/GLUETUN_PROXY/SEARXNG_URL replacements

**Device modules (9 files):**
- `workspace/runtime/devices/hue_lights.py` - BRIDGE_IP = HUE_BRIDGE_IP
- `workspace/runtime/devices/samsung_tv.py` - DEVICES dict uses SAMSUNG_M8_IP/MAC, SAMSUNG_Q70_IP/MAC
- `workspace/runtime/devices/google_cast.py` - SPEAKERS uses NEST_MINI_IP, HOME_MINI_IP
- Plus 6 more device files (marantz, sync box, nest doorbell, shield, xiaomi, xiaomi_token)

**Data registries (5 files):**
- `workspace/runtime/device_discovery.py` - SUBNET and 15 infra IPs in KNOWN_DEVICES migrated
- `workspace/runtime/knowledge_graph.py` - TOPOLOGY dict uses imported constants
- `workspace/runtime/world_model.py` - KNOWN_DEVICES, KNOWN_SERVICES, get_topology all migrated
- `workspace/runtime/runbook_generator.py` - Runbook command templates use f-strings with constants
- `workspace/runtime/action_registry.py` - allowed_targets uses LAN_SUBNET

**Tool tuning (4 files):**
- `workspace/runtime/tool_tuning/eval_runner.py` - OLLAMA_GPU_URL imported
- `workspace/runtime/tool_tuning/router.py` - DEFAULT_OLLAMA_HOST uses OLLAMA_GPU_URL
- `workspace/runtime/tool_tuning/router_runtime.py` - HUE_BRIDGE and nmap subnet migrated
- `workspace/runtime/tool_tuning/test_model_v3.py` - --host default uses OLLAMA_GPU_URL

## Decisions Made

1. **Device inventory IPs left as literals** - Camera IPs (ezviz), phone IPs, tablet IPs, printer IPs, Deco extender IPs in device_discovery.py KNOWN_DEVICES are per-device data, not overridable infrastructure config
2. **Diagnostic message IPs preserved** - infra_diagnostics.py uses IPs in user-facing text (e.g., "Verificar Ollama en notebook (192.168.2.6)") -- these are documentation, not connection config
3. **Alias pattern for backward compatibility** - When local variable names differed from runtime_config names, added `LOCAL_NAME = IMPORTED_CONSTANT` to minimize downstream diff
4. **samsung_pair.py unchanged** - CLI usage examples in print statements are documentation
5. **build_multitool_dataset.py line 224 unchanged** - Training data example string, not functional code

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed files with pre-existing runtime_config imports**
- **Found during:** Task 1
- **Issue:** Several files (finance_daily, continuous_learning_loop, portfolio_reconciler, health_sweep) already imported from runtime_config for telegram functions. Migration script added a second import block instead of extending the existing one.
- **Fix:** Extended existing try/except import blocks to include new constants alongside existing telegram function imports
- **Files modified:** finance_daily.py, continuous_learning_loop.py, portfolio_reconciler.py, health_sweep.py, telegram_router.py
- **Committed in:** 2b662fd (Task 1 commit)

**2. [Rule 3 - Blocking] telegram_router.py OLLAMA_NAS_URL also hardcoded**
- **Found during:** Task 1
- **Issue:** telegram_router.py had `OLLAMA_NAS_URL = "http://127.0.0.1:11434"` not mentioned in plan
- **Fix:** Imported OLLAMA_NAS_URL from runtime_config with alias to avoid shadowing
- **Files modified:** workspace/runtime/telegram_router.py
- **Committed in:** 2b662fd (Task 1 commit)

---

**Total deviations:** 2 auto-fixed (both Rule 3 - blocking)
**Impact on plan:** Both fixes necessary for correctness. No scope creep.

## Issues Encountered

- **Pre-existing test failures (16 suites):** Tests that fail are due to pre-existing bugs unrelated to the migration:
  - `model_router.py` guardrails: `now = time.monotonic()` shadows imported `now()` function (TypeError: 'float' object is not callable)
  - `docker_wrapper.py`: datetime JSON serialization issue
  - Several other suites with similar pre-existing issues
  - All tests that exercise migrated code (world_model, planner, safety_guard, shared_memory, orchestrator core) pass correctly
  - 1430 tests pass, 16 suites have pre-existing failures

## User Setup Required

None - no external service configuration required. The migration preserves all existing default values. To override, set TENAZAS_* environment variables (documented in runtime_config.py).

## Next Phase Readiness
- Config centralization phase is complete
- All runtime modules now import from runtime_config.py
- Tests can override any endpoint with `unittest.mock.patch("runtime_config.CONSTANT")`
- Ready for Phase 3 (Orchestrator Simplification)

---
*Phase: 02-config-centralization*
*Completed: 2026-04-28*
