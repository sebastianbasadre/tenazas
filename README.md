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
│   ├── prometheus       — Metricas (:9090), retencion 30d/1GB
│   ├── grafana          — Dashboard (:3000), admin/tenazas
│   ├── ollama           — LLM fallback CPU
│   ├── portainer        — Dashboard Docker
│   └── watchtower       — Auto-update de imagenes
│
HomeLab (192.168.2.6) — GPU remota
└── Ollama (:11434) — RTX 3090, 24GB VRAM
    ├── qwen2.5:7b        — fast: chat, sistema, default
    ├── deepseek-r1:14b   — reason: analisis, debugging
    ├── qwen2.5:32b       — heavy: tareas criticas, sintesis
    ├── qwen2.5-coder:14b — code: generacion, refactor
    ├── qwen2.5vl:7b      — vision: input visual
    ├── qwen3:8b          — fast: alternativa
    └── nomic-embed-text  — embed: RAG

Smart Home (LAN)
├── Philips Hue Bridge   — 8 luces
└── Samsung M8 Monitor   — Control via WebSocket
```

**20 containers** en operacion normal. Canal de comunicacion principal: **Telegram**.

---

## Componentes clave

| Componente | Descripcion |
|---|---|
| **Orchestrator** | Clasifica queries y routea al pipeline + modelo optimo (keyword+regex, sin LLM) |
| **Runtime Layer** | 42 modulos: monitoreo, diagnosticos, razonamiento, aprendizaje, simulacion |
| **RAG Pipeline** | Query rewrite → hybrid retrieval → rerank → confidence → memory fusion → compress → actions → LLM |
| **Model Failover** | Detecta Gemini 429, auto-switch a Ollama qwen2.5:7b, retry cada 5min |
| **Observabilidad** | Prometheus + Grafana, 15 metricas (health, runs, duration, restarts, VPN, RAG, steps, failover) |
| **Reasoning** | Multi-agent (Qwen+Phi), agent lab, planner, predictive engine |
| **Learning** | Metrics engine, self-optimizer, idle learning, model distillation, dataset builder |
| **Safety** | Safety guard, docker wrapper, audit logs. Nunca auto-ejecuta acciones de infra |
| **Infra Intelligence** | Infra memory (patrones), predictive engine, runbooks, world model, simulation lab |
| **LLM Routing** | Router V2: role-based (fast/reason/heavy/code/vision/embed) + hardware-aware fallback. Local-first |
| **Arr-stack** | 10 containers de media gestionados via VPN (gluetun) |
| **Smart Home** | Control de luces Hue y monitor Samsung M8 |
| **22 Skills** | Desde clima y email hasta auto-analisis semanal (`self-evolve`) |

---

## Observabilidad

### Prometheus (:9090)
Scrapea metricas cada 30s de `:9105/metrics`. Retencion 30d/1GB.

### Grafana (:3000)
Dashboard "Tenazas Runtime" con 14 paneles:
- Health Score (gauge + historia)
- Ciclos completados, failures, durations
- Container restarts, VPN disconnects, Ollama failures
- Freezes, watchdog events
- Step durations (update_state, detect_events, run_tasks, nmap)
- RAG queries, low confidence
- Memoria y CPU del proceso

### Metricas exportadas
```
runtime_runs_total              runtime_last_duration_seconds
runtime_failures_total          runtime_health_score
container_restarts_total        vpn_disconnects_total
ollama_failures_total           runtime_freeze_detected
runtime_watchdog_triggered      rag_queries_total
rag_low_confidence_total        model_failover_active
runtime_step_update_state_seconds
runtime_step_detect_events_seconds
runtime_step_run_tasks_seconds
runtime_step_nmap_seconds
```

### Flujo de metricas
```
cron (5min) → worker_supervisor.py → workers/* → metrics_state.json → metrics_server.py → :9105 → Prometheus → Grafana
```

---

## Worker Architecture

El runtime usa **crash isolation** via subprocesses. Cada worker corre aislado:

```
worker_supervisor.py (cron cada 5 min)
├── runtime_worker.py    [60s timeout] — core: state, events, tasks, memory
├── diagnostics_worker.py [30s timeout] — patterns, infra diagnostics
├── rag_worker.py        [30s timeout] — retrieval quality
├── agents_worker.py     [30s timeout] — skill analytics, proposals
└── learning_worker.py   [30s timeout] — background agents, goals, idle learning
```

Si un worker se congela → killed. Si crashea → los demás siguen.

Incluye:
- **Lockfile** con stale detection (10 min)
- **Log rotation** (5MB max, 5 backups)
- **Heartbeat** por worker (freeze detection 60s)
- **Devin-lite** suggestions (análisis de crashes → sugerencias por Telegram)

---

## Model Failover

Gemini Flash es el modelo primario (gratis, capaz). Cuando da rate limit (429):

```
Gemini 429 detectado
    → model_failover.py cambia config a ollama/phi4-mini
    → OpenClaw hot-reload (sin restart)
    → phi4-mini ya pre-cargado en VRAM (sin cold start)
    → Cada 5 min, prueba si Gemini volvio
    → Si disponible, switch back automatico
```

Docker healthcheck reinicia el container si el gateway no responde por 3 minutos.

---

## Prerequisitos

- **Synology NAS** con Docker (o cualquier host Linux con Docker)
- **Node.js 22** (incluido en la imagen Docker)
- **Python 3** con pip + prometheus_client
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

El container `openclaw` arranca con `startup.sh`, que inicia:
1. OpenClaw gateway (bot Telegram)
2. Metrics server (Prometheus :9105)
3. Model failover monitor (auto-switch Gemini/Ollama)
4. Notificacion Telegram de startup

### Cron jobs (DSM Task Scheduler)

| Frecuencia | Comando |
|---|---|
| Cada 5 min | `docker exec openclaw python3 /home/tenazas/runtime/worker_supervisor.py` |
| Domingos 21:00 | `python3 self-evolve.py` |
| Semanal | `python3 learn.py` |

---

## Tests

```bash
# Todas las suites
docker exec openclaw bash /home/tenazas/tests/run_tests.sh
```

### Test suites (414 tests)

| Suite | Tests | Cobertura |
|---|---|---|
| test_runtime.py | 54 | Docker wrapper, eventos, tareas, patterns, VPN, cooldown |
| test_safety_guard.py | 62 | Blocked actions, protected containers, sanitizacion, file ops |
| test_orchestrator.py | 43 | Clasificacion, model selection, pipeline, escalation |
| test_self_debugger.py | 41 | Log analysis, error classification, scan_all_logs |
| test_confidence.py | 40 | Scoring, source classification, action mapping, diagnostics |
| test_advanced.py | 41 | Metrics, infra memory, runbooks, multi-agent, optimizer |
| test_world_model.py | 38 | Entities, status, topology, state loading, data integrity |
| test_shared_memory.py | 39 | 4 memory types, persistence, ingestion, queries |
| test_planner.py | 35 | Templates, context substitution, approval, event mapping |
| test_query.py | 34 | RAG reranking, compression, memory fusion, scoring |
| test_skill_generator.py | 34 | Workflow analysis, proposals, approve/reject lifecycle |
| test_intelligence.py | 33 | Context, trace, conflict, retrieval, knowledge graph |
| test_model_router.py | 31 | GPU/CPU routing, VRAM constraints, fallback, catalog |
| test_goal_engine.py | 30 | 3 goals evaluation, metrics counting, state persistence |
| test_self_dev_engine.py | 28 | Debug/metrics/idle analysis, proposals, dedup |
| test_idle_learning.py | 27 | Confidence/escalation/latency/imbalance, log patterns |
| test_background_agents.py | 27 | Infra watcher, scheduling, execution, status |
| test_observability.py | 25 | Freeze detection, metrics export, health score |
| test_supervisor.py | 22 | Lock lifecycle, timeout, terminate fallback |
| test_stability.py | 17 | Lockfile, nmap cache, crash capture |
| test_compact.py | 11 | Context compaction |
| test_cognitive.py | varies | Intent, decomposition, reasoning |
| test_auto_heal.sh | bash | VPN leak, containers, lock |
| test_ollama_check.sh | bash | Routing, cache, JSON |

Coverage: **77%** (36 de 47 modulos runtime testeados)

---

## Estructura del proyecto

```
openclaw/
├── docker-compose.yml       — Servicios (openclaw + prometheus + grafana)
├── Dockerfile               — Imagen base (node:22-bookworm + python3)
├── startup.sh               — Arranque: gateway + metrics + failover + telegram
├── prometheus.yml            — Config de Prometheus scraping
├── grafana-dashboard.json    — Dashboard Grafana (14 paneles)
├── ollama-check.sh          — Routing de modelos Ollama con cache
├── learn.py                 — Aprendizaje multi-source semanal
├── self-evolve.py           — Auto-analisis y propuestas de mejora
│
├── state/                   — Config y estado (gitignored)
│   ├── openclaw.json        — Config principal + secrets
│   └── workspace/
│       ├── SOUL.md          — Personalidad e instrucciones del bot
│       └── skills/          — 22 skills con SKILL.md cada una
│
├── workspace/runtime/         — Runtime layer (48 modulos)
│   ├── runtime.py             — Loop principal event-driven (cada 5 min)
│   ├── runtime_supervisor.py  — Wrapper legacy con timeout protection
│   ├── worker_supervisor.py   — Worker orchestrator (5 isolated subprocesses)
│   ├── workers/               — Isolated worker subprocesses
│   │   ├── runtime_worker.py, diagnostics_worker.py, rag_worker.py
│   │   ├── agents_worker.py, learning_worker.py
│   ├── log_rotation.py        — Log rotation (5MB, 5 backups)
│   ├── worker_heartbeat.py    — Per-worker freeze detection
│   ├── network_cache.py       — Nmap cache (30min TTL)
│   ├── orchestrator.py        — Central AI routing
│   ├── safety_guard.py        — Validacion central de seguridad
│   ├── docker_wrapper.py      — Capa segura para Docker
│   ├── model_failover.py      — Auto-switch Gemini/Ollama en 429
│   ├── model_router.py        — Hardware-aware model selection
│   ├── metrics_exporter.py    — Recording API + cross-process sync
│   ├── metrics_server.py      — Prometheus HTTP server (:9105)
│   ├── planner.py             — Rule-based recovery plans
│   ├── world_model.py         — Infrastructure state representation
│   ├── shared_memory.py       — Multi-type memory system
│   ├── skill_generator.py     — Detects recurring workflows
│   ├── goal_engine.py         — 3 permanent goals tracking
│   ├── self_debugger.py       — Log error analysis
│   ├── self_dev_engine.py     — Improvement proposals
│   ├── idle_learning_engine.py — Idle-time optimization
│   ├── background_agents.py   — Persistent background tasks
│   └── ... (30+ mas)
│
├── tests/                     — 414 tests (unittest + bash)
└── CLAUDE.md                — Documentacion completa para Claude Code
```

---

## Seguridad

- **docker_wrapper.py** es la unica via para ejecutar comandos Docker desde el runtime
- Whitelist de acciones: `ps`, `restart`, `start`, `stop` (no `exec`, `rm`, `build`)
- Containers protegidos: `openclaw` y `watchtower` no pueden ser reiniciados automaticamente
- **safety_guard.py** valida todas las acciones antes de ejecutar (62 tests)
- Sanitizacion de nombres via regex + blocklist
- Audit log en JSON (`logs/docker_actions.log`)
- Secrets nunca hardcodeados — viven en `openclaw.json`, env vars, o `credentials/`
- Docker healthcheck con auto-restart si gateway no responde (3 min)

---

## Licencia

Proyecto privado de uso personal.
