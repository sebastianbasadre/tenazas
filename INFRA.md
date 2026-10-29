# Tenazas infrastructure

Configuration reviewed **2026-10-28**. Addresses describe the reference homelab;
connectivity and live inventory were not checked during this documentation review.

## Reference hosts

| Host | Reference address | Role |
|---|---|---|
| Synology DS923+ NAS | `192.168.2.2` | Docker host and assistant runtime |
| Windows HomeLab | `192.168.2.6` | Ollama, RTX 3090 24 GB GPU, and training |
| Backup NAS | `192.168.2.3` | Existing deployment's backup target |
| Router | `192.168.2.1` | LAN `192.168.2.0/24` |

Adapt `TENAZAS_*` settings in [.env.example](.env.example) and
[runtime_config.py](workspace/runtime/runtime_config.py). DSM versions, disk usage,
available RAM, loaded models, and active container counts require host inspection.

## Compose services

| Service | Repository configuration |
|---|---|
| `openclaw` | Local build, Node 24, host networking, UID 1026, mounted Docker socket |
| `prometheus` | Image required through `.env`, port 9090, retention 30 d / 1 GB |
| `grafana` | Image required through `.env`, port 3000, versioned provisioning and dashboards |
| `searxng` | Image required through `.env`, host port 8890 → container 8080 |
| `n8n` | `docker.n8n.io/n8nio/n8n:2.19.5`, port 5678, optional `migration` profile |

Ollama, Piper, ChromaDB, gluetun, the media stack, Portainer, and Watchtower are
referenced environment dependencies, not services created by this Compose file.
A fixed container count is not a reliable health criterion.

## Endpoints and telemetry

| Port | Service | Configuration source |
|---|---|---|
| 18789 | OpenClaw gateway, `/health` | Compose healthcheck |
| 18891 | Job runner, `/health` and `/run/<job>` | Localhost by default; POST requires token |
| 9101 | Portfolio exporter | Startup and Prometheus target |
| 9105 | Runtime metrics | `metrics_server.py` |
| 9106 | API call counters | `api_call_counter.py` |
| 9107 | Tool evaluation exporter | `tool_tuning/eval_exporter.py` |
| 9108 | Network exporter | `network_exporter.py` |
| 9109 | Finance exporter | `finance_exporter.py` |
| 9090 | Prometheus | Compose |
| 3000 | Grafana | Compose |
| 8890 | SearXNG | Compose |
| 5678 | Optional n8n | `migration` profile |
| 11434 | HomeLab / NAS Ollama | External service |
| 9182 / 9835 | HomeLab Windows / GPU exporters | External Prometheus targets |

Prometheus defaults to a **15 s** scrape interval and **30 s** rule evaluation.
Job overrides use 30 s for API calls/network/portfolio, 60 s for finance, and
300 s for evaluation. See [prometheus.yml](prometheus.yml) and [alerts.yml](alerts.yml).

Dashboard JSON files live in [grafana/dashboards](grafana/dashboards/).
Provisioning lives in [grafana/provisioning](grafana/provisioning/); there is no
root-level `grafana-dashboard.json`.

## Lifecycle and scheduling

[startup.sh](startup.sh) launches several processes inside `openclaw`. The Telegram
router and job-search watcher have watchdogs; other background processes do not
necessarily use the same restart strategy. The healthcheck observes the gateway
and does not itself restart an `unhealthy` container.

The runner binds to localhost by default. From n8n's bridge network,
`host.docker.internal` resolves the host gateway, not host loopback. Set a reachable
bind and appropriate access restrictions using the [cutover runbook](docs/n8n-cutover-runbook.md).

Inventory DSM tasks and workflow schedules on the host. n8n's profile is disabled
by default in Compose, which does not establish whether an existing deployment
has activated it. Follow the [DSM checklist](docs/dsm-task-checklist.md) and keep
one scheduler per job.

## Credentials and persistence

`.env`, `state/`, OAuth tokens, email configuration, and SSH credentials are local
operator data. Bind mounts need permissions compatible with UID 1026 and the real
socket GID. Prometheus and Grafana use named volumes; n8n uses `n8n/data/`.

See [Setup](docs/setup.md), [Architecture](docs/architecture.md), and [Models](MODELS.md).
