# Platform Migration Plan

## Goal

Reduce `openclaw` to:

- Telegram ingress
- orchestrator
- skills
- RAG

Move scheduling, recurring ops, and host-level recovery out of the monolith.

## Target Split

- `openclaw`: conversation, orchestration, skills, RAG
- `n8n`: scheduling, retries, alert routing, one-shot operational jobs
- `HomeLab Ollama`: model serving only
- `Prometheus/Grafana`: observability only
- `DSM Task Scheduler`: temporary fallback only, then remove

## Execution State

- Phase A is the active production path.
- Phase B remains prepared in-repo but disabled by default on the NAS.
- `n8n` stays behind a Compose profile until the host proves stable again.
- Exact cutover steps live in `docs/n8n-cutover-runbook.md`.

## Two-Week Cutover

Task-by-task DSM commands and toggles live in `docs/dsm-task-checklist.md`.

### Week 1

1. Keep DSM + wrappers as the production scheduler.
2. Move existing DSM jobs to one-shot wrappers:
   - `health_sweep_cron.sh`
   - `gpu_keepalive_cron.sh`
   - `autofix_agent_cron.sh`
   - `proposal_executor_cron.sh`
3. Add recurring one-shot jobs for:
   - `portfolio_reconciler_cron.sh`
   - `iol_refresh_cron.sh`
   - `notification_calendar_cron.sh`
   - `notification_email_cron.sh`
   - `finance_daily_cron.sh`
4. Keep monolith loops enabled until the same schedules are proven in `n8n`.

### Week 2

1. Start `n8n` explicitly with the migration profile:
   - `docker compose --profile migration up -d n8n`
2. Import and validate workflows one by one before publishing any schedule.
3. Disable internal loops with env flags:
   - `ENABLE_PORTFOLIO_RECONCILER_WATCH=false`
   - `ENABLE_IOL_REFRESH_LOOP=false`
   - optionally `ENABLE_STARTUP_IOL_REFRESH=false` after cron is stable
4. Remove DSM schedules only after the matching `n8n` workflow is stable.
5. Let `n8n` own retries, timeout handling, and notification fan-out.
6. Re-audit alerts and cut any remaining host-side `docker exec` cron entries.

## Job Map

| Domain | Current source | New runner | Schedule |
|---|---|---|---|
| GPU keepalive | DSM | `scripts/gpu_keepalive_cron.sh` | every 4 min |
| Health sweep | DSM | `scripts/health_sweep_cron.sh` | hourly |
| Autofix pass | DSM | `scripts/autofix_agent_cron.sh` | every 10-15 min |
| Proposal executor | DSM | `scripts/proposal_executor_cron.sh` | every 10-15 min |
| Portfolio reconciler | startup watch loop | `scripts/portfolio_reconciler_cron.sh` | every 10 min |
| IOL token refresh | startup loop | `scripts/iol_refresh_cron.sh` | every 10 min |
| Calendar poller | ad hoc | `scripts/notification_calendar_cron.sh` | every 15 min |
| Email poller | DSM/container direct | `scripts/notification_email_cron.sh` | every 10 min |
| Daily finance summary | ad hoc | `scripts/finance_daily_cron.sh` | daily |

## Cutover Rules

- Every recurring job must be one-shot.
- Every recurring job must have a host-side timeout.
- Every recurring job must log to its own file under `/volume1/docker/openclaw/logs`.
- `openclaw` startup should not own operational loops once `n8n` takes over.
- If a job can notify Telegram on its own, keep that behavior during migration.

## Rollback

If `n8n` is unstable:

1. Re-enable env flags in `docker-compose.yml` or `.env`.
2. Re-run `docker compose up -d openclaw`.
3. Stop `n8n` with `docker compose --profile migration stop n8n`.
4. Restore the previous DSM task to the corresponding wrapper.

## Not In Scope Yet

- Replacing `telegram_router.py`
- Rewriting orchestrator/skills into LibreChat
- Migrating smart-home logic to Home Assistant
- Changing RAG or fine-tuning pipelines
