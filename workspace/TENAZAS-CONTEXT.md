# Tenazas — Full Context

## Qué es
Tenazas es el asistente personal de Sebastián (Sebas), corriendo sobre OpenClaw (framework de agentes Node.js) dentro de Docker en un NAS Synology DS923+. No es una app web ni una librería — es infraestructura de asistente personal.

## Canal de comunicación
- **Telegram** — canal principal, bot @debianoc_bot
- Chat ID: 1565794551
- Policy: allowlist (solo responde a Sebas)

## Personalidad (SOUL.md)
- Habla en español rioplatense, directo, sin vueltas
- No servil. Competente, no mayordomo
- Respuestas cortas (Telegram)
- Humor seco permitido
- **REGLA PRINCIPAL: ejecutar comandos, no hablar de ellos**

---

## Arquitectura

```
Synology DS923+ (192.168.2.2) — Host principal, Ryzen CPU
├── Docker Engine (20 containers)
│   ├── openclaw          — Tenazas (Node.js 22, Bookworm, network_mode: host)
│   ├── gluetun           — VPN gateway NordVPN (España, es247.nordvpn.com)
│   ├── arr-stack (10)    — gluetun, qbittorrent, sonarr, radarr, lidarr,
│   │                       prowlarr, seerr, requestrr, tautulli, sabnzbd
│   ├── chromadb          — Vector DB para RAG (:8100)
│   ├── piper             — TTS local (voz es_MX-claude-high)
│   ├── prometheus        — Métricas (:9090), retención 30d/1GB
│   ├── grafana           — Dashboard (:3000), admin/tenazas
│   ├── ollama            — LLM NAS CPU (fallback, solo modelos chicos)
│   ├── portainer         — Dashboard Docker
│   └── watchtower        — Auto-update de imágenes
│
├── NAS 2 (192.168.2.3)  — Backup target (HyperBackup, daily 2-3AM)
└── Router (192.168.2.1)

HomeLab (192.168.2.6) — GPU remota
└── Ollama (:11434) — RTX 3090, 24GB VRAM
    ├── qwen2.5:3b        — Tareas de sistema, rápido
    ├── qwen2.5:7b        — Razonamiento (necesita ~5GB VRAM)
    ├── qwen3:8b          — Alternativa (necesita ~5.5GB VRAM)
    ├── phi4-mini          — Fallback liviano (~3GB VRAM, pre-cargado)
    └── nomic-embed-text  — Embeddings para RAG

Smart Home (LAN 192.168.2.0/24, ~40 dispositivos)
├── Philips Hue Bridge (192.168.2.11) — 8 luces
└── Samsung M8 Monitor (192.168.2.46) — WebSocket control
```

---

## LLM Routing

### Modelo principal
- **Gemini 2.5 Flash** (Google Cloud, gratis) — modelo por defecto
- Es el ÚNICO modelo que opera OpenClaw correctamente (tools, skills, SOUL.md)

### Fallback automático (model_failover.py)
Cuando Gemini da 429 (rate limit):
1. model_failover.py detecta el error en logs cada 30s
2. Cambia openclaw.json a `ollama/phi4-mini` (hot-reload, sin restart)
3. phi4-mini ya está pre-cargado en VRAM (sin cold start de 46s)
4. Cada 5 min, prueba si Gemini volvió → switch back automático

### Limitación conocida
Los modelos locales (phi4-mini, qwen2.5:7b, qwen3:8b) NO pueden operar OpenClaw como agente. No ejecutan tools, no siguen SOUL.md bien, responden en español neutro. Solo sirven para el runtime (monitoreo, clasificación).

### Cascada de routing (ollama-check.sh)
1. Notebook GPU (192.168.2.6:11434) — preferido
2. NAS CPU (127.0.0.1:11434) — solo modelos chicos
3. Gemini Flash (cloud) — último recurso

---

## Skills (22 implementadas)

Cada skill tiene SKILL.md con docs y opcionalmente scripts .py/.sh.

| Skill | Función | Comando |
|---|---|---|
| weather | Clima Open-Meteo | `curl -s "https://api.open-meteo.com/v1/forecast?latitude=-34.76&longitude=-58.41&current_weather=true&daily=..."` |
| calendar-agenda | Google Calendar | `python3 gcal.py 2 2>/dev/null` |
| email-digest | Gmail via himalaya | `himalaya envelope list --folder INBOX --page-size 20 2>/dev/null` |
| smart-home | Hue lights + Samsung M8 | `curl` a Hue REST API (192.168.2.11) |
| tts | Text-to-speech Piper | `bash tts.sh "texto"` |
| voice-transcribe | Whisper STT | OpenAI Whisper base, español |
| rag | Knowledge base | `python3 query.py "<pregunta>" --top-k 3 --json --no-rewrite` |
| infra-monitor | Health del NAS/Docker/red | `docker ps`, nmap, ping |
| network-scanner | Scan LAN | `nmap -sn 192.168.2.0/24` |
| container-restart | Auto-restart containers | Via docker_wrapper.py |
| self-evolve | Auto-análisis semanal | Domingos 9PM, propone mejoras por Telegram |
| morning-greeting | Saludo diario | 8AM con status de infra |
| agent-dispatcher | Multi-agent routing | 5 agentes especializados |
| reminders | Recordatorios | Follow-ups |
| arr-monitor | Media stack | 10 containers |
| backup-monitor | HyperBackup | Freshness check |
| internet-health | Conectividad/DNS/VPN | Checks |
| model-manager | Lifecycle Ollama | Pull/remove modelos |
| skill-factory | Creación on-demand | Propone nuevas skills |
| compact-context | Compactación | Sesiones largas |
| command-validator | Seguridad | Prevención de inyección |

---

## Runtime Layer (42 módulos, event-driven)

### Loop principal (cada 5 min via cron DSM)
```
lockfile → update_state → detect_events → enqueue_tasks → run_tasks
→ append_memory → analyze_patterns → trigger_patterns → notify_telegram → release_lock
```

### Eventos detectados
- container_down / container_recovered
- internet_lost / internet_restored
- vpn_down / vpn_leak (IP argentina = leak) / vpn_changed
- device_count_changed (delta > 2 hosts)
- ollama_notebook_down / ollama_notebook_restored

### Acciones automáticas
- Container arr-stack caído → restart
- VPN down o leak → restart gluetun (prioridad)
- Cooldown 20 min por target (no re-reinicia)
- Deduplicación de tareas

### Protecciones
- Lockfile con stale detection (10 min)
- SIGALRM watchdog hard kill a 120s
- Freeze detector con heartbeat
- Runtime supervisor wrapper externo
- nmap rate limiting (cache 30 min)
- Log rotation (5MB max, 5 backups)

### Seguridad
- **docker_wrapper.py**: ÚNICO módulo que llama Docker via subprocess
- Whitelist: ps, restart, start, stop (NO exec, rm, build, pull)
- Containers protegidos: openclaw, watchtower
- **safety_guard.py**: valida TODAS las acciones antes de ejecutar
- Sanitización regex + blocklist case-insensitive
- Audit log JSON por línea

### Módulos principales
```
Core: orchestrator, runtime, docker_wrapper, executors, safety_guard
RAG: confidence_engine, retrieval_quality, context_manager
Diagnostics: infra_diagnostics, action_mapper, runbook_generator, knowledge_graph
Reasoning: multi_agent_engine, agent_lab, agent_conflict, decision_trace
Memory: metrics_engine, infra_memory, shared_memory, world_model, predictive_engine
Optimization: self_optimizer, adaptive_router, prompt_optimizer, experiment_engine, idle_learning
Autonomous: goal_engine, planner, model_router, skill_generator, background_agents, research_engine
Self-improvement: self_debugger, self_dev_engine, self_benchmark, dataset_builder
Observability: metrics_exporter, metrics_server, model_failover, freeze_detector
Testing: simulation_lab, failure_simulator, architecture_evolution
```

---

## Observabilidad

### Prometheus + Grafana
- Prometheus scrapes :9105 cada 30s
- Grafana en :3000 con dashboard "Tenazas Runtime" (14 paneles)
- Flujo: runtime.py → metrics_state.json → metrics_server.py → :9105 → Prometheus → Grafana

### 15 métricas exportadas
```
runtime_runs_total, runtime_last_duration_seconds, runtime_failures_total, runtime_health_score
container_restarts_total, vpn_disconnects_total, ollama_failures_total
runtime_freeze_detected, runtime_watchdog_triggered
rag_queries_total, rag_low_confidence_total, model_failover_active
runtime_step_update_state_seconds, runtime_step_detect_events_seconds
runtime_step_run_tasks_seconds, runtime_step_nmap_seconds
```

### Health Score (0-100)
Empieza en 100, resta:
- Ciclo >60s: -30, >30s: -15, >10s: -5
- Step individual >20s: -15, >10s: -5
- Freeze: -20 por evento
- >3 restarts: -20, 1-3 restarts: -10

### Docker Healthcheck
- Cada 60s: `curl http://127.0.0.1:18789/health`
- 3 fallos seguidos → Docker reinicia el container automáticamente
- start_period: 60s (espera arranque)

---

## RAG (Retrieval-Augmented Generation)

- **ChromaDB** @ 192.168.2.2:8100 — collection `tenazas-knowledge`
- **Embeddings:** nomic-embed-text via Ollama
- **Pipeline:** query_rewrite → hybrid_retrieval → reranking → confidence → memory_fusion → compression → action_mapping
- **MIN_SCORE = 0.6** — filtro duro
- **Ingestion:** cada 6 horas, chunking por headers markdown

---

## VPN y Red

- ISP: Movistar Argentina
- VPN: NordVPN via gluetun → es247.nordvpn.com (España)
- IP esperada VPN: 82.140.x.x (España)
- Proxy gluetun: 192.168.2.2:8888
- Leak detection: country=AR → reiniciar gluetun
- Red LAN: 192.168.2.0/24, ~40 dispositivos

### Dispositivos conocidos
- 192.168.2.1 — Router
- 192.168.2.2 — NAS principal (DS923+)
- 192.168.2.3 — NAS backup
- 192.168.2.11 — Hue Bridge
- 192.168.2.46 — Samsung M8
- 192.168.2.6 — HomeLab (Ollama GPU)

---

## Tests

414 tests en 24 suites, 77% coverage (36 de 47 módulos runtime testeados).

---

## Cron jobs

| Frecuencia | Comando | Estado |
|---|---|---|
| Cada 5 min | `docker exec openclaw python3 /home/tenazas/runtime/runtime_supervisor.py` | ACTIVO |
| Domingos 21:00 | `self-evolve.py` | ACTIVO |
| Semanal | `learn.py` | ACTIVO |
| Cada 6 horas | `python3 ingest.py` (RAG) | ACTIVO |

### OpenClaw internal cron
- infra-monitor: cada 30 min
- morning-greeting: 8AM daily
- email-check: cada hora (8-23h)
- security-alert-monitor: cada 15 min

---

## Archivos clave

| Archivo | Contenido |
|---|---|
| `state/openclaw.json` | Config principal + secrets (bot token, API keys) |
| `state/workspace/SOUL.md` | Personalidad e instrucciones del bot |
| `state/workspace/skills/*/SKILL.md` | Documentación de cada skill |
| `workspace/runtime/*.py` | 42 módulos del runtime layer |
| `workspace/runtime/logs/` | Logs JSON por módulo |
| `workspace/runtime/state.json` | Snapshot del sistema (containers, internet, VPN, ollama) |
| `workspace/runtime/memory_log.json` | Historial de eventos (cap 1000) |
| `workspace/runtime/metrics_state.json` | Métricas para Prometheus (cross-process) |
| `docker-compose.yml` | openclaw + prometheus + grafana |
| `startup.sh` | Arranque: gateway + metrics_server + model_failover + telegram notify |
| `CLAUDE.md` | Documentación completa para Claude Code |

---

## Problemas conocidos

1. **Modelos locales no sirven como agente** — phi4-mini/qwen no ejecutan tools ni siguen SOUL.md. Solo Gemini Flash funciona bien para chat.
2. **Gemini rate limit** — cuota gratis se agota, failover baja a phi4-mini (funcional pero tonto).
3. **Model loading** — RTX 3090 (24GB) caben todos los modelos, pero qwen2.5:32b (20GB) tarda ~10-15s en cargar.
4. **RAG no integrado al bot** — query.py funciona standalone pero el bot (excepto Gemini) no lo invoca automáticamente.

---

## Acceso SSH

```bash
ssh -p 4822 sbasadre@192.168.2.2    # NAS desde Windows
# Docker path en NAS: /usr/local/bin/docker
```
