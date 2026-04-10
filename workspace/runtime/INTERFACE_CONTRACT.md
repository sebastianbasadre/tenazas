# INTERFACE CONTRACT — Tenazas routing layers

## Instrucciones para agentes que trabajan en este repo

Antes de tocar `orchestrator.py`, `router.py`, `router_runtime.py` o cualquier `skill/*`:

1. Leer este archivo completo
2. No agregar labels sin actualizar `VALID_ROUTES` en `routes_contract.py`
3. No duplicar clasificación en una skill que ya tiene route asignado
4. Si encontrás ambigüedad no listada en la sección de riesgos, documentarla antes de resolver
5. No usar `network-scanner` — el label oficial es `network_scan`
6. Labels nuevos requieren update de: router + runtime + exporter + dashboard + tests

---

## 1. Responsabilidad por capa

### orchestrator.py
- **Clasifica**: intents de alto nivel via keyword/regex (primera capa, sin LLM)
- **Deriva al router**: cuando el intent no matchea keyword directa
- **Ejecuta skill directa**: cuando el intent es inequívoco y no requiere router
- **NO debe**: duplicar semántica detallada del router, hacer inferencia LLM, reclasificar output del router

### router.py
- **Clasifica**: queries usando regex fast-path primero, LLM solo si no hay match
- **Devuelve**: exactamente un label de VALID_ROUTES, nunca texto libre
- **NO debe**: ejecutar tools, llamar skills, hacer lógica de negocio
- **Fallback**: si falla o label desconocido → `none`

### skills/*
- **Reciben**: intención ya resuelta (route + query + contexto opcional)
- **Ejecutan**: lógica de la tool, no reclasifican
- **NO deben**: hacer clasificación de intención general, llamar al router
- **Output**: texto final o payload estructurado + error controlado

---

## 2. Ownership matrix

| Capa | Decide intención | Decide modelo | Ejecuta tool | Re-clasifica | Puede hacer fallback |
|------|-----------------|---------------|--------------|--------------|---------------------|
| orchestrator.py | primera capa | no | no | no | si → router |
| router.py | segunda capa | no | no | no | si → none |
| model_router.py | no | si | no | no | si → cloud |
| router_runtime.py | no | no | si dispatch | no | si → none |
| skills/* | no | no | si | no | si → error |

---

## 3. Contratos explícitos

### Contrato A — orchestrator → router

Input:
```python
{
  "query": str,           # texto del usuario
  "context": dict | None  # contexto conversacional opcional
}
```

Output:
```python
{
  "route": str,           # label de VALID_ROUTES
  "confidence": float | None,
  "method": str,          # "regex" | "llm" | "fallback"
  "raw_output": str | None
}
```

### Contrato B — router → runtime

Output válido:
- Solo labels dentro de `VALID_ROUTES`
- Si no matchea → `"none"`
- Nunca `None`, nunca texto libre, nunca lista

### Contrato C — runtime → skill

Input:
```python
{
  "query": str,
  "route": str,           # ya validado contra VALID_ROUTES
  "context": dict | None
}
```

Output:
```python
{
  "text": str,            # respuesta final para el usuario
  "data": dict | None,    # payload estructurado opcional
  "error": str | None     # mensaje de error controlado
}
```

---

## 4. Labels oficiales (VALID_ROUTES)

```python
VALID_ROUTES = {
    "weather",           # clima
    "calendar",          # agenda y calendario
    "weather_calendar",  # clima + agenda juntos
    "lights",            # control luces Hue
    "network_scan",      # escaneo red LAN  <- CANONICAL (no "network-scanner")
    "infra",             # estado homelab/containers/descargas
    "tts",               # text-to-speech
    "arr_monitor",       # media/downloads stack
    "none",              # chat sin tool / fallback
}
```

### Labels deprecados (NO usar)
| Label deprecado | Reemplazar por |
|----------------|----------------|
| `network-scanner` | `network_scan` |
| `smart-home-on` | `lights` |
| `smart-home-off` | `lights` |
| `infra-monitor` | `infra` |
| `arr-monitor` | `arr_monitor` |

---

## 5. Reglas de no superposición

- `orchestrator.py` no debe duplicar semántica detallada del router
- `router.py` no ejecuta tools
- `skills/*` no hacen clasificación de intención general
- Labels nuevos deben agregarse primero al contrato y a `routes_contract.py`
- Cambios de labels requieren update de:
  1. `routes_contract.py` (VALID_ROUTES)
  2. `router.py` (regex patterns)
  3. `router_runtime.py` (dispatch table)
  4. `eval_exporter.py` (métricas)
  5. dashboard Grafana (per-tool F1)
  6. tests del router

---

## 6. Ambiguedades actuales detectadas

| Ambiguedad | Descripción | Resolución |
|------------|-------------|------------|
| `infra` vs `network_scan` | "cómo está la red" puede ser infra o network_scan | `network_scan` = dispositivos/hosts LAN; `infra` = servicios/homelab/containers |
| `chat` vs `none` | some chat intent is `none` | `none` es el label único para chat sin tool |
| `self-evolve` vs reminders | referencias a "propuestas" se confunden | self-evolve tiene su propio handler; reminders es skill separada |
| smart-home escenas | `lights`, `smart-home-cinema`, `smart-home-relax` | hoy solo `lights` es oficial; escenas específicas pendientes de expansión |
| `arr_monitor` vs `infra` | "descargas" puede ir a cualquiera | `arr_monitor` = sonarr/radarr/qbittorrent; `infra` = containers/sistema |

---

## 7. Enforcement mínimo

Sin refactor grande. Solo:

1. Módulo compartido `routes_contract.py` con `VALID_ROUTES` y helpers
2. `router.py` y `router_runtime.py` importan de ahí
3. Tests de contrato mínimos
4. Header en cada archivo que toca routing indicando que debe respetar este contrato

---

## 8. Cómo usar este contrato antes de lanzar agentes

Cada prompt de agente debe incluir al inicio:

```
Antes de modificar cualquier archivo de routing, leer:
/home/tenazas/runtime/INTERFACE_CONTRACT.md

Reglas no negociables:
- No agregar labels sin actualizar routes_contract.py
- No duplicar clasificación entre capas
- Label oficial: network_scan (no network-scanner)
- Skills no reclasifican
```
