# DSM Task Checklist

## Purpose

This is the operator checklist for Synology DSM Task Scheduler during the `n8n` migration.

Use this together with:

- `docs/n8n-cutover-runbook.md`
- `docs/n8n-phase1-jobs.md`

## Shared DSM Settings

Recommended task type:

- `Triggered Task`
- `User-defined script`
- run as the same user that already owns the current Docker tasks

Shared environment during the first migration pass:

```env
OPENCLAW_JOB_MODE=direct
OPENCLAW_JOB_RUNNER_URL=http://127.0.0.1:18891
OPENCLAW_JOB_RUNNER_TOKEN=<same random value as TENAZAS_JOB_RUNNER_TOKEN in .env>
```

When you temporarily want DSM to use the same authenticated endpoint as `n8n`:

```env
OPENCLAW_JOB_MODE=runner
OPENCLAW_JOB_RUNNER_URL=http://127.0.0.1:18891
OPENCLAW_JOB_RUNNER_TOKEN=<same random value as TENAZAS_JOB_RUNNER_TOKEN in .env>
```

Base working directory:

```bash
/volume1/docker/openclaw
```

## Commands

### finance_daily

Frequency:

- daily at 11:00

Command:

```bash
cd /volume1/docker/openclaw && /bin/bash scripts/finance_daily_cron.sh
```

Cutover state:

- keep enabled until `n8n` workflow is manually executed and verified
- disable DSM task after `n8n` owns the schedule

### notification_calendar

Frequency:

- every 15 minutes

Command:

```bash
cd /volume1/docker/openclaw && /bin/bash scripts/notification_calendar_cron.sh
```

Cutover state:

- keep enabled until `n8n` workflow is verified
- watch for duplicate calendar alerts

### notification_email

Frequency:

- every 10 minutes

Command:

```bash
cd /volume1/docker/openclaw && /bin/bash scripts/notification_email_cron.sh
```

Cutover state:

- verify local Ollama classification still works
- disable DSM task after `n8n` is stable

### portfolio_reconciler

Frequency:

- every 10 minutes

Command:

```bash
cd /volume1/docker/openclaw && /bin/bash scripts/portfolio_reconciler_cron.sh
```

Before giving ownership to `n8n`:

```env
ENABLE_PORTFOLIO_RECONCILER_WATCH=false
```

Validation:

```bash
docker logs openclaw --tail 100 | grep "Portfolio reconciler watch deshabilitado"
```

### iol_refresh

Frequency:

- every 10 minutes

Command:

```bash
cd /volume1/docker/openclaw && /bin/bash scripts/iol_refresh_cron.sh
```

Before giving ownership to `n8n`:

```env
ENABLE_IOL_REFRESH_LOOP=false
ENABLE_STARTUP_IOL_REFRESH=true
```

Validation:

```bash
docker logs openclaw --tail 100 | grep "IOL auto-refresh loop deshabilitado"
```

### gpu_keepalive

Frequency:

- every 4 minutes

Command:

```bash
cd /volume1/docker/openclaw && /bin/bash scripts/gpu_keepalive_cron.sh
```

Cutover state:

- lower risk than self-heal jobs
- migrate after low-risk notification/finance jobs are stable

### proposal_executor

Frequency:

- every 15 minutes

Command:

```bash
cd /volume1/docker/openclaw && /bin/bash scripts/proposal_executor_cron.sh
```

Cutover state:

- migrate after low-risk jobs
- keep close watch on logs and side effects

### health_sweep

Frequency:

- hourly

Command:

```bash
cd /volume1/docker/openclaw && /bin/bash scripts/health_sweep_cron.sh
```

Notes:

- wrapper includes its own one-hour minimum interval gate
- keep this late in the migration order

### autofix_agent

Frequency:

- every 15 minutes

Command:

```bash
cd /volume1/docker/openclaw && /bin/bash scripts/autofix_agent_cron.sh
```

Notes:

- keep this last
- only migrate after `health_sweep` and alerting paths are proven stable

## Activation Order

Use this exact order:

1. `finance_daily`
2. `notification_calendar`
3. `notification_email`
4. `portfolio_reconciler`
5. `iol_refresh`
6. `gpu_keepalive`
7. `proposal_executor`
8. `health_sweep`
9. `autofix_agent`

## Disable Rules

For each job:

1. execute the workflow manually in `n8n`
2. verify logs and Telegram behavior
3. if clean, disable the matching DSM schedule
4. never leave both DSM and `n8n` scheduling the same job longer than the observation window

## Rollback Rules

If a migrated workflow fails:

1. disable that workflow in `n8n`
2. re-enable the DSM task
3. set DSM wrapper back to `OPENCLAW_JOB_MODE=direct`
4. if affected, restore:
   - `ENABLE_PORTFOLIO_RECONCILER_WATCH=true`
   - `ENABLE_IOL_REFRESH_LOOP=true`

## Completion

DSM migration is complete when:

- the low-risk tasks are disabled in DSM and enabled in `n8n`
- stateful in-container loops are disabled
- only the intended fallback tasks remain in DSM
- no duplicate job execution is observed
