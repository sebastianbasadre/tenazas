# n8n Runtime

This directory stores `n8n` persistent data after `docker compose --profile migration up -d n8n`.

Phase 1 usage:

- Use `n8n` only as scheduler/orchestrator
- Call wrapper scripts from `/workspace/scripts`
- Do not move Telegram routing or orchestrator logic here yet
- Keep `n8n` disabled in production by default
- Activate workflows gradually, not in one publish batch

First cutover jobs:

- health sweep
- gpu keepalive
- autofix pass
- proposal executor
- portfolio reconciler
- IOL token refresh
- calendar/email pollers
- daily finance summary
