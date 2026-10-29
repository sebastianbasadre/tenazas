# Setup and configuration

Reviewed **2026-10-28**. The intended deployment is a Linux Docker host with
Compose; adapt paths, UID/GID, and permissions to Synology DSM when applicable.

## Requirements

- Docker Engine and Compose with profiles and `host-gateway` support.
- Deployment-user access to the Docker socket.
- Storage for images, models, state, and metrics.
- A reachable HomeLab Ollama endpoint for local inference.
- Credentials for the integrations you enable.

The Dockerfile uses `node:24-bookworm`, creates user `tenazas` with UID 1026, and
installs Python, networking tools, Docker CLI, and `requirements.txt` dependencies.
`OPENCLAW_VERSION` pins the gateway package; the Compose default is `2026.6.1`.

## Prepare the checkout

```bash
git clone https://github.com/sebastianbasadre/tenazas.git
cd tenazas
cp .env.example .env
mkdir -p state himalaya-config ssh-keys
```

Edit `.env` before startup:

| Variable | Required action |
|---|---|
| `GF_SECURITY_ADMIN_PASSWORD` | Set your own Grafana password |
| `TENAZAS_JOB_RUNNER_TOKEN` | Set a unique random token; generate with `openssl rand -hex 32` |
| `HOST_DOCKER_GID` | Read the socket GID using `stat -c '%g' /var/run/docker.sock` |
| `PROMETHEUS_IMAGE`, `GRAFANA_IMAGE`, `SEARXNG_IMAGE` | Review tags; the SearXNG example still uses `latest` |
| `OPENCLAW_TELEGRAM_BOT_TOKEN`, `OPENCLAW_TELEGRAM_CHAT_ID` | Supply Telegram helper credentials and destination |
| `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, `OLLAMA_API_BASE` | Configure enabled providers |
| Infrastructure `TENAZAS_*` variables | Adapt addresses, endpoints, and usernames |

Compose injects `.env` into `openclaw` using `env_file`; its explicit `environment`
entries may take precedence. Full `docker compose config` output can contain
secrets. Use `--quiet` for validation without displaying resolved values.

## Configure OpenClaw

Create `state/openclaw.json` using a configuration accepted by the installed
gateway version. [openclaw.example.json](../openclaw.example.json) illustrates fields
read by Tenazas helpers, including custom `hue`, `devices`, and `iol` keys that the
gateway's strict schema may reject.

Do not treat that template as a validated final gateway configuration. Store
extensions separately where supported by their readers. Consult
[schema notes](../tools/SCHEMA_NOTES.md), `runtime_config.py`, and each integration.
Tenazas `validate_config()` is a lightweight helper check, not gateway schema validation.

Configure the Telegram allowlist in `channels.telegram.allowFrom`. The coordinator
is disabled by default. Tenazas reads `USE_AGENT_COORDINATOR` before
`channels.telegram.useAgentCoordinator`; verify gateway acceptance before writing
that custom field into gateway configuration.

## Check bind mounts

| Host source | Container destination or purpose |
|---|---|
| `state/` | `/home/tenazas/.openclaw`, configuration and operational workspace |
| `workspace/runtime/` | `/home/tenazas/runtime`, Python source |
| `tests/`, `tools/` | Test suites and utilities |
| `himalaya-config/` | Email configuration when enabled |
| `ssh-keys/` | Read-only SSH credentials |
| `gcal-token.json` | Read-only Calendar OAuth file |
| `/volume1/docker/piper/data` | Piper data; adapt to your host |
| `/var/run/docker.sock` | Host Docker socket |

Provide a real OAuth `gcal-token.json` file for Calendar. If you omit Calendar,
remove its mount from your local Compose configuration; otherwise Docker may
create a directory when the expected file is absent. The repository does not
include a `gcal-token.json.example` template.

Compose does not automatically copy this repository's workspace into
`state/workspace/`. Runtime source is mounted separately. Provision personality
and operational skills for the integrations you use; several handlers expect
scripts under `/home/tenazas/.openclaw/workspace/skills/`. The checkout alone does
not bootstrap the operator's complete personal state.

Ensure UID 1026 can write required state, logs, and runtime-generated files.
Some modules write JSON alongside their source, and startup initializes a Git sandbox.

## Start and verify

```bash
docker compose config --quiet
docker compose config --services
docker compose up -d --build
docker compose ps
docker compose logs --tail 100 openclaw
curl -fsS http://127.0.0.1:18789/health
curl -fsS http://127.0.0.1:18891/health
curl -fsS http://127.0.0.1:9105/metrics
```

An `Up` container does not establish that every background process is healthy.
Check logs, endpoints, and Prometheus targets. The healthcheck covers the gateway;
it does not automatically restart an `unhealthy` container.

Follow [n8n cutover](n8n-cutover-runbook.md) before enabling the `migration` profile,
runner access, or workflow schedules.

## Updates and backups

Back up configuration, credentials, operational data, and named volumes before
version changes. Review and adapt [backup-state.sh](../backup-state.sh) before use.
Rebuilding an image does not replace bind mounts, migrate configuration, or install DSM tasks.
