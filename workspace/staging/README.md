# Staging de mejoras

Este directorio contiene candidatos y borradores. Su presencia no significa que
estén desplegados ni que hayan sido aprobados.

| Directorio previsto | Contenido |
|---|---|
| `prompts/` | Ajustes de prompts |
| `router_rules/` | Reglas de routing |
| `runbooks/` | Propuestas de remediación |
| `tests/` | Casos de prueba |
| `datasets/` | Material de entrenamiento |

Los directorios y artefactos se crean según el flujo que los use. Revisar
`improvement_candidates.py`, `self_benchmark.py` y los handlers de aprobación
antes de aplicar un candidato.

El flujo de propuestas del runtime también usa `learning/proposals.json`.
`proposal_executor.py` selecciona propuestas `accepted`, determina cuáles son
`deterministic` y cuáles `manual_only`, y puede ejecutar las primeras en una rama
sandbox con controles, validación y rollback. No todos los archivos de staging
son entradas directas de ese executor.

La revisión humana debe preceder la aceptación que habilita ejecución. Registrar
la evidencia, las pruebas y el destino del cambio; no confundir aprobar un
candidato con verificar su despliegue.

[Arquitectura](../../docs/architecture.md) · [Desarrollo](../../docs/development.md)
