# n8n Phase 1 Jobs

## Purpose

Phase 1 moves operational scheduling out of the `openclaw` container without rewriting the runtime.

`n8n` should call the authenticated OpenClaw job runner over HTTP.
DSM keeps using host-side wrappers during Phase A, but those wrappers can now target the same HTTP runner.

Current posture:

- Production path: DSM + wrappers
- `n8n` path: prepared, manual, disabled by default
- Activation command: `docker compose --profile migration up -d n8n`
- Full operator sequence: `docs/n8n-cutover-runbook.md`

## Job Runner Allowlist

These are the allowed remote jobs exposed by `workspace/runtime/job_runner.py`.

| Job | HTTP path | Timeout |
|---|---|---|
| Health sweep | `/run/health_sweep` | 240s |
| GPU keepalive | `/run/gpu_keepalive` | 90s |
| Autofix agent | `/run/autofix_agent` | 1200s |
| Proposal executor | `/run/proposal_executor` | 300s |
| Portfolio reconciler | `/run/portfolio_reconciler` | 180s |
| IOL refresh | `/run/iol_refresh` | 120s |
| Calendar poller | `/run/notification_calendar` | 90s |
| Email poller | `/run/notification_email` | 180s |
| Daily finance | `/run/finance_daily` | 180s |

## n8n Pattern

Each workflow should follow the same structure:

1. `Schedule Trigger`
2. `HTTP Request`
3. optional `IF` on JSON `ok/exit_code`
4. optional Telegram/Email notification

Request example:

```http
POST {{$env.TENAZAS_JOB_RUNNER_BASE_URL}}/run/health_sweep
X-Tenazas-Job-Token: {{$env.TENAZAS_JOB_RUNNER_TOKEN}}
```

Container default:

```env
TENAZAS_JOB_RUNNER_BASE_URL=http://host.docker.internal:18891
```

## Recommended Schedules

- Health sweep: `0 * * * *`
- GPU keepalive: `*/4 * * * *`
- Autofix agent: `*/15 * * * *`
- Proposal executor: `*/15 * * * *`
- Portfolio reconciler: `*/10 * * * *`
- IOL refresh: `*/10 * * * *`
- Calendar poller: `*/15 * * * *`
- Email poller: `*/10 * * * *`
- Daily finance: `0 11 * * *`

## OpenClaw Flags After Cutover

Set these in `.env` or Compose environment when `n8n` owns the jobs:

```env
ENABLE_PORTFOLIO_RECONCILER_WATCH=false
ENABLE_IOL_REFRESH_LOOP=false
ENABLE_STARTUP_IOL_REFRESH=true
```

Keep `ENABLE_STARTUP_IOL_REFRESH=true` for the first cutover so the token still heals on container restart.

## Validation

Check the runner first:

```bash
curl -sf http://127.0.0.1:18891/health
curl -sf -X POST \
  -H "X-Tenazas-Job-Token: $TENAZAS_JOB_RUNNER_TOKEN" \
  http://127.0.0.1:18891/run/finance_daily
```

After each workflow import:

```bash
docker exec openclaw python3 /home/tenazas/runtime/health_sweep.py --no-telegram
docker exec openclaw python3 /home/tenazas/runtime/portfolio_reconciler.py --json
docker exec openclaw python3 /home/tenazas/tools/iol_refresh.py --status
```

For DSM wrapper parity, the host-side scripts now support two modes:

```env
OPENCLAW_JOB_MODE=direct
OPENCLAW_JOB_MODE=runner
OPENCLAW_JOB_RUNNER_URL=http://127.0.0.1:18891
OPENCLAW_JOB_RUNNER_TOKEN=<same random value as TENAZAS_JOB_RUNNER_TOKEN in .env>
```

Use `direct` during initial rollout, then switch DSM tasks to `runner` job-by-job when you want both schedulers using the same authenticated endpoint.

Do not publish all workflows in one batch on the NAS again. Activate low-risk workflows first:

1. `finance_daily`
2. `notification_calendar`
3. `notification_email`
4. `portfolio_reconciler`
5. `iol_refresh`

Keep `health_sweep`, `autofix_agent`, and any self-heal workflow for the final step.

## Cutover Notes

When a job is moved to `n8n`, disable the matching in-container loop/watch so it only has one scheduler.

- `portfolio_reconciler` → `ENABLE_PORTFOLIO_RECONCILER_WATCH=false`
- `iol_refresh` → `ENABLE_IOL_REFRESH_LOOP=false`
- keep `ENABLE_STARTUP_IOL_REFRESH=true` during the first migration pass
