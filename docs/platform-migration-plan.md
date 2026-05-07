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

## Two-Week Cutover

### Week 1

1. Start `n8n` from `docker-compose.yml`.
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

1. Disable internal loops with env flags:
   - `ENABLE_PORTFOLIO_RECONCILER_WATCH=false`
   - `ENABLE_IOL_REFRESH_LOOP=false`
   - optionally `ENABLE_STARTUP_IOL_REFRESH=false` after cron is stable
2. Remove DSM schedules for migrated jobs.
3. Let `n8n` own retries, timeout handling, and notification fan-out.
4. Re-audit alerts and cut any remaining host-side `docker exec` cron entries.

## Job Map

| Domain | Current source | New runner | Schedule |
|---|---|---|---|
| GPU keepalive | DSM | `scripts/gpu_keepalive_cron.sh` | every 4 min |
| Health sweep | DSM | `scripts/health_sweep_cron.sh` | every 5 min |
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
3. Restore the previous DSM task to the corresponding wrapper.

## Not In Scope Yet

- Replacing `telegram_router.py`
- Rewriting orchestrator/skills into LibreChat
- Migrating smart-home logic to Home Assistant
- Changing RAG or fine-tuning pipelines
