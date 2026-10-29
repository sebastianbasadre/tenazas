# n8n

This directory contains workflows and an import script. `data/` stores local
persistent state and is excluded from Git.

Activate the service explicitly:

```bash
docker compose --profile migration up -d n8n
# After preflight and backup:
docker exec n8n /workspace/import-workflows.sh
```

Compose declares version `2.19.5`. n8n acts as a scheduler/orchestrator calling
Tenazas's HTTP runner with `X-Tenazas-Job-Token`; it does not replace Telegram routing.

The runner defaults to `127.0.0.1`. n8n's target,
`http://host.docker.internal:18891`, requires a host bind reachable from the Docker
bridge. Resolve connectivity and access restrictions before activating schedules.

Importing workflows does not complete a cutover. Verify each workflow, activation
state, notifications, and previous scheduler to avoid duplicate executions.

- [Cutover runbook](../docs/n8n-cutover-runbook.md)
- [Allowlist and timeouts](../docs/n8n-phase1-jobs.md)
- [DSM checklist](../docs/dsm-task-checklist.md)
- [Migration plan](../docs/platform-migration-plan.md)

Reviewed **2026-10-28** against code; this does not confirm active NAS workflows.
