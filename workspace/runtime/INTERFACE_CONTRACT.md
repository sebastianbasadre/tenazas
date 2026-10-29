# Contrato de routing

Revisado el **2026-10-28** contra las APIs del repositorio.

## Alcance y responsabilidades

Antes de modificar routing, revisar este contrato y `routes_contract.py`.
Las rutas son relativas a `workspace/runtime/`:

| Módulo | Responsabilidad |
|---|---|
| `telegram_router.py` | Entrada, interceptores, contexto y selección del camino de respuesta |
| `orchestrator.py` | Clasificación de alto nivel y ejecución de pipelines/skills |
| `agent_coordinator.py` | Ownership de agentes y agregación, si está habilitado |
| `tool_tuning/router.py` | Clasificación de tools, regex y fallback Ollama |
| `tool_tuning/router_runtime.py` | Dispatch de la ruta a un handler real |
| `model_router.py` | Selección de modelo/backend por capacidades o rol |
| `routes_contract.py` | Fuente canónica de labels y normalización |

El orquestador contiene ejecución; no es solo un clasificador. El dispatcher
`dispatch()` invoca al clasificador internamente y luego ejecuta el handler.
Las skills reciben la intención de su caller; evitar duplicar clasificación general.

## Labels oficiales

`VALID_ROUTES` en [routes_contract.py](routes_contract.py):

```python
{
    "weather", "calendar", "weather_calendar", "lights", "network_scan",
    "infra", "tts", "arr_monitor", "none",
}
```

No agregar labels copiando este conjunto a otros módulos. Importar el contrato.
`normalize_route()` reconoce aliases históricos, devuelve un label canónico o
`none`; `assert_valid_route()` rechaza valores desconocidos.

| Alias histórico | Canónico |
|---|---|
| `network-scanner`, `network_scanner` | `network_scan` |
| `smart-home-on`, `smart-home-off`, `smart-home` | `lights` |
| `infra-monitor`, `infra_monitor` | `infra` |
| `arr-monitor` | `arr_monitor` |
| `chat` | `none` |

Otros routers como `finetuned_router.py` y el parser multitool usan nombres de
skills/tools que no son este conjunto cerrado. No intercambiar sus outputs sin
normalización y sin revisar el contrato del consumidor.

## API del clasificador

`tool_tuning/router.py` expone `route_intent(user_input, ...)`, que devuelve un
`RouteResult`, no una cadena ni un objeto con confidence:

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

`to_dict()` serializa esos campos. Un error de clasificación produce ruta `none`,
`success=False` y método `fallback`. Una clasificación válida `none` no implica
que haya una tool ejecutable.

## API del dispatcher

`tool_tuning/router_runtime.py` expone:

```python
dispatch(user_input: str, model: str | None = None) -> str | None
dispatch_json(user_input: str, model: str | None = None) -> dict
```

`dispatch()` devuelve output del handler o `None` para continuar al chat.
`dispatch_json()` incluye `ok`, `route`, `raw_label`, `router_ok`, `router_error`,
`handler_ok`, `handler_error`, `output`, `classify_ms`, `handler_ms`, `total_ms`,
`ollama_host`, `ollama_model` y `fallback_to_chat`.

No exigir a todas las skills un payload uniforme `text/data/error`: sus interfaces
reales varían. La telemetría anterior corresponde al dispatcher.

## Ambigüedades

| Caso | Criterio |
|---|---|
| `infra` / `network_scan` | Servicios y contenedores / hosts y dispositivos LAN |
| `infra` / `arr_monitor` | Sistema general / media stack y descargas |
| `none` / `chat` | El clasificador cerrado usa `none`; `chat` es alias histórico |
| Escenas y `lights` | Los nombres de escenas de otras capas no agregan labels al contrato |
| `self-evolve` y otras skills | Pueden tener handlers propios fuera del clasificador cerrado |

## Cambios del contrato

1. Documentar ambigüedades antes de resolverlas.
2. Cambiar primero `routes_contract.py`.
3. Revisar regex, validación, dispatch y aliases.
4. Revisar métricas, exporter y dashboards afectados.
5. Actualizar pruebas y datasets del caller correspondiente.
6. No reemplazar `network_scan` por `network-scanner` en el contrato canónico.

Ver [arquitectura](../../docs/architecture.md) y [modelos](../../MODELS.md).
