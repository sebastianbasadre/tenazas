# Staging — Tenazas Self-Improvement

**NADA en este directorio es produccion.**

Los archivos aqui son propuestas generadas por `improvement_candidates.py`
que esperan aprobacion explicita de Sebas antes de aplicarse al runtime.

## Directorios

| Dir | Contenido | Destino si aprobado |
|-----|-----------|---------------------|
| prompts/ | Ajustes de prompts para clasificacion | orchestrator.py |
| router_rules/ | Reglas nuevas de routing | model_router.py |
| runbooks/ | Runbooks de remediacion | runbook_generator.py |
| tests/ | Test cases nuevos | tests/ |
| datasets/ | Datos de entrenamiento | tool_tuning/ |

## Flujo

```
error_mining.py → improvement_candidates.py → staging/
                                                  ↓
                                          self_benchmark.py --gate
                                                  ↓
                                          Sebas aprueba via Telegram
                                                  ↓
                                          Se aplica a produccion
```

## Politica

- Nunca se auto-aplica nada
- Todo requiere `--gate` pass + aprobacion manual
- Si el benchmark falla, el candidato se rechaza automaticamente
