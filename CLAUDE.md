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
├── Ollama (:11434) — RTX 3090, modelos principales
│   ├── qwen2.5:7b        — Tareas de sistema y razonamiento
│   ├── deepseek-r1:14b   — Razonamiento complejo
│   ├── qwen3:8b          — Alternativa
│   ├── phi4-mini          — Fallback liviano
│   └── nomic-embed-text  — Embeddings para RAG
├── windows_exporter (:9182) — CPU, RAM, disk, network, OS
└── nvidia_gpu_exporter (:9835) — GPU temp, VRAM, utilization, power

Smart Home (LAN)
├── Philips Hue Bridge (192.168.2.11) — 8 luces
└── Samsung M8 Monitor (192.168.2.46) — WebSocket control
```

**20 containers esperados** en operacion normal.

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
- **Conversation history:** sliding window por chat (10 turnos, 10 min TTL, cap 200 chars), usa Ollama `/api/chat` para multi-turn con fallback a single-turn `/api/generate`
- Context injection para queries analiticas (inyecta logs reales, filtra security blocks)
- Callback query handling para exec_approvals (botones inline)
- Fallback Markdown → texto plano si Telegram rechaza
- Error handling robusto en classify_intent (fallback a "chat" si orchestrator falla)
- **Chat system prompt:** `_CHAT_SYSTEM_PROMPT` separado de SOUL.md — anti-hallucination, admite ignorancia, no mezcla temas
- **Live data guardrail:** `_is_unsupported_live_data()` intercepta nafta/dolar/noticias/partidos con early return (sin LLM)
- **Structured composers:** weather/gmail/calendar pasan por `response_composer.py` (determinístico) antes de humanize LLM

### Response Composer (`response_composer.py`)

Compone respuestas naturales desde datos estructurados de skills. Separa DATA de PRESENTACION. Sin LLM, determinístico, rápido y consistente.

**Skills migradas:**

| Skill | Parser | Composer | Query types |
|---|---|---|---|
| weather | `parse_weather_structured()` | `compose_weather()` | temp, condition, rain/cold advice |
| gmail | `parse_gmail_structured()` | `compose_gmail()` | search_contact, count, latest, recent_important |
| calendar | `parse_calendar_structured()` | `compose_calendar()` | today, tomorrow, next_event, count, time_lookup |

**Flujo:** skill raw output → parser → structured dict → composer → texto final. Si composer falla → fallback a humanize LLM → fallback a raw.

**API:**
```python
from response_composer import compose_skill_response
text = compose_skill_response("weather", raw_json, "como esta el clima")
# Returns composed text or None if source not supported
```

### Multitool Inference — Ejecucion multi-tool (`multitool_parser.py` + `orchestrator.py`)

Permite que un modelo finetuned seleccione multiples tools en una sola query via XML.

**Parser XML:** `parse_multitool_response(text)` → `{valid, tools, error}`
- Allowlist cerrada de 14 tools
- Dedup preservando orden, max 3 tools
- Rechaza texto fuera del XML

**Registry:** `TOOL_REGISTRY` mapea tool XML → `skill_category` del orchestrator.

**Policy:** `should_use_multitool(query)` → True solo si 2+ dominios + conector ("y", "ademas").

**Orchestrator:** `route_and_execute_multitool(query)` — llama modelo multitool, parsea XML, ejecuta skills en orden, agrega resultados. Fallback a `route_and_execute()` si falla.

**Dataset:** `tool_tuning/build_multitool_dataset.py` genera ~296 ejemplos JSONL (80 single, 81 multi, 20 no-tool, 20 ambiguos, +95 expansion: network-scanner, tts, infra-monitor, arr-monitor, negativos).

**Training:** `tool_tuning/train_multitool.py` — LoRA/QLoRA para Qwen2.5-7B-Instruct, RTX 3090.

**GGUF Pipeline (HomeLab):**
```
train_multitool.py → LoRA adapter → merge_3b.py (background)
→ convert_to_gguf.bat → tenazas-tools-3b.gguf → ollama create → modelo listo
```
- `full_pipeline.bat` — pipeline completo end-to-end (train→merge→gguf→ollama→test)
- `convert_to_gguf.bat` — conversión obligatoria HF→GGUF (fix: ollama create sobre HF mergeado crashea)
- `merge_3b.py` — merge de LoRA adapter con modelo base
- `Modelfile_3b` — Ollama Modelfile para modelo 3B finetuned
- `compare_models.py` — comparación baseline vs finetuned en eval set
- `setup_hf_cache_machine.ps1` — configura HF_HOME persistente en D:\hf_cache
- `verify_python311.bat`, `verify_gguf_env.bat`, `verify_hf_cache.bat` — validación de prereqs

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

### Dataset Builder — Tool Augmentation (`dataset_builder.py`)

Genera datos sinteticos de entrenamiento para tools con bajo F1. Usa Ollama (qwen2.5:3b) para parafrasear, con fallback a variaciones rule-based.

**CLI:**
```bash
python3 /home/tenazas/runtime/dataset_builder.py augment --tools smart-home-cinema smart-home-on --samples 100
python3 /home/tenazas/runtime/dataset_builder.py augment --seed-file custom_seeds.jsonl
python3 /home/tenazas/runtime/dataset_builder.py build-all   # datasets operacionales (legacy)
python3 /home/tenazas/runtime/dataset_builder.py list         # listar datasets
```

**Output:** `state/workspace/evals/dataset_augmented.jsonl` (ChatML format)

**Tools con seeds hardcodeados:** smart-home-cinema, smart-home-on, self-evolve, calendar, email, weather, infra-monitor, arr-monitor

### Eval Runner — Evaluacion de clasificacion (`eval_runner.py`)

Evalua el orchestrator.classify_query() contra datasets etiquetados. Calcula P/R/F1 por tool con deteccion de acciones smart-home.

**CLI:**
```bash
python3 /home/tenazas/runtime/eval_runner.py --dataset path/to/dataset.jsonl
python3 /home/tenazas/runtime/eval_runner.py --push-to-prometheus
python3 /home/tenazas/runtime/eval_runner.py --json-only
```

**Output:** Console table + `state/workspace/evals/eval_results_{timestamp}.json`

**Recomendaciones automaticas:** F1 < 75% → mas ejemplos, P < R → regex amplio, R < P → faltan variaciones.

### Fine-Tune Pipeline — Entrenamiento end-to-end (`fine_tune_pipeline.py`)

Pipeline de 4 fases que orquesta fine-tuning desde el NAS al HomeLab via SSH.

| Fase | Accion | Confirmacion |
|---|---|---|
| 1. Prepare | Validar dataset, split 90/10, SCP a HomeLab | Si |
| 2. Fine-tune | Unsloth LoRA en RTX 3090 via SSH | Si |
| 3. Deploy | ollama create + test query | Si |
| 4. Eval | eval_runner + comparar F1 + rollback si peor | Auto |

**CLI:**
```bash
python3 /home/tenazas/runtime/fine_tune_pipeline.py --dry-run
python3 /home/tenazas/runtime/fine_tune_pipeline.py --skip-phase 3
python3 /home/tenazas/runtime/fine_tune_pipeline.py --auto-approve
```

**Notificaciones:** Telegram en cada transicion de fase.

**Seguridad:** nunca auto-ejecuta sin aprobacion (salvo --auto-approve). Si F1 no mejora, modelo anterior sigue activo.

### Finetuned Router — Intent classifier via Ollama (`finetuned_router.py`)

Wrapper para el modelo finetuneado. Extrae intent + args con parseo tolerante en 4 niveles. Nunca explota.

**Flujo:** mensaje → Ollama /api/chat → parseo tolerante → intent + args

**Parseo (4 niveles, en orden):**
1. JSON directo (clean)
2. JSON extraido de texto libre (`{...}` embedded)
3. Keyword fallback deterministico (sin LLM)
4. No match → intent=None

**Config:**
- `FINETUNED_MODEL`: modelo Ollama a usar (default: `tenazas-qwen25-7b-v2:latest`)
- `OLLAMA_URL`: endpoint `/api/chat` del HomeLab
- `KNOWN_INTENTS`: lights_on, lights_off, cinema_mode, weather, media_search, infra_check, chat

**API:**
```python
from finetuned_router import route
result = route("prendé las luces del living")
# → {"intent": "lights_on", "args": {"room": "living"}, "_parsed_by": "model_json", "_raw": "...", "_error": None}
```

**Garantia:** siempre retorna dict con claves `intent`, `args`, `_parsed_by`, `_raw`, `_error`. Si Ollama falla → keyword fallback sobre el mensaje original.

### Intent Normalizer — Schema-based slot filling (`intent_normalizer.py`)

Convierte el output de finetuned_router en un `NormalizedCall` validado con skill names exactos del orchestrator.

**Flujo:** `finetuned_router.route()` → `normalize()` → `NormalizedCall` → orchestrator

**NormalizedCall:**
```python
@dataclass
class NormalizedCall:
    intent:        str           # "lights_on", "cinema_mode", etc.
    skill:         str           # "skill:smart-home", "skill:weather", etc.
    action:        str           # "on", "cinema", "current", etc.
    args:          dict          # slots validados con defaults
    confidence:    str           # "high" | "medium" | "low"
    source:        str           # origen del parseo
    missing_slots: list          # slots requeridos faltantes
    valid:         bool          # True si listo para ejecutar
```

**Schemas (10 intents):**

| Intent | Skill | Action | Defaults |
|---|---|---|---|
| lights_on | skill:smart-home | on | brightness=100, room=all |
| lights_off | skill:smart-home | off | room=all |
| cinema_mode | skill:smart-home | cinema | brightness=10, room=living |
| weather | skill:weather | current | location=Buenos Aires, days=1 |
| media_search | skill:arr-monitor | search | type=any |
| infra_check | skill:infra-monitor | check | target=all |
| email | skill:email | digest | count=10 |
| calendar | skill:calendar-agenda | agenda | days=2 |
| self_evolve | skill:self-evolve | run | — |
| chat | chat | respond | — |

**Seguridad:** args fuera del schema son rechazados. None/empty usan default. Intent desconocido → chat.

**Confianza:** model_json=high, extracted=medium, keyword/error=low.

**API:**
```python
from intent_normalizer import normalize, normalize_or_chat
call = normalize(router_output, "prende las luces")     # puede retornar None
call = normalize_or_chat(router_output, "hola")          # nunca None, fallback chat
```

### Smoking Tracker (`smoking.py`)

Registra cigarrillos fumados con timestamp. Telegram triggers + Grafana dashboard.

**Triggers:** "estoy fumando", "pucho", "me fumo uno", "cuanto fume", "mis cigarrillos"

**Storage:** `runtime/smoking/log.json` + `metrics.json`

**Prometheus:** `tenazas_smoking_today`, `tenazas_smoking_week`, `tenazas_smoking_total`, `tenazas_smoking_day{date,label}`, `tenazas_smoking_hour{date,hour}`

**Grafana:** dashboard `tenazas-smoking` — stats + bar por dia + bar por hora + timeline

### Apply Improvements (`apply_improvements.py`)

Procesa propuestas aprobadas de self-evolve. Genera seeds via Ollama para tools con bajo F1.

**Flujo:** `pending_approvals.json` (approved) → genera seeds → `auto_seeds.jsonl` → sincroniza `improvement_queue.json`

**CLI:** `python3 /home/tenazas/runtime/apply_improvements.py`

### ROI Tracker (`roi_tracker.py` + `roi_report.py`)

Mide el valor real que Tenazas aporta: tareas automatizadas, horas ahorradas, errores evitados, uso por skill.

**Source:** `telegram_router.log` (JSONL) — filtra `skill_response` y `llm_response`.

**Metricas:**
- Skill executions vs LLM chats (automation rate)
- Horas ahorradas estimadas por skill (config en `MINUTES_SAVED`)
- Errores evitados (container restarts, VPN fixes, Ollama recoveries)
- Success rate por skill, breakdown diario

**CLI:**
```bash
python3 /home/tenazas/runtime/roi_tracker.py              # snapshot + summary
python3 /home/tenazas/runtime/roi_tracker.py --json        # raw JSON
python3 /home/tenazas/runtime/roi_tracker.py --days 30     # custom period
python3 /home/tenazas/runtime/roi_report.py                # Telegram-formatted report
python3 /home/tenazas/runtime/roi_report.py --send         # enviar a Telegram
```

**Output:** `logs/roi_state.json` — leido por `metrics_server.py` cada 30s.

**Prometheus (13 metricas):** `tenazas_roi_*` — skill_executions, llm_chats, automation_rate, hours_saved, errors_avoided, skill_usage{skill}, skill_success_rate{skill}, skill_hours_saved{skill}

**Grafana:** dashboard `tenazas-roi` — 11 paneles (horas ahorradas, automation rate, uso por skill, success rate, errores evitados, pie chart skills vs LLM)

**Cron:** cada 6h (snapshot) + domingos 20:00 (reporte Telegram)

### Health Sweep (`health_sweep.py`)

Full-stack health sweep: NAS, HomeLab, APIs externas, servicios internos, dashboard cache. 25 probes paralelos con ThreadPoolExecutor.

**Secciones (5):**

| Sección | Probes | Qué chequea |
|---|---|---|
| NAS | docker_socket, containers, chromadb, prometheus, grafana, piper, vpn, ollama_nas | Containers up, VPN leak (ES), servicios locales |
| HomeLab | ollama_gpu, ollama_vram, homelab_tcp, gpu_exporter | Ollama models, VRAM, GPU exporter :9835 |
| APIs | binance_ping, binance_ticker, iol_api, gemini, telegram_bot, openmeteo | Reachability + latencia |
| Interno | freeze_detector, rag, memory_stores, orchestrator, metrics_exporter | Heartbeat, RAG alive, bloat |
| Dashboards | grafana_cache, chromadb_frozen | Cache stale detection |

**Output:** Telegram (scored 0-100 con delta) + SQLite (`state/health_sweeps.sqlite`) + Prometheus gauges `tenazas_health_score{section}`

**CLI:**
```bash
python3 health_sweep.py                # one-shot + Telegram
python3 health_sweep.py --no-telegram  # solo SQLite
python3 health_sweep.py --watch        # loop 5 min
python3 health_sweep.py --json         # JSON output
python3 health_sweep.py --section nas  # sección específica
```

**Cron:** cada 5 min via DSM Task Scheduler. 31 tests.

### Autofix Agent (`autofix_agent.py` + 6 módulos)

Agente autónomo de auto-reparación con 3 tiers de confianza. Lee failures de `health_sweep.py`.

**Arquitectura modular:**

| Módulo | Función |
|---|---|
| `autofix_agent.py` | Orchestrator + CLI + kill switch |
| `autofix_tiers.py` | Regression detection + tier classification |
| `autofix_sandbox.py` | Git ops (branch, diff validation, merge, rollback) |
| `autofix_aider.py` | Aider CLI wrapper con system prompt constrained |
| `autofix_ssh.py` | SSH whitelisted a HOMELAB (solo ollama pull) |
| `autofix_telegram.py` | Notify + approval flow |
| `autofix_llm_check.py` | Ollama/Gemini availability check |

**Tiers:**

| Tier | Tipo | Auto? | Ejemplos |
|---|---|---|---|
| 1 | OPS | Sí (si habilitado) | Restart containers, ollama pull, load model |
| 2 | CODE | Sí (si habilitado) | Aider + Ollama qwen2.5-coder:32b → git branch → test → merge |
| 3 | PROPOSAL | Siempre | Telegram notification, human approval |

**Safety:**
- Kill switch: `state/AUTOFIX_DISABLED` (creado por default al deploy)
- Tiers: T1=off, T2=off, T3=on por default. Habilitar gradualmente.
- Protected files: orchestrator.py, safety_guard.py, docker_wrapper.py, runtime.py, etc.
- Rate limits: 3 restarts/container/hora, 5 merges/día, auto-pause tras 3 fallos consecutivos
- Post-deploy: re-sweep 60s después, auto-rollback si score baja >10 puntos
- Regression detection: new, regression, chronic (→T3), flapping (→skip), gradual (→T3)
- Expected degradations: ollama_nas skip cuando HomeLab alive

**CLI:**
```bash
python3 autofix_agent.py                    # one-shot
python3 autofix_agent.py --dry-run          # clasifica sin actuar
python3 autofix_agent.py --status           # estado actual
python3 autofix_agent.py --force-tier 3     # todo → approval
python3 autofix_agent.py --rollback <sha>   # revert commit
python3 autofix_agent.py --kill             # kill switch ON
python3 autofix_agent.py --enable           # kill switch OFF
```

**Config:** `state/autofix_config.json` — tiers, rate limits, protected files, pullable models, timeouts
**State:** `state/autofix_state.json` + `state/autofix_log.jsonl` (audit append-only)
**Tests:** 54 tests passing.

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

### Arquitectura (42+ modulos)
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
├── tool_tuning/build_multitool_dataset.py  — ~296 ejemplos JSONL (con expansion)
├── tool_tuning/train_multitool.py          — LoRA/QLoRA training script
├── tool_tuning/split_dataset.py            — Train/eval split (80/20)
├── tool_tuning/eval_metrics.py             — Precision, recall, F1, hallucination
├── tool_tuning/eval_runner.py              — Eval runner + Prometheus :9107
├── tool_tuning/Modelfile                   — Ollama Modelfile para modelo finetuned (7B)
├── tool_tuning/Modelfile_3b                — Ollama Modelfile para modelo 3B
├── tool_tuning/merge_3b.py                 — Merge LoRA adapter con base model
├── tool_tuning/convert_to_gguf.bat         — Conversión HF→GGUF (obligatoria para ollama)
├── tool_tuning/full_pipeline.bat           — Pipeline completo: train→merge→gguf→ollama→test
├── tool_tuning/compare_models.py           — Comparación baseline vs finetuned
├── tool_tuning/setup_hf_cache_machine.ps1  — Setup HF_HOME persistente (HomeLab)
├── tool_tuning/verify_python311.bat        — Verifica Python 3.11+
├── tool_tuning/verify_gguf_env.bat         — Verifica llama.cpp + dependencias GGUF
├── tool_tuning/verify_hf_cache.bat         — Verifica HF_HOME configurado
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
├── self_debugger.py           — Auto-diagnostico de errores de Tenazas (filtra security blocks)
├── self_dev_engine.py         — Propuestas de mejora de codigo
├── self_benchmark.py          — Benchmarking de rendimiento
├── dataset_builder.py         — Generacion de datasets operacionales + tool augmentation
├── eval_runner.py             — Eval orchestrator classification vs labeled JSONL datasets
├── fine_tune_pipeline.py      — Pipeline end-to-end: prepare→finetune→deploy→eval (HomeLab SSH)
├── finetuned_router.py        — Intent classifier via Ollama (4-level tolerant parse + keyword fallback)
├── intent_normalizer.py       — Converts router intents to NormalizedCall with skill mapping + slot defaults
├── smoking.py                 — Smoking tracker: log cigarettes, stats, Prometheus metrics
├── smoking/                   — Smoking data: log.json + metrics.json (gitignored)
│
│ ── IoT Devices ──
├── devices/__init__.py        — Device registry, dispatcher, composite actions
├── devices/hue_lights.py      — Philips Hue lights (8 fixtures, 5 scenes)
├── devices/marantz_avr.py     — Marantz CINEMA 60 AVR (HTTP, no auth)
├── devices/hue_sync_box.py    — Hue Sync Box (HTTPS + Bearer token)
├── devices/samsung_tv.py      — Samsung M8 + Q70 (WebSocket)
├── devices/google_cast.py     — Nest Mini + Home Mini (pychromecast)
├── devices/xiaomi_vacuum.py   — Xiaomi S20+ (python-miio)
├── devices/shield_tv.py       — NVIDIA SHIELD (androidtvremote2)
├── devices/ezviz_cameras.py   — EZVIZ cameras (ping only)
├── devices/nest_doorbell.py   — Nest Doorbell (ping only)
├── apply_improvements.py      — Auto-apply approved self-evolve proposals (seeds via Ollama)
│
│ ── ROI Dashboard ──
├── roi_tracker.py             — ROI metrics: skill usage, hours saved, errors avoided
├── roi_report.py              — Weekly ROI report formatted for Telegram
│
│ ── Health & Autofix ──
├── health_sweep.py            — Full-stack health sweep (25 probes, SQLite, Telegram, Prometheus)
├── autofix_agent.py           — 3-tier auto-repair orchestrator (kill switch, rate limits)
├── autofix_tiers.py           — Regression detection + tier classification
├── autofix_sandbox.py         — Git ops (branch, diff validation, merge, rollback)
├── autofix_aider.py           — Aider CLI wrapper (constrained system prompt)
├── autofix_ssh.py             — SSH whitelisted to HOMELAB (ollama pull only)
├── autofix_telegram.py        — Autofix notifications + approval flow
├── autofix_llm_check.py       — Ollama/Gemini backend availability
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
- **Metricas Ollama GPU:** ollama_gpu_up, ollama_gpu_latency_seconds, ollama_gpu_models_count (health check cada 60s a OLLAMA_GPU_URL, default http://192.168.2.6:11434)
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
- **Feature flag:** `tenazas.use_finetuned_router: true` en openclaw.json — activa Router V3 (finetuned_router + intent_normalizer). Si confianza >= medium, usa resultado del modelo. Si falla o low confidence, cae al scoring deterministico. Bajo namespace `tenazas` para no romper el schema del gateway.
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
| `skill:smart-home` | handler | IoT devices via `devices/` dispatcher (9 device types) |
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

## Scripts de infraestructura (host/container)

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
docker exec openclaw python3 /home/tenazas/tests/test_finetuned_router.py # 29 tests
docker exec openclaw python3 /home/tenazas/tests/test_intent_normalizer.py # 42 tests
docker exec openclaw python3 /home/tenazas/tests/test_health_sweep.py     # 31 tests
docker exec openclaw python3 /home/tenazas/tests/test_autofix.py          # 54 tests
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
- IP: 192.168.2.6
- OS: Windows 11 Pro (Build 26200)
- Placa madre: MSI MPG Z690 EDGE WIFI DDR4 (MS-7D31)
- CPU: Intel Core i7-12700KF 3600MHz, 12 cores, 20 threads
- RAM: 16 GB físicos / 15.8 GB disponibles
- GPU: RTX 3090 — 24GB VRAM
- Ollama: http://192.168.2.6:11434
- NUNCA llamar 'notebook' a esta máquina — es un desktop PC

## Infraestructura — NAS
- Nombre: DS923+
- IP: 192.168.2.2
- OS: Synology DSM 7.3.2 (build 86009)
- CPU: AMD Ryzen Embedded R1600
- RAM: 16 GB
- Storage: 27T (volume1) + 1.8T (volume2)
- Docker: 24.0.2
- Docker host principal
- Tenazas corre acá en container 'openclaw'

## Containers Docker (20 en producción)

| Container | Imagen | Función |
|---|---|---|
| openclaw | openclaw-openclaw | Tenazas (asistente principal) |
| gluetun | qmcgaw/gluetun | VPN gateway NordVPN |
| qbittorrent | linuxserver/qbittorrent | Torrent client |
| sonarr | linuxserver/sonarr | TV series |
| radarr | linuxserver/radarr | Movies |
| lidarr | linuxserver/lidarr | Music |
| prowlarr | linuxserver/prowlarr | Indexer manager |
| sabnzbd | linuxserver/sabnzbd | Usenet |
| seerr | seerr-team/seerr | Media requests UI |
| requestrr | darkalfx/requestrr | Media request bot |
| tautulli | tautulli/tautulli | Plex stats |
| chromadb | chromadb/chroma | Vector DB para RAG (:8100) |
| piper | linuxserver/piper | TTS local |
| prometheus | prom/prometheus | Métricas (:9090) |
| grafana | grafana/grafana | Dashboards (:3000) |
| portainer | portainer/portainer-ee | Docker dashboard |
| WATCHTOWER | nickfedor/watchtower | Auto-update imágenes |
| hireswarm-agent-server | dev-agent-pipeline | HireSwarm agente |
| hireswarm-inngest | inngest/inngest | HireSwarm event engine |
| backing-track-generator | backing-track-generator | Generador de backing tracks |

## Build Rules
- NUNCA usar --no-cache salvo que haya conflicto de dependencias confirmado
- Comando correcto: docker compose build && docker compose up -d
- --no-cache solo si hay error de versión o dependencia rota
- PYTHON_HOMELAB = C:\Python314\python.exe (Python 3.11 bloqueado por WDAC)
- GGUF conversion: llama.cpp b4924, outtype q8_0 (no q4_k_m)

<!-- rtk-instructions v2 -->
# RTK (Rust Token Killer) - Token-Optimized Commands

## Golden Rule

**Always prefix commands with `rtk`**. If RTK has a dedicated filter, it uses it. If not, it passes through unchanged. This means RTK is always safe to use.

**Important**: Even in command chains with `&&`, use `rtk`:
```bash
# ❌ Wrong
git add . && git commit -m "msg" && git push

# ✅ Correct
rtk git add . && rtk git commit -m "msg" && rtk git push
```

## RTK Commands by Workflow

### Build & Compile (80-90% savings)
```bash
rtk cargo build         # Cargo build output
rtk cargo check         # Cargo check output
rtk cargo clippy        # Clippy warnings grouped by file (80%)
rtk tsc                 # TypeScript errors grouped by file/code (83%)
rtk lint                # ESLint/Biome violations grouped (84%)
rtk prettier --check    # Files needing format only (70%)
rtk next build          # Next.js build with route metrics (87%)
```

### Test (90-99% savings)
```bash
rtk cargo test          # Cargo test failures only (90%)
rtk vitest run          # Vitest failures only (99.5%)
rtk playwright test     # Playwright failures only (94%)
rtk test <cmd>          # Generic test wrapper - failures only
```

### Git (59-80% savings)
```bash
rtk git status          # Compact status
rtk git log             # Compact log (works with all git flags)
rtk git diff            # Compact diff (80%)
rtk git show            # Compact show (80%)
rtk git add             # Ultra-compact confirmations (59%)
rtk git commit          # Ultra-compact confirmations (59%)
rtk git push            # Ultra-compact confirmations
rtk git pull            # Ultra-compact confirmations
rtk git branch          # Compact branch list
rtk git fetch           # Compact fetch
rtk git stash           # Compact stash
rtk git worktree        # Compact worktree
```

Note: Git passthrough works for ALL subcommands, even those not explicitly listed.

### GitHub (26-87% savings)
```bash
rtk gh pr view <num>    # Compact PR view (87%)
rtk gh pr checks        # Compact PR checks (79%)
rtk gh run list         # Compact workflow runs (82%)
rtk gh issue list       # Compact issue list (80%)
rtk gh api              # Compact API responses (26%)
```

### JavaScript/TypeScript Tooling (70-90% savings)
```bash
rtk pnpm list           # Compact dependency tree (70%)
rtk pnpm outdated       # Compact outdated packages (80%)
rtk pnpm install        # Compact install output (90%)
rtk npm run <script>    # Compact npm script output
rtk npx <cmd>           # Compact npx command output
rtk prisma              # Prisma without ASCII art (88%)
```

### Files & Search (60-75% savings)
```bash
rtk ls <path>           # Tree format, compact (65%)
rtk read <file>         # Code reading with filtering (60%)
rtk grep <pattern>      # Search grouped by file (75%)
rtk find <pattern>      # Find grouped by directory (70%)
```

### Analysis & Debug (70-90% savings)
```bash
rtk err <cmd>           # Filter errors only from any command
rtk log <file>          # Deduplicated logs with counts
rtk json <file>         # JSON structure without values
rtk deps                # Dependency overview
rtk env                 # Environment variables compact
rtk summary <cmd>       # Smart summary of command output
rtk diff                # Ultra-compact diffs
```

### Infrastructure (85% savings)
```bash
rtk docker ps           # Compact container list
rtk docker images       # Compact image list
rtk docker logs <c>     # Deduplicated logs
rtk kubectl get         # Compact resource list
rtk kubectl logs        # Deduplicated pod logs
```

### Network (65-70% savings)
```bash
rtk curl <url>          # Compact HTTP responses (70%)
rtk wget <url>          # Compact download output (65%)
```

### Meta Commands
```bash
rtk gain                # View token savings statistics
rtk gain --history      # View command history with savings
rtk discover            # Analyze Claude Code sessions for missed RTK usage
rtk proxy <cmd>         # Run command without filtering (for debugging)
rtk init                # Add RTK instructions to CLAUDE.md
rtk init --global       # Add RTK to ~/.claude/CLAUDE.md
```

## Token Savings Overview

| Category | Commands | Typical Savings |
|----------|----------|-----------------|
| Tests | vitest, playwright, cargo test | 90-99% |
| Build | next, tsc, lint, prettier | 70-87% |
| Git | status, log, diff, add, commit | 59-80% |
| GitHub | gh pr, gh run, gh issue | 26-87% |
| Package Managers | pnpm, npm, npx | 70-90% |
| Files | ls, read, grep, find | 60-75% |
| Infrastructure | docker, kubectl | 85% |
| Network | curl, wget | 65-70% |

Overall average: **60-90% token reduction** on common development operations.
<!-- /rtk-instructions -->

---

## B+A Roadmap — Completado

### Flujo completo end-to-end

```
infra_monitor detecta problema
  → incident_schema normaliza
  → policy_engine clasifica riesgo (SAFE/LOW/MEDIUM/HIGH)
  → proactive_planner propone accion
  → approval_queue envia a Telegram con botones
  → Sebas aprueba/rechaza
  → skill_chain_executor ejecuta
  → action_validator confirma resultado
  → decision_trace registra
  → error_mining analiza outcome (daily)
  → improvement_candidates genera propuestas
  → self_benchmark --gate evalua
  → queda en staging/ para aprobacion
  → preference_learning aprende de la decision
```

### Modulos B+A (por Sprint)

| Sprint | Modulo | Funcion |
|--------|--------|---------|
| 1 | `action_registry.py` | Registro de acciones con risk levels |
| 1 | `policy_engine.py` | Evaluacion de riesgo antes de ejecutar |
| 1 | `homelab_snapshot.py` | Snapshot unificado del homelab |
| 1 | `homelab_goals.py` | Objetivos evaluables (uptime, availability) |
| 1 | `temporal_guard.py` | Filtro de notificaciones por horario |
| 2 | `device_discovery.py` | Scanner de red + inventario persistente |
| 2 | `deco_client.py` | TP-Link Deco API (WiFi clients, band, signal) |
| 2 | `network_exporter.py` | Prometheus :9108 per-device metrics |
| 3 | `error_mining.py` | Extrae fallas de produccion de logs |
| 3 | `improvement_candidates.py` | Transforma errores en propuestas tipadas |
| 3 | `self_benchmark.py` | Gate de benchmark antes de promover |
| 3 | `staging/` | Pre-produccion aislada (nada auto-aplica) |
| 4 | `preference_learning.py` | Aprende de aprobaciones/rechazos |
| 4 | `skill_gap_detector.py` | Detecta necesidades sin skill asignado |
| 4 | `experiment_runner.py` | A/B con guardrails (10% traffic, rollback) |
| 5 | `homelab_dashboard_payload.py` | Dashboard unificado "estado tenazas" |
| 6 | `infra_predictor.py` | Predice problemas por tendencias (VRAM, RAM, disco, latencia) |
| 6 | `root_cause_engine.py` | Cadenas causales X→Y→Z, fix correcto al root cause |
| 6 | `incident_clustering.py` | Agrupa incidentes similares, detecta patrones ocultos |
| 7 | `improvement_loop.py` | Closed-loop: approved → experiment → validate → staging |
| 7 | `meta_optimizer.py` | Optimiza frecuencia de mining, calidad de senales, trafico |
| 7 | `skill_evolution.py` | Adopcion progresiva: proposed → shadow → 10% → active |
| 7 | `benchmark_continuous.py` | Monitoreo 24/7 con alertas por umbrales |
| 8 | `unified_briefing.py` | Briefing diario unico 08:00 (agenda, email, finanzas, homelab) |
| 8 | `contextual_awareness.py` | Decide cuando interrumpir (calendar, ausencia, critical) |
| 8 | `personal_optimizer.py` | Aprende patrones personales (active hours, skill rhythm) |
| 9 | `agent_infra.py` | Agente infra (containers, GPU, VPN, disco) |
| 9 | `agent_personal.py` | Agente personal (calendar, email, reminders) |
| 9 | `agent_finance.py` | Agente finanzas (Binance, IOL, alertas) |
| 9 | `agent_research.py` | Agente investigacion (SearXNG, RAG) |
| 9 | `agent_self_improve.py` | Agente auto-mejora (mining, experiments) |
| 9 | `agent_arbitrator.py` | Resolucion de conflictos entre agentes |
| 9 | `agent_coordinator.py` | Routing multi-agente, agregacion de respuestas |
| 10 | `trading_observer.py` | Observacion pasiva: precios, F&G, MA, volatilidad |
| 10 | `trading_journal.py` | Journal diario automatico del mercado |
| 10 | `trading_hypothesis.py` | Genera y trackea hipotesis de trading |
| 10 | `trading_paper.py` | Paper trading (simulacion, $10k virtual) |
| 10 | `trading_scorer.py` | Scoring: win rate, profit factor, drawdown, sharpe |

### Comandos Telegram disponibles

| Comando | Funcion |
|---------|---------|
| `estado tenazas` / `dashboard` | Dashboard completo B+A |
| (cron 08:00) | Briefing diario unificado (unified_briefing.py) |
| `estado homelab` / `estado` | Snapshot del homelab |
| `aprobar [ID]` | Aprobar propuesta (exec queue o improvement) |
| `rechazar [ID]` | Rechazar propuesta |
| `scan red` / `escanear red` | Network discovery |

### Politica de staging

| Accion | Auto? | Requiere aprobacion? |
|--------|-------|---------------------|
| Restart arr-stack container | No | Si (Telegram buttons) |
| Restart gluetun (VPN down) | No | Si |
| Improvement candidate → staging | Auto | No (va a staging) |
| Staging → produccion | Nunca auto | Si (Sebas aprueba) |
| Skill gap → propuesta | Auto | No (JSON en staging) |
| Experimento A/B | Auto (10%) | No (guardrails automaticos) |

### Prometheus B+A Metrics (:9105)

| Metrica | Descripcion |
|---------|-------------|
| `tenazas_goals_failing` | Goals en estado fail |
| `tenazas_staging_pending` | Propuestas pendientes en staging |
| `tenazas_experiments_active` | Experimentos A/B corriendo |
| `tenazas_backend_offline_events_24h` | Outages HomeLab en 24h |
| `tenazas_preference_confidence{category}` | Confianza por categoria |

### Grafana Dashboards

| Dashboard | UID | Paneles |
|-----------|-----|---------|
| Tenazas Device Discovery | `tenazas-device-discovery` | Inventario, categorias, vendors, WiFi/Ethernet |
| Tenazas Runtime | `tenazas-runtime` | Infra general, NAS, Ollama, Telegram |
| Tenazas Agent | `tenazas-agent` | Router V2, roles, guardrails |
| Portfolio IOL — Vista Analista | `iol-portfolio-analista` | Valor total, P&L, posiciones (tabla+gain%+PnL+evolución+detalle), evolución histórica, salud API |
| Tenazas ROI Dashboard | `tenazas-roi` | Horas ahorradas, automation rate, uso por skill, success rate, errores evitados, skills vs LLM chat |
| Tenazas Infra — NAS + HomeLab | `tenazas-infra` | CPU/RAM/disk NAS+HomeLab, GPU RTX 3090 (temp/VRAM/util/power/fan/clocks/throttle), Ollama status |

### Tests B+A

```bash
# Sprint 3 — self-improvement pipeline
docker exec openclaw python3 /home/tenazas/tests/test_sprint3.py                          # 26 tests
# Sprint 4 — useful learning
docker exec openclaw python3 /home/tenazas/tests/test_sprint4.py                          # 23 tests
# Sprint 5 — e2e integration
docker exec openclaw python3 /home/tenazas/tests/test_e2e_sprint5.py                      # 15 tests
# Sprint 5 — governance unit tests
docker exec openclaw python3 /home/tenazas/tests/test_sprint5_governance.py               # 64 tests
docker exec openclaw python3 /home/tenazas/tests/test_sprint5_expanded.py                 # 131 tests
# Simulation — governance scenarios
docker exec openclaw python3 /home/tenazas/tests/simulation/governance/test_sim_container_flapping.py  # 10 tests
docker exec openclaw python3 /home/tenazas/tests/simulation/governance/test_sim_ollama_gpu.py          # 7 tests
docker exec openclaw python3 /home/tenazas/tests/simulation/governance/test_sim_noise_storm.py         # 9 tests
docker exec openclaw python3 /home/tenazas/tests/simulation/governance/test_sim_multi_incident.py      # 10 tests
# Long-run simulations
docker exec openclaw python3 /home/tenazas/tests/simulation/longrun/test_longrun_1h.py                # 10 tests
docker exec openclaw python3 /home/tenazas/tests/simulation/longrun/test_longrun_degradation.py        # 9 tests
# Hardening — adversarial, policy, chains, regression
docker exec openclaw python3 /home/tenazas/tests/test_adversarial_governance.py            # 34 tests
docker exec openclaw python3 /home/tenazas/tests/test_policy_enforcement.py                # 32 tests
docker exec openclaw python3 /home/tenazas/tests/test_cross_failure_chains.py              # 18 tests
docker exec openclaw python3 /home/tenazas/tests/test_self_improvement_regression.py       # 32 tests
# Sprint 6 — operational intelligence
docker exec openclaw python3 /home/tenazas/tests/test_sprint6.py                          # 47 tests
# Sprint 7 — strong self-improvement
docker exec openclaw python3 /home/tenazas/tests/test_sprint7.py                          # 38 tests
# Sprint 8 — total copilot
docker exec openclaw python3 /home/tenazas/tests/test_sprint8.py                          # 28 tests
# Sprint 9 — multi-agent system
docker exec openclaw python3 /home/tenazas/tests/test_sprint9.py                          # 42 tests
# Sprint 10 — autonomous trading (safe phases only)
docker exec openclaw python3 /home/tenazas/tests/test_sprint10.py                         # 37 tests
```

**Total B+A tests: 622** (Sprint 3-9: 585, Sprint 10: 37)

#### Test categories

| Categoria | Suites | Tests | Que cubre |
|-----------|--------|-------|-----------|
| Unit | governance + expanded | 195 | decision_quality, noise_filter, action_budget, confidence_calibration |
| Simulation | 4 scenarios | 36 | container flapping, Ollama GPU, noise storm, multi-incident |
| Long-run | 2 scenarios | 19 | 1h activity (60 events), progressive degradation (300 decisions) |
| Adversarial | 1 suite | 34 | Manipulated scores, corrupted data, spam, bypass attempts |
| Policy | 1 suite | 32 | Security invariants (container protection, staging isolation) |
| Failure chains | 1 suite | 18 | Cascading failures (GPU+Ollama, VPN+arr, RAM+unhealthy, heal loops) |
| Regression | 1 suite | 32 | Error mining corruption, benchmark gate edge cases, feedback loops |
| E2E | 1 suite | 15 | incident→staging, preference→blacklist, skill_gap→staging |
| Sprint 3+4 | 2 suites | 49 | error_mining, improvement_candidates, preference, skill_gap, experiments |

#### Metric asserts en simulaciones

| Metrica | Umbral | Donde se verifica |
|---------|--------|-------------------|
| decision_accuracy | >= 70% | container_flapping, longrun_1h |
| false_positive_rate | <= 15% | noise_storm, container_flapping |
| alert_reduction_rate | >= 60% | noise_storm, longrun_1h |
| action_success_rate | >= 80% | longrun_1h, multi_incident |
| rollback_rate | <= 10% | longrun_1h, container_flapping |
| confidence_calibration_error | <= 0.15 | ollama_gpu, degradation |

### IoT Device Control (`runtime/devices/`)

Arquitectura modular con dispatcher central. `_handle_smart_home` en orchestrator delega a `devices.dispatch()`.

| Modulo | Dispositivo | IP | Protocolo | Acciones |
|---|---|---|---|---|
| `hue_lights.py` | Philips Hue (8 luces) | 192.168.2.11 | REST API | on/off/cinema/relax/work/status |
| `marantz_avr.py` | Marantz CINEMA 60 | 192.168.2.80 | HTTP GET (self-signed) | power/volume/input/surround/mute/status |
| `hue_sync_box.py` | Hue Sync Box | 192.168.2.36 | HTTPS + Bearer token | sync on-off/mode/intensity/hdmi/status |
| `samsung_tv.py` | Samsung M8 + Q70 | .46 / .29 | WebSocket :8001 | power(WOL)/volume/input/apps/status |
| `google_cast.py` | Nest Mini + Home Mini | .62 / .83 | pychromecast | play/pause/volume/mute/status |
| `xiaomi_vacuum.py` | Xiaomi S20+ | 192.168.2.99 | python-miio | start/stop/dock/fan speed/find/status |
| `shield_tv.py` | NVIDIA SHIELD | 192.168.2.35 | androidtvremote2 | power(WOL)/apps/media/navigate/status |
| `ezviz_cameras.py` | EZVIZ + Dahua + Hikvision (10) | varios | ping only | status (online/offline) |
| `nest_doorbell.py` | Nest Doorbell | 192.168.2.22 | ping only | status (online/offline) |

**Composite cinema mode:** "modo cine" → Hue dim + Marantz ON + Sync Box video/high (3 dispositivos en secuencia)

**Device detection:** `devices.detect_device(query)` — regex por dispositivo, orden especifico (sync box antes de hue lights para evitar conflicto "hue sync" vs "hue")

**Config:** `openclaw.json` → `hue_sync_box.token` (ya pareado), `devices.xiaomi_vacuum.token` (pendiente), `devices.shield_tv.cert_path` (pendiente pairing)

**Deps Dockerfile:** `websocket-client` (Samsung TV WebSocket)

### Network Discovery

| Componente | Descripcion |
|---|---|
| `device_discovery.py` | Scanner nmap + ARP + OUI vendor lookup |
| `deco_client.py` | TP-Link Deco B63 API (WiFi clients, band, signal) |
| `network_exporter.py` | Prometheus :9108 per-device metrics |
| `device_inventory.json` | Inventario persistente (42 dispositivos, 0 unknown) |

Dispositivos conocidos: 11 cameras, 8 network, 4 phones, 4 tablets, 3 smart_home, 2 servers, 2 speakers, 2 displays, 2 IoT, 1 media, 1 printer.

```bash
# Scan rapido (~10s)
docker exec openclaw python3 /home/tenazas/runtime/device_discovery.py --quick
# Scan profundo (~5 min)
docker exec openclaw python3 /home/tenazas/runtime/device_discovery.py --deep
# Deco WiFi clients
docker exec openclaw python3 /home/tenazas/runtime/deco_client.py
# Dashboard Grafana: http://192.168.2.2:3000/d/tenazas-device-discovery
```

<!-- GSD:project-start source:PROJECT.md -->
## Project

**Tenazas Runtime Refactor**

A structural refactoring of the Tenazas runtime layer — the 42+ Python module system running Sebas's personal AI assistant on a Synology NAS. The goal is to clean up organic growth, fix reliability issues, remove dead code, and make the codebase testable and maintainable without changing any user-facing behavior.

**Core Value:** After the refactor, Tenazas works exactly as before — same skills, same Telegram interactions, same cron jobs — but the code is organized, reliable under concurrent access, and easy to extend.

### Constraints

- **Runtime:** Python 3 inside Docker container (node:24-bookworm base)
- **No new dependencies:** Prefer stdlib solutions (fcntl, tempfile, pathlib) over adding packages
- **Test compatibility:** All existing unittest-based tests must pass without modification (unless testing refactored interfaces)
- **Container paths:** Scripts reference `/home/tenazas/runtime/` — module moves must preserve import paths or update all references
- **Cron/startup:** `startup.sh`, `runtime.py`, `metrics_server.py` entry points must continue to work
<!-- GSD:project-end -->

<!-- GSD:stack-start source:codebase/STACK.md -->
## Technology Stack

## Languages
- Node.js 24 (Bookworm) - OpenClaw gateway and main runtime host
- Python 3 (system) - 42+ runtime modules for orchestration, AI routing, infra monitoring
- Bash - Startup scripts, system monitoring (`ollama-check.sh`)
## Runtime
- Node.js 24 (node:24-bookworm base image)
- Python 3 (system packages via apt-get)
- Container: Docker (DSM 7.3.2 on Synology DS923+)
- Host: Synology DS923+ with Ryzen Embedded R1600, 16GB RAM, 27TB storage
- npm (Node.js) - manages openclaw CLI
- pip3 (Python) - installs runtime dependencies
- System: apt-get (Debian/Bookworm)
## Frameworks
- OpenClaw - Node.js 24 gateway framework (main entry point)
- Ollama - Local LLM inference (GPU: RTX 3090 @ 192.168.2.6:11434, CPU fallback: NAS @ 127.0.0.1:11434)
- unittest - Python test framework (54+ test suites across runtime/)
- pytest - Test discovery and execution
- Docker Compose - Container orchestration (20 services total)
- Prometheus - Metrics collection (:9090)
- Grafana - Dashboarding and visualization (:3000)
## Key Dependencies
- requests - HTTP library for API calls to external services
- torch - Deep learning (CPU only, installed from pytorch.org/whl/cpu)
- openai-whisper - Speech-to-text transcription (base model, Spanish)
- google-auth, google-auth-oauthlib, google-api-python-client - Google APIs (Calendar, Gmail)
- prometheus_client - Metrics export to Prometheus
- pycryptodome - Cryptography utilities
- tplinkrouterc6u - TP-Link Deco WiFi API
- python-miio - Xiaomi device control (vacuum)
- pychromecast - Google Cast device control
- websocket-client - WebSocket support for Samsung TV control
- himalaya CLI (v0.7+) - Email client (IMAP/SMTP for Gmail)
- curl - HTTP requests (system package)
- nmap - Network scanning (system package)
- iputils-ping, dnsutils - Network utilities (system packages)
- docker.io - Docker CLI in-container (for docker_wrapper.py)
- ffmpeg - Audio/video processing (system package)
## Configuration
- `.env` file with variables: TZ, NODE_OPTIONS, OLLAMA_API_KEY, OLLAMA_API_BASE, GOG_ACCOUNT, GOOGLE_API_KEY, OPENROUTER_API_KEY, BINANCE_API_KEY, BINANCE_SECRET, GF_SECURITY_ADMIN_PASSWORD
- `state/openclaw.json` - Primary config (20KB+): auth profiles, model providers, LLM backends, Telegram settings, channel config
- `state/iol_config.json` - InvertirOnline (IOL) broker configuration
- `state/iol_credentials.json` - IOL API credentials (gitignored)
- `Dockerfile` - 15 lines, installs deps, adds himalaya CLI, creates tenazas user
- `docker-compose.yml` - 91 lines, defines 4 services (openclaw, prometheus, grafana, searxng)
- `prometheus.yml` - Scrape config (NAS metrics, OpenRouter, ROI, health)
- `alerts.yml` - Alert rules for Prometheus
## Platform Requirements
- Docker (Synology DSM compatible)
- Python 3.8+ (system)
- Node.js 24+
- SSH access to NAS (port 4822)
- Windows 11 (HomeLab server with RTX 3090)
- Synology DS923+ NAS (primary host)
- HomeLab GPU server 192.168.2.6 (RTX 3090, Windows 11)
- NordVPN (via gluetun container, Spanish gateway)
- ChromaDB - Vector database (:8100 on NAS)
- Piper TTS - Text-to-speech container (Spanish voice es_MX-claude-high)
- 20 Docker containers total (arr-stack × 10, Ollama, gluetun, chromadb, prometheus, grafana, etc.)
<!-- GSD:stack-end -->

<!-- GSD:conventions-start source:CONVENTIONS.md -->
## Conventions

## Python Overview
## Naming Patterns
- Lowercase with underscores: `model_router.py`, `orchestrator.py`, `docker_wrapper.py`
- Test files use prefix: `test_*.py` (e.g., `test_orchestrator.py`, `test_runtime.py`)
- Bash scripts use hyphens: `ollama-check.sh`
- Lowercase with underscores for public functions: `classify_query()`, `select_model()`, `detect_backends()`
- Private functions prefixed with underscore: `_now()`, `_log()`, `_ping_backend()`, `_check_action()`
- Inline helper functions use underscore prefix: `_mock_backends()` in test files
- Lowercase with underscores: `routing_metrics`, `confidence_score`, `available_backends`
- Constants in UPPERCASE_WITH_UNDERSCORES: `ALLOWED_ACTIONS`, `BLOCKED_CONTAINERS`, `MODEL_CATALOG`, `GUARDRAIL_COOLDOWN`
- Internal module-level state prefixed with underscore: `_guardrail_state`, `_routing_metrics`, `_ollama_status`
- Classes use PascalCase: `TestDockerWrapper`, `TestQueryClassification` (unittest)
- Enums/Role constants: `VALID_ROLES`, `ROLE_MAP`, `CATEGORY_TO_ROLE`
- Dict keys use lowercase with underscores: `{"ok": bool, "error": str, "ts": datetime}`
## Code Style
- No explicit formatter is configured (no `.prettier`, `.flake8`, `pyproject.toml`)
- Implicit style: 4 spaces indentation (Python standard)
- Line length: appears to be ~100-120 characters based on existing code
- No trailing whitespace
- No explicit linter configured (no `.eslintrc`, `.pylintrc`)
- Code follows PEP8-like conventions but not enforced
- Type hints absent — dynamic typing throughout
- Comments are inline and docstring-based (see below)
#!/usr/bin/env python3
## Error Handling
## Logging
## Comments
- Explain *why*, not *what* (code shows what)
- Complex business logic or non-obvious heuristics
- Trade-offs and design decisions
- Warnings about side effects
- Docstrings on simple functions (rare)
- Type hints (dynamic Python, not enforced)
- Inline comments for obvious code
- Line comments use `#` with single space: `# This explains logic`
- Section markers (e.g., above major sections): 
- Example from `orchestrator.py` (lines 28-35):
## Module Design
- No explicit `__all__` (all public functions available)
- Private helpers prefixed with `_` (naming convention only)
- No barrel files (each module imports directly)
- Module-level dicts for metrics/state: `_guardrail_state`, `_routing_metrics`
- Lazy loading with guard variables: `_guardrail_loaded` in `model_router.py`
- JSON file persistence for state: `guardrail_state.json`, `gluetun_restart_state.json`
## Function Design
- Most functions 10-50 lines
- Complex multi-step operations wrapped in functions (e.g., `route_by_role()` is ~80 lines but broken into logical sections)
- Helper functions extracted for readability
- Few args (2-4 typical), use dicts for complex options
- Optional args have defaults: `flags: dict | None = None`
- Use `**kwargs` rarely (prefer explicit dicts)
- Consistent types per function (don't mix list/dict/None for same function)
- Dicts with known keys for complex returns:
- Tuples for fixed-length multi-return: `(backend, model, url)`
- Empty dict `{}` or list `[]` instead of `None` (graceful defaults)
## Subprocess & Security
- ONLY use list arguments: `["docker", "restart", "sonarr"]`
- NEVER use `shell=True`
- Example from `docker_wrapper.py` (lines 94-98):
- Container names validated via regex before subprocess call
- All Docker actions whitelisted in `docker_wrapper.py`
- All actions logged to `logs/docker_actions.log`
## Code Organization by Domain
<!-- GSD:conventions-end -->

<!-- GSD:architecture-start source:ARCHITECTURE.md -->
## Architecture

## System Overview
```text
```
## Component Responsibilities
| Component | Responsibility | File |
|-----------|----------------|------|
| **Orchestrator** | Classify queries, route to models/skills, execute deterministically | `orchestrator.py` |
| **Model Router V2** | Select optimal model by role, manage VRAM/guardrails, fallback chains | `model_router.py` |
| **Telegram Router** | Poll Telegram, classify skills deterministically, forward others to LLM | `telegram_router.py` |
| **Runtime Loop** | Event detection → task enqueue → safe execution → memory → patterns | `runtime.py` |
| **Docker Wrapper** | Whitelist-enforced Docker subprocess calls (restart/ps/start/stop only) | `docker_wrapper.py` |
| **IoT Dispatcher** | Route device commands, manage composite actions, handle multi-device queries | `devices/__init__.py` |
| **Worker Supervisor** | Run 5 isolated worker subprocesses, protect against freezes, stagger execution | `worker_supervisor.py` |
| **Learning Engine** | Multi-model critic+synthesizer, extract truths, generate typed proposals | `idle_learning.py` |
| **Error Mining** | Parse logs, classify failures, detect patterns for improvement candidates | `error_mining.py` |
| **Finance Module** | Parse natural language transactions, categorize, alert on anomalies | `finance.py` |
| **RAG Pipeline** | Retrieve + rerank context from ChromaDB, inject into LLM prompts | `skills/rag/query.py` |
| **Self-Heal Agent** | Tier-1 OPS, Tier-2 CODE, Tier-3 PROPOSAL approval workflow | `autofix_agent.py` |
## Pattern Overview
- **5-minute event loop** — Cron runs `runtime.py` every 5 min for infrastructure monitoring
- **Skill-first routing** — Orchestrator intercepts known skills (weather, calendar, email) before LLM
- **Role-based model selection** — V2 router classifies prompts by role (fast/reason/code) and selects hardware-optimal model
- **Deterministic execution** — Docker restarts, device commands, finance transactions executed safely without LLM
- **Approval gates** — High-risk actions (code fixes, tier-2 autofix) require Telegram approval before execution
- **Learning in isolation** — Proposals never auto-apply; always review→accept→execute workflow
- **VRAM-aware routing** — Model selection respects GPU/CPU constraints, falls back to cloud if needed
- **Modular devices** — Each IoT device type in separate module, composed via dispatcher
## Layers
- Purpose: Accept input from users (Telegram, HTTP endpoints, CLI)
- Location: `telegram_router.py`, OpenClaw gateway (Node.js), CLI scripts
- Contains: Message polling, payload parsing, response formatting
- Depends on: Orchestrator for routing
- Used by: All upstream; public-facing interface
- Purpose: Classify queries, route to optimal pipeline (LLM/skill/diagnostics), execute
- Location: `orchestrator.py`
- Contains: Classification rules (skill, infra, reasoning, etc.), pipeline definitions, skill execution engine
- Depends on: Model Router V2 (for model selection), skill modules, RAG, diagnostics
- Used by: Telegram Router, HTTP gateway, CLI commands
- Purpose: Route queries to best LLM by capability, manage hardware constraints
- Location: `model_router.py`
- Contains: Backend detection (ping HomeLab/NAS), role classification, VRAM tracking, guardrails, fallback chains
- Depends on: Ollama endpoints (local GPU/CPU), OpenRouter API (cloud)
- Used by: Orchestrator, Worker pipeline
- Purpose: Monitor infrastructure, detect anomalies, execute safe remediation
- Location: `runtime.py`, `executors.py`, `docker_wrapper.py`
- Contains: State monitoring (docker ps, network scan, VPN check), task enqueue, execution with cooldown
- Depends on: Docker socket, network tools (nmap, curl), pattern engine
- Used by: Cron scheduler (every 5 min)
- Purpose: Deterministic implementation of specific capabilities (weather, calendar, smart-home, etc.)
- Location: `workspace/skills/*/`, `devices/`
- Contains: Email (himalaya), Calendar (Google Calendar API), Weather (Open-Meteo), Device commands (REST/WebSocket)
- Depends on: External APIs, local services (Piper for TTS)
- Used by: Orchestrator skill_execution engine
- Purpose: Control smart home devices and infrastructure
- Location: `devices/hue_lights.py`, `devices/samsung_tv.py`, `devices/marantz_avr.py`, etc.
- Contains: Device drivers for 9 device types, composite action support (e.g., cinema mode)
- Depends on: Device REST/WebSocket endpoints
- Used by: Orchestrator via device dispatcher
- Purpose: Standalone management of personal finance, jobs, health tracking
- Location: `finance.py`, `expenses.py`, `jobs.py`, `smoking.py`
- Contains: Natural language parsing, categorization, state persistence
- Depends on: JSON persistence (no external APIs)
- Used by: Telegram router, daily brief, personal supervisor
- Purpose: Analyze logs/metrics, generate improvement proposals, support review workflow
- Location: `idle_learning.py`, `error_mining.py`, `improvement_candidates.py`, `dreaming.py`
- Contains: Multi-model analysis (critic+synthesizer), typed proposals, memory consolidation
- Depends on: Runtime logs, metrics, proposal execution engine
- Used by: Daily cron, workers (learning_worker)
- Purpose: Expose metrics, monitor health, support dashboards
- Location: `metrics_server.py`, `health_sweep.py`
- Contains: Prometheus scrape targets, SQLite health history, Grafana payload builders
- Depends on: Runtime state files, external service health checks
- Used by: Grafana dashboards, alert systems
## Data Flow
### Primary Request Path (User Message to Response)
- User input: `telegram_router.py` line ~200 (update loop)
- Classification: `telegram_router.py` line ~400 (`classify_intent()`)
- Skill execution: `orchestrator.py` line ~600 (`route_and_execute()`)
- Model selection: `model_router.py` line ~250 (`route_by_role()`)
- Response send: `telegram_router.py` line ~800 (`_send_response()`)
### Infrastructure Monitoring Path (Event-Driven)
- Main loop: `runtime.py` line ~1 (`main()`)
- Update state: `runtime.py` line ~150 (`update_state()`)
- Event detection: `runtime.py` line ~206 (`detect_events()`)
- Task enqueue: `runtime.py` line ~296 (`enqueue_tasks()`)
- Execution: `runtime.py` line ~343 (`run_tasks()`)
- Pattern analysis: `runtime.py` line ~381 (`analyze_patterns()`, `trigger_patterns()`)
### Self-Improvement Path (Learning → Proposal → Review → Execute)
- Learning loop: `idle_learning.py` line ~1
- Critic: `idle_learning.py` line ~100 (`run_critic()`)
- Synthesizer: `idle_learning.py` line ~150 (`run_synthesizer()`)
- Proposals save: `idle_learning.py` line ~200 (`save_proposals()`)
- Execution: `apply_improvements.py` line ~1
### State Management
- `state.json` — Current system snapshot (containers, internet, VPN, Ollama status)
- `events.json` — Last detected events
- `memory_log.json` — Event history (max 1000 entries, capped on trim)
- `patterns.json` — Learned time-correlated patterns + last_triggered timestamps
- `finance/transactions.json` — Income/expenses with timestamps
- `jobs/opportunities.json` — Job search pipeline state
- `smoking/log.json` — Cigarette log with timestamps
- `learning/proposals.json` — All proposals (pending/reviewed/accepted/rejected)
- `learning/dreams.md` — REM memory consolidation (append-only, dedup)
- Routing metrics (v2_calls, v1_calls, fallbacks, role distribution) — reset on process restart
- Telegram conversation history per chat (sliding window 10 turns, 10 min TTL)
- Guardrail state (cooldowns, frequency counters, VRAM swap tracking) — persistent file `guardrail_state.json`
## Key Abstractions
- Purpose: Map user messages to execution paths without full LLM inference
- Examples: `telegram_router.py` keyword matchers for skills, `orchestrator.py` category rules, `model_router.py` role heuristics
- Pattern: Regex rules first, keyword scoring second, fallback to LLM if ambiguous
- Purpose: Prevent dangerous or unauthorized Docker/file operations
- Examples: `docker_wrapper.py` whitelist (ps/restart/start/stop), blocklist (openclaw/watchtower), container name regex validation
- Pattern: Default-deny, explicit allowlist, sanitization before subprocess call
- Purpose: Select optimal model for task type without user specification
- Examples: fast (qwen2.5:7b), reason (deepseek-r1:14b), code (qwen2.5-coder:14b), heavy (qwen2.5:32b)
- Pattern: Heuristic classification → role → backend detection → VRAM fit check → guardrails → fallback chain
- Purpose: Execute known capabilities (weather, calendar, email) without LLM hallucination risk
- Examples: Weather calls Open-Meteo curl, Calendar queries Google Calendar API, Email queries Gmail via himalaya
- Pattern: Orchestrator intercepts → calls skill subprocess → captures output → formats for user
- Purpose: Require human review for risky actions before execution
- Examples: Autofix tier-2 code changes, high-risk proposals, infrastructure restarts
- Pattern: Action generated → queued for approval → Telegram buttons (approve/reject/details) → execute only if approved
- Purpose: Track improvement ideas with context, decision history, and execution status
- Examples: Error-mining proposals (type: routing_adjustment), guardrail tuning (type: guardrail_tuning)
- Pattern: Generated with `id`, `type`, `status` (proposed/reviewed/accepted/rejected), `source`, `evidence`, never auto-applied
- Purpose: Combine insights from multiple models (Qwen for synthesis, Deepseek for reasoning, Ollama for local speed)
- Examples: Critic (deepseek-r1) analyzes logs, Synthesizer (qwen2.5:32b) generates solutions
- Pattern: Critic provides analysis → synthesizer builds on analysis → merge results with dedup
## Entry Points
- Location: `telegram_router.py` line ~1 (`main()`)
- Triggers: Telegram bot poll updates every 30s (long-polling)
- Responsibilities: Classify message, intercept skills, forward to LLM if needed, send response
- Location: `runtime.py` line ~1 (`main()`)
- Triggers: DSM Task Scheduler via `docker exec` every 5 minutes
- Responsibilities: Detect infrastructure events, enqueue tasks, execute, log, pattern detection
- Location: `idle_learning.py` line ~1 (`main()`)
- Triggers: CLI `python3 idle_learning.py` or cron (configurable)
- Responsibilities: Analyze logs/metrics via multi-model, generate proposals, save JSON
- Location: `worker_supervisor.py` line ~1
- Triggers: Cron (alternative to direct `runtime.py`)
- Responsibilities: Run 5 workers (runtime, diagnostics, rag, agents, learning) with isolation + timeout protection
## Architectural Constraints
- **Threading:** Single-threaded event loop (runtime.py, telegram_router.py) — all concurrency via subprocess + multiprocessing for workers
- **Global state:** Orchestrator metrics stored in-memory with reset on process restart; guardrail state persisted to `guardrail_state.json`
- **Circular imports:** Models imported lazily in `model_router.py` to avoid import cycles with orchestrator
- **IPC:** No shared memory between workers; coordination via JSON state files + SQL (health_sweep.sqlite)
- **Model loading:** All Ollama models loaded on-demand (not preloaded); latency ~2-5s first inference
- **API rate limits:** OpenRouter balance checked hourly (cached), Telegram uses long-polling (no rate limit)
- **Docker socket:** Mounted at `/var/run/docker.sock` in container, only accessible via `docker_wrapper.py`
## Anti-Patterns
### LLM Over Determinism
### Missing Approval for Risky Actions
### Blocking on External API Calls
### Unbounded Memory Growth
## Error Handling
- Load state gracefully: `load_json()` returns default dict on missing/corrupt file
- Subprocess safety: All Docker calls wrapped in try-except + logged; non-zero exit doesn't stop loop
- LLM fallback: If Ollama offline, try NAS CPU; if both down, try cloud; if all fail, log and return empty
- Approval timeout: If user doesn't approve within 5 min, auto-expire and log decision
- Proposal parsing: JSON parse failures logged with `_raw` field for manual recovery
## Cross-Cutting Concerns
- `telegram_router.log` — message classification, skill execution, LLM calls
- `orchestrator.log` — routing decisions, model selection, pipeline execution
- `model_router.log` — backend pings, role classification, guardrail decisions
- `runtime.log` — event detection, task execution, pattern triggers
- `finance.log` — transaction parsing, alerts
- Container names: Regex `^[a-zA-Z0-9][a-zA-Z0-9_.-]*$` before Docker subprocess
- API responses: Check `ok` field, handle missing/malformed data gracefully
- User input (Telegram): Strip to 4096 char limit, sanitize Markdown for Telegram format
- Telegram: Bot token from `openclaw.json`, allowlist chat IDs from config
- Google Calendar: OAuth refresh token in `gcal-token.json` (read-only)
- Gmail: App password via himalaya config (IMAP/SMTP)
- IOL InvertirOnline: Bearer token from `openclaw.json`, refresh every 10 min
<!-- GSD:architecture-end -->

<!-- GSD:skills-start source:skills/ -->
## Project Skills

No project skills found. Add skills to any of: `.claude/skills/`, `.agents/skills/`, `.cursor/skills/`, `.github/skills/`, or `.codex/skills/` with a `SKILL.md` index file.
<!-- GSD:skills-end -->

<!-- GSD:workflow-start source:GSD defaults -->
## GSD Workflow Enforcement

Before using Edit, Write, or other file-changing tools, start work through a GSD command so planning artifacts and execution context stay in sync.

Use these entry points:
- `/gsd-quick` for small fixes, doc updates, and ad-hoc tasks
- `/gsd-debug` for investigation and bug fixing
- `/gsd-execute-phase` for planned phase work

Do not make direct repo edits outside a GSD workflow unless the user explicitly asks to bypass it.
<!-- GSD:workflow-end -->

<!-- GSD:profile-start -->
## Developer Profile

> Profile not yet configured. Run `/gsd-profile-user` to generate your developer profile.
> This section is managed by `generate-claude-profile` -- do not edit manually.
<!-- GSD:profile-end -->
