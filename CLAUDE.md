# Contributor instructions — Tenazas / OpenClaw

Reviewed **2026-10-28**. Tenazas is a personal-assistant infrastructure project
running OpenClaw and a Python runtime on a Synology NAS. Its user-facing assistant
speaks concise Rioplatense Spanish; maintained project documentation is English.

## Current technical references

Read the [documentation index](docs/README.md), [setup](docs/setup.md),
[architecture](docs/architecture.md), [development](docs/development.md),
[infrastructure](INFRA.md), and [models](MODELS.md). These replace the previous
monolithic inventory in this file. Git history retains the original operational
notes; dated reports and `.planning/` retain historical decisions and results.

For routes, ports, models, flags, and API signatures, source code is authoritative.
Do not present historical test totals, host inventories, or evaluation scores as
current measurements.

## Repository rules

### Credentials and configuration

- Never hardcode API keys or log bot tokens. Read operator configuration or environment.
- Keep secrets, OAuth files, SSH keys, email configuration, and generated personal data
  out of commits. Review diffs before publishing.
- Do not add arbitrary gateway configuration keys without checking its strict schema.
  Keep Tenazas extensions separate where their reader supports it.
- `runtime_config.validate_config()` is not a replacement for gateway validation.
- Consult [schema notes](tools/SCHEMA_NOTES.md) before editing `openclaw.json`.

### Infrastructure and execution

- Use `docker_wrapper.py` for runtime Docker commands and validate names against
  its pattern and blocklist. Do not automatically restart `openclaw` or `watchtower`.
- Prefer `subprocess.run([...])`; do not introduce `shell=True` for runtime commands.
- Docker socket access remains privileged even when a wrapper is present.
- After intentional Compose changes, recreate affected services with Compose.
- Do not use `--no-cache` unless a dependency/version conflict has been confirmed.
- A failed healthcheck marks `unhealthy`; it does not itself restart the container.
- Accepted proposals may be executed by the proposal executor; review approval,
  sandbox, validation, rate limits, and rollback boundaries before enabling changes.
- Keep one scheduler per operational job. Follow the n8n cutover checklist rather
  than activating all workflows at once.

### Runtime code and skills

- Reuse `runtime_utils` helpers such as `load_json`, `save_json`, and `now`, and
  shared `runtime_config` settings instead of duplicating them.
- Use JSON log entries with appropriate handling of datetime values (`default=str`
  when needed). Bound logs and state growth.
- Read [INTERFACE_CONTRACT.md](workspace/runtime/INTERFACE_CONTRACT.md) before
  routing changes. Canonical labels live in `routes_contract.py`.
- Do not duplicate general intent classification inside skills.
- Review operational `SOUL.md` when skill triggers/routing change. New operational
  skills need their instruction file, scripts, and dispatcher registration where applicable.
- Query/ingestion changes require review of reindexing requirements. Do not run
  production ingestion automatically as a documentation check.
- Select tests appropriate to the change; some suites depend on Linux/container
  paths and live integrations. See [Development](docs/development.md).

## Operational lessons

### Gateway rejects configuration

Unknown root keys can cause `Invalid input` errors and startup failure. Inspect
logs and schema before restoring state. Known backup conventions include
`openclaw.json.last-good`, `.bak`, `.bak.1` through `.bak.4`, and `.clobbered.*`;
check that the selected backup exists and is valid for the installed version.

Operator recovery example, to use only during an intended recovery:

```bash
sudo docker exec openclaw cp /home/tenazas/.openclaw/openclaw.json.last-good \
  /home/tenazas/.openclaw/openclaw.json
sudo docker restart openclaw
```

### HomeLab inference unexpectedly uses CPU

Inspect `ollama ps`, GPU availability, and `OLLAMA_LLM_LIBRARY`. Historical
misconfiguration used the literal `cuda`, although installed library directories
had versioned names. Verify the actual installation before changing environment
settings and restarting the relevant process.

### Qwen custom templates

`qwen3-fast:4b` and `qwen3-fast:8b` are custom names used by `ROLE_MAP`. Historical
Modelfiles lived outside this repository. Standard `qwen3:*` models need not have
the same thinking/template behavior. See [Models](MODELS.md).

### Repeated startup or missing background services

Inspect gateway logs and process-specific logs. Repeated job-runner startup messages
may be a symptom of container restarts rather than a runner defect. Gateway
health alone does not verify every exporter or watcher.

### Training environment notes

Historical HomeLab notes used `C:\Python314\python.exe`, llama.cpp `b4924`, and
GGUF `q8_0`. These are environment-specific observations, not repository-wide
requirements. Review workbench compatibility and installed tooling before training.

## GSD workflow availability

Codex prompts live in `.codex/prompts/`. They refer to an externally installed
`.claude/get-shit-done` engine and workflows, absent from this checkout. Check
local/global installation before invoking them; do not claim a workflow ran when
its engine was unavailable. Keep execution records separate from completed
historical milestone status.

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (90-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk vitest run          # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%)
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->
