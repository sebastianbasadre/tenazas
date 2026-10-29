# Instalación y configuración

Revisión del repositorio: **2026-10-28**. Despliegue previsto: host Linux con Docker
Compose; en Synology, adaptar directorios, UID/GID y permisos a DSM.

## Requisitos

- Docker Engine y Compose con perfiles y `host-gateway`.
- Acceso del usuario de despliegue al socket Docker.
- Espacio para imágenes, modelos, estado y métricas.
- Ollama accesible en el HomeLab si se usarán modelos locales.
- Tokens y credenciales para las integraciones que se habiliten.

El Dockerfile usa `node:24-bookworm`, crea el usuario `tenazas` con UID 1026 e
instala Python, herramientas de red, Docker CLI y dependencias de `requirements.txt`.
OpenClaw se fija mediante `OPENCLAW_VERSION` (default del Compose: `2026.6.1`).

## Preparar el checkout

```bash
git clone https://github.com/sebastianbasadre/tenazas.git
cd tenazas
cp .env.example .env
mkdir -p state himalaya-config ssh-keys
```

Editar `.env` antes del arranque:

| Variable | Configuración |
|---|---|
| `GF_SECURITY_ADMIN_PASSWORD` | Contraseña propia para Grafana |
| `TENAZAS_JOB_RUNNER_TOKEN` | Valor aleatorio propio; generar con `openssl rand -hex 32` |
| `HOST_DOCKER_GID` | GID real del socket, consultar `stat -c '%g' /var/run/docker.sock` |
| `PROMETHEUS_IMAGE`, `GRAFANA_IMAGE`, `SEARXNG_IMAGE` | Revisar tags; el ejemplo de SearXNG usa `latest` |
| `OPENCLAW_TELEGRAM_BOT_TOKEN`, `OPENCLAW_TELEGRAM_CHAT_ID` | Credenciales/destino de los helpers Telegram |
| `GOOGLE_API_KEY`, `OPENROUTER_API_KEY`, `OLLAMA_API_BASE` | Completar según proveedores habilitados |
| `TENAZAS_*` de infraestructura | Adaptar IPs, endpoints y usuario a la red propia |

Compose carga `.env` dentro de `openclaw` mediante `env_file`. Los valores del
bloque `environment` pueden prevalecer sobre ese archivo. No publicar la salida
de `docker compose config`, que puede contener secretos; usar `--quiet` para validar.

## Configuración de OpenClaw

Crear `state/openclaw.json` con una configuración válida para la versión instalada
del gateway. La plantilla [openclaw.example.json](../openclaw.example.json) ilustra
campos que consumen helpers de Tenazas; incluye claves propias como `hue`, `devices`
e `iol` que pueden ser rechazadas por el schema estricto del gateway.

No copiar esa plantilla como configuración final sin validarla. Separar extensiones
propias en archivos de estado cuando el componente las admita; revisar
[notas de schema](../tools/SCHEMA_NOTES.md), `runtime_config.py` y los lectores de
cada integración. La validación ligera `validate_config()` de Tenazas no reemplaza
la validación del gateway.

Configurar la allowlist Telegram en `channels.telegram.allowFrom`. El coordinador
se desactiva por defecto; `USE_AGENT_COORDINATOR` prevalece sobre
`channels.telegram.useAgentCoordinator` para los helpers de Tenazas. Ese campo
propio también debe verificarse contra el schema del gateway antes de guardarlo.

## Revisar los bind mounts

| Origen en host | Destino o uso |
|---|---|
| `state/` | `/home/tenazas/.openclaw`, configuración y workspace operativo |
| `workspace/runtime/` | `/home/tenazas/runtime`, código Python |
| `tests/`, `tools/` | Suites y utilidades |
| `himalaya-config/` | Configuración de correo, si se usa |
| `ssh-keys/` | Claves SSH, montaje de solo lectura |
| `gcal-token.json` | Archivo OAuth Calendar, montaje de solo lectura |
| `/volume1/docker/piper/data` | Datos de Piper; adaptar a cada host |
| `/var/run/docker.sock` | Socket del host |

Preparar un archivo real `gcal-token.json` con OAuth si se usa Calendar. Si esa
integración se omite, quitar su mount en la configuración local de Compose; no
permitir que Docker cree un directorio con ese nombre por falta del archivo.
No hay una plantilla `gcal-token.json.example` en el repositorio.

El workspace completo no se copia automáticamente a `state/workspace/`: solo
se monta `workspace/runtime/` en otra ruta. Provisionar personalidad y skills
operativas según las integraciones elegidas; varios handlers esperan scripts en
`/home/tenazas/.openclaw/workspace/skills/`. El checkout no es un bootstrap completo
del estado personal del operador.

Asegurar que UID 1026 pueda escribir en estado, logs y runtime: algunas tareas
generan JSON junto al código y `startup.sh` inicializa un sandbox Git.

## Arrancar y comprobar

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

Un contenedor `Up` no prueba que todos los procesos secundarios estén sanos.
Comprobar logs, endpoints y targets de Prometheus. El healthcheck solo verifica
el gateway; no provoca un reinicio automático al marcar `unhealthy`.

Para n8n seguir el [runbook de cutover](n8n-cutover-runbook.md): requiere el perfil
`migration`, conectividad al runner y activación manual de cada workflow.

## Actualizar y respaldar

Respaldar configuración, tokens, datos operativos y volúmenes antes de cambiar
versiones. Revisar [backup-state.sh](../backup-state.sh) antes de ejecutarlo y adaptar
sus rutas al despliegue. Una reconstrucción de imagen no reemplaza los bind mounts
ni migra automáticamente configuraciones o tareas DSM.
