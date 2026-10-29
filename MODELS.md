# Models and routing

Code reviewed **2026-10-28**. The catalog is configuration, not proof of installed
models, provider availability, current pricing, or inference performance.

## Python model router

Source: [model_router.py](workspace/runtime/model_router.py). V1 selects by
capability; V2 selects by role. Both account for backends and constraints.

| V2 role | Ordered `ROLE_MAP` chain |
|---|---|
| `fast` | `qwen3-fast:8b` → `qwen2.5:7b` → `qwen3-fast:4b` |
| `reason` | `qwen3-fast:8b` → `qwen3-fast:4b` → `qwen2.5:7b` |
| `heavy` | `qwen3-fast:8b` → `qwen3-fast:4b` → `qwen2.5:7b` |
| `code` | `qwen2.5-coder:7b` → `qwen3-fast:8b` → `qwen2.5:7b` |
| `vision` | `qwen3-vl:4b` → `qwen3-fast:8b` → `qwen2.5:7b` |
| `embed` | `nomic-embed-text` |
| `cloud` | `moonshotai/kimi-k2` → `google/gemini-flash-1.5` |

Declared backends are `notebook_gpu` (the internal HomeLab name), `nas_cpu`, and
`openrouter`, with priorities 1, 2, and 3. Selection considers availability, VRAM,
cooldown, frequency, and latency. The catalog marks `qwen2.5:3b` and `phi4-mini`
as legacy. Memory estimates are approximate.

`qwen3-fast:*` names refer to custom models. Do not assume they are registry tags
that `ollama pull` can download. Import or recreate their Modelfiles and check
`ollama list`. The local Modelfiles mentioned in historical HomeLab notes are not
included in this checkout.

## Independent selectors

| Component | Code configuration |
|---|---|
| `model_failover.py` | Primary `google/gemini-2.5-flash`; fallback `ollama/qwen2.5:7b` |
| `telegram_router.py` | `TENAZAS_TELEGRAM_FALLBACK_MODEL`, default `qwen3:8b` |
| `tool_tuning/router.py` | Default classifier `tenazas-router-labels`, configurable through `OLLAMA_MODEL` |
| `agent_job_search.py` | CV matching with `qwen2.5:3b` |
| `scripts/homelab-ollama-startup.ps1` | Pulls `gemma3:12b`, `qwen3:8b`, `qwen2.5:7b`, `nomic-embed-text`; warms `gemma3:12b` |
| `finetuned_router.py` | Separate skill classifier; inspect its constants and caller before changing routing |

These settings do not form one global cascade. The gateway failover monitor
observes rate limits and changes gateway configuration. The Python model router
selects models for its own callers. HomeLab startup uses a different model set
from `ROLE_MAP`; see the [Ollama runbook](docs/homelab-ollama-runbook.md).

Cloud identifiers are documented because they exist in source. Verify provider
availability and pricing before use; this guide does not promise a free tier or
maintain current price quotations.

## Tool classification and training

Canonical labels are `weather`, `calendar`, `weather_calendar`, `lights`,
`network_scan`, `infra`, `tts`, `arr_monitor`, and `none`. See the
[routing contract](workspace/runtime/INTERFACE_CONTRACT.md).

[Runtime tool_tuning](workspace/runtime/tool_tuning/) contains dataset builders,
evaluation, and exporters. [workbench/tenazas-router](workbench/tenazas-router/)
contains a separate pipeline and training data. Review each pipeline's paths,
arguments, dependencies, and formats; they are not one fixed-version dataset.

`tenazas-router-labels` and `tenazas-multitool` need training/import artifacts beyond
the standard bootstrap. Previously documented April 2026 evaluation results were
historical and do not establish current accuracy or improvement from fine-tuning.
Record model, dataset, split, and date with a fresh evaluation before comparing results.

## Evaluation observability

The evaluation exporter listens on `:9107`; Prometheus scrapes it every 300 s.
Check timestamps, exporter health, and result files before interpreting F1,
precision, recall, or exact match. Dashboard:
[grafana/dashboards/tenazas-multitool-eval.json](grafana/dashboards/tenazas-multitool-eval.json).
