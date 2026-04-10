# Modelos de IA — Tenazas

## Modelos en producción

### HomeLab Ollama (192.168.2.6:11434, RTX 3090 24GB)

| Modelo | Params | VRAM | Rol | Uso |
|---|---|---|---|---|
| qwen2.5:7b | 7B | 4.7 GB | fast | Chat, sistema, tareas rápidas |
| deepseek-r1:14b | 14B | 9.0 GB | reason | Análisis, debugging, reflexión |
| qwen2.5:32b | 32B | 20.0 GB | heavy | Tareas críticas, contexto largo |
| qwen2.5-coder:14b | 14B | 9.0 GB | code | Generación, refactor, review |
| qwen2.5vl:7b | 7B | 6.0 GB | vision | Input visual, imágenes |
| qwen3:8b | 8B | 5.2 GB | fast | Alternativa a qwen2.5:7b |
| nomic-embed-text | 137M | 0.3 GB | embed | Embeddings para RAG |
| tenazas-router-labels | ~3B | ~2.0 GB | routing | Intent classification (labels cerradas) |
| tenazas-multitool | ~3B | ~2.0 GB | multitool | Multi-tool selection XML |

### NAS Ollama (127.0.0.1:11434, CPU only)

| Modelo | Uso |
|---|---|
| phi4-mini | Fallback liviano (solo si HomeLab offline) |

### Cloud (fallback)

| Modelo | Provider | Costo (per Mtok) | Uso |
|---|---|---|---|
| moonshotai/kimi-k2 | OpenRouter | $0.60 in / $2.40 out | General, reasoning, self-evolve |
| Gemini 2.5 Flash | Google Cloud | Gratis (tier) | Fallback final |
| Gemini 2.5 Pro | Google Cloud | Variable | Razonamiento cloud (alias: "reasoning") |

---

## Modelo de routing: tenazas-router-labels

Modelo GGUF fine-tuned desde qwen2.5-3b para clasificar queries en labels cerradas.

### Labels disponibles
| Label | Significado | Ejemplo |
|---|---|---|
| `weather` | Clima | "cómo está el clima" |
| `calendar` | Agenda/calendario | "qué tengo hoy" |
| `weather_calendar` | Clima + agenda | "clima y agenda de hoy" |
| `lights` | Control de luces Hue | "prendé las luces" |
| `network_scan` | Escaneo de red LAN | "escanea la red" |
| `infra` | Estado homelab/containers | "cómo están los containers" |
| `tts` | Text-to-speech | "mandame un audio" |
| `arr_monitor` | Media/downloads stack | "cómo están las descargas" |
| `none` | Chat sin tool | "hola, cómo estás" |

### Arquitectura del router (router.py)
```
User query
    ↓
[Regex fast-path] → Match? → Return label (0ms)
    ↓ (no match)
[Ollama LLM] → tenazas-router-labels → Validate label → Return
    ↓ (error/offline)
[Fallback] → "none"
```

- 30+ regex patterns para network_scan, infra, arr_monitor, weather, etc.
- Separación semántica: network_scan = dispositivos/hosts, infra = servicios/sistema
- Cache LRU con TTL (60s, configurable)
- Logging JSON estructurado → logs/tool_router.log
- Validación estricta contra VALID_ROUTES (label desconocido → "none")

---

## Modelo multitool: tenazas-multitool

Modelo GGUF fine-tuned desde qwen2.5-3b para selección de tools via XML.

### Dataset
- 306 ejemplos totales (245 train / 61 eval)
- Categorías: 80 single-tool, 81 multi-tool, 20 no-tool, 20 ambiguos, +95 expansión
- Tools: weather, calendar, email, smart-home, tts, network-scanner, infra-monitor, arr-monitor, self-evolve

### Pipeline de training
```
build_multitool_dataset.py  → 306 ejemplos JSONL
split_dataset.py            → 80/20 train/eval split
train_multitool.py          → LoRA/QLoRA (RTX 3090)
merge_3b.py                 → Merge LoRA + base model
convert_to_gguf.bat         → HF → GGUF (llama.cpp b4924, q8_0)
ollama create               → Modelo listo en Ollama
eval_runner.py              → Evaluar contra eval set
```

### Herramientas (HomeLab)
- Python: 3.14 (3.11 bloqueado por WDAC en HomeLab)
- llama.cpp: b4924
- GGUF quantization: q8_0 (no q4_k_m)
- HF_HOME: D:\hf_cache (persistente, setup_hf_cache_machine.ps1)

---

## Evaluaciones actuales

### Baseline (qwen2.5:7b + system prompt)
| Métrica | Valor |
|---|---|
| F1 | 0.683 |
| Precision | 0.683 |
| Recall | 0.697 |
| Exact match | 0.656 |
| Hallucination rate | 0.0 |
| Over-trigger | 0.098 |
| Under-trigger | 0.164 |

### Finetuned (tenazas-multitool)
| Métrica | Valor |
|---|---|
| F1 | 0.683 |
| Precision | 0.683 |
| Recall | 0.697 |
| Exact match | 0.656 |
| Hallucination rate | 0.0 |
| Over-trigger | 0.098 |
| Under-trigger | 0.164 |

**Nota**: Baseline y finetuned muestran scores idénticos. Posibles causas:
- El eval runner usó el mismo modelo para ambos
- El fine-tuning no mejoró sobre el baseline con system prompt
- El dataset de 306 ejemplos puede ser insuficiente para un delta significativo

### Per-tool F1 (ambos modelos)
| Tool | F1 | Estado |
|---|---|---|
| calendar | 1.0 | OK |
| email | 1.0 | OK |
| smart-home-work | 1.0 | OK |
| infra-monitor | 0.941 | OK |
| weather | 0.909 | OK |
| smart-home-on | 0.667 | Mejorable |
| tts | 0.615 | Mejorable |
| arr-monitor | 0.5 | Bajo |
| network_scan | 0.0 | Crítico — label mismatch resuelto en router.py |
| self-evolve | 0.0 | Crítico — patterns expandidos en orchestrator |
| smart-home-cinema | 0.0 | Crítico |
| smart-home-relax | 0.0 | Crítico |

### Router labels (tenazas-router-labels)
Test manual: 4/4 correcto (weather_calendar, lights, none, network_scan)
Router regex: 99 tests automatizados, todos pasan

---

## Observabilidad de modelos

### Prometheus metrics (:9107)
- `tenazas_eval_f1{model="baseline|finetuned|3b_baseline"}`
- `tenazas_eval_precision{model="..."}`
- `tenazas_eval_recall{model="..."}`
- `tenazas_eval_exact_match{model="..."}`
- `tenazas_eval_hallucination_rate{model="..."}`
- `tenazas_eval_over_trigger{model="..."}`
- `tenazas_eval_under_trigger{model="..."}`
- `tenazas_eval_f1_per_tool{tool="...", model="..."}`
- `tenazas_eval_file_mtime_seconds{model="..."}`
- `tenazas_eval_exporter_up`

### Dashboard Grafana: tenazas-multitool-eval
12+ panels: exact match, F1, precision, recall, hallucination, over/under trigger,
per-tool F1 (bar gauge baseline vs finetuned), delta F1, freshness, exporter status

---

## Último relevamiento
Fecha: 2026-04-09
