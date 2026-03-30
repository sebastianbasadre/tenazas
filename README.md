# Tenazas

Asistente personal de Sebastian, corriendo sobre [OpenClaw](https://github.com/nicholasgasior/openclaw) dentro de un contenedor Docker en un NAS Synology DS923+.

Tenazas monitorea infraestructura, controla dispositivos smart home, gestiona una media stack completa, y se comunica via Telegram — todo de forma autonoma y local-first.

---

## Arquitectura

```
Synology DS923+ (192.168.2.2)
├── Docker Engine
│   ├── openclaw         — Tenazas (Node.js 22, network_mode: host)
│   ├── gluetun          — VPN gateway (NordVPN, servidor España)
│   ├── arr-stack (10)   — qbittorrent, sonarr, radarr, lidarr, prowlarr,
│   │                      seerr, requestrr, tautulli, sabnzbd
│   ├── chromadb         — Vector DB para RAG (:8100)
│   ├── piper            — TTS local (voz es_MX)
│   ├── ollama           — LLM fallback CPU
│   ├── portainer        — Dashboard Docker
│   └── watchtower       — Auto-update de imagenes
│
MSI Notebook (192.168.2.76) — GPU remota
└── Ollama (:11434) — RTX 4050
    ├── qwen2.5:3b/7b    — Tareas de sistema y razonamiento
    ├── qwen3:8b          — Alternativa
    └── nomic-embed-text  — Embeddings para RAG

Smart Home (LAN)
├── Philips Hue Bridge   — 8 luces
└── Samsung M8 Monitor   — Control via WebSocket
```

**18 containers** en operacion normal. Canal de comunicacion principal: **Telegram**.

---

## Componentes clave

| Componente | Descripcion |
|---|---|
| **Orchestrator** | Clasifica queries y routea al pipeline + modelo optimo (keyword+regex, sin LLM) |
| **Runtime Layer** | Monitoreo event-driven (cada 5 min): detecta containers caidos, leaks VPN, Ollama offline. Cooldown de 20 min por target |
| **RAG Pipeline** | Query rewrite → hybrid retrieval → rerank → confidence check → memory fusion → context compression → action mapping → LLM |
| **Confidence Engine** | Score 0-1 (similarity, agreement, recency, source quality). Self-check: answer / caveat / expand / clarify |
| **Action Mapper** | Mapea problemas a skills existentes. Nunca auto-ejecuta |
| **Infra Diagnostics** | Reglas: crash loops, VPN leaks, network failures, Ollama offline. Severity: critical/warning/info |
| **LLM Routing** | Cascada: Notebook GPU → NAS CPU → Gemini Cloud |
| **Arr-stack** | 10 containers de media gestionados via VPN (gluetun) |
| **Smart Home** | Control de luces Hue y monitor Samsung M8 |
| **22 Skills** | Desde clima y email hasta auto-analisis semanal (`self-evolve`) |
| **Multi-agent** | 5 agentes especializados: Reasoner, Executor, Guardian, Analyst, Memory |

---

## Prerequisitos

- **Synology NAS** con Docker (o cualquier host Linux con Docker)
- **Node.js 22** (incluido en la imagen Docker)
- **Python 3** con pip (incluido en la imagen Docker)
- **Docker socket** accesible (`/var/run/docker.sock`)
- **API keys** configuradas en `state/openclaw.json`:
  - Telegram Bot Token
  - Google API Key (Gemini)
  - Google Calendar OAuth (`gcal-token.json`)
  - Gmail App Password (himalaya)

---

## Instalacion y ejecucion

```bash
# Clonar el repo
git clone <repo-url> && cd openclaw

# Configurar secrets
cp openclaw.json.example state/openclaw.json   # editar con tus keys
cp gcal-token.json.example gcal-token.json      # OAuth token de Calendar

# Levantar
docker compose up -d

# Verificar
docker ps --format "table {{.Names}}\t{{.Status}}" | head -20
```

El container `openclaw` arranca con `startup.sh`, que inicia el gateway y notifica via Telegram.

### Cron jobs (DSM Task Scheduler)

| Frecuencia | Comando |
|---|---|
| Cada 5 min | `docker exec openclaw python3 /home/tenazas/runtime/runtime.py` |
| Domingos 21:00 | `python3 self-evolve.py` |
| Semanal | `python3 learn.py` |

---

## Tests

```bash
# Todas las suites
docker exec openclaw bash /home/tenazas/tests/run_tests.sh

# Por suite individual
docker exec openclaw python3 /home/tenazas/tests/test_runtime.py      # 48 tests
docker exec openclaw python3 /home/tenazas/tests/test_query.py
docker exec openclaw python3 /home/tenazas/tests/test_compact.py
docker exec openclaw bash   /home/tenazas/tests/test_auto_heal.sh
docker exec openclaw bash   /home/tenazas/tests/test_ollama_check.sh
```

Desde Windows via SSH:
```bash
ssh -p 4822 sbasadre@192.168.2.2 '/usr/local/bin/docker exec openclaw python3 /home/tenazas/tests/test_runtime.py'
```

---

## Estructura del proyecto

```
openclaw/
├── docker-compose.yml       — Definicion de servicios
├── Dockerfile               — Imagen base (node:22-bookworm + python3)
├── startup.sh               — Arranque del container
├── ollama-check.sh          — Routing de modelos Ollama con cache
├── auto-heal.sh             — (deprecated, reemplazado por runtime layer)
├── learn.py                 — Aprendizaje multi-source semanal
├── self-evolve.py           — Auto-analisis y propuestas de mejora
├── gcal-auth.py             — Setup OAuth para Google Calendar
│
├── state/                   — Config y estado (gitignored)
│   ├── openclaw.json        — Config principal + secrets
│   └── workspace/skills/    — 22 skills con SKILL.md cada una
│
├── workspace/
│   ├── runtime/               — Runtime layer (event-driven, preferido)
│   │   ├── orchestrator.py    — Central AI routing (classify + model select)
│   │   ├── runtime.py         — Loop principal
│   │   ├── executors.py       — Probes de monitoreo + dispatch
│   │   ├── docker_wrapper.py  — Capa de seguridad Docker
│   │   ├── confidence_engine.py — Scoring de confianza para RAG
│   │   ├── action_mapper.py   — Mapeo de problemas a skills
│   │   └── infra_diagnostics.py — Diagnosticos rule-based
│   ├── runtime.py             — Runtime legacy (coexiste)
│   └── executors.py           — Executors legacy
│
├── tests/                     — Test suites (unittest + bash)
│   ├── run_tests.sh
│   ├── test_runtime.py        — 54 tests del runtime layer + cooldown
│   ├── test_query.py          — 34 tests del RAG pipeline
│   ├── test_confidence.py     — 40 tests confidence + actions + diagnostics
│   ├── test_orchestrator.py   — 43 tests del orchestrator
│   ├── test_compact.py        — 11 tests de compactacion
│   ├── test_auto_heal.sh      — Tests de auto-heal
│   └── test_ollama_check.sh   — Tests de ollama routing
│
├── himalaya-config/         — Configuracion de email (gitignored)
├── credentials/             — API keys (gitignored)
└── CLAUDE.md                — Documentacion completa para Claude Code
```

---

## Seguridad

- **docker_wrapper.py** es la unica via para ejecutar comandos Docker desde el runtime
- Whitelist de acciones: `ps`, `restart`, `start`, `stop` (no `exec`, `rm`, `build`)
- Containers protegidos: `openclaw` y `watchtower` no pueden ser reiniciados automaticamente
- Sanitizacion de nombres via regex + blocklist
- Audit log en JSON (`logs/docker_actions.log`)
- Secrets nunca hardcodeados — viven en `openclaw.json`, env vars, o `credentials/`

---

## Licencia

Proyecto privado de uso personal.
