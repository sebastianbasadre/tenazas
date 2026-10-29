# Routing interface contract

Reviewed against implemented APIs on **2026-10-28**.

## Scope and ownership

Read this contract and `routes_contract.py` before changing routing. Paths below
are relative to `workspace/runtime/`.

| Module | Responsibility |
|---|---|
| `telegram_router.py` | Ingress, interceptors, context, and response-path selection |
| `orchestrator.py` | High-level classification and pipeline/skill execution |
| `agent_coordinator.py` | Domain ownership and aggregation when enabled |
| `tool_tuning/router.py` | Closed-label tool classification using regex and Ollama fallback |
| `tool_tuning/router_runtime.py` | Dispatch classified routes to real handlers |
| `model_router.py` | Model/backend selection by capability or role |
| `routes_contract.py` | Canonical labels and normalization |

The orchestrator includes execution, not only classification. `dispatch()` invokes
the classifier internally before running a handler. Skills receive their caller's
resolved intent; avoid duplicating general intent classification inside them.

## Canonical labels

`VALID_ROUTES` in [routes_contract.py](routes_contract.py):

```python
{
    "weather", "calendar", "weather_calendar", "lights", "network_scan",
    "infra", "tts", "arr_monitor", "none",
}
```

Import this contract rather than copying its set into other modules.
`normalize_route()` accepts historical aliases and returns a canonical label or
`none`; `assert_valid_route()` rejects unknown values.

| Historical alias | Canonical label |
|---|---|
| `network-scanner`, `network_scanner` | `network_scan` |
| `smart-home-on`, `smart-home-off`, `smart-home` | `lights` |
| `infra-monitor`, `infra_monitor` | `infra` |
| `arr-monitor` | `arr_monitor` |
| `chat` | `none` |

Other classifiers, including `finetuned_router.py` and the multitool parser, use
skill/tool names outside this closed set. Do not interchange their outputs without
normalization and consumer-contract review.

## Classifier API

`tool_tuning/router.py` exposes `route_intent(user_input, ...)`, returning a
`RouteResult`, not a string or an object containing a confidence score:

```python
@dataclass
class RouteResult:
    route: str
    raw_output: str
    success: bool
    method: str = "unknown"  # regex | ollama | fallback
    error: str | None = None
    cached: bool = False
```

`to_dict()` serializes these fields. Classification errors produce `route="none"`,
`success=False`, and `method="fallback"`. A valid `none` classification does not
imply an executable tool.

## Dispatcher API

`tool_tuning/router_runtime.py` exposes:

```python
dispatch(user_input: str, model: str | None = None) -> str | None
dispatch_json(user_input: str, model: str | None = None) -> dict
```

`dispatch()` returns handler output or `None` to continue with chat.
`dispatch_json()` includes `ok`, `route`, `raw_label`, `router_ok`, `router_error`,
`handler_ok`, `handler_error`, `output`, `classify_ms`, `handler_ms`, `total_ms`,
`ollama_host`, `ollama_model`, and `fallback_to_chat`.

Skill interfaces vary; do not require every skill to implement a uniform
`text/data/error` payload. The fields above describe dispatcher telemetry.

## Ambiguities

| Case | Boundary |
|---|---|
| `infra` / `network_scan` | Services and containers / LAN hosts and devices |
| `infra` / `arr_monitor` | General system / media stack and downloads |
| `none` / `chat` | The closed classifier uses `none`; `chat` is a historical alias |
| Scenes / `lights` | Other layers' scene names do not add canonical labels |
| `self-evolve` and other skills | May have handlers outside this classifier |

## Contract changes

1. Document ambiguities before resolving them.
2. Update `routes_contract.py` first.
3. Review regex, validation, dispatch, and aliases.
4. Review affected metrics, exporters, and dashboards.
5. Update the relevant caller's tests and datasets.
6. Keep `network_scan` as the canonical spelling.

See [Architecture](../../docs/architecture.md) and [Models](../../MODELS.md).
