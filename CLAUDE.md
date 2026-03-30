# CLAUDE.md — Tenazas / openclaw

Instrucciones completas para Claude Code al trabajar en este proyecto.

## Contexto del proyecto

Tenazas es el asistente personal de Sebastian (Sebas), corriendo en OpenClaw dentro de un contenedor Docker en un NAS Synology DS923+. El proyecto es la infraestructura del asistente, no una app web ni una libreria.

---

## Arquitectura

```
Synology DS923+ (192.168.2.2) — Host principal
├── Docker Engine
│   ├── openclaw          — Tenazas (network_mode: host, docker.sock montado)
│   ├── gluetun           — VPN gateway NordVPN (es247.nordvpn.com, Espana)
│   ├── arr-stack (10)    — gluetun, qbittorrent, sonarr, radarr, lidarr,
│   │                       prowlarr, seerr, requestrr, tautulli, sabnzbd
│   ├── chromadb          — Vector DB para RAG (:8100)
│   ├── piper             — TTS local (voz es_MX-claude-high)
│   ├── ollama            — LLM NAS CPU (fallback, solo modelos chicos)
│   ├── portainer         — Dashboard Docker
│   └── watchtower        — Auto-update de imagenes
│
├── NAS 2 (192.168.2.3)  — Backup target (HyperBackup)
└── Router (192.168.2.1)

MSI Notebook (192.168.2.76) — GPU remota
└── Ollama (:11434) — RTX 4050, modelos principales
    ├── qwen2.5:3b        — Tareas de sistema, rapido
    ├── qwen2.5:7b        — Razonamiento
    ├── qwen3:8b          — Alternativa
    ├── phi4-mini          — Fallback liviano
    └── nomic-embed-text  — Embeddings para RAG

Smart Home (LAN)
├── Philips Hue Bridge (192.168.2.11) — 8 luces
└── Samsung M8 Monitor (192.168.2.46) — WebSocket control
```

**18 containers esperados** en operacion normal.

---

## Stack completo

| Componente | Tecnologia | Ubicacion |
|---|---|---|
| Runtime host | Synology DS923+, Ryzen CPU | 192.168.2.2 |
| Container engine | Docker + docker-compose | NAS |
| Asistente | OpenClaw (Node.js 22, Bookworm) | Container `openclaw` |
| GPU inference | Ollama (RTX 4050) | 192.168.2.76:11434 |
| CPU inference | Ollama (Ryzen, fallback) | 127.0.0.1:11434 |
| Cloud inference | Google Gemini 2.5 Flash/Pro | API (fallback final) |
| Vector DB (RAG) | ChromaDB | 192.168.2.2:8100 |
| Embeddings | nomic-embed-text via Ollama | Local, gratis |
| TTS | Piper (es_MX-claude-high) | Container `piper` |
| STT | OpenAI Whisper (base, es) | Dentro de `openclaw` |
| VPN | NordVPN via gluetun | Proxy 192.168.2.2:8888 |
| Email | himalaya CLI (IMAP/SMTP Gmail) | Dentro de `openclaw` |
| Calendario | Google Calendar API (OAuth) | gcal.py |
| Clima | Open-Meteo (gratis, sin key) | curl directo |
| Chat | Telegram Bot API | Canal principal |
| Luces | Philips Hue REST API | 192.168.2.11 |
| Monitoreo | nmap, ping, curl | Dentro de `openclaw` |
| Backups | Hyper Backup → NAS 2 | Daily 2-3AM |

---

## LLMs y routing

### Modelo principal
- **Gemini 2.5 Flash** (Google Cloud, gratis) — modelo por defecto en openclaw.json
- **Gemini 2.5 Pro** — razonamiento complejo (alias: "reasoning")

### Modelos locales (Ollama)
| Modelo | Parametros | Uso | Backend |
|---|---|---|---|
| qwen2.5:3b | 3B | Sistema, logs, JSON | Notebook GPU |
| qwen2.5:7b | 7B | Razonamiento, analisis | Notebook GPU |
| qwen3:8b | 8B | Alternativa | Notebook GPU |
| phi4-mini | ~4B | Fallback liviano | NAS CPU o Notebook |
| nomic-embed-text | — | Embeddings RAG | Notebook GPU |

### Cascada de routing (`ollama-check.sh`)
1. **Notebook GPU** (192.168.2.76:11434) — preferido, todos los modelos
2. **NAS CPU** (127.0.0.1:11434) — solo modelos chicos (phi4-mini)
3. **Gemini Flash** (cloud) — ultimo recurso

```bash
bash ollama-check.sh --route system   # → BACKEND + MODEL + ENDPOINT
bash ollama-check.sh --route reason   # → usa 7b si notebook online
bash ollama-check.sh --route embed    # → nomic-embed-text
bash ollama-check.sh --json           # → status completo
bash ollama-check.sh --status         # → ok / offline
```

Cache de 120s en `/tmp/ollama-notebook.status` para evitar pings excesivos.

### Configuracion de modelos en OpenClaw
- `openclaw.json` (root) — config para desarrollo (usa gemini-pro por defecto)
- `state/openclaw.json` — config del container (usa phi4-mini por defecto, Ollama apunta a notebook)

---

## Telegram

Canal principal de comunicacion con Sebas.

- **Chat ID:** `1565794551`
- **Bot:** Tenazas (token en `state/openclaw.json` → `channels.telegram.botToken`)
- **Policy:** `allowlist` — solo responde a Sebas
- **Streaming:** `partial`
- **Notificaciones automaticas:**
  - Startup: "Tenazas Online — HH:MM ART" (startup.sh)
  - Auto-heal: solo si tomo alguna accion
  - Runtime: cuando ejecuta restarts o detecta problemas
  - Morning greeting: 8AM daily
  - Self-evolve: domingos 9PM

### Enviar mensaje desde scripts
```python
import json
from urllib.request import urlopen, Request

config = json.load(open("/home/tenazas/.openclaw/openclaw.json"))
token = config["channels"]["telegram"]["botToken"]
payload = json.dumps({"chat_id": "1565794551", "text": "mensaje"}).encode()
urlopen(Request(f"https://api.telegram.org/bot{token}/sendMessage",
        data=payload, headers={"Content-Type": "application/json"}), timeout=10)
```

---

## RAG (Retrieval-Augmented Generation)

### Componentes
- **ChromaDB** @ `192.168.2.2:8100` — vector store
- **Collection:** `tenazas-knowledge`
- **Embeddings:** nomic-embed-text via Ollama (local, gratis)
- **Ingestion:** cada 6 horas via cron

### Fuentes indexadas
- `workspace/*.md` (SOUL.md, AGENTS.md, MEMORY.md, etc.)
- `workspace/skills/*/SKILL.md` (22 skills)
- `workspace/memory/*.md` (notas diarias, learnings)

### Query
```bash
# Buscar sin generar respuesta
python3 skills/rag/query.py "gluetun proxy" --top-k 5

# Buscar + generar con LLM
python3 skills/rag/query.py "como controlo las luces?" --generate

# Output JSON
python3 skills/rag/query.py "IP del Hue Bridge" --json
```

### Pipeline avanzado (query.py)
```
query → query_rewrite → hybrid_retrieval → reranking
→ confidence_check → (expand if low) → memory_fusion
→ context_compression → action_mapping → LLM
```

**Etapas:**
1. **Query rewrite** — LLM expande query con sinonimos, descompone multi-part
2. **Hybrid retrieval** — Multi-query vector + keyword scoring, union con dedup
3. **Reranking** — source_boost + keyword_boost + recency_boost + multi_query_boost
4. **Confidence check** — Score 0-1 (similarity, agreement, recency, source_quality)
5. **Memory fusion** — 4 tipos: semantic + episodic + procedural + working
6. **Context compression** — Dedup >80% overlap + truncacion por budget
7. **Action mapping** — Mapea problemas a skills existentes (nunca auto-ejecuta)

**Scoring:**
- **MIN_SCORE = 0.6** — filtro duro
- **source_boost():** skills/SKILL.md y workspace root → +0.05, memory → -0.03
- **keyword_boost():** hasta +0.15
- **recency_boost():** memory reciente (7 dias) hasta +0.04

**Confidence thresholds:**
- 0.8-1.0 high → answer normally
- 0.6-0.79 medium → answer with caveat
- <0.6 low → expand retrieval or clarify

**CLI flags:** `--context "texto"`, `--no-rewrite`, `--debug`, `--json`

### Ingestion
```bash
python3 skills/rag/ingest.py   # re-indexa todo
```
- Chunking por headers markdown (max 2000 chars, min 200)
- Merge de chunks chicos con el siguiente
- Strip de code blocks para embeddings mas limpios
- Upsert a ChromaDB con metadata (source, chunk_index, total_chunks)

### Cuando re-indexar
- Despues de modificar `query.py` o `ingest.py`
- Despues de agregar o modificar SKILL.md files
- Despues de cambios significativos en SOUL.md o AGENTS.md

---

## Skills (22 implementadas)

Cada skill tiene `SKILL.md` con documentacion, y opcionalmente scripts `.py` / `.sh`.
Ubicacion: `state/workspace/skills/`

| Skill | Funcion | Scripts |
|---|---|---|
| **agent-dispatcher** | Multi-agent routing (5 agentes) | dispatcher.py |
| **arr-monitor** | Media stack (10 containers) | Solo SKILL.md |
| **backup-monitor** | HyperBackup + workspace freshness | Solo SKILL.md |
| **calendar-agenda** | Google Calendar (OAuth) | gcal.py |
| **command-validator** | Seguridad, prevencion de inyeccion | validate.py, alert-monitor.py |
| **compact-context** | Compactacion de contexto de sesion | compact.py |
| **container-restart** | Auto-restart containers caidos | Solo SKILL.md |
| **email-digest** | Gmail via himalaya CLI | Solo SKILL.md |
| **infra-monitor** | Health del NAS, Docker, Ollama, red | Solo SKILL.md |
| **internet-health** | Conectividad, DNS, VPN | Solo SKILL.md |
| **model-manager** | Lifecycle de modelos Ollama | Solo SKILL.md |
| **morning-greeting** | Saludo diario 8AM con status | Solo SKILL.md |
| **network-scanner** | Scan de dispositivos LAN (nmap) | Solo SKILL.md |
| **rag** | Vector store + query + ingestion | query.py, ingest.py |
| **reminders** | Recordatorios y follow-ups | Solo SKILL.md |
| **self-evolve** | Auto-analisis semanal (dom 9PM) | analyze.py, analyze.sh |
| **skill-factory** | Creacion on-demand de skills | Solo SKILL.md |
| **smart-home** | Hue lights + Samsung M8 | Solo SKILL.md |
| **tts** | Text-to-speech Piper → Telegram | tts.sh |
| **voice-transcribe** | Whisper transcription | Solo SKILL.md |
| **weather** | Clima via Open-Meteo | Solo SKILL.md |

### Multi-agent system (agent-dispatcher)
5 agentes especializados, todos locales (Ollama):
- **Reasoner** (qwen2.5:7b) — razonamiento y analisis
- **Executor** (qwen2.5:3b) — ejecucion de tareas
- **Guardian** (command-validator) — seguridad
- **Analyst** (qwen2.5:3b) — analisis de datos
- **Memory** (RAG) — consulta de knowledge base

Flow: Guardian primero (siempre) → Reasoner+Executor en paralelo → Analyst → Memory

---

## Runtime Layer (event-driven)

Sistema de monitoreo inteligente en `workspace/runtime/`.

### Arquitectura (41 modulos, reemplaza auto-heal.sh)
```
workspace/runtime/
│
│ ── Core ──
├── orchestrator.py            — Central AI routing (classify + model select)
├── runtime.py                 — Loop principal event-driven (cada 5 min)
├── docker_wrapper.py          — Capa de seguridad Docker
├── executors.py               — Probes de monitoreo + dispatch
├── safety_guard.py            — Validacion central de seguridad
│
│ ── RAG & Confidence ──
├── confidence_engine.py       — Scoring de confianza para RAG
├── retrieval_quality.py       — Calidad de retrieval (overlap, diversity, recency)
├── context_manager.py         — Token budget allocation y context pruning
│
│ ── Diagnostics & Actions ──
├── infra_diagnostics.py       — Diagnosticos rule-based
├── action_mapper.py           — Mapeo de problemas a skills
├── runbook_generator.py       — Runbooks de reparacion reutilizables
├── knowledge_graph.py         — Grafo de dependencias de infraestructura
│
│ ── Reasoning ──
├── multi_agent_engine.py      — Razonamiento cooperativo Qwen+Phi
├── agent_lab.py               — Lab de agentes distribuidos
├── agent_conflict.py          — Deteccion de desacuerdo entre agentes
├── decision_trace.py          — Log estructurado de decisiones
│
│ ── Intelligence & Memory ──
├── metrics_engine.py          — Metricas operacionales + agregacion
├── infra_memory.py            — Deteccion de patrones recurrentes
├── shared_memory.py           — Memoria compartida (semantic/episodic/procedural/working)
├── world_model.py             — Modelo del mundo (containers, red, servicios)
├── predictive_engine.py       — Prediccion de fallas de infra
│
│ ── Optimization & Learning ──
├── self_optimizer.py          — Aprende mejores pipeline+modelo combos
├── adaptive_router.py         — Routing aprendido por metricas historicas
├── prompt_optimizer.py        — Analisis de eficiencia de prompts
├── experiment_engine.py       — A/B testing de pipelines y modelos
├── idle_learning_engine.py    — Aprendizaje en periodos idle
├── model_distillation.py      — Transferencia de conocimiento entre modelos
│
│ ── Autonomous ──
├── goal_engine.py             — Sistema de objetivos (stability, uptime, accuracy)
├── planner.py                 — Planificacion de tareas complejas
├── model_router.py            — Seleccion de modelos por hardware disponible
├── skill_generator.py         — Propuesta de nuevos skills (requiere aprobacion)
├── skill_analytics.py         — Analisis de uso de skills
├── background_agents.py       — Agentes persistentes (watcher, pattern, metrics)
├── research_engine.py         — Investigacion autonoma de problemas
│
│ ── Self-improvement ──
├── self_debugger.py           — Auto-diagnostico de errores de Tenazas
├── self_dev_engine.py         — Propuestas de mejora de codigo
├── self_benchmark.py          — Benchmarking de rendimiento
├── dataset_builder.py         — Generacion de datasets operacionales
│
│ ── Testing & Evolution ──
├── simulation_lab.py          — Simulacion de escenarios de infra
├── failure_simulator.py       — Escenarios de falla granulares
├── architecture_evolution.py  — Deteccion de limitaciones arquitecturales
└── __init__.py
```

### Loop (cada 5 min via cron)
`lockfile → update_state → detect_events → enqueue_tasks → run_tasks → append_memory → analyze_patterns → trigger_patterns → notify_telegram → release_lock`

### Protecciones del runtime loop
- **Lockfile:** `runtime.lock` previene ejecuciones concurrentes (PID check)
- **SIGALRM watchdog:** hard kill a 240s si el ciclo se cuelga (Linux only)
- **Runtime Supervisor:** `runtime_supervisor.py` — wrapper externo que mata subprocess colgados
- **nmap rate limiting:** scan de red cada 30 min, no cada 5 min
- **Global timeout watchdog:** warning si ciclo excede 4 min
- **Log rotation:** `logs/runtime.log` se rota a 500KB
- **Cooldown:** 20 min por target (no re-reinicia si ya se reinicio)

### Cron recomendado (DSM Task Scheduler)
```bash
# Opcion A: supervisor (recomendado — protege contra cuelgues)
docker exec openclaw python3 /home/tenazas/runtime/runtime_supervisor.py

# Opcion B: directo (SIGALRM se encarga del timeout)
docker exec openclaw python3 /home/tenazas/runtime/runtime.py
```

### docker_wrapper.py — Capa de seguridad
- **UNICO modulo** que llama Docker via subprocess
- **Whitelist de acciones:** ps, restart, start, stop (NO exec, rm, build, pull)
- **Containers protegidos:** openclaw, watchtower (bloqueados)
- **Sanitizacion:** regex `^[a-zA-Z0-9][a-zA-Z0-9_.-]*$` + blocklist case-insensitive
- **Audit log:** `logs/docker_actions.log` (JSON por linea)
- Usa `subprocess.run([...])` con lista (NO `shell=True`)

### Eventos detectados
- `container_down` / `container_recovered`
- `internet_lost` / `internet_restored`
- `vpn_down` / `vpn_leak` (IP argentina = leak) / `vpn_changed`
- `device_count_changed` (delta > 2 hosts)
- `ollama_notebook_down` / `ollama_notebook_restored`

### Tareas automaticas
- Container de arr-stack caido → restart
- VPN down o leak → restart gluetun
- gluetun siempre tiene prioridad (se reinicia primero)
- Deduplicacion de tareas
- **Cooldown de 20 min** por target (no re-reinicia si ya se reinicio recientemente)
- Cooldown solo cuenta restarts exitosos (fallidos no cuentan)

### orchestrator.py — Central AI routing
- Clasifica queries en: chat, knowledge_query, infrastructure_issue, automation_command, diagnostic_request, complex_reasoning
- **Clasificacion lightweight:** keyword + regex rules (sin LLM)
- **Model selection:** elige modelo segun categoria + disponibilidad de backends
- **Pipeline routing:** cada categoria tiene modulos asignados (rag, diagnostics, action_mapper)
- **Context-aware:** recent_events, working_memory, ollama_available influyen en routing
- **Escalation:** low confidence → escala a pipeline mas capaz (knowledge→reasoning, etc.)
- **Fallback chain:** si GPU offline → cloud, si cloud offline → local
- **Telemetria:** `logs/orchestrator.log` (JSON por linea)
- `route_query(query, context)` → {classification, pipeline, model, parameters}
- `escalate(original_route, reason)` → ruta escalada

### confidence_engine.py — Scoring de confianza
- Score 0-1 combinando: similarity (0.35), agreement (0.25), recency (0.20), source_quality (0.20)
- **Thresholds:** high (>=0.8), medium (>=0.6), low (<0.6)
- **Self-check:** high→answer, medium→answer+caveat, low→expand/clarify
- Source reliability tiers: skill=1.0, core_doc=0.95, workspace=0.80, memory_recent=0.70
- Log: `logs/diagnostics.log`

### action_mapper.py — Mapeo problemas → skills
- Regex pattern matching contra ACTION_RULES
- Mapea: vpn_down→gluetun restart, container_down→container restart, internet_slow→network diag
- Deduplicacion por target, sorted por prioridad
- **NUNCA ejecuta** — solo sugiere acciones
- `format_suggestions()` para output Telegram/LLM

### infra_diagnostics.py — Diagnosticos rule-based
- Reglas: container_down, crash_loop (>=3 restarts/2h), internet_down, high_latency, vpn_down/leak, ollama_offline
- Severity: critical, warning, info
- `quick_health_check()` para embed en otros outputs
- `format_diagnostic_report()` para Telegram

### Pattern engine
- Detecta eventos recurrentes correlacionados por hora
- Throttled a 1 analisis/hora
- Minimo 3 ocurrencias en 7 dias para declarar patron
- Preemptive restart en horas predichas

### State files (gitignored, regenerados)
- `state.json` — snapshot del sistema
- `events.json` — eventos del ultimo run
- `tasks.json` — cola de tareas
- `memory_log.json` — historial (cap 1000 entries)
- `patterns.json` — patrones aprendidos

### Notificaciones
- Telegram solo cuando se ejecutan acciones o notebook offline
- VPN country check via ip-api.com (AR = leak)
- Ollama status file `/tmp/ollama-notebook.status` (compatibilidad con ollama-check.sh)

---

## Workspace runtime legacy (workspace/runtime.py + workspace/executors.py)

**NOTA:** Estos son los modulos originales, anteriores al runtime layer nuevo. Coexisten pero el nuevo (`workspace/runtime/`) es el preferido.

### workspace/runtime.py
- Loop similar pero usa `shell=True` (riesgo de seguridad)
- Incluye subsistema de reminders
- Pattern engine con clasificacion de Hue por keywords
- Enqueue de `hue.scene` basado en patrones temporales

### workspace/executors.py
- **Docker:** restart, start, stop (sin sanitizacion de nombres)
- **Hue:** 5 escenas (off, on, work, cinema, relax) via curl a Hue API
- **Network:** scan nmap
- Usa `shell=True` en `_sh()` — NO pasar input de usuario

---

## Scripts de infraestructura (host/container)

### `auto-heal.sh` (DEPRECATED — reemplazado por runtime layer)
Cron DSM deshabilitado (`#DISABLED_BY_RUNTIME#`). El runtime layer cubre todas sus responsabilidades:
- VPN leak detection (country check)
- Container restarts (arr-stack)
- Ollama status file
- Telegram notifications

### `ollama-check.sh`
Routing helper con cache de 120s. Sigue activo.
```bash
bash ollama-check.sh --route system   # → BACKEND + MODEL + ENDPOINT
bash ollama-check.sh --route reason
bash ollama-check.sh --json           # status completo
bash ollama-check.sh --status         # ok / offline
```

### `startup.sh`
Arranque del container: lanza `openclaw gateway` + notifica Telegram.

### `learn.py` (cron semanal en host)
Multi-source learning: descubre modelos Ollama, recopila logs, pide analisis a multiples modelos, sintetiza, guarda en `memory/learnings.md`, notifica Telegram.

### `self-evolve.py` (cron semanal en host)
Analisis de errores, skills, Docker health. Propone mejoras via Telegram. **NUNCA auto-deploya** — espera aprobacion de Sebas.

---

## APIs y servicios externos

| Servicio | Uso | Auth | Notas |
|---|---|---|---|
| Google Gemini | LLM cloud (flash/pro) | API Key (env var) | Fallback cuando Ollama offline |
| Google Calendar | Eventos proximos | OAuth refresh token | gcal-token.json |
| Gmail | Email digest | App password (himalaya) | IMAP/SMTP |
| Telegram Bot API | Chat con Sebas | Bot token (openclaw.json) | Canal principal |
| ip-api.com | VPN country check | Sin auth (gratis) | Rate limited |
| Open-Meteo | Clima | Sin auth (gratis) | Primary weather source |
| wttr.in | Clima fallback | Sin auth | Secondary |
| NordVPN | VPN | Via gluetun config | Servidor Espana |

### APIs internas (LAN, sin auth)
| Servicio | URL | Uso |
|---|---|---|
| Ollama Notebook | http://192.168.2.76:11434 | LLM inference (GPU) |
| Ollama NAS | http://127.0.0.1:11434 | LLM fallback (CPU) |
| ChromaDB | http://192.168.2.2:8100 | Vector store RAG |
| Gluetun Proxy | http://192.168.2.2:8888 | VPN HTTP proxy |
| Hue Bridge | http://192.168.2.11/api/{key} | Smart lights |
| Samsung M8 | ws://192.168.2.46:8001 | Monitor control |

---

## Docker

### docker-compose.yml
```yaml
services:
  openclaw:
    build: .
    container_name: openclaw
    restart: unless-stopped
    network_mode: host
    environment:
      - TZ=America/Buenos_Aires
      - NODE_OPTIONS=--dns-result-order=ipv4first
      - OLLAMA_API_KEY=ollama-local
      - GOG_KEYRING_PASSWORD=***
      - GOG_ACCOUNT=***
      - GOOGLE_API_KEY=***
    volumes:
      - ./state:/home/tenazas/.openclaw
      - /var/run/docker.sock:/var/run/docker.sock
      - /volume1/docker/piper/data:/home/tenazas/piper
      - ./startup.sh:/home/tenazas/startup.sh
      - ./auto-heal.sh:/home/tenazas/auto-heal.sh
      - ./ollama-check.sh:/home/tenazas/ollama-check.sh
      - ./tests:/home/tenazas/tests
      - ./workspace/runtime:/home/tenazas/runtime
      - ./himalaya-config:/home/tenazas/.config/himalaya
      - ./gcal-token.json:/home/tenazas/.openclaw/gcal-token.json:ro
    command: ["bash", "/home/tenazas/startup.sh"]
```

### Dockerfile
- Base: `node:22-bookworm`
- Deps: python3, pip, ffmpeg, curl, nmap, iputils-ping, dnsutils, docker.io
- Python: requests, torch (CPU), whisper, google-auth
- User: `tenazas` (uid 1026), grupo docker
- Entrypoint: `openclaw gateway`

### Volumes (paths dentro del container)
| Host | Container | Notas |
|---|---|---|
| `./state` | `/home/tenazas/.openclaw` | Config, credentials, sessions |
| `/var/run/docker.sock` | `/var/run/docker.sock` | Control de Docker |
| `./workspace/runtime` | `/home/tenazas/runtime` | Runtime layer |
| `./tests` | `/home/tenazas/tests` | Test suites |
| `./himalaya-config` | `/home/tenazas/.config/himalaya` | Email config |
| `./gcal-token.json` | `...gcal-token.json:ro` | Calendar (read-only) |

---

## Cron jobs

### DSM Task Scheduler (host NAS)
| Frecuencia | Comando | Estado |
|---|---|---|
| Cada 5 min | `docker exec openclaw python3 /home/tenazas/runtime/runtime.py` | **ACTIVO** |
| Cada 15 min | `docker exec openclaw bash /home/tenazas/auto-heal.sh` | DESHABILITADO |
| Domingos 21:00 | `self-evolve.py` | ACTIVO |
| Semanal | `learn.py` | ACTIVO |

### OpenClaw internal cron (jobs.json)
| Job | Schedule | Descripcion |
|---|---|---|
| infra-monitor | Cada 30 min | Health check completo |
| morning-greeting | 8AM daily | Saludo + status |
| email-check | Cada hora (8-23h) | Digest de emails |
| security-alert-monitor | Cada 15 min | Alertas de seguridad |

### RAG ingestion
```
0 */6 * * *  python3 ingest.py  # Cada 6 horas
```

---

## VPN y red

- **ISP:** Movistar Argentina
- **VPN:** NordVPN via gluetun → servidor `es247.nordvpn.com` (Espana)
- **IP esperada VPN:** 82.140.x.x (Espana)
- **IP real publica:** 206.223.x.x (Argentina/Movistar)
- **Proxy gluetun:** 192.168.2.2:8888
- **Leak detection:** Si VPN IP resuelve a country=AR → reiniciar gluetun
- **Red LAN:** 192.168.2.0/24, ~40 dispositivos

### Dispositivos conocidos
- 192.168.2.1 — Router
- 192.168.2.2 — NAS principal (DS923+)
- 192.168.2.3 — NAS backup
- 192.168.2.11 — Hue Bridge
- 192.168.2.46 — Samsung M8
- 192.168.2.76 — Notebook MSI (Ollama GPU)

---

## Reglas para trabajar en este repo

### Secrets
- **Nunca hardcodear API keys** — leerlas desde `state/openclaw.json` o env vars
- **No loguear bot tokens** — solo el chat ID es OK
- Secrets viven en: `openclaw.json`, env vars de compose, `credentials/`, `himalaya-config/`

### Docker
- **No reiniciar `openclaw` ni `watchtower`** via scripts automaticos
- Los scripts del host usan `docker exec openclaw bash <script>`
- Despues de cambios en compose: `docker compose up -d`
- El docker_wrapper.py es la UNICA via segura para ejecutar comandos Docker desde el runtime

### Skills
- Al modificar skills (`.md`): actualizar `SOUL.md` si cambia routing o triggers
- Al modificar `query.py` o `ingest.py`: re-indexar con `python3 ingest.py`
- Skills nuevas requieren SKILL.md + scripts + entry en agent-dispatcher si aplica

### Codigo
- Preferir `subprocess.run([...])` con lista (NO `shell=True`)
- Container names siempre validados contra regex + blocklist
- Logs en JSON (una linea por entry) para parseabilidad

---

## Tests

### Paths dentro del container
```
auto-heal.sh    → /home/tenazas/auto-heal.sh
ollama-check.sh → /home/tenazas/ollama-check.sh
tests/          → /home/tenazas/tests/
runtime/        → /home/tenazas/runtime/
```

### Correr todo
```bash
docker exec openclaw bash /home/tenazas/tests/run_tests.sh
```

### Correr por suite
```bash
docker exec openclaw python3 /home/tenazas/tests/test_runtime.py       # 54 tests
docker exec openclaw python3 /home/tenazas/tests/test_query.py         # 34 tests
docker exec openclaw python3 /home/tenazas/tests/test_confidence.py    # 40 tests
docker exec openclaw python3 /home/tenazas/tests/test_orchestrator.py  # 43 tests
docker exec openclaw python3 /home/tenazas/tests/test_advanced.py      # 41 tests
docker exec openclaw python3 /home/tenazas/tests/test_intelligence.py  # 33 tests
docker exec openclaw python3 /home/tenazas/tests/test_compact.py       # 11 tests
docker exec openclaw bash   /home/tenazas/tests/test_auto_heal.sh
docker exec openclaw bash   /home/tenazas/tests/test_ollama_check.sh
```

### Desde Windows (via SSH al NAS)
```bash
ssh -p 4822 sbasadre@192.168.2.2 '/usr/local/bin/docker exec openclaw python3 /home/tenazas/tests/test_runtime.py'
```

### Que cubre cada suite

**test_runtime.py** (54 tests — unittest + mock)
- Wrapper: sanitizacion de nombres, whitelist de acciones, logging, docker ps parsing
- Eventos: container_down/recovered, internet_lost/restored, vpn_down/leak/changed, device_count, ollama
- Tasks: container → restart, vpn_down → gluetun, vpn_leak → gluetun, deduplicacion, prioridad gluetun
- Patterns: deteccion, throttling, no-trigger doble
- VPN: country check (ES/AR/??)
- Ollama: status file (ok/offline)
- Telegram: notify ok/fail
- Notificaciones: build message, ollama offline
- Integracion: two-run detect container_down
- Cooldown: skip recent, allow after expiry, ignore failed, per-target, vpn_down/leak

**test_query.py** (34 tests — unittest + mock HTTP)
- source_boost(), recency_boost(), reranking con todas las senales
- Confidence scoring integrado en pipeline (dict return)
- Context compression: dedup, top_k, truncation
- Memory fusion: classify procedural/semantic/episodic, working memory injection
- Query rewrite: short skip, LLM fallback, successful rewrite
- Score filtering: MIN_SCORE=0.6, SKILL.md > memory, JSON output

**test_confidence.py** (40 tests — unittest + mock)
- Source classification: skill, core_doc, working, memory_recent/old, unknown
- Confidence scoring: high/medium/low, components, normalized 0-1
- Self-check: answer/caveat/expand/clarify strategies
- Action mapping: vpn_down, container_down, internet_slow, ollama, no-match
- Event to action: maps runtime events, deduplicates
- Infra diagnostics: healthy, container_down, internet_down, vpn_leak, crash_loop, high_latency
- Diagnostic report formatting, quick_health_check, severity sorting

**test_advanced.py** (41 tests — unittest)
- Metrics: recording, aggregation (avg confidence, distributions, latency, escalation rate, timer)
- Infra memory: pattern detection, confidence calculation, update/save, target filtering
- Runbooks: generation por tipo (crash loop, VPN, network, ollama), save/get/list, formatting
- Multi-agent: full pipeline mock, offline fallback, verification failure continues
- Self-optimizer: preferences, escalation detection, expand retrieval, preferred model

**test_intelligence.py** (33 tests — unittest)
- Context manager: budget allocation, prune low value, budget respect, prioritize sources
- Decision trace: create/get/filter entries, escalations, summary
- Agent conflict: agreement/disagreement, contradiction detection, multi-agent check
- Retrieval quality: good/poor/empty results, diversity scoring
- Knowledge graph: nodes, dependencies, dependents, impact analysis, build
- Failure simulator: scenarios (crash loop, network, VPN), run, list
- Experiment engine: create, record results, analyze winner, list

**test_orchestrator.py** (43 tests — unittest)
- Classification: chat, knowledge, infra, automation, diagnostic, reasoning, context boost
- Model selection: per-category, GPU offline fallback, cloud offline fallback, all offline
- Pipeline assembly: modules per category, unknown defaults
- Route query: complete output, logging, context signals, temperature tuning
- Escalation: knowledge→reasoning, chat→knowledge, infra→reasoning, logs, model change
- Context signals: events, ollama_offline, previous_classification

**test_compact.py** (unittest + mock HTTP)
- save_compact(), generate_summary, print_anchor

**test_auto_heal.sh** (bash + mocks)
- VPN leak, gluetun down, container caido, lock file, ollama status

**test_ollama_check.sh** (bash + mock curl)
- Routing por backend, cache 120s, --json valido

---

## Acceso SSH

```bash
ssh -p 4822 sbasadre@192.168.2.2    # NAS desde Windows
# Key auth configurado desde MSI (w4rchild@MSI)
# Docker path en NAS: /usr/local/bin/docker
```

---

## Archivos NO commiteados (por diseno)

Excluidos via `.gitignore`:
- `openclaw.json` — bot token + API keys
- `gcal-creds.json` / `gcal-token.json` — OAuth
- `credentials/` — API keys (gemini, google, anthropic, elevenlabs)
- `state/` — runtime state (sqlite, sessions, memory, identity)
- `himalaya-config/` — email credentials
- `*.bak` / `*.bak2` — backups de compose
- `media/` — audio files
- `workspace/runtime/*.json` — state files regenerados
- `workspace/runtime/logs/` — runtime logs
