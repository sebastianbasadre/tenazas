# Phase 2: Config Centralization - Context

**Gathered:** 2026-04-28
**Status:** Ready for planning
**Mode:** Auto-generated (infrastructure phase — discuss skipped)

<domain>
## Phase Boundary

Replace all 268 hardcoded IP/port occurrences across 65 files with imports from a single `runtime_config.py` module. The config module loads values from `openclaw.json` with env var overrides for each endpoint. Tests can override any config value by patching `runtime_config` in one place.

</domain>

<decisions>
## Implementation Decisions

### Claude's Discretion
All implementation choices are at Claude's discretion — pure infrastructure phase. Key constraints:
- stdlib only (no new dependencies)
- Config must load from `state/openclaw.json` (the existing config file inside the container at `/home/tenazas/.openclaw/openclaw.json`)
- Env var overrides with `TENAZAS_` prefix (e.g., `TENAZAS_OLLAMA_GPU_URL`)
- All 700+ existing tests must pass after migration
- Use the `runtime_utils.py` module created in Phase 1 for any JSON loading

</decisions>

<code_context>
## Existing Code Insights

### Reusable Assets
- `workspace/runtime/runtime_utils.py` — load_json() for reading config files
- `workspace/runtime/runtime_config.py` — may already exist with some config (check first)
- `state/openclaw.json` — existing config file with bot tokens, API settings

### Established Patterns
- Hardcoded pattern: `OLLAMA_URL = "http://192.168.2.6:11434"` (in 20+ files)
- Some modules already use `os.environ.get("KEY", "default")` (inconsistent)
- IPs: 192.168.2.2 (NAS), 192.168.2.6 (HomeLab), 192.168.2.11 (Hue), 192.168.2.46 (Samsung)

### Integration Points
- All 65 files with hardcoded IPs will import from runtime_config
- Tests that patch individual module constants will patch runtime_config instead
- Docker container has env vars in docker-compose.yml (GOOGLE_API_KEY, OPENROUTER_API_KEY, etc.)

</code_context>

<specifics>
## Specific Ideas

No specific requirements — infrastructure phase. Key endpoints to centralize:
- OLLAMA_GPU_URL (192.168.2.6:11434)
- OLLAMA_NAS_URL (127.0.0.1:11434)
- CHROMADB_URL (192.168.2.2:8100)
- HUE_BRIDGE_IP (192.168.2.11)
- SAMSUNG_M8_IP (192.168.2.46)
- DECO_IP (192.168.2.30)
- HOMELAB_IP (192.168.2.6)
- NAS_IP (192.168.2.2)
- PROMETHEUS_URL, GRAFANA_URL, etc.

</specifics>

<deferred>
## Deferred Ideas

- Config validation on startup (v2 STRC-01)
- Module dependency graph enforcement (v2 STRC-02)

</deferred>
