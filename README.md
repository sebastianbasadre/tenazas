# Tenazas

Asistente personal de Sebastián sobre OpenClaw, con runtime Python, Telegram,
automatización del homelab, búsqueda laboral, finanzas y dispositivos del hogar.
Se despliega en un NAS Synology y utiliza Ollama en un HomeLab con GPU.

Documentación revisada el **2026-10-28** contra el código del repositorio. Esto
no certifica el estado de los equipos ni los servicios actualmente desplegados.

## Empezar

- [Índice de documentación](docs/README.md)
- [Instalación y configuración](docs/setup.md)
- [Arquitectura y flujos](docs/architecture.md)
- [Desarrollo y validación](docs/development.md)
- [Infraestructura y observabilidad](INFRA.md)
- [Modelos y routing](MODELS.md)

## Componentes

| Componente | Implementación |
|---|---|
| Entrada Telegram | `workspace/runtime/telegram_router.py`, interceptores y watchdog |
| Orquestación | `orchestrator.py` y coordinador opcional `agent_coordinator.py` |
| Agentes de dominio | Infraestructura, finanzas, investigación, seguridad, empleo y otros dominios |
| Modelos | `model_router.py`: roles, backends, límites de VRAM y guardrails |
| Monitoreo periódico | `worker_supervisor.py`: cinco subprocessos con timeout individual |
| Memoria y RAG | Módulos de retrieval, memoria compartida y consolidación |
| Automatización | Wrappers DSM y runner HTTP autenticado; n8n bajo perfil `migration` |
| Observabilidad | Exporters, Prometheus y dashboards provisionados de Grafana |
| Búsqueda laboral | Descubrimiento, matching, seguimiento y preparación de CV |

## Despliegue

El [Compose](docker-compose.yml) define `openclaw`, `prometheus`, `grafana` y
`searxng`. `n8n` requiere el perfil `migration`. Ollama, ChromaDB, Piper y la
media stack dependen de infraestructura externa a este Compose.

```bash
git clone https://github.com/sebastianbasadre/tenazas.git
cd tenazas
cp .env.example .env
```

Antes de levantar servicios, completar credenciales, configuración del gateway,
permisos y archivos montados según la [guía de instalación](docs/setup.md).
`openclaw.example.json` es una referencia para helpers de Tenazas: requiere
adaptación al schema del gateway y no garantiza un arranque completo.

```bash
# Después de completar los pasos de instalación:
docker compose config --quiet
docker compose up -d --build
docker compose ps
```

## Validación

Python requiere **3.11 o superior**. La imagen usa Node.js 24 y fija la versión
de OpenClaw mediante un argumento de build.

```bash
python -m pip install -r requirements-dev.txt
python -m pytest --collect-only
# Runner curado para Linux/contenedor:
bash tests/run_tests.sh
```

Hay suites que dependen de rutas Linux, Docker, APIs o dispositivos reales.
La [guía de desarrollo](docs/development.md) explica cómo seleccionar pruebas.
Los conteos de tests y métricas de evaluación deben obtenerse de cada ejecución.

## Operación y seguridad

- El socket Docker da acceso privilegiado al host; el runtime cuenta con wrapper,
  validación y registros de acciones.
- `docker_wrapper.py` permite consultas y acciones `restart/start/stop`, y protege
  `openclaw` y `watchtower` de sus acciones mutantes.
- El job runner exige un token del operador y escucha en `127.0.0.1:18891` por defecto.
- Las propuestas aceptadas pueden ser ejecutadas por `proposal_executor.py` con
  ramas, validaciones y rollback; revisar cada mecanismo antes de habilitarlo.
- Un healthcheck fallido marca el contenedor como `unhealthy`. La política
  `unless-stopped` reinicia procesos que salen; el healthcheck por sí solo no reinicia.
- Configuración, tokens y datos personales deben quedar fuera de los commits.

Ver [migración n8n](docs/n8n-cutover-runbook.md),
[búsqueda laboral](docs/job-search-platform.md) y
[contexto del workspace](workspace/TENAZAS-CONTEXT.md).

## Licencia

El repositorio no incluye un archivo de licencia. Su publicación no otorga por sí
sola una licencia de uso, modificación o redistribución.
