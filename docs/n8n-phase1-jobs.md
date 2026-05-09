# n8n Phase 1 Jobs

## Purpose

Phase 1 moves operational scheduling out of the `openclaw` container without rewriting the runtime.

`n8n` should call host-side wrapper scripts, not Python modules directly.

Current posture:

- Production path: DSM + wrappers
- `n8n` path: prepared, manual, disabled by default
- Activation command: `docker compose --profile migration up -d n8n`

## Wrappers

| Job | Wrapper | Timeout |
|---|---|---|
| Health sweep | `/workspace/scripts/health_sweep_cron.sh` | 240s |
| GPU keepalive | `/workspace/scripts/gpu_keepalive_cron.sh` | 90s |
| Autofix agent | `/workspace/scripts/autofix_agent_cron.sh` | 1200s |
| Proposal executor | `/workspace/scripts/proposal_executor_cron.sh` | add timeout before production use |
| Portfolio reconciler | `/workspace/scripts/portfolio_reconciler_cron.sh` | 180s |
| IOL refresh | `/workspace/scripts/iol_refresh_cron.sh` | 120s |
| Calendar poller | `/workspace/scripts/notification_calendar_cron.sh` | 90s |
| Email poller | `/workspace/scripts/notification_email_cron.sh` | 180s |
| Daily finance | `/workspace/scripts/finance_daily_cron.sh` | 180s |

## n8n Pattern

Each workflow should follow the same structure:

1. `Schedule Trigger`
2. `Execute Command`
3. optional `IF` on exit log content
4. optional Telegram/Email notification

Command example:

```bash
/workspace/scripts/health_sweep_cron.sh
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

After each workflow import:

```bash
docker exec openclaw python3 /home/tenazas/runtime/health_sweep.py --no-telegram
docker exec openclaw python3 /home/tenazas/runtime/portfolio_reconciler.py --json
docker exec openclaw python3 /home/tenazas/tools/iol_refresh.py --status
```

Do not publish all workflows in one batch on the NAS again. Activate low-risk workflows first:

1. `finance_daily`
2. `notification_calendar`
3. `notification_email`
4. `portfolio_reconciler`
5. `iol_refresh`

Keep `health_sweep`, `autofix_agent`, and any self-heal workflow for the final step.
