# n8n Cutover Runbook

## Goal

Move scheduled operational jobs from DSM and in-container loops to `n8n`, while keeping a clean rollback path.

Documentation reviewed against repository code on **2026-10-28**. This runbook
does not confirm the current NAS deployment or workflow activation state:

- `openclaw` exposes `job_runner.py` on port `18891`
- `n8n` is behind the Compose profile `migration`
- DSM wrappers support both `direct` and `runner` modes
- DSM task-by-task commands live in `docs/dsm-task-checklist.md`

## Preflight

Confirm the container and runner configuration:

```bash
docker compose config --services
docker compose config --quiet
curl -sf http://127.0.0.1:18891/health
```

Expected runner health keys:

- `ok`
- `service=job_runner`
- `jobs`

## Runner connectivity from n8n

The runner binds to `127.0.0.1:18891` by default. n8n is on a Docker bridge;
`host.docker.internal` resolves the host gateway and cannot reach a listener bound
only to host loopback. A successful host-local health check does not prove n8n connectivity.

Before cutover, set `TENAZAS_JOB_RUNNER_HOST` in `.env` to a host address reachable
from the Docker bridge (or `0.0.0.0` only with host firewall rules restricting access
to the intended clients). Keep a unique `TENAZAS_JOB_RUNNER_TOKEN`; recreate
`openclaw` after changing its environment. Do not expose the runner publicly.

After starting n8n, verify the actual container path without running a job:

```bash
docker exec n8n node -e 'fetch(process.env.TENAZAS_JOB_RUNNER_BASE_URL + "/health").then(async r => { console.log(await r.text()); process.exit(r.ok ? 0 : 1); }).catch(e => { console.error(e.message); process.exit(1); })'
```

`GET /health` is unauthenticated. It verifies reachability and the allowlist;
authenticated POST and job side effects require a separate deliberate check.

## Bring Up n8n

Start only the migration service:

```bash
docker compose --profile migration up -d n8n
docker ps --format "table {{.Names}}\t{{.Status}}" | grep -E "openclaw|n8n"
```

Import the repository workflows:

```bash
docker exec n8n /workspace/import-workflows.sh
```

Optional verification:

```bash
docker exec n8n n8n list:workflow
```

## Validate the HTTP Path

The following POST runs a real finance job and may notify Telegram. Execute it
only as part of the planned cutover, after checking its effects and disabling any
conflicting schedule:

```bash
export TENAZAS_JOB_RUNNER_TOKEN="$(grep '^TENAZAS_JOB_RUNNER_TOKEN=' .env | cut -d= -f2-)"

curl -fsS -X POST \
  -H "X-Tenazas-Job-Token: ${TENAZAS_JOB_RUNNER_TOKEN}" \
  http://127.0.0.1:18891/run/finance_daily
```

Then verify one operational path and one stateful path directly:

```bash
docker exec openclaw python3 /home/tenazas/runtime/finance_daily.py --test
docker exec openclaw python3 /home/tenazas/runtime/portfolio_reconciler.py --json
docker exec openclaw python3 /home/tenazas/tools/iol_refresh.py --status
```

## Activation Order

Publish and enable workflows in this order:

1. `finance_daily`
2. `notification_calendar`
3. `notification_email`
4. `portfolio_reconciler`
5. `iol_refresh`
6. `gpu_keepalive`
7. `proposal_executor`
8. `health_sweep`
9. `autofix_agent`

Do not enable all workflows at once.

## DSM Task Settings

Keep DSM as production fallback during the first pass.

Initial DSM wrapper environment:

```env
OPENCLAW_JOB_MODE=direct
OPENCLAW_JOB_RUNNER_URL=http://127.0.0.1:18891
OPENCLAW_JOB_RUNNER_TOKEN=<same random value as TENAZAS_JOB_RUNNER_TOKEN in .env>
```

After a workflow is proven in `n8n`, either:

- disable the DSM task entirely, or
- switch that DSM task to `OPENCLAW_JOB_MODE=runner` temporarily if you want DSM and `n8n` to hit the same authenticated path during observation

Do not leave both DSM and `n8n` actively scheduling the same job long-term.

## OpenClaw Env Changes

When `n8n` takes ownership of stateful recurring jobs, set:

```env
ENABLE_PORTFOLIO_RECONCILER_WATCH=false
ENABLE_IOL_REFRESH_LOOP=false
ENABLE_STARTUP_IOL_REFRESH=true
```

Apply them with:

```bash
docker compose up -d openclaw
```

Keep `ENABLE_STARTUP_IOL_REFRESH=true` through the first stable cutover so container restarts still heal the token.

## Job-by-Job Cutover

### 1. finance_daily

- import workflow
- execute it manually in `n8n`
- verify Telegram output and logs
- disable matching DSM schedule if present

### 2. notification_calendar

- publish only after manual execution succeeds
- confirm no duplicate calendar alerts

### 3. notification_email

- confirm the local Ollama classifier responds
- confirm no duplicate urgent-email alerts

### 4. portfolio_reconciler

Before enabling:

```bash
export ENABLE_PORTFOLIO_RECONCILER_WATCH=false
docker compose up -d openclaw
```

After restart:

```bash
docker logs openclaw --tail 100 | grep "Portfolio reconciler watch deshabilitado"
```

### 5. iol_refresh

Before enabling:

```bash
export ENABLE_IOL_REFRESH_LOOP=false
docker compose up -d openclaw
```

After restart:

```bash
docker logs openclaw --tail 100 | grep "IOL auto-refresh loop deshabilitado"
```

Leave startup refresh enabled.

## Observability Checks

Check logs after each job activation:

```bash
docker logs n8n --tail 100
docker logs openclaw --tail 100
tail -n 50 workspace/runtime/logs/job_runner.log
```

Check runner health again:

```bash
curl -sf http://127.0.0.1:18891/health
```

## Rollback

If a workflow misbehaves:

1. disable the workflow in `n8n`
2. re-enable the matching DSM task in `direct` mode
3. persist affected flags in `.env` (an export only lasts for the current shell):
   - set `ENABLE_PORTFOLIO_RECONCILER_WATCH=true`
   - set `ENABLE_IOL_REFRESH_LOOP=true`
4. restart `openclaw`

Commands:

```bash
docker compose --profile migration stop n8n
docker compose up -d openclaw
```

## Done Criteria

The cutover is complete when:

- low-risk workflows run only from `n8n`
- duplicate DSM tasks are disabled
- startup loops for reconciler and IOL refresh are disabled
- `job_runner` health stays clean
- no duplicate Telegram notifications appear for the migrated jobs
