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

HomeLab (192.168.2.6) — GPU remota
└── Ollama (:11434) — RTX 3090, modelos principales
    ├── qwen2.5:7b        — Tareas de sistema y razonamiento
    ├── deepseek-r1:14b   — Razonamiento complejo
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
| Asistente | OpenClaw (Node.js 24, Bookworm) | Container `openclaw` |
| GPU inference | Ollama (RTX 3090) | 192.168.2.6:11434 |
| CPU inference | Ollama (Ryzen, fallback) | 127.0.0.1:11434 |
| Cloud inference | Google Gemini 2.5 Flash/Pro | API (fallback final) |
| Cloud inference | Kimi K2 via OpenRouter | API (gateway provider + self-evolve + fallback) |
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
- **qwen2.5:7b** (local, HomeLab GPU) — modelo por defecto
- **Gemini 2.5 Flash** (Google Cloud, gratis) — fallback cloud en openclaw.json
- **Gemini 2.5 Pro** — razonamiento cloud (alias: "reasoning")

### Modelos en HomeLab (192.168.2.6, Ollama)
| Modelo | Parametros | VRAM | Rol | Uso |
|---|---|---|---|---|
| qwen2.5:7b | 7B | 4.7 GB | `fast` | Chat, sistema, tareas rapidas |
| deepseek-r1:14b | 14B | 9.0 GB | `reason` | Analisis, debugging, reflexion |
| qwen2.5:32b | 32B | 20.0 GB | `heavy` | Tareas criticas, contexto largo, sintesis |
| qwen2.5-coder:14b | 14B | 9.0 GB | `code` | Generacion, refactor, review de codigo |
| qwen2.5vl:7b | 7B | 6.0 GB | `vision` | Input visual, imagenes |
| qwen3:8b | 8B | 5.2 GB | `fast` | Alternativa a qwen2.5:7b |
| nomic-embed-text | 137M | 0.3 GB | `embed` | Embeddings para RAG |

**VRAM:** RTX 3090 tiene 24 GB. Modelos > 24 GB funcionan via partial CPU offloading de Ollama (mas lento pero operativo).

### Modelos legacy (NAS CPU solamente)
| Modelo | Uso |
|---|---|
| qwen2.5:3b | Sistema, JSON (solo si HomeLab offline) |
| phi4-mini | Fallback liviano (solo NAS CPU) |

### Router V2 — Routing por rol (`model_router.py`)

Sistema de routing inteligente basado en roles. Clasifica el prompt por heuristicas y selecciona el modelo optimo.

**Roles y fallback chains:**
| Rol | Modelo primario | Fallback | Cuando se usa |
|---|---|---|---|
| `fast` | qwen2.5:7b | qwen3:8b | Chat general, skills rapidas |
| `reason` | deepseek-r1:14b | qwen2.5:7b | Analisis, debugging, decisiones |
| `heavy` | qwen2.5:32b | deepseek-r1:14b → qwen2.5:7b | Tareas criticas, contexto largo |
| `code` | qwen2.5-coder:14b | qwen2.5:7b | Generacion/refactor de codigo |
| `vision` | qwen2.5vl:7b | qwen2.5:7b | Input con imagen |
| `embed` | nomic-embed-text | — | Embeddings RAG |
| `cloud` | moonshotai/kimi-k2 | google/gemini-flash-1.5 | Cloud fallback cuando local offline |

**Clasificacion automatica (heuristicas):**
1. Flag explicito (`flags["role"]`) — override directo
2. `has_image` → vision
3. `embed` flag → embed
4. Keywords de codigo (refactor, debug, backticks, `def`, `class`) → code
5. Keywords de razonamiento (analiza, evalua, why, explain step by step) → reason
6. Keywords criticos (critical, production, deploy) o prompt > 2000 chars → heavy
7. Default → fast

**API:**
```python
from runtime.model_router import classify_task, route_by_role

# Clasificar
role = classify_task("refactor the auth module")  # → "code"

# Routing completo
result = route_by_role("analiza los logs de error")
# → {"model": "deepseek-r1:14b", "role": "reason", "backend": "notebook_gpu",
#     "fallback_used": False, "vram_swap": True, ...}

# Con override
result = route_by_role("do this", flags={"role": "heavy", "critical": True})

# V1 legacy (sigue funcionando)
result = select_model("system")  # → filtra por VRAM, solo modelos que caben
backend, model, url = route("reason")
```

**Logging:** Cada decision se loguea en `logs/model_router.log` (JSONL):
```json
{"ts": "...", "action": "route_v2", "task_type": "code", "model_chosen": "qwen2.5-coder:14b", "role": "code", "fallback_used": false, "vram_swap": true, "signals": ["keyword:refactor"]}
```

**Cloud models (OpenRouter):**
| Modelo | Uso | Costo (per Mtok) |
|---|---|---|
| moonshotai/kimi-k2 | General, reasoning, code, analysis | $0.60 in / $2.40 out |
| google/gemini-flash-1.5 | Fast cloud fallback | Variable |

**`call_openrouter(prompt, model, max_tokens)`** — helper stdlib para llamar OpenRouter directamente (usa `OPENROUTER_API_KEY` env var).

**Integracion futura con idle learning:** El formato de log permite que `idle_learning_engine.py` analice patrones de routing (tasa de fallback, frecuencia de VRAM swap, distribucion por rol) sin cambios adicionales.

**Guardrails (proteccion contra abuso de modelos pesados):**
Modelo pesado (`qwen2.5:32b`) tiene protecciones automaticas (con RTX 3090 cabe en VRAM pero es costoso de cargar):

| Guardrail | Config | Efecto |
|---|---|---|
| Cooldown | 120s despues de usar heavy model | heavy→reason, code→fast |
| Frecuencia | Max 3 heavy calls / 10 min | heavy→reason |
| Latencia | Si ultimo heavy > 30s | heavy→reason |
| VRAM storm | 3+ swaps / 10 min | heavy→reason |

- **Bypass:** `flags={"force_heavy": True}` salta todos los guardrails
- **Degradacion:** heavy→reason→fast (nunca bloquea, siempre responde)
- **Scope:** solo afecta roles `heavy` y `code` (fast/reason/vision/embed no tienen guardrails)
- **State persistente:** `guardrail_state.json` — sobrevive restarts del cron, rolling window automático
- **Post-inferencia:** `report_inference_complete(model, latency)` cierra el loop de feedback
- **Orchestrator:** `report_inference(model_id, latency, route_result)` — wrapper para llamar post-LLM
- **Log:** señal `guardrail:<reason>` en `signals` del log entry
- **Prometheus:** `guardrail_degradations_total{reason}`, paneles Grafana por motivo
- `get_guardrail_stats()` → counters de degradaciones por tipo
- `reset_guardrails()` — reset manual (testing)

**Limitaciones conocidas:**
- Clasificacion por heuristicas (sin LLM) — puede clasificar mal prompts ambiguos
- Con RTX 3090 (24GB) todos los modelos caben en VRAM, VRAM swap no aplica en operacion normal
- En NAS CPU solo modelos <= 3.5 GB VRAM (phi4-mini)
- Guardrails: state file puede quedar stale si el proceso crashea mid-write (unlikely)

### Idle Learning Engine (`idle_learning.py`)

Motor de aprendizaje colaborativo multi-modelo. NO auto-aplica cambios.

**Pipeline:**
```
load_recent_decisions()     → model_router.log + guardrail_state.json
run_critic(deepseek-r1:14b) → analiza decisiones, detecta problemas
run_synthesizer(qwen2.5:32b)→ genera propuestas estructuradas
save_proposals()            → learning/proposals.json (dedup, validacion)
generate_report()           → learning/learning_report.md
```

**Ejecucion manual:**
```bash
docker exec openclaw python3 /home/tenazas/runtime/idle_learning.py
```

**Output:**
- `learning/proposals.json` — propuestas con status="proposed", nunca auto-aplicadas
- `learning/learning_report.md` — reporte legible con problemas, propuestas, metricas
- `logs/idle_learning_v2.log` — telemetria JSONL de cada ejecucion

**Tipos de propuestas:** routing_adjustment, guardrail_tuning, threshold_change, classification_fix

**Review workflow (proposals.json):**
```
proposed → reviewed → accepted | rejected
```
- Cada propuesta tiene un ID estable (sha256[:12])
- `review_proposal(id, "reviewed", "nota")` — marcar como revisada
- `review_proposal(id, "accepted"|"rejected", "nota")` — decision final
- `list_proposals(status="proposed")` — filtrar por estado
- `get_review_summary()` → {total, proposed, reviewed, accepted, rejected}
- Campos enriquecidos: `reviewed_at`, `decision`, `reviewer_note`
- Transiciones invalidas retornan False (no se puede saltar review, no se puede revertir)

**Seguridad:** nunca modifica archivos existentes, nunca cambia config del router, solo escribe en `learning/`

### Daily Improvement — Mejora diaria (`daily_improvement.py`)

Genera UNA propuesta de mejora concreta por dia escaneando todo el ambiente.

**Fuentes escaneadas:**
- `model_router.log` — fallbacks, degradaciones, VRAM swaps, role imbalance
- `guardrail_state.json` — cooldown agresivo, latencia alta
- `finance/transactions.json` — gastos sin categorizar
- `jobs/opportunities.json` — oportunidades stale, tasa de rechazo
- `orchestrator.log` — cloud fallbacks, gaps de observabilidad

**Pipeline:** `scan_signals() → prioritize → select_best → generate_proposal(qwen2.5:7b) → save + report`

**Priorizacion:** alto impacto > bajo riesgo > evidencia suficiente. Evita repetir propuestas recientes (14 dias).

**Ejecucion:**
```bash
docker exec openclaw python3 /home/tenazas/runtime/daily_improvement.py
```

**Output:** `learning/daily_improvement.json` (historial) + `learning/daily_improvement.md` (propuesta del dia)

**Futuro:** cron a las 6AM (antes del morning greeting a las 8AM)

### Daily Brief — Brief diario para Telegram (`daily_brief.py`)

Agrega datos de finanzas, gastos, jobs, mejora diaria y alertas en un brief conciso.

**Secciones (cada una degrada silenciosamente si falta):**
1. Finanzas del mes (income, expenses, balance, top categorias)
2. Gastos de hoy (total, count, top categoria)
3. Alertas activas (expense thresholds + finance alerts)
4. Pipeline laboral (por estado, reciente)
5. Mejora del dia (area, propuesta)
6. Accion recomendada (prioriza: alertas > jobs stale > gasto alto > mejora)

**Ejecucion:**
```bash
docker exec openclaw python3 /home/tenazas/runtime/daily_brief.py
docker exec openclaw python3 /home/tenazas/runtime/daily_brief.py --json
```

**Ejemplo de salida:**
```
Buenos dias Sebas — viernes 04/04

Finanzas del mes: $1,200,000 ingreso, $450,000 gasto, balance $750,000
  Top: supermercado($120,000), transporte($80,000)
Hoy: $15,000 en 3 gastos — mayor: transporte

Busqueda: 2 applied, 1 interview
  Reciente: TIVIT — TAM (applied)

Mejora [guardrails]: reducir cooldown de 120s a 60s

>> Revisar oportunidades detectadas y decidir cuales postular

(sin datos: improvement)
```

**Futuro:** integrar con morning greeting (8AM) para envio automatico por Telegram

### Memory Dreaming — Consolidacion REM (`dreaming.py`)

Motor de consolidacion de memoria en idle. Lee eventos episodicos recientes, extrae "lasting truths" via LLM, y las escribe a `learning/dreams.md` de forma replay-safe (sin duplicados).

**Flujo:**
```
load_recent_episodic() → memory_log.json + shared_memory.json
_extract_truths(LLM)  → qwen2.5:7b (GPU) o phi4-mini (NAS CPU fallback)
_write_to_dreams()     → learning/dreams.md (dedup por content hash)
_log_session()         → learning/dream_log.json
```

**Ejecucion:**
```bash
docker exec openclaw python3 /home/tenazas/runtime/dreaming.py
docker exec openclaw python3 /home/tenazas/runtime/dreaming.py --force
```

**Output:**
- `learning/dreams.md` — staging area con lasting truths (nunca se auto-borra)
- `learning/dream_log.json` — historial de sesiones REM
- `logs/dreaming.log` — telemetria JSONL

**Integracion:** se ejecuta automaticamente al final de `idle_learning_engine.analyze_idle()` y despues de `self-evolve.py`. Tambien cron standalone Wed/Sat 3AM.

**Seguridad:** nunca modifica archivos existentes, solo append a dreams.md. Dedup via sha256 hash.

### Exec Approvals — Telegram Inline Keyboard (`exec_approvals.py`)

Aprobacion de acciones via botones interactivos de Telegram (reemplaza "escribi SI").

**API:**
- `send_approval_request(summary, details, skill)` → envia mensaje con botones Aprobar/Cancelar/Ver detalles
- `handle_callback_query(callback)` → procesa tap de boton
- `get_approval_status(id)` / `poll_approval(id)` → consulta estado

**Estado:** `pending_approvals.json`. Timeout 5 min. Log: `logs/exec_approvals.log`.

### Telegram Router (`telegram_router.py`)

Bot de polling Telegram que intercepta skills deterministicamente antes del LLM.

**Skills interceptadas:** email, weather, smart-home, network-scanner, arr-monitor, infra-monitor, calendar, morning-greeting, self-evolve, reminders, iol_broker.

**Funcionalidades:**
- Clasificacion via `orchestrator.classify_query()`
- IOL broker: ejecuta `iol_handler_cli.py` directamente
- Model routing V2 para queries no-skill (selecciona modelo por rol)
- Context injection para queries analiticas (inyecta logs reales)
- Callback query handling para exec_approvals (botones inline)
- Formateo de email: parsea tabla himalaya a formato legible
- Fallback Markdown → texto plano si Telegram rechaza

### Multitool Inference — Ejecucion multi-tool (`multitool_parser.py` + `orchestrator.py`)

Permite que un modelo finetuned seleccione multiples tools en una sola query via XML.

**Parser XML:** `parse_multitool_response(text)` → `{valid, tools, error}`
- Allowlist cerrada de 14 tools
- Dedup preservando orden, max 3 tools
- Rechaza texto fuera del XML

**Registry:** `TOOL_REGISTRY` mapea tool XML → `skill_category` del orchestrator.

**Policy:** `should_use_multitool(query)` → True solo si 2+ dominios + conector ("y", "ademas").

**Orchestrator:** `route_and_execute_multitool(query)` — llama modelo multitool, parsea XML, ejecuta skills en orden, agrega resultados. Fallback a `route_and_execute()` si falla.

**Dataset:** `tool_tuning/build_multitool_dataset.py` genera 201 ejemplos JSONL (80 single, 81 multi, 20 no-tool, 20 ambiguos).

**Training:** `tool_tuning/train_multitool.py` — LoRA/QLoRA para Qwen2.5-7B-Instruct, RTX 3090.

**Eval harness:** `tool_tuning/eval_runner.py` — precision, recall, F1, hallucination rate, per-tool F1. Exporta a Prometheus :9107. Dashboard Grafana `tenazas-multitool-eval`.

### Personal Supervisor — Orquestación unificada (`personal_supervisor.py`)

Capa que coordina dominios personales. Clasifica intención del usuario, delega al módulo correcto, combina respuestas multi-dominio.

**Dominios:** finance, expense, jobs, improvement, overview

**Clasificación por keywords:**
```
"cuál es mi balance"          → [finance]
"en qué se me va la plata"   → [expense]
"qué empresas tengo activas"  → [jobs]
"cómo vengo este mes"          → [overview] → [finance, expense, jobs, improvement]
"qué debería priorizar hoy"   → [overview] → todos
```

**CLI:**
```bash
python3 /home/tenazas/runtime/personal_supervisor.py --ask "cómo vengo este mes"
python3 /home/tenazas/runtime/personal_supervisor.py --status
python3 /home/tenazas/runtime/personal_supervisor.py --classify "cuánto gasté"
```

**API:** `handle_request(text)` → `{query, domains, results, response, missing}`

**Contrato con módulos:** cada handler retorna `(data_dict, formatted_text)` o `(None, None)`. El supervisor nunca modifica datos — solo lee y combina.

### Operational Memory — Memoria operativa (`operational_memory.py`)

Memoria estructurada por dominio para contexto personal entre interacciones.

**Dominios:** finance, expense, jobs, general

**Tipos de facts:** preference, pattern, entity, threshold, note, context

**API:**
```python
from runtime.operational_memory import save_fact, get_context, list_recent, search_facts

save_fact("finance", "threshold", "Alerta si super > 80k",
          metadata={"category": "supermercado", "amount": 80000})
save_fact("jobs", "preference", "Prefiere remoto", source="user")
save_fact("expense", "pattern", "Gasta mas los viernes")

facts = get_context("finance")                    # todos los facts de finance
facts = get_context("jobs", fact_type="entity")   # solo entidades de jobs
recent = list_recent("expense", limit=5)          # ultimos 5
results = search_facts("TIVIT")                   # buscar across domains
```

**CLI:**
```bash
python3 /home/tenazas/runtime/operational_memory.py --save finance threshold "Super max 80k"
python3 /home/tenazas/runtime/operational_memory.py --context finance
python3 /home/tenazas/runtime/operational_memory.py --search TIVIT
python3 /home/tenazas/runtime/operational_memory.py --stats
python3 /home/tenazas/runtime/operational_memory.py --cleanup
```

**Dedup:** mismo domain+type+content → actualiza timestamp y metadata, no duplica.
**TTL:** 90 dias por defecto, `ttl_days=0` para facts permanentes. `cleanup_expired()` limpia.
**Cap:** max 200 facts por dominio.

### Finance — Finanzas personales (`finance.py`)

Modulo standalone, desacoplado del runtime. Persistencia en `runtime/finance/transactions.json`.

**Input natural (espanol rioplatense):**
```
"gasté 18500 en supermercado"    → expense $18,500 [supermercado]
"cobré 1200000 de sueldo"       → income $1,200,000 [trabajo]
"pagué 42000 de internet"       → expense $42,000 [servicios]
"compré remedio por 5000"       → expense $5,000 [salud]
```

**Categorias:** supermercado, transporte, servicios, alquiler, salud, educacion, ocio, impuestos, trabajo, ingresos, otros

**Alertas:**
- Gasto individual > $50,000
- Gasto diario total > $100,000
- Categoria > 2x promedio mensual

**CLI:**
```bash
python3 /home/tenazas/runtime/finance.py --add "gasté 18500 en super"
python3 /home/tenazas/runtime/finance.py --summary         # resumen mensual
python3 /home/tenazas/runtime/finance.py --day              # resumen diario
python3 /home/tenazas/runtime/finance.py --alerts           # alertas activas
python3 /home/tenazas/runtime/finance.py --list             # ultimos 20 movimientos
python3 /home/tenazas/runtime/finance.py --list --category supermercado --json
```

**API Python:**
```python
from runtime.finance import add_from_text, summary, check_alerts, format_summary
txn = add_from_text("gasté 18500 en super")   # parse + save
s = summary("month")                           # resumen mensual
alerts = check_alerts()                         # alertas activas
```

**Seguridad:** no conecta a bancos, no ejecuta pagos, no infiere datos sensibles. Solo lee/escribe en `runtime/finance/`.

### Expenses — Captura rapida de gastos (`expenses.py`)

Front-end ultra-rapido para gastos diarios. Comparte storage con finance.py (`finance/transactions.json`).

**Input informal (rioplatense):**
```
"uber 4200"              → $4,200 [transporte]
"almuerzo 8500"          → $8,500 [ocio]
"12k en nafta"           → $12,000 [transporte]
"15 lucas en farmacia"   → $15,000 [salud]
"ayer super 18.000"      → $18,000 [supermercado] (fecha: ayer)
"gasto 12k en nafta"     → $12,000 [transporte] (prefix stripped)
```

**Montos informales:** `12k`→12000, `15lucas`→15000, `12.500`→12500, `$8500`→8500

**Fechas relativas:** `ayer`, `anteayer`, `hoy` (default: hoy)

**CLI:**
```bash
python3 /home/tenazas/runtime/expenses.py --add "uber 4200"
python3 /home/tenazas/runtime/expenses.py --today
python3 /home/tenazas/runtime/expenses.py --week
python3 /home/tenazas/runtime/expenses.py --week --category transporte
python3 /home/tenazas/runtime/expenses.py --reclassify ID transporte
python3 /home/tenazas/runtime/expenses.py --alerts
```

**Alertas semanales:** supermercado>$80k, transporte>$40k, ocio>$50k, servicios>$60k (configurables)

**Integracion:** comparte `finance/transactions.json` con finance.py — gastos registrados desde expenses.py aparecen en finance.py y viceversa.

### Jobs — Busqueda laboral (`jobs.py`)

Modulo de gestion de busqueda laboral con pipeline de estados, analisis de JD, match scoring e interview prep.

**Status machine:**
```
detected → interested → applied → screening → interview → offer → closed
                                                                 → rejected (desde cualquier estado activo)
```

**Input natural:**
```
"postulé a TIVIT para TAM"              → applied [TIVIT, TAM]
"vi un puesto de DevOps en Globant"     → detected [Globant, DevOps]
"me contactó recruiter de Accenture"    → screening [Accenture]
"nueva oportunidad: MercadoLibre - SRE" → detected [MercadoLibre, SRE]
```

**CLI:**
```bash
python3 /home/tenazas/runtime/jobs.py --add "postulé a TIVIT para TAM"
python3 /home/tenazas/runtime/jobs.py --pipeline            # resumen del pipeline
python3 /home/tenazas/runtime/jobs.py --update ID screening  # cambiar estado
python3 /home/tenazas/runtime/jobs.py --list                 # oportunidades activas
python3 /home/tenazas/runtime/jobs.py --analyze "JD text..."  # analisis con deepseek
python3 /home/tenazas/runtime/jobs.py --match ID             # match score con perfil
python3 /home/tenazas/runtime/jobs.py --brief ID             # brief de entrevista
python3 /home/tenazas/runtime/jobs.py --jd ID "full JD"      # guardar job description
```

**LLM-assisted:**
- `analyze_description()` → deepseek-r1:14b extrae requisitos, tecnologias, red flags
- `match_score()` → qwen2.5:7b evalua match candidato-puesto (0-100)
- `interview_brief()` → qwen2.5:7b genera prep sheet para entrevista

**Perfil:** Guardar en `jobs/profile.json` para match scoring automatico.

**Seguridad:** no postula automaticamente, no envia emails, no modifica CVs, no hace scraping. Solo lee/escribe en `runtime/jobs/`.

### Cascada de routing legacy (`ollama-check.sh`)
1. **HomeLab GPU** (192.168.2.6:11434) — preferido, todos los modelos
2. **NAS CPU** (127.0.0.1:11434) — solo modelos chicos (phi4-mini)
3. **Gemini Flash** (cloud) — ultimo recurso

```bash
bash ollama-check.sh --route system   # → BACKEND + MODEL + ENDPOINT
bash ollama-check.sh --route reason   # → usa deepseek-r1:14b si HomeLab online
bash ollama-check.sh --route embed    # → nomic-embed-text
bash ollama-check.sh --json           # → status completo
bash ollama-check.sh --status         # → ok / offline
```

Cache de 120s en `/tmp/ollama-notebook.status` para evitar pings excesivos.

### Configuracion de modelos en OpenClaw
- `openclaw.json` (root) — config para desarrollo
- `state/openclaw.json` — config del container (Ollama apunta a HomeLab)

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

## Skills (25 implementadas)

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
| **iol_broker** | InvertirOnline: portafolio, cotizaciones, compra/venta | iol_client.py, iol_portfolio.py, iol_orders.py, iol_handler.py, mcp_server.py |
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
| **orchestrator-bridge** | MCP bridge: orchestrator skills + status + classify | mcp_server.py |
| **model-trainer** | Pipeline automatizado de fine-tuning multitool (HomeLab SSH) | trainer_orchestrator.py, promote_model.py |
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

### Arquitectura (42+ modulos, reemplaza auto-heal.sh)
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
├── idle_learning_engine.py    — Aprendizaje en periodos idle (metricas pasivas)
├── idle_learning.py           — Multi-model collaborative learning (critic+synthesizer)
├── daily_improvement.py       — Mejora diaria: 1 propuesta concreta por dia
├── daily_brief.py             — Brief diario: finanzas, gastos, jobs, mejoras, accion
├── vision_pipeline.py         — Vision: image→VL extraction→deepseek analysis chain
├── personal_supervisor.py     — Supervisor unificado (clasifica intención, delega, combina)
├── operational_memory.py      — Memoria operativa por dominio (finance, expense, jobs)
├── memory/operational.json    — Persistencia de facts
├── model_distillation.py      — Transferencia de conocimiento entre modelos
├── openrouter_usage.py        — Balance y usage tracking OpenRouter API (stdlib only)
├── dreaming.py                — REM memory consolidation (lasting truths from episodic)
├── exec_approvals.py          — Telegram inline keyboard approvals (never auto-exec)
├── multitool_parser.py        — XML parser + tool registry + multitool policy
├── telegram_router.py         — Telegram polling bot (deterministic skill interception)
├── pending_approvals.json     — State file for exec_approvals (gitignored)
│
│ ── Tool Tuning ──
├── tool_tuning/build_multitool_dataset.py  — 201 ejemplos JSONL
├── tool_tuning/train_multitool.py          — LoRA/QLoRA training script
├── tool_tuning/split_dataset.py            — Train/eval split (80/20)
├── tool_tuning/eval_metrics.py             — Precision, recall, F1, hallucination
├── tool_tuning/eval_runner.py              — Eval runner + Prometheus :9107
├── tool_tuning/Modelfile                   — Ollama Modelfile para modelo finetuned
│
│ ── Learning Output ──
├── learning/proposals.json    — Propuestas generadas (nunca auto-aplicadas)
├── learning/proposals_raw.txt — Respuesta raw de Kimi si falla el parse JSON
├── learning/learning_report.md — Reporte legible de cada analisis
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
│
│ ── Personal ──
├── finance.py                 — Finanzas personales (ingresos, gastos, resumen, alertas)
├── expenses.py                — Captura rapida de gastos diarios (ultra-short input)
├── finance/transactions.json  — Persistencia compartida finance+expenses
├── jobs.py                    — Busqueda laboral (pipeline, analisis JD, match, interview prep)
├── jobs/opportunities.json    — Persistencia de oportunidades
├── jobs/profile.json          — Perfil del candidato para match scoring
└── __init__.py
```

### Loop (cada 5 min via cron)
`lockfile → update_state → detect_events → enqueue_tasks → run_tasks → append_memory → analyze_patterns → trigger_patterns → notify_telegram → release_lock`

### Protecciones del runtime loop
- **Lockfile:** `/tmp/tenazas_runtime.lock` — stale detection por edad de archivo (10 min)
- **SIGALRM watchdog:** hard kill a 120s si el ciclo se cuelga (Linux only)
- **Freeze detector:** heartbeat cada step, alerta si >30s sin progreso + Telegram alert
- **Watchdog thread:** background thread chequea freeze cada 5s
- **Runtime Supervisor:** `runtime_supervisor.py` — wrapper externo que mata subprocess a 120s
- **nmap rate limiting:** `nmap_cache.json` — scan cada 30 min, reutiliza cache entre ciclos
- **Log rotation:** `RotatingFileHandler` — 5MB max, 5 backups
- **Crash capture:** `devin_lite.py` — stacktrace + state + events en `logs/runtime_crash.json`
- **Cooldown:** 20 min por target (no re-reinicia si ya se reinicio)

### Observabilidad (Prometheus-compatible)
- **metrics_exporter.py** — exporta metricas en `:9105/metrics` (si `prometheus_client` instalado)
- **Fallback:** si no hay prometheus, loguea a `logs/runtime_metrics.log` (JSON)
- **Step profiling:** cada fase del runtime se cronometra y exporta
- **Health score:** 0-100 basado en duracion, freezes, restarts
- **Metricas:** runtime_runs_total, runtime_duration_seconds, container_restarts_total, vpn_disconnects_total, runtime_freeze_detected, runtime_health_score
- **metrics_server.py** — proceso persistente que arranca desde startup.sh, polls `metrics_state.json` cada 5s + colecta metricas NAS/Ollama/Telegram/IOL/OpenRouter
- **metrics_state.json** — archivo compartido entre runtime.py (escribe) y metrics_server.py (lee)
- **model_failover.py** — proceso persistente, detecta 429 de Gemini y auto-switch a Ollama qwen2.5:7b
- **Metricas extendidas:** runtime_step_*_seconds, rag_queries_total, rag_low_confidence_total, model_failover_active
- **Metricas Router V2:** router_v2_decisions_total{role}, router_v1_decisions_total, router_v1_fallback_from_v2_total, router_v2_internal_fallbacks_total{role}, router_vram_swaps_total{role,model}, router_cloud_fallbacks_total, router_decision_latency_seconds{role}, router_model_latency_seconds{model,role}
- **Metricas NAS Host:** nas_ram_used_bytes, nas_ram_total_bytes, nas_cpu_load_percent, nas_disk_used_bytes, nas_disk_total_bytes, nas_cpu_temp_celsius (lectura directa de /proc y /sys cada 5s)
- **Metricas Ollama GPU:** ollama_gpu_up, ollama_gpu_latency_seconds, ollama_gpu_models_count (health check cada 60s a OLLAMA_GPU_URL, default http://192.168.2.76:11434)
- **Metricas Telegram:** telegram_messages_total, telegram_messages_today (parseo de telegram_handler.log cada 30s)
- **Metricas IOL:** iol_portfolio_value_ars, iol_portfolio_pnl_day_ars, iol_portfolio_last_success_ts, iol_portfolio_errors_total (API IOL cada 5 min, loguea errores)
- **Metricas OpenRouter:** tenazas_openrouter_balance_usd, tenazas_openrouter_used_usd, tenazas_openrouter_requests_total, tenazas_openrouter_last_check_ts (API OpenRouter cada 60s con cache, falla silenciosamente)
- **Prometheus:** container `prom/prometheus` en network_mode: host, scrape cada 30s, retencion 30d/1GB
- **Grafana:** container `grafana/grafana` en `:3000` (admin/tenazas)
  - **Tenazas Runtime** (`tenazas-runtime`) — 31 panels: infra general, NAS host (CPU/RAM/disco/temp), Ollama GPU (status/latencia/modelos), Tenazas operativo (Telegram/RAG), IOL portfolio (valor/P&L)
  - **Tenazas Agent** (`tenazas-agent`) — dashboard dedicado del agente: routing V1/V2, roles, latencia, guardrails, fallbacks, VRAM, failover, ciclos. Todos los panels scoped a `instance="openclaw"` con descriptions operativas
  - **OpenRouter / Kimi-K2** (`openrouter-kimi-k2`) — 4 panels: gastado total (stat $USD), balance (stat, -1=Unlimited), último check (stat relativo), gasto acumulado (timeseries USD)
  - **Tenazas APIs** (`tenazas-apis`) — Gemini (calls, total, distribución), IOL (portfolio, P&L, cuota, freshness, errors), OpenRouter (gasto, balance), todos los servicios. IOL health: freshness (verde<5m, amarillo<10m, rojo>10m), errors (counter), last success (timestamp)
  - **Tenazas Multitool Eval** (`tenazas-multitool-eval`) — 12 panels: exact match, F1, precision, recall (gauges), hallucination rate, over/under trigger, per-tool F1 (bar gauge baseline vs finetuned), F1 historico (timeseries), delta F1, estado del modelo, umbrales

### Cron recomendado (DSM Task Scheduler)
```bash
# Opcion A: worker supervisor (recomendado — aísla workers como subprocesses)
docker exec openclaw python3 /home/tenazas/runtime/worker_supervisor.py

# Opcion B: supervisor legacy (single subprocess, protege contra cuelgues)
docker exec openclaw python3 /home/tenazas/runtime/runtime_supervisor.py

# Opcion C: directo (SIGALRM se encarga del timeout)
docker exec openclaw python3 /home/tenazas/runtime/runtime.py
```

### Worker Supervisor (worker_supervisor.py)
Ejecuta 5 workers secuencialmente como subprocesses aislados:

| Worker | Timeout | Función |
|---|---|---|
| runtime_worker | 60s | Core loop: state→events→tasks→memory |
| diagnostics_worker | 30s | Pattern analysis, infra diagnostics |
| rag_worker | 30s | RAG retrieval quality check |
| agents_worker | 30s | Skill analytics, proposals, self-dev |
| learning_worker | 30s | Background agents, goals, idle learning |

Si un worker se congela o crashea, los demás siguen ejecutando.
Incluye: lockfile protection, log rotation, heartbeat freeze detection.

### Módulos de soporte
- **log_rotation.py** — rota logs >5MB, mantiene 5 backups
- **worker_heartbeat.py** — heartbeat por worker, freeze detection (60s threshold)
- **network_cache.py** — cache de nmap scans (30min TTL)
- **devin_lite.py** — crash capture + improvement suggestions (Telegram)

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

### orchestrator.py — Central AI routing (integrado con Router V2)
- Clasifica queries en: chat, knowledge_query, infrastructure_issue, automation_command, diagnostic_request, complex_reasoning, openrouter_usage, skill:*
- **Clasificacion lightweight:** keyword + regex rules (sin LLM)
- **Skill classification:** 11 skill categories (skill:weather, skill:email, skill:calendar, skill:smart-home, skill:tts, skill:network-scanner, skill:arr-monitor, skill:infra-monitor, skill:morning-greeting, skill:self-evolve, skill:reminders). Requieren score >= 3 (pattern match) para evitar falsos positivos. Se normalizan a `automation_command` para model selection.
- **Model selection (V2):** delega a `model_router.route_by_role()` para seleccion inteligente por rol
  - Prompt-based: codigo detectado → coder, razonamiento → deepseek, imagen → vision
  - Category-based: complex_reasoning → reason role, chat → fast role
  - V2 flags desde context: `has_image`, `critical`, `embed`, `role`
- **Feature flag:** `USE_ROUTER_V2 = True` — set False para revertir a V1 sin cambios de codigo
- **Fallback seguro:** si V2 falla (backends offline, import error) → V1 automatico
- **Pipeline routing:** cada categoria tiene modulos asignados (rag, diagnostics, action_mapper)
- **Context-aware:** recent_events, working_memory, ollama_available influyen en routing
- **Escalation:** low confidence → escala a pipeline mas capaz (knowledge→reasoning, etc.)
- **OpenRouter en fallback chains:** V1 fallback chains incluyen `openrouter_kimi` antes de `gemini_flash`. Si todos los backends locales offline, intenta OpenRouter antes de Gemini como last resort.
- **Metricas:** `v2_calls`, `v1_calls`, `v1_fallback_from_v2`, `v2_vram_swaps`, `v2_fallbacks`, `v2_role_{role}`
- **Telemetria:** `logs/orchestrator.log` (JSON por linea, incluye `router_v2_role` y `router_v2_model`)
- `route_query(query, context)` → {classification, pipeline, model, parameters, router_v2}
- `route_and_execute(query, context)` → route_query + ejecucion directa si es skill category
- `execute_skill(skill_category, query)` → ejecuta skill y retorna {skill, success, output, raw, destructive, error}
- `escalate(original_route, reason)` → ruta escalada
- `get_routing_metrics()` → snapshot de metricas de routing

### Skill Execution Engine (orchestrator.py)

Motor de ejecucion directa de skills desde el orchestrator.

**SKILL_MAP:** 11 skills con command templates, parsers y handlers.

| Skill | Tipo | Descripcion |
|---|---|---|
| `skill:weather` | command + parser | Open-Meteo → texto legible (WMO codes) |
| `skill:email` | command | himalaya envelope list |
| `skill:calendar` | command | gcal.py (proximos eventos) |
| `skill:smart-home` | handler | Hue lights: on/off/cinema/relax/work + status |
| `skill:tts` | handler | Piper TTS → Telegram audio |
| `skill:network-scanner` | command | nmap -sn LAN |
| `skill:arr-monitor` | command | docker ps arr-stack |
| `skill:infra-monitor` | command | docker ps all |
| `skill:morning-greeting` | handler | Agrega calendar + weather + containers |
| `skill:self-evolve` | command + parser | Lee proposals.json |
| `skill:reminders` | handler | Lista o delega creacion al LLM |

**Timeout:** 30s por skill.

**Parsers:** `_parse_weather()` (WMO codes → español), `_parse_proposals()` (JSON → resumen).

**Handlers:** skills parametrizadas o destructivas tienen handlers custom que extraen accion del query.

**`route_and_execute(query)`** — entry point principal: clasifica, si es skill la ejecuta directamente y retorna output; si no, retorna route normal con `executed=False`.

### MCP Servers (openclaw.json → mcp.servers)

Servidores MCP que OpenClaw spawna como subprocesos stdio. El LLM los ve como tools nativas.

| Server | Script | Tools |
|---|---|---|
| `iol-broker` | `skills/iol_broker/mcp_server.py` | iol_portfolio, iol_balance, iol_cotizacion, iol_dolar, iol_ordenes, iol_comprar_propuesta, iol_vender_propuesta |
| `orchestrator-bridge` | `skills/orchestrator_bridge/mcp_server.py` | execute_skill, classify_query, get_system_status |

**orchestrator-bridge** — Puente entre el gateway Node.js y el orchestrator Python:
- `execute_skill(query)` → llama `route_and_execute()`, ejecuta skills determinísticamente
- `classify_query(query)` → retorna categoría sin ejecutar (chat, skill:weather, iol_broker, etc.)
- `get_system_status()` → `quick_health_check()` de infra_diagnostics (containers, VPN, Ollama)

**Protocolo:** JSON-RPC 2.0 sobre stdio, misma implementación que iol-broker.

### Cloud Fallback Policy (EXPLÍCITA)
La caída a cloud **no es silenciosa**. Cadena: OpenRouter (Kimi K2) → Gemini Flash.

1. **V2→V1 fallback:** Router V2 detecta todos los backends Ollama offline → V1 toma control
2. **V1 chain exhaustion:** Todos los modelos locales no disponibles → intenta OpenRouter Kimi K2 → luego Gemini Flash
3. **All offline last resort:** Ningún backend local responde → OpenRouter si `OPENROUTER_API_KEY` presente → Gemini Flash como último recurso

**OpenRouter como provider en OpenClaw gateway:**
- Configurado en `state/openclaw.json` como provider `openrouter` (api: openai-compatible)
- Modelo: `moonshotai/kimi-k2` con alias `kimi`
- API key via env var `OPENROUTER_API_KEY` (docker-compose.yml)

**Visibilidad:**
- **Log:** `orchestrator.log` con `type: "cloud_fallback"` y motivo explícito
- **Prometheus:** `router_cloud_fallbacks_total` (counter)
- **Grafana:** Panel "Cloud Fallbacks" con threshold naranja en 1, rojo en 5
- **In-memory:** `get_routing_metrics()["v1_fallback_from_v2"]`

**Política:** cloud fallback debe ser raro. Si crece, investigar por qué HomeLab está offline.

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

**Motor dual de analisis:**
1. **Ollama (qwen2.5:3b)** — analisis basico de errores/skills/Docker (motor original)
2. **Kimi K2 (moonshotai/kimi-k2 via OpenRouter)** — analisis profundo semanal si `OPENROUTER_API_KEY` presente

**Flujo Kimi K2:**
- `build_weekly_context()` — recopila logs (errors/warnings), metrics_state, proposals existentes, containers, modelos Ollama, guardrail state. Sanitiza secrets con regex antes de enviar.
- `analyze_with_kimi(context)` — llama OpenRouter (120s timeout, 4096 max_tokens, temp=0.3). Parsea JSON, recupera arrays truncados.
- `save_kimi_proposals(proposals)` — merge con dedup en `learning/proposals.json` (type=`kimi_weekly_analysis`, source=`kimi-k2`, status=`proposed`)
- Notifica a Telegram con resumen de propuestas (prioridad con emojis 🔴🟡🟢)

**Fallback:** si `OPENROUTER_API_KEY` no existe o Kimi falla → flujo Ollama continua intacto.

**Dry run:**
```bash
OPENROUTER_API_KEY=... sudo -E python3 /volume1/docker/openclaw/self-evolve.py --dry-run
```
Ejecuta analisis completo sin guardar ni notificar. Imprime JSON de propuestas en stdout.

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
| OpenRouter | LLM cloud (Kimi K2) | API Key (env var) | Gateway provider, cloud fallback, self-evolve semanal |

### APIs internas (LAN, sin auth)
| Servicio | URL | Uso |
|---|---|---|
| Ollama HomeLab | http://192.168.2.6:11434 | LLM inference (GPU) |
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
      - OPENROUTER_API_KEY=***
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
- Base: `node:24-bookworm`
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

### IOL token refresh
```
*/10 * * * *  docker exec openclaw python3 /home/tenazas/tools/iol_refresh.py
```

### RAG reindex semanal (con reporte)
```
0 4 * * 1  docker exec openclaw python3 /home/tenazas/.openclaw/workspace/skills/rag/reindex_all.py
```

### Knowledge Base (RAG memory)

Archivos de memoria por dominio indexados en ChromaDB (251 chunks):

```
workspace/memory/
├── infra/
│   ├── homelab.md              — nodos, containers, modelos, puertos, cron
│   ├── docker_troubleshooting.md — diagnostico, crash loops, sintomas
│   └── gluetun_vpn.md          — NordVPN, arr-stack dependency, puertos
├── finance/
│   ├── iol_broker.md           — credenciales, endpoints, refresh, queries
│   └── inversiones.md          — plataformas, instrumentos, reglas
├── personal/
│   ├── preferencias.md         — comunicacion, horarios, principios, aprobaciones
│   └── smart_home.md           — Hue lights, Samsung M8, escenas
└── jobs/                       — (futuro: busqueda laboral)
```

Datasets de routing y evaluacion en `workspace/datasets/`:
- `router_examples.jsonl` — 39 ejemplos de routing (input → route + role + tool)
- `eval_set.jsonl` — 24 ejemplos con dificultad (easy/medium/hard)
- `tool_examples.jsonl` — 23 ejemplos de tool selection con reasoning

### IOL Token Auto-refresh (`tools/iol_refresh.py`)

Refresh automatico del bearer token de IOL InvertirOnline.

- `--status`: ver estado del token (valido/vencido, expiracion)
- `--force`: forzar refresh aunque no haya vencido
- Sin args: refresh solo si esta por vencer (2 min margen)
- Fallback: si refresh_token vencio, re-autentica con usuario/password desde openclaw.json
- Si todo falla: notifica por Telegram

`iol_init_token.py` modificado: lee credenciales de openclaw.json (iol_username/iol_password) con fallback a input interactivo.

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
- 192.168.2.6 — HomeLab (Ollama GPU)

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
docker exec openclaw python3 /home/tenazas/tests/test_orchestrator.py  # 65 tests
docker exec openclaw python3 /home/tenazas/tests/test_advanced.py      # 41 tests
docker exec openclaw python3 /home/tenazas/tests/test_intelligence.py  # 33 tests
docker exec openclaw python3 /home/tenazas/tests/test_safety_guard.py   # 62 tests
docker exec openclaw python3 /home/tenazas/tests/test_supervisor.py    # 22 tests
docker exec openclaw python3 /home/tenazas/tests/test_model_router.py  # 93 tests
docker exec openclaw python3 /home/tenazas/tests/test_planner.py       # 35 tests
docker exec openclaw python3 /home/tenazas/tests/test_world_model.py   # 38 tests
docker exec openclaw python3 /home/tenazas/tests/test_shared_memory.py # 39 tests
docker exec openclaw python3 /home/tenazas/tests/test_skill_gen.py     # 34 tests
docker exec openclaw python3 /home/tenazas/tests/test_goal_engine.py   # 30 tests
docker exec openclaw python3 /home/tenazas/tests/test_self_debugger.py # 41 tests
docker exec openclaw python3 /home/tenazas/tests/test_self_dev.py      # 28 tests
docker exec openclaw python3 /home/tenazas/tests/test_idle_learning.py # 27 tests
docker exec openclaw python3 /home/tenazas/tests/test_bg_agents.py     # 27 tests
docker exec openclaw python3 /home/tenazas/tests/test_worker_super.py  # 22 tests
docker exec openclaw python3 /home/tenazas/tests/test_stability.py     # 17 tests
docker exec openclaw python3 /home/tenazas/tests/test_observability.py # 25 tests
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

**test_orchestrator.py** (65 tests — unittest)
- Classification: chat, knowledge, infra, automation, diagnostic, reasoning, context boost
- V1 model selection: per-category, GPU offline fallback, cloud offline fallback, all offline
- Pipeline assembly: modules per category, unknown defaults
- Route query: complete output, logging, context signals, temperature tuning
- Escalation: knowledge→reasoning, chat→knowledge, infra→reasoning, logs, model change
- Context signals: events, ollama_offline, previous_classification
- V2 integration: chat→fast, reasoning→deepseek, code override, vision/critical context flags
- V2 result keys, V2 log format, V2 metrics (v2_calls, vram_swaps, role distribution)
- V2 fallback: offline→V1, feature flag off→V1, explicit backends→V1, exception→V1
- V2 escalation: escalate uses V2 model selection
- Build flags from context: image, critical, role passthrough, unrelated keys ignored
- Routing metrics: reset, copy safety, role distribution tracking

**test_compact.py** (unittest + mock HTTP)
- save_compact(), generate_summary, print_anchor

**test_background_agents.py** (27 tests — unittest)
- infra_watcher: healthy no flags, container down, protected skipped, internet down, high latency, vpn down/leak, ollama offline, missing state, multiple flags
- _should_run: first run, elapsed, not elapsed, bad timestamp, empty
- check_and_run: first run executes all, saves state, skips not-due, exception captured, logs created
- get_agents_status: all listed, never run, after run state, description+interval
- Registry: 3 agents, all callable, all named

**test_idle_learning.py** (27 tests — unittest)
- Metrics confidence: low triggers, high no trigger, insufficient samples, metric value
- Metrics escalation: high rate triggers, low rate no trigger
- Metrics latency: outliers trigger, uniform no trigger
- Metrics model imbalance: imbalanced triggers, balanced/single no trigger
- Log patterns: recurring failure, no failures, recurring critical, missing logs, below threshold
- Confidence trends: declining triggers, stable no trigger, insufficient days, missing ts
- analyze_idle: empty e2e, saves file, dedup, sorted by priority, logs
- get_suggestions: empty, returns saved

**test_self_dev_engine.py** (28 tests — unittest + mock)
- Debug analysis: high error rate proposal, low rate no proposal, timeout threshold, module error, import error, empty reports
- Metrics analysis: low confidence, normal confidence, high escalation, high latency, critical diagnostics, import error
- Idle learnings: high/medium priority proposals, low priority skipped, import error
- generate_proposals: end-to-end, dedup by module+description, max cap, no proposals, preserves history, timestamps
- get_pending/get_all: filters, missing file
- Safety: all proposals require_approval=True and status=pending

**test_self_debugger.py** (41 tests — unittest)
- Load log lines: JSON, non-JSON (raw), missing file, max lines cap
- Error detection: ok=False, error field, keyword matching, clean entries
- Error classification: timeout, connection, permission, not_found, parse, module, general
- Error message extraction: error/stderr/detail/raw fields, action+target fallback, truncation, empty
- diagnose(): empty/missing/healthy log, errors with root cause, most common category, suggestion, summary stats, error rate, max cap, string path
- scan_all_logs(): no dir, empty, all healthy, degraded, unhealthy thresholds, skips self_debug.log, per-file reports

**test_goal_engine.py** (30 tests — unittest)
- Load goals: missing file creates 3 permanent goals, initialized as active, existing preserved
- Save/roundtrip
- Evaluate all met: healthy state → all achieved, metrics populated, last_evaluation set
- Stability goal: low container % not met, internet down, vpn down, container pct metric
- Downtime goal: too many events/restarts not met, within limits achieved
- Accuracy goal: notebook ok, NAS-only ok, both offline not met
- Count today: events, restarts, failed restarts excluded, empty/missing/non-list memory
- get_goal: existing, nonexistent
- get_blocked_goals: all active, achieved excluded
- Empty state: missing state file handled

**test_skill_generator.py** (34 tests — unittest)
- Workflow analysis: recurring patterns, below threshold skipped, old entries skipped, empty/non-list/missing memory
- Entries without events skipped, failed actions excluded
- Proposal creation: handler vs monitor name, required keys, always requires_approval, pending status
- Restart steps require approval, notify fallback, recurrence stored, trigger contains events
- generate_proposals: end-to-end, dedup existing, empty memory, saved to disk, max cap
- Load/save proposals: missing file, roundtrip
- Pending filter: returns only pending
- Approve: pending→approved, nonexistent/already approved/rejected returns false
- Reject: pending→rejected, nonexistent/already rejected returns false
- Safety invariant: proposals ALWAYS require approval

**test_shared_memory.py** (39 tests — unittest)
- Load/save: missing file, existing data, missing types initialized, max entries cap, keeps recent
- add_entry: all 4 types, invalid type rejected, timestamp, source, metadata, append
- query_memory: all entries, keyword filter (case-insensitive, source match), limit, recent-first, invalid type
- clear_working_memory: clears working, preserves other types
- ingest_episodic: from memory_log.json, no duplicates, missing file, non-list, container in content
- ingest_procedural: scan SKILL.md, no duplicates, missing dir, dir without SKILL.md skipped
- get_memory_stats: empty, reflects entries
- JSON helpers: missing file default, corrupt default, roundtrip

**test_world_model.py** (38 tests — unittest + mock)
- get_entity container: list all, specific lookup, missing, empty state
- get_entity device: list all, by IP, by name (case-insensitive), unknown, type field
- get_entity service: list all, specific, unknown
- get_entity model: list all, specific, unknown
- get_entity skill: returns count
- get_entity unknown type: returns empty, logs error
- get_status: required keys, container up/down counts, down_names, internet, empty state safe
- get_topology: subnet, gateway, devices, services url+host
- State loading: missing file, corrupt JSON, valid JSON
- Data integrity: devices/services/models have required fields, known IPs present

**test_planner.py** (35 tests — unittest + mock)
- Known problems: container_down→restart, vpn_down/leak→recovery, internet→diagnostic, ollama→failover, arr→arr_recovery
- Generic plans: unknown problems get diagnose+notify, no approval needed
- Context substitution: container name in targets, arr recovery substitution
- Plan structure: required keys (problem, template, steps, generated_at, requires_any_approval)
- Step structure: required keys (action, target, requires_approval, estimated_duration, description)
- Approval flag: restart plans require approval, diagnostics don't
- get_plan_for_event(): arr vs non-arr routing, vpn events, unknown events, missing type
- list_templates(): returns all templates with descriptions
- PROBLEM_MAP: all mapped templates exist, expected problems covered
- Logging: plan generation logged with template, problem, steps_count

**test_model_router.py** (93 tests — unittest + mock)
- Backend ping: success, failure, timeout, unknown backend, curl flags
- Backend detection: both online, notebook offline, logging
- V1 model selection GPU: system→7b, reason→7b, embed→nomic, prefer_fast, result keys
- V1 model selection CPU: small model constraint (<=3.5 VRAM), fallback, NAS URL
- V1 model selection offline: returns empty, logs failure
- V1 route(): tuple return, offline nones, embed routing
- Legacy models: never selected on GPU
- Helpers: get_model_info (known/unknown), list_models (with/without legacy)
- Catalog integrity: required fields, role field, ROLE_MAP references valid models, HomeLab models
- V2 classify_task: explicit flag, vision/embed/code/reason/heavy keywords, backticks, special chars, long prompt, spanish, default fast, invalid flag
- V2 route_by_role: fast→7b, reason→deepseek, code→coder, vision→vl, heavy→32b, embed→nomic, no backend→empty, CPU fallback, backward compat, pre-fetched status, vram_swap flag, log format, result keys, role override
- Guardrails: no guardrail on fast/reason/vision, heavy/code pass when clean, force_heavy bypass, cooldown degrades heavy→reason and code→fast, cooldown expiry, frequency limit, latency threshold, vram_swap storm, record_heavy_usage tracking, reset, cleanup expired
- Guardrails integration: route_by_role respects cooldown, force_heavy bypasses, degraded signals in result

**test_supervisor.py** (22 tests — unittest + mock)
- Lock acquisition: fresh, concurrent blocked, stale removal, threshold boundary
- Lock release: removes file, no-file safe
- Runtime execution: normal exit (code 0), error exit (code != 0), timeout kill, terminate fallback
- Subprocess safety: uses list args (not shell=True)
- Main flow: lock→run→release, release on error, skip if locked, stale proceeds
- Logging: directory creation, timestamps, message content, append mode
- Constants: timeout range, stale > timeout, script path

**test_safety_guard.py** (62 tests — unittest)
- Blocked actions: docker exec/rm/build/pull, network config, file delete outside
- Protected containers: openclaw, watchtower (case-insensitive, whitespace)
- Arr-stack whitelist: all 10 containers, unknown blocked, no target blocked
- Container name sanitization: injection, backticks, pipes, newlines, dashes
- File operations: read always allowed, write/delete only within runtime dir, path traversal
- Unknown actions: default-deny whitelist policy
- is_safe_container(): helper validation
- Getters: return copies (mutation safety)
- Logging: all validations logged to safety.log with timestamp, action, target, context

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

## Infraestructura — Homelab (GPU Server)
- Nombre: HOMELAB
- IP: 192.168.2.76
- OS: Windows 11 Pro (Build 26200)
- Placa madre: MSI MPG Z690 EDGE WIFI DDR4 (MS-7D31)
- CPU: Intel Core i7-12700KF 3600MHz, 12 cores, 20 threads
- RAM: 16 GB físicos / 15.8 GB disponibles
- GPU: RTX 3090 — 24GB VRAM
- Ollama: http://192.168.2.76:11434
- NUNCA llamar 'notebook' a esta máquina — es un desktop PC

## Infraestructura — NAS
- Nombre: DS923+
- IP: 192.168.2.2
- OS: Synology DSM
- CPU: AMD Ryzen R1600
- Docker host principal
- Tenazas corre acá en container 'openclaw'

## Build Rules
- NUNCA usar --no-cache salvo que haya conflicto de dependencias confirmado
- Comando correcto: docker compose build && docker compose up -d
- --no-cache solo si hay error de versión o dependencia rota
