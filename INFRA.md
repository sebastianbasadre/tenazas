# Infraestructura Tenazas

Revisión de configuración: **2026-10-28**. Las direcciones describen el homelab de
referencia. No se comprobó conectividad ni inventario activo durante esta revisión.

## Hosts de referencia

| Host | Dirección de referencia | Función |
|---|---|---|
| NAS Synology DS923+ | `192.168.2.2` | Docker y runtime |
| HomeLab Windows | `192.168.2.6` | Ollama, GPU RTX 3090 de 24 GB y entrenamiento |
| NAS de backup | `192.168.2.3` | Destino de respaldo del despliegue existente |
| Router | `192.168.2.1` | Red LAN `192.168.2.0/24` |

Adaptar variables `TENAZAS_*` de [.env.example](.env.example) y
[runtime_config.py](workspace/runtime/runtime_config.py). Versiones DSM, uso de
disco, RAM disponible, modelos cargados y cantidad de contenedores requieren un
relevamiento del host; no se deducen del repositorio.

## Servicios declarados en Compose

| Servicio | Configuración |
|---|---|
| `openclaw` | Build local, Node 24, red host, usuario UID 1026, socket Docker |
| `prometheus` | Imagen exigida por `.env`, puerto 9090, retención 30 d / 1 GB |
| `grafana` | Imagen exigida por `.env`, puerto 3000, provisioning y dashboards del repo |
| `searxng` | Imagen exigida por `.env`, puerto host 8890 a contenedor 8080 |
| `n8n` | `docker.n8n.io/n8nio/n8n:2.19.5`, puerto 5678, perfil opcional `migration` |

Ollama, Piper, ChromaDB, gluetun, media stack, Portainer y Watchtower forman parte
del entorno referenciado por el código, pero no son creados por este Compose.
No usar una cifra fija de contenedores como criterio de salud.

## Endpoints y métricas

| Puerto | Servicio | Fuente |
|---|---|---|
| 18789 | Gateway OpenClaw, `/health` | Healthcheck Compose |
| 18891 | Job runner, `/health` y `/run/<job>` | Localhost por defecto; POST con token |
| 9101 | Portfolio exporter | `startup.sh`, target Prometheus |
| 9105 | Métricas runtime | `metrics_server.py` |
| 9106 | Contador de llamadas API | `api_call_counter.py` |
| 9107 | Evaluación de tools | `tool_tuning/eval_exporter.py` |
| 9108 | Exporter de red | `network_exporter.py` |
| 9109 | Exporter de finanzas | `finance_exporter.py` |
| 9090 | Prometheus | Compose |
| 3000 | Grafana | Compose |
| 8890 | SearXNG | Compose |
| 5678 | n8n opcional | Perfil `migration` |
| 11434 | Ollama HomeLab / NAS | Servicio externo |
| 9182 / 9835 | Exporters Windows / GPU HomeLab | Targets externos de Prometheus |

Prometheus usa intervalo global de **15 s**, evaluación de reglas cada **30 s**
y overrides por job: APIs/red/cartera 30 s, finanzas 60 s, evaluación 300 s.
Ver [prometheus.yml](prometheus.yml) y [alerts.yml](alerts.yml).

Los dashboards están en [grafana/dashboards](grafana/dashboards/), no en un archivo
`grafana-dashboard.json` en la raíz. El provisioning se versiona en
[grafana/provisioning](grafana/provisioning/).

## Arranque y tareas

[startup.sh](startup.sh) inicia varios procesos dentro de `openclaw`. Router Telegram
y watcher laboral tienen watchdog; los demás procesos no comparten necesariamente
la misma política de reinicio. El healthcheck observa el gateway y no reinicia
por sí mismo el contenedor cuando está `unhealthy`.

El runner escucha en localhost por defecto. Desde un contenedor bridge como n8n,
`host.docker.internal` apunta al host, no a su loopback; configurar un bind alcanzable
y limitar acceso según el [runbook](docs/n8n-cutover-runbook.md).

Las tareas DSM y los workflows deben inventariarse en el host. El perfil de n8n
está desactivado por defecto en Compose, lo cual no demuestra si ya se activó en
un despliegue existente. Consultar el [checklist DSM](docs/dsm-task-checklist.md)
y evitar dos schedulers para el mismo trabajo.

## Credenciales y persistencia

`.env`, `state/`, tokens OAuth, configuración de correo y claves SSH son datos
locales. Los bind mounts requieren permisos compatibles con UID 1026 y el GID real
del socket. Prometheus y Grafana usan volúmenes nombrados; n8n usa `n8n/data/`.
Ver [instalación](docs/setup.md), [arquitectura](docs/architecture.md) y
[modelos](MODELS.md).
