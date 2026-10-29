# Modelos y routing

Revisión del código: **2026-10-28**. El catálogo es configuración, no un inventario
de modelos instalados ni una medición de disponibilidad, costo o rendimiento.

## Model router Python

Fuente: [model_router.py](workspace/runtime/model_router.py). V1 selecciona por
capacidades y V2 por roles; ambos consideran backends y restricciones.

| Rol V2 | Cadena `ROLE_MAP`, en orden |
|---|---|
| `fast` | `qwen3-fast:8b` → `qwen2.5:7b` → `qwen3-fast:4b` |
| `reason` | `qwen3-fast:8b` → `qwen3-fast:4b` → `qwen2.5:7b` |
| `heavy` | `qwen3-fast:8b` → `qwen3-fast:4b` → `qwen2.5:7b` |
| `code` | `qwen2.5-coder:7b` → `qwen3-fast:8b` → `qwen2.5:7b` |
| `vision` | `qwen3-vl:4b` → `qwen3-fast:8b` → `qwen2.5:7b` |
| `embed` | `nomic-embed-text` |
| `cloud` | `moonshotai/kimi-k2` → `google/gemini-flash-1.5` |

Los backends declarados son `notebook_gpu` (nombre interno del HomeLab), `nas_cpu`
y `openrouter`, con prioridades 1, 2 y 3. Se detecta disponibilidad y se consideran
VRAM, cooldown, frecuencia y latencia. El catálogo marca `qwen2.5:3b` y `phi4-mini`
como legacy; sus estimaciones de memoria son orientativas.

`qwen3-fast:*` son nombres de modelos custom; no asumir que existen en un registry
ni que `ollama pull` puede descargarlos. Crear/importar sus Modelfiles y comprobar
`ollama list` en el host. El repositorio no contiene los Modelfiles locales
mencionados en las notas históricas de HomeLab.

## Otros selectores

| Componente | Configuración del código |
|---|---|
| `model_failover.py` | Primario `google/gemini-2.5-flash`; fallback `ollama/qwen2.5:7b` |
| `telegram_router.py` | Fallback `TENAZAS_TELEGRAM_FALLBACK_MODEL`, default `qwen3:8b` |
| `tool_tuning/router.py` | Clasificador default `tenazas-router-labels`, configurable con `OLLAMA_MODEL` |
| `agent_job_search.py` | Matching de CV con `qwen2.5:3b` |
| `scripts/homelab-ollama-startup.ps1` | Descarga `gemma3:12b`, `qwen3:8b`, `qwen2.5:7b`, `nomic-embed-text`; precarga `gemma3:12b` |

Estas configuraciones no son una única cascada global. El monitor de failover
revisa rate limits y modifica la configuración del gateway; el router Python
elige modelos para sus propios flujos. El script de arranque HomeLab conserva
un conjunto distinto del `ROLE_MAP`; ver [runbook Ollama](docs/homelab-ollama-runbook.md).

Los identificadores cloud anteriores se documentan porque figuran en el código.
Su disponibilidad y precio deben verificarse en el proveedor antes de usarlos.
No se mantienen tarifas ni promesas de free tier en esta guía.

## Tools y entrenamiento

Los labels canónicos del clasificador son `weather`, `calendar`, `weather_calendar`,
`lights`, `network_scan`, `infra`, `tts`, `arr_monitor` y `none`.
Ver [contrato](workspace/runtime/INTERFACE_CONTRACT.md).

El runtime contiene [tool_tuning](workspace/runtime/tool_tuning/), con builders,
evaluación y exportación de métricas. [workbench/tenazas-router](workbench/tenazas-router/)
contiene otro pipeline y datos de entrenamiento. Revisar rutas, argumentos,
dependencias y formato de cada pipeline antes de ejecutarlo; sus datasets no
constituyen un único artefacto de versión fija.

Los modelos `tenazas-router-labels` y `tenazas-multitool` requieren artefactos de
entrenamiento/importación externos al bootstrap estándar. Los resultados de
abril de 2026 que figuraban aquí eran históricos; no prueban precisión actual ni
una mejora del modelo ajustado. Generar una evaluación nueva, registrando modelo,
dataset, split y fecha antes de comparar resultados.

## Observabilidad

El exporter de evaluación corre en `:9107`; Prometheus lo consulta cada 300 s.
Revisar timestamps, estado del exporter y archivos de resultados antes de usar
F1, precision, recall o exact match. Dashboard:
[grafana/dashboards/tenazas-multitool-eval.json](grafana/dashboards/tenazas-multitool-eval.json).
